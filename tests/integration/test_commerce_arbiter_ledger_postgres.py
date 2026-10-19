# SPDX-License-Identifier: Apache-2.0
"""Phase 2 on a real Postgres: arbiter decisions, single-use consumption, ledger rows and append-only triggers.

Requires the disposable database in ``AGENTICORG_DB_URL`` (migrated to head by the module fixture).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests import commerce_arbiter_fixtures as fx


@pytest.fixture(scope="module", autouse=True)
async def migrated_schema(tmp_path_factory):
    if not os.getenv("AGENTICORG_DB_URL"):
        pytest.skip("Requires the disposable Docker Postgres database")
    env = {**os.environ, "AGENTICORG_MIGRATION_AUDIT_DIR": str(tmp_path_factory.mktemp("migration-audit"))}
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "scripts/alembic_migrate.py"],
        capture_output=True, text=True, check=False, env=env,
    )
    assert result.returncode == 0, result.stderr[-4000:]


@pytest.fixture()
async def db(monkeypatch):
    from core import database

    engine = create_async_engine(os.environ["AGENTICORG_DB_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture()
def runtime(monkeypatch, db):
    """Route handlers on the real database, with the runtime scope and merchant config supplied in memory."""
    from api.v1 import commerce_runtime as api

    tenant = str(uuid.uuid4())
    state = {"config": fx.merchant_config()}

    async def _scope(**_kwargs):
        return fx.cache_records(tenant), fx.products(tenant), fx.capabilities()

    async def _config(*_args):
        return state["config"]

    monkeypatch.setattr(api, "_load_runtime_scope", _scope)
    monkeypatch.setattr(api, "_merchant_config_for_arbiter", _config)
    monkeypatch.setenv("PLURAL_PINE_LIVE_EXECUTION_ENABLED", "true")
    return api, tenant, state


async def _ledger(engine, tenant: str) -> list[dict]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT event_type, outcome, resource_id, details, signature FROM audit_log "
            "WHERE tenant_id = :t AND resource_type = 'commerce_ledger' ORDER BY created_at, id"
        ), {"t": tenant})).mappings().all()
    return [dict(row) for row in rows]


async def _decision_row(engine, decision_id: str) -> dict | None:
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT * FROM commerce_arbiter_decisions WHERE decision_id = :d"
        ), {"d": decision_id})).mappings().first()
    return dict(row) if row else None


def _purchase(api, **overrides):
    body = {
        "merchant_id": fx.MERCHANT, "seller_agent_id": fx.SELLER, "buyer_agent_id": fx.BUYER,
        "product_ref_or_query": "Canvas Tote", "quantity": 1, "idempotency_key": "purchase-key-1",
        "buyer_authority": fx.buyer_authority(),
    }
    body.update(overrides)
    return api.PurchasePreparationRequest(**body)


def _pos(api, **overrides):
    body = {
        "merchant_id": fx.MERCHANT, "seller_agent_id": fx.SELLER, "buyer_agent_id": fx.BUYER,
        "buyer_session_ref": "buyer-session-1", "product_ref_or_query": "Canvas Tote", "quantity": 1,
        "store_id": "store-1", "pos_location": {"display_name": "Example Store", "city": "Pune", "country_code": "IN"},
        "idempotency_key": "pos-key-1", "buyer_authority": fx.buyer_authority(),
    }
    body.update(overrides)
    return api.OfflinePosHandoffRequest(**body)


@pytest.mark.asyncio
async def test_purchase_decision_is_sealed_and_consumed_once(runtime, db):
    api, tenant, _ = runtime
    first = await api.prepare_purchase_handoff(_purchase(api), tenant_id=tenant)
    decision = first["arbiter_decision"]
    assert decision["outcome"] == "allow"
    assert first["status"] == "prepared_handoff_blocked_live_execution_disabled"
    assert first["decision_consumed"] is False
    row = await _decision_row(db, decision["decision_id"])
    assert row["outcome"] == "allow" and row["consumed_at"] is None
    assert row["payload_hash"] == decision["payload_hash"]
    assert row["rules_evaluated"] == list(decision["rules_evaluated"])

    second = await api.prepare_purchase_handoff(
        _purchase(api, decision_id=decision["decision_id"]), tenant_id=tenant
    )
    assert second["decision_consumed"] is True
    assert second["status"] == "prepared_provider_handoff"
    assert second["prepared_handoff"]["live_execution_enabled"] is True
    assert (await _decision_row(db, decision["decision_id"]))["consumed_at"] is not None

    replay = await api.prepare_purchase_handoff(
        _purchase(api, decision_id=decision["decision_id"]), tenant_id=tenant
    )
    assert replay["decision_consumed"] is False
    assert replay["status"] == "prepared_handoff_blocked_decision_refused"
    assert replay["blocker"]["code"] == "decision_consumed"

    ledger = await _ledger(db, tenant)
    events = [row["event_type"] for row in ledger]
    assert events.count("commerce.arbiter.decision") == 3
    assert events.count("commerce.runtime.purchase.prepare") == 3
    assert events.count("commerce.arbiter.decision.consumed") == 1
    for row in ledger:
        assert len(row["details"]["ledger"]["payload_hash"]) == 64
        assert row["signature"]
    sealed_decision = next(r for r in ledger if r["resource_id"] == decision["decision_id"])
    assert sealed_decision["details"]["ledger"]["payload_hash"] == decision["payload_hash"]


@pytest.mark.asyncio
async def test_purchase_with_a_decision_for_another_payload_is_refused(runtime, db):
    api, tenant, _ = runtime
    first = await api.prepare_purchase_handoff(_purchase(api, quantity=1), tenant_id=tenant)
    decision_id = first["arbiter_decision"]["decision_id"]
    other = await api.prepare_purchase_handoff(
        _purchase(api, quantity=2, decision_id=decision_id), tenant_id=tenant
    )
    assert other["arbiter_decision"]["outcome"] == "allow"
    assert other["decision_consumed"] is False
    assert other["blocker"]["code"] == "decision_payload_mismatch"
    assert (await _decision_row(db, decision_id))["consumed_at"] is None


@pytest.mark.asyncio
async def test_purchase_refusal_is_sealed_with_the_failing_rule_and_returned_as_a_blocker(runtime, db):
    api, tenant, _ = runtime
    result = await api.prepare_purchase_handoff(
        _purchase(api, buyer_authority=fx.buyer_authority(consent_status="revoked")), tenant_id=tenant
    )
    assert result["status"] == "prepared_handoff_blocked_arbiter_refused"
    assert result["blocker"]["code"] == "arbiter_refused"
    assert result["blocker"]["failing_rule"] == "consent_active"
    assert set(result["blocker"]) >= {"code", "owner", "action", "required_config", "unblock_command"}
    assert result["escalation"] is None
    row = await _decision_row(db, result["arbiter_decision"]["decision_id"])
    assert row["outcome"] == "refuse" and row["failing_rule"] == "consent_active"
    ledger = await _ledger(db, tenant)
    refused = [r for r in ledger if r["event_type"] == "commerce.arbiter.decision"]
    assert refused[0]["outcome"] == "refuse"
    assert refused[0]["details"]["decision"]["failing_rule"] == "consent_active"


@pytest.mark.asyncio
async def test_deprecated_live_execution_boolean_is_ignored(runtime, db):
    api, tenant, _ = runtime
    result = await api.prepare_purchase_handoff(
        _purchase(api, live_execution_approved=True, buyer_authority=None), tenant_id=tenant
    )
    assert result["deprecated_fields_ignored"] == ["live_execution_approved"]
    assert result["decision_consumed"] is False
    assert result["prepared_handoff"]["live_execution_enabled"] is False
    assert result["arbiter_decision"]["failing_rule"] == "mandate_present_unexpired"


@pytest.mark.asyncio
async def test_pos_handoff_without_an_allow_decision_is_refused(runtime, db):
    api, tenant, _ = runtime
    refused = await api.create_offline_pos_handoff(_pos(api, buyer_authority=None), tenant_id=tenant)
    assert refused["pos_handoff_created"] is False
    assert refused["blocker"]["code"] == "arbiter_refused"
    assert refused["blocker"]["failing_rule"] == "mandate_present_unexpired"

    issued = await api.create_offline_pos_handoff(_pos(api), tenant_id=tenant)
    assert issued["status"] == "decision_issued"
    assert issued["pos_handoff_created"] is False
    assert issued["blocker"]["code"] == "decision_id_missing"

    forged = await api.create_offline_pos_handoff(_pos(api, decision_id="arbdec_" + "0" * 40), tenant_id=tenant)
    assert forged["pos_handoff_created"] is False
    assert forged["blocker"]["code"] == "decision_not_found"

    async with db.connect() as conn:
        packets = (await conn.execute(text(
            "SELECT count(*) FROM commerce_c6z_offline_pos_handoff_packets WHERE tenant_id = :t"
        ), {"t": tenant})).scalar_one()
    assert packets == 0

    created = await api.create_offline_pos_handoff(_pos(api, decision_id=issued["decision_id"]), tenant_id=tenant)
    assert created["pos_handoff_created"] is True
    assert created["packet"]["decision_id"] == issued["decision_id"]
    again = await api.create_offline_pos_handoff(_pos(api, decision_id=issued["decision_id"]), tenant_id=tenant)
    assert again["pos_handoff_created"] is False
    assert again["blocker"]["code"] == "decision_consumed"

    ledger = await _ledger(db, tenant)
    handoffs = [r for r in ledger if r["event_type"] == "commerce.runtime.pos.offline.handoff.create"]
    assert [r["outcome"] for r in handoffs] == ["refused", "blocked", "blocked", "success", "blocked"]


@pytest.mark.asyncio
async def test_two_concurrent_consumers_one_wins(db):
    from core.commerce.arbiter import (
        ArbiterContext,
        ConsentEvidence,
        MerchantLimits,
        PassportEvidence,
        artifact_evidence_from_records,
        decide,
    )
    from core.commerce.arbiter_store import DecisionRefusedError, persist_decision, require_allow_decision
    from core.database import get_tenant_session

    tenant = str(uuid.uuid4())
    now = datetime.now(UTC)
    allow = decide("mandate_request", ArbiterContext(
        tenant_id=tenant, merchant_id=fx.MERCHANT, seller_agent_id=fx.SELLER, buyer_agent_id=fx.BUYER, now=now,
        payload={"request": "mandate", "nonce": tenant}, amount_minor_units=2500, currency="INR", quantity=1,
        consent=ConsentEvidence(ref="consent-1", status="active"),
        passport=PassportEvidence(ref="passport-1", max_amount_minor_units=5000, currency="INR",
                                  expires_at=now + timedelta(hours=1)),
        artifacts=artifact_evidence_from_records(fx.cache_records(tenant, now=now)),
        merchant_limits=MerchantLimits(max_order_amount_minor_units=5000, currency_allowlist=("INR",),
                                       max_quantity_per_order=2),
    ))
    assert allow.outcome == "allow", allow.reason
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        await persist_decision(session, allow)

    async def consume():
        async with get_tenant_session(uuid.UUID(tenant)) as session:
            await require_allow_decision(
                session, tenant_id=tenant, decision_id=allow.decision_id,
                payload_hash=allow.payload_hash, action="mandate_request",
            )
            await asyncio.sleep(0.2)  # hold the row lock so the other consumer has to wait for it
            return "won"

    results = await asyncio.gather(consume(), consume(), return_exceptions=True)
    wins = [r for r in results if r == "won"]
    losses = [r for r in results if isinstance(r, DecisionRefusedError)]
    assert len(wins) == 1 and len(losses) == 1, results
    assert losses[0].code == "decision_consumed"
    consumed = [r for r in await _ledger(db, tenant) if r["event_type"] == "commerce.arbiter.decision.consumed"]
    assert len(consumed) == 1


@pytest.mark.asyncio
async def test_guard_refuses_wrong_action_tenant_and_expired(db):
    from core.commerce.arbiter_store import DecisionRefusedError, require_allow_decision
    from core.database import get_tenant_session

    tenant, other = str(uuid.uuid4()), str(uuid.uuid4())
    live, old = f"arbdec_live_{tenant[:8]}", f"arbdec_old_{tenant[:8]}"
    now = datetime.now(UTC)
    async with db.begin() as conn:
        for decision_id, expires in ((live, now + timedelta(minutes=10)), (old, now - timedelta(minutes=1))):
            await conn.execute(text("""
                INSERT INTO commerce_arbiter_decisions (decision_id, tenant_id, merchant_id, seller_agent_id,
                    action, payload_hash, outcome, rules_evaluated, rule_results, evidence, evidence_hashes,
                    policy_version, decided_at, expires_at)
                VALUES (:d, :t, 'm', 's', 'pos_handoff', :h, 'allow', '[]', '[]', '{}', '[]', 'v1', :at, :exp)
            """), {"d": decision_id, "t": tenant, "h": "e" * 64, "at": expires - timedelta(minutes=10),
                   "exp": expires})
    cases = [
        (tenant, live, "purchase_handoff", "e" * 64, "decision_action_mismatch"),
        (tenant, live, "pos_handoff", "f" * 64, "decision_payload_mismatch"),
        (tenant, old, "pos_handoff", "e" * 64, "decision_expired"),
        (tenant, None, "pos_handoff", "e" * 64, "decision_id_missing"),
    ]
    for tid, decision_id, action, payload_hash, code in cases:
        with pytest.raises(DecisionRefusedError) as exc:
            async with get_tenant_session(uuid.UUID(tid)) as session:
                await require_allow_decision(
                    session, tenant_id=tid, decision_id=decision_id, payload_hash=payload_hash, action=action
                )
        assert exc.value.code == code
    # Another tenant presenting the id: the WHERE binds the tenant, so it is not found.
    with pytest.raises(DecisionRefusedError) as exc:
        async with get_tenant_session(uuid.UUID(other)) as session:
            await require_allow_decision(
                session, tenant_id=other, decision_id=live, payload_hash="e" * 64, action="pos_handoff"
            )
    assert exc.value.code == "decision_not_found"


@pytest.mark.asyncio
async def test_rls_hides_decisions_from_another_tenant_for_a_non_bypass_role(db):
    tenant, other = str(uuid.uuid4()), str(uuid.uuid4())
    async with db.begin() as conn:
        await conn.execute(text("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ao_p2_rls_probe') THEN
                    CREATE ROLE ao_p2_rls_probe NOLOGIN NOSUPERUSER NOBYPASSRLS;
                END IF;
            END $$;
        """))
        await conn.execute(text("GRANT SELECT, INSERT ON commerce_arbiter_decisions TO ao_p2_rls_probe"))
        await conn.execute(text("""
            INSERT INTO commerce_arbiter_decisions (decision_id, tenant_id, merchant_id, seller_agent_id,
                action, payload_hash, outcome, rules_evaluated, failing_rule, rule_results, evidence,
                evidence_hashes, policy_version, decided_at, expires_at)
            VALUES (:d, :t, 'm', 's', 'pos_handoff', :h, 'refuse', '[]', 'consent_active', '[]', '{}', '[]',
                    'v1', now(), now() + interval '10 minutes')
        """), {"d": f"arbdec_rls_{tenant[:8]}", "t": tenant, "h": "a" * 64})
    async with db.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SET LOCAL ROLE ao_p2_rls_probe"))
            await conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, true)"), {"t": other})
            seen = (await conn.execute(text("SELECT count(*) FROM commerce_arbiter_decisions"))).scalar_one()
            assert seen == 0
            with pytest.raises(DBAPIError):
                await conn.execute(text("""
                    INSERT INTO commerce_arbiter_decisions (decision_id, tenant_id, merchant_id, seller_agent_id,
                        action, payload_hash, outcome, rules_evaluated, failing_rule, rule_results, evidence,
                        evidence_hashes, policy_version, decided_at, expires_at)
                    VALUES ('arbdec_rls_cross', :t, 'm', 's', 'pos_handoff', :h, 'refuse', '[]', 'consent_active',
                            '[]', '{}', '[]', 'v1', now(), now() + interval '10 minutes')
                """), {"t": tenant, "h": "a" * 64})


