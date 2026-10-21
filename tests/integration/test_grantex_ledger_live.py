# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Arbiter countersignature and chain-head witnessing against a live Grantex (contract section 7).

Skipped unless these point at a running Grantex auth service with the arbiter-decision and
witness routes (a local stack is fine):

    AGENTICORG_GRANTEX_LIVE_URL=http://localhost:55552            # issuer and API origin
    GRANTEX_COMMERCE_INTERNAL_TOKEN=<the AgenticOrg service token>
    AGENTICORG_GRANTEX_LIVE_TENANT=<a tenant in COMMERCE_C6Z_AUTHORITY_SERVICE_TENANTS>

No database is used: the grant attach and the witness row write are captured in memory.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

LIVE_URL = os.getenv("AGENTICORG_GRANTEX_LIVE_URL", "").rstrip("/")
TOKEN = os.getenv("GRANTEX_COMMERCE_INTERNAL_TOKEN", "")
TENANT = os.getenv("AGENTICORG_GRANTEX_LIVE_TENANT", "")

pytestmark = pytest.mark.skipif(
    not (LIVE_URL and TOKEN and TENANT),
    reason="set AGENTICORG_GRANTEX_LIVE_URL, GRANTEX_COMMERCE_INTERNAL_TOKEN and AGENTICORG_GRANTEX_LIVE_TENANT",
)


def _client():
    from core.commerce.grantex_service import GrantexServiceClient
    from core.commerce.oacp_grantex_trust import OacpGrantexArtifactVerifier, OacpGrantexTrustConfig

    def plain() -> httpx.AsyncBaseTransport:
        # A local stack listens on loopback, which the public egress transport refuses by design.
        return httpx.AsyncHTTPTransport()

    verifier = OacpGrantexArtifactVerifier(
        OacpGrantexTrustConfig(
            issuer=LIVE_URL,
            jwks_uri=f"{LIVE_URL}/.well-known/jwks.json",
            status_list_origins=frozenset({LIVE_URL}),
            allow_local_endpoints=True,
        ),
        transport_factory=plain,
    )
    return GrantexServiceClient(
        base_url=LIVE_URL, token=TOKEN, verifier=verifier, transport_factory=plain, allow_local_endpoints=True
    )


def _decision(*, require_verified_authority: bool = False):
    from core.commerce.arbiter import (
        ArbiterContext,
        ArtifactEvidence,
        ConsentEvidence,
        MandateEvidence,
        MerchantLimits,
        PassportEvidence,
        decide,
    )

    now = datetime.now(UTC)
    verified = f"oacp-artifact-jws-v1;kid=oacp-live;payload_hash={'a' * 64};status=valid;verified_at=x"
    families = ("catalog_snapshot", "price", "inventory", "policy", "mandate_capability", "protocol_adapter")
    context = ArbiterContext(
        tenant_id=TENANT,
        merchant_id="merchant.example",
        seller_agent_id="seller-01",
        buyer_agent_id="shopper-01",
        now=now,
        payload={"handoff_id": f"live-{uuid.uuid4()}", "quantity": 1},
        amount_minor_units=5000,
        currency="INR",
        quantity=1,
        mandate=MandateEvidence(ref="mandate-live-1", hash="b" * 64, expires_at=now + timedelta(hours=1)),
        consent=ConsentEvidence(ref="consent-live-1", status="active"),
        passport=PassportEvidence(
            ref="passport-live-1", max_amount_minor_units=10000, currency="INR", expires_at=now + timedelta(hours=1)
        ),
        artifacts=tuple(
            ArtifactEvidence(
                artifact_id=f"oacp_art_live_{family}",
                family=family,
                tenant_id=TENANT,
                merchant_id="merchant.example",
                seller_agent_id="seller-01",
                buyer_agent_id=None,
                verifier_result_ref=verified,
                expires_at=now + timedelta(minutes=5),
                freshness_status="fresh",
                revocation_status="fresh",
            )
            for family in families
        ),
        merchant_limits=MerchantLimits(
            max_order_amount_minor_units=20000, currency_allowlist=("INR",), max_quantity_per_order=5
        ),
        require_verified_authority=require_verified_authority,
    )
    return decide("purchase_handoff", context)


@pytest.mark.asyncio
async def test_live_allow_is_countersigned_with_a_verified_machine_profile_grant(monkeypatch) -> None:
    from core.commerce import arbiter_grantex

    attached: list[tuple[str, str]] = []

    async def _attach(decision, *, jti, recorded_at):
        attached.append((decision.decision_id, jti))
        return True

    monkeypatch.setattr(arbiter_grantex, "attach_grant", _attach)
    decision = _decision()
    assert decision.outcome == "allow", decision.reason
    client = _client()
    first = await arbiter_grantex.countersign_decision(decision, client=client, post_enabled=True)
    assert first.status == "granted", first
    again = await arbiter_grantex.countersign_decision(decision, client=client, post_enabled=True)
    assert (again.status, again.jti) == ("granted", first.jti)
    assert attached == [(decision.decision_id, first.jti), (decision.decision_id, first.jti)]

    # A different body under the same decision_id is a conflict, never a second grant.
    changed = dataclasses.replace(decision, policy_version="commerce-arbiter-v2-changed")
    conflict = await arbiter_grantex.countersign_decision(changed, client=client, post_enabled=True)
    assert conflict.status == "conflict"


