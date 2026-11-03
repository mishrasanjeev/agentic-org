# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights explorers and run evidence against real Postgres, as a role without BYPASSRLS.

A scratch schema holds the tables the explorers read (built from the ORM
models), the v6z106 migration adds the insights tables with their partitions,
and every tenant-scoped table gets the forced row-level security policy
production uses. The handlers run through the real ``get_tenant_session`` on
an engine that logs in as a NOBYPASSRLS role.

Two tenants each have agents, runs, approvals, feedback, connectors, workflow
runs and audit rows. Tenant A's administrator sees only tenant A's rows in
every explorer; a CFO and a CHRO see their own domain's agents, runs and
approvals; the CHRO is refused the workflow and audit explorers; the cost
explorer is tenant-wide for the CFO and limited to HR agents for the CHRO.
Filters, sorting and paging run in SQL for runs and in memory for agents; an
export carries every filtered row and writes one signed audit row; run
evidence finds the run's gateway records, spans, approval and audit row, and
answers 404 to another tenant and to a role outside the run's domain.
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
    "agent_feedback",
    "connectors",
    "connector_configs",
    "workflow_definitions",
    "workflow_runs",
    "model_gateway_records",
    "run_spans",
    # Exports read the tenant's PII masking setting (v6z110 release).
    "governance_config",
)

pytestmark = [
    pytest.mark.skipif(not os.getenv("AGENTICORG_DB_URL"), reason="Requires the disposable Docker Postgres database"),
    pytest.mark.timeout(300),
]


def _sync_url() -> str:
    return os.environ["AGENTICORG_DB_URL"].replace("+asyncpg", "")


def _migration():
    spec = importlib.util.spec_from_file_location("_v6_z106_insights_foundation_explore", MIGRATION)
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
    schema = "insights_explore_" + uuid.uuid4().hex[:12]
    role = "insights_explore_" + uuid.uuid4().hex[:12]
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
    from core.config import settings

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


def _request(role: str, *, domains: list[str] | None, admin: bool = False, query: dict | None = None):
    scopes = ["agenticorg:admin"] if admin else ["insights:read"]
    claims = {
        "sub": f"{role}@example.test",
        "role": role,
        "agenticorg:user_id": str(uuid.uuid4()),
        "agenticorg:domains": domains,
        "grantex:scopes": scopes,
    }
    return SimpleNamespace(
        state=SimpleNamespace(claims=claims, scopes=scopes, auth_mode=None), query_params=dict(query or {})
    )


def admin_req(**query):
    return _request("admin", domains=None, admin=True, query=query)


def cfo_req(**query):
    return _request("cfo", domains=["finance"], query=query)


def chro_req(**query):
    return _request("chro", domains=["hr"], query=query)


