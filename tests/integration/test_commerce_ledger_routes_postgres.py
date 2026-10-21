# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Phase 2 G14-G16 and D1 on a real Postgres.

Strict buyer authority refusal, the Grantex grant requirement on consumption,
operator decisions that never change an outcome, an export that verifies
offline (and fails when tampered), retention through the trigger's one DELETE
transition, and witness checkpoints stored by the sealing task.

Requires the disposable database in ``AGENTICORG_DB_URL`` (migrated to head by the module fixture).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests import commerce_arbiter_fixtures as fx

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module", autouse=True)
async def migrated_schema(tmp_path_factory):
    if not os.getenv("AGENTICORG_DB_URL"):
        pytest.skip("Requires the disposable Docker Postgres database")
    env = {**os.environ, "AGENTICORG_MIGRATION_AUDIT_DIR": str(tmp_path_factory.mktemp("migration-audit"))}
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "scripts/alembic_migrate.py"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-4000:]


@pytest.fixture()
async def db(monkeypatch):
    from core import database
    from core.commerce.oacp_grantex_trust import reset_oacp_trust_caches

    reset_oacp_trust_caches()
    engine = create_async_engine(os.environ["AGENTICORG_DB_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)
    try:
        yield engine
    finally:
        await engine.dispose()
        reset_oacp_trust_caches()


@pytest.fixture()
def runtime(monkeypatch, db):
    from api.v1 import commerce_runtime as api

    tenant = str(uuid.uuid4())

    async def _scope(**_kwargs):
        return fx.cache_records(tenant), fx.products(tenant), fx.capabilities()

    async def _config(*_args):
        return fx.merchant_config()

    monkeypatch.setattr(api, "_load_runtime_scope", _scope)
    monkeypatch.setattr(api, "_merchant_config_for_arbiter", _config)
    return api, tenant


@pytest.fixture()
def fake_grantex(monkeypatch):
    from core.commerce import arbiter_grantex
    from core.governance import audit_witness
    from tests.grantex_service_fakes import FakeGrantexService

    fake = FakeGrantexService()
    monkeypatch.setattr(arbiter_grantex, "grantex_service_client_from_env", fake.client)
    monkeypatch.setattr(audit_witness, "grantex_service_client_from_env", fake.client)
    return fake


def _pos(api, **overrides):
    body = {
        "merchant_id": fx.MERCHANT,
        "seller_agent_id": fx.SELLER,
        "buyer_agent_id": fx.BUYER,
        "buyer_session_ref": "buyer-session-1",
        "product_ref_or_query": "Canvas Tote",
        "quantity": 1,
        "store_id": "store-1",
        "pos_location": {"display_name": "Example Store", "city": "Pune", "country_code": "IN"},
        "idempotency_key": "pos-key-1",
        "buyer_authority": fx.buyer_authority(),
    }
    body.update(overrides)
    return api.OfflinePosHandoffRequest(**body)


def _purchase(api, **overrides):
    body = {
        "merchant_id": fx.MERCHANT,
        "seller_agent_id": fx.SELLER,
        "buyer_agent_id": fx.BUYER,
        "product_ref_or_query": "Canvas Tote",
        "quantity": 1,
        "idempotency_key": "purchase-key-1",
        "buyer_authority": fx.buyer_authority(),
    }
    body.update(overrides)
    return api.PurchasePreparationRequest(**body)


async def _scalar(engine, sql: str, **params):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar()


async def _events(engine, tenant: str) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT event_type FROM audit_log WHERE tenant_id = :t ORDER BY created_at, id"), {"t": tenant}
        )
        return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# D1: caller-asserted buyer authority
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strict_runtime_refuses_caller_asserted_authority_and_no_pos_packet_is_stored(runtime, db, monkeypatch):
    from core.config import settings

    api, tenant = runtime
    monkeypatch.setattr(settings, "arbiter_require_verified_authority", True)
    purchase = await api.prepare_purchase_handoff(_purchase(api), tenant_id=tenant)
    assert purchase["arbiter_decision"]["failing_rule"] == "buyer_authority_verified"
    assert purchase["blocker"]["failing_rule"] == "buyer_authority_verified"

    first = await api.create_offline_pos_handoff(_pos(api), tenant_id=tenant)
    assert first["status"] == "blocked" and first["pos_handoff_created"] is False
    refused_id = first["arbiter_decision"]["decision_id"]
    second = await api.create_offline_pos_handoff(_pos(api, decision_id=refused_id), tenant_id=tenant)
    assert second["pos_handoff_created"] is False
    packets = await _scalar(
        db, "SELECT count(*) FROM commerce_c6z_offline_pos_handoff_packets WHERE tenant_id = :t", t=tenant
    )
    assert packets == 0


@pytest.mark.asyncio
async def test_relaxed_runtime_records_caller_asserted_authority(runtime, db):
    api, tenant = runtime
    first = await api.create_offline_pos_handoff(_pos(api), tenant_id=tenant)
    decision = first["arbiter_decision"]
    assert decision["outcome"] == "allow" and decision["buyer_authority_source"] == "caller_asserted"
    source = await _scalar(
        db,
        "SELECT evidence->>'buyer_authority_source' FROM commerce_arbiter_decisions WHERE decision_id = :d",
        d=decision["decision_id"],
    )
    assert source == "caller_asserted"


# ---------------------------------------------------------------------------
# G16(a): a consumable allow needs a verified Grantex grant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pos_handoff_consumes_only_a_countersigned_allow(runtime, db, monkeypatch, fake_grantex):
    from core.config import settings

    api, tenant = runtime
    monkeypatch.setattr(settings, "arbiter_require_grantex_grant", True)
    first = await api.create_offline_pos_handoff(_pos(api), tenant_id=tenant)
    assert first["status"] == "decision_issued"
    assert first["grantex_countersignature"]["status"] == "granted"
    decision_id = first["decision_id"]
    jti = await _scalar(
        db, "SELECT grantex_grant_jti FROM commerce_arbiter_decisions WHERE decision_id = :d", d=decision_id
    )
    assert jti == first["grantex_countersignature"]["jti"]
    posted = [body for path, body in fake_grantex.posted if path.endswith("arbiter-decisions")]
    assert posted[0]["decision_id"] == decision_id and posted[0]["failing_rule"] is None

    created = await api.create_offline_pos_handoff(_pos(api, decision_id=decision_id), tenant_id=tenant)
    assert created["pos_handoff_created"] is True
    assert "commerce.arbiter.decision.countersigned" in await _events(db, tenant)


@pytest.mark.asyncio
async def test_grantex_unreachable_leaves_the_allow_pending_and_the_handoff_is_refused(
    runtime, db, monkeypatch, fake_grantex
):
    from core.config import settings

    api, tenant = runtime
    monkeypatch.setattr(settings, "arbiter_require_grantex_grant", True)
    fake_grantex.mode = "unreachable"
    first = await api.create_offline_pos_handoff(_pos(api), tenant_id=tenant)
    assert first["arbiter_decision"]["outcome"] == "allow"
    assert first["grantex_countersignature"]["status"] == "unavailable"
    second = await api.create_offline_pos_handoff(_pos(api, decision_id=first["decision_id"]), tenant_id=tenant)
    assert second["pos_handoff_created"] is False
    assert second["blocker"]["code"] == "grantex_grant_missing"
    # The same grant bound to another payload is refused before it is attached.
    fake_grantex.mode = "wrong_action_hash"
    third = await api.create_offline_pos_handoff(_pos(api), tenant_id=tenant)
    assert third["grantex_countersignature"] == {
        "status": "invalid",
        "reason": "action_hash_mismatch",
        "jti": None,
        "exp": None,
    }
    assert (
        await _scalar(
            db,
            "SELECT grantex_grant_jti FROM commerce_arbiter_decisions WHERE decision_id = :d",
            d=third["decision_id"],
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_refusal_is_recorded_at_grantex_and_gets_no_grant(runtime, db, monkeypatch, fake_grantex):
    from core.config import settings

    api, tenant = runtime
    monkeypatch.setattr(settings, "arbiter_require_grantex_grant", True)
    monkeypatch.setattr(settings, "arbiter_require_verified_authority", True)
    first = await api.create_offline_pos_handoff(_pos(api), tenant_id=tenant)
    assert first["grantex_countersignature"]["status"] == "recorded"
    [(_, body)] = fake_grantex.posted
    assert body["outcome"] == "refuse" and body["failing_rule"] == "buyer_authority_verified"
    assert body["failing_rule"] in body["rules_evaluated"]


# ---------------------------------------------------------------------------
# G14: operator decisions annotate; they never allow or execute
# ---------------------------------------------------------------------------


def _request(sub: str = "operator-1") -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user_sub=sub))


@pytest.mark.asyncio
async def test_operator_decision_on_a_refusal_never_makes_it_consumable(runtime, db, monkeypatch):
    from api.v1 import commerce_ledger as ledger_api
    from core.commerce.arbiter_store import DecisionRefusedError, require_allow_decision
    from core.config import settings
    from core.database import get_tenant_session

    api, tenant = runtime
    monkeypatch.setattr(settings, "arbiter_require_verified_authority", True)
    refused = await api.create_offline_pos_handoff(_pos(api), tenant_id=tenant)
    decision_id = refused["arbiter_decision"]["decision_id"]
    before = await _scalar(
        db, "SELECT to_jsonb(d) FROM commerce_arbiter_decisions d WHERE decision_id = :d", d=decision_id
    )

    answer = await ledger_api.create_operator_decision(
        decision_id,
        ledger_api.OperatorDecisionCreate(decision_kind="escalate_to_human_support"),
        _request(),
        tenant_id=tenant,
    )
    assert answer["outcome_binding"] is False and answer["allowed_to_execute"] is False
    record_id = answer["operator_decision"]["decision_record_id"]
    stored = await _scalar(
        db, "SELECT review_packet_id FROM oacp_operator_decision_records WHERE decision_id = :d", d=record_id
    )
    assert stored == decision_id
    assert (
        await _scalar(db, "SELECT to_jsonb(d) FROM commerce_arbiter_decisions d WHERE decision_id = :d", d=decision_id)
        == before
    )
    events = await _events(db, tenant)
    assert "commerce.runtime.ledger.operator_decision.create" in events
    assert "commerce.oacp.operator_decision.record" in events

    with pytest.raises(DecisionRefusedError) as refusal:
        async with get_tenant_session(uuid.UUID(tenant)) as session:
            await require_allow_decision(
                session,
                tenant_id=tenant,
                decision_id=decision_id,
                payload_hash=refused["arbiter_decision"]["payload_hash"],
                action="pos_handoff",
            )
    assert refusal.value.code == "decision_not_allow"
    second = await api.create_offline_pos_handoff(_pos(api, decision_id=decision_id), tenant_id=tenant)
    assert second["pos_handoff_created"] is False

    read = await ledger_api.read_arbiter_decision(decision_id, tenant_id=tenant)
    assert [item["decision_record_id"] for item in read["operator_decisions"]] == [record_id]
    with pytest.raises(DBAPIError):
        async with db.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE oacp_operator_decision_records SET decision_kind = 'approve_future_refresh_request' "
                    "WHERE decision_id = :d"
                ),
                {"d": record_id},
            )


