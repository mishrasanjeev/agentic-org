# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights packs (Phase 3, part 3) against real Postgres without BYPASSRLS.

The scratch schema of the exports test plus the governance tables. An auditor
asks for a period evidence pack: the ZIP holds the summary PDF, the CSV
appendices (approvals with the delegation, data subject requests without the
subject), verification.json naming the one audit row whose signature no
longer verifies, and a manifest whose HMAC and per-file SHA-256 check out. The
auditor signs it once (a signed audit row); a CFO cannot see it. A CFO's
finance pack holds the workbook with its sheets, the Finance view PDF and the
manifest; a view PDF is a PDF.
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
VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
MIGRATIONS = ("v6_z106_insights_foundation.py", "v6_z110_insights_exports.py", "v6_z116_insights_export_formats.py")
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
    "governance_config",
    "user_delegations",
    "dsar_requests",
    "provider_residency_attestations",
    "prompt_edit_history",
    "audit_chain_anchors",
    "audit_chain_witness_checkpoints",
    "value_baselines",
    "value_rates",
    "insights_alerts",
)

pytestmark = [
    pytest.mark.skipif(not os.getenv("AGENTICORG_DB_URL"), reason="Requires the disposable Docker Postgres database"),
    pytest.mark.timeout(300),
]


def _sync_url() -> str:
    return os.environ["AGENTICORG_DB_URL"].replace("+asyncpg", "")


def _migration(name: str):
    spec = importlib.util.spec_from_file_location("_exports_" + name.removesuffix(".py"), VERSIONS / name)
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
    schema = "insights_packs_" + uuid.uuid4().hex[:12]
    role = "insights_packs_" + uuid.uuid4().hex[:12]
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
    monkeypatch.setattr(settings, "insights_export_bucket", "packs-it")
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


def _audit_count(world: World, tenant: str, event: str) -> int:
    from sqlalchemy import text

    with world.admin.begin() as conn:
        _in_schema(conn, world.schema)
        return int(
            conn.execute(
                text("SELECT count(*) FROM audit_log WHERE tenant_id = :t AND event_type = :e"),
                {"t": tenant, "e": event},
            ).scalar()
        )


AUDITOR_ID = uuid.uuid4()
DELEGATOR_ID = uuid.uuid4()


def _named_request(role: str, user_id: uuid.UUID, *, domains: list[str] | None, admin: bool = False):
    request = _request(role, domains=domains, admin=admin)
    request.state.claims["agenticorg:user_id"] = str(user_id)
    return request


AUDITOR = _named_request("auditor", AUDITOR_ID, domains=None)
CFO = _request("cfo", domains=["finance"])


def _seed(world: World) -> SimpleNamespace:
    from core.config import settings
    from core.models.agent import Agent
    from core.models.audit import AuditLog
    from core.models.delegation import UserDelegation
    from core.models.dsar import DSARRequestRecord
    from core.models.hitl import HITLQueue
    from core.models.insights import RunFact
    from core.models.provider_attestation import ProviderAttestation
    from core.models.tenant import Tenant
    from core.models.user import User
    from core.tool_gateway.audit_logger import sign_audit_record

    key = settings.secret_key.encode()
    now = datetime.now(UTC)
    today = local_today(KOLKATA, now)
    start = (day_bounds(today, KOLKATA)[0] + timedelta(minutes=1)).replace(microsecond=0)
    a = uuid.uuid4()
    world.add(Tenant(id=a, name="Synthetic packs", slug="packs-" + a.hex, data_region="IN", settings={}))
    reviewer = uuid.uuid4()
    world.add(
        User(id=AUDITOR_ID, tenant_id=a, email=f"meera-{a.hex[:6]}@example.test", name="Meera", role="auditor"),
        User(id=DELEGATOR_ID, tenant_id=a, email=f"ravi-{a.hex[:6]}@example.test", name="Ravi", role="cfo"),
        User(id=reviewer, tenant_id=a, email=f"asha-{a.hex[:6]}@example.test", name="Asha", role="cfo"),
    )
    agent = uuid.uuid4()
    base = {"system_prompt_ref": "prompts/synthetic.txt", "hitl_condition": "confidence < 0.8", "status": "active"}
    world.add(Agent(id=agent, tenant_id=a, name="Ledger bot", agent_type="ap_bot", domain="finance", **base))
    world.add(
        RunFact(
            tenant_id=a,
            run_key="agents:" + uuid.uuid4().hex,
            entrypoint="agents",
            agent_id=agent,
            agent_type="ap_bot",
            domain="finance",
            status="completed",
            duration_ms=1000,
            tokens_total=10,
            cost_usd=Decimal("0.01"),
            started_at=start,
        )
    )
    item = uuid.uuid4()
    world.add(
        HITLQueue(
            id=item,
            tenant_id=a,
            agent_id=agent,
            title="Pay vendor",
            trigger_type="amount",
            priority="normal",
            status="decided",
            assignee_role="cfo",
            decision_options=["approve", "reject"],
            context={"policy_state": {"approvals": [{"user_id": str(reviewer), "decision": "approve", "sequence": 1}]}},
            decision="approve",
            decision_by=reviewer,
            created_at=start,
            first_viewed_at=start + timedelta(minutes=2),
            decision_at=start + timedelta(minutes=2, seconds=30),
            expires_at=start + timedelta(days=2),
        )
    )
    rows = [
        AuditLog(
            tenant_id=a,
            event_type="hitl.decided",
            actor_type="user",
            actor_id=str(reviewer),
            action="approve",
            outcome="success",
            resource_type="hitl_item",
            resource_id=str(item),
            details={"delegated_from": str(DELEGATOR_ID)},
            created_at=start + timedelta(minutes=3),
        ),
        AuditLog(
            tenant_id=a,
            event_type="agent.create",
            actor_type="user",
            actor_id=str(reviewer),
            action="create",
            outcome="success",
            details={"contact": "owner@vendor.example"},
            created_at=start + timedelta(minutes=4),
        ),
        AuditLog(
            tenant_id=a,
            event_type="config.reporting.write",
            actor_type="user",
            actor_id=str(reviewer),
            action="write",
            outcome="success",
            details={"timezone": "Asia/Kolkata"},
            created_at=start + timedelta(minutes=5),
        ),
    ]
    for row in rows:
        row.signature = sign_audit_record(row, key)
    tampered_id = uuid.uuid4()
    rows[2].id = tampered_id
    rows[2].details = {"timezone": "UTC"}  # changed after it was signed
    world.add(
        *rows,
        AuditLog(
            tenant_id=a,
            event_type="dsar.access",
            actor_type="user",
            actor_id="x",
            action="dsar",
            outcome="success",
            details={},
            created_at=start + timedelta(minutes=6),
        ),
        UserDelegation(
            tenant_id=a,
            delegator_id=DELEGATOR_ID,
            delegate_id=reviewer,
            starts_at=start,
            ends_at=start + timedelta(days=7),
        ),
        DSARRequestRecord(
            tenant_id=a,
            request_type="access",
            subject_email="subject@example.test",
            requested_by="x",
            status="completed",
            created_at=start,
            completed_at=start + timedelta(hours=3),
        ),
        ProviderAttestation(tenant_id=a, provider="gemini", data_region="IN", attested_by="x"),
    )
    return SimpleNamespace(a=str(a), today=today.isoformat(), tampered=str(tampered_id))


