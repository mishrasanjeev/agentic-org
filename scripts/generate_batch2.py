#!/usr/bin/env python3
"""Generate batch 2: Auth, Tool Gateway, LLM, Connectors framework."""

import os
import textwrap

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def w(rel_path, content):
    full = os.path.join(BASE, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content).lstrip("\n"))
    print(f"  {rel_path}")


def gen_auth():
    w("auth/__init__.py", '"""Authentication layer for AgenticOrg."""\n')

    w(
        "auth/jwt.py",
        '''
    """JWT validation using RS256 with JWKS support."""
    from __future__ import annotations

    import time
    from typing import Any

    import httpx
    from jose import JWTError, jwt
    from jose.backends import RSAKey

    from core.config import settings, external_keys
    from core.schemas.errors import ErrorCode, make_error

    _jwks_cache: dict[str, Any] = {}
    _jwks_cache_time: float = 0
    JWKS_CACHE_TTL = 3600


    async def _fetch_jwks() -> dict[str, Any]:
        global _jwks_cache, _jwks_cache_time
        if _jwks_cache and (time.time() - _jwks_cache_time) < JWKS_CACHE_TTL:
            return _jwks_cache
        async with httpx.AsyncClient() as client:
            resp = await client.get(settings.jwt_public_key_url)
            resp.raise_for_status()
            _jwks_cache = resp.json()
            _jwks_cache_time = time.time()
        return _jwks_cache


    async def validate_token(token: str) -> dict[str, Any]:
        """Validate a JWT and return its claims. Raises on failure."""
        try:
            jwks = await _fetch_jwks()
            unverified_header = jwt.get_unverified_header(token)

            # Reject alg:none attacks (SEC-AUTH-004)
            if unverified_header.get("alg", "").lower() == "none":
                raise ValueError("Algorithm none is not permitted")

            # Find matching key
            kid = unverified_header.get("kid")
            rsa_key = None
            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    rsa_key = key
                    break
            if not rsa_key:
                raise ValueError(f"No matching key found for kid={kid}")

            payload = jwt.decode(
                token,
                rsa_key,
                algorithms=["RS256"],
                audience="agenticorg-tool-gateway",
            )
            return payload
        except JWTError as e:
            raise ValueError(f"Token validation failed: {e}") from e


    def extract_scopes(claims: dict[str, Any]) -> list[str]:
        return claims.get("grantex:scopes", [])


    def extract_tenant_id(claims: dict[str, Any]) -> str:
        return claims.get("agenticorg:tenant_id", "")


    def extract_agent_id(claims: dict[str, Any]) -> str:
        return claims.get("agenticorg:agent_id", "")
    ''',
    )

    w(
        "auth/grantex.py",
        '''
    """Grantex/OAuth2 client for platform and agent token management."""
    from __future__ import annotations

    from datetime import datetime, timezone
    from typing import Any

    import httpx

    from core.config import external_keys


    class GrantexClient:
        """Manages OAuth2 tokens via the Grantex authorization server."""

        def __init__(self):
            self.token_server = external_keys.grantex_token_server
            self.client_id = external_keys.grantex_client_id
            self.client_secret = external_keys.grantex_client_secret
            self._platform_token: str | None = None
            self._platform_token_exp: float = 0

        async def get_platform_token(self) -> str:
            """Obtain platform-level token via client_credentials grant."""
            now = datetime.now(timezone.utc).timestamp()
            if self._platform_token and now < self._platform_token_exp - 60:
                return self._platform_token

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.token_server}/oauth2/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "scope": "agenticorg:orchestrate agenticorg:agents:read",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                self._platform_token = data["access_token"]
                self._platform_token_exp = now + data.get("expires_in", 3600)
                return self._platform_token

        async def delegate_agent_token(
            self, agent_id: str, agent_type: str, scopes: list[str], ttl: int = 3600
        ) -> dict[str, Any]:
            """Obtain scoped agent token via delegation grant."""
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.token_server}/oauth2/token",
                    data={
                        "grant_type": "urn:grantex:agent_delegation",
                        "agent_id": agent_id,
                        "agent_type": agent_type,
                        "delegated_scopes": " ".join(scopes),
                        "ttl": str(ttl),
                    },
                    headers={"Authorization": f"Bearer {await self.get_platform_token()}"},
                )
                resp.raise_for_status()
                return resp.json()

        async def revoke_token(self, token: str) -> None:
            """Revoke a specific token."""
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{self.token_server}/oauth2/revoke",
                    data={"token": token},
                    headers={"Authorization": f"Bearer {await self.get_platform_token()}"},
                )


    grantex_client = GrantexClient()
    ''',
    )

    w(
        "auth/token_pool.py",
        '''
    """Redis-backed token pool for agent tokens."""
    from __future__ import annotations

    import asyncio
    import json
    from typing import Any

    import redis.asyncio as aioredis

    from core.config import settings
    from auth.grantex import grantex_client


    class TokenPool:
        """Cache and manage agent tokens in Redis."""

        def __init__(self):
            self.redis: aioredis.Redis | None = None
            self._refresh_tasks: dict[str, asyncio.Task] = {}

        async def init(self):
            self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            # Subscribe to revocation channel
            pubsub = self.redis.pubsub()
            await pubsub.subscribe("agenticorg:token:revoke")
            asyncio.create_task(self._listen_revocations(pubsub))

        async def get_token(self, agent_id: str) -> str | None:
            """Get cached token for an agent."""
            if not self.redis:
                return None
            data = await self.redis.get(f"agent:{agent_id}:token")
            if data:
                token_data = json.loads(data)
                return token_data.get("access_token")
            return None

        async def store_token(self, agent_id: str, token_data: dict[str, Any]) -> None:
            """Store token with TTL matching token expiry."""
            if not self.redis:
                return
            ttl = token_data.get("expires_in", 3600)
            await self.redis.setex(
                f"agent:{agent_id}:token",
                ttl,
                json.dumps(token_data),
            )
            # Schedule refresh at 50% TTL
            self._schedule_refresh(agent_id, ttl // 2)

        async def revoke_token(self, agent_id: str) -> None:
            """Revoke token and broadcast to all pool nodes."""
            if not self.redis:
                return
            await self.redis.delete(f"agent:{agent_id}:token")
            await self.redis.publish("agenticorg:token:revoke", agent_id)
            if agent_id in self._refresh_tasks:
                self._refresh_tasks[agent_id].cancel()
                del self._refresh_tasks[agent_id]

        def _schedule_refresh(self, agent_id: str, delay: int) -> None:
            if agent_id in self._refresh_tasks:
                self._refresh_tasks[agent_id].cancel()
            self._refresh_tasks[agent_id] = asyncio.create_task(
                self._refresh_after(agent_id, delay)
            )

        async def _refresh_after(self, agent_id: str, delay: int) -> None:
            await asyncio.sleep(delay)
            # Fetch agent config and refresh token
            # (In production, load agent_type and scopes from DB)
            # For now, just delete expired token
            await self.redis.delete(f"agent:{agent_id}:token")

        async def _listen_revocations(self, pubsub) -> None:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    agent_id = message["data"]
                    await self.redis.delete(f"agent:{agent_id}:token")

        async def close(self):
            for task in self._refresh_tasks.values():
                task.cancel()
            if self.redis:
                await self.redis.close()


    token_pool = TokenPool()
    ''',
    )

    w(
        "auth/scopes.py",
        '''
    """Scope parsing and enforcement per PRD naming convention."""
    from __future__ import annotations

    import re
    from dataclasses import dataclass
    from typing import Optional


    @dataclass
    class ParsedScope:
        """Parsed scope components."""
        category: str        # tool | agenticorg
        connector: str       # oracle_fusion, etc.
        permission: str      # read | write | admin
        resource: str        # purchase_order, journal_entry, etc.
        cap: Optional[int] = None  # Capped amount for write scopes

    # Pattern: tool:{connector}:{perm}:{resource}[:capped:{N}]
    SCOPE_PATTERN = re.compile(
        r"^(tool|agenticorg):([\\w]+):([\\w]+)(?::([\\w]+))?(?::capped:(\\d+))?$"
    )


    def parse_scope(scope_str: str) -> ParsedScope | None:
        """Parse a scope string into components."""
        m = SCOPE_PATTERN.match(scope_str)
        if not m:
            return None
        return ParsedScope(
            category=m.group(1),
            connector=m.group(2),
            permission=m.group(3),
            resource=m.group(4) or "",
            cap=int(m.group(5)) if m.group(5) else None,
        )


    def check_scope(
        granted_scopes: list[str],
        required_connector: str,
        required_permission: str,
        required_resource: str,
        amount: float | None = None,
    ) -> tuple[bool, str]:
        """Check if granted scopes allow the requested operation.

        Returns (allowed: bool, reason: str).
        """
        for scope_str in granted_scopes:
            parsed = parse_scope(scope_str)
            if not parsed:
                continue

            # Admin scope covers everything for that connector
            if parsed.connector == required_connector and parsed.permission == "admin":
                return True, "admin_scope"

            # Exact match
            if (
                parsed.connector == required_connector
                and parsed.permission == required_permission
                and parsed.resource == required_resource
            ):
                # Check cap if applicable
                if parsed.cap is not None and amount is not None:
                    if amount > parsed.cap:
                        return False, f"cap_exceeded:{parsed.cap}"
                return True, "scope_match"

        return False, "no_matching_scope"


    def validate_clone_scopes(parent_scopes: list[str], child_scopes: list[str]) -> list[str]:
        """Ensure child scopes do not exceed parent scopes (scope ceiling).

        Returns list of violations (empty = valid).
        """
        violations = []
        for child_scope in child_scopes:
            child_parsed = parse_scope(child_scope)
            if not child_parsed:
                continue

            # Find matching parent scope
            found_parent = False
            for parent_scope in parent_scopes:
                parent_parsed = parse_scope(parent_scope)
                if not parent_parsed:
                    continue

                if (
                    parent_parsed.connector == child_parsed.connector
                    and parent_parsed.permission == child_parsed.permission
                    and parent_parsed.resource == child_parsed.resource
                ):
                    # Check cap elevation
                    if child_parsed.cap and parent_parsed.cap:
                        if child_parsed.cap > parent_parsed.cap:
                            violations.append(
                                f"Cap elevation: {child_scope} exceeds parent {parent_scope}"
                            )
                    found_parent = True
                    break

                # Admin covers all
                if parent_parsed.connector == child_parsed.connector and parent_parsed.permission == "admin":
                    found_parent = True
                    break

            if not found_parent:
                violations.append(f"Scope not in parent: {child_scope}")

        return violations
    ''',
    )

    w(
        "auth/opa.py",
        '''
    """OPA (Open Policy Agent) client for authorization decisions."""
    from __future__ import annotations

    from typing import Any

    import httpx


    class OPAClient:
        """Evaluate authorization policies against OPA."""

        def __init__(self, opa_url: str = "http://localhost:8181"):
            self.opa_url = opa_url

        async def evaluate(self, policy_path: str, input_data: dict[str, Any]) -> bool:
            """Evaluate a policy and return allow/deny."""
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{self.opa_url}/v1/data/{policy_path}",
                        json={"input": input_data},
                        timeout=5.0,
                    )
                    if resp.status_code == 200:
                        result = resp.json()
                        return result.get("result", {}).get("allow", False)
                    return False
            except httpx.HTTPError:
                # Fail closed — deny on OPA unavailability
                return False


    opa_client = OPAClient()
    ''',
    )

    w(
        "auth/middleware.py",
        '''
    """FastAPI auth middleware — JWT validation, tenant context, rate limiting."""
    from __future__ import annotations

    import time
    from collections import defaultdict
    from typing import Any
    from uuid import UUID

    from fastapi import HTTPException, Request, Response
    from starlette.middleware.base import BaseHTTPMiddleware

    from auth.jwt import validate_token, extract_tenant_id, extract_scopes
    from core.schemas.errors import ErrorCode

    # Rate limiting: track failed attempts per IP
    _failed_attempts: dict[str, list[float]] = defaultdict(list)
    _blocked_ips: dict[str, float] = {}
    BLOCK_DURATION = 900  # 15 minutes
    MAX_FAILURES = 10
    FAILURE_WINDOW = 60  # 1 minute


    class AuthMiddleware(BaseHTTPMiddleware):
        """Validate JWT, set tenant context, enforce rate limits."""

        EXEMPT_PATHS = {"/api/v1/health", "/docs", "/openapi.json", "/redoc"}

        async def dispatch(self, request: Request, call_next) -> Response:
            # Skip auth for health and docs
            if request.url.path in self.EXEMPT_PATHS:
                return await call_next(request)

            client_ip = request.client.host if request.client else "unknown"

            # Check if IP is blocked
            if client_ip in _blocked_ips:
                if time.time() < _blocked_ips[client_ip]:
                    raise HTTPException(status_code=429, detail="Too many failed attempts")
                else:
                    del _blocked_ips[client_ip]

            # Extract token
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                self._record_failure(client_ip)
                raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

            token = auth_header[7:]
            try:
                claims = await validate_token(token)
            except ValueError as e:
                self._record_failure(client_ip)
                raise HTTPException(status_code=401, detail=str(e))

            # Set request state
            tenant_id = extract_tenant_id(claims)
            request.state.claims = claims
            request.state.tenant_id = tenant_id
            request.state.scopes = extract_scopes(claims)
            request.state.agent_id = claims.get("agenticorg:agent_id")
            request.state.user_sub = claims.get("sub", "")

            # Tenant mismatch check (E4004)
            path_tenant = request.path_params.get("tenant_id")
            if path_tenant and path_tenant != tenant_id:
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": "E4004", "message": "Tenant mismatch"}}
                )

            return await call_next(request)

        def _record_failure(self, ip: str) -> None:
            now = time.time()
            attempts = _failed_attempts[ip]
            # Remove old attempts
            _failed_attempts[ip] = [t for t in attempts if now - t < FAILURE_WINDOW]
            _failed_attempts[ip].append(now)
            if len(_failed_attempts[ip]) >= MAX_FAILURES:
                _blocked_ips[ip] = now + BLOCK_DURATION
    ''',
    )

    print("[OK] Auth")


