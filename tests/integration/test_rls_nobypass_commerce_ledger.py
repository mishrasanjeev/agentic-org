# SPDX-License-Identifier: Apache-2.0
"""Phase 2 under the NOBYPASSRLS application role: arbiter decisions and ledger rows stay tenant-bound.

Uses the fixtures of ``test_rls_nobypass_role`` (app-role engine, every session routed to it).
Skipped unless ``AGENTICORG_RLS_TEST_DB_URL`` points at a NOBYPASSRLS role on a database
migrated to ``v6z88_commerce_ledger`` or later.

    AGENTICORG_RLS_TEST_DB_URL=postgresql+asyncpg://agenticorg_app:<pw>@localhost:5432/agenticorg \\
        python -m pytest -q --no-cov tests/integration/test_rls_nobypass_commerce_ledger.py
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests import commerce_arbiter_fixtures as fx
from tests.integration.test_rls_nobypass_role import RLS_DB_URL, _app_role_sessions, env  # noqa: F401

pytestmark = pytest.mark.skipif(
    not RLS_DB_URL,
    reason="set AGENTICORG_RLS_TEST_DB_URL to a NOBYPASSRLS application-role DSN",
)


def _allow_decision(tenant: str):
    from core.commerce.arbiter import (
        ArbiterContext,
        ConsentEvidence,
        MerchantLimits,
        PassportEvidence,
        artifact_evidence_from_records,
        decide,
    )

    now = datetime.now(UTC)
    decision = decide("mandate_request", ArbiterContext(
        tenant_id=tenant, merchant_id=fx.MERCHANT, seller_agent_id=fx.SELLER, buyer_agent_id=fx.BUYER, now=now,
        payload={"request": "mandate", "nonce": str(uuid.uuid4())}, amount_minor_units=2500, currency="INR",
        quantity=1, consent=ConsentEvidence(ref="consent-1", status="active"),
        passport=PassportEvidence(ref="passport-1", max_amount_minor_units=5000, currency="INR",
                                  expires_at=now + timedelta(hours=1)),
        artifacts=artifact_evidence_from_records(fx.cache_records(tenant, now=now)),
        merchant_limits=MerchantLimits(max_order_amount_minor_units=5000, currency_allowlist=("INR",),
                                       max_quantity_per_order=2),
    ))
    assert decision.outcome == "allow", decision.reason
    return decision


async def _count(session, sql: str, **params) -> int:
    return int((await session.execute(text(sql), params)).scalar_one())


@pytest.mark.asyncio
async def test_tenant_b_cannot_read_or_consume_tenant_a_decision(env) -> None:  # noqa: F811
    from core.commerce.arbiter_store import (
        DecisionRefusedError,
        get_decision,
        persist_decision,
        require_allow_decision,
    )
    from core.database import get_tenant_session

    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    decision = _allow_decision(tenant_a)
    async with get_tenant_session(uuid.UUID(tenant_a)) as session:
        await persist_decision(session, decision)

    async with get_tenant_session(uuid.UUID(tenant_b)) as session:
        assert await get_decision(session, tenant_a, decision.decision_id) is None
        assert await _count(session, "SELECT count(*) FROM commerce_arbiter_decisions WHERE decision_id = :d",
                            d=decision.decision_id) == 0
        assert await _count(session, "SELECT count(*) FROM audit_log WHERE resource_id = :d",
                            d=decision.decision_id) == 0
    for presented_tenant in (tenant_b, tenant_a):
        # Tenant B's session cannot see the row, whichever tenant id it names.
        with pytest.raises(DecisionRefusedError) as exc:
            async with get_tenant_session(uuid.UUID(tenant_b)) as session:
                await require_allow_decision(
                    session, tenant_id=presented_tenant, decision_id=decision.decision_id,
                    payload_hash=decision.payload_hash, action="mandate_request",
                )
        assert exc.value.code == "decision_not_found"

    async with get_tenant_session(uuid.UUID(tenant_a)) as session:
        row = await get_decision(session, tenant_a, decision.decision_id)
        assert row is not None and row.consumed_at is None
        assert await _count(session, "SELECT count(*) FROM audit_log WHERE resource_id = :d",
                            d=decision.decision_id) == 1
        await require_allow_decision(
            session, tenant_id=tenant_a, decision_id=decision.decision_id,
            payload_hash=decision.payload_hash, action="mandate_request",
        )
    with pytest.raises(DecisionRefusedError) as exc:
        async with get_tenant_session(uuid.UUID(tenant_a)) as session:
            await require_allow_decision(
                session, tenant_id=tenant_a, decision_id=decision.decision_id,
                payload_hash=decision.payload_hash, action="mandate_request",
            )
    assert exc.value.code == "decision_consumed"


@pytest.mark.asyncio
async def test_a_tenant_cannot_write_a_decision_or_ledger_row_for_another(env) -> None:  # noqa: F811
    from core.commerce.arbiter_store import persist_decision
    from core.database import get_tenant_session

    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    decision = _allow_decision(tenant_a)
    with pytest.raises(DBAPIError):
        # Session bound to tenant B, rows for tenant A: WITH CHECK refuses.
        async with get_tenant_session(uuid.UUID(tenant_b)) as session:
            await persist_decision(session, decision)


@pytest.mark.asyncio
async def test_append_only_triggers_hold_under_the_app_role(env) -> None:  # noqa: F811
    from core.commerce.arbiter_store import persist_decision
    from core.database import get_tenant_session

    tenant = str(uuid.uuid4())
    decision = _allow_decision(tenant)
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        await persist_decision(session, decision)
    for statement in (
        "UPDATE commerce_arbiter_decisions SET payload_hash = repeat('0', 64) WHERE decision_id = :d",
        "DELETE FROM commerce_arbiter_decisions WHERE decision_id = :d",
        "UPDATE audit_log SET outcome = 'refuse' WHERE resource_id = :d",
        "DELETE FROM audit_log WHERE resource_id = :d",
    ):
        # audit_log: the app role also lacks UPDATE/DELETE (scripts/create_app_role.sql); the trigger backs it.
        with pytest.raises(DBAPIError, match="append-only|permission denied for table audit_log"):
            async with get_tenant_session(uuid.UUID(tenant)) as session:
                await session.execute(text(statement), {"d": decision.decision_id})


@pytest.mark.asyncio
async def test_purchase_preparation_seals_the_decision_under_the_app_role(env, monkeypatch) -> None:  # noqa: F811
    from api.v1 import commerce_runtime as api
    from core.database import get_tenant_session

    tenant = str(uuid.uuid4())

    async def _scope(**_kwargs):
        return fx.cache_records(tenant), fx.products(tenant), fx.capabilities()

    async def _config(*_args):
        return fx.merchant_config()

    monkeypatch.setattr(api, "_load_runtime_scope", _scope)
    monkeypatch.setattr(api, "_merchant_config_for_arbiter", _config)
    result = await api.prepare_purchase_handoff(
        api.PurchasePreparationRequest(
            merchant_id=fx.MERCHANT, seller_agent_id=fx.SELLER, buyer_agent_id=fx.BUYER,
            product_ref_or_query="Canvas Tote", quantity=1, buyer_authority=fx.buyer_authority(),
        ),
        tenant_id=tenant,
    )
    decision = result["arbiter_decision"]
    assert decision["outcome"] == "allow"
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        events = [
            row[0]
            for row in (await session.execute(text(
                "SELECT event_type FROM audit_log WHERE tenant_id = :t AND resource_type = 'commerce_ledger'"
            ), {"t": tenant})).all()
        ]
    assert sorted(events) == ["commerce.arbiter.decision", "commerce.runtime.purchase.prepare"]
