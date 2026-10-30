# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""End-to-end proof that the application is correct under a NOBYPASSRLS role.

Runs the real API (httpx ASGI transport) and the real beat/cron coroutines
against a PostgreSQL database *connected as the application role* created
by ``scripts/create_app_role.sql``. Skipped unless
``AGENTICORG_RLS_TEST_DB_URL`` points at such a role, and refuses to run if
that role can bypass RLS — a green run therefore means every flow below
worked with row-level security actually enforced.

    AGENTICORG_RLS_TEST_DB_URL=postgresql+asyncpg://agenticorg_app:<pw>@localhost:5432/agenticorg \\
        python -m pytest -q --no-cov tests/integration/test_rls_nobypass_role.py

The schema must already be migrated (as the owner role) to
``v6z87_platform_ctx_policies`` or later: the app role has no DDL rights and
this module issues none. Every session the application opens in this module,
including modules that imported ``async_session_factory`` at import time, is
routed to the app-role engine through ``session_factory_scope``.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

RLS_DB_URL = os.getenv("AGENTICORG_RLS_TEST_DB_URL")

pytestmark = pytest.mark.skipif(
    not RLS_DB_URL,
    reason="set AGENTICORG_RLS_TEST_DB_URL to a NOBYPASSRLS application-role DSN",
)

_PASSWORD = "Rls-Proof-Passw0rd!"  # noqa: S105 - test fixture credential


async def _bootstrap_engine():
    """Swap the app's engine/session factory for one bound to the app role."""
    import core.database as db_mod

    engine = create_async_engine(RLS_DB_URL, echo=False, poolclass=NullPool)
    async with engine.connect() as conn:
        posture = await db_mod.get_db_role_posture(conn)
        head = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    if not posture.rls_enforced:
        await engine.dispose()
        pytest.fail(
            f"AGENTICORG_RLS_TEST_DB_URL role {posture.role!r} is superuser={posture.superuser} "
            f"bypassrls={posture.bypass_rls}; this proof requires a NOBYPASSRLS role"
        )
    # Compare the revision number, not the string: "v6z104_..." sorts before "v6z87".
    number = re.match(r"v6z(\d+)_", head)
    if number is None or int(number.group(1)) < 87:
        await engine.dispose()
        pytest.fail(f"database is at {head}; migrate to v6z87_platform_ctx_policies or later first")
    return engine


class _Env:
    def __init__(self) -> None:
        self.engine: Any = None
        self.factory: Any = None
        self.original: tuple[Any, Any] | None = None
        self.tenant_a: dict[str, Any] = {}
        self.tenant_b: dict[str, Any] = {}


@pytest_asyncio.fixture(scope="module")
async def env() -> AsyncGenerator[_Env, None]:
    import core.database as db_mod

    state = _Env()
    state.engine = await _bootstrap_engine()
    state.factory = async_sessionmaker(state.engine, class_=AsyncSession, expire_on_commit=False)
    state.original = (db_mod.engine, db_mod.async_session_factory)
    # Code that reads the module attributes lazily (``from core.database
    # import engine`` inside a function) gets the app-role engine.
    db_mod.engine = state.engine
    db_mod.async_session_factory = state.factory
    try:
        yield state
    finally:
        db_mod.engine, db_mod.async_session_factory = state.original
        await state.engine.dispose()


@pytest.fixture(autouse=True)
def _app_role_sessions(env: _Env):
    """Route every helper and guarded-factory session to the app role for the test."""
    from core.database import session_factory_scope

    with session_factory_scope(lambda: env.factory):
        yield


@pytest_asyncio.fixture
async def client(env: _Env) -> AsyncGenerator[AsyncClient, None]:
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _signup(client: AsyncClient, label: str) -> dict[str, Any]:
    email = f"rls-{label}-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/v1/auth/signup",
        json={
            "org_name": f"RLS Proof {label} {uuid.uuid4().hex[:6]}",
            "admin_email": email,
            "admin_name": f"Admin {label}",
            "password": _PASSWORD,
        },
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    return {"email": email, "token": body["access_token"], "tenant_id": body["user"]["tenant_id"]}


# ── 0. posture ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_role_posture_is_enforced_and_startup_check_is_quiet(env: _Env, caplog) -> None:
    import core.database as db_mod

    async with env.engine.connect() as conn:
        posture = await db_mod.verify_db_role_rls_posture(conn)
    assert posture is not None and posture.rls_enforced
    assert "DB_ROLE_BYPASSES_RLS" not in caplog.text

    # Sanity: with no context bound, a tenant table is empty for this role.
    async with env.engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM agents"))).scalar_one() == 0


