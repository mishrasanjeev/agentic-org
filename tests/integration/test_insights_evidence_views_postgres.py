# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights Phase 3 evidence and the Compliance and Engineering views, against real Postgres without BYPASSRLS.

A scratch schema holds the tables these read (from the ORM models), the v6z106
migration adds the insights tables, and every tenant-scoped table gets the
forced row-level policy production uses. One failed run has a root span, a
reasoning span and a failing tool call (another run in the same trace must not
appear), a model call, an approval decided under a delegation and audit rows,
one of them unsigned. The run evidence draws the run's own spans, names the
failing tool call as the cause, gives the delegation and the vote, and checks
each signature; the bundle's checksum is the SHA-256 of its bytes and only an
administrator or auditor gets the signed audit payloads. The auditor's
Compliance view counts the delegated decision, the rubber stamp, the open and
completed data subject requests, the attestation about to lapse and the
changes; the developer's Engineering view is limited to the developer's domain.
Another tenant sees none of it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
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
from sqlalchemy import create_engine
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
    "run_spans",
    "model_gateway_records",
    "dsar_requests",
    "provider_residency_attestations",
    "prompt_edit_history",
    "prompt_template_edit_history",
    "user_delegations",
    "eval_datasets",
    "eval_runs",
    "value_baselines",
    "value_rates",
    "insights_alerts",
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
    spec = importlib.util.spec_from_file_location("_v6_z106_insights_foundation_evidence", MIGRATION)
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
    schema = "insights_evidence_" + uuid.uuid4().hex[:12]
    role = "insights_evidence_" + uuid.uuid4().hex[:12]
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
    # No chain sealing in this scratch schema: the integrity tile says so (its sealed path is covered elsewhere).
    monkeypatch.setattr(settings, "audit_chain_enabled", False)
    monkeypatch.setattr(settings, "audit_chain_default_on", False)
    try:
        yield World(admin, schema)
    finally:
        await runtime.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.exec_driver_sql(f'DROP OWNED BY "{role}"')
            conn.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')
        admin.dispose()


def _request(role: str, *, domains: list[str] | None, admin: bool = False):
    scopes = ["agenticorg:admin"] if admin else ["insights:read"]
    claims = {
        "sub": f"{role}@example.test",
        "role": role,
        "agenticorg:user_id": str(uuid.uuid4()),
        "agenticorg:domains": domains,
        "grantex:scopes": scopes,
    }
    return SimpleNamespace(state=SimpleNamespace(claims=claims, scopes=scopes, auth_mode=None), query_params={})


ADMIN = _request("admin", domains=None, admin=True)
AUDITOR = _request("auditor", domains=None)
DEVELOPER = _request("developer", domains=["ops"])
COO = _request("coo", domains=["ops"])
MERCHANT = _request("merchant", domains=["commerce"])
TRACE = "a" * 32
ROOT, REASON, TOOL, OTHER_ROOT = "1" * 16, "2" * 16, "3" * 16, "4" * 16


