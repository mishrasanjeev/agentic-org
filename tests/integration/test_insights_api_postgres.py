# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights API against real Postgres, as a role without BYPASSRLS: tenant isolation and the cost-source fallback.

A scratch schema holds the tables the Insights routes read (built from the
ORM models), the v6z106 migration adds the insights tables with their
partitions, and every tenant-scoped table gets the forced row-level security
policy production uses. The routes' handlers run through the real
``get_tenant_session`` on an engine that logs in as a NOBYPASSRLS role.

Two IN tenants each have agents, run facts (yesterday rolled up by the real
rollup, today read raw), pending approvals, cost-ledger rows and a company.
Tenant A's views, metrics and export count only tenant A's rows, a company
filter naming tenant B's company is refused, a CFO's run metrics keep to the
finance domain, and the export writes one audit row in tenant A only. Cost
reads the ledger in USD (no FX rate: the hint says so), converts to INR once
a USD rate is recorded, and switches to metered rollups once spend metering
is on and the tenant has rollup rows, while tenant B stays on its ledger.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

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

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z106_insights_foundation.py"
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
)

pytestmark = [
    pytest.mark.skipif(not os.getenv("AGENTICORG_DB_URL"), reason="Requires the disposable Docker Postgres database"),
    # Building the scratch schema (ORM tables, the v6z106 migration, RLS) takes a while.
    pytest.mark.timeout(300),
]


def _sync_url() -> str:
    return os.environ["AGENTICORG_DB_URL"].replace("+asyncpg", "")


def _migration():
    spec = importlib.util.spec_from_file_location("_v6_z106_insights_foundation_api", MIGRATION)
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
        if name in [t.name for t in closure]:
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


