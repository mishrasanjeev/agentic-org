"""Main Tool Gateway — validates and executes every agent tool call."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

import structlog

from auth.scopes import check_scope
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
        self._connectors: dict[tuple[str, str], Any] = {}
        self._connector_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()  # protects _connector_locks dict itself

    def register_connector(self, name: str, connector: Any, tenant_id: str = "_global") -> None:
        """Register a pre-configured connector. Use tenant_id='_global' for shared instances."""
        self._connectors[(tenant_id, name)] = connector

    async def _get_connector_lock(self, tenant_id: str, connector_name: str) -> asyncio.Lock:
        """Get or create per-connector lock atomically."""
        key = (tenant_id, connector_name)
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
    ) -> dict[str, Any]:
        """Execute a tool call through the gateway pipeline."""
        start_time = time.monotonic()

        # 1. Validate scope via Grantex enforce (manifest-based, offline JWT verification)
        effective_token = grant_token or getattr(self, "_current_grant_token", None)
        if effective_token:
            from core.langgraph.grantex_auth import get_grantex_client

            grantex = get_grantex_client()
            result = grantex.enforce(
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
            permission = (
                "write"
                if any(
                    w in tool_name
                    for w in ("create", "post", "update", "delete", "send", "file", "initiate", "queue")
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

        # 3. Check idempotency
        if idempotency_key and self.idempotency:
            cached = await self.idempotency.get(tenant_id, idempotency_key)
            if cached is not None:
                return cached

        # 4. Resolve connector — tenant-scoped + global fallback
        connector = (
            self._connectors.get((tenant_id, connector_name))
            or self._connectors.get(("_global", connector_name))
        )
        if not connector:
            connector = await self._resolve_connector(tenant_id, connector_name)
        if not connector:
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

            # 6. Store idempotency result (unmasked — it's server-side)
            if idempotency_key and self.idempotency:
                await self.idempotency.store(tenant_id, idempotency_key, result)

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

        except Exception as e:
            latency_ms = int((time.monotonic() - start_time) * 1000)
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

    async def _resolve_connector(self, tenant_id: str, connector_name: str) -> Any | None:
        """Dynamically resolve and connect a connector from registry + DB config.

        P1.3: Uses per-connector asyncio.Lock to prevent races where two
        concurrent requests load the same connector with different configs.
        Cache is keyed by (tenant_id, connector_name) to prevent cross-tenant
        credential confusion.
        """
        from connectors.registry import ConnectorRegistry

        cache_key = (tenant_id, connector_name)
        lock = await self._get_connector_lock(tenant_id, connector_name)

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
            config: dict[str, Any] = {}
            try:
                import json as _json
                import uuid as _uuid

                from sqlalchemy import select

                from core.database import get_tenant_session
                from core.models.connector import Connector
                from core.models.connector_config import ConnectorConfig

                tid = _uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
                async with get_tenant_session(tid) as session:
                    # Preferred: encrypted connector config
                    cc_result = await session.execute(
                        select(ConnectorConfig).where(
                            ConnectorConfig.tenant_id == tid,
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

                            raw = decrypt_for_tenant(creds["_encrypted"])
                            creds = _json.loads(raw)
                        # Merge non-secret config with decrypted creds
                        config = {**(cc.config or {}), **(creds or {})}
                    else:
                        # Fallback: legacy plaintext auth_config
                        result = await session.execute(
                            select(Connector).where(
                                Connector.tenant_id == tid,
                                Connector.name == connector_name,
                            )
                        )
                        db_connector = result.scalar_one_or_none()
                        if db_connector:
                            config = db_connector.auth_config or {}
            except Exception as e:
                logger.warning("connector_config_load_failed", connector=connector_name, error=str(e))

            connector = connector_cls(config=config)
            try:
                await connector.connect()
                # P1.3: Only cache on successful connect — prevent caching broken connectors
                self._connectors[cache_key] = connector
                return connector
            except Exception as e:
                # Critical Analysis #6: Do NOT return a broken connector.
                # Returning it would cause silent downstream failures.
                logger.warning(
                    "connector_connect_failed",
                    connector=connector_name,
                    error=str(e),
                )
                return None