def _seed(world: World) -> SimpleNamespace:
    from core.models.agent import Agent, AgentCostLedger
    from core.models.audit import AuditLog
    from core.models.company import Company
    from core.models.connector import Connector
    from core.models.connector_config import ConnectorConfig
    from core.models.feedback import AgentFeedback
    from core.models.hitl import HITLQueue
    from core.models.insights import RunFact
    from core.models.model_gateway_record import ModelGatewayRecord
    from core.models.run_span import RunSpan
    from core.models.tenant import Tenant
    from core.models.user import User
    from core.models.workflow import WorkflowDefinition, WorkflowRun

    now = datetime.now(UTC)
    today = local_today(KOLKATA, now)
    start = day_bounds(today, KOLKATA)[0] + timedelta(minutes=1)
    a, b = uuid.uuid4(), uuid.uuid4()
    world.add(
        Tenant(id=a, name="Synthetic explore A", slug="explore-a-" + a.hex, data_region="IN", settings={}),
        Tenant(id=b, name="Synthetic explore B", slug="explore-b-" + b.hex, data_region="IN", settings={}),
    )
    company_a = uuid.uuid4()
    world.add(Company(id=company_a, tenant_id=a, name="Acme Books", pan="AAAPA1234A"))
    reviewer = uuid.uuid4()
    world.add(User(id=reviewer, tenant_id=a, email="reviewer@example.test", name="Asha Reviewer", role="cfo"))
    base = {"system_prompt_ref": "prompts/synthetic.txt", "hitl_condition": "confidence < 0.8"}
    a_fin, a_hr, a_shadow, b_fin = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    world.add(
        Agent(
            id=a_fin,
            tenant_id=a,
            name="Invoice matcher",
            agent_type="ap_processor",
            domain="finance",
            status="active",
            prompt_amendments=["Always quote the PO number", "Use INR"],
            **base,
        ),
        Agent(id=a_hr, tenant_id=a, name="Leave checker", agent_type="leave_bot", domain="hr", status="active", **base),
        Agent(
            id=a_shadow,
            tenant_id=a,
            name="Shadow screener",
            agent_type="talent_acquisition",
            domain="hr",
            status="shadow",
            shadow_min_samples=10,
            shadow_sample_count=4,
            shadow_scored_sample_count=4,
            **base,
        ),
        Agent(
            tenant_id=a,
            name="Dormant payroll",
            agent_type="payroll_engine",
            domain="hr",
            status="active",
            created_at=now - timedelta(days=200),
            **base,
        ),
        Agent(
            id=b_fin,
            tenant_id=b,
            name="Tenant B agent",
            agent_type="ap_processor",
            domain="finance",
            status="active",
            **base,
        ),
    )
    correlation = "corr-" + uuid.uuid4().hex
    trace = uuid.uuid4().hex
    traced = uuid.uuid4()

    def fact(tenant, agent_id, domain, started, **extra):
        values = {
            "tenant_id": tenant,
            "run_key": "agents:" + uuid.uuid4().hex,
            "entrypoint": "agents",
            "agent_id": agent_id,
            "agent_type": "ap_processor" if domain == "finance" else "leave_bot",
            "domain": domain,
            "status": "completed",
            "duration_ms": 1200,
            "tokens_in": 100,
            "tokens_out": 50,
            "tokens_total": 150,
            "cost_usd": Decimal("0.01"),
            "started_at": started,
        }
        values.update(extra)
        return RunFact(**values)

    world.add(
        fact(a, a_fin, "finance", start, id=traced, correlation_id=correlation, trace_id=trace, company_id=company_a),
        fact(a, a_fin, "finance", start + timedelta(minutes=1)),
        fact(
            a,
            a_fin,
            "finance",
            start + timedelta(minutes=2),
            status="failed",
            duration_ms=9000,
            error_class="TimeoutError",
        ),
        fact(a, a_hr, "hr", start + timedelta(minutes=3)),
        *(fact(b, b_fin, "finance", start + timedelta(minutes=i)) for i in range(4)),
    )
    common = {
        "trigger_type": "low_confidence",
        "decision_options": {"options": ["approve"]},
        "expires_at": now + timedelta(days=2),
    }
    world.add(
        HITLQueue(
            tenant_id=a,
            agent_id=a_fin,
            title="Approve invoice INV-1",
            assignee_role="finance",
            context={"correlation_id": correlation},
            status="decided",
            decision="approve",
            decision_by=reviewer,
            decision_at=start + timedelta(minutes=30),
            created_at=start,
            **common,
        ),
        HITLQueue(
            tenant_id=a,
            agent_id=a_hr,
            title="Approve leave L-7",
            assignee_role="hr",
            context={},
            created_at=start + timedelta(minutes=5),
            **common,
        ),
        HITLQueue(
            tenant_id=b,
            agent_id=b_fin,
            title="Tenant B approval",
            assignee_role="finance",
            context={},
            created_at=start,
            **common,
        ),
    )
    world.add(
        ModelGatewayRecord(
            tenant_id=a,
            correlation_id=correlation,
            use_case="agent_run",
            provider="gemini",
            model="gemini-2.5-flash",
            outcome="ok",
            latency_ms=800,
            tokens=150,
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
            created_at=start + timedelta(seconds=1),
        ),
        ModelGatewayRecord(
            tenant_id=b,
            correlation_id=correlation,
            use_case="agent_run",
            provider="gemini",
            model="gemini-2.5-flash",
            outcome="ok",
            latency_ms=800,
            tokens=1,
            cost_usd=0.0,
        ),
        RunSpan(
            tenant_id=a,
            trace_id=trace,
            run_span_id=uuid.uuid4().hex[:16],
            span_id=uuid.uuid4().hex[:16],
            name="llm.call",
            kind="llm",
            status="ok",
            started_at=start,
            duration_ms=800,
        ),
        AuditLog(
            tenant_id=a,
            event_type="agent.run",
            actor_type="agent",
            actor_id=str(a_fin),
            action="run",
            outcome="success",
            trace_id=trace,
            created_at=start + timedelta(seconds=2),
        ),
        AuditLog(
            tenant_id=a,
            event_type="hitl.decided",
            actor_type="user",
            actor_id=str(reviewer),
            action="approve",
            outcome="success",
            created_at=start + timedelta(minutes=30),
        ),
        AuditLog(
            tenant_id=b,
            event_type="hitl.decided",
            actor_type="user",
            actor_id="someone",
            action="approve",
            outcome="success",
            created_at=start,
        ),
    )
    world.add(
        AgentFeedback(tenant_id=a, agent_id=a_fin, run_id="r1", feedback_type="thumbs_down", created_at=start),
        AgentFeedback(tenant_id=a, agent_id=a_fin, run_id="r2", feedback_type="correction", created_at=start),
        AgentFeedback(tenant_id=b, agent_id=b_fin, run_id="r3", feedback_type="thumbs_down", created_at=start),
    )
    conn_a = uuid.uuid4()
    world.add(
        Connector(id=conn_a, tenant_id=a, name="tally", category="accounting", auth_type="api_key"),
        Connector(tenant_id=b, name="zoho", category="accounting", auth_type="oauth2"),
        ConnectorConfig(tenant_id=a, connector_name="tally", company_id=company_a, health_status="unhealthy"),
    )
    world.scalar(
        "UPDATE agents SET connector_ids = CAST(:ids AS JSONB) WHERE id = :id RETURNING 1",
        ids=f'["{conn_a}"]',
        id=a_fin,
    )
    wf_a = uuid.uuid4()
    world.add(
        WorkflowDefinition(id=wf_a, tenant_id=a, name="Invoice to pay", domain="finance", definition={"steps": []}),
    )
    world.add(
        WorkflowRun(
            tenant_id=a,
            workflow_def_id=wf_a,
            status="completed",
            steps_total=3,
            steps_completed=3,
            started_at=start,
            completed_at=start + timedelta(minutes=4),
        ),
    )
    midnight = datetime(today.year, today.month, today.day)  # noqa: DTZ001 - the ledger is naive UTC
    world.add(
        AgentCostLedger(tenant_id=a, agent_id=a_fin, period_date=midnight, cost_usd=Decimal("3.0"), token_count=10),
        AgentCostLedger(tenant_id=a, agent_id=a_hr, period_date=midnight, cost_usd=Decimal("1.0"), token_count=10),
        AgentCostLedger(tenant_id=b, agent_id=b_fin, period_date=midnight, cost_usd=Decimal("9.0"), token_count=10),
    )
    return SimpleNamespace(
        a=a, b=b, a_fin=a_fin, a_hr=a_hr, a_shadow=a_shadow, traced=traced, start=start, company_a=company_a
    )