def _zip(content: bytes) -> dict[str, bytes]:
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


async def _build_and_fetch(api, export_jobs, tid: str, request, body):
    job = (await api.create_export(body, request, tenant_id=tid))["export"]
    assert await export_jobs.build(uuid.UUID(tid), uuid.UUID(job["id"])) == "ready", (
        await api.get_export(request, uuid.UUID(job["id"]), tenant_id=tid)
    )["export"]["error"]
    response = await api.download_export(request, uuid.UUID(job["id"]), tenant_id=tid)
    return job, response.body


async def test_the_auditor_gets_a_verifiable_evidence_pack_and_signs_it_once(world, monkeypatch) -> None:

    from api.v1 import insights_exports as api
    from core.insights import export_jobs, packs

    monkeypatch.setattr(api, "_enqueue", lambda _tid, _job: True)
    seed = _seed(world)
    body = api.ExportRequest(kind="evidence_pack", format="zip", params={"from": seed.today, "to": seed.today})
    with pytest.raises(HTTPException) as refused:
        await api.create_export(body, CFO, tenant_id=seed.a)
    assert refused.value.status_code == 403
    job, content = await _build_and_fetch(api, export_jobs, seed.a, AUDITOR, body)
    files = _zip(content)
    assert set(files) == {
        "summary.pdf",
        "audit_log.csv",
        "approvals.csv",
        "delegations.csv",
        "dsar.csv",
        "residency_attestations.csv",
        "change_log.csv",
        "verification.json",
        "manifest.json",
    }
    manifest = json.loads(files["manifest.json"])
    assert packs.verify_manifest(manifest)
    for name, entry in manifest["files"].items():
        assert hashlib.sha256(files[name]).hexdigest() == entry["sha256"], name
    verification = json.loads(files["verification.json"])
    assert verification["status"] == "broken"
    # The seeded rows plus the signed row recording this pack's request, which falls in the period too.
    assert verification["rows"] == {"total": 5, "signed": 4, "signature_verifies": 3, "unsigned": 1}
    assert verification["failed_rows"] == [seed.tampered]
    approvals = files["approvals.csv"].decode("utf-8-sig")
    assert "Pay vendor" in approvals and "Ravi" in approvals and "Asha" in approvals and ",30.0" in approvals
    assert "subject@example.test" not in files["dsar.csv"].decode("utf-8-sig")
    assert "owner@vendor.example" not in files["audit_log.csv"].decode("utf-8-sig")  # masked by default
    changes = files["change_log.csv"].decode("utf-8-sig")
    assert "agent.create" in changes and "config.reporting.write" in changes and "delegation.created" in changes
    assert files["summary.pdf"][:5] == b"%PDF-"
    ready = (await api.get_export(AUDITOR, uuid.UUID(job["id"]), tenant_id=seed.a))["export"]
    assert ready["verification_status"] == "broken" and ready["row_count"] == 5

    signed = (
        await api.sign_export(
            api.SignRequest(statement="Reviewed; one row fails its signature."),
            AUDITOR,
            uuid.UUID(job["id"]),
            tenant_id=seed.a,
        )
    )["export"]
    assert signed["signature"]["by"] == "Meera" and signed["signature"]["role"] == "auditor"
    assert signed["signature"]["manifest_sha256"] == hashlib.sha256(files["manifest.json"]).hexdigest()
    assert _audit_count(world, seed.a, "insights.evidence_pack.signed") == 1
    with pytest.raises(HTTPException) as again:
        await api.sign_export(api.SignRequest(statement="again"), AUDITOR, uuid.UUID(job["id"]), tenant_id=seed.a)
    assert again.value.status_code == 409
    with pytest.raises(HTTPException) as hidden:
        await api.sign_export(api.SignRequest(statement="x"), CFO, uuid.UUID(job["id"]), tenant_id=seed.a)
    assert hidden.value.status_code == 404


