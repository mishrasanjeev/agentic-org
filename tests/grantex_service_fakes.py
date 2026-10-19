# SPDX-License-Identifier: Apache-2.0
"""A fake Grantex for the arbiter countersignature and witness checkpoint routes (contract section 7).

Keys are generated at test time. ``mode`` switches the answers so tests can
replay an unreachable Grantex, a grant bound to another payload, a fork, and so on.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from core.commerce.grantex_service import GrantexServiceClient
from core.commerce.oacp_grantex_trust import OacpGrantexArtifactVerifier, OacpGrantexTrustConfig
from tests.oacp_signing import ISSUER, JWKS_URI, EcSigningKey, sign_compact_jwt

BASE_URL = "https://grantex.example"


@dataclass
class FakeGrantexService:
    key: EcSigningKey = field(default_factory=lambda: EcSigningKey(kid="platform-test-1"))
    mode: str = "ok"
    posted: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    witnessed: dict[tuple[str, int], tuple[str, str, str]] = field(default_factory=dict)

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.key.public_jwk()]}

    def _grant(self, body: dict[str, Any]) -> httpx.Response:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": "urn:grantex:decision",
            "sub": body["buyer_agent_id"],
            "jti": f"dg_{body['decision_id'][-12:]}",
            "iat": now,
            "exp": now + 300,
            "approver_auth": "policy_arbiter",
            "amr": ["mandate", "consent"],
            "dwell_ms": 0,
            "dwell_source": "server",
            "action_hash": body["action_hash"],
            "decision_request": body["decision_id"],
        }
        if self.mode == "wrong_action_hash":
            claims["action_hash"] = "f" * 64
        if self.mode == "human_profile":
            claims["approver_auth"] = "sso"
            claims["acr"] = "urn:example:acr:human"
        signer = EcSigningKey(kid=self.key.kid) if self.mode == "forged_signature" else self.key
        token = sign_compact_jwt(signer, claims, typ="decision+jwt")
        return httpx.Response(
            201,
            json={
                "decision_id": body["decision_id"],
                "outcome": "allow",
                "decision_grant": token,
                "jti": claims["jti"],
                "exp": claims["exp"],
            },
        )

    def _witness(self, body: dict[str, Any]) -> httpx.Response:
        key = (body["tenant_ref"], body["head_seq"])
        stored = self.witnessed.get(key)
        if stored is not None and stored[0] != body["head_hash"]:
            return httpx.Response(
                409,
                json={
                    "code": "WITNESS_FORK",
                    "message": "A different head_hash is already witnessed",
                    "fork": {
                        "fork_id": "wfork_TEST",
                        "head_seq": body["head_seq"],
                        "witnessed": {"head_hash": stored[0], "witness_id": stored[1], "countersignature": stored[2]},
                        "presented": {"head_hash": body["head_hash"], "anchored_at": body["anchored_at"]},
                    },
                },
            )
        witness_id = stored[1] if stored else f"awit_{len(self.witnessed) + 1:04d}"
        claims = {
            "iss": ISSUER,
            "jti": witness_id,
            "iat": int(time.time()),
            **{k: body[k] for k in ("source", "tenant_ref", "chain", "head_seq", "head_hash", "anchored_at")},
            "witnessed_at": "2026-10-20T09:00:00.000Z",
        }
        if self.mode == "witness_wrong_hash":
            claims["head_hash"] = "e" * 64
        token = sign_compact_jwt(self.key, claims, typ="audit-witness+jwt")
        self.witnessed[key] = (body["head_hash"], witness_id, token)
        return httpx.Response(
            201 if stored is None else 200,
            json={"witness_id": witness_id, "countersignature": token, "witnessed_at": claims["witnessed_at"]},
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == JWKS_URI:
            return httpx.Response(200, json=self.jwks())
        if self.mode == "unreachable":
            raise httpx.ConnectError("connection refused", request=request)
        body = json.loads(request.content or b"{}")
        self.posted.append((request.url.path, body))
        assert request.headers.get("Authorization") == "Bearer service-token-placeholder"
        if request.url.path == "/v1/commerce/oacp/arbiter-decisions":
            if self.mode == "http_503":
                return httpx.Response(503, json={"code": "unavailable"})
            if body["outcome"] == "refuse":
                return httpx.Response(
                    201, json={"decision_id": body["decision_id"], "outcome": "refuse", "recorded": True}
                )
            return self._grant(body)
        if request.url.path == "/v1/audit/witness-checkpoints":
            return self._witness(body)
        return httpx.Response(404, json={"code": "not_found"})

    def transport_factory(self) -> Callable[[], httpx.MockTransport]:
        return lambda: httpx.MockTransport(self.handler)

    def client(self) -> GrantexServiceClient:
        verifier = OacpGrantexArtifactVerifier(
            OacpGrantexTrustConfig(issuer=ISSUER, jwks_uri=JWKS_URI, status_list_origins=frozenset({ISSUER})),
            transport_factory=self.transport_factory(),
        )
        return GrantexServiceClient(
            base_url=BASE_URL,
            token="service-token-placeholder",
            verifier=verifier,
            transport_factory=self.transport_factory(),
        )
