# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights background exports (Phase 3, part 2) against real Postgres without BYPASSRLS.

A scratch schema holds the tables the runs explorer and the runs metric read
(from the ORM models), the v6z106 and v6z110 migrations add the insights and
export tables, and every tenant-scoped table gets the forced row-level policy
production uses; files go to the in-process fake bucket. A CFO asks for the
runs table in the background: the worker builds it with the CFO's scope, masks
the phone number in an agent name (the tenant masks PII by default), stores it
and records its SHA-256; the download streams the same bytes with one audit
row. Another user and another tenant see nothing; an administrator sees every
job; a CFO may not ask for raw values but an administrator may; with masking
off the values are raw; a file past its seven days is deleted and answers 410;
without a bucket the request is refused with 503.
"""

from __future__ import annotations

import hashlib
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
MIGRATIONS = ("v6_z106_insights_foundation.py", "v6_z110_insights_exports.py")
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
    schema = "insights_exports_" + uuid.uuid4().hex[:12]
    role = "insights_exports_" + uuid.uuid4().hex[:12]
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
    monkeypatch.setattr(settings, "insights_export_bucket", "exports-it")
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


ADMIN = _request("admin", domains=None, admin=True)
CFO = _request("cfo", domains=["finance"])
OTHER_CFO = _request("cfo", domains=["finance"])


def _seed(world: World) -> SimpleNamespace:
    from core.models.agent import Agent
    from core.models.insights import RunFact
    from core.models.tenant import Tenant

    now = datetime.now(UTC)
    start = day_bounds(local_today(KOLKATA, now), KOLKATA)[0] + timedelta(minutes=1)
    a, b = uuid.uuid4(), uuid.uuid4()
    world.add(
        Tenant(id=a, name="Synthetic exports A", slug="exports-a-" + a.hex, data_region="IN", settings={}),
        Tenant(id=b, name="Synthetic exports B", slug="exports-b-" + b.hex, data_region="IN", settings={}),
    )
    agent = uuid.uuid4()
    base = {"system_prompt_ref": "prompts/synthetic.txt", "hitl_condition": "confidence < 0.8", "status": "active"}
    world.add(Agent(id=agent, tenant_id=a, name="Desk 9876543210", agent_type="ap_bot", domain="finance", **base))
    world.add(
        *(
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
                started_at=start + timedelta(minutes=i),
            )
            for i in range(3)
        )
    )
    return SimpleNamespace(a=str(a), b=str(b))


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


async def test_a_background_export_is_built_with_the_callers_scope_masked_stored_and_downloaded(
    world, monkeypatch
) -> None:
    from api.v1 import insights_exports as api
    from core.insights import export_jobs
    from core.test_doubles import fake_storage

    fake_storage.reset()
    queued: list[str] = []
    monkeypatch.setattr(api, "_enqueue", lambda _tid, job_id: queued.append(str(job_id)) or True)
    seed = _seed(world)
    body = api.ExportRequest(kind="explorer", format="csv", params={"key": "runs", "query": {"sort": "started_at"}})
    job = (await api.create_export(body, CFO, tenant_id=seed.a))["export"]
    assert job["status"] == "queued" and queued == [job["id"]]
    assert _audit_count(world, seed.a, "insights.export.requested") == 1

    assert await export_jobs.build(uuid.UUID(seed.a), uuid.UUID(job["id"])) == "ready"
    # Built once: a second delivery of the task does nothing.
    assert await export_jobs.build(uuid.UUID(seed.a), uuid.UUID(job["id"])) == "ready"
    ready = (await api.get_export(CFO, uuid.UUID(job["id"]), tenant_id=seed.a))["export"]
    assert ready["status"] == "ready" and ready["row_count"] == 3 and ready["download"]
    response = await api.download_export(CFO, uuid.UUID(job["id"]), tenant_id=seed.a)
    content = response.body
    assert hashlib.sha256(content).hexdigest() == ready["sha256"] == response.headers["X-Content-SHA256"]
    text_body = content.decode("utf-8")
    assert "Desk" in text_body and "9876543210" not in text_body  # masked by the tenant's default
    assert _audit_count(world, seed.a, "insights.export") == 1

    # Another person of the same tenant, and another tenant, see nothing; an administrator sees the tenant's.
    assert (await api.list_exports(OTHER_CFO, tenant_id=seed.a))["exports"] == []
    with pytest.raises(HTTPException) as hidden:
        await api.download_export(OTHER_CFO, uuid.UUID(job["id"]), tenant_id=seed.a)
    assert hidden.value.status_code == 404
    assert [j["id"] for j in (await api.list_exports(ADMIN, tenant_id=seed.a))["exports"]] == [job["id"]]
    assert (await api.list_exports(ADMIN, tenant_id=seed.b))["exports"] == []
    assert await export_jobs.build(uuid.UUID(seed.b), uuid.UUID(job["id"])) == "missing"

    # Raw values: refused for the CFO; an administrator may, and so do all when the tenant turns masking off.
    raw = api.ExportRequest(kind="explorer", format="json", params={"key": "runs"}, include_pii=True)
    with pytest.raises(HTTPException) as refused:
        await api.create_export(raw, CFO, tenant_id=seed.a)
    assert refused.value.status_code == 403 and refused.value.detail["error"] == "include_pii_forbidden"
    admin_raw = (await api.create_export(raw, ADMIN, tenant_id=seed.a))["export"]
    assert await export_jobs.build(uuid.UUID(seed.a), uuid.UUID(admin_raw["id"])) == "ready"
    raw_body = (await api.download_export(ADMIN, uuid.UUID(admin_raw["id"]), tenant_id=seed.a)).body
    assert b"9876543210" in raw_body

    from core.models.governance_config import GovernanceConfig

    world.add(GovernanceConfig(tenant_id=uuid.UUID(seed.a), pii_masking=False))
    plain = (await api.create_export(body, CFO, tenant_id=seed.a))["export"]
    assert await export_jobs.build(uuid.UUID(seed.a), uuid.UUID(plain["id"])) == "ready"
    assert b"9876543210" in (await api.download_export(CFO, uuid.UUID(plain["id"]), tenant_id=seed.a)).body

    # A metric in the background too.
    metric = api.ExportRequest(kind="metric", format="xlsx", params={"key": "runs.volume"})
    metric_job = (await api.create_export(metric, CFO, tenant_id=seed.a))["export"]
    assert await export_jobs.build(uuid.UUID(seed.a), uuid.UUID(metric_job["id"])) == "ready"
    xlsx = (await api.download_export(CFO, uuid.UUID(metric_job["id"]), tenant_id=seed.a)).body
    assert xlsx[:2] == b"PK"

    # Seven days on: the files are deleted and the download says so.
    later = datetime.now(UTC) + timedelta(days=8)
    assert await export_jobs.expire(uuid.UUID(seed.a), later) == 4
    with pytest.raises(HTTPException) as gone:
        await api.download_export(CFO, uuid.UUID(job["id"]), tenant_id=seed.a)
    assert gone.value.status_code == 410
    assert fake_storage.list_in("exports-it") == []


async def test_a_job_the_worker_lost_is_reported_and_no_bucket_means_503(world, monkeypatch) -> None:
    from api.v1 import insights_exports as api
    from core.config import settings
    from core.insights import export_jobs

    monkeypatch.setattr(api, "_enqueue", lambda _tid, _job: False)
    seed = _seed(world)
    body = api.ExportRequest(kind="explorer", format="csv", params={"key": "runs"})
    failed = (await api.create_export(body, CFO, tenant_id=seed.a))["export"]
    assert failed["status"] == "failed" and failed["error"].startswith("queue_unavailable")

    monkeypatch.setattr(api, "_enqueue", lambda _tid, _job: True)
    lost = (await api.create_export(body, CFO, tenant_id=seed.a))["export"]
    assert await export_jobs.expire(uuid.UUID(seed.a), datetime.now(UTC) + timedelta(hours=3)) == 0
    assert (await api.get_export(CFO, uuid.UUID(lost["id"]), tenant_id=seed.a))["export"]["error"].startswith(
        "build_lost"
    )

    monkeypatch.setattr(settings, "insights_export_bucket", "")
    with pytest.raises(HTTPException) as unconfigured:
        await api.create_export(body, CFO, tenant_id=seed.a)
    assert unconfigured.value.status_code == 503