def gen_tool_gateway():
    w(
        "core/tool_gateway/__init__.py",
        '"""Tool Gateway — auth, rate limit, idempotency, PII mask, audit."""\n',
    )

    w(
        "core/tool_gateway/gateway.py",
        '''
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
    ''',
    )

    w(
        "core/tool_gateway/rate_limiter.py",
        '''
    """Token bucket rate limiter backed by Redis."""
    from __future__ import annotations

    import redis.asyncio as aioredis

    from core.config import settings


    class RateLimiter:
        """Per-connector token bucket rate limiter."""

        def __init__(self):
            self.redis: aioredis.Redis | None = None
            self._default_rpm = 60

        async def init(self):
            self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

        async def check(self, tenant_id: str, connector_name: str, rpm: int | None = None) -> bool:
            """Return True if request is allowed, False if rate limited."""
            if not self.redis:
                return True

            limit = rpm or self._default_rpm
            key = f"ratelimit:{tenant_id}:{connector_name}"
            pipe = self.redis.pipeline()
            pipe.incr(key)
            pipe.ttl(key)
            results = await pipe.execute()
            count, ttl = results

            if ttl == -1:
                await self.redis.expire(key, 60)

            return count <= limit

        async def close(self):
            if self.redis:
                await self.redis.close()
    ''',
    )

    w(
        "core/tool_gateway/idempotency.py",
        '''
    """Idempotency enforcement via Redis."""
    from __future__ import annotations

    import json
    from typing import Any

    import redis.asyncio as aioredis

    from core.config import settings

    IDEMPOTENCY_TTL = 86400  # 24 hours


    class IdempotencyStore:
        """Store and retrieve idempotent results."""

        def __init__(self):
            self.redis: aioredis.Redis | None = None

        async def init(self):
            self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

        async def get(self, tenant_id: str, key: str) -> dict[str, Any] | None:
            if not self.redis:
                return None
            data = await self.redis.get(f"idempotency:{tenant_id}:{key}")
            if data:
                return json.loads(data)
            return None

        async def store(self, tenant_id: str, key: str, result: dict[str, Any]) -> None:
            if not self.redis:
                return
            await self.redis.setex(
                f"idempotency:{tenant_id}:{key}",
                IDEMPOTENCY_TTL,
                json.dumps(result, default=str),
            )

        async def close(self):
            if self.redis:
                await self.redis.close()
    ''',
    )

    w(
        "core/tool_gateway/pii_masker.py",
        '''
    """PII masking — default ON. Masks email, phone, Aadhaar, PAN, bank accounts, IFSC."""
    from __future__ import annotations

    import re
    from typing import Any

    from core.config import settings

    # Patterns for Indian and international PII
    PATTERNS = [
        # Email
        (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}"), lambda m: m.group()[:2] + "***@***"),
        # Indian phone (+91 or 10 digits)
        (re.compile(r"(?:\\+91[\\-\\s]?)?[6-9]\\d{9}"), lambda m: m.group()[:4] + "******"),
        # Aadhaar (12 digits, may have spaces)
        (re.compile(r"\\b\\d{4}[\\s-]?\\d{4}[\\s-]?\\d{4}\\b"), lambda m: "XXXX-XXXX-" + m.group()[-4:]),
        # PAN (ABCDE1234F)
        (re.compile(r"\\b[A-Z]{5}\\d{4}[A-Z]\\b"), lambda m: "XXXXX" + m.group()[-5:]),
        # Bank account (8-18 digits)
        (re.compile(r"\\b\\d{8,18}\\b"), lambda m: "****" + m.group()[-4:] if len(m.group()) > 6 else m.group()),
        # IFSC
        (re.compile(r"\\b[A-Z]{4}0[A-Z0-9]{6}\\b"), lambda m: m.group()[:4] + "0******"),
    ]


    def mask_string(value: str) -> str:
        """Mask PII patterns in a string."""
        if not settings.pii_masking:
            return value
        for pattern, replacer in PATTERNS:
            value = pattern.sub(replacer, value)
        return value


    def mask_pii(data: Any) -> Any:
        """Recursively mask PII in dicts, lists, and strings."""
        if not settings.pii_masking:
            return data
        if isinstance(data, str):
            return mask_string(data)
        if isinstance(data, dict):
            return {k: mask_pii(v) for k, v in data.items()}
        if isinstance(data, list):
            return [mask_pii(item) for item in data]
        return data
    ''',
    )

    w(
        "core/tool_gateway/audit_logger.py",
        '''
    """Audit log writer — append-only with HMAC-SHA256 signature."""
    from __future__ import annotations

    import hashlib
    import hmac
    import json
    import uuid
    from datetime import datetime, timezone
    from typing import Any

    import structlog

    from core.config import settings

    logger = structlog.get_logger()


    class AuditLogger:
        """Write tamper-evident audit log entries."""

        def __init__(self, db_session_factory=None):
            self._db = db_session_factory
            self._secret = settings.secret_key.encode()

        def _sign(self, data: dict[str, Any]) -> str:
            """Compute HMAC-SHA256 signature for an audit entry."""
            payload = json.dumps(data, sort_keys=True, default=str)
            return hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()

        async def log(
            self,
            tenant_id: str,
            agent_id: str | None = None,
            tool_name: str = "",
            action: str = "",
            outcome: str = "",
            details: dict[str, Any] | None = None,
            workflow_run_id: str | None = None,
            resource_type: str | None = None,
            resource_id: str | None = None,
            actor_type: str = "agent",
            actor_id: str | None = None,
            trace_id: str | None = None,
        ) -> None:
            """Write an audit log entry. Never update or delete."""
            from core.tool_gateway.pii_masker import mask_pii

            entry = {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "event_type": f"tool.{tool_name}" if tool_name else action,
                "actor_type": actor_type,
                "actor_id": actor_id or agent_id or "",
                "agent_id": agent_id,
                "workflow_run_id": workflow_run_id,
                "resource_type": resource_type or "tool_call",
                "resource_id": resource_id or tool_name,
                "action": action,
                "outcome": outcome,
                "details": mask_pii(details or {}),
                "trace_id": trace_id or "",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            entry["signature"] = self._sign(entry)

            # Log to structured logger (and DB if available)
            logger.info("audit_log", **entry)

            if self._db:
                try:
                    from core.models.audit import AuditLog
                    async with self._db() as session:
                        log_entry = AuditLog(**{
                            k: v for k, v in entry.items()
                            if k not in ("id",)  # Let DB generate ID
                        })
                        session.add(log_entry)
                        await session.commit()
                except Exception as e:
                    logger.error("audit_log_db_error", error=str(e))
    ''',
    )

    print("[OK] Tool Gateway")