def _names(page: dict, column: str = "agent") -> set:
    return {row[column] for row in page["rows"]}


@pytest.mark.asyncio
async def test_explorers_keep_to_the_tenant_and_the_callers_domain(world) -> None:
    from api.v1 import insights_explore as api

    seed = _seed(world)
    a = str(seed.a)

    agents = await api.insights_explore("agents", admin_req(), tenant_id=a)
    assert _names(agents) == {"Invoice matcher", "Leave checker", "Shadow screener", "Dormant payroll"}
    by_name = {r["agent"]: r for r in agents["rows"]}
    matcher = by_name["Invoice matcher"]
    assert matcher["runs"] == 3 and matcher["success_rate"] == pytest.approx(66.7)
    assert matcher["spend"] == 3.0 and matcher["learned_rules"] == 2 and matcher["idle_days"] == 0
    shadow = by_name["Shadow screener"]
    assert shadow["promotion_eligible"] is False and "4/10 scored samples" in shadow["promotion_reason"]
    assert by_name["Leave checker"]["promotion_eligible"] is None
    dormant = by_name["Dormant payroll"]
    assert dormant["idle_days"] == 90 and dormant["last_run_at"] is None and dormant["runs"] == 0

    runs = await api.insights_explore("runs", admin_req(), tenant_id=a)
    assert runs["total"] == 4 and "Tenant B agent" not in _names(runs)
    approvals = await api.insights_explore("approvals", admin_req(), tenant_id=a)
    assert _names(approvals, "title") == {"Approve invoice INV-1", "Approve leave L-7"}
    decided = next(r for r in approvals["rows"] if r["title"] == "Approve invoice INV-1")
    assert decided["decided_by"] == "Asha Reviewer" and decided["cycle_time_ms"] == pytest.approx(30 * 60 * 1000)
    quality = await api.insights_explore("quality", admin_req(), tenant_id=a)
    assert [(r["rule_number"], r["rule"]) for r in quality["rows"]] == [
        (1, "Always quote the PO number"),
        (2, "Use INR"),
    ]
    assert quality["rows"][0]["actions"] == {"revoke": {"agent_id": str(seed.a_fin), "index": 0}}
    assert all(r["negative_feedback"] == 2 for r in quality["rows"])
    assert quality["summary"]["feedback_by_type"] == {"correction": 1, "thumbs_down": 1}
    connectors = await api.insights_explore("connectors", admin_req(), tenant_id=a)
    assert [(r["connector"], r["health"], r["dependent_agents"]) for r in connectors["rows"]] == [
        ("tally", "unhealthy", 1)
    ]
    workflows = await api.insights_explore("workflows", admin_req(), tenant_id=a)
    assert workflows["total"] == 1 and workflows["rows"][0]["duration_ms"] == pytest.approx(240_000)
    audit = await api.insights_explore("audit", admin_req(), tenant_id=a)
    assert audit["total"] == 2 and _names(audit, "actor") >= {"Asha Reviewer"}

    # The CFO sees finance; the CHRO sees HR, and neither the workflow nor the audit explorer.
    cfo_agents = await api.insights_explore("agents", cfo_req(), tenant_id=a)
    assert _names(cfo_agents) == {"Invoice matcher"}
    chro_runs = await api.insights_explore("runs", chro_req(), tenant_id=a)
    assert chro_runs["total"] == 1 and chro_runs["rows"][0]["agent"] == "Leave checker"
    chro_approvals = await api.insights_explore("approvals", chro_req(), tenant_id=a)
    assert _names(chro_approvals, "title") == {"Approve leave L-7"}
    for key in ("workflows", "audit", "connectors"):
        with pytest.raises(HTTPException) as refused:
            await api.insights_explore(key, chro_req(), tenant_id=a)
        assert refused.value.status_code == 403
    listed = await api.insights_explorers(chro_req(), tenant_id=a)
    assert [e["key"] for e in listed["explorers"]] == ["agents", "runs", "approvals", "cost", "quality"]

    # Cost: tenant-wide for the CFO, HR agents only for the CHRO.
    cfo_cost = await api.insights_explore("cost", cfo_req(), tenant_id=a)
    assert {r["agent"]: r["spend"] for r in cfo_cost["rows"]} == {"Invoice matcher": 3.0, "Leave checker": 1.0}
    chro_cost = await api.insights_explore("cost", chro_req(), tenant_id=a)
    assert _names(chro_cost) == {"Leave checker"}
    first = cfo_cost["rows"][0]
    assert first["agent"] == "Invoice matcher" and first["prior_spend"] == 0.0 and first["change"] == 3.0

    # Tenant B's administrator sees none of tenant A's rows.
    b_agents = await api.insights_explore("agents", admin_req(), tenant_id=str(seed.b))
    assert _names(b_agents) == {"Tenant B agent"}
    b_audit = await api.insights_explore("audit", admin_req(), tenant_id=str(seed.b))
    assert b_audit["total"] == 1