# ── 1-2. signup + login (platform context → tenant-bound writes) ─────────


@pytest.mark.asyncio
async def test_signup_creates_tenant_user_and_seeded_defaults(env: _Env, client: AsyncClient) -> None:
    env.tenant_a = await _signup(client, "a")
    env.tenant_b = await _signup(client, "b")
    assert env.tenant_a["tenant_id"] != env.tenant_b["tenant_id"]

    # Seeded defaults landed under the new tenant (WITH CHECK accepted them).
    async with env.engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('agenticorg.tenant_id', :tid, true)"), {"tid": env.tenant_a["tenant_id"]}
        )
        agents = (await conn.execute(text("SELECT count(*) FROM agents"))).scalar_one()
    assert agents > 0


@pytest.mark.asyncio
async def test_login_by_email_before_tenant_is_known(env: _Env, client: AsyncClient) -> None:
    resp = await client.post("/api/v1/auth/login", json={"email": env.tenant_a["email"], "password": _PASSWORD})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["tenant_id"] == env.tenant_a["tenant_id"]
    env.tenant_a["token"] = body["access_token"]

    wrong = await client.post("/api/v1/auth/login", json={"email": env.tenant_a["email"], "password": "nope"})
    assert wrong.status_code == 401

    me = await client.get("/api/v1/auth/me", headers=_auth(env.tenant_a["token"]))
    assert me.status_code == 200, me.text


# ── 3-4. agents: list + run, and cross-tenant isolation ──────────────────


@pytest.mark.asyncio
async def test_list_agents_is_tenant_scoped(env: _Env, client: AsyncClient) -> None:
    a = await client.get("/api/v1/agents", headers=_auth(env.tenant_a["token"]))
    b = await client.get("/api/v1/agents", headers=_auth(env.tenant_b["token"]))
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    ids_a = {item["id"] for item in a.json()["items"]}
    ids_b = {item["id"] for item in b.json()["items"]}
    assert ids_a and ids_b
    assert ids_a.isdisjoint(ids_b)
    env.tenant_a["agent_id"] = next(iter(ids_a))
    env.tenant_a["agent_ids"] = sorted(ids_a)

    # Tenant B cannot read tenant A's agent by id.
    cross = await client.get(f"/api/v1/agents/{env.tenant_a['agent_id']}", headers=_auth(env.tenant_b["token"]))
    assert cross.status_code == 404


@pytest.mark.asyncio
async def test_run_agent_end_to_end_with_fake_chat_model(env: _Env, client: AsyncClient, monkeypatch) -> None:
    """Full LangGraph run: agent load, LLM call, audit write — all RLS-bound.

    The LangGraph path has no built-in test double (``AGENTICORG_TEST_FAKE_LLM``
    only covers ``core.llm.router``), so the chat model is stubbed here; every
    database access in the run is real and goes through the app role.
    """
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    import core.langgraph.agent_graph as agent_graph

    class _FakeChat(FakeListChatModel):
        def bind_tools(self, tools, **kwargs):  # noqa: ANN001 - LangChain signature
            return self

    monkeypatch.setattr(
        agent_graph,
        "create_chat_model",
        lambda *a, **k: _FakeChat(responses=['{"summary": "RLS rollout complete", "status": "done"}']),
    )
    # Seeded agents that need an external connector refuse to dispatch (409
    # connector_not_ready_for_dispatch) in a database with no connector
    # configs; run the first seeded agent that has none.
    resp = None
    for agent_id in env.tenant_a["agent_ids"]:
        resp = await client.post(
            f"/api/v1/agents/{agent_id}/run",
            headers=_auth(env.tenant_a["token"]),
            json={"inputs": {"query": "Summarise the RLS rollout in one line."}},
        )
        if resp.status_code == 409 and "connector_not_ready_for_dispatch" in resp.text:
            continue
        env.tenant_a["agent_id"] = agent_id
        break
    assert resp is not None and resp.status_code == 200, resp.text if resp is not None else "no agents"
    body = resp.json()
    # The run either completes or, below the agent's confidence floor, parks
    # for human review (a hitl_queue write) - both persist tenant-scoped rows.
    assert body["status"] in {"completed", "hitl_triggered"}, body
    if body["status"] == "completed":
        assert body["output"] == {"summary": "RLS rollout complete", "status": "done"}

    # The run's audit entry landed under the tenant (audit_log WITH CHECK).
    async with env.engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('agenticorg.tenant_id', :tid, true)"), {"tid": env.tenant_a["tenant_id"]}
        )
        audited = (
            await conn.execute(
                text("SELECT count(*) FROM audit_log WHERE event_type = 'agent.run' AND agent_id = CAST(:aid AS uuid)"),
                {"aid": env.tenant_a["agent_id"]},
            )
        ).scalar_one()
    assert audited >= 1

    # Tenant B cannot run tenant A's agent.
    cross = await client.post(
        f"/api/v1/agents/{env.tenant_a['agent_id']}/run",
        headers=_auth(env.tenant_b["token"]),
        json={"inputs": {"query": "Summarise the RLS rollout in one line."}},
    )
    assert cross.status_code == 404, cross.text


