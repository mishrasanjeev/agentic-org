# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The Grantex routes agent identity uses: agent key registration, grant checks and consent grants.

Endpoints (Grantex auth service, developer API key):

``GET    /v1/agents/{id}/keys``                         the agent's registered keys and their status
``POST   /v1/agents/{id}/keys``                         register a public JWK (``pending``)
``POST   /v1/agents/{id}/keys/{thumbprint}/challenge``  a single-use nonce to sign
``POST   /v1/agents/{id}/keys/{thumbprint}/prove``      a compact JWS over the nonce; the key becomes ``active``
``POST   /v1/grants/verify``                            whether a grant token is active, and its claims
``POST   /v1/authorize``                                a consent request for a principal, answered with a consent URL
``GET    /v1/grants?agentId=&principalId=&status=``     grants for an agent and principal
``GET    /v1/grants/{id}``                              one grant and its status
``DELETE /v1/grants/{id}``                              revoke a grant and everything delegated from it

Grantex binds a grant to an agent key itself: once the agent's key is active, grants issued or
delegated to the agent carry ``cnf.jkt`` with the key's thumbprint. There is no route that binds a
caller-chosen key to an existing grant, so this client never claims to; it checks the binding on
the token Grantex issued instead (:meth:`verify_grant`). There is no business-enrolment consent
resource either: the closest primitive is a consent grant (``/v1/authorize`` for the person, with
purpose ``x-agenticorg.agent_enrolment``), revocable with ``DELETE /v1/grants/{id}``.

Everything fails closed: no configured issuer is :class:`GrantexIdentityError`
``grantex_not_configured``; a transport failure is ``grantex_unreachable``; a refusal keeps the
issuer's code. The API key is never logged.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from connectors.framework.agent_identity_provider import (
    AgentIdentityError,
    AgentIdentityProvider,
    AgentRef,
)
from connectors.framework.verification_provider import Deadline

logger = structlog.get_logger()

ENROLMENT_PURPOSE = "x-agenticorg.agent_enrolment"


class GrantexIdentityError(RuntimeError):
    def __init__(self, reason: str, detail: str = "", *, status: int = 502) -> None:
        self.reason = reason
        self.detail = detail
        self.status = status
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass
class GrantexIdentityService:
    base_url: str
    api_key: str
    timeout_seconds: float = 5.0
    #: Injected in tests: a callable returning an ``httpx.AsyncClient``.
    client_factory: Any = None
    _headers: dict[str, str] = field(init=False, default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.base_url or not self.api_key:
            raise GrantexIdentityError("grantex_not_configured", status=503)
        self.base_url = self.base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}

    def _client(self) -> httpx.AsyncClient:
        if self.client_factory is not None:
            return self.client_factory()
        return httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(self.timeout_seconds))

    async def _call(
        self, method: str, path: str, body: Mapping[str, Any] | None = None, params: Mapping[str, str] | None = None
    ) -> tuple[int, dict[str, Any]]:
        try:
            async with self._client() as client:
                response = await client.request(
                    method, path, headers=self._headers, json=dict(body) if body is not None else None,
                    params=dict(params) if params else None, follow_redirects=False,
                )  # fmt: skip
        # enterprise-gate: broad-except-ok reason=grantex-transport-failure-fails-the-identity-check-closed
        except Exception as exc:
            logger.warning("agent_identity_grantex_unreachable", path=path.split("?")[0], error=type(exc).__name__)
            raise GrantexIdentityError("grantex_unreachable", type(exc).__name__) from exc
        if response.status_code == 204:
            return 204, {}
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if response.status_code >= 400:
            code = str(payload.get("code") or response.status_code)
            logger.warning("agent_identity_grantex_refused", status=response.status_code, code=code)
            raise GrantexIdentityError(
                "grantex_unreachable" if response.status_code >= 500 else "grantex_refused",
                code,
                status=502 if response.status_code >= 500 else 409,
            )
        return response.status_code, payload

    # --- agent keys ------------------------------------------------------------------------------

    async def list_keys(self, agent_id: str) -> list[dict[str, Any]]:
        _, body = await self._call("GET", f"/v1/agents/{quote(agent_id, safe='')}/keys")
        keys = body.get("keys")
        return [k for k in keys if isinstance(k, dict)] if isinstance(keys, list) else []

    async def register_key(self, agent_id: str, public_jwk: Mapping[str, Any]) -> dict[str, Any]:
        _, body = await self._call(
            "POST", f"/v1/agents/{quote(agent_id, safe='')}/keys", {"publicJwk": dict(public_jwk)}
        )
        return body

    async def key_challenge(self, agent_id: str, thumbprint: str) -> dict[str, Any]:
        path = f"/v1/agents/{quote(agent_id, safe='')}/keys/{quote(thumbprint, safe='')}/challenge"
        _, body = await self._call("POST", path, {})
        return body

    async def prove_key(self, agent_id: str, thumbprint: str, proof: str) -> dict[str, Any]:
        path = f"/v1/agents/{quote(agent_id, safe='')}/keys/{quote(thumbprint, safe='')}/prove"
        _, body = await self._call("POST", path, {"proof": proof})
        return body

    # --- grants ----------------------------------------------------------------------------------

    async def verify_grant(self, token: str) -> dict[str, Any]:
        """``{"active": bool, "claims": {...}}`` or ``{"active": false, "reason": ...}``."""
        _, body = await self._call("POST", "/v1/grants/verify", {"token": token})
        return body

    async def authorize_consent(
        self, *, agent_id: str, principal_id: str, scopes: list[str], purpose: str, expires_in: str = "24h"
    ) -> dict[str, Any]:
        _, body = await self._call(
            "POST",
            "/v1/authorize",
            {"agentId": agent_id, "principalId": principal_id, "scopes": scopes, "purpose": purpose,
             "expiresIn": expires_in},
        )  # fmt: skip
        return body

    async def active_grants(self, *, agent_id: str, principal_id: str) -> list[dict[str, Any]]:
        _, body = await self._call(
            "GET", "/v1/grants", params={"agentId": agent_id, "principalId": principal_id, "status": "active"}
        )
        grants = body.get("grants")
        return [g for g in grants if isinstance(g, dict)] if isinstance(grants, list) else []

    async def get_grant(self, grant_id: str) -> dict[str, Any]:
        _, body = await self._call("GET", f"/v1/grants/{quote(grant_id, safe='')}")
        return body

    async def revoke_grant(self, grant_id: str) -> bool:
        """True when revoked now; False when Grantex no longer holds it as active (404)."""
        try:
            await self._call("DELETE", f"/v1/grants/{quote(grant_id, safe='')}")
        except GrantexIdentityError as exc:
            if exc.reason == "grantex_refused" and exc.detail == "NOT_FOUND":
                return False
            raise
        return True