def _seed(world: World) -> SimpleNamespace:
    from core.governance.model_gateway_records import sign_record
    from core.models.agent import Agent
    from core.models.audit import AuditLog
    from core.models.delegation import UserDelegation
    from core.models.dsar import DSARRequestRecord
    from core.models.eval_dataset import EvalDataset
    from core.models.eval_run import EvalRun
    from core.models.hitl import HITLQueue
    from core.models.insights import RunFact
    from core.models.model_gateway_record import ModelGatewayRecord
    from core.models.prompt_template import PromptEditHistory
    from core.models.provider_attestation import ProviderAttestation
    from core.models.run_span import RunSpan
    from core.models.tenant import Tenant
    from core.models.user import User
    from core.tool_gateway.audit_logger import sign_audit_record

    key = __import__("core.config", fromlist=["settings"]).settings.secret_key.encode()
    now = datetime.now(UTC)
    today = local_today(KOLKATA, now)
    start = (day_bounds(today, KOLKATA)[0] + timedelta(minutes=1)).replace(microsecond=0)
    a, b = uuid.uuid4(), uuid.uuid4()
    world.add(
        Tenant(id=a, name="Synthetic evidence A", slug="evidence-a-" + a.hex, data_region="IN", settings={}),
        Tenant(id=b, name="Synthetic evidence B", slug="evidence-b-" + b.hex, data_region="IN", settings={}),
    )
    reviewer, delegator = uuid.uuid4(), uuid.uuid4()
    world.add(
        User(id=reviewer, tenant_id=a, email="asha@example.test", name="Asha", role="coo", domain="ops"),
        User(id=delegator, tenant_id=a, email="ravi@example.test", name="Ravi", role="coo", domain="ops"),
    )
    base = {"system_prompt_ref": "prompts/synthetic.txt", "hitl_condition": "confidence < 0.8", "status": "active"}
    ops_agent, finance_agent = uuid.uuid4(), uuid.uuid4()
    world.add(
        Agent(id=ops_agent, tenant_id=a, name="Payroll checker", agent_type="ops_bot", domain="ops", **base),
        Agent(id=finance_agent, tenant_id=a, name="Ledger bot", agent_type="fin_bot", domain="finance", **base),
    )
    fact_id = uuid.uuid4()
    fact = RunFact(
        id=fact_id,
        tenant_id=a,
        run_key="agents:" + uuid.uuid4().hex,
        entrypoint="agents",
        agent_id=ops_agent,
        agent_type="ops_bot",
        domain="ops",
        status="failed",
        error_class="ToolError",
        duration_ms=1500,
        tokens_total=10,
        cost_usd=Decimal("0.01"),
        started_at=start,
        correlation_id="corr-1",
        trace_id=TRACE,
    )
    world.add(fact)

    def span(span_id, name, status, offset_ms, duration_ms, *, parent=None, run=ROOT, corr="corr-1", **extra):
        return RunSpan(
            tenant_id=a,
            trace_id=TRACE,
            run_span_id=run,
            span_id=span_id,
            parent_span_id=parent,
            name=name,
            kind="internal",
            status=status,
            agent_id=str(ops_agent),
            correlation_id=corr,
            started_at=start + timedelta(milliseconds=offset_ms),
            duration_ms=duration_ms,
            attributes=extra.get("attributes", {}),
            events=extra.get("events", []),
        )

    world.add(
        span(ROOT, "agenticorg.agent.run", "error", 0, 1500, attributes={"agent.run.status": "failed"}),
        span(
            REASON,
            "agenticorg.agent.reason",
            "ok",
            100,
            200,
            parent=ROOT,
            attributes={"llm.provider": "gemini", "llm.model": "gemini-2.5-flash", "llm.input_tokens": 7},
        ),
        span(
            TOOL,
            "agenticorg.tool.call",
            "error",
            400,
            1000,
            parent=ROOT,
            attributes={"tool.name": "get_payslip", "connector.id": "zoho", "tool.outcome": "failed"},
            events=[{"name": "exception", "offset_ms": 990, "attributes": {"exception.type": "TimeoutError"}}],
        ),
        # Another run in the same trace (a later step) must not show in this run's waterfall.
        span(OTHER_ROOT, "agenticorg.agent.run", "ok", 5000, 300, run=OTHER_ROOT, corr="corr-2"),
    )
    created = start + timedelta(seconds=2)
    gateway = [
        ModelGatewayRecord(
            tenant_id=a,
            correlation_id="corr-1",
            agent_id=str(ops_agent),
            provider="gemini",
            model="gemini-2.5-flash",
            outcome="failed",
            error_type="timeout",
            latency_ms=900,
            created_at=created,
        ),
        ModelGatewayRecord(
            tenant_id=a,
            correlation_id="corr-9",
            agent_id=str(finance_agent),
            provider="gemini",
            model="gemini-2.5-pro",
            outcome="failed",
            latency_ms=900,
            created_at=created,
        ),
    ]
    for record in gateway:
        # Signed as the writer does: every signed field set (column defaults are filled only on insert).
        record.use_case, record.restricted, record.tokens, record.cost_usd = "", False, 0, 0.0
        record.signature = sign_record(record, key)
    world.add(*gateway)
    item = uuid.uuid4()
    raised = start + timedelta(seconds=30)
    world.add(
        HITLQueue(
            id=item,
            tenant_id=a,
            agent_id=ops_agent,
            title="Release payroll",
            trigger_type="confidence",
            priority="normal",
            status="decided",
            assignee_role="coo",
            decision_options=["approve", "reject"],
            context={
                "correlation_id": "corr-1",
                "policy_state": {
                    "approvals": [
                        {"user_id": str(reviewer), "decision": "approve", "sequence": 1, "at": raised.isoformat()}
                    ]
                },
            },
            decision="approve",
            decision_by=reviewer,
            first_viewed_at=raised + timedelta(seconds=5),
            decision_at=raised + timedelta(seconds=12),
            created_at=raised,
            expires_at=raised + timedelta(days=2),
        )
    )
    signed_rows = [
        AuditLog(
            tenant_id=a,
            event_type="hitl.decided",
            actor_type="user",
            actor_id=str(reviewer),
            agent_id=ops_agent,
            action="approve",
            outcome="success",
            resource_type="hitl_item",
            resource_id=str(item),
            details={"decision": "approve", "delegated_from": str(delegator)},
            created_at=raised + timedelta(seconds=12),
        ),
        AuditLog(
            tenant_id=a,
            event_type="tool.get_payslip",
            actor_type="agent",
            actor_id=str(ops_agent),
            agent_id=ops_agent,
            action="call",
            outcome="failure",
            resource_type="tool_call",
            trace_id=TRACE,
            details={"correlation_id": "corr-1"},
            created_at=start + timedelta(seconds=1),
        ),
        AuditLog(
            tenant_id=a,
            event_type="agent.create",
            actor_type="user",
            actor_id=str(reviewer),
            action="create",
            outcome="success",
            details={},
            created_at=start + timedelta(minutes=5),
        ),
    ]
    for row in signed_rows:
        row.signature = sign_audit_record(row, key)
    world.add(
        *signed_rows,
        # Unsigned (as some writers leave them): shown, never counted as verified.
        AuditLog(
            tenant_id=a,
            event_type="tool.lookup",
            actor_type="agent",
            actor_id=str(ops_agent),
            action="call",
            outcome="success",
            trace_id=TRACE,
            details={},
            created_at=start + timedelta(seconds=1, milliseconds=500),
        ),
    )
    world.add(
        DSARRequestRecord(
            tenant_id=a,
            request_type="access",
            subject_email="s1@example.test",
            requested_by="x",
            status="received",
            created_at=start,
        ),
        DSARRequestRecord(
            tenant_id=a,
            request_type="erase",
            subject_email="s2@example.test",
            requested_by="x",
            status="completed",
            created_at=start,
            completed_at=start + timedelta(hours=6),
        ),
        ProviderAttestation(
            tenant_id=a, provider="gemini", data_region="IN", attested_by="x", expires_at=now + timedelta(days=10)
        ),
        ProviderAttestation(
            tenant_id=a, provider="openai", data_region="IN", attested_by="x", expires_at=now + timedelta(days=90)
        ),
        ProviderAttestation(
            tenant_id=a,
            provider="anthropic",
            data_region="IN",
            attested_by="x",
            expires_at=now + timedelta(days=5),
            revoked_at=now,
        ),
        PromptEditHistory(tenant_id=a, agent_id=ops_agent, prompt_after="v2", created_at=start + timedelta(minutes=7)),
        PromptEditHistory(
            tenant_id=a, agent_id=finance_agent, prompt_after="v2", created_at=start + timedelta(minutes=8)
        ),
        UserDelegation(
            tenant_id=a,
            delegator_id=delegator,
            delegate_id=reviewer,
            created_at=start,
            starts_at=start,
            ends_at=start + timedelta(days=7),
        ),
    )
    dataset = uuid.uuid4()
    world.add(EvalDataset(id=dataset, tenant_id=a, name="Payroll cases"))
    world.add(
        EvalRun(
            tenant_id=a,
            dataset_id=dataset,
            version=1,
            content_hash="c" * 64,
            model="gemini-2.5-flash",
            prompt_hash="p" * 64,
            pass_rate=0.82,
            cases_run=50,
            created_at=start + timedelta(minutes=9),
        ),
    )
    return SimpleNamespace(a=str(a), b=str(b), fact_id=fact_id, start=start)


