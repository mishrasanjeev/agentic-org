# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Regression: the artifact cache must verify every Grantex OACP artifact (G5).

Failure replayed: ``POST /commerce/runtime/artifacts/cache`` cached whatever
Grantex (or any caller) posted and stamped ``verifier_result_ref =
"<artifact_id>:verified"`` without verifying a signature. A tampered payload, a
tampered envelope, an artifact signed by an unknown or withdrawn key, the
legacy template signature and a revoked artifact were all cached as verified.

Now the endpoint verifies each artifact (ES256 detached JWS against the pinned
Grantex JWKS, scope, TTL, Token Status List) before it opens a database
session. In strict mode one failure refuses the whole issuance and nothing is
stored; in log-only mode the failure is logged and the artifact is cached
without a ``verifier_result_ref``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException

from api.v1 import commerce_runtime as commerce_runtime_api
from core.commerce.oacp_grantex_trust import (
    OacpGrantexArtifactVerifier,
    OacpGrantexTrustConfig,
    reset_oacp_trust_caches,
)
from core.config import oacp_verify_strict_enabled, settings
from tests.commerce_ledger_fakes import patch_commerce_ledger
from tests.oacp_signing import (
    ISSUER,
    JWKS_URI,
    STATUS_LIST_URI,
    EcSigningKey,
    FakeGrantex,
    RsaSigningKey,
    build_envelope,
    sign_envelope,
    status_list_token,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def commerce_ledger_events(monkeypatch: pytest.MonkeyPatch) -> list:
    """The commerce ledger writes these route tests make (the database session is a fake here)."""
    return patch_commerce_ledger(monkeypatch, commerce_runtime_api)


@pytest.fixture(autouse=True)
def _fresh_trust_caches():
    reset_oacp_trust_caches()
    yield
    reset_oacp_trust_caches()


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "artifact_family": "catalog_snapshot",
        "tenant_id": TENANT_ID,
        "merchant_id": "merchant_1",
        "seller_agent_id": "seller_agent_1",
        "source_evidence_ref": "agenticorg:shopify:evidence:abc:redacted",
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "no_public_discovery_enablement": True,
        "non_authoritative_for_transaction": True,
        "product_count": 12,
    }
    payload.update(overrides)
    return payload


class Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, strict: bool = True) -> None:
        self.key = EcSigningKey(kid="oacp-test-2026-10-a")
        self.status_key = RsaSigningKey(kid="platform-test-1")
        self.now = datetime.now(UTC)
        self.statuses: dict[int, int] = {}
        self.grantex = FakeGrantex(jwks={"keys": [self.key.public_jwk(), self.status_key.public_jwk()]})
        self.publish_status_list()
        self.stored: list[Any] = []
        self.config = OacpGrantexTrustConfig(
            issuer=ISSUER,
            jwks_uri=JWKS_URI,
            status_list_origins=frozenset({ISSUER}),
        )
        harness = self

        @asynccontextmanager
        async def fake_session(_tenant_id):
            yield object()

        class FakeRepository:
            def __init__(self, _session: object) -> None:
                pass

            async def upsert(self, record) -> dict:
                harness.stored.append(record)
                return {"stored": True, "status": "stored", "cache_record_id": record.cache_record_id}

        monkeypatch.setattr(commerce_runtime_api, "get_tenant_session", fake_session)
        monkeypatch.setattr(commerce_runtime_api, "DurableOacpArtifactCacheRepository", FakeRepository)
        monkeypatch.setattr(
            commerce_runtime_api,
            "_oacp_artifact_verifier",
            lambda: OacpGrantexArtifactVerifier(harness.config, transport_factory=harness.grantex.transport_factory()),
        )
        monkeypatch.setattr(commerce_runtime_api, "oacp_verify_strict_enabled", lambda: strict)

    def publish_status_list(self) -> None:
        epoch = int(self.now.timestamp())
        self.grantex.status_lists[STATUS_LIST_URI] = status_list_token(
            self.status_key,
            uri=STATUS_LIST_URI,
            statuses=self.statuses,
            iat=epoch - 60,
            exp=epoch + 3600,
        )

    def artifact(self, *, payload: dict[str, Any] | None = None, key: EcSigningKey | None = None, **envelope_overrides):
        artifact_payload = payload or _payload()
        envelope = build_envelope(
            artifact_payload,
            key=key or self.key,
            issued_at=_iso(self.now - timedelta(minutes=1)),
            expires_at=_iso(self.now + timedelta(minutes=30)),
            artifact_id=envelope_overrides.pop("artifact_id", "oacp_art_TESTCATALOG01"),
            **envelope_overrides,
        )
        return {"artifact_family": "catalog_snapshot", "envelope": envelope, "payload": artifact_payload}

    async def cache(self, *artifacts: dict[str, Any]) -> dict[str, Any]:
        return await commerce_runtime_api.cache_grantex_artifacts(
            commerce_runtime_api.CacheArtifactsRequest(artifacts=list(artifacts)),
            tenant_id=TENANT_ID,
        )

    async def refused(self, *artifacts: dict[str, Any]) -> dict[str, Any]:
        with pytest.raises(HTTPException) as exc_info:
            await self.cache(*artifacts)
        assert exc_info.value.status_code == 422
        detail = exc_info.value.detail
        assert detail["status"] == "artifact_verification_refused"
        assert detail["records_stored"] == 0
        assert self.stored == []  # nothing reached the repository
        return detail


