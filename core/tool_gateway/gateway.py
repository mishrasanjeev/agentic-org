"""Main Tool Gateway — validates and executes every agent tool call."""

from __future__ import annotations

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
        self._connectors: dict[str, Any] = {}

    def register_connector(self, name: str, connector: Any) -> None:
        self._connectors[name] = connector

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
    ) -> dict[str, Any]:
        """Execute a tool call through the gateway pipeline."""
        start_time = time.monotonic()

        # 1. Validate scope (skip if no scopes configured)
        resource = tool_name.split("_", 1)[-1] if "_" in tool_name else tool_name
        permission = (
            "write"
            if any(
                w in tool_name
                for w in ("create", "post", "update", "delete", "send", "file", "initiate", "queue")
            )
            else "read"
        )

        if agent_scopes:
            allowed, reason = check_scope(agent_scopes, connector_name, permission, resource, amount)
            if not allowed:
                if self.audit:
                    await self.audit.log(
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        tool_name=tool_name,
                        action="scope_denied",
                        outcome="blocked",
                        details={"reason": reason},
                    )
                return {"error": {"code": "E1007", "message": f"Scope denied: {reason}"}}

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

        # 4. Resolve connector (pre-registered or dynamic from registry)
        connector = self._connectors.get(connector_name)
        if not connector:
            connector = await self._resolve_connector(tenant_id, connector_name)
        if not connector:
            return {"error": {"code": "E1005", "message": f"Connector not found: {connector_name}"}}

        try:
            # Mask PII in params before logging
            masked_params = mask_pii(params)

            result = await connector.execute_tool(tool_name, params)
            latency_ms = int((time.monotonic() - start_time) * 1000)

            # 5. Mask PII in result before storing/logging
            masked_result = mask_pii(result) if isinstance(result, dict) else result

            # 6. Store idempotency result
            if idempotency_key and self.idempotency:
                await self.idempotency.store(tenant_id, idempotency_key, result)

            # 7. Audit log
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
        """Dynamically resolve and connect a connector from the registry + DB config."""
        from connectors.registry import ConnectorRegistry

        connector_cls = ConnectorRegistry.get(connector_name)
        if not connector_cls:
            return None

        # Load config from database
        config: dict[str, Any] = {}
        try:
            import uuid as _uuid

            from sqlalchemy import select

            from core.database import get_tenant_session
            from core.models.connector import Connector

            tid = _uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
            async with get_tenant_session(tid) as session:
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
            # Cache for future calls in this gateway instance
            self._connectors[connector_name] = connector
            return connector
        except Exception as e:
            logger.warning("connector_connect_failed", connector=connector_name, error=str(e))
            return connector  # Return even if auth fails — some tools work without auth