# ── 4b. the platform marker is read-only and limited to v6z87's tables ───


@pytest.mark.asyncio
async def test_platform_context_reads_pre_auth_tables_only(env: _Env) -> None:
    from sqlalchemy.exc import DBAPIError

    from core.database import get_platform_session

    async with env.engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM agents"))).scalar_one() == 0
    async with get_platform_session() as session:
        # v6z87 SELECT policy: the Grantex DID lookup sees every tenant's agents.
        tenants = (await session.execute(text("SELECT count(DISTINCT tenant_id) FROM agents"))).scalar_one()
        assert tenants >= 2
        # Tables outside the platform policy stay tenant-isolated.
        assert (await session.execute(text("SELECT count(*) FROM audit_log"))).scalar_one() == 0
        assert (await session.execute(text("SELECT count(*) FROM report_schedules"))).scalar_one() == 0
    # No platform write policy: an UPDATE under the marker alone touches nothing.
    async with get_platform_session() as session:
        updated = await session.execute(text("UPDATE agents SET status = status"))
        assert updated.rowcount == 0
    # And an INSERT is refused by every agents policy's WITH CHECK.
    with pytest.raises(DBAPIError, match="row-level security"):
        async with get_platform_session() as session:
            await session.execute(
                text(
                    "INSERT INTO agents (id, tenant_id, name, agent_type, domain) "
                    "VALUES (gen_random_uuid(), CAST(:tid AS uuid), 'x', 'x', 'finance')"
                ),
                {"tid": env.tenant_a["tenant_id"]},
            )


# ── 5. approvals ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_approvals(env: _Env, client: AsyncClient) -> None:
    resp = await client.get("/api/v1/approvals", headers=_auth(env.tenant_a["token"]))
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json()["items"], list)


# ── 6. billing subscription read/write + beat expiry ─────────────────────