@pytest.fixture
async def world(monkeypatch) -> AsyncIterator[World]:
    admin = create_engine(_sync_url())
    schema = "insights_api_" + uuid.uuid4().hex[:12]
    role = "insights_api_" + uuid.uuid4().hex[:12]
    tables = _tables()
    with admin.begin() as conn:
        # Identifiers are a fixed prefix plus UUID hex; never client input.
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        _in_schema(conn, schema)
        from core.models.base import BaseModel

        BaseModel.metadata.create_all(conn, tables=tables)
        with Operations.context(MigrationContext.configure(conn)):
            _migration().upgrade()
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

    factory = async_sessionmaker(runtime, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(database, "current_session_factory", lambda: factory)
    monkeypatch.setattr("api.v1.insights.insights_enabled_for", AsyncMock(return_value=True))
    try:
        yield World(admin, schema)
    finally:
        await runtime.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
            conn.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')
        admin.dispose()


def _request(role: str, *, domains: list[str] | None, admin: bool = False) -> SimpleNamespace:
    scopes = ["agenticorg:admin"] if admin else ["insights:read"]
    claims = {
        "sub": f"{role}@example.test",
        "role": role,
        "agenticorg:user_id": str(uuid.uuid4()),
        "agenticorg:domains": domains,
        "grantex:scopes": scopes,
    }
    return SimpleNamespace(state=SimpleNamespace(claims=claims, scopes=scopes, auth_mode=None))


ADMIN = _request("admin", domains=None, admin=True)
CFO = _request("cfo", domains=["finance"])


def _seed(world: World) -> SimpleNamespace:
    from zoneinfo import ZoneInfo

    from core.models.agent import Agent, AgentCostLedger
    from core.models.budget_alert import BudgetAlert
    from core.models.company import Company
    from core.models.hitl import HITLQueue
    from core.models.insights import RunFact
    from core.models.tenant import Tenant

    now = datetime.now(UTC)
    zone = ZoneInfo("Asia/Kolkata")
    today = local_today(zone, now)
    yesterday = today - timedelta(days=1)
    t_start = day_bounds(today, zone)[0] + timedelta(minutes=1)
    y_start = day_bounds(yesterday, zone)[0] + timedelta(hours=1)
    a, b = uuid.uuid4(), uuid.uuid4()
    company_a, company_b = uuid.uuid4(), uuid.uuid4()
    world.add(
        Tenant(id=a, name="Synthetic insights A", slug="insights-a-" + a.hex[:8], data_region="IN", settings={}),
        Tenant(id=b, name="Synthetic insights B", slug="insights-b-" + b.hex[:8], data_region="IN", settings={}),
    )
    world.add(
        Company(id=company_a, tenant_id=a, name="Acme Books", pan="AAAPA1234A"),
        Company(id=company_b, tenant_id=b, name="Nimbus Traders", pan="BBBPB1234B"),
    )
    agent = {"system_prompt_ref": "prompts/synthetic.txt", "hitl_condition": "confidence < 0.8"}
    a_fin, a_hr, b_fin = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    world.add(
        Agent(id=a_fin, tenant_id=a, name="Invoice matcher", agent_type="ap_processor", domain="finance",
              status="active", **agent),
        Agent(id=a_hr, tenant_id=a, name="Leave checker", agent_type="leave_bot", domain="hr", status="paused",
              updated_at=now - timedelta(days=10), **agent),
        Agent(id=b_fin, tenant_id=b, name="Tenant B agent", agent_type="ap_processor", domain="finance",
              status="active", **agent),
    )

    def fact(tenant, agent_id, domain, started, status="completed", company=None, ms=1200):
        return RunFact(
            tenant_id=tenant, run_key="agents:" + uuid.uuid4().hex, entrypoint="agents", agent_id=agent_id,
            agent_type="ap_processor" if domain == "finance" else "leave_bot", domain=domain, status=status,
            company_id=company, duration_ms=ms, tokens_in=100, tokens_out=50, tokens_total=150,
            cost_usd=Decimal("0.01"), started_at=started,
        )

    world.add(
        fact(a, a_fin, "finance", t_start, company=company_a),
        fact(a, a_fin, "finance", t_start + timedelta(minutes=1), company=company_a),
        fact(a, a_fin, "finance", t_start + timedelta(minutes=2), status="failed", company=company_a, ms=9000),
        fact(a, a_hr, "hr", t_start + timedelta(minutes=3)),
        fact(a, a_fin, "finance", y_start),
        fact(a, a_fin, "finance", y_start + timedelta(minutes=5)),
        *(fact(b, b_fin, "finance", t_start + timedelta(minutes=i)) for i in range(5)),
    )
    common = {
        "trigger_type": "low_confidence",
        "assignee_role": "cfo",
        "decision_options": {"options": ["approve"]},
        "context": {},
        "expires_at": now + timedelta(days=2),
    }
    world.add(
        HITLQueue(tenant_id=a, agent_id=a_fin, title="Approve invoice INV-1", created_at=now - timedelta(hours=30),
                  **common),
        HITLQueue(tenant_id=b, agent_id=b_fin, title="Tenant B approval 1", **common),
        HITLQueue(tenant_id=b, agent_id=b_fin, title="Tenant B approval 2", **common),
    )
    # Ledger days are naive UTC dates; the tenant's local today is inside every period the test reads.
    midnight = datetime(today.year, today.month, today.day)  # noqa: DTZ001 - the ledger is naive UTC
    world.add(
        AgentCostLedger(tenant_id=a, agent_id=a_fin, period_date=midnight, cost_usd=Decimal("1.5"), token_count=10),
        AgentCostLedger(tenant_id=a, agent_id=a_hr, period_date=midnight - timedelta(days=1),
                        cost_usd=Decimal("0.5"), token_count=10),
        AgentCostLedger(tenant_id=b, agent_id=b_fin, period_date=midnight, cost_usd=Decimal("10"), token_count=10),
        BudgetAlert(tenant_id=a, name="Monthly AI budget", period="monthly", threshold_usd=Decimal("1.00"),
                    warn_at_percent=80),
    )
    return SimpleNamespace(a=a, b=b, company_a=company_a, company_b=company_b, today=today, yesterday=yesterday)


def _by_key(view: dict) -> dict:
    return {m["key"]: m for m in view["tiles"] + view["charts"]}


@pytest.mark.asyncio
async def test_views_metrics_and_exports_keep_to_the_tenant(world, monkeypatch) -> None:
    from api.v1 import insights as api
    from core.config import settings
    from core.insights import rollup

    monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
    seed = _seed(world)
    for tenant in (seed.a, seed.b):
        await rollup.rollup_day(tenant, seed.yesterday)

    view_a = _by_key(await api.insights_view("executive", ADMIN, tenant_id=str(seed.a)))
    volume = view_a["runs.volume"]
    assert volume["value"] == 6 and volume["data_quality"] == "measured"
    assert volume["source"] == "insights_daily+run_facts"
    series = {p["t"]: p["value"] for p in volume["series"]}
    assert series[seed.today.isoformat()] == 4 and series[seed.yesterday.isoformat()] == 2
    assert view_a["runs.success_rate"]["value"] == pytest.approx(83.3)
    assert view_a["agents.by_status"]["value"] == 2
    assert view_a["approvals.pending"]["value"] == 1
    assert [r["title"] for r in view_a["approvals.pending"]["rows"]] == ["Approve invoice INV-1"]
    assert view_a["runs.latency"]["value"] == 10000.0  # 1 of 6 runs took 9 s: p95 is its bucket's bound

    view_b = _by_key(await api.insights_view("executive", ADMIN, tenant_id=str(seed.b)))
    assert view_b["runs.volume"]["value"] == 5
    assert view_b["agents.by_status"]["value"] == 1
    assert view_b["approvals.pending"]["value"] == 2
    assert view_b["cost.spend"]["value"] == 10.0

    full = await api.insights_view("executive", ADMIN, tenant_id=str(seed.a))
    kinds = {(item["kind"], item["title"]) for item in full["needs_you"]}
    assert ("approval", "Approve invoice INV-1") in kinds
    assert ("budget", "Monthly AI budget") in kinds
    assert ("agent_paused", "Leave checker") in kinds
    assert not any("Tenant B" in title for _, title in kinds)

    # A CFO's run metrics keep to the finance domain; cost stays tenant-wide.
    finance = _by_key(await api.insights_view("finance", CFO, tenant_id=str(seed.a)))
    assert finance["runs.volume"]["value"] == 5
    assert finance["cost.spend"]["value"] == 2.0
    assert finance["cost.per_run"]["value"] == pytest.approx(2.0 / 6, abs=1e-6)  # all of tenant A's runs
    budgets = finance["cost.budgets"]
    assert budgets["state"] == "act" and budgets["rows"][0]["state"] == "breached"

    # Company filter: the tenant's own company narrows; another tenant's is refused.
    narrowed = await api.insights_metric("runs.volume", ADMIN, tenant_id=str(seed.a), company_id=str(seed.company_a))
    assert narrowed["value"] == 3
    with pytest.raises(HTTPException) as refused:
        await api.insights_metric("runs.volume", ADMIN, tenant_id=str(seed.a), company_id=str(seed.company_b))
    assert refused.value.status_code == 422

    grouped = await api.insights_metric("runs.volume", ADMIN, tenant_id=str(seed.a), group_by="agent")
    assert {r["group"]: r["value"] for r in grouped["rows"]} == {"Invoice matcher": 5, "Leave checker": 1}

    # Export: tenant A's rows only, and one signed audit row in tenant A.
    exported = await api.insights_export("runs.volume", ADMIN, tenant_id=str(seed.a), format="csv")
    rows = list(csv.DictReader(io.StringIO(exported.body.decode("utf-8"))))
    assert sum(int(r["value"]) for r in rows) == 6
    assert world.scalar(
        "SELECT count(*) FROM audit_log WHERE tenant_id = :t AND event_type = 'insights.export'", t=seed.a
    ) == 1
    assert world.scalar("SELECT count(*) FROM audit_log WHERE tenant_id = :t", t=seed.b) == 0
    entry = world.scalar("SELECT details FROM audit_log WHERE tenant_id = :t", t=seed.a)
    assert entry["metric"] == "runs.volume" and entry["row_count"] == len(rows)


@pytest.mark.asyncio
async def test_cost_source_falls_back_to_the_ledger_and_converts_currency(world, monkeypatch) -> None:
    from api.v1 import insights as api
    from core.config import settings
    from core.models.spend import SpendFxRate
    from core.models.spend_usage import SpendUsageRollup

    monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
    seed = _seed(world)

    spend = await api.insights_metric("cost.spend", CFO, tenant_id=str(seed.a))
    assert spend["source"] == "agent_cost_ledger" and spend["currency"] == "USD" and spend["value"] == 2.0
    assert "No USD to INR rate" in spend["hint"]
    by_model = await api.insights_metric("cost.by_model", CFO, tenant_id=str(seed.a))
    assert by_model["data_quality"] == "no_data" and "AI spend metering" in by_model["hint"]

    world.add(SpendFxRate(tenant_id=seed.a, rate_date=seed.yesterday, currency="USD", rate_to_inr=Decimal("80")))
    converted = await api.insights_metric("cost.spend", CFO, tenant_id=str(seed.a))
    assert converted["currency"] == "INR" and converted["unit"] == "inr" and converted["value"] == 160.0
    assert converted["conversion"]["kind"] == "fx_converted" and "hint" not in converted

    # Metering on but no rollup rows yet: still the ledger.
    monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
    assert (await api.insights_metric("cost.spend", CFO, tenant_id=str(seed.a)))["source"] == "agent_cost_ledger"

    world.add(
        SpendUsageRollup(
            tenant_id=seed.a, day=seed.today, dims_hash="a" * 64, billing_date=seed.today, application="agents",
            provider="gemini", model="gemini-2.5-flash", usage_type="llm_tokens", unit="input_token",
            price_source="list", quantity=Decimal("1000"), amount=Decimal("6"), amount_inr=Decimal("500"),
            record_count=4, call_count=4, unpriced_count=1,
        )
    )
    metered = await api.insights_metric("cost.spend", CFO, tenant_id=str(seed.a))
    assert metered["source"] == "spend_usage_rollups" and metered["currency"] == "INR" and metered["value"] == 500.0
    models = await api.insights_metric("cost.by_model", CFO, tenant_id=str(seed.a))
    assert models["rows"] == [{"provider": "gemini", "model": "gemini-2.5-flash", "value": 500.0}]
    quality = await api.insights_metric("cost.data_quality", CFO, tenant_id=str(seed.a))
    assert quality["value"] == 75.0 and quality["data_quality"] == "measured"
    tokens = await api.insights_metric("tokens.by_unit", CFO, tenant_id=str(seed.a))
    assert tokens["source"] == "spend_usage_rollups" and tokens["rows"] == [{"unit": "input_token", "value": 1000}]

    # Tenant B has no rollup rows: its cost stays on its own ledger.
    other = await api.insights_metric("cost.spend", CFO, tenant_id=str(seed.b))
    assert other["source"] == "agent_cost_ledger" and other["value"] == 10.0 and other["currency"] == "USD"
    forecast = await api.insights_metric("cost.forecast", CFO, tenant_id=str(seed.b))
    assert forecast["data_quality"] == "estimated" and forecast["rows"][0]["actual"] == 10.0
    assert forecast["value"] >= 10.0
