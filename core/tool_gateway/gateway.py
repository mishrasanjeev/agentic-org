"""Main Tool Gateway — validates and executes every agent tool call."""
from __future__ import annotations

import hashlib
import time
from typing import Any
from uuid import UUID

import structlog

from auth.scopes import check_scope
from core.schemas.errors import ErrorCode
from core.tool_gateway.rate_limiter import RateLimiter
from core.tool_gateway.idempotency import IdempotencyStore
from core.tool_gateway.pii_masker import mask_pii
from core.tool_gateway.audit_logger import AuditLogger

logger = structlog.get_logger()


class ToolGateway:
    """Central gateway for all agent tool calls."""

    def __init__(
        self,
        rate_limiter: RateLimiter,
        idempotency_store: IdempotencyStore,
        audit_logger: AuditLogger,
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

        # 1. Validate scope
        resource = tool_name.split("_", 1)[-1] if "_" in tool_name else tool_name
        # Determine permission from tool name convention
        permission = "write" if any(
            w in tool_name for w in ("create", "post", "update", "delete", "send", "file", "initiate", "queue")
        ) else "read"

        allowed, reason = check_scope(agent_scopes, connector_name, permission, resource, amount)
        if not allowed:
            if "cap_exceeded" in reason:
                await self.audit.log(
                    tenant_id=tenant_id, agent_id=agent_id, tool_name=tool_name,
                    action="cap_exceeded", outcome="blocked", details={"reason": reason}
                )
                return {"error": {"code": "E1008", "message": f"Cap exceeded: {reason}"}}

            await self.audit.log(
                tenant_id=tenant_id, agent_id=agent_id, tool_name=tool_name,
                action="scope_denied", outcome="blocked", details={"reason": reason}
            )
            logger.warning("scope_denied", agent_id=agent_id, tool=tool_name, reason=reason)
            return {"error": {"code": "E1007", "message": f"Scope denied: {reason}"}}

        # 2. Check rate limit
        allowed_rl = await self.rate_limiter.check(tenant_id, connector_name)
        if not allowed_rl:
            return {"error": {"code": "E1003", "message": "Rate limit exceeded", "retry_after_seconds": 60}}

        # 3. Check idempotency
        if idempotency_key:
            cached = await self.idempotency.get(tenant_id, idempotency_key)
            if cached is not None:
                return cached

        # 4. Execute via connector
        connector = self._connectors.get(connector_name)
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
            if idempotency_key:
                await self.idempotency.store(tenant_id, idempotency_key, result)

            # 7. Audit log
            input_hash = hashlib.sha256(str(masked_params).encode()).hexdigest()[:16]
            output_hash = hashlib.sha256(str(masked_result).encode()).hexdigest()[:16]
            await self.audit.log(
                tenant_id=tenant_id, agent_id=agent_id, tool_name=tool_name,
                action="execute", outcome="success",
                details={"latency_ms": latency_ms, "input_hash": input_hash, "output_hash": output_hash}
            )

            return result

        except Exception as e:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            await self.audit.log(
                tenant_id=tenant_id, agent_id=agent_id, tool_name=tool_name,
                action="execute", outcome="error",
                details={"error": str(e), "latency_ms": latency_ms}
            )
            return {"error": {"code": "E1001", "message": str(e)}}