@pytest.mark.asyncio
async def test_append_only_triggers_refuse_update_and_delete(db):
    tenant = str(uuid.uuid4())
    now = datetime.now(UTC)
    async with db.begin() as conn:
        await conn.execute(text("""
            INSERT INTO commerce_arbiter_decisions (decision_id, tenant_id, merchant_id, seller_agent_id,
                action, payload_hash, outcome, rules_evaluated, rule_results, evidence, evidence_hashes,
                policy_version, decided_at, expires_at)
            VALUES (:d, :t, 'm', 's', 'pos_handoff', :h, 'allow', '[]', '[]', '{}', '[]', 'v1', :at, :exp)
        """), {"d": f"arbdec_trg_{tenant[:8]}", "t": tenant, "h": "b" * 64, "at": now,
               "exp": now + timedelta(minutes=10)})
    decision_id = f"arbdec_trg_{tenant[:8]}"
    forbidden = [
        "UPDATE commerce_arbiter_decisions SET outcome = 'refuse', failing_rule = 'x' WHERE decision_id = :d",
        "UPDATE commerce_arbiter_decisions SET payload_hash = repeat('c', 64) WHERE decision_id = :d",
        "UPDATE commerce_arbiter_decisions SET consumed_at = now(), merchant_id = 'other' WHERE decision_id = :d",
        "DELETE FROM commerce_arbiter_decisions WHERE decision_id = :d",
    ]
    for statement in forbidden:
        with pytest.raises(DBAPIError, match="append-only"):
            async with db.begin() as conn:
                await conn.execute(text(statement), {"d": decision_id})
    # The one-time transitions: consumption, then the countersignature reference.
    async with db.begin() as conn:
        await conn.execute(text("UPDATE commerce_arbiter_decisions SET consumed_at = now() WHERE decision_id = :d"),
                           {"d": decision_id})
    with pytest.raises(DBAPIError, match="append-only"):
        async with db.begin() as conn:
            await conn.execute(text(
                "UPDATE commerce_arbiter_decisions SET consumed_at = now() + interval '1 second' WHERE decision_id = :d"
            ), {"d": decision_id})
    async with db.begin() as conn:
        await conn.execute(text(
            "UPDATE commerce_arbiter_decisions SET grantex_grant_jti = 'jti-1', grantex_recorded_at = now() "
            "WHERE decision_id = :d"
        ), {"d": decision_id})
    with pytest.raises(DBAPIError, match="append-only"):
        async with db.begin() as conn:
            await conn.execute(text(
                "UPDATE commerce_arbiter_decisions SET grantex_grant_jti = 'jti-2' WHERE decision_id = :d"
            ), {"d": decision_id})