def gen_llm():
    w("core/llm/__init__.py", '"""LLM router and adapters."""\n')

    w(
        "core/llm/router.py",
        '''
    """LLM Router — primary Claude, fallback GPT-4o, optional Gemini."""
    from __future__ import annotations

    import time
    from dataclasses import dataclass, field
    from typing import Any

    import structlog

    from core.config import settings, external_keys

    logger = structlog.get_logger()


    @dataclass
    class LLMResponse:
        """Standardized LLM response."""
        content: str
        model: str
        tokens_used: int = 0
        cost_usd: float = 0.0
        latency_ms: int = 0
        raw: dict[str, Any] = field(default_factory=dict)


    class LLMRouter:
        """Route LLM calls to primary/fallback models."""

        def __init__(self):
            self.primary_model = settings.llm_primary
            self.fallback_model = settings.llm_fallback
            self.temperature = settings.llm_temperature

        async def complete(
            self,
            messages: list[dict[str, str]],
            model_override: str | None = None,
            temperature: float | None = None,
            max_tokens: int = 4096,
        ) -> LLMResponse:
            """Send completion request with automatic failover."""
            model = model_override or self.primary_model
            temp = temperature if temperature is not None else self.temperature

            try:
                return await self._call_model(model, messages, temp, max_tokens)
            except Exception as e:
                logger.warning("llm_primary_failed", model=model, error=str(e))
                if model != self.fallback_model:
                    logger.info("llm_falling_back", fallback=self.fallback_model)
                    return await self._call_model(
                        self.fallback_model, messages, temp, max_tokens
                    )
                raise

        async def _call_model(
            self, model: str, messages: list[dict], temperature: float, max_tokens: int
        ) -> LLMResponse:
            start = time.monotonic()

            if "claude" in model:
                return await self._call_claude(model, messages, temperature, max_tokens, start)
            elif "gpt" in model:
                return await self._call_openai(model, messages, temperature, max_tokens, start)
            else:
                raise ValueError(f"Unsupported model: {model}")

        async def _call_claude(self, model, messages, temperature, max_tokens, start) -> LLMResponse:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=external_keys.anthropic_api_key)
            # Separate system message
            system_msg = ""
            user_msgs = []
            for m in messages:
                if m["role"] == "system":
                    system_msg = m["content"]
                else:
                    user_msgs.append(m)

            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_msg,
                messages=user_msgs,
            )
            latency = int((time.monotonic() - start) * 1000)
            tokens = response.usage.input_tokens + response.usage.output_tokens
            # Approximate cost (Claude 3.5 Sonnet pricing)
            cost = (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000
            return LLMResponse(
                content=response.content[0].text,
                model=model, tokens_used=tokens, cost_usd=cost,
                latency_ms=latency, raw=response.model_dump(),
            )

        async def _call_openai(self, model, messages, temperature, max_tokens, start) -> LLMResponse:
            import openai
            client = openai.AsyncOpenAI(api_key=external_keys.openai_api_key)
            response = await client.chat.completions.create(
                model=model, messages=messages,
                temperature=temperature, max_tokens=max_tokens,
            )
            latency = int((time.monotonic() - start) * 1000)
            tokens = response.usage.total_tokens if response.usage else 0
            cost = tokens * 10 / 1_000_000  # Approximate
            return LLMResponse(
                content=response.choices[0].message.content or "",
                model=model, tokens_used=tokens, cost_usd=cost,
                latency_ms=latency, raw=response.model_dump(),
            )


    llm_router = LLMRouter()
    ''',
    )

    print("[OK] LLM")


