# SPDX-License-Identifier: Apache-2.0
"""Regression: cache rows stamped ``<artifact_id>:verified`` must not count as verified (G5).

Failure replayed: before ES256 verification, every cached artifact got
``verifier_result_ref = "<artifact_id>:verified"`` without any check. Those rows
stay in ``oacp_artifact_cache_records`` after the upgrade, and every runtime read
(buyer answers, protocol adapters, purchase and POS preparation) trusted them.

Now a record counts as verified only when its ref has the
``oacp-artifact-jws-v1;kid=oacp-...`` form the verifier writes. Strict runtimes
refuse legacy rows (``artifact_unverified_legacy``) and rows without a ref
(``artifact_unverified``); log-only runtimes log the irregularity. The operator
refresh path re-fetches the scope from Grantex replay and caches it verified.
"""

from __future__ import annotations

import dataclasses
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from api.v1 import commerce_runtime as commerce_runtime_api
from core.commerce import c6z_runtime_vertical as c6z
from core.commerce.oacp_artifacts import (
    OacpPersistentArtifactCacheRecord,
    is_verified_oacp_cache_record,
    oacp_cache_record_verification_refusal,
)
from core.commerce.oacp_grantex_trust import (
    OacpGrantexArtifactVerifier,
    OacpGrantexTrustConfig,
    reset_oacp_trust_caches,
)
from tests.commerce_ledger_fakes import patch_commerce_ledger
from tests.oacp_signing import (
    ISSUER,
    JWKS_URI,
    STATUS_LIST_URI,
    EcSigningKey,
    FakeGrantex,
    build_envelope,
    status_list_token,
)


@pytest.fixture(autouse=True)
def commerce_ledger_events(monkeypatch: pytest.MonkeyPatch) -> list:
    """The commerce ledger writes these route tests make (the database session is a fake here)."""
    return patch_commerce_ledger(monkeypatch, commerce_runtime_api)


TENANT_ID = "11111111-1111-1111-1111-111111111111"
VERIFIED_REF = (
    "oacp-artifact-jws-v1;kid=oacp-20261019-0123456789abcdef;payload_hash=ab;status=valid;"
    "verified_at=2026-10-19T10:00:00Z"
)
PRODUCTS = [
    {
        "title": "Canvas Tote",
        "product_ref": "shopify:product:1",
        "variants": [{"variant_id": "v1", "sku": "TOTE-1", "price": "1299", "currency": "INR", "inventory": 4}],
    }
]


@pytest.fixture(autouse=True)
def _fresh_trust_caches():
    reset_oacp_trust_caches()
    yield
    reset_oacp_trust_caches()


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _record(ref: str | None, artifact_type: str = "catalog_snapshot") -> OacpPersistentArtifactCacheRecord:
    now = datetime.now(UTC)
    return OacpPersistentArtifactCacheRecord(
        cache_record_id=f"cache_{artifact_type}",
        artifact_id=f"c6z:{artifact_type}:tenant:merchant:seller",
        artifact_type=artifact_type,
        authority="grantex.internal.oacp.authority",
        issuer="grantex.internal.oacp.authority",
        scope_kind="seller_agent",
        tenant_id=TENANT_ID,
        merchant_id="merchant_1",
        seller_agent_id="seller_agent_1",
        buyer_agent_id=None,
        source_refs=("agenticorg:shopify:evidence:abc:redacted",),
        evidence_refs=("agenticorg:shopify:evidence:abc:redacted",),
        generated_at=_iso(now - timedelta(minutes=1)),
        cached_at=_iso(now - timedelta(minutes=1)),
        expires_at=_iso(now + timedelta(minutes=5)),
        freshness_status="fresh",
        revocation_snapshot_status="fresh",
        revocation_snapshot_observed_at=_iso(now - timedelta(seconds=10)),
        ttl_policy_seconds=360,
        risk_tier="low",
        revocation_snapshot_age_seconds=10,
        verifier_result_ref=ref,
    )


LEGACY = "c6z:catalog_snapshot:tenant:merchant:seller:verified"


def _strict(monkeypatch: pytest.MonkeyPatch, strict: bool) -> None:
    monkeypatch.setattr(c6z, "oacp_verify_strict_enabled", lambda: strict)