def _tiles(view: dict) -> dict:
    return {m["key"]: m for m in (*view["tiles"], *view["charts"])}


async def test_run_evidence_draws_the_runs_waterfall_and_names_its_failure(world) -> None:
    from api.v1 import insights_explore as api

    seed = _seed(world)
    at = seed.start.isoformat()
    evidence = await api.insights_run_evidence(seed.fact_id, COO, tenant_id=seed.a, at=at)
    spans = evidence["spans"]
    assert [s["span_id"] for s in spans] == [ROOT, REASON, TOOL]
    assert [s["depth"] for s in spans] == [0, 1, 1]
    assert [s["offset_ms"] for s in spans] == [0, 100, 400]
    assert spans[1]["summary"] == "gemini/gemini-2.5-flash · 7 in / 0 out"
    assert spans[2]["events"][0]["label"] == "exception TimeoutError"
    assert evidence["span_total_ms"] == 1500
    failure = evidence["failure"]
    assert failure["source"] == "span" and failure["span_id"] == TOOL
    assert failure["exception"] == "exception TimeoutError" and failure["summary"] == "zoho:get_payslip failed"
    [approval] = evidence["approvals"]
    assert approval["decided_by"] == "Asha" and approval["delegated_from"] == "Ravi"
    assert approval["votes"] == [{"by": "Asha", "decision": "approve", "step": 1, "at": approval["votes"][0]["at"]}]
    audit = {row["event_type"]: row for row in evidence["audit"]}
    assert audit["tool.get_payslip"]["signed"] and audit["tool.get_payslip"]["verified"]
    assert not audit["tool.lookup"]["signed"] and not audit["tool.lookup"]["verified"]
    [call] = evidence["gateway_records"]
    assert call["signed"] and call["verified"]
    # Nothing a person typed or a model wrote reaches the page.
    assert "s1@example.test" not in json.dumps(evidence)

    # The bundle: its header checksum is the SHA-256 of its bytes; only administrators and auditors get payloads.
    response = await api.insights_run_bundle(seed.fact_id, ADMIN, tenant_id=seed.a, at=at)
    assert hashlib.sha256(response.body).hexdigest() == response.headers["X-Content-SHA256"]
    bundle = json.loads(response.body)
    assert bundle["format"] == "agenticorg-insights-run-bundle-v1"
    assert {p["payload"]["event_type"] for p in bundle["audit_payloads"]} == {"tool.get_payslip", "tool.lookup"}
    coo_bundle = json.loads((await api.insights_run_bundle(seed.fact_id, COO, tenant_id=seed.a, at=at)).body)
    assert "audit_payloads" not in coo_bundle and coo_bundle["failure"]["span_id"] == TOOL
    with pytest.raises(HTTPException) as refused:
        await api.insights_run_bundle(seed.fact_id, MERCHANT, tenant_id=seed.a, at=at)
    assert refused.value.status_code == 403
    with pytest.raises(HTTPException) as missing:
        await api.insights_run_evidence(seed.fact_id, ADMIN, tenant_id=seed.b, at=at)
    assert missing.value.status_code == 404


