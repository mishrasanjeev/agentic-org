# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Scheduled Insights reports (Phase 3, part 4) against real Postgres without BYPASSRLS.

The scratch schema of the packs test plus the report schedule tables (v6z111).
A CFO schedules the Finance view: the API keeps the view and period and
records the CFO as the owner, and refuses a view the CFO may not read. A run
builds the PDF as the CFO, emails the recipient a link (no attachment) and
records the run; the recipient, who signs in to the organisation, may list and
download that file. A period with no data is blocked with the reason, a
schedule whose owner left fails, and the run history lists every run.
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
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from core.insights.metrics import day_bounds, local_today

KOLKATA = ZoneInfo("Asia/Kolkata")
VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
MIGRATIONS = ("v6_z106_insights_foundation.py", "v6_z110_insights_exports.py", "v6_z111_report_schedule_runs.py")
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
    "report_schedules",
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
    schema = "insights_sched_" + uuid.uuid4().hex[:12]
    role = "insights_sched_" + uuid.uuid4().hex[:12]
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
    monkeypatch.setattr(settings, "insights_export_bucket", "sched-it")
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


def _named_request(role: str, user_id: uuid.UUID, *, domains: list[str] | None, admin: bool = False):
    request = _request(role, domains=domains, admin=admin)
    request.state.claims["agenticorg:user_id"] = str(user_id)
    return request


CFO_ID = uuid.uuid4()
READER_ID = uuid.uuid4()


def _claims(role: str, user_id: uuid.UUID, *, admin: bool = False) -> dict:
    return {
        "sub": f"{role}@example.test",
        "role": role,
        "agenticorg:user_id": str(user_id),
        "grantex:scopes": ["agenticorg:admin"] if admin else ["report_schedules.read", "report_schedules.write"],
    }


def _seed(world: World) -> SimpleNamespace:
    from core.models.agent import Agent
    from core.models.insights import RunFact
    from core.models.tenant import Tenant
    from core.models.user import User

    now = datetime.now(UTC)
    today = local_today(KOLKATA, now)
    start = day_bounds(today, KOLKATA)[0] + timedelta(minutes=1)
    a = uuid.uuid4()
    world.add(Tenant(id=a, name="Synthetic schedules", slug="sched-" + a.hex, data_region="IN", settings={}))
    world.add(
        User(id=CFO_ID, tenant_id=a, email=f"cfo-{a.hex[:6]}@example.test", name="Priya", role="cfo"),
        User(id=READER_ID, tenant_id=a, email=f"reader-{a.hex[:6]}@example.test", name="Dev", role="analyst"),
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
    return SimpleNamespace(a=str(a), reader_email=f"reader-{a.hex[:6]}@example.test")


async def _schedule(api, seed, claims, *, view: str, period: str, emails: list[str]) -> str:
    body = api.ReportScheduleCreate(
        report_type="insights_view",
        cron_expression="0 9 * * 1",
        delivery_channels=[{"type": "email", "target": e} for e in emails],
        params={"view": view, "period": period},
    )
    return (await api.create_report_schedule(body, tenant_id=seed.a, current_user=claims)).id


async def test_a_scheduled_insights_view_runs_as_its_owner_and_reaches_its_recipients(world, monkeypatch) -> None:
    from api.v1 import insights_exports as exports_api
    from api.v1 import report_schedules as api
    from core.insights import scheduled
    from core.test_doubles import fake_mail

    monkeypatch.setattr("core.email.validate_email_domain", lambda _to: (True, ""))
    monkeypatch.setattr("core.insights.scheduled.insights_enabled_for", AsyncMock(return_value=True), raising=False)
    monkeypatch.setattr("core.insights.flags.insights_enabled_for", AsyncMock(return_value=True))
    fake_mail.reset()
    seed = _seed(world)
    cfo = _claims("cfo", CFO_ID)

    with pytest.raises(HTTPException) as refused:
        await _schedule(api, seed, cfo, view="compliance", period="last_7_days", emails=["x@example.test"])
    assert refused.value.status_code == 403
    sid = await _schedule(api, seed, cfo, view="finance", period="month_to_date", emails=[seed.reader_email])
    listed = await api.list_report_schedules(tenant_id=seed.a, current_user=cfo)
    mine = next(s for s in listed if s.id == sid)
    assert mine.params == {"view": "finance", "period": "month_to_date"} and mine.format == "pdf"

    result = await scheduled.run({"tenant_id": seed.a, "schedule_id": sid, "report_type": "insights_view"})
    assert result["status"] == "delivered", result
    mail = fake_mail.last()
    assert mail is not None and mail.to == seed.reader_email
    assert "/dashboard/insights/downloads" in mail.html and "SHA-256" in mail.html

    # The recipient (an analyst of the organisation) may list and download the file it was sent.
    reader = _named_request("analyst", READER_ID, domains=[])
    files = (await exports_api.list_exports(reader, tenant_id=seed.a))["exports"]
    assert len(files) == 1 and files[0]["kind"] == "view_pdf"
    body = (await exports_api.download_export(reader, uuid.UUID(files[0]["id"]), tenant_id=seed.a)).body
    assert body[:5] == b"%PDF-"

    # A period with no data is held back; a schedule whose owner left fails; both are in the history.
    empty = await _schedule(api, seed, cfo, view="finance", period="last_month", emails=[seed.reader_email])
    blocked = await scheduled.run({"tenant_id": seed.a, "schedule_id": empty, "report_type": "insights_view"})
    assert blocked["status"] == "blocked" and blocked["reason"].startswith("no_data")

    runs = (await api.list_report_schedule_runs(sid, tenant_id=seed.a, current_user=cfo))["runs"]
    assert [r["status"] for r in runs] == ["delivered"] and runs[0]["export_id"] == files[0]["id"]
    assert runs[0]["channels"] == [{"type": "email", "target": seed.reader_email, "status": "sent"}]
    await scheduled.record_run(seed.a, sid, {"status": "completed"}, "manual")
    runs = (await api.list_report_schedule_runs(sid, tenant_id=seed.a, current_user=cfo))["runs"]
    assert [(r["status"], r["trigger"]) for r in runs][0] == ("generated", "manual")

    from sqlalchemy import text

    with world.admin.begin() as conn:
        _in_schema(conn, world.schema)
        conn.execute(text("UPDATE users SET status = 'inactive' WHERE id = :u"), {"u": str(CFO_ID)})
    gone = await scheduled.run({"tenant_id": seed.a, "schedule_id": sid, "report_type": "insights_view"})
    assert gone["status"] == "failed" and gone["reason"].startswith("no_owner")
