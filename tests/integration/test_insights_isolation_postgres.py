# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Tenant isolation across every Insights read and export path, against real Postgres without BYPASSRLS.

Two tenants, "Alpha" and "Bravo", get the same shape of data: agents, runs
(one failed, with a trace and a model gateway record), approvals, feedback,
connectors, a workflow run, cost-ledger rows and signed audit rows, each
named after its tenant. Then, as each tenant's administrator (and auditor),
every view, every metric (also grouped by agent where it can be), every
explorer and its export, the value summary, the alerts list, run evidence,
run bundles and background export jobs (metric, explorer, finance pack,
evidence pack) are read, and nothing from the other tenant (its names or its
record ids) appears in any response or file. The other tenant's runs, bundles
and export jobs answer 404, including download and sign-off.

The programme acceptance criterion this covers: "tenant-isolation tests pass
for every explorer and export endpoint, including under a NOBYPASSRLS role".
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import uuid
import zipfile
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
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from core.insights.metrics import day_bounds, local_today

KOLKATA = ZoneInfo("Asia/Kolkata")
VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
MIGRATIONS = (
    "v6_z106_insights_foundation.py",
    "v6_z110_insights_exports.py",
    # Saved views, last visits and the partner_pack export kind.
    "v6_z113_insights_saved_views.py",
)
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
    "governance_config",
    "user_delegations",
    "dsar_requests",
    "provider_residency_attestations",
    "prompt_edit_history",
    "prompt_template_edit_history",
    "audit_chain_anchors",
    "audit_chain_witness_checkpoints",
    "eval_datasets",
    "eval_runs",
    "value_baselines",
    "value_rates",
    "insights_alerts",
    "lead_pipeline",
    "content_drafts",
    "professional_tax_returns",
    "commerce_carts",
    "commerce_checkout_sessions",
    "commerce_orders",
    "commerce_payment_operations",
    "commerce_order_webhook_deliveries",
    "compliance_deadlines",
    "txn_findings",
    "cost_centers",
    "agent_lifecycle_events",
    "filing_approvals",
    "ca_client_invoices",
    "ca_client_payments",
    "tool_calls",
    "guardrail_events",
    "security_events_daily",
)

pytestmark = [
    pytest.mark.skipif(not os.getenv("AGENTICORG_DB_URL"), reason="Requires the disposable Docker Postgres database"),
    pytest.mark.timeout(600),
]


def _sync_url() -> str:
    return os.environ["AGENTICORG_DB_URL"].replace("+asyncpg", "")


def _migration(name: str):
    spec = importlib.util.spec_from_file_location("_isolation_" + name.removesuffix(".py"), VERSIONS / name)
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


@pytest.fixture
async def world(monkeypatch) -> AsyncIterator[World]:
    admin = create_engine(_sync_url())
    schema = "insights_iso_" + uuid.uuid4().hex[:12]
    role = "insights_iso_" + uuid.uuid4().hex[:12]
    tables = _tables()
    with admin.begin() as conn:
        # Identifiers are a fixed prefix plus UUID hex; never client input.
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        _in_schema(conn, schema)
        from core.models.base import BaseModel

        BaseModel.metadata.create_all(conn, tables=tables)
        with Operations.context(MigrationContext.configure(conn)):
            for name in MIGRATIONS:
                _migration(name).upgrade()
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
    monkeypatch.setattr(settings, "insights_export_bucket", "isolation-it")
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_STORAGE", "1")
    try:
        yield World(admin, schema)
    finally:
        await runtime.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
            conn.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')
        admin.dispose()


def _request(role: str, user_id: uuid.UUID, *, admin: bool = False, query: dict | None = None) -> SimpleNamespace:
    scopes = ["agenticorg:admin"] if admin else ["insights:read"]
    claims = {
        "sub": f"{role}@example.test",
        "role": role,
        "agenticorg:user_id": str(user_id),
        "agenticorg:domains": None,
        "grantex:scopes": scopes,
    }
    return SimpleNamespace(
        state=SimpleNamespace(claims=claims, scopes=scopes, auth_mode=None), query_params=dict(query or {})
    )