async def test_the_compliance_and_engineering_views(world) -> None:
    from api.v1 import insights as api

    seed = _seed(world)
    compliance = _tiles(await api.insights_view("compliance", AUDITOR, tenant_id=seed.a))
    decided = compliance["compliance.approvals_decided"]
    assert decided["value"] == 1 and {r["label"]: r["value"] for r in decided["rows"]} == {"direct": 0, "delegated": 1}
    assert compliance["compliance.rubber_stamps"]["value"] == 100.0  # decided 7 s after it was opened
    assert compliance["compliance.rubber_stamps"]["state"] == "act"
    dsar = compliance["compliance.dsar_open"]
    assert dsar["value"] == 1 and "s1@example.test" not in json.dumps(dsar)
    assert compliance["compliance.dsar_completion_time"]["value"] == 6 * 3_600_000
    expiring = compliance["compliance.attestations_expiring"]
    assert expiring["value"] == 1 and expiring["rows"][0]["label"] == "gemini (IN)"
    changes = compliance["compliance.change_log"]
    assert {r["label"]: r["value"] for r in changes["rows"]} == {"agents": 1, "prompts": 2, "delegations": 1}
    assert compliance["compliance.audit_integrity"]["data_quality"] == "no_data"

    engineering = _tiles(await api.insights_view("engineering", DEVELOPER, tenant_id=seed.a))
    issues = engineering["engineering.gateway_issues"]
    assert issues["value"] == 1  # the finance agent's failed call is outside the developer's domain
    spans = engineering["engineering.slow_spans"]
    assert spans["rows"][0]["label"] == "agent.run" and spans["rows"][0]["errors"] == 1
    assert engineering["engineering.eval_pass_rate"]["data_quality"] == "no_data"  # tenant-wide: administrators
    assert engineering["engineering.prompt_edits"]["value"] == 1
    admin_engineering = _tiles(await api.insights_view("engineering", ADMIN, tenant_id=seed.a))
    assert admin_engineering["engineering.eval_pass_rate"]["value"] == 82.0
    assert admin_engineering["engineering.gateway_issues"]["value"] == 2
    assert admin_engineering["engineering.prompt_edits"]["value"] == 2

    for view, request in (("compliance", COO), ("engineering", AUDITOR), ("compliance", DEVELOPER)):
        with pytest.raises(HTTPException) as refused:
            await api.insights_view(view, request, tenant_id=seed.a)
        assert refused.value.status_code == 403, view

    other = _tiles(await api.insights_view("compliance", ADMIN, tenant_id=seed.b))
    assert all(m["data_quality"] == "no_data" for m in other.values()), other
    other_eng = _tiles(await api.insights_view("engineering", ADMIN, tenant_id=seed.b))
    assert all(m["data_quality"] == "no_data" for m in other_eng.values()), other_eng