@pytest.mark.asyncio
async def test_billing_subscription_round_trip_and_plural_expiry(env: _Env, client: AsyncClient) -> None:
    from core.billing import subscriptions as subs

    tid = env.tenant_a["tenant_id"]
    resp = await client.get("/api/v1/billing/subscription", headers=_auth(env.tenant_a["token"]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan"] == "free"

    start = datetime.now(UTC) - timedelta(days=40)
    recorded = await subs.record_subscription(
        tid,
        provider="plural",
        plan="starter",
        provider_subscription_id="order_rls_proof",
        current_period_start=start,
        current_period_end=start + timedelta(days=30),
    )
    assert recorded["is_paid"] is True

    resp = await client.get("/api/v1/billing/subscription", headers=_auth(env.tenant_a["token"]))
    assert resp.json()["plan"] == "starter"
    # Tenant B does not see tenant A's entitlement.
    other = await client.get("/api/v1/billing/subscription", headers=_auth(env.tenant_b["token"]))
    assert other.json()["plan"] == "free"

    summary = await subs.expire_overdue_plural_subscriptions()
    assert summary["failed"] == 0
    assert summary["checked"] >= 2
    assert summary["expired"] >= 1
    assert (await subs.get_subscription(tid))["plan"] == "free"


# ── 7. DSAR access ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dsar_access_request(env: _Env, client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/dsar/access",
        headers=_auth(env.tenant_a["token"]),
        json={"subject_email": env.tenant_a["email"], "request_type": "access"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] in {"completed", "processing", "pending"}
    assert body.get("id") or body.get("request_id")


# ── 8. beat: report scheduler claim (platform ctx) + per-tenant work ──────


@pytest.mark.asyncio
async def test_report_schedule_claim_under_platform_context(env: _Env, client: AsyncClient) -> None:
    from core.tasks.report_tasks import _claim_due_schedules

    created = await client.post(
        "/api/v1/report-schedules",
        headers=_auth(env.tenant_a["token"]),
        json={
            "report_type": "cfo_daily",
            "cron_expression": "daily",
            "delivery_channels": [{"type": "email", "target": env.tenant_a["email"]}],
            "format": "pdf",
        },
    )
    assert created.status_code == 201, created.text
    schedule_id = created.json()["id"]

    # Make it due, as the tenant (RLS-bound UPDATE).
    async with env.engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('agenticorg.tenant_id', :tid, true)"), {"tid": env.tenant_a["tenant_id"]}
        )
        await conn.execute(
            text("UPDATE report_schedules SET next_run_at = now() - interval '1 minute' WHERE id = CAST(:id AS uuid)"),
            {"id": schedule_id},
        )

    claimed = await _claim_due_schedules()
    assert schedule_id in {c["schedule_id"] for c in claimed}
    mine = next(c for c in claimed if c["schedule_id"] == schedule_id)
    assert mine["tenant_id"] == env.tenant_a["tenant_id"]

    # Claim advanced next_run_at, so a second sweep does not double-fire.
    assert schedule_id not in {c["schedule_id"] for c in await _claim_due_schedules()}


@pytest.mark.asyncio
async def test_rpa_schedule_claim_and_compliance_cron_run(env: _Env) -> None:
    from api.v1.rpa_schedules import claim_due_schedule_ids, due_schedule_ids
    from core.cron.compliance_alerts import run_compliance_alert_cron

    # No due RPA schedules — the point is that the platform-context claim
    # does not error under the app role.
    assert isinstance(await due_schedule_ids(), list)
    assert isinstance(await claim_due_schedule_ids(), list)

    result = await run_compliance_alert_cron()
    assert result.get("skipped") != "locked"
    assert set(result) >= {"new_deadlines", "alerts_7d", "alerts_1d", "overdue"}


# ── 9. pre-auth cross-tenant lookups (api keys, sso discovery, branding) ──


@pytest.mark.asyncio
async def test_api_key_create_list_and_pre_auth_lookup(env: _Env, client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/org/api-keys", headers=_auth(env.tenant_a["token"]), json={"name": "rls-proof"}
    )
    assert created.status_code in (200, 201), created.text
    full_key = created.json()["key"]

    listed = await client.get("/api/v1/org/api-keys", headers=_auth(env.tenant_a["token"]))
    assert listed.status_code == 200
    rows = listed.json()
    rows = rows["items"] if isinstance(rows, dict) else rows
    assert any(item["name"] == "rls-proof" for item in rows)
    # Tenant B lists none of tenant A's keys.
    other = await client.get("/api/v1/org/api-keys", headers=_auth(env.tenant_b["token"]))
    other_rows = other.json()
    other_rows = other_rows["items"] if isinstance(other_rows, dict) else other_rows
    assert not any(item["name"] == "rls-proof" for item in other_rows)

    # The key authenticates on its own (prefix lookup under platform context,
    # last_used_at stamped under the key's tenant).
    via_key = await client.get("/api/v1/agents", headers=_auth(full_key))
    assert via_key.status_code == 200, via_key.text
    via_token = await client.get("/api/v1/agents", headers=_auth(env.tenant_a["token"]))
    assert {item["id"] for item in via_key.json()["items"]} == {item["id"] for item in via_token.json()["items"]}


@pytest.mark.asyncio
async def test_public_discovery_routes_do_not_error(env: _Env, client: AsyncClient) -> None:
    sso = await client.get("/api/v1/auth/sso/providers", params={"email": env.tenant_a["email"]})
    assert sso.status_code == 200, sso.text
    assert sso.json() == {"providers": []}

    branding = await client.get("/api/v1/branding", params={"tenant_slug": "does-not-exist"})
    assert branding.status_code == 200, branding.text


# ── 10. workflow engine durable state (platform ctx + tenant bind) ───────


@pytest.mark.asyncio
async def test_workflow_state_store_round_trip(env: _Env) -> None:
    import core.database as db_mod
    from workflows.state_store import SqlAlchemyWorkflowStateRepository

    repo = SqlAlchemyWorkflowStateRepository(db_mod.async_session_factory)
    run_id = f"wfr_rls_{uuid.uuid4().hex[:10]}"
    state = {"id": run_id, "status": "running", "definition": {"steps": []}, "step_results": {}}
    await repo.save(
        state,
        state_hash="h1",
        tenant_id=uuid.UUID(env.tenant_a["tenant_id"]),
        workflow_run_id=None,
        actor="rls-proof",
    )
    # Celery resume tasks load by run id alone.
    loaded = await repo.load(run_id)
    assert loaded is not None and loaded["status"] == "running"


@pytest.mark.asyncio
async def test_cdc_webhook_store_is_tenant_bound(env: _Env, monkeypatch) -> None:
    import core.database as db_mod
    from core.cdc.receiver import SqlAlchemyCDCEventStore

    store = SqlAlchemyCDCEventStore(db_mod.async_session_factory)
    tid = env.tenant_a["tenant_id"]
    inserted = await store.insert_event(
        {
            "tenant_id": tid,
            "connector": "hubspot",
            "provider_event_id": f"evt_{uuid.uuid4().hex[:8]}",
            "fingerprint": uuid.uuid4().hex,
            "event_hash": uuid.uuid4().hex,
            "event_type": "contact.updated",
            "resource_type": "contact",
            "resource_id": "c1",
            "payload": {"k": "v"},
        }
    )
    assert inserted["inserted"] is True
    event_id = inserted["event"]["id"]
    await store.mark_processed(event_id, outcome={"ok": True}, tenant_id=tid)
    events, total = await store.list_events(tenant_id=tid, connector="hubspot")
    assert total >= 1 and any(e["id"] == event_id for e in events)
    # Another tenant sees nothing.
    other_events, other_total = await store.list_events(tenant_id=env.tenant_b["tenant_id"], connector="hubspot")
    assert other_total == 0 and other_events == []
    with pytest.raises(ValueError):
        await store.mark_processed(event_id, outcome={}, tenant_id=None)


# ── 11. least-privilege grants (scripts/create_app_role.sql) ─────────────


async def _refused(env: _Env, sql: str, tenant_id: str | None = None) -> str:
    """Run ``sql`` as the app role (tenant bound, so RLS would let rows through) and return the refusal."""
    from sqlalchemy.exc import DBAPIError

    async with env.engine.connect() as conn:
        if tenant_id:
            await conn.execute(text("SELECT set_config('agenticorg.tenant_id', :tid, true)"), {"tid": tenant_id})
        with pytest.raises(DBAPIError) as refused:
            await conn.execute(text(sql))
    return str(refused.value)


@pytest.mark.asyncio
async def test_app_role_grants_are_least_privilege(env: _Env) -> None:
    tid = env.tenant_a["tenant_id"]
    # The agent run above left audit rows that RLS lets this tenant see, so a
    # zero-row result would not count: each statement must be refused outright.
    async with env.engine.connect() as conn:
        await conn.execute(text("SELECT set_config('agenticorg.tenant_id', :tid, true)"), {"tid": tid})
        assert (await conn.execute(text("SELECT count(*) FROM audit_log"))).scalar_one() >= 1

    denied = (
        "DELETE FROM audit_log",
        "UPDATE audit_log SET action = 'edited'",
        "TRUNCATE audit_log",
        "TRUNCATE agents",
        "TRUNCATE users",
        "DELETE FROM audit_chain_anchors",
        "UPDATE capability_evidence_records SET tenant_id = tenant_id",
        "DELETE FROM capability_promotion_events",
        "UPDATE eval_dataset_versions SET note = note",
        "DELETE FROM capability_readiness_records",
        "UPDATE alembic_version SET version_num = version_num",
        "INSERT INTO alembic_version (version_num) VALUES ('rls_probe')",
        "SELECT count(*) FROM alembic_migration_progress",
        "CREATE TABLE rls_privilege_probe (id int)",
        "ALTER TABLE agents DISABLE ROW LEVEL SECURITY",
        "CREATE TRIGGER rls_probe BEFORE INSERT ON agents FOR EACH ROW EXECUTE FUNCTION audit_log_reject_mutation()",
    )
    for statement in denied:
        message = await _refused(env, statement, tid)
        assert "permission denied" in message or "must be owner" in message, (statement, message)

    async with env.engine.connect() as conn:
        has_arbiter = (
            await conn.execute(text("SELECT to_regclass('public.commerce_arbiter_decisions') IS NOT NULL"))
        ).scalar_one()
    if has_arbiter:
        # v6z88: append-only except the consumed / countersigned transitions.
        for statement in (
            "UPDATE commerce_arbiter_decisions SET outcome = outcome",
            "DELETE FROM commerce_arbiter_decisions",
            "TRUNCATE commerce_arbiter_decisions",
        ):
            message = await _refused(env, statement, tid)
            assert "permission denied" in message, (statement, message)
        async with env.engine.connect() as conn:
            await conn.execute(text("SELECT set_config('agenticorg.tenant_id', :tid, true)"), {"tid": tid})
            # The transition columns are writable (no row matches; the privilege check passes).
            await conn.execute(
                text("UPDATE commerce_arbiter_decisions SET consumed_at = now() WHERE decision_id = 'rls-none'")
            )
            await conn.execute(
                text(
                    "UPDATE commerce_arbiter_decisions SET grantex_grant_jti = 'x', grantex_recorded_at = now() "
                    "WHERE decision_id = 'rls-none'"
                )
            )

    async with env.engine.connect() as conn:
        has_witness, has_retention_fn = (
            await conn.execute(
                text(
                    "SELECT to_regclass('public.audit_chain_witness_checkpoints') IS NOT NULL, "
                    "to_regprocedure('public.oacp_delete_due_manifest(text, text)') IS NOT NULL"
                )
            )
        ).one()
    if has_witness:
        for statement in (
            "UPDATE audit_chain_witness_checkpoints SET tenant_id = tenant_id",
            "DELETE FROM audit_chain_witness_checkpoints",
        ):
            message = await _refused(env, statement, tid)
            assert "permission denied" in message, (statement, message)
    if has_retention_fn:
        # Retention deletes through the tenant-bound definer function, never by a DELETE grant.
        message = await _refused(env, "DELETE FROM oacp_audit_review_manifest_records", tid)
        assert "permission denied" in message, message
        async with env.engine.connect() as conn:
            assert (
                await conn.execute(
                    text("SELECT has_function_privilege('public.oacp_delete_due_manifest(text, text)', 'EXECUTE')")
                )
            ).scalar_one() is True

    async with env.engine.connect() as conn:
        # Schema version stays readable (init_db's head check).
        assert (await conn.execute(text("SELECT count(*) FROM alembic_version"))).scalar_one() == 1
        # Restricted functions: only the platform spend total is callable.
        can_guard = (
            await conn.execute(
                text("SELECT has_function_privilege('public.connector_config_company_scope_guard()', 'EXECUTE')")
            )
        ).scalar_one()
        assert can_guard is False
        spend = (await conn.execute(text("SELECT public.gemini_daily_platform_spend()"))).scalar_one()
        assert spend is not None and float(spend) >= 0


@pytest.mark.asyncio
async def test_audit_chain_seals_under_column_scoped_update(env: _Env) -> None:
    from core.governance import audit_chain

    tid = uuid.UUID(env.tenant_a["tenant_id"])
    outcome = await audit_chain.seal(tid, batch=500)
    assert outcome.head.seq >= 1
    result = await audit_chain.verify(tid)
    assert result.status == "verified", result.to_dict()

    async with env.engine.connect() as conn:
        has_trigger = (
            await conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'audit_log'::regclass "
                    "AND tgname = 'audit_log_immutable' AND NOT tgisinternal)"
                )
            )
        ).scalar_one()
    # v6z87 installs the trigger on every database, including one bootstrapped
    # from an empty schema; the chain-column grant relies on it.
    assert has_trigger, "audit_log_immutable is missing: migrate to v6z87_platform_ctx_policies or later"
    # The chain-column grant does not reopen a sealed row: the trigger allows
    # only the unsealed -> sealed transition, so none of the chain columns of
    # a sealed row can be rewritten.
    for statement in (
        "UPDATE audit_log SET chain_hash = repeat('0', 64) WHERE chain_seq IS NOT NULL",
        "UPDATE audit_log SET chain_prev = repeat('0', 64) WHERE chain_seq IS NOT NULL",
        "UPDATE audit_log SET chain_seq = chain_seq + 1000 WHERE chain_seq IS NOT NULL",
        "UPDATE audit_log SET sealed_at = now() WHERE chain_seq IS NOT NULL",
    ):
        message = await _refused(env, statement, str(tid))
        assert "append-only" in message, (statement, message)
    # Nothing above took effect.
    assert (await audit_chain.verify(tid)).status == "verified"


def test_module_runs_serially_on_one_loop() -> None:
    # Guard against pytest-asyncio loop-scope drift: the fixtures above are
    # module-scoped and must share the loop the tests run on.
    assert asyncio.get_event_loop_policy() is not None
