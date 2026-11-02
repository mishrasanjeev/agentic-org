# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Money saved, alerts and approval views against real Postgres, as a role without BYPASSRLS.

A scratch schema holds the tables these routes read (built from the ORM
models, without the v6z109 tables), then the v6z106 and v6z109 migrations
run, and every tenant-scoped table gets the forced row-level policy production
uses. Handlers run through the real ``get_tenant_session`` as a NOBYPASSRLS
role.

Money saved: the first read seeds tenant A's defaults (and only A's), the
figures stay hidden until an administrator confirms the baselines and rates
the period uses, then add up exactly; an administrator's edit is audited and
counts as confirmed; a CFO reads but cannot edit; tenant B's rows are out of
reach. Alerts: the sweep opens one alert per condition, refreshes it while it
holds, resolves it when it clears, emails act alerts once; each role sees the
alerts it owns, an acknowledgement is audited and sticks, the view's needs-you
list carries them, and tenant B sees none. Approval views: the first view of a
pending approval is recorded once.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from core.insights.metrics import day_bounds, local_today

KOLKATA = ZoneInfo("Asia/Kolkata")
VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
PASSWORD = "local-test-password"  # noqa: S105 - a throwaway role in a disposable test database
BASE_TABLES = (
    "tenants",
    "users",
    "companies",
    "agents",
    "hitl_queue",
    "agent_cost_ledger",
    "spend_usage_rollups",
    "spend_fx_rates",
    "budget_alerts",
    "audit_log",
    "connectors",
    "connector_configs",
    "workflow_definitions",
    "workflow_runs",
)
MIGRATED = ("value_baselines", "value_rates", "insights_alerts")

pytestmark = [
    pytest.mark.skipif(not os.getenv("AGENTICORG_DB_URL"), reason="Requires the disposable Docker Postgres database"),
    pytest.mark.timeout(300),
]


def _sync_url() -> str:
    return os.environ["AGENTICORG_DB_URL"].replace("+asyncpg", "")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_{name}_value_alerts", VERSIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _in_schema(conn: Connection, schema: str) -> None:
    conn.exec_driver_sql(f'SET search_path TO "{schema}"')


def _tables():
    import core.models  # noqa: F401 - registers every model on the metadata
    from core.models.base import BaseModel

    md = BaseModel.metadata
    closure: list = []
    stack = list(BASE_TABLES)
    while stack:
        name = stack.pop()
        if name in [t.name for t in closure] or name in MIGRATED:
            continue
        table = md.tables[name]
        closure.append(table)
        stack.extend(fk.column.table.name for fk in table.foreign_keys)
    return closure