def gen_connector_framework():
    w("connectors/__init__.py", '"""Connector layer — 42 typed adapters."""\n')
    w("connectors/framework/__init__.py", '"""Connector framework."""\n')

    w(
        "connectors/framework/base_connector.py",
        '''
    """Abstract base connector class."""
    from __future__ import annotations

    import abc
    from typing import Any

    import httpx
    import structlog

    logger = structlog.get_logger()


    class BaseConnector(abc.ABC):
        """Base class for all 42 connectors."""

        name: str = ""
        category: str = ""
        auth_type: str = ""
        base_url: str = ""
        rate_limit_rpm: int = 60
        timeout_ms: int = 10000

        def __init__(self, config: dict[str, Any] | None = None):
            self.config = config or {}
            self._client: httpx.AsyncClient | None = None
            self._auth_headers: dict[str, str] = {}
            self._tool_registry: dict[str, Any] = {}
            self._register_tools()

        @abc.abstractmethod
        def _register_tools(self) -> None:
            """Register all tool functions for this connector."""

        async def connect(self) -> None:
            """Initialize HTTP client and authenticate."""
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_ms / 1000,
                headers=self._auth_headers,
            )
            await self._authenticate()

        async def disconnect(self) -> None:
            if self._client:
                await self._client.aclose()

        async def health_check(self) -> dict[str, Any]:
            """Test connectivity. Override for connector-specific checks."""
            try:
                if self._client:
                    resp = await self._client.get("/")
                    return {"status": "healthy", "http_status": resp.status_code}
                return {"status": "not_connected"}
            except Exception as e:
                return {"status": "unhealthy", "error": str(e)}

        async def execute_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
            """Execute a registered tool function."""
            handler = self._tool_registry.get(tool_name)
            if not handler:
                raise ValueError(f"Tool {tool_name} not registered on {self.name}")
            return await handler(**params)

        @abc.abstractmethod
        async def _authenticate(self) -> None:
            """Perform authentication. Must set self._auth_headers."""

        async def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
            if not self._client:
                raise RuntimeError("Connector not connected")
            resp = await self._client.get(path, params=params)
            resp.raise_for_status()
            return resp.json()

        async def _post(self, path: str, data: dict | None = None) -> dict[str, Any]:
            if not self._client:
                raise RuntimeError("Connector not connected")
            resp = await self._client.post(path, json=data)
            resp.raise_for_status()
            return resp.json()

        async def _put(self, path: str, data: dict | None = None) -> dict[str, Any]:
            if not self._client:
                raise RuntimeError("Connector not connected")
            resp = await self._client.put(path, json=data)
            resp.raise_for_status()
            return resp.json()

        async def _delete(self, path: str) -> dict[str, Any]:
            if not self._client:
                raise RuntimeError("Connector not connected")
            resp = await self._client.delete(path)
            resp.raise_for_status()
            return resp.json()
    ''',
    )

    w(
        "connectors/framework/auth_adapters.py",
        '''
    """Authentication adapters for various connector auth types."""
    from __future__ import annotations

    from typing import Any

    import httpx


    class OAuth2Adapter:
        """OAuth2 client credentials or authorization code flow."""

        def __init__(self, token_url: str, client_id: str, client_secret: str, scope: str = ""):
            self.token_url = token_url
            self.client_id = client_id
            self.client_secret = client_secret
            self.scope = scope
            self._token: str | None = None

        async def get_headers(self) -> dict[str, str]:
            if not self._token:
                await self.refresh()
            return {"Authorization": f"Bearer {self._token}"}

        async def refresh(self) -> None:
            async with httpx.AsyncClient() as client:
                resp = await client.post(self.token_url, data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": self.scope,
                })
                resp.raise_for_status()
                self._token = resp.json()["access_token"]


    class APIKeyAdapter:
        """Simple API key auth via header or query param."""

        def __init__(self, api_key: str, header_name: str = "X-API-Key"):
            self.api_key = api_key
            self.header_name = header_name

        async def get_headers(self) -> dict[str, str]:
            return {self.header_name: self.api_key}


    class DSCAdapter:
        """Digital Signature Certificate adapter for Indian government portals."""

        def __init__(self, dsc_path: str, dsc_password: str = ""):
            self.dsc_path = dsc_path
            self.dsc_password = dsc_password

        async def get_headers(self) -> dict[str, str]:
            # DSC signing is done at request level, not via headers
            return {"X-DSC-Signed": "true"}

        async def sign_request(self, data: bytes) -> bytes:
            # In production, use pyOpenSSL or cryptography to sign with DSC
            return data


    class SCIMAdapter:
        """SCIM 2.0 adapter for identity providers like Okta."""

        def __init__(self, base_url: str, token: str):
            self.base_url = base_url
            self.token = token

        async def get_headers(self) -> dict[str, str]:
            return {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/scim+json",
            }
    ''',
    )

    w(
        "connectors/framework/circuit_breaker.py",
        '''
    """Circuit breaker pattern — Redis-backed, per-connector."""
    from __future__ import annotations

    import time
    from enum import Enum
    from typing import Any

    import redis.asyncio as aioredis

    from core.config import settings


    class CircuitState(str, Enum):
        CLOSED = "closed"
        OPEN = "open"
        HALF_OPEN = "half_open"


    class CircuitBreaker:
        """Per-connector circuit breaker."""

        def __init__(
            self,
            failure_threshold: int = 5,
            recovery_timeout: int = 60,
            half_open_max: int = 3,
        ):
            self.failure_threshold = failure_threshold
            self.recovery_timeout = recovery_timeout
            self.half_open_max = half_open_max
            self.redis: aioredis.Redis | None = None

        async def init(self):
            self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

        async def can_execute(self, connector_name: str) -> bool:
            """Check if the circuit allows a request."""
            if not self.redis:
                return True
            state = await self._get_state(connector_name)
            if state == CircuitState.CLOSED:
                return True
            if state == CircuitState.OPEN:
                last_fail = float(await self.redis.get(f"cb:{connector_name}:last_fail") or 0)
                if time.time() - last_fail > self.recovery_timeout:
                    await self._set_state(connector_name, CircuitState.HALF_OPEN)
                    return True
                return False
            # Half open — allow limited requests
            return True

        async def record_success(self, connector_name: str) -> None:
            if not self.redis:
                return
            await self.redis.set(f"cb:{connector_name}:failures", 0)
            await self._set_state(connector_name, CircuitState.CLOSED)

        async def record_failure(self, connector_name: str) -> None:
            if not self.redis:
                return
            failures = await self.redis.incr(f"cb:{connector_name}:failures")
            await self.redis.set(f"cb:{connector_name}:last_fail", str(time.time()))
            if failures >= self.failure_threshold:
                await self._set_state(connector_name, CircuitState.OPEN)

        async def _get_state(self, name: str) -> CircuitState:
            if not self.redis:
                return CircuitState.CLOSED
            state = await self.redis.get(f"cb:{name}:state")
            return CircuitState(state) if state else CircuitState.CLOSED

        async def _set_state(self, name: str, state: CircuitState) -> None:
            if self.redis:
                await self.redis.set(f"cb:{name}:state", state.value)

        async def close(self):
            if self.redis:
                await self.redis.close()
    ''',
    )

    print("[OK] Connector Framework")


if __name__ == "__main__":
    print("Generating batch 2...")
    gen_auth()
    gen_tool_gateway()
    gen_llm()
    gen_connector_framework()
    print("Done with batch 2!")