@pytest.mark.asyncio
async def test_live_refusal_is_recorded_without_a_grant(monkeypatch) -> None:
    from core.commerce import arbiter_grantex

    decision = _decision(require_verified_authority=True)
    assert decision.failing_rule == "buyer_authority_verified"
    result = await arbiter_grantex.countersign_decision(decision, client=_client(), post_enabled=True)
    assert (result.status, result.jti) == ("recorded", None)


@pytest.mark.asyncio
async def test_live_chain_head_is_witnessed_and_a_fork_is_refused(monkeypatch) -> None:
    from core.governance import audit_witness

    stored: list[dict] = []

    async def _store(tenant_id, body, **values):
        stored.append({**body, **values})

    monkeypatch.setattr(audit_witness, "_store", _store)
    client = _client()
    tenant = uuid.uuid4()  # a fresh chain each run
    head = "c" * 64
    first = await audit_witness.witness_head(
        tenant, head_seq=1, head_hash=head, anchored_at=datetime.now(UTC), client=client, enabled=True
    )
    assert first["status"] == "witnessed", first
    again = await audit_witness.witness_head(
        tenant, head_seq=1, head_hash=head, anchored_at=datetime.now(UTC), client=client, enabled=True
    )
    assert again["witness_id"] == first["witness_id"]
    fork = await audit_witness.witness_head(
        tenant, head_seq=1, head_hash="d" * 64, anchored_at=datetime.now(UTC), client=client, enabled=True
    )
    assert fork["status"] == "fork"
    assert stored[-1]["status"] == "fork" and stored[-1]["fork_evidence"]["presented"]["head_hash"] == "d" * 64

    # The stored countersignature verifies offline against the live JWKS, as an export carries it.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    try:
        import verify_ledger
    finally:
        sys.path.pop(0)
    jwks = await client.jwks()
    witnessed = stored[0]
    problem = verify_ledger._witness_problem(
        verify_ledger._jws(),
        jwks,
        LIVE_URL,
        {
            "countersignature": witnessed["countersignature"],
            "head_seq": 1,
            "head_hash": head,
            "witness_id": witnessed["witness_id"],
        },
        str(tenant),
    )
    assert problem is None


@pytest.mark.skipif(
    not os.getenv("AGENTICORG_DB_URL"), reason="also needs the disposable Postgres in AGENTICORG_DB_URL"
)
@pytest.mark.asyncio
async def test_live_pos_handoff_consumes_only_a_grantex_countersigned_allow(monkeypatch) -> None:
    """End to end on the database: strict grant requirement, live Grantex, POS handoff created once."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from api.v1 import commerce_runtime as api
    from core import database
    from core.commerce import arbiter_grantex
    from core.config import settings
    from tests import commerce_arbiter_fixtures as fx

    engine = create_async_engine(os.environ["AGENTICORG_DB_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)
    monkeypatch.setattr(settings, "arbiter_require_grantex_grant", True)
    monkeypatch.setattr(arbiter_grantex, "grantex_service_client_from_env", _client)

    async def _scope(**_kwargs):
        return fx.cache_records(TENANT), fx.products(TENANT), fx.capabilities()

    async def _config(*_args):
        return fx.merchant_config()

    monkeypatch.setattr(api, "_load_runtime_scope", _scope)
    monkeypatch.setattr(api, "_merchant_config_for_arbiter", _config)
    body = {
        "merchant_id": fx.MERCHANT,
        "seller_agent_id": fx.SELLER,
        "buyer_agent_id": fx.BUYER,
        "buyer_session_ref": "buyer-session-live",
        "product_ref_or_query": "Canvas Tote",
        "quantity": 1,
        "store_id": "store-1",
        "pos_location": {"display_name": "Example Store", "city": "Pune", "country_code": "IN"},
        "idempotency_key": f"pos-live-{uuid.uuid4()}",
        "buyer_authority": fx.buyer_authority(),
    }
    try:
        first = await api.create_offline_pos_handoff(api.OfflinePosHandoffRequest(**body), tenant_id=TENANT)
        assert first["status"] == "decision_issued"
        assert first["grantex_countersignature"]["status"] == "granted", first["grantex_countersignature"]
        created = await api.create_offline_pos_handoff(
            api.OfflinePosHandoffRequest(**body, decision_id=first["decision_id"]), tenant_id=TENANT
        )
        assert created["pos_handoff_created"] is True
        replay = await api.create_offline_pos_handoff(
            api.OfflinePosHandoffRequest(**body, decision_id=first["decision_id"]), tenant_id=TENANT
        )
        assert replay["pos_handoff_created"] is False and replay["blocker"]["code"] == "decision_consumed"
    finally:
        await engine.dispose()