def _seed_tenant(world: World, label: str) -> SimpleNamespace:
    """One tenant whose every name carries ``label``; returns its ids."""
    from core.config import settings
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
    from core.tool_gateway.audit_logger import sign_audit_record

    now = datetime.now(UTC)
    today = local_today(KOLKATA, now)
    start = (day_bounds(today, KOLKATA)[0] + timedelta(minutes=1)).replace(microsecond=0)
    tid, company, admin_id, auditor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    slug = label.lower()
    world.add(Tenant(id=tid, name=f"{label} Holdings", slug=f"{slug}-{tid.hex}", data_region="IN", settings={}))
    world.add(
        Company(id=company, tenant_id=tid, name=f"{label} Books", pan="AAAPA1234A"),
        User(id=admin_id, tenant_id=tid, email=f"{slug}-admin-{tid.hex}@example.test",
             name=f"{label} Admin", role="admin"),
        User(id=auditor_id, tenant_id=tid, email=f"{slug}-auditor-{tid.hex}@example.test",
             name=f"{label} Auditor", role="auditor"),
    )
    base = {"system_prompt_ref": "prompts/synthetic.txt", "hitl_condition": "confidence < 0.8", "status": "active"}
    fin, hr = uuid.uuid4(), uuid.uuid4()
    world.add(
        Agent(id=fin, tenant_id=tid, name=f"{label} invoice agent", agent_type="ap_processor", domain="finance",
              **base),
        Agent(id=hr, tenant_id=tid, name=f"{label} leave agent", agent_type="leave_bot", domain="hr", **base),
    )
    trace, correlation, failed_run, ok_run = uuid.uuid4().hex, f"corr-{slug}-{uuid.uuid4().hex}", uuid.uuid4(), (
        uuid.uuid4()
    )

    def fact(run_id, agent_id, domain, at, **extra):
        values = {
            "id": run_id,
            "tenant_id": tid,
            "run_key": f"agents:{slug}:{uuid.uuid4().hex}",
            "entrypoint": "agents",
            "agent_id": agent_id,
            "agent_type": "ap_processor" if domain == "finance" else "leave_bot",
            "domain": domain,
            "status": "completed",
            "duration_ms": 1500,
            "tokens_in": 100,
            "tokens_out": 50,
            "tokens_total": 150,
            "cost_usd": Decimal("0.02"),
            "company_id": company,
            "started_at": at,
        }
        values.update(extra)
        return RunFact(**values)

    world.add(
        fact(ok_run, fin, "finance", start),
        fact(failed_run, fin, "finance", start + timedelta(minutes=1), status="failed", error_class="TimeoutError",
             trace_id=trace, correlation_id=correlation),
        fact(uuid.uuid4(), hr, "hr", start + timedelta(minutes=2)),
    )
    common = {"trigger_type": "low_confidence", "decision_options": {"options": ["approve"]},
              "expires_at": now + timedelta(days=2)}
    item = uuid.uuid4()
    world.add(
        HITLQueue(id=item, tenant_id=tid, agent_id=fin, title=f"{label} invoice approval", assignee_role="cfo",
                  context={"correlation_id": correlation}, status="decided", decision="approve",
                  decision_by=admin_id, decision_at=start + timedelta(minutes=5), created_at=start, **common),
        HITLQueue(tenant_id=tid, agent_id=hr, title=f"{label} leave approval", assignee_role="chro", context={},
                  created_at=start + timedelta(minutes=3), **common),
        ModelGatewayRecord(tenant_id=tid, correlation_id=correlation, use_case="agent_run", provider="gemini",
                           model="gemini-2.5-flash", outcome="error", latency_ms=900, tokens=10, cost_usd=0.001,
                           created_at=start + timedelta(minutes=1, seconds=1)),
        RunSpan(tenant_id=tid, trace_id=trace, run_span_id=uuid.uuid4().hex[:16], span_id=uuid.uuid4().hex[:16],
                name=f"{slug}.llm.call", kind="llm", status="error", started_at=start + timedelta(minutes=1),
                duration_ms=900),
        AgentFeedback(tenant_id=tid, agent_id=fin, run_id=f"{slug}-r1", feedback_type="correction",
                      created_at=start),
    )
    key = settings.secret_key.encode()
    rows = [
        AuditLog(tenant_id=tid, event_type="agent.run", actor_type="agent", actor_id=str(fin), action="run",
                 outcome="failure", trace_id=trace, details={"note": f"{label} run"},
                 created_at=start + timedelta(minutes=1)),
        AuditLog(tenant_id=tid, event_type="hitl.decided", actor_type="user", actor_id=str(admin_id),
                 action="approve", outcome="success", resource_type="hitl_item", resource_id=str(item),
                 details={"note": f"{label} decision"}, created_at=start + timedelta(minutes=5)),
        AuditLog(tenant_id=tid, event_type="agent.create", actor_type="user", actor_id=str(admin_id),
                 action="create", outcome="success", details={"name": f"{label} invoice agent"},
                 created_at=start + timedelta(minutes=6)),
    ]
    for row in rows:
        row.signature = sign_audit_record(row, key)
    world.add(*rows)
    connector = uuid.uuid4()
    workflow = uuid.uuid4()
    world.add(
        Connector(id=connector, tenant_id=tid, name=f"{slug}-ledger", category="accounting", auth_type="api_key"),
        ConnectorConfig(tenant_id=tid, connector_name=f"{slug}-ledger", company_id=company, health_status="unhealthy"),
        WorkflowDefinition(id=workflow, tenant_id=tid, name=f"{label} invoice to pay", domain="finance",
                           definition={"steps": []}),
    )
    world.add(
        WorkflowRun(tenant_id=tid, workflow_def_id=workflow, status="failed", steps_total=3, steps_completed=1,
                    started_at=start, completed_at=start + timedelta(minutes=4)),
    )
    from core.models.insights import GuardrailEvent, SecurityEventDaily
    from core.models.tool_call import ToolCall

    world.add(
        ToolCall(tenant_id=tid, agent_id=fin, tool_name=f"{slug}.post_invoice", connector_name=f"{slug}-ledger",
                 status="failed", error_code="E1001", latency_ms=250, called_at=start + timedelta(minutes=1)),
        GuardrailEvent(tenant_id=tid, day=now.date(), stage="input", detector=f"{slug}_pii", action="mask",
                       mode="enforced", count=3),
        SecurityEventDaily(tenant_id=tid, day=now.date(), event_kind="login_failed", count=2),
    )
    midnight = datetime(today.year, today.month, today.day)  # noqa: DTZ001 - the ledger is naive UTC
    world.add(
        AgentCostLedger(tenant_id=tid, agent_id=fin, period_date=midnight, cost_usd=Decimal("2.0"), token_count=10),
        AgentCostLedger(tenant_id=tid, agent_id=hr, period_date=midnight, cost_usd=Decimal("1.0"), token_count=10),
    )
    return SimpleNamespace(
        label=label,
        tid=str(tid),
        today=today.isoformat(),
        ids={str(x) for x in (tid, company, admin_id, auditor_id, fin, hr, failed_run, ok_run, item, connector,
                              workflow)},
        failed_run=failed_run,
        failed_at=(start + timedelta(minutes=1)).isoformat(),
        admin=_request("admin", admin_id, admin=True),
        auditor=_request("auditor", auditor_id),
    )