def grantex_identity_service() -> GrantexIdentityService | None:
    """The configured service, or None. The issuer has to be named explicitly (``GRANTEX_BASE_URL``)."""
    from core.config import external_keys

    explicit = os.getenv("GRANTEX_BASE_URL", "").strip() or (
        str(external_keys.grantex_base_url or "").strip()
        if "grantex_base_url" in external_keys.model_fields_set
        else ""
    )
    api_key = os.getenv("GRANTEX_API_KEY", "") or str(external_keys.grantex_api_key or "")
    if not explicit or not api_key:
        return None
    return GrantexIdentityService(base_url=explicit, api_key=api_key)


# --- MO-61: register a desk agent's key and prove possession -------------------------------------


async def bind_agent_key(
    service: GrantexIdentityService,
    provider: AgentIdentityProvider,
    agent: AgentRef,
    grantex_agent_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Register the agent's identity key on its Grantex agent and prove possession of it.

    Grants issued or delegated to the agent afterwards carry the key's thumbprint (``cnf.jkt``).
    Returns the key's thumbprint and Grantex status. Idempotent: an active key is left alone.
    """
    deadline = Deadline.after(10)
    try:
        credential = await provider.resolve(agent, deadline=deadline)
    except AgentIdentityError as exc:
        raise GrantexIdentityError(exc.reason, status=503) from exc
    if not credential.public_jwk:
        raise GrantexIdentityError("agent_identity_key_missing", "the provider gave no public key", status=409)
    thumbprint = credential.key_thumbprint
    key = next((k for k in await service.list_keys(grantex_agent_id) if k.get("thumbprint") == thumbprint), None)
    if key is None:
        key = await service.register_key(grantex_agent_id, credential.public_jwk)
        if key.get("thumbprint") not in (None, thumbprint):
            raise GrantexIdentityError("agent_key_thumbprint_mismatch", status=409)
    status = str(key.get("status") or "pending")
    if status == "active":
        return {"thumbprint": thumbprint, "status": "active", "registered": False}
    if status != "pending":
        raise GrantexIdentityError("agent_key_not_usable", status, status=409)
    challenge = await service.key_challenge(grantex_agent_id, thumbprint)
    moment = now or datetime.now(UTC)
    claims = {
        "aud": challenge.get("audience"),
        "sub": challenge.get("subject"),
        "nonce": challenge.get("challenge"),
        "iat": int(moment.timestamp()),
    }
    if not all(claims.values()):
        raise GrantexIdentityError("agent_key_challenge_invalid", status=502)
    try:
        proof = await provider.prove_possession(
            credential, claims, typ=str(challenge.get("typ") or "agent-key-proof+jwt"), deadline=deadline
        )
    except AgentIdentityError as exc:
        raise GrantexIdentityError(exc.reason, status=503) from exc
    proved = await service.prove_key(grantex_agent_id, thumbprint, proof)
    logger.info("agent_identity_key_bound", role=agent.role, status=proved.get("status"))
    return {"thumbprint": thumbprint, "status": str(proved.get("status") or "pending"), "registered": True}