@pytest.mark.asyncio
async def test_operator_decision_for_another_tenants_decision_is_not_found(runtime, db):
    from api.v1 import commerce_ledger as ledger_api

    api, tenant = runtime
    first = await api.create_offline_pos_handoff(_pos(api), tenant_id=tenant)
    with pytest.raises(HTTPException) as missing:
        await ledger_api.create_operator_decision(
            first["arbiter_decision"]["decision_id"],
            ledger_api.OperatorDecisionCreate(decision_kind="request_more_evidence"),
            _request(),
            tenant_id=str(uuid.uuid4()),
        )
    assert missing.value.status_code == 404


# ---------------------------------------------------------------------------
# G15 + G16(b): sealed chain, witness checkpoints, export verified offline
# ---------------------------------------------------------------------------


def _run_verifier(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed interpreter and repository script, test-generated file
        [sys.executable, str(ROOT / "scripts" / "verify_ledger.py"), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.asyncio
async def test_export_of_a_sealed_witnessed_chain_verifies_offline(runtime, db, monkeypatch, fake_grantex, tmp_path):
    from api.v1 import commerce_ledger as ledger_api
    from core.commerce import oacp_grantex_trust
    from core.config import settings
    from core.tasks import audit_chain_tasks

    api, tenant = runtime
    for key in ("pos-a", "pos-b"):
        await api.create_offline_pos_handoff(_pos(api, idempotency_key=key), tenant_id=tenant)
    monkeypatch.setattr(settings, "audit_chain_enabled", True)
    monkeypatch.setattr(settings, "audit_witness_enabled", True)

    async def _only_this_tenant():
        return [uuid.UUID(tenant)]

    monkeypatch.setattr(audit_chain_tasks, "_tenant_ids", _only_this_tenant)
    sealed = await audit_chain_tasks._seal_audit_chains_async()
    assert sealed["sealed"] >= 4 and sealed["errors"] == 0
    witness = await _scalar(db, "SELECT status FROM audit_chain_witness_checkpoints WHERE tenant_id = :t", t=tenant)
    assert witness == "witnessed"

    monkeypatch.setattr(oacp_grantex_trust, "default_oacp_grantex_verifier", lambda: fake_grantex.client()._verifier)
    bundle = await ledger_api.export_ledger(from_seq=1, to_seq=None, tenant_id=tenant)
    assert bundle["chain"]["row_count"] == bundle["chain"]["head"]["seq"] >= 4
    assert bundle["signatures"]["offline_verifiable"] is False
    clean = tmp_path / "export.json"
    clean.write_text(json.dumps(bundle), encoding="utf-8")
    result = _run_verifier(clean)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["witness_countersignatures_verified"] == 1

    edited = json.loads(clean.read_text(encoding="utf-8"))
    edited["rows"][1]["outcome"] = "allow" if edited["rows"][1]["outcome"] != "allow" else "refuse"
    tampered = tmp_path / "edited.json"
    tampered.write_text(json.dumps(edited), encoding="utf-8")
    broken = _run_verifier(tampered)
    assert broken.returncode == 1 and json.loads(broken.stdout)["first_break"]["seq"] == 2

    cut = json.loads(clean.read_text(encoding="utf-8"))
    cut["rows"] = cut["rows"][:-1]
    cut["chain"]["to_seq"] -= 1
    cut["chain"]["head"] = {"seq": cut["chain"]["to_seq"], "hash": cut["rows"][-1]["chain_hash"]}
    cut["anchors"]["audit_chain_anchor"] = dict(cut["chain"]["head"])
    truncated = tmp_path / "truncated.json"
    truncated.write_text(json.dumps(cut), encoding="utf-8")
    result = _run_verifier(truncated)
    assert result.returncode == 1 and json.loads(result.stdout)["first_break"]["reason"] == "truncated"


@pytest.mark.asyncio
async def test_a_forked_head_is_stored_as_fork_evidence(runtime, db, fake_grantex):
    from core.governance.audit_witness import witness_head

    _, tenant = runtime
    tenant_id = uuid.UUID(tenant)
    first = await witness_head(tenant_id, head_seq=5, head_hash="a" * 64, anchored_at=None, enabled=True)
    fork = await witness_head(tenant_id, head_seq=5, head_hash="b" * 64, anchored_at=None, enabled=True)
    assert (first["status"], fork["status"]) == ("witnessed", "fork")
    rows = await _scalar(
        db,
        "SELECT jsonb_agg(status ORDER BY status) FROM audit_chain_witness_checkpoints WHERE tenant_id = :t",
        t=tenant,
    )
    assert rows == ["fork", "witnessed"]
    with pytest.raises(DBAPIError):
        async with db.begin() as conn:
            await conn.execute(text("DELETE FROM audit_chain_witness_checkpoints WHERE tenant_id = :t"), {"t": tenant})


# ---------------------------------------------------------------------------
# G14: retention through the one documented DELETE transition
# ---------------------------------------------------------------------------


def _retention_chain(tenant: str, *, decided_at: str = "2026-06-12T00:08:00.000Z"):
    """A stored-manifest-ready OACP chain for ``tenant`` (the C6X9 -> C6Y5 builders), dated in the past."""
    from core.commerce.oacp_artifacts import (
        OacpArtifactCacheRepositoryQuery,
        OacpAuditReviewManifestRepositoryQuery,
        build_oacp_c6x9_audit_export_bundle,
        build_oacp_c6y1_audit_export_review_manifest,
        build_oacp_c6y3_audit_review_manifest_summary,
        build_oacp_c6y4_retention_disposition_dry_run,
        build_oacp_c6y4_retention_operator_review_packet,
        build_oacp_cache_maintenance_dry_run_report,
        build_oacp_cache_operator_decision_record,
        plan_oacp_artifact_cache_maintenance,
    )
    from tests.unit.test_oacp_c6y5_retention_disposition_decisions import _record

    record = replace(_record(), tenant_id=tenant)
    plan = plan_oacp_artifact_cache_maintenance(
        records=(record,),
        now_iso="2026-06-12T00:01:00.000Z",
        grantex_available=True,
        action_intent="prepare_only",
        risk_tier="low",
        scope_filter=OacpArtifactCacheRepositoryQuery(tenant_id=tenant, merchant_id="mch_C6W3"),
    )
    packet = build_oacp_cache_maintenance_dry_run_report(maintenance_plan=plan, report_kind="operator_review_packet")
    decision = build_oacp_cache_operator_decision_record(
        review_packet=packet,
        decision_kind="request_more_evidence",
        reviewer_identity_ref="operator_ref_c6y5_reviewer_001",
        decided_at="2026-06-12T00:02:00.000Z",
    )
    bundle = build_oacp_c6x9_audit_export_bundle(
        cache_records=(record,),
        maintenance_plans=(plan,),
        review_packets=(packet,),
        operator_decision_records=(decision,),
        durable_decision_records=(dict(decision),),
        generated_at="2026-06-12T00:03:00.000Z",
        tenant_id=tenant,
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
    )
    manifest = build_oacp_c6y1_audit_export_review_manifest(
        audit_export_bundle=bundle,
        generated_at="2026-06-12T00:04:00.000Z",
        retention_class="standard_internal_review",
    )
    summary = build_oacp_c6y3_audit_review_manifest_summary(
        (manifest,),
        query=OacpAuditReviewManifestRepositoryQuery(
            tenant_id=tenant, merchant_id="mch_C6W3", retention_class="standard_internal_review"
        ),
        generated_at="2026-06-12T00:05:00.000Z",
    )
    dry_run = build_oacp_c6y4_retention_disposition_dry_run(
        manifest_summary=summary, generated_at="2026-06-12T00:06:00.000Z"
    )
    operator_packet = build_oacp_c6y4_retention_operator_review_packet(
        retention_disposition_dry_run=dry_run, generated_at="2026-06-12T00:07:00.000Z"
    )
    return bundle, dry_run, operator_packet


async def _store_chain(ledger_api, tenant: str, kinds: tuple[str, ...]) -> str:
    bundle, dry_run, operator_packet = _retention_chain(tenant)
    stored = await ledger_api.create_review_manifest(
        ledger_api.ReviewManifestCreate(
            audit_export_bundle=bundle,
            retention_class="standard_internal_review",
            generated_at=datetime(2026, 6, 12, 0, 4, tzinfo=UTC),
        ),
        tenant_id=tenant,
    )
    for minute, kind in enumerate(kinds, start=8):
        await ledger_api.create_retention_disposition(
            ledger_api.RetentionDispositionCreate(
                retention_disposition_dry_run=dry_run,
                operator_review_packet=operator_packet,
                decision_kind=kind,
                decided_at=datetime(2026, 6, 12, 0, minute, tzinfo=UTC),
            ),
            _request(),
            tenant_id=tenant,
        )
    return stored["manifest"]["manifest_id"]


@pytest.mark.asyncio
async def test_due_disposition_deletes_the_manifest_through_the_trigger_and_ledgers_it(db):
    from api.v1 import commerce_ledger as ledger_api
    from core.commerce.oacp_retention import apply_due_dispositions

    tenant = str(uuid.uuid4())
    manifest_id = await _store_chain(ledger_api, tenant, ("approve_future_retention_review",))
    with pytest.raises(DBAPIError):
        async with db.begin() as conn:
            await conn.execute(
                text("DELETE FROM oacp_audit_review_manifest_records WHERE manifest_id = :m"), {"m": manifest_id}
            )
    with pytest.raises(DBAPIError):
        async with db.begin() as conn:
            await conn.execute(text("SELECT set_config('agenticorg.oacp_retention_disposition', 'forged', true)"))
            await conn.execute(
                text("DELETE FROM oacp_audit_review_manifest_records WHERE manifest_id = :m"), {"m": manifest_id}
            )

    # The deleting function runs as the table owner, which may bypass row-level security (it does on
    # this database): it still deletes nothing for a session bound to another tenant, or to none.
    disposition_id = await _scalar(
        db,
        "SELECT disposition_decision_id FROM oacp_retention_disposition_decision_records WHERE tenant_id = :t",
        t=tenant,
    )
    for bound in (str(uuid.uuid4()), ""):
        async with db.begin() as conn:
            await conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, true)"), {"t": bound})
            removed = (
                await conn.execute(
                    text("SELECT oacp_delete_due_manifest(:d, :m)"), {"d": disposition_id, "m": manifest_id}
                )
            ).scalar()
        assert removed is False
    assert (
        await _scalar(
            db, "SELECT count(*) FROM oacp_audit_review_manifest_records WHERE manifest_id = :m", m=manifest_id
        )
        == 1
    )

    due = await ledger_api.list_due_retention(tenant_id=tenant)
    assert [item["manifest_ids"] for item in due["due"]] == [[manifest_id]]
    outcome = await apply_due_dispositions(uuid.UUID(tenant))
    assert outcome == {"dispositions": 1, "deleted": 1, "errors": 0}
    assert (
        await _scalar(
            db, "SELECT count(*) FROM oacp_audit_review_manifest_records WHERE manifest_id = :m", m=manifest_id
        )
        == 0
    )
    assert (
        await _scalar(
            db, "SELECT count(*) FROM oacp_retention_disposition_decision_records WHERE tenant_id = :t", t=tenant
        )
        == 1
    )
    events = await _events(db, tenant)
    assert "commerce.oacp.retention.manifest_deleted" in events
    assert "commerce.oacp.retention.disposition_applied" in events
    assert (await apply_due_dispositions(uuid.UUID(tenant)))["deleted"] == 0


@pytest.mark.asyncio
async def test_a_legal_hold_blocks_retention_in_the_task_and_in_the_trigger(db):
    from api.v1 import commerce_ledger as ledger_api
    from core.commerce.oacp_retention import apply_due_dispositions

    tenant = str(uuid.uuid4())
    manifest_id = await _store_chain(
        ledger_api, tenant, ("approve_future_retention_review", "approve_future_legal_hold_review")
    )
    assert await apply_due_dispositions(uuid.UUID(tenant)) == {"dispositions": 0, "deleted": 0, "errors": 0}
    approving = await _scalar(
        db,
        "SELECT disposition_decision_id FROM oacp_retention_disposition_decision_records "
        "WHERE tenant_id = :t AND decision_kind = 'approve_future_retention_review'",
        t=tenant,
    )
    with pytest.raises(DBAPIError):
        async with db.begin() as conn:
            await conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, true)"), {"t": tenant})
            await conn.execute(
                text("SELECT set_config('agenticorg.oacp_retention_disposition', :d, true)"), {"d": approving}
            )
            await conn.execute(
                text("DELETE FROM oacp_audit_review_manifest_records WHERE manifest_id = :m"), {"m": manifest_id}
            )
    assert (
        await _scalar(
            db, "SELECT count(*) FROM oacp_audit_review_manifest_records WHERE manifest_id = :m", m=manifest_id
        )
        == 1
    )


@pytest.mark.asyncio
async def test_manifest_naming_another_tenant_is_refused(db):
    from api.v1 import commerce_ledger as ledger_api

    bundle, _, _ = _retention_chain(str(uuid.uuid4()))
    with pytest.raises(HTTPException) as refused:
        await ledger_api.create_review_manifest(
            ledger_api.ReviewManifestCreate(audit_export_bundle=bundle), tenant_id=str(uuid.uuid4())
        )
    assert refused.value.status_code == 403