def _leaks(payload: object, other: SimpleNamespace) -> list[str]:
    """The other tenant's names or ids found in ``payload`` (a response or decoded file text)."""
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    found = [other.label] if other.label in text else []
    found += [i for i in other.ids if i in text]
    return found


def _file_texts(name: str, content: bytes) -> list[tuple[str, str]]:
    """Every readable text inside an export: CSV/JSON as is, XLSX and ZIP entries opened (PDF streams skipped)."""
    if name.endswith(".pdf") or content[:5] == b"%PDF-":
        return []
    if content[:2] == b"PK":
        out: list[tuple[str, str]] = []
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for entry in archive.namelist():
                # A workbook's theme, styles and properties are formatting, not data.
                if entry.startswith(("xl/theme/", "docProps/")) or entry in ("xl/styles.xml", "[Content_Types].xml"):
                    continue
                out.extend(_file_texts(f"{name}/{entry}", archive.read(entry)))
        return out
    return [(name, content.decode("utf-8", errors="replace"))]


async def _sweep(own: SimpleNamespace, other: SimpleNamespace) -> list[str]:
    """Read every Insights surface as ``own``'s administrator and auditor; return what leaked from ``other``."""
    from api.v1 import insights as api
    from api.v1 import insights_alerts, insights_value
    from api.v1 import insights_explore as explore
    from core.insights import explore_registry, registry

    leaks: list[str] = []

    def check(where: str, payload: object) -> None:
        leaks.extend(f"{where}: {item}" for item in _leaks(payload, other))

    check("status", await api.insights_status(own.admin, tenant_id=own.tid))
    check("catalog", await api.insights_catalog(own.admin, tenant_id=own.tid))
    for view in registry.allowed_views("admin", is_admin=True):
        check(f"view {view}", await api.insights_view(view, own.admin, tenant_id=own.tid))
    for key, metric in registry.METRICS.items():
        check(f"metric {key}", await api.insights_metric(key, own.admin, tenant_id=own.tid))
        if "agent" in metric.group_by:
            check(f"metric {key} by agent", await api.insights_metric(key, own.admin, tenant_id=own.tid,
                                                                     group_by="agent"))
        exported = await api.insights_export(key, own.admin, tenant_id=own.tid, format="csv")
        for name, body in _file_texts(f"{key}.csv", exported.body):
            check(f"metric export {name}", body)
    for key in explore_registry.EXPLORERS:
        page = await explore.insights_explore(key, own.admin, tenant_id=own.tid)
        check(f"explorer {key}", page)
        if key in ("agents", "approvals", "connectors", "workflows"):
            # Not vacuous: the tenant's own rows are there.
            assert own.label in json.dumps(page, default=str) or own.label.lower() in json.dumps(page), (own.label, key)
        for fmt in ("csv", "xlsx", "json"):
            exported = await explore.insights_explore_export(key, own.admin, tenant_id=own.tid, format=fmt)
            for name, body in _file_texts(f"{key}.{fmt}", exported.body):
                check(f"explorer export {name}", body)
    check("value", await insights_value.get_value(own.admin, tenant_id=own.tid))
    check("alerts", await insights_alerts.list_alerts(own.admin, tenant_id=own.tid))
    check("run evidence", await explore.insights_run_evidence(own.failed_run, own.admin, tenant_id=own.tid,
                                                              at=own.failed_at))
    bundle = await explore.insights_run_bundle(own.failed_run, own.auditor, tenant_id=own.tid, at=own.failed_at)
    check("run bundle", bundle.body.decode("utf-8"))
    return leaks