@pytest.mark.asyncio
async def test_filters_sort_paging_and_export(world) -> None:
    from api.v1 import insights_explore as api

    seed = _seed(world)
    a = str(seed.a)
    failed = await api.insights_explore("runs", admin_req(**{"filter[status]": "failed"}), tenant_id=a)
    assert failed["total"] == 1 and failed["rows"][0]["error_class"] == "TimeoutError"
    assert failed["filters"] == {"status": "failed"}
    slowest = await api.insights_explore(
        "runs", admin_req(sort="duration_ms", order="desc", page_size="2"), tenant_id=a
    )
    assert slowest["total"] == 4 and len(slowest["rows"]) == 2 and slowest["rows"][0]["duration_ms"] == 9000
    second = await api.insights_explore("runs", admin_req(sort="duration_ms", page_size="2", page="2"), tenant_id=a)
    assert len(second["rows"]) == 2 and not {r["started_at"] for r in second["rows"]} & {
        r["started_at"] for r in slowest["rows"]
    }
    idle = await api.insights_explore(
        "agents", admin_req(**{"filter[idle_days]": "30", "filter[status]": "active"}), tenant_id=a
    )
    assert _names(idle) == {"Dormant payroll"}
    company = await api.insights_explore("runs", admin_req(), tenant_id=a, company_id=str(seed.company_a))
    assert company["total"] == 1
    for bad in ({"filter[nope]": "x"}, {"filter[status]": "exploded"}, {"sort": "agent"}, {"page_size": "500"}):
        with pytest.raises(HTTPException) as refused:
            await api.insights_explore("runs", admin_req(**bad), tenant_id=a)
        assert refused.value.status_code == 422

    exported = await api.insights_explore_export(
        "approvals", admin_req(**{"filter[status]": "decided"}), tenant_id=a, format="csv"
    )
    rows = list(csv.DictReader(io.StringIO(exported.body.decode("utf-8"))))
    assert [r["title"] for r in rows] == ["Approve invoice INV-1"]
    assert "links" not in rows[0] and rows[0]["decided_by"] == "Asha Reviewer"
    xlsx = await api.insights_explore_export("approvals", admin_req(), tenant_id=a, format="xlsx")
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(xlsx.body), read_only=True)
    assert book.sheetnames == ["Data", "Filters", "Definitions"]
    assert sum(1 for _ in book["Data"].iter_rows()) == 3  # header and two approvals
    # Read-only rows drop trailing empty cells.
    definitions = {
        row[0]: (row[1] if len(row) > 1 else None) for row in book["Definitions"].iter_rows(values_only=True)
    }
    assert definitions["key"] == "explore.approvals" and "column:cycle_time_ms" in definitions
    assert (
        world.scalar("SELECT count(*) FROM audit_log WHERE tenant_id = :t AND event_type = 'insights.export'", t=seed.a)
        == 2
    )
    entry = world.scalar(
        "SELECT details FROM audit_log WHERE tenant_id = :t AND event_type = 'insights.export' "
        "AND details->>'format' = 'csv'",
        t=seed.a,
    )
    assert entry["explorer"] == "approvals" and entry["row_count"] == 1
    assert entry["filters"]["filter[status]"] == "decided"
    assert (
        world.scalar("SELECT count(*) FROM audit_log WHERE tenant_id = :t AND event_type = 'insights.export'", t=seed.b)
        == 0
    )


