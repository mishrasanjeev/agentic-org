"""Main Tool Gateway — validates and executes every agent tool call."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

import structlog

from auth.grant_enforcement import EnforcementMode, GrantCallContext
from auth.run_grants import RunGrant, check_run_grant
from auth.scopes import check_scope
from core.config import is_strict_runtime_env, settings
from core.governance.action_policy import (
    ActionContext,
    ActionDomain,
    ActionRisk,
    CapabilityAuthorization,
    database_feature_flag_resolver,
    evaluate_action,
)
from core.pii.pseudonymiser import PseudonymisationError, PseudonymSession, refusal
from core.tool_gateway.audit_logger import AuditLogger
from core.tool_gateway.idempotency import IdempotencyStore
from core.tool_gateway.pii_masker import mask_pii
from core.tool_gateway.rate_limiter import RateLimiter

logger = structlog.get_logger()


class ToolGateway:
    """Central gateway for all agent tool calls."""

    def __init__(
        self,
        rate_limiter: RateLimiter | None = None,
        idempotency_store: IdempotencyStore | None = None,
        audit_logger: AuditLogger | None = None,
    ):
        self.rate_limiter = rate_limiter
        self.idempotency = idempotency_store
        self.audit = audit_logger
        # P1.3: Cache keyed by (tenant_id, connector_name) to prevent
        # cross-tenant credential confusion. Per-key asyncio.Lock prevents
        # races when concurrent requests load the same connector.
        self._connectors: dict[tuple[str, str | None, str], Any] = {}
        self._connector_locks: dict[tuple[str, str | None, str], asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()  # protects _connector_locks dict itself

    def register_connector(
        self,
        name: str,
        connector: Any,
        tenant_id: str = "_global",
        company_id: str | None = None,
    ) -> None:
        """Register an instance for one exact tenant/company scope.

        Global instances remain available to company-less legacy calls but
        are never inherited by a company-scoped execution.
        """
        self._connectors[(tenant_id, company_id, name)] = connector

    async def _get_connector_lock(
        self,
        tenant_id: str,
        company_id: str | None,
        connector_name: str,
    ) -> asyncio.Lock:
        """Get or create per-connector lock atomically."""
        key = (tenant_id, company_id, connector_name)
        async with self._global_lock:
            if key not in self._connector_locks:
                self._connector_locks[key] = asyncio.Lock()
            return self._connector_locks[key]

    async def execute(
        self,
        tenant_id: str,
        agent_id: str,
        agent_scopes: list[str],
        connector_name: str,
        tool_name: str,
        params: dict[str, Any],
        idempotency_key: str | None = None,
        amount: float | None = None,
        grant_token: str | None = None,
        company_id: str | None = None,
        domain: ActionDomain | str | None = None,
        capability_authorization: CapabilityAuthorization | None = None,
        *,
        run_grant: RunGrant | None,
        agent_type: str = "",
        pseudonymiser: PseudonymSession | None = None,
    ) -> dict[str, Any]:
        """Execute a tool call through the gateway pipeline.

        ``pseudonymiser`` restores pseudonymised model arguments before any
        check or dispatch; a call whose pseudonyms cannot all be restored is
        refused and audited, never dispatched.

        PRD F-1: with a ``run_grant`` in ``warn`` or ``deny`` the grant check
        (``auth/grant_enforcement.py``) runs first, and then every legacy check
        below runs exactly as in ``off`` - including strict enforcement of a
        token passed to the gateway - so enforcement never skips or downgrades
        a check ``off`` makes. ``run_grant`` is required so the grant check
        cannot be left out by omission; only tests that exercise the legacy
        checks alone pass ``auth.run_grants.NO_RUN_GRANT_FOR_TESTS``.
        """
        start_time = time.monotonic()

        if pseudonymiser is not None:
            try:
                params = await pseudonymiser.restore_arguments(params)
            except PseudonymisationError as exc:
                if self.audit:
                    await self.audit.log(
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        tool_name=tool_name,
                        action="pseudonym_restore_failed",
                        outcome="blocked",
                        details={"reason": exc.reason},
                    )
                return refusal(exc)

        # In strict runtimes every tool dispatch must carry exact company and
        # domain context. Relaxed runtimes preserve legacy callers unless they
        # opt into governance context, which keeps local/unit fixtures usable
        # while production remains fail closed.
        governance_decision = None
        if is_strict_runtime_env(settings.env) or company_id is not None or domain is not None:
            governance_decision = await evaluate_action(
                f"{connector_name}:{tool_name}",
                context=ActionContext(
                    tenant_id=tenant_id,
                    company_id=company_id,
                    domain=domain,
                    runtime_env=settings.env,
                ),
                capability_authorization=capability_authorization,
                feature_flags=database_feature_flag_resolver,
            )
            if not governance_decision.dispatch_allowed:
                details = governance_decision.to_dict()
                if self.audit:
                    await self.audit.log(
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        tool_name=tool_name,
                        action="action_contained",
                        outcome="blocked",
                        details=details,
                    )
                return {
                    "error": {
                        "code": "E1011",
                        "message": f"action_contained: {governance_decision.reason}",
                    },
                    "governance": details,
                }

        # 1. Validate scope via Grantex enforce (manifest-based, offline JWT verification)
        effective_token = grant_token or getattr(self, "_current_grant_token", None)
        if run_grant is not None and run_grant.mode is not EnforcementMode.OFF:
            check = await check_run_grant(
                run_grant,
                connector=connector_name,
                tool=tool_name,
                amount=amount,
                context=GrantCallContext(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    agent_type=agent_type,
                    runtime="tool_gateway",
                    grant_source=run_grant.source,
                ),
            )
            if not check.dispatch_allowed and check.denial is not None:
                if self.audit:
                    await self.audit.log(
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        tool_name=tool_name,
                        action="scope_denied",
                        outcome="blocked",
                        details={
                            "reason": f"grant_denied: {check.denial.reason.value}",
                            "sub_reason": check.denial.sub_reason,
                            "grant_id": check.denial.grant_id,
                        },
                    )
                return {
                    "error": {
                        "code": "E1007",
                        "message": f"grant_denied: {check.denial.reason.value}",
                        "reason": check.denial.reason.value,
                        "sub_reason": check.denial.sub_reason,
                    }
                }

        if effective_token:
            from core.langgraph.grantex_auth import get_grantex_client

            grantex = get_grantex_client()
            # ``enforce`` verifies the grant JWT against Grantex's JWKS with a
            # synchronous HTTPS fetch; run it off the event loop.
            result = await asyncio.to_thread(
                grantex.enforce,
                grant_token=effective_token,
                connector=connector_name,
                tool=tool_name,
                amount=amount,
            )
            if not result.allowed:
                if self.audit:
                    await self.audit.log(
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        tool_name=tool_name,
                        action="scope_denied",
                        outcome="blocked",
                        details={"reason": result.reason},
                    )
                return {"error": {"code": "E1007", "message": f"scope_denied: {result.reason}"}}
        elif agent_scopes:
            # Legacy fallback for HS256 tokens without Grantex
            resource = tool_name.split("_", 1)[-1] if "_" in tool_name else tool_name
            if governance_decision is not None:
                permission = "read" if governance_decision.risk is ActionRisk.READ else "write"
            else:
                # Relaxed-only compatibility path. Strict runtimes can never
                # reach this heuristic because governance context is required.
                permission = (
                    "write"
                    if any(
                        word in tool_name
                        for word in (
                            "create",
                            "post",
                            "update",
                            "delete",
                            "send",
                            "file",
                            "initiate",
                            "queue",
                        )
                    )
                    else "read"
                )
            allowed, reason = check_scope(agent_scopes, connector_name, permission, resource, amount)
            if not allowed:
                if "cap_exceeded" in reason:
                    code, action_type = "E1008", "cap_exceeded"
                else:
                    code, action_type = "E1007", "scope_denied"
                if self.audit:
                    await self.audit.log(
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        tool_name=tool_name,
                        action=action_type,
                        outcome="blocked",
                        details={"reason": reason},
                    )
                return {"error": {"code": code, "message": f"{action_type}: {reason}"}}
        else:
            # A missing grant must never turn into an authorization bypass.
            # Legacy callers remain supported only when they provide an
            # explicit, non-empty scope set that can be evaluated above.
            reason = "missing_grant_and_legacy_scopes"
            if self.audit:
                await self.audit.log(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    tool_name=tool_name,
                    action="scope_denied",
                    outcome="blocked",
                    details={"reason": reason},
                )
            return {
                "error": {
                    "code": "E1007",
                    "message": f"scope_denied: {reason}",
                }
            }

        # 2. Check rate limit
        if self.rate_limiter:
            rl_result = await self.rate_limiter.check(tenant_id, connector_name)
            if not rl_result.allowed:
                return {
                    "error": {
                        "code": "E1003",
                        "message": "Rate limit exceeded",
                        "retry_after_seconds": rl_result.retry_after_seconds,
                    }
                }

        # 3. Reserve the idempotency key (SET NX) so two concurrent calls with
        # the same key cannot both pass a check-then-store race and execute
        # the side effect twice. The reservation is released on any failure.
        scoped_idempotency_key = (
            f"{company_id or '_global'}:{idempotency_key}" if idempotency_key else None
        )
        reserved = False
        if scoped_idempotency_key and self.idempotency:
            reserved, cached = await self.idempotency.reserve(tenant_id, scoped_idempotency_key)
            if cached is not None:
                return cached
            if not reserved:
                return {
                    "error": {
                        "code": "E1009",
                        "message": "idempotent_request_in_progress: a call with this key is already executing",
                    }
                }

        async def _release_reservation() -> None:
            if reserved and scoped_idempotency_key and self.idempotency:
                await self.idempotency.release(tenant_id, scoped_idempotency_key)

        # 4. Resolve connector — tenant-scoped + global fallback
        connector = self._connectors.get((tenant_id, company_id, connector_name))
        if connector is None and company_id is None:
            connector = self._connectors.get(("_global", None, connector_name))
        if not connector:
            connector = await self._resolve_connector(tenant_id, company_id, connector_name)
        if not connector:
            await _release_reservation()
            return {"error": {"code": "E1005", "message": f"Connector not found: {connector_name}"}}

        # Execute with RAW params — connectors need the real values
        # (account numbers, emails, identifiers) to perform the business
        # action. PII is masked ONLY for audit logging below.
        try:
            result = await connector.execute_tool(tool_name, params)
            latency_ms = int((time.monotonic() - start_time) * 1000)

            # 5. Mask PII in params + result for audit/logging ONLY —
            # never feed the masked version to the connector.
            masked_params = mask_pii(params) if isinstance(params, dict) else params
            masked_result = mask_pii(result) if isinstance(result, dict) else result

            # 6. Store idempotency result (unmasked — it's server-side).
            # An error payload is not a completed side effect: release the
            # reservation so a retry with the same key can run.
            if scoped_idempotency_key and self.idempotency:
                if isinstance(result, dict) and result.get("error"):
                    await _release_reservation()
                else:
                    await self.idempotency.store(tenant_id, scoped_idempotency_key, result)

            # 7. Audit log (masked)
            if self.audit:
                input_hash = hashlib.sha256(str(masked_params).encode()).hexdigest()[:16]
                output_hash = hashlib.sha256(str(masked_result).encode()).hexdigest()[:16]
                await self.audit.log(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    tool_name=tool_name,
                    action="execute",
                    outcome="success",
                    details={
                        "latency_ms": latency_ms,
                        "input_hash": input_hash,
                        "output_hash": output_hash,
                    },
                )

            return result

        # enterprise-gate: broad-except-ok reason=tool-execution-boundary-returns-explicit-error-result
        except Exception as e:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            await _release_reservation()
            if self.audit:
                await self.audit.log(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    tool_name=tool_name,
                    action="execute",
                    outcome="error",
                    details={"error": str(e), "latency_ms": latency_ms},
                )
            return {"error": {"code": "E1001", "message": str(e)}}

    async def _resolve_connector(
        self,
        tenant_id: str,
        company_id: str | None,
        connector_name: str,
    ) -> Any | None:
        """Dynamically resolve and connect a connector from registry + DB config.

        P1.3: Uses per-connector asyncio.Lock to prevent races where two
        concurrent requests load the same connector with different configs.
        Cache is keyed by (tenant_id, connector_name) to prevent cross-tenant
        credential confusion.
        """
        from connectors.registry import ConnectorRegistry

        cache_key = (tenant_id, company_id, connector_name)
        lock = await self._get_connector_lock(tenant_id, company_id, connector_name)

        async with lock:
            # Double-check after acquiring lock — another task may have populated the cache
            if cache_key in self._connectors:
                return self._connectors[cache_key]

            connector_cls = ConnectorRegistry.get(connector_name)
            if not connector_cls:
                return None

            # Load config from the ENCRYPTED connector_configs table first
            # (credentials_encrypted JSONB), falling back to the legacy
            # Connector.auth_config (plaintext) for backward compatibility.
            # Decryption happens at execution time only — never cached in
            # cleartext in memory.
            # Exact encrypted config is mandatory. Any lookup/decryption
            # failure returns unavailable instead of constructing a provider
            # with empty defaults.
            config: dict[str, Any] = {}
            try:
                import json as _json
                import uuid as _uuid

                from sqlalchemy import select

                from core.database import get_tenant_session
                from core.models.company import Company
                from core.models.connector_config import ConnectorConfig

                tid = _uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
                company_uuid = _uuid.UUID(company_id) if company_id else None
                if company_uuid is not None:
                    async with get_tenant_session(tid) as tenant_session:
                        company_result = await tenant_session.execute(
                            select(Company.id).where(
                                Company.id == company_uuid,
                                Company.tenant_id == tid,
                            )
                        )
                        if company_result.scalar_one_or_none() is None:
                            logger.warning(
                                "connector_company_scope_invalid",
                                tenant_id=str(tid),
                                company_id=str(company_uuid),
                                connector=connector_name,
                            )
                            return None
                async with get_tenant_session(tid, company_uuid) as session:
                    # Preferred: encrypted connector config
                    cc_result = await session.execute(
                        select(ConnectorConfig).where(
                            ConnectorConfig.tenant_id == tid,
                            (
                                ConnectorConfig.company_id == company_uuid
                                if company_uuid is not None
                                else ConnectorConfig.company_id.is_(None)
                            ),
                            ConnectorConfig.connector_name == connector_name,
                        )
                    )
                    cc = cc_result.scalar_one_or_none()
                    if cc and cc.credentials_encrypted:
                        creds = cc.credentials_encrypted
                        if isinstance(creds, str):
                            creds = _json.loads(creds)
                        # Decrypt if wrapped by tenant-aware encryption
                        if isinstance(creds, dict) and "_encrypted" in creds:
                            from core.crypto import decrypt_for_tenant

                            # KMS-backed decrypt is synchronous (gRPC); keep it
                            # off the event loop.
                            raw = await asyncio.to_thread(decrypt_for_tenant, creds["_encrypted"])
                            creds = _json.loads(raw)
                        # Merge non-secret config with decrypted creds
                        config = {**(cc.config or {}), **(creds or {})}
                    else:
                        return None
                    # No fallback to plaintext Connector.auth_config — all
                    # secrets must be in encrypted ConnectorConfig after backfill.
            # enterprise-gate: broad-except-ok reason=connector-config-load-failure-returns-unavailable
            except Exception as e:
                logger.warning("connector_config_load_failed", connector=connector_name, error=str(e))
                return None

            connector = connector_cls(config=config)
            try:
                await connector.connect()
                # P1.3: Only cache on successful connect — prevent caching broken connectors
                self._connectors[cache_key] = connector
                return connector
            # enterprise-gate: broad-except-ok reason=connector-connect-failure-returns-unavailable-not-broken-connector
            except Exception as e:
                # Critical Analysis #6: Do NOT return a broken connector.
                # Returning it would cause silent downstream failures.
                logger.warning(
                    "connector_connect_failed",
                    connector=connector_name,
                    error=str(e),
                )
                return None