async def _jobs(own: SimpleNamespace) -> dict[str, str]:
    """Ask for and build one job of each kind as ``own``; returns kind -> job id."""
    from api.v1 import insights_exports as api
    from core.insights import export_jobs

    period = {"from": own.today, "to": own.today}
    asks = {
        "metric": (own.admin, api.ExportRequest(kind="metric", format="json",
                                                params={"key": "runs.volume", "group_by": ["agent", "status"]})),
        "explorer": (own.admin, api.ExportRequest(kind="explorer", format="xlsx", params={"key": "agents"})),
        "finance_pack": (own.admin, api.ExportRequest(kind="finance_pack", format="zip", params=period)),
        "evidence_pack": (own.auditor, api.ExportRequest(kind="evidence_pack", format="zip", params=period)),
    }
    ids: dict[str, str] = {}
    for kind, (request, body) in asks.items():
        job = (await api.create_export(body, request, tenant_id=own.tid))["export"]
        status = await export_jobs.build(uuid.UUID(own.tid), uuid.UUID(job["id"]))
        assert status == "ready", (own.label, kind, (await api.get_export(request, uuid.UUID(job["id"]),
                                                                          tenant_id=own.tid))["export"]["error"])
        ids[kind] = job["id"]
    return ids


@pytest.mark.asyncio
async def test_every_insights_surface_keeps_to_its_tenant(world, monkeypatch) -> None:
    from api.v1 import insights_explore as explore
    from api.v1 import insights_exports as exports_api

    monkeypatch.setattr(exports_api, "_enqueue", lambda _tid, _job: True)
    alpha = _seed_tenant(world, "Alpha")
    bravo = _seed_tenant(world, "Bravo")

    # Every view, metric (and its export), explorer (and each export format), value, alerts and run evidence.
    for own, other in ((alpha, bravo), (bravo, alpha)):
        assert await _sweep(own, other) == [], own.label

    # The other tenant's run is not found, as evidence or as a bundle.
    for own, other in ((alpha, bravo), (bravo, alpha)):
        for call in (explore.insights_run_evidence, explore.insights_run_bundle):
            with pytest.raises(HTTPException) as missing:
                await call(other.failed_run, own.auditor, tenant_id=own.tid, at=other.failed_at)
            assert missing.value.status_code == 404, (own.label, call.__name__)

    # Background jobs of every kind: each tenant's files hold only its own rows.
    jobs = {alpha.label: await _jobs(alpha), bravo.label: await _jobs(bravo)}
    for own, other in ((alpha, bravo), (bravo, alpha)):
        for request in (own.admin, own.auditor):
            listed = (await exports_api.list_exports(request, tenant_id=own.tid))["exports"]
            assert {job["id"] for job in listed} <= set(jobs[own.label].values()), own.label
            assert _leaks(listed, other) == []
        for kind, job_id in jobs[own.label].items():
            request = own.auditor if kind == "evidence_pack" else own.admin
            response = await exports_api.download_export(request, uuid.UUID(job_id), tenant_id=own.tid)
            for name, body in _file_texts(kind, response.body):
                assert _leaks(body, other) == [], (own.label, kind, name)
        # The other tenant's jobs: not found to read, download or sign.
        for kind, job_id in jobs[other.label].items():
            for request in (own.admin, own.auditor):
                for call in (exports_api.get_export, exports_api.download_export):
                    with pytest.raises(HTTPException) as missing:
                        await call(request, uuid.UUID(job_id), tenant_id=own.tid)
                    assert missing.value.status_code == 404, (own.label, kind, call.__name__)
            if kind == "evidence_pack":
                with pytest.raises(HTTPException) as missing:
                    await exports_api.sign_export(
                        exports_api.SignRequest(statement="Not ours"), own.auditor, uuid.UUID(job_id),
                        tenant_id=own.tid,
                    )
                assert missing.value.status_code == 404