def _rls(conn: Connection, table: str, column: str = "tenant_id") -> None:
    # Identifiers come from the ORM metadata, never from input.
    conn.exec_driver_sql(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    conn.exec_driver_sql(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    conn.exec_driver_sql(
        f'CREATE POLICY tenant_isolation ON "{table}" '
        f"USING ({column}::text = current_setting('agenticorg.tenant_id', true)) "
        f"WITH CHECK ({column}::text = current_setting('agenticorg.tenant_id', true))"
    )


class World:
    def __init__(self, admin: Engine, schema: str) -> None:
        self.admin = admin
        self.schema = schema

    def add(self, *objects) -> None:
        with self.admin.connect() as conn:
            _in_schema(conn, self.schema)
            with Session(bind=conn) as session:
                session.add_all(objects)
                session.commit()
            conn.commit()

    def scalar(self, sql: str, **params):
        with self.admin.begin() as conn:
            _in_schema(conn, self.schema)
            return conn.execute(text(sql), params).scalar()

    def rows(self, sql: str, **params) -> list:
        with self.admin.begin() as conn:
            _in_schema(conn, self.schema)
            return list(conn.execute(text(sql), params).all())


@pytest.fixture
async def world(monkeypatch) -> AsyncIterator[World]:
    admin = create_engine(_sync_url())
    schema = "insights_value_" + uuid.uuid4().hex[:12]
    role = "insights_value_" + uuid.uuid4().hex[:12]
    tables = _tables()
    with admin.begin() as conn:
        # Identifiers are a fixed prefix plus UUID hex; never client input.
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        _in_schema(conn, schema)
        from core.models.base import BaseModel

        BaseModel.metadata.create_all(conn, tables=tables)
        with Operations.context(MigrationContext.configure(conn)):
            _load("v6_z106_insights_foundation").upgrade()
            _load("v6_z109_insights_value_alerts").upgrade()
        for table in tables:
            if table.name == "tenants":
                _rls(conn, "tenants", "id")
            elif "tenant_id" in table.c:
                _rls(conn, table.name)
        conn.exec_driver_sql(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{PASSWORD}' NOBYPASSRLS")
        conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
        conn.exec_driver_sql(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
    url = admin.url.set(drivername="postgresql+asyncpg", username=role, password=PASSWORD)
    runtime = create_async_engine(url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}})
    from core import database
    from core.config import settings

    factory = async_sessionmaker(runtime, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)
    monkeypatch.setattr("api.v1.insights.insights_enabled_for", AsyncMock(return_value=True))
    monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
    try:
        yield World(admin, schema)
    finally:
        await runtime.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
            conn.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')
        admin.dispose()


def _request(role: str, tenant: uuid.UUID, user_id: uuid.UUID, *, domains=None, admin=False, machine=False):
    scopes = ["agenticorg:admin"] if admin else ["insights:read", "approvals:write"]
    claims = {
        "sub": f"{role}@example.test",
        "role": role,
        "agenticorg:user_id": str(user_id),
        "agenticorg:tenant_id": str(tenant),
        "tenant_id": str(tenant),
        "agenticorg:domains": domains,
        "grantex:scopes": scopes,
    }
    return SimpleNamespace(
        state=SimpleNamespace(
            claims=claims, scopes=scopes, auth_mode="api_key" if machine else None, tenant_id=str(tenant)
        ),
        query_params={},
    )


def _seed(world: World) -> SimpleNamespace:
    from core.models.agent import Agent, AgentCostLedger
    from core.models.budget_alert import BudgetAlert
    from core.models.connector_config import ConnectorConfig
    from core.models.hitl import HITLQueue
    from core.models.insights import RunFact
    from core.models.spend import SpendFxRate
    from core.models.tenant import Tenant
    from core.models.user import User

    now = datetime.now(UTC)
    today = local_today(KOLKATA, now)
    start = day_bounds(today, KOLKATA)[0] + timedelta(minutes=1)
    a, b = uuid.uuid4(), uuid.uuid4()
    world.add(
        Tenant(id=a, name="Synthetic value A", slug="value-a-" + a.hex, data_region="IN", settings={}),
        Tenant(id=b, name="Synthetic value B", slug="value-b-" + b.hex, data_region="IN", settings={}),
    )
    admin_a, cfo_a, coo_a, admin_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    world.add(
        User(id=admin_a, tenant_id=a, email="admin-a@example.test", name="Admin A", role="admin", status="active"),
        User(id=cfo_a, tenant_id=a, email="cfo-a@example.test", name="CFO A", role="cfo", status="active"),
        User(id=coo_a, tenant_id=a, email="coo-a@example.test", name="COO A", role="coo", status="active"),
        User(id=admin_b, tenant_id=b, email="admin-b@example.test", name="Admin B", role="admin", status="active"),
    )
    base = {"system_prompt_ref": "prompts/synthetic.txt", "hitl_condition": "confidence < 0.8"}
    ap, custom, idle, b_ap = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    world.add(
        Agent(
            id=ap,
            tenant_id=a,
            name="Invoice matcher",
            agent_type="ap_processor",
            domain="finance",
            status="active",
            **base,
        ),
        Agent(
            id=custom,
            tenant_id=a,
            name="Custom bot",
            agent_type="custom_bot",
            domain="finance",
            status="active",
            **base,
        ),
        Agent(
            id=idle,
            tenant_id=a,
            name="Dormant payroll",
            agent_type="payroll_engine",
            domain="hr",
            status="active",
            created_at=now - timedelta(days=40),
            **base,
        ),
        Agent(
            id=b_ap,
            tenant_id=b,
            name="Tenant B matcher",
            agent_type="ap_processor",
            domain="finance",
            status="active",
            **base,
        ),
    )

    def fact(tenant, agent_id, agent_type, minute, status="completed"):
        return RunFact(
            tenant_id=tenant,
            run_key="agents:" + uuid.uuid4().hex,
            entrypoint="agents",
            agent_id=agent_id,
            agent_type=agent_type,
            domain="finance",
            status=status,
            duration_ms=1000,
            tokens_total=10,
            cost_usd=Decimal("0.01"),
            started_at=start + timedelta(minutes=minute),
        )

    world.add(
        *(fact(a, ap, "ap_processor", i) for i in range(6)),
        fact(a, ap, "ap_processor", 7, status="failed"),
        fact(a, custom, "custom_bot", 8),
        fact(a, custom, "custom_bot", 9),
        *(fact(b, b_ap, "ap_processor", i) for i in range(3)),
    )
    common = {"trigger_type": "low_confidence", "decision_options": {"options": ["approve"]}, "context": {}}
    decided = start + timedelta(minutes=30)
    world.add(
        # Decided ten minutes after it was first opened: ten minutes of review.
        HITLQueue(
            tenant_id=a,
            agent_id=ap,
            title="Approve INV-1",
            assignee_role="finance",
            status="decided",
            decision="approve",
            decision_by=admin_a,
            decision_at=decided,
            first_viewed_at=decided - timedelta(minutes=10),
            created_at=start,
            expires_at=now + timedelta(days=2),
            **common,
        ),
        # Pending past its expiry: an alert.
        HITLQueue(
            tenant_id=a,
            agent_id=ap,
            title="Approve INV-2",
            assignee_role="finance",
            created_at=start,
            expires_at=now - timedelta(minutes=5),
            **common,
        ),
    )
    midnight = datetime(today.year, today.month, today.day)  # noqa: DTZ001 - the ledger is naive UTC
    world.add(
        AgentCostLedger(tenant_id=a, agent_id=ap, period_date=midnight, cost_usd=Decimal("1.5"), token_count=10),
        SpendFxRate(tenant_id=a, rate_date=today, currency="USD", rate_to_inr=Decimal("80")),
        BudgetAlert(
            tenant_id=a, name="Daily AI budget", period="daily", threshold_usd=Decimal("1.00"), warn_at_percent=80
        ),
        ConnectorConfig(tenant_id=a, connector_name="tally", health_status="unhealthy"),
    )
    return SimpleNamespace(a=a, b=b, admin_a=admin_a, cfo_a=cfo_a, coo_a=coo_a, admin_b=admin_b, ap=ap, idle=idle)


@pytest.mark.asyncio
async def test_money_saved_waits_for_confirmation_then_adds_up(world) -> None:
    from api.deps import get_active_human_admin
    from api.v1 import insights as views
    from api.v1 import insights_value as api
    from core.insights.value_rates import IN_RATES

    seed = _seed(world)
    a = str(seed.a)
    admin = _request("admin", seed.a, seed.admin_a, admin=True)
    cfo = _request("cfo", seed.a, seed.cfo_a, domains=["finance"])

    first = await api.get_value(admin, tenant_id=a)
    # The defaults are seeded for tenant A only, and unconfirmed: nothing is shown.
    assert world.scalar("SELECT count(*) FROM value_baselines WHERE tenant_id = :t", t=seed.a) == 29
    assert world.scalar("SELECT count(*) FROM value_rates WHERE tenant_id = :t", t=seed.a) == len(IN_RATES)
    assert world.scalar("SELECT count(*) FROM value_baselines WHERE tenant_id = :t", t=seed.b) == 0
    summary = first["summary"]
    assert summary["confirmed"] is False and summary["waterfall"] is None and summary["hours_saved"] is None
    assert summary["completed_tasks"] == 6 and summary["failed_tasks"] == 1
    assert summary["unpriced_task_types"] == ["custom_bot"]
    assert len(summary["unconfirmed"]["baseline_ids"]) == 1 and len(summary["unconfirmed"]["rate_ids"]) == 2
    finance = {m["key"]: m for m in (await views.insights_view("finance", admin, tenant_id=a))["tiles"]}
    assert finance["value.money_saved"]["value"] is None
    assert finance["value.money_saved"]["confirmation"]["confirmed"] is False

    # The CFO reads but cannot change anything; another tenant's administrator cannot either.
    assert (await api.get_value(cfo, tenant_id=a))["can_edit"] is False
    with pytest.raises(HTTPException) as refused:
        await get_active_human_admin(cfo)
    assert refused.value.status_code == 403
    admin_a = await get_active_human_admin(admin)
    admin_b = await get_active_human_admin(_request("admin", seed.b, seed.admin_b, admin=True))
    baseline_id = uuid.UUID(summary["unconfirmed"]["baseline_ids"][0])
    with pytest.raises(HTTPException) as missing:
        await api.edit_baseline(
            baseline_id, api.BaselinePatch(baseline_minutes=Decimal(99)), admin_b, tenant_id=str(seed.b)
        )
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as wrong_tenant:
        await api.confirm(api.ConfirmIn(baseline_ids=[baseline_id]), admin_b, tenant_id=a)
    assert wrong_tenant.value.status_code == 403

    await api.confirm(
        api.ConfirmIn(
            baseline_ids=[uuid.UUID(i) for i in summary["unconfirmed"]["baseline_ids"]],
            rate_ids=[uuid.UUID(i) for i in summary["unconfirmed"]["rate_ids"]],
        ),
        admin_a,
        tenant_id=a,
    )
    after = (await api.get_value(admin, tenant_id=a))["summary"]
    assert after["confirmed"] is True
    # 6 invoices x 12 min at the clerk rate; 1 failed; 10 review minutes at the reviewer rate; 1.5 USD at 80.
    clerk = IN_RATES["finance_associate"].hourly_cost
    reviewer = IN_RATES["reviewer"].hourly_cost
    gross = Decimal(6) * 12 / 60 * clerk
    error = Decimal(1) * 12 / 60 * clerk
    review = Decimal(10) / 60 * reviewer
    net = gross - Decimal("120") - review - error
    steps = {row["label"]: row["value"] for row in after["waterfall"]}
    assert steps["gross_saved"] == pytest.approx(float(gross), abs=1e-3)
    assert steps["spend"] == pytest.approx(-120.0, abs=1e-3)
    assert steps["review_cost"] == pytest.approx(-float(review), abs=1e-3)
    assert steps["error_cost"] == pytest.approx(-float(error), abs=1e-3)
    assert steps["net_saved"] == pytest.approx(float(net), abs=1e-3)
    assert after["hours_saved"] == 1.2
    # Spend under the INR 5,000 floor: no ROI.
    assert after["roi_percent"] is None

    edited = await api.edit_baseline(baseline_id, api.BaselinePatch(baseline_minutes=Decimal(20)), admin_a, tenant_id=a)
    assert edited["baseline_minutes"] == 20.0 and edited["source"] == "tenant" and edited["confirmed"] is True
    change = world.rows(
        "SELECT action, details FROM audit_log WHERE tenant_id = :t AND event_type = 'insights.value.change' "
        "ORDER BY created_at",
        t=seed.a,
    )
    assert [r[0] for r in change] == ["confirm", "baseline.edit"]
    assert change[1][1]["before"]["baseline_minutes"] == 12.0 and change[1][1]["after"]["baseline_minutes"] == 20.0
    hours = (await api.get_value(admin, tenant_id=a))["summary"]["hours_saved"]
    assert hours == 2.0  # 6 x 20 min

    added = await api.add_baseline(
        api.BaselineIn(agent_type="custom_bot", baseline_minutes=Decimal(5), role="finance_associate"),
        admin_a,
        tenant_id=a,
    )
    assert added["confirmed"] is True
    with pytest.raises(HTTPException) as duplicate:
        await api.add_baseline(
            api.BaselineIn(agent_type="custom_bot", baseline_minutes=Decimal(5), role="finance_associate"),
            admin_a,
            tenant_id=a,
        )
    assert duplicate.value.status_code == 409
    assert (await api.get_value(admin, tenant_id=a))["summary"]["unpriced_task_types"] == []

    await api.put_settings(api.SettingsIn(roi_floor=Decimal(100)), admin_a, tenant_id=a)
    roi = (await api.get_value(admin, tenant_id=a))["summary"]["roi_percent"]
    assert roi is not None
    b_value = await api.get_value(_request("admin", seed.b, seed.admin_b, admin=True), tenant_id=str(seed.b))
    assert all(r["source"] == "seeded" and not r["confirmed"] for r in b_value["baselines"])


@pytest.mark.asyncio
async def test_alerts_open_refresh_resolve_and_are_acknowledged(world, monkeypatch) -> None:
    from api.v1 import insights as views
    from api.v1 import insights_alerts as api
    from core.insights import alerts
    from core.insights.rollup import tenant_zone
    from core.tasks import insights_tasks

    seed = _seed(world)
    notified: list[tuple[uuid.UUID, int]] = []

    async def fake_notify(tenant_id, ids, now):
        notified.append((tenant_id, len(ids)))
        return 0

    monkeypatch.setattr(alerts, "notify", fake_notify)
    clock = insights_tasks.TenantClock(seed.a, tenant_zone({}, "IN"))
    now = datetime.now(UTC)
    opened = await insights_tasks._evaluate_alerts(clock, now)
    live = {
        r[0]: r
        for r in world.rows(
            "SELECT rule_key, entity_ref, severity, owner_role, state FROM insights_alerts WHERE tenant_id = :t",
            t=seed.a,
        )
    }
    assert opened == len(live)
    assert {"approvals.expired", "cost.budget", "connectors.unhealthy", "agents.idle"} <= set(live)
    assert live["connectors.unhealthy"][1:4] == ("tally", "act", "coo")
    assert live["agents.idle"][1] == str(seed.idle) and live["agents.idle"][3] == "chro"
    assert live["cost.budget"][2] == "act" and live["cost.budget"][3] == "cfo"
    acts = sum(1 for r in live.values() if r[2] == "act")
    assert notified == [(seed.a, acts)]

    # A second sweep refreshes, opens nothing and does not notify again (the fake left notified_at unset,
    # so mark them sent as notify would).
    world.scalar("UPDATE insights_alerts SET notified_at = now() WHERE severity = 'act' RETURNING 1")
    assert await insights_tasks._evaluate_alerts(clock, now + timedelta(minutes=15)) == 0
    assert notified == [(seed.a, acts)]

    # Each role sees what it owns; the administrator sees everything; tenant B sees nothing.
    coo = await api.list_alerts(_request("coo", seed.a, seed.coo_a, domains=["ops"]), tenant_id=str(seed.a))
    assert [x["rule_key"] for x in coo["alerts"]] == ["connectors.unhealthy"]
    cfo = await api.list_alerts(_request("cfo", seed.a, seed.cfo_a, domains=["finance"]), tenant_id=str(seed.a))
    assert {x["rule_key"] for x in cfo["alerts"]} == {"cost.budget"}
    admin = _request("admin", seed.a, seed.admin_a, admin=True)
    everything = await api.list_alerts(admin, tenant_id=str(seed.a))
    assert len(everything["alerts"]) == len(live)
    b = await api.list_alerts(_request("admin", seed.b, seed.admin_b, admin=True), tenant_id=str(seed.b))
    assert b["alerts"] == []

    # The executive view's needs-you list carries the alerts (budgets and pending approvals show live there).
    view = await views.insights_view("executive", admin, tenant_id=str(seed.a))
    alert_items = [i for i in view["needs_you"] if i["kind"] == "alert"]
    assert {i["rule_key"] for i in alert_items} >= {"connectors.unhealthy", "approvals.expired"}
    assert all(i["rule_key"] != "cost.budget" for i in alert_items)

    # The COO acknowledges the connector alert; it is audited, leaves the open list and stays acknowledged.
    connector = coo["alerts"][0]["id"]
    coo_request = _request("coo", seed.a, seed.coo_a, domains=["ops"])
    acked = await api.ack_alert(coo_request, uuid.UUID(connector), tenant_id=str(seed.a))
    assert acked["state"] == "acked"
    assert (await api.list_alerts(coo_request, tenant_id=str(seed.a)))["alerts"] == []
    assert (
        world.scalar(
            "SELECT count(*) FROM audit_log WHERE tenant_id = :t AND event_type = 'insights.alert.ack'", t=seed.a
        )
        == 1
    )
    # The CFO cannot see or acknowledge it; an API key cannot acknowledge at all.
    with pytest.raises(HTTPException) as hidden:
        await api.ack_alert(
            _request("cfo", seed.a, seed.cfo_a, domains=["finance"]), uuid.UUID(connector), tenant_id=str(seed.a)
        )
    assert hidden.value.status_code == 404
    with pytest.raises(HTTPException) as machine:
        await api.ack_alert(
            _request("admin", seed.a, seed.admin_a, admin=True, machine=True),
            uuid.UUID(connector),
            tenant_id=str(seed.a),
        )
    assert machine.value.status_code == 403

    # The connector recovers: its alert resolves; it recurs: a new alert opens.
    world.scalar("UPDATE connector_configs SET health_status = 'healthy' RETURNING 1")
    await insights_tasks._evaluate_alerts(clock, now + timedelta(minutes=30))
    assert (
        world.scalar(
            "SELECT state FROM insights_alerts WHERE rule_key = 'connectors.unhealthy' AND tenant_id = :t", t=seed.a
        )
        == "resolved"
    )
    world.scalar("UPDATE connector_configs SET health_status = 'unhealthy' RETURNING 1")
    await insights_tasks._evaluate_alerts(clock, now + timedelta(minutes=45))
    states = [
        r[0]
        for r in world.rows(
            "SELECT state FROM insights_alerts WHERE rule_key = 'connectors.unhealthy' ORDER BY fired_at"
        )
    ]
    assert states == ["resolved", "open"]


@pytest.mark.asyncio
async def test_approval_views_are_recorded_once(world) -> None:
    from api.v1 import approvals

    seed = _seed(world)
    pending = world.scalar("SELECT id FROM hitl_queue WHERE tenant_id = :t AND status = 'pending'", t=seed.a)
    decided = world.scalar("SELECT id FROM hitl_queue WHERE tenant_id = :t AND status = 'decided'", t=seed.a)
    admin = _request("admin", seed.a, seed.admin_a, admin=True)
    marked = await approvals.mark_approvals_viewed(
        approvals.ApprovalsViewed(ids=[pending, decided]), admin, tenant_id=str(seed.a)
    )
    assert marked == {"marked": 1}
    first = world.scalar("SELECT first_viewed_at FROM hitl_queue WHERE id = :i", i=pending)
    assert first is not None
    again = await approvals.mark_approvals_viewed(
        approvals.ApprovalsViewed(ids=[pending]), admin, tenant_id=str(seed.a)
    )
    assert again == {"marked": 0}
    assert world.scalar("SELECT first_viewed_at FROM hitl_queue WHERE id = :i", i=pending) == first
    # Another tenant cannot mark tenant A's approval; an API key marks nothing.
    other = await approvals.mark_approvals_viewed(
        approvals.ApprovalsViewed(ids=[pending]),
        _request("admin", seed.b, seed.admin_b, admin=True),
        tenant_id=str(seed.b),
    )
    assert other == {"marked": 0}
    machine = await approvals.mark_approvals_viewed(
        approvals.ApprovalsViewed(ids=[pending]),
        _request("admin", seed.a, seed.admin_a, admin=True, machine=True),
        tenant_id=str(seed.a),
    )
    assert machine == {"marked": 0}