async def test_the_finance_pack_and_a_view_pdf(world, monkeypatch) -> None:
    import io

    from openpyxl import load_workbook

    from api.v1 import insights_exports as api
    from core.insights import export_jobs, packs

    monkeypatch.setattr(api, "_enqueue", lambda _tid, _job: True)
    seed = _seed(world)
    _job, content = await _build_and_fetch(
        api,
        export_jobs,
        seed.a,
        CFO,
        api.ExportRequest(kind="finance_pack", format="zip", params={"from": seed.today, "to": seed.today}),
    )
    files = _zip(content)
    assert set(files) == {"finance.xlsx", "finance.pdf", "manifest.json"}
    assert packs.verify_manifest(json.loads(files["manifest.json"]))
    sheets = load_workbook(io.BytesIO(files["finance.xlsx"]), read_only=True).sheetnames
    assert sheets == [
        "Summary",
        "Spend by day",
        "Spend by agent",
        "Spend by model",
        "Budgets",
        "Tokens",
        "Money saved",
        "Assumptions",
        "Filters",
        "Definitions",
    ]
    assert files["finance.pdf"][:5] == b"%PDF-"
    _pdf_job, view_pdf = await _build_and_fetch(
        api, export_jobs, seed.a, CFO, api.ExportRequest(kind="view_pdf", format="pdf", params={"view": "finance"})
    )
    assert view_pdf[:5] == b"%PDF-"
    with pytest.raises(HTTPException) as refused:
        await api.create_export(
            api.ExportRequest(kind="view_pdf", format="pdf", params={"view": "compliance"}), CFO, tenant_id=seed.a
        )
    assert refused.value.status_code == 403


async def test_a_view_as_powerpoint_and_tables_as_parquet(world, monkeypatch) -> None:
    import io

    import pyarrow.parquet as pq
    from pptx import Presentation

    from api.v1 import insights_exports as api
    from core.insights import export_jobs

    monkeypatch.setattr(api, "_enqueue", lambda _tid, _job: True)
    seed = _seed(world)
    job, deck = await _build_and_fetch(
        api, export_jobs, seed.a, CFO, api.ExportRequest(kind="view_pptx", format="pptx", params={"view": "finance"})
    )
    assert deck[:2] == b"PK" and job["format"] == "pptx"
    slides = list(Presentation(io.BytesIO(deck)).slides)
    texts = [shape.text_frame.text for slide in slides for shape in slide.shapes if shape.has_text_frame]
    assert texts[0].startswith("Insights - Finance") and any("Headline figures" in t for t in texts)
    assert any(shape.has_chart for slide in slides for shape in slide.shapes) or any("No data" in t for t in texts)
    assert all("Bravo" not in t for t in texts)
    # The PowerPoint is a view export: the same view rules as the PDF.
    with pytest.raises(HTTPException) as refused:
        await api.create_export(
            api.ExportRequest(kind="view_pptx", format="pptx", params={"view": "compliance"}), CFO, tenant_id=seed.a
        )
    assert refused.value.status_code == 403
    with pytest.raises(HTTPException) as wrong:
        await api.create_export(
            api.ExportRequest(kind="view_pptx", format="pdf", params={"view": "finance"}), CFO, tenant_id=seed.a
        )
    assert wrong.value.status_code == 422

    _metric_job, table = await _build_and_fetch(
        api,
        export_jobs,
        seed.a,
        CFO,
        api.ExportRequest(kind="metric", format="parquet",
                          params={"key": "cost.spend", "from": seed.today, "to": seed.today}),
    )
    parquet = pq.read_table(io.BytesIO(table))
    meta = {k.decode(): v.decode() for k, v in (parquet.schema.metadata or {}).items()}
    assert meta["insights.key"] == "cost.spend" and json.loads(meta["insights.filters"])["from"] == seed.today
    assert table[:4] == b"PAR1"