@pytest.mark.asyncio
async def test_run_evidence_gathers_the_records_and_keeps_to_the_caller(world) -> None:
    from api.v1 import insights_explore as api

    seed = _seed(world)
    runs = await api.insights_explore("runs", admin_req(**{"filter[status]": "completed"}), tenant_id=str(seed.a))
    traced = next(r for r in runs["rows"] if r["links"]["run"].startswith(f"/dashboard/insights/runs/{seed.traced}"))
    at = traced["started_at"]
    evidence = await api.insights_run_evidence(seed.traced, admin_req(), tenant_id=str(seed.a), at=at)
    assert evidence["run"]["agent"] == "Invoice matcher" and evidence["run"]["tokens"]["total"] == 150
    assert [g["model"] for g in evidence["gateway_records"]] == ["gemini-2.5-flash"]
    assert [s["name"] for s in evidence["spans"]] == ["llm.call"]
    assert [i["title"] for i in evidence["approvals"]] == ["Approve invoice INV-1"]
    assert evidence["approvals"][0]["decided_by"] == "Asha Reviewer"
    assert [e["event_type"] for e in evidence["audit"]] == ["agent.run"]

    # An encoded "+" that arrived as a space still parses.
    spaced = at.replace("+", " ")
    again = await api.insights_run_evidence(seed.traced, admin_req(), tenant_id=str(seed.a), at=spaced)
    assert again["run"]["id"] == str(seed.traced)

    for request, tenant in ((admin_req(), seed.b), (chro_req(), seed.a)):
        with pytest.raises(HTTPException) as missing:
            await api.insights_run_evidence(seed.traced, request, tenant_id=str(tenant), at=at)
        assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as bad_at:
        await api.insights_run_evidence(seed.traced, admin_req(), tenant_id=str(seed.a), at="yesterday")
    assert bad_at.value.status_code == 422