@pytest.mark.asyncio
async def test_oacp_record_is_mirrored_and_append_only_and_audit_log_seals(db):
    from core.commerce.oacp_artifacts import DurableOacpOperatorDecisionRepository
    from core.database import get_tenant_session
    from core.governance import audit_chain
    from tests.unit import test_oacp_c6x8_operator_decision_repository as c6x8

    tenant = c6x8.TENANT_C6W3
    decision = c6x8._decision()
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        stored = await DurableOacpOperatorDecisionRepository(session).upsert_decision(decision)
    assert stored["stored"] is True or stored["refusal_code"] == "record_append_only"
    decision_id = str(decision["decision_record_id"])
    for statement in (
        "UPDATE oacp_operator_decision_records SET reviewer_ref = 'operator_ref_other' WHERE decision_id = :d",
        "DELETE FROM oacp_operator_decision_records WHERE decision_id = :d",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db.begin() as conn:
                await conn.execute(text(statement), {"d": decision_id})
    for table in ("oacp_audit_review_manifest_records", "oacp_retention_disposition_decision_records"):
        async with db.connect() as conn:
            triggers = (await conn.execute(text(
                "SELECT count(*) FROM pg_trigger WHERE tgrelid = CAST(:t AS regclass) AND NOT tgisinternal"
            ), {"t": table})).scalar_one()
        assert triggers == 1
    mirrored = [r for r in await _ledger(db, tenant) if r["event_type"] == "commerce.oacp.operator_decision.record"]
    assert len(mirrored) == 1

    # audit_log: append-only, the sealing transition allowed, the chain verifies over the ledger rows.
    with pytest.raises(DBAPIError, match="append-only"):
        async with db.begin() as conn:
            await conn.execute(text("UPDATE audit_log SET outcome = 'tampered' WHERE tenant_id = :t"), {"t": tenant})
    outcome = await audit_chain.seal(uuid.UUID(tenant))
    assert outcome.head.seq >= 1
    verification = await audit_chain.verify(uuid.UUID(tenant))
    assert verification.status == "verified"