def test_only_the_verifier_ref_form_counts_as_verified() -> None:
    assert is_verified_oacp_cache_record(_record(VERIFIED_REF)) is True
    assert is_verified_oacp_cache_record(_record(LEGACY)) is False
    assert is_verified_oacp_cache_record(_record(None)) is False
    assert is_verified_oacp_cache_record(_record("oacp-artifact-jws-v1;kid=kid_c6z_internal_demo")) is False
    assert oacp_cache_record_verification_refusal(_record(LEGACY))["refusal_code"] == "artifact_unverified_legacy"
    assert oacp_cache_record_verification_refusal(_record(None))["refusal_code"] == "artifact_unverified"


def test_strict_buyer_answer_refuses_a_legacy_verified_row(monkeypatch) -> None:
    _strict(monkeypatch, True)
    answer = c6z.answer_product_question_from_cache(
        cache_records=[_record(LEGACY)],
        products=PRODUCTS,
        question="What is the price of Canvas Tote?",
        now_iso=_iso(datetime.now(UTC)),
        grantex_available=False,
    )
    assert answer.status == "needs_refresh"
    assert "artifact_unverified_legacy" in str(answer.refusal_reason)


def test_strict_buyer_answer_uses_a_verified_row(monkeypatch) -> None:
    _strict(monkeypatch, True)
    answer = c6z.answer_product_question_from_cache(
        cache_records=[_record(VERIFIED_REF)],
        products=PRODUCTS,
        question="What is the price of Canvas Tote?",
        now_iso=_iso(datetime.now(UTC)),
        grantex_available=False,
    )
    assert answer.status != "needs_refresh"


def test_strict_adapters_and_purchase_prep_refuse_legacy_rows(monkeypatch) -> None:
    _strict(monkeypatch, True)
    now_iso = _iso(datetime.now(UTC))
    records = [_record(LEGACY, t) for t in ("catalog_snapshot", "price", "inventory", "policy")] + [
        _record(None, "mandate_capability")
    ]
    adapters = c6z.generate_protocol_adapter_payloads(
        cache_records=records,
        products=PRODUCTS,
        merchant_id="merchant_1",
        seller_agent_id="seller_agent_1",
        now_iso=now_iso,
    )
    assert adapters["status"] == "blocked_missing_valid_oacp_artifacts"
    assert set(adapters["missing_or_blocked_reasons"]) == {"artifact_unverified_legacy", "artifact_unverified"}

    purchase = c6z.prepare_purchase_or_mandate_handoff(
        cache_records=records,
        products=PRODUCTS,
        capability_evidence=[],
        tenant_id=TENANT_ID,
        merchant_id="merchant_1",
        seller_agent_id="seller_agent_1",
        buyer_agent_id=None,
        product_ref_or_query="Canvas Tote",
        variant_id=None,
        quantity=1,
        now_iso=now_iso,
    )
    assert purchase.artifact_refs == ()
    assert purchase.allowed_to_execute is False


def test_log_only_keeps_legacy_rows_usable_and_logs_irregularity(monkeypatch) -> None:
    _strict(monkeypatch, False)
    with capture_logs() as logs:
        answer = c6z.answer_product_question_from_cache(
            cache_records=[_record(LEGACY)],
            products=PRODUCTS,
            question="What is the price of Canvas Tote?",
            now_iso=_iso(datetime.now(UTC)),
            grantex_available=False,
        )
    assert answer.status != "needs_refresh"
    events = [log for log in logs if log["event"] == "oacp_cache_record_unverified_irregularity"]
    assert events and events[0]["refusal_code"] == "artifact_unverified_legacy"


async def test_replay_fetch_follows_cursor_and_keeps_only_issued() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        assert request.headers["Authorization"] == "Bearer replay-token-placeholder"
        if "cursor" not in params:
            return httpx.Response(
                200,
                json={
                    "artifacts": [{"status": "issued", "envelope": {"artifact_id": "a1"}}, {"status": "revoked"}],
                    "next_cursor": "c2",
                },
            )
        return httpx.Response(200, json={"artifacts": [{"status": "issued", "envelope": {"artifact_id": "a2"}}]})

    items = await c6z.fetch_grantex_artifact_replay(
        tenant_id=TENANT_ID,
        merchant_id="merchant_1",
        seller_agent_id="seller_agent_1",
        env={
            "GRANTEX_COMMERCE_BASE_URL": "https://grantex.example",
            "GRANTEX_COMMERCE_INTERNAL_TOKEN": "replay-token-placeholder",
        },
        transport=httpx.MockTransport(handler),
    )
    assert [item["envelope"]["artifact_id"] for item in items] == ["a1", "a2"]
    assert seen[1]["cursor"] == "c2"
    assert seen[0]["tenant_id"] == TENANT_ID


