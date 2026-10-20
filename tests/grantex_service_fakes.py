# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
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

from core.commerce import oacp_mandates
from core.commerce.grantex_service import GrantexServiceClient
from core.commerce.oacp_grantex_trust import OacpGrantexArtifactVerifier, OacpGrantexTrustConfig
from tests.oacp_signing import ISSUER, JWKS_URI, EcSigningKey, sign_compact_jwt, status_list_token

BASE_URL = "https://grantex.example"
MANDATE_TENANT = "cten_TEST01"
MANDATE_MERCHANT = "mch_TEST01"
MANDATE_STATUS_URI = f"{ISSUER}/status/oacp/lst_MANDATES"
# A synthetic Commerce Passport shape (the fake does not verify it; Grantex does).
PASSPORT_JWT = "eyJhbGciOiJFUzI1NiJ9.eyJzdWIiOiJidXllcl9wcmluY2lwYWxfMDEifQ.c2lnbmF0dXJl"


def _jwt_claims(token: str) -> dict[str, Any]:
    import base64

    segment = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def webauthn_assertion() -> dict[str, Any]:
    """A platform-supplied AuthenticationResponseJSON shape (the fake Grantex records it; it verifies nothing)."""
    return {
        "id": "Y3JlZGVudGlhbC0x",
        "rawId": "Y3JlZGVudGlhbC0x",
        "type": "public-key",
        "response": {"clientDataJSON": "e30", "authenticatorData": "AAAA", "signature": "AAAA"},
        "clientExtensionResults": {},
    }


@dataclass
class FakeGrantexService:
    key: EcSigningKey = field(default_factory=lambda: EcSigningKey(kid="platform-test-1"))
    mode: str = "ok"
    posted: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    witnessed: dict[tuple[str, int], tuple[str, str, str]] = field(default_factory=dict)
    # Mandates: signed with an OACP issuer key; refusals per type; status per index; issued tokens.
    mandate_key: EcSigningKey = field(default_factory=lambda: EcSigningKey(kid="oacp-test-mandates"))
    mandate_refusals: dict[str, str] = field(default_factory=dict)
    mandate_statuses: dict[int, int] = field(default_factory=dict)
    mandates: list[tuple[str, str]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    payment_amount_skew: int = 0

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.key.public_jwk(), self.mandate_key.public_jwk()]}

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
        if url == MANDATE_STATUS_URI:
            return self._status_list()
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
        if request.url.path.startswith("/v1/commerce/oacp/mandates/"):
            return self._mandate(request.url.path.rsplit("/", 1)[-1], body)
        return httpx.Response(404, json={"code": "not_found"})

    # --- OACP mandates (contract section 8) ------------------------------------------------------

    def _status_list(self) -> httpx.Response:
        now = int(time.time())
        token = status_list_token(
            self.mandate_key, uri=MANDATE_STATUS_URI, statuses=self.mandate_statuses, iat=now - 5, exp=now + 600
        )
        return httpx.Response(200, text=token, headers={"content-type": "application/statuslist+jwt"})

    def _mandate(self, kind: str, body: dict[str, Any]) -> httpx.Response:
        refusal = self.mandate_refusals.get(kind)
        if refusal is not None:
            return httpx.Response(
                403, json={"error": {"code": refusal, "message": "refused", "details": {"refusal_record_id": "rr_1"}}}
            )
        assert body["tenant_id"] == MANDATE_TENANT and body["passport_jwt"].count(".") == 2
        now = int(time.time())
        idx = len(self.mandates)
        presence = body.get("presence", "human_not_present")
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "sub": "buyer_principal_01",
            "aud": MANDATE_MERCHANT,
            "iat": now,
            "exp": now + 540 - idx * 30,
            "jti": f"oacp_mnd_{kind}_{idx:04d}",
            "mandate_version": "oacp.mandate.v1",
            "tenant_id": MANDATE_TENANT,
            "merchant_id": MANDATE_MERCHANT,
            "buyer_agent_id": "cag_TEST01",
            "presence": presence,
            "amr": ["consent", "otp"],
            "passport_ref": "cpsp_TEST01",
            "consent_ref": "crec_TEST01",
            "status": {"status_list": {"idx": idx, "uri": MANDATE_STATUS_URI}},
        }
        if kind == "intent":
            claims["intent"] = {
                "description_hash": oacp_mandates.checkout_hash(body["description"]),
                "max_amount": body["max_amount"],
                "merchants": [MANDATE_MERCHANT],
                "expires_at": "2099-01-01T00:00:00Z",
                "constraints": [{"type": "payment.amount_range", "max": body["max_amount"]["value"]}],
            }
        elif kind == "cart":
            intent = _jwt_claims(body["intent_mandate"])
            checkout = body["checkout"]
            total = next(t["amount"] for t in checkout["totals"] if t["type"] == "total")
            if total > intent["intent"]["max_amount"]["value"]:
                return httpx.Response(403, json={"error": {"code": "intent_cap_exceeded", "message": "cap"}})
            digest = oacp_mandates.checkout_hash(checkout)
            claims["cart"] = {
                "checkout_hash": digest,
                "checkout_id": checkout["id"],
                "line_items": [
                    {"id": li["id"], "quantity": li["quantity"], "digest": oacp_mandates.checkout_hash(li)}
                    for li in checkout["line_items"]
                ],
                "total": {"value": total, "currency": checkout["currency"]},
            }
            claims["intent_mandate_jti"] = intent["jti"]
            if presence == "human_present":
                assertion = body.get("webauthn_assertion")
                if not isinstance(assertion, dict):
                    return httpx.Response(403, json={"error": {"code": "passkey_evidence_required", "message": "x"}})
                self.assertions.append(assertion)
                claims["amr"] = ["consent", "otp", "passkey"]
                claims["webauthn"] = {
                    "digest": "d" * 64,
                    "rp_id": "wallet.example",
                    "origin": "https://wallet.example",
                    "challenge": digest,
                    "user_verified": True,
                    "asserted_at": "2026-10-20T09:00:00.000Z",
                    "credential_ref": "c" * 64,
                }
        else:
            cart = _jwt_claims(body["cart_mandate"])
            claims["amr"] = cart["amr"]
            claims["presence"] = cart["presence"]
            claims["payment"] = {
                "transaction_id": cart["cart"]["checkout_hash"],
                "amount": cart["cart"]["total"]["value"] + self.payment_amount_skew,
                "currency": cart["cart"]["total"]["currency"],
                "payee": {"id": MANDATE_MERCHANT},
                "rail": body["rail"],
                "token_binding": body["token_binding"],
                "cart_mandate_jti": cart["jti"],
            }
        token = sign_compact_jwt(self.mandate_key, claims, typ=f"oacp-{kind}-mandate+jwt")
        self.mandates.append((kind, token))
        return httpx.Response(
            201,
            json={
                "mandate": token,
                "jti": claims["jti"],
                "mandate_type": kind,
                "exp": claims["exp"],
                "status": "issued",
                "mandate_hash": oacp_mandates.mandate_hash(token),
                "claims": claims,
            },
        )

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