async def test_verified_artifact_is_cached_with_a_real_verifier_result_ref(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    result = await harness.cache(harness.artifact())

    assert result["status"] == "cached"
    assert result["verification"] == {"mode": "strict", "verified": 1, "refused": []}
    [record] = harness.stored
    ref = record.verifier_result_ref
    assert ref.startswith("oacp-artifact-jws-v1;kid=oacp-test-2026-10-a;payload_hash=")
    assert ";status=valid;verified_at=" in ref
    assert not ref.endswith(":verified")


async def test_tampered_payload_is_refused_and_not_cached(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    artifact = harness.artifact()
    artifact["payload"] = {**artifact["payload"], "product_count": 13}

    detail = await harness.refused(artifact)
    assert detail["refusals"][0]["refusal_code"] == "payload_hash_mismatch"


async def test_tampered_envelope_is_refused(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    artifact = harness.artifact()
    artifact["envelope"] = {**artifact["envelope"], "expires_at": _iso(harness.now + timedelta(hours=5))}

    detail = await harness.refused(artifact)
    assert detail["refusals"][0]["refusal_code"] == "detached_jws_verification_failed"


async def test_one_bad_artifact_refuses_the_whole_issuance(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    good = harness.artifact(artifact_id="oacp_art_GOOD")
    bad = harness.artifact(artifact_id="oacp_art_BAD")
    bad["envelope"] = {**bad["envelope"], "merchant_id": "merchant_other"}

    detail = await harness.refused(good, bad)
    assert detail["records_rejected"] == 2
    assert [item["artifact_id"] for item in detail["refusals"]] == ["oacp_art_BAD"]


async def test_unknown_key_is_refused_after_one_jwks_refresh(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    stranger = EcSigningKey(kid="oacp-test-2026-10-z")

    detail = await harness.refused(harness.artifact(key=stranger))
    assert detail["refusals"][0]["refusal_code"] == "issuer_key_untrusted"
    assert harness.grantex.requests.count(JWKS_URI) == 2  # cached fetch + one refresh

    await harness.refused(harness.artifact(key=stranger))
    assert harness.grantex.requests.count(JWKS_URI) == 2  # kid negatively cached


async def test_key_withdrawn_after_rotation_or_expiry_is_refused(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    retired = EcSigningKey(kid="oacp-test-2026-09-retired")
    artifact = harness.artifact(key=retired)  # signed while the key was current
    # Grantex withdraws a key from its JWKS once it is revoked or every artifact it signed has expired.
    detail = await harness.refused(artifact)
    assert detail["refusals"][0]["refusal_code"] == "issuer_key_untrusted"


async def test_key_rotated_in_after_the_jwks_was_cached_is_accepted(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    await harness.cache(harness.artifact())
    successor = EcSigningKey(kid="oacp-test-2026-11-a")
    harness.grantex.jwks = {"keys": [*harness.grantex.jwks["keys"], successor.public_jwk()]}

    result = await harness.cache(harness.artifact(key=successor, artifact_id="oacp_art_NEXT"))
    assert result["verification"]["verified"] == 1


async def test_expired_artifact_is_refused(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    payload = _payload()
    envelope = build_envelope(
        payload,
        key=harness.key,
        issued_at=_iso(harness.now - timedelta(hours=2)),
        expires_at=_iso(harness.now - timedelta(hours=1)),
    )
    detail = await harness.refused({"envelope": envelope, "payload": payload})
    assert detail["refusals"][0]["refusal_code"] == "artifact_expired_or_stale"


async def test_legacy_template_signature_is_refused(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    artifact = harness.artifact()
    envelope = artifact["envelope"]
    legacy = f"eyJhbGciOiJFUzI1NiJ9..sig_{envelope['artifact_id']}_{envelope['payload_hash'][:16]}"
    artifact["envelope"] = {**envelope, "issuer_key_id": "kid_c6z_internal_demo", "signature": legacy}

    detail = await harness.refused(artifact)
    assert detail["refusals"][0]["refusal_code"] in {
        "detached_jws_verification_failed",
        "signature_missing_or_placeholder",
    }


async def test_unsigned_legacy_shape_is_refused(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    payload = _payload()
    legacy = {
        "envelope": {
            "artifact_id": "c6z:catalog_snapshot:tenant:merchant:seller",
            "artifact_type": "catalog_snapshot",
            "issuer": "grantex_internal_oacp_authority",
            "issued_at": _iso(harness.now),
            "expires_at": _iso(harness.now + timedelta(minutes=5)),
        },
        "payload": payload,
        "verifier_result_ref": "c6z:catalog_snapshot:tenant:merchant:seller:verified",
    }
    detail = await harness.refused(legacy)
    assert detail["refusals"][0]["refusal_code"] == "signature_missing_or_placeholder"


@pytest.mark.parametrize(("status", "code"), [(1, "artifact_revoked"), (2, "artifact_suspended")])
async def test_revoked_or_suspended_status_is_refused(monkeypatch, status: int, code: str) -> None:
    harness = Harness(monkeypatch)
    harness.statuses[7] = status
    harness.publish_status_list()

    detail = await harness.refused(harness.artifact(status_idx=7))
    assert detail["refusals"][0]["refusal_code"] == code


@pytest.mark.parametrize("http_status", [503, 404], ids=["unreachable", "public_status_list_disabled"])
async def test_status_list_unreachable_is_refused(monkeypatch, http_status: int) -> None:
    """404 is what Grantex serves while OACP_STATUS_LIST_PUBLIC_ENABLED is off."""
    harness = Harness(monkeypatch)
    harness.grantex.status_list_status = http_status

    detail = await harness.refused(harness.artifact())
    assert detail["refusals"][0]["refusal_code"] == "revocation_status_unavailable"


async def test_status_list_on_another_origin_is_never_fetched(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    foreign = "https://status.attacker.example/status/oacp/lst_X"
    detail = await harness.refused(harness.artifact(status_uri=foreign))
    assert detail["refusals"][0]["refusal_code"] == "revocation_status_unavailable"
    assert not any("attacker.example" in url for url in harness.grantex.requests)


@pytest.mark.parametrize("defect", ["wrong_sub", "expired", "unknown_kid", "tampered"])
async def test_invalid_status_list_token_is_refused(monkeypatch, defect: str) -> None:
    harness = Harness(monkeypatch)
    epoch = int(harness.now.timestamp())
    key = harness.status_key if defect != "unknown_kid" else RsaSigningKey(kid="platform-unknown")
    token = status_list_token(
        key,
        uri=STATUS_LIST_URI,
        statuses={},
        iat=epoch - 7200 if defect == "expired" else epoch - 60,
        exp=epoch - 3600 if defect == "expired" else epoch + 3600,
        sub="https://issuer.example/status/oacp/other" if defect == "wrong_sub" else None,
    )
    if defect == "tampered":
        head, _claims, sig = token.split(".")
        forged = status_list_token(harness.status_key, uri=STATUS_LIST_URI, statuses={7: 0}, iat=0, exp=epoch + 9999)
        token = f"{head}.{forged.split('.')[1]}.{sig}"
    harness.grantex.status_lists[STATUS_LIST_URI] = token

    detail = await harness.refused(harness.artifact())
    assert detail["refusals"][0]["refusal_code"] == "revocation_status_unavailable"


async def test_revocation_mismatch_between_status_and_url_is_refused(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    artifact = harness.artifact(revocation_status_url="https://issuer.example/status/oacp/lst_OTHER")
    detail = await harness.refused(artifact)
    assert detail["refusals"][0]["refusal_code"] == "revocation_status_unavailable"


async def test_jwks_unreachable_refuses_in_strict_mode(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    harness.grantex.jwks_status = 500

    detail = await harness.refused(harness.artifact())
    assert detail["refusals"][0]["refusal_code"] == "issuer_key_untrusted"
    await harness.refused(harness.artifact())
    assert harness.grantex.requests.count(JWKS_URI) == 1  # negative cache, no retry storm


async def test_jwks_and_status_list_are_cached_between_requests(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    await harness.cache(harness.artifact())
    await harness.cache(harness.artifact())
    assert harness.grantex.requests.count(JWKS_URI) == 1
    assert harness.grantex.requests.count(STATUS_LIST_URI) == 1


async def test_log_only_mode_caches_without_verifier_ref_and_logs_irregularity(monkeypatch, caplog) -> None:
    harness = Harness(monkeypatch, strict=False)
    good = harness.artifact(artifact_id="oacp_art_GOOD")
    tampered = harness.artifact(artifact_id="oacp_art_TAMPERED")
    tampered["payload"] = {**tampered["payload"], "product_count": 99}

    with caplog.at_level(logging.WARNING, logger=commerce_runtime_api.logger.name):
        result = await harness.cache(good, tampered)

    assert result["status"] == "cached"
    assert result["verification"]["mode"] == "log_only"
    assert result["verification"]["refused"][0]["refusal_code"] == "payload_hash_mismatch"
    refs = {record.artifact_id: record.verifier_result_ref for record in harness.stored}
    assert refs["oacp_art_GOOD"].startswith("oacp-artifact-jws-v1;")
    assert refs["oacp_art_TAMPERED"] is None
    assert "oacp_artifact_verification_irregularity" in caplog.text


async def test_log_only_mode_ignores_caller_supplied_verifier_ref(monkeypatch) -> None:
    harness = Harness(monkeypatch, strict=False)
    harness.grantex.jwks_status = 500
    artifact = harness.artifact()
    artifact["verifier_result_ref"] = "oacp_art_TESTCATALOG01:verified"

    await harness.cache(artifact)
    assert harness.stored[0].verifier_result_ref is None


async def test_status_list_signed_with_the_oacp_key_is_accepted(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    epoch = int(harness.now.timestamp())
    harness.grantex.status_lists[STATUS_LIST_URI] = status_list_token(
        harness.key, uri=STATUS_LIST_URI, statuses={}, iat=epoch - 60, exp=epoch + 3600
    )
    result = await harness.cache(harness.artifact())
    assert result["verification"]["verified"] == 1


async def test_envelope_resigned_by_an_attacker_key_with_the_trusted_kid_is_refused(monkeypatch) -> None:
    harness = Harness(monkeypatch)
    artifact = harness.artifact()
    impostor = EcSigningKey(kid=harness.key.kid)
    artifact["envelope"] = sign_envelope({**artifact["envelope"], "product_count": 1}, impostor)
    detail = await harness.refused(artifact)
    assert detail["refusals"][0]["refusal_code"] == "detached_jws_verification_failed"


@pytest.mark.parametrize(
    ("env", "explicit", "expected"),
    [
        ("production", None, True),
        ("staging", None, True),
        ("preview", None, True),
        ("some-unknown-env", None, True),
        ("development", None, False),
        ("test", None, False),
        ("production", False, False),
        ("development", True, True),
    ],
)
def test_strict_flag_defaults_on_in_strict_runtimes(monkeypatch, env: str, explicit, expected: bool) -> None:
    monkeypatch.setattr(settings, "oacp_verify_strict", explicit)
    assert oacp_verify_strict_enabled(env) is expected