async def test_refresh_endpoint_recaches_replayed_artifacts_verified(monkeypatch) -> None:
    key = EcSigningKey(kid="oacp-20261019-0123456789abcdef")
    now = datetime.now(UTC)
    epoch = int(now.timestamp())
    grantex = FakeGrantex(jwks={"keys": [key.public_jwk()]})
    grantex.status_lists[STATUS_LIST_URI] = status_list_token(
        key, uri=STATUS_LIST_URI, statuses={}, iat=epoch - 60, exp=epoch + 3600
    )
    payload = {
        "artifact_family": "catalog_snapshot",
        "tenant_id": TENANT_ID,
        "merchant_id": "merchant_1",
        "seller_agent_id": "seller_agent_1",
        "source_evidence_ref": "agenticorg:shopify:evidence:abc:redacted",
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "no_public_discovery_enablement": True,
    }
    current = build_envelope(
        payload,
        key=key,
        artifact_id="oacp_art_CURRENT",
        issued_at=_iso(now - timedelta(minutes=1)),
        expires_at=_iso(now + timedelta(minutes=30)),
    )
    expired = build_envelope(
        payload,
        key=key,
        artifact_id="oacp_art_EXPIRED",
        issued_at=_iso(now - timedelta(hours=3)),
        expires_at=_iso(now - timedelta(hours=1)),
    )

    async def fake_replay(**_kwargs):
        return [
            {"status": "issued", "envelope": current, "payload": payload},
            {"status": "issued", "envelope": expired, "payload": payload},
        ]

    stored: list[Any] = []

    @asynccontextmanager
    async def fake_session(_tenant_id):
        yield object()

    class FakeRepository:
        def __init__(self, _session: object) -> None:
            pass

        async def upsert(self, record) -> dict:
            stored.append(record)
            return {"stored": True, "status": "stored", "cache_record_id": record.cache_record_id}

    config = OacpGrantexTrustConfig(issuer=ISSUER, jwks_uri=JWKS_URI, status_list_origins=frozenset({ISSUER}))
    monkeypatch.setattr(commerce_runtime_api, "get_tenant_session", fake_session)
    monkeypatch.setattr(commerce_runtime_api, "DurableOacpArtifactCacheRepository", FakeRepository)
    monkeypatch.setattr(commerce_runtime_api, "fetch_grantex_artifact_replay", fake_replay)
    monkeypatch.setattr(
        commerce_runtime_api,
        "_oacp_artifact_verifier",
        lambda: OacpGrantexArtifactVerifier(config, transport_factory=grantex.transport_factory()),
    )
    monkeypatch.setattr(commerce_runtime_api, "oacp_verify_strict_enabled", lambda: True)
    monkeypatch.setenv("GRANTEX_COMMERCE_BASE_URL", "https://grantex.example")
    monkeypatch.setenv("GRANTEX_COMMERCE_INTERNAL_TOKEN", "replay-token-placeholder")

    result = await commerce_runtime_api.refresh_grantex_artifacts(
        commerce_runtime_api.RefreshArtifactsRequest(merchant_id="merchant_1", seller_agent_id="seller_agent_1"),
        tenant_id=TENANT_ID,
    )

    assert result["status"] == "cached"
    assert result["source"] == "grantex_replay"
    assert result["replayed"] == 2
    [record] = stored  # the expired replay item is skipped, not refused
    assert record.artifact_id == "oacp_art_CURRENT"
    assert is_verified_oacp_cache_record(record)
    _strict(monkeypatch, True)
    assert c6z.oacp_cache_record_verification_refusal(dataclasses.replace(record)) is None


async def test_refresh_endpoint_reports_missing_grantex_env(monkeypatch) -> None:
    monkeypatch.delenv("GRANTEX_COMMERCE_BASE_URL", raising=False)
    monkeypatch.delenv("GRANTEX_COMMERCE_INTERNAL_TOKEN", raising=False)
    result = await commerce_runtime_api.refresh_grantex_artifacts(
        commerce_runtime_api.RefreshArtifactsRequest(merchant_id="merchant_1", seller_agent_id="seller_agent_1"),
        tenant_id=TENANT_ID,
    )
    assert result["status"] == "blocked_missing_grantex_env"
