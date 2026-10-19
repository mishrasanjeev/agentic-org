# SPDX-License-Identifier: Apache-2.0
"""G14-G16 under the NOBYPASSRLS application role: witness checkpoints, retention, operator decisions, grants.

Uses the fixtures of ``test_rls_nobypass_role`` (app-role engine, every session routed to it).
Skipped unless ``AGENTICORG_RLS_TEST_DB_URL`` points at a NOBYPASSRLS role on a database
migrated to ``v6z88_commerce_ledger`` or later.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.test_rls_nobypass_role import RLS_DB_URL, _app_role_sessions, env  # noqa: F401

pytestmark = pytest.mark.skipif(
    not RLS_DB_URL,
    reason="set AGENTICORG_RLS_TEST_DB_URL to a NOBYPASSRLS application-role DSN",
)


async def _count(session, sql: str, **params) -> int:
    return int((await session.execute(text(sql), params)).scalar_one())


@pytest.mark.asyncio
async def test_witness_checkpoints_are_tenant_bound_and_append_only(env) -> None:  # noqa: F811
    from core.commerce.oacp_grantex_trust import reset_oacp_trust_caches
    from core.database import get_tenant_session
    from core.governance.audit_witness import witness_head
    from tests.grantex_service_fakes import FakeGrantexService

    reset_oacp_trust_caches()
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    answer = await witness_head(
        tenant_a, head_seq=3, head_hash="a" * 64, anchored_at=None, client=FakeGrantexService().client(), enabled=True
    )
    assert answer["status"] == "witnessed"
    async with get_tenant_session(tenant_b) as session:
        assert (
            await _count(
                session, "SELECT count(*) FROM audit_chain_witness_checkpoints WHERE tenant_id = :t", t=str(tenant_a)
            )
            == 0
        )
    with pytest.raises(DBAPIError):
        async with get_tenant_session(tenant_b) as session:
            await session.execute(
                text(
                    "INSERT INTO audit_chain_witness_checkpoints "
                    "(id, tenant_id, head_seq, head_hash, anchored_at, status, fork_evidence) "
                    "VALUES (:id, :t, 1, :h, now(), 'fork', '{}'::jsonb)"
                ),
                {"id": str(uuid.uuid4()), "t": str(tenant_a), "h": "b" * 64},
            )
    with pytest.raises(DBAPIError):
        async with get_tenant_session(tenant_a) as session:
            await session.execute(
                text("UPDATE audit_chain_witness_checkpoints SET head_seq = 9 WHERE tenant_id = :t"),
                {"t": str(tenant_a)},
            )
    reset_oacp_trust_caches()


@pytest.mark.asyncio
async def test_retention_runs_under_the_app_role_and_stays_in_its_tenant(env) -> None:  # noqa: F811
    from api.v1 import commerce_ledger as ledger_api
    from core.commerce.oacp_retention import apply_due_dispositions
    from core.database import get_tenant_session
    from tests.integration.test_commerce_ledger_routes_postgres import _retention_chain

    tenant = str(uuid.uuid4())
    bundle, dry_run, packet = _retention_chain(tenant)
    stored = await ledger_api.create_review_manifest(
        ledger_api.ReviewManifestCreate(
            audit_export_bundle=bundle, generated_at=datetime(2026, 6, 12, 0, 4, tzinfo=UTC)
        ),
        tenant_id=tenant,
    )
    manifest_id = stored["manifest"]["manifest_id"]
    disposition = await ledger_api.create_retention_disposition(
        ledger_api.RetentionDispositionCreate(
            retention_disposition_dry_run=dry_run,
            operator_review_packet=packet,
            decision_kind="approve_future_retention_review",
            decided_at=datetime(2026, 6, 12, 0, 8, tzinfo=UTC),
        ),
        SimpleNamespace(state=SimpleNamespace(user_sub="operator-1")),
        tenant_id=tenant,
    )
    disposition_id = disposition["disposition_decision"]["disposition_decision_id"]

    # Another tenant's session cannot see, let alone delete, the manifest, even naming the disposition.
    other = uuid.uuid4()
    async with get_tenant_session(other) as session:
        removed = (
            await session.execute(
                text("SELECT oacp_delete_due_manifest(:d, :m)"), {"d": disposition_id, "m": manifest_id}
            )
        ).scalar()
        assert removed is False
    assert (await apply_due_dispositions(other))["deleted"] == 0

    # The app role holds no plain DELETE path around the trigger.
    with pytest.raises(DBAPIError):
        async with get_tenant_session(uuid.UUID(tenant)) as session:
            await session.execute(
                text("DELETE FROM oacp_audit_review_manifest_records WHERE manifest_id = :m"), {"m": manifest_id}
            )

    assert await apply_due_dispositions(uuid.UUID(tenant)) == {"dispositions": 1, "deleted": 1, "errors": 0}
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        assert (
            await _count(
                session, "SELECT count(*) FROM oacp_audit_review_manifest_records WHERE manifest_id = :m", m=manifest_id
            )
            == 0
        )
        assert (
            await _count(
                session,
                "SELECT count(*) FROM audit_log WHERE tenant_id = :t AND "
                "event_type = 'commerce.oacp.retention.manifest_deleted'",
                t=tenant,
            )
            == 1
        )


@pytest.mark.asyncio
async def test_operator_decisions_and_grant_attach_under_the_app_role(env) -> None:  # noqa: F811
    from api.v1 import commerce_ledger as ledger_api
    from core.commerce.arbiter_grantex import attach_grant
    from core.commerce.arbiter_store import persist_decision
    from core.database import get_tenant_session
    from tests.integration.test_rls_nobypass_commerce_ledger import _allow_decision

    tenant = str(uuid.uuid4())
    decision = _allow_decision(tenant)
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        await persist_decision(session, decision)
    assert await attach_grant(decision, jti="dg_app_role_1", recorded_at=datetime.now(UTC)) is True
    assert await attach_grant(decision, jti="dg_app_role_2", recorded_at=datetime.now(UTC)) is False

    request = SimpleNamespace(state=SimpleNamespace(user_sub="operator-1"))
    answer = await ledger_api.create_operator_decision(
        decision.decision_id,
        ledger_api.OperatorDecisionCreate(decision_kind="request_more_evidence"),
        request,
        tenant_id=tenant,
    )
    assert answer["arbiter_decision"]["grantex_grant_jti"] == "dg_app_role_1"
    with pytest.raises(HTTPException) as missing:
        await ledger_api.create_operator_decision(
            decision.decision_id,
            ledger_api.OperatorDecisionCreate(decision_kind="request_more_evidence"),
            request,
            tenant_id=str(uuid.uuid4()),
        )
    assert missing.value.status_code == 404
    bundle = await ledger_api.export_ledger(from_seq=1, to_seq=None, tenant_id=tenant)
    assert bundle["tenant_id"] == tenant and bundle["unsealed_commerce_rows"] >= 3
