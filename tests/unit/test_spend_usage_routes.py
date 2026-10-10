# SPDX-License-Identifier: Apache-2.0
"""Spend usage routes, jobs, partitions, the migration, the Celery tasks and the hooks into reference data."""

from __future__ import annotations

import asyncio
import importlib.util
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from api.deps import ActiveHumanAdmin
from api.v1 import spend as api
from core.config import settings
from core.models.spend_usage import SpendJob, SpendMeterGap, SpendUsageRecord, SpendUsageRollup
from core.ownership import Caller
from core.spend import fx, jobs, meter, partitions, rates, rollups
from core.spend.errors import SpendError
from tests.unit.spend_usage_fakes import ACTOR, T0, TENANT, install
from tests.unit.test_spend_usage import card, event, hints

TID = str(TENANT)
DAY = date(2026, 10, 1)
ADMIN = ActiveHumanAdmin(user_id=uuid.UUID(ACTOR), tenant_id=TENANT, email="admin@example.com", role="admin")
ADMIN_CALLER = Caller(user_id=uuid.UUID(ACTOR), role="admin", domains=None, is_admin=True, is_machine=False)
MIGRATION = Path("migrations/versions/v6_z80_spend_usage.py")
MODELS = (SpendUsageRecord, SpendUsageRollup, SpendMeterGap, SpendJob)


@pytest.fixture
def store(monkeypatch):
    return install(monkeypatch)


def _usage_calls():
    """One direct call of every PR B route."""
    job = uuid.uuid4()
    return [
        api.list_usage(DAY, DAY, caller=ADMIN_CALLER, tenant_id=TID),
        api.list_rollups(DAY, DAY, caller=ADMIN_CALLER, tenant_id=TID),
        api.spend_coverage(DAY, DAY, tenant_id=TID),
        api.ledger_comparison(DAY, DAY, caller=ADMIN_CALLER, tenant_id=TID),
        api.list_gaps(DAY, DAY, tenant_id=TID),
        api.rebuild_rollups(api.RebuildIn(start=DAY, end=DAY), ADMIN, tenant_id=TID),
        api.backfill_usage(api.BackfillIn(start=DAY, end=DAY), ADMIN, tenant_id=TID),
        api.restate_usage(
            api.RestateIn(provider="openai", start=DAY, end=DAY, reason="a long enough reason"), ADMIN, tenant_id=TID
        ),
        api.reattribute_usage(api.ReattributeIn(start=DAY, end=DAY), ADMIN, tenant_id=TID),
        api.settle_fx_rates(api.SettleIn(start=DAY, end=DAY), ADMIN, tenant_id=TID),
        api.recompute_commitments(api.RecomputeIn(), ADMIN, tenant_id=TID),
        api.list_jobs(tenant_id=TID),
        api.get_job(job, tenant_id=TID),
    ]


class TestRoutes:
    @pytest.mark.asyncio
    async def test_usage_routes_not_found_while_off(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        calls = _usage_calls()
        assert len(calls) == 13
        for call in calls:
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404 and info.value.detail["error"] == "spend_disabled"
        out = await api.spend_status(tenant_id=TID)
        assert out["enabled"] is False and out["writer"] == {"started": False, "pending": 0}

    @pytest.mark.asyncio
    async def test_usage_route_flow_while_on(self, store):
        store.add(card())
        await meter.write_events(store, TENANT, [event(), event(provider="mystery", model="m")], now=T0)
        listed = await api.list_usage(DAY, DAY, limit=1, caller=ADMIN_CALLER, tenant_id=TID)
        assert len(listed["items"]) == 1 and listed["next_cursor"]
        rest = await api.list_usage(DAY, DAY, cursor=listed["next_cursor"], caller=ADMIN_CALLER, tenant_id=TID)
        assert len(rest["items"]) == 1 and rest["next_cursor"] is None
        assert {listed["items"][0]["id"], rest["items"][0]["id"]} == {
            str(r.id) for r in store.of("spend_usage_records")
        }
        unpriced = await api.list_usage(DAY, DAY, unpriced=True, caller=ADMIN_CALLER, tenant_id=TID)
        assert [i["flags"] for i in unpriced["items"]] == [["unpriced"]]
        filtered = await api.list_usage(
            DAY, DAY, provider="openai", usage_type="llm_tokens", unattributed=True, caller=ADMIN_CALLER, tenant_id=TID
        )
        assert len(filtered["items"]) == 1 and filtered["items"][0]["price_source"] == "contract"
        assert (await api.list_usage(DAY, DAY, unattributed=False, caller=ADMIN_CALLER, tenant_id=TID))["items"] == []
        rolled = await api.list_rollups(
            DAY, DAY, group_by="provider", provider="openai", caller=ADMIN_CALLER, tenant_id=TID
        )
        assert [r["provider"] for r in rolled["rows"]] == ["openai"]
        coverage = await api.spend_coverage(DAY, DAY, tenant_id=TID)
        assert coverage["period"]["records"] == 2
        assert (await api.ledger_comparison(DAY, DAY, caller=ADMIN_CALLER, tenant_id=TID))["days"][0]["usage"][
            "calls"
        ] == 2
        await meter.upsert_gaps(store, TENANT, {(DAY, "llm_tokens", "queue_full", ""): 2})
        gaps = await api.list_gaps(DAY, DAY, tenant_id=TID)
        assert gaps["items"] == [
            {"day": "2026-10-01", "usage_type": "llm_tokens", "reason": "queue_full", "detail": "", "count": 2}
        ]
        with pytest.raises(HTTPException) as info:
            await api.list_usage(DAY, DAY + timedelta(days=31), caller=ADMIN_CALLER, tenant_id=TID)
        assert info.value.status_code == 422 and info.value.detail["error"] == "range_too_long"
        with pytest.raises(HTTPException) as info:
            await api.list_usage(DAY, DAY, cursor="nonsense", caller=ADMIN_CALLER, tenant_id=TID)
        assert info.value.status_code == 422
        status = await api.spend_status(tenant_id=TID)
        assert status["partition_horizon"] == {"last_month": "2028-12", "months_ahead": 26, "low": False}

    @pytest.mark.asyncio
    async def test_usage_reads_filter_personal_agents_and_redact_user_ids(self, store):
        from core.models.agent import Agent

        owner = uuid.uuid4()
        personal = uuid.uuid4()
        store.add(
            Agent(
                id=personal,
                tenant_id=TENANT,
                name="p",
                agent_type="t",
                domain="finance",
                visibility="personal",
                owner_user_id=owner,
            )
        )
        store.agents[str(personal)] = ("1", "t", "active", None, None, None, None, None)
        user = str(uuid.uuid4())
        await meter.write_events(
            store,
            TENANT,
            [
                event(hints=hints(agent_id=str(personal), initiating_user_id=user)),
                event(hints=hints(initiating_user_id=user)),
            ],
            now=T0,
        )
        admin = await api.list_usage(DAY, DAY, caller=ADMIN_CALLER, tenant_id=TID)
        assert len(admin["items"]) == 2 and {i["initiating_user_id"] for i in admin["items"]} == {user}
        domain_head = Caller(user_id=uuid.uuid4(), role="cfo", domains=["finance"], is_admin=False, is_machine=False)
        theirs = await api.list_usage(DAY, DAY, caller=domain_head, tenant_id=TID)
        assert len(theirs["items"]) == 1 and theirs["items"][0]["agent_id"] is None
        assert theirs["items"][0]["initiating_user_id"] is None  # user ids are for administrators and auditors
        owner_view = Caller(user_id=owner, role="cfo", domains=["finance"], is_admin=False, is_machine=False)
        assert len((await api.list_usage(DAY, DAY, caller=owner_view, tenant_id=TID))["items"]) == 2

    @pytest.mark.asyncio
    async def test_job_routes_answer_202_with_a_job_id(self, store):
        rebuild = await api.rebuild_rollups(api.RebuildIn(start=DAY, end=DAY), ADMIN, tenant_id=TID)
        assert rebuild["status"] == "queued" and uuid.UUID(rebuild["job_id"])
        with pytest.raises(HTTPException) as info:
            await api.rebuild_rollups(api.RebuildIn(start=DAY, end=DAY), ADMIN, tenant_id=TID)
        assert info.value.status_code == 409 and info.value.detail["error"] == "job_running"
        with pytest.raises(HTTPException) as info:
            await api.rebuild_rollups(api.RebuildIn(start=DAY, end=DAY + timedelta(days=40)), ADMIN, tenant_id=TID)
        assert info.value.status_code == 422
        restate = await api.restate_usage(
            api.RestateIn(provider="GPT", start=DAY, end=DAY, card_ids=[uuid.uuid4()], reason="A corrected contract"),
            ADMIN,
            tenant_id=TID,
        )
        job = next(r for r in store.of("spend_jobs") if str(r.id) == restate["job_id"])
        assert job.params["provider"] == "openai" and job.params["include_unpriced"] is True
        for call in (
            api.backfill_usage(api.BackfillIn(start=DAY, end=DAY), ADMIN, tenant_id=TID),
            api.reattribute_usage(api.ReattributeIn(start=DAY, end=DAY), ADMIN, tenant_id=TID),
            api.settle_fx_rates(api.SettleIn(start=DAY, end=DAY), ADMIN, tenant_id=TID),
            api.recompute_commitments(api.RecomputeIn(provider="openai"), ADMIN, tenant_id=TID),
        ):
            assert (await call)["status"] == "queued"
        listed = await api.list_jobs(tenant_id=TID)
        assert len(listed["items"]) == 6
        assert (await api.list_jobs(kind="rebuild", tenant_id=TID))["items"][0]["kind"] == "rebuild"
        one = await api.get_job(uuid.UUID(rebuild["job_id"]), tenant_id=TID)
        assert one["params"] == {"start": "2026-10-01", "end": "2026-10-01"} and one["requested_by"] == ACTOR
        with pytest.raises(HTTPException) as info:
            await api.get_job(uuid.uuid4(), tenant_id=TID)
        assert info.value.status_code == 404
        audits = [r.event_type for r in store.of("audit_log")]
        assert audits.count("spend.job.enqueue") == 6

    def test_routes_are_registered_with_their_scopes(self):
        from api.main import app
        from api.route_metadata import ROUTE_METADATA_ATTR

        paths = set(app.openapi()["paths"])
        for path in (
            "/api/v1/spend/usage",
            "/api/v1/spend/rollups",
            "/api/v1/spend/coverage",
            "/api/v1/spend/coverage/ledgers",
            "/api/v1/spend/gaps",
            "/api/v1/spend/rollups/rebuild",
            "/api/v1/spend/usage/backfill",
            "/api/v1/spend/usage/restate",
            "/api/v1/spend/usage/reattribute",
            "/api/v1/spend/fx-rates/settle",
            "/api/v1/spend/commitments/recompute",
            "/api/v1/spend/jobs",
            "/api/v1/spend/jobs/{job_id}",
        ):
            assert path in paths, path
        scopes = {r.path: getattr(r.endpoint, ROUTE_METADATA_ATTR)["scope"] for r in api.router.routes}
        assert scopes["/spend/usage"] == "spend.usage.read" and scopes["/spend/jobs"] == "spend.jobs.read"
        assert scopes["/spend/rollups/rebuild"] == "spend.rollups.sensitive.write"
        assert scopes["/spend/fx-rates/settle"] == "spend.fx.sensitive.write"

    def test_bodies_are_bounded(self):
        from pydantic import ValidationError

        for bad in (
            {"provider": "", "start": DAY, "end": DAY, "reason": "x" * 20},
            {"provider": "p", "start": DAY, "end": DAY, "reason": "short"},
            {"provider": "p", "start": DAY, "end": DAY, "reason": "x" * 20, "card_ids": [str(uuid.uuid4())] * 51},
            {"provider": "p", "start": DAY, "end": DAY, "reason": "x" * 20, "extra": 1},
        ):
            with pytest.raises(ValidationError):
                api.RestateIn(**bad)
        with pytest.raises(ValidationError):
            api.RebuildIn(start=DAY, end=DAY, extra=1)


# ---------------------------------------------------------------- jobs


class TestJobs:
    @pytest.mark.asyncio
    async def test_job_enqueue_refuses_a_second_active_job_of_the_kind(self, store):
        first = await jobs.enqueue(TENANT, kind="rebuild", params={"start": DAY, "end": DAY}, actor=ACTOR)
        with pytest.raises(SpendError) as info:
            await jobs.enqueue(TENANT, kind="rebuild", params={}, actor=ACTOR)
        assert info.value.code == "job_running" and first["job_id"] in info.value.message
        other = await jobs.enqueue(TENANT, kind="reattribute", params={}, actor="")
        assert other["status"] == "queued"
        assert next(r for r in store.of("spend_jobs") if str(r.id) == other["job_id"]).requested_by == jobs.SYSTEM_ACTOR
        followup = await jobs.enqueue_followup(TENANT, kind="rebuild", params={}, actor=ACTOR)
        assert followup == {"job_id": first["job_id"], "status": "queued", "kind": "rebuild", "coalesced": True}
        assert await jobs.enqueue_followup(TENANT, kind="nonsense", params={}, actor=ACTOR) is None

    @pytest.mark.asyncio
    async def test_dispatch_failure_fails_the_job_so_the_kind_is_not_blocked(self, store, monkeypatch):
        def broken(tenant_id, job_id):
            raise ConnectionError("broker down")

        monkeypatch.setattr(jobs, "_dispatch", broken)
        out = await jobs.enqueue(TENANT, kind="rebuild", params={}, actor=ACTOR)
        assert out["status"] == "failed"
        row = store.of("spend_jobs")[0]
        assert row.status == "failed" and row.error_code == "dispatch_failed"
        monkeypatch.setattr(jobs, "_dispatch", lambda tenant_id, job_id: None)
        assert (await jobs.enqueue(TENANT, kind="rebuild", params={}, actor=ACTOR))["status"] == "queued"

    @pytest.mark.asyncio
    async def test_job_claim_takes_over_a_stale_running_job(self, store):
        queued = await jobs.enqueue(
            TENANT, kind="rebuild", params={"start": "2026-10-01", "end": "2026-10-01"}, actor=ACTOR
        )
        job_id = uuid.UUID(queued["job_id"])
        row = store.of("spend_jobs")[0]
        row.status, row.started_at = "running", datetime(2026, 10, 1, 11, 30, tzinfo=UTC)  # 30 minutes ago
        assert (await jobs.run(TENANT, job_id))["skipped"] == "not_claimable"
        row.started_at = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)  # three hours ago: the worker was lost
        done = await jobs.run(TENANT, job_id, now=T0)
        assert done["status"] == "succeeded" and row.status == "succeeded" and row.result["days"] == 1
        assert (await jobs.run(TENANT, job_id))["skipped"] == "not_claimable"

    @pytest.mark.asyncio
    async def test_job_failure_stores_error_code_only(self, store, monkeypatch):
        from core.spend import maintenance

        async def broken(*args, **kwargs):
            raise RuntimeError("secret detail that must not be stored")

        monkeypatch.setattr(maintenance, "reattribute", broken)
        queued = await jobs.enqueue(
            TENANT, kind="reattribute", params={"start": "2026-10-01", "end": "2026-10-01"}, actor=ACTOR
        )
        out = await jobs.run(TENANT, uuid.UUID(queued["job_id"]))
        row = store.of("spend_jobs")[0]
        assert out["status"] == "failed" and row.error_code == "RuntimeError" and "secret" not in str(row.result)

    @pytest.mark.asyncio
    async def test_each_kind_dispatches_to_its_job(self, store, monkeypatch):
        from core.spend import commitments, ledgers, maintenance

        seen: list[str] = []

        def recorder(name):
            async def run(*args, **kwargs):
                seen.append(name)
                return {"ok": name}

            return run

        monkeypatch.setattr(ledgers, "backfill_model_calls", recorder("backfill"))
        monkeypatch.setattr(maintenance, "restate", recorder("restate"))
        monkeypatch.setattr(maintenance, "settle_fx", recorder("settle_fx"))
        monkeypatch.setattr(commitments, "recompute", recorder("recompute_commitments"))
        span = {"start": "2026-10-01", "end": "2026-10-01"}
        await jobs._execute(TENANT, "backfill", span, ACTOR, T0)
        await jobs._execute(
            TENANT, "restate", {**span, "provider": "openai", "card_ids": [str(uuid.uuid4())]}, ACTOR, T0
        )
        await jobs._execute(TENANT, "settle_fx", {**span, "force_dates": [["USD", "2026-10-01"]]}, ACTOR, T0)
        await jobs._execute(TENANT, "recompute_commitments", {}, ACTOR, T0)
        assert seen == ["backfill", "restate", "settle_fx", "recompute_commitments"]
        with pytest.raises(SpendError):
            await jobs._execute(TENANT, "nonsense", {}, ACTOR, T0)


# ---------------------------------------------------------------- partitions and the migration


def _migration():
    spec = importlib.util.spec_from_file_location("_v6_z80_spend_usage", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Op:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, sql):
        self.sql.append(" ".join(str(sql).split()))


def _strip_ws(text: str) -> str:
    return re.sub(r"\s+", "", text)


class TestPartitionsAndMigration:
    def test_static_partitions_cover_thirty_months_and_have_policies(self, monkeypatch):
        assert len(partitions.STATIC_MONTHS) == 30 and partitions.STATIC_MONTHS[0] == (2026, 7)
        assert partitions.STATIC_MONTHS[-1] == (2028, 12) and len(partitions.STATIC_PARTITIONS) == 31
        assert partitions.partition_name(2026, 7) == "spend_usage_records_y2026m07"
        assert "TO ('2027-01-01 00:00:00+00')" in partitions.partition_ddl(2026, 12)[0]
        migration = _migration()
        op = _Op()
        monkeypatch.setattr(migration, "op", op)
        migration.upgrade()
        created = [s for s in op.sql if "PARTITION OF spend_usage_records" in s]
        assert len(created) == 31 and created[0] == partitions.partition_ddl(2026, 7)[0]
        assert (
            created[-1]
            == "CREATE TABLE IF NOT EXISTS spend_usage_records_default PARTITION OF spend_usage_records DEFAULT;"
        )
        for name in (*partitions.STATIC_PARTITIONS, *migration.TABLES):
            assert f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY;" in op.sql, name
            assert any(s.startswith(f"CREATE POLICY {name}_tenant_isolation") for s in op.sql), name
        assert "PARTITION BY RANGE (event_time)" in " ".join(op.sql)
        assert not any(s.startswith("ALTER TABLE") and "ROW LEVEL" not in s for s in op.sql)

    @pytest.mark.asyncio
    async def test_partition_horizon_reports_months_ahead(self):
        assert await partitions.horizon(datetime(2026, 10, 1, tzinfo=UTC)) == {
            "last_month": "2028-12", "months_ahead": 26, "low": False,
        }  # fmt: skip
        assert (await partitions.horizon(datetime(2028, 8, 1, tzinfo=UTC)))["low"] is True
        assert partitions.months_ahead(datetime(2029, 3, 1, tzinfo=UTC)) == 0
        assert partitions.months_ahead() >= 0

    def test_migration_v6z80_chain_partitioning_rls_and_fk_indexes(self, monkeypatch):
        migration = _migration()
        assert migration.revision == "v6z80_spend_usage" and len(migration.revision) <= 32
        assert migration.down_revision == "v6z79_spend_reference"
        assert migration.TABLES == tuple(model.__tablename__ for model in MODELS)
        assert migration._months() == list(partitions.STATIC_MONTHS)
        for model in MODELS:
            table = model.__table__
            leading = [tuple(c.name for c in index.columns) for index in table.indexes]
            for fk in table.foreign_key_constraints:
                columns = tuple(c.name for c in fk.columns)
                assert columns[0] == "tenant_id" and fk.ondelete == "RESTRICT"
                assert any(cols[: len(columns)] == columns for cols in leading), (table.name, columns)
        assert SpendUsageRecord.__table__.dialect_options["postgresql"]["partition_by"] == "RANGE (event_time)"
        down = _Op()
        monkeypatch.setattr(migration, "op", down)
        migration.downgrade()
        assert down.sql == [f"DROP TABLE IF EXISTS {t};" for t in reversed(migration.TABLES)]

    def test_models_compile_to_the_migration_ddl(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        flat = _strip_ws(sql.replace('"\n        "', "").replace('" "', ""))
        ddl = ""
        for model in MODELS:
            ddl += str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
            for index in model.__table__.indexes:
                ddl += str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        flat_ddl = _strip_ws(ddl)
        names = set(re.findall(r"CONSTRAINT (\w+)", sql)) | set(re.findall(r"INDEX IF NOT EXISTS (\w+)", sql))
        assert len(names) > 25
        for name in names:
            assert name in ddl, name
        for match in re.finditer(r"(?<!WITH )CHECK \(", sql):
            depth, start, index = 1, match.end(), match.end()
            while depth:
                depth += {"(": 1, ")": -1}.get(sql[index], 0)
                index += 1
            body = _strip_ws(sql[start : index - 1])
            assert f"CHECK({body})" in flat_ddl, body
        for predicate in (
            "rate_card_id IS NOT NULL",
            "org_node_id IS NOT NULL",
            "fx_estimated OR unconverted",
            "status IN ('queued','running')",
        ):
            assert f"WHERE {predicate}" in ddl, predicate
        for model in MODELS:
            table_sql = sql[sql.index(f"CREATE TABLE IF NOT EXISTS {model.__tablename__} (") :]
            table_sql = table_sql[: table_sql.index(");")]
            for column in model.__table__.columns:
                assert re.search(rf"\b{column.name} ", table_sql), (model.__tablename__, column.name)
            for name, _default in re.findall(r"(\w+) [A-Z(),0-9 ]+? NOT NULL DEFAULT ([^,\n]+)", table_sql):
                assert model.__table__.c[name].server_default is not None, (model.__tablename__, name)
        assert str(SpendUsageRecord.__table__.c.correlation_ref.type) == "CHAR(32)"
        assert "pk_spend_usage_records" in flat and "PARTITIONBYRANGE(event_time)" in flat_ddl.replace(")\n", ")")

    def test_rollup_fillfactor_is_set_on_the_orm_table_too(self):
        from core.models import spend_usage

        assert "fillfactor = 70" in str(spend_usage.ROLLUP_FILLFACTOR_DDL.statement)
        assert "WITH (fillfactor = 70)" in MIGRATION.read_text(encoding="utf-8")

    def test_every_tenant_table_is_named_by_an_rls_migration(self):
        from tests.unit.test_rls_tenant_coverage import _rls_tables_declared_in_migrations

        assert {model.__tablename__ for model in MODELS} <= _rls_tables_declared_in_migrations()

    def test_drift_allowlist_lists_the_partitions(self):
        from tests.integration.alembic_schema_drift_allowlist import MIGRATION_OWNED_TABLES

        assert set(partitions.STATIC_PARTITIONS) <= set(MIGRATION_OWNED_TABLES)


# ---------------------------------------------------------------- Celery


class TestTasks:
    def test_spend_tasks_registered_and_scheduled(self):
        from core.tasks.celery_app import app

        app.loader.import_default_modules()
        for name in ("persist_usage", "run_job", "check_partitions", "settle_fx_daily", "recompute_commitments"):
            assert f"core.tasks.spend_tasks.{name}" in app.tasks, name
        assert "core.tasks.spend_tasks" in app.conf.include
        beat = app.conf.beat_schedule
        assert beat["spend-check-partitions"]["task"] == "core.tasks.spend_tasks.check_partitions"
        assert beat["spend-settle-fx"]["task"] == "core.tasks.spend_tasks.settle_fx_daily"
        assert beat["spend-recompute-commitments"]["schedule"] == 900.0
        assert {
            beat[k]["options"]["queue"]
            for k in ("spend-check-partitions", "spend-settle-fx", "spend-recompute-commitments")
        } == {"maintenance"}

    def test_tasks_skip_while_off_and_when_sweeps_are_off(self, monkeypatch):
        import core.tasks.spend_tasks as tasks

        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        for task in (tasks.check_partitions, tasks.settle_fx_daily, tasks.recompute_commitments):
            assert task.run() == {"skipped": "spend_intelligence_disabled"}
        assert tasks.run_job.run(TID, str(uuid.uuid4())) == {"skipped": "spend_intelligence_disabled"}
        monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
        monkeypatch.setattr(settings, "spend_sweeps_enabled", False)
        for task in (tasks.check_partitions, tasks.settle_fx_daily, tasks.recompute_commitments):
            assert task.run() == {"skipped": "spend_sweeps_disabled"}

    def test_beat_bodies_queue_jobs_per_tenant(self, store, monkeypatch):
        import core.tasks.spend_tasks as tasks
        from core.models.spend import SpendCommitment
        from core.spend import tenants

        async def two_tenants():
            return [TENANT, uuid.UUID("99999999-9999-4999-8999-999999999999")]

        monkeypatch.setattr(tenants, "active_tenant_ids", two_tenants)
        monkeypatch.setattr(tasks, "run_async", _run)
        monkeypatch.setattr(settings, "spend_sweeps_enabled", True)
        assert tasks.check_partitions.run()["months_ahead"] == 26
        assert tasks.settle_fx_daily.run() == {"queued": 0}
        store.add(card(currency="EUR"))
        _run(meter.write_events(store, TENANT, [event()], now=T0))  # unconverted: pending FX
        assert tasks.settle_fx_daily.run() == {"queued": 1}  # only the tenant with pending records
        assert tasks.recompute_commitments.run() == {"queued": 0}
        store.add(
            SpendCommitment(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                provider="openai",
                kind="money",
                committed_amount=Decimal(1),
                currency="USD",
                period_start=DAY,
                period_end=date(2026, 11, 1),
                status="active",
            )
        )
        assert tasks.recompute_commitments.run() == {"queued": 1}  # only the tenant with a commitment
        assert tasks.recompute_commitments.run() == {"queued": 0}  # already queued: folded in

    def test_beat_isolates_a_failing_tenant(self, store, monkeypatch):
        import core.tasks.spend_tasks as tasks
        from core.spend import tenants

        async def one_tenant():
            return [TENANT]

        def broken(tenant_id):
            raise RuntimeError("tenant session down")

        import core.database

        monkeypatch.setattr(tenants, "active_tenant_ids", one_tenant)
        monkeypatch.setattr(tasks, "run_async", _run)
        monkeypatch.setattr(core.database, "get_tenant_session", broken)
        assert tasks.settle_fx_daily.run() == {"queued": 0}
        assert tasks.recompute_commitments.run() == {"queued": 0}

    def test_run_job_task_runs_the_job(self, store, monkeypatch):
        import core.tasks.spend_tasks as tasks

        monkeypatch.setattr(tasks, "run_async", _run)
        queued = _run(
            jobs.enqueue(TENANT, kind="rebuild", params={"start": "2026-10-01", "end": "2026-10-01"}, actor=ACTOR)
        )
        assert tasks.run_job.run(TID, queued["job_id"])["status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_active_tenants_skip_deleted_ones(self, monkeypatch):
        import core.database
        from core.models.tenant import Tenant
        from core.spend import tenants
        from tests.unit.spend_usage_fakes import UsageSession

        session = UsageSession()
        live, gone = uuid.uuid4(), uuid.uuid4()
        session.add(Tenant(id=live, name="a", slug="a", deleted_at=None))
        session.add(Tenant(id=gone, name="b", slug="b", deleted_at=T0))
        monkeypatch.setattr(core.database, "async_session_factory", lambda: session)
        assert await tenants.active_tenant_ids() == [live]


def _run(awaitable):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(awaitable)
    finally:
        loop.close()


# ---------------------------------------------------------------- hooks into the reference data


class TestReferenceHooks:
    @pytest.mark.asyncio
    async def test_card_in_use_reads_direct_and_blend_references(self, store):
        priced = card()
        output = card(unit="1m_output_tokens", unit_price=Decimal("10"))
        store.add(priced)
        store.add(output)
        assert await rates.card_in_use(store, TENANT, priced.id) is None
        await meter.write_events(store, TENANT, [event()], now=T0)
        assert await rates.card_in_use(store, TENANT, priced.id) == DAY
        assert await rates.card_in_use(store, TENANT, output.id) is None
        blend_input = card(model_sku="blend-model")
        blend_output = card(model_sku="blend-model", unit="1m_output_tokens", effective_to=date(2027, 1, 1))
        store.add(blend_input)
        store.add(blend_output)
        await meter.write_events(
            store, TENANT, [event(unit="token", model="blend-model", event_time=T0 + timedelta(days=2))], now=T0
        )
        assert await rates.card_in_use(store, TENANT, blend_output.id) == date(2026, 10, 3)

    @pytest.mark.asyncio
    async def test_correction_of_a_used_card_queues_its_restatement(self, store):
        used = card(effective_to=date(2026, 12, 1))
        store.add(used)
        await meter.write_events(store, TENANT, [event()], now=T0)
        out = await rates.correct_card(
            TENANT, used.id, {"unit_price": "3"}, reason="Contract price was wrong", actor=ACTOR, now=T0
        )
        job = next(r for r in store.of("spend_jobs") if str(r.id) == out["restate_job_id"])
        assert job.kind == "restate" and job.params["card_ids"] == [str(used.id)]
        assert (job.params["start"], job.params["end"]) == ("2026-01-01", "2026-10-01")
        assert job.params["reason"] == "Contract price was wrong"

    @pytest.mark.asyncio
    async def test_a_queued_correction_restatement_runs_over_its_whole_range(self, store):
        """A card in force since January: the restatement covers nine months, past the route's 92 days."""
        used = card()
        store.add(used)
        await meter.write_events(store, TENANT, [event()], now=T0)
        out = await rates.correct_card(
            TENANT, used.id, {"unit_price": "3"}, reason="Contract price was wrong", actor=ACTOR, now=T0
        )
        done = await jobs.run(TENANT, uuid.UUID(out["restate_job_id"]), now=T0)
        assert done["status"] == "succeeded" and done["result"]["changed"] == 1 and done["result"]["days"] == 274
        record = store.of("spend_usage_records")[0]
        assert record.rate_card_id == uuid.UUID(out["card"]["id"]) and record.amount == Decimal("0.0030000000")
        with pytest.raises(HTTPException) as info:
            await api.restate_usage(
                api.RestateIn(
                    provider="openai", start=DAY, end=DAY + timedelta(days=92), reason="a long enough reason"
                ),
                ADMIN,
                tenant_id=TID,
            )
        assert info.value.status_code == 422 and info.value.detail["error"] == "range_too_long"

    @pytest.mark.asyncio
    async def test_moving_effective_to_over_priced_records_queues_a_restatement(self, store):
        used = card()
        store.add(used)
        await meter.write_events(store, TENANT, [event()], now=T0)
        with pytest.raises(SpendError) as info:
            await rates.update_card(TENANT, used.id, {"effective_to": "2026-09-15"}, actor=ACTOR, now=T0)
        assert info.value.code == "restate_required"
        out = await rates.update_card(
            TENANT, used.id, {"effective_to": "2026-09-15", "restate": True}, actor=ACTOR, now=T0
        )
        job = next(r for r in store.of("spend_jobs") if str(r.id) == out["restate_job_id"])
        assert (job.params["start"], job.params["end"]) == ("2026-09-15", "2026-10-01")

    @pytest.mark.asyncio
    async def test_rate_card_import_queues_one_restatement_per_provider(self, store):
        old = card(source="list", effective_from=date(2026, 1, 1))
        store.add(old)
        await meter.write_events(store, TENANT, [event()], now=T0)
        rows = [
            {"provider": "openai", "usage_type": "llm_tokens", "model_sku": "gpt-4o", "unit": "1m_input_tokens",
             "unit_price": "3", "currency": "USD", "effective_from": "2026-09-01", "source": "list",
             "supersede": "true", "restate": "true"},
        ]  # fmt: skip
        report = await rates.import_cards(TENANT, rows, actor=ACTOR, dry_run=False, file_sha256="0" * 64, now=T0)
        assert report["created"] == 1 and report["restate_jobs"][0]["provider"] == "openai"
        assert rates._merge_plans([("p", DAY, DAY, old.id), ("p", date(2026, 9, 1), DAY, old.id)])[0][0][1] == date(
            2026, 9, 1
        )

    @pytest.mark.asyncio
    async def test_fx_rate_in_use_needs_restate_and_reconverts(self, store):
        store.add(card())
        await fx.put_rate(
            TENANT, {"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "83"}, actor=ACTOR, now=T0
        )
        await meter.write_events(store, TENANT, [event()], now=T0)
        assert not store.of("spend_usage_records")[0].fx_estimated
        with pytest.raises(SpendError) as info:
            await fx.put_rate(
                TENANT, {"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "84"}, actor=ACTOR, now=T0
            )
        assert info.value.code == "fx_in_use"
        for job in store.of("spend_jobs"):
            job.status = "succeeded"
        out = await fx.put_rate(
            TENANT,
            {"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "84", "restate": True},
            actor=ACTOR,
            now=T0,
        )
        job = next(r for r in store.of("spend_jobs") if str(r.id) == out["settle_job_id"])
        assert job.params["force_dates"] == [["USD", "2026-10-01"]]
        assert (job.params["start"], job.params["end"]) == ("2026-10-01", "2026-11-01")
        report = await fx.import_rates(
            TENANT, [{"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "85"}], actor=ACTOR, dry_run=False,
            file_sha256="0" * 64, now=T0,
        )  # fmt: skip
        assert report["rejected"] == [{"row": 2, "key": "USD:2026-10-01", "reason": "fx_in_use"}]
        later = await fx.import_rates(
            TENANT, [{"rate_date": "2026-10-05", "currency": "USD", "rate_to_inr": "85"}], actor=ACTOR, dry_run=False,
            file_sha256="0" * 64, now=T0,
        )  # fmt: skip
        assert later["created"] == 1 and later["settle_job_id"]
        window = await fx.settle_window(store, TENANT, "USD", date(2026, 10, 1))
        assert window == (date(2026, 10, 1), date(2026, 10, 4))

    @pytest.mark.asyncio
    async def test_commitment_changes_queue_a_recompute(self, store):
        from core.spend import commitments

        body = {"provider": "openai", "kind": "money", "committed_amount": "100", "currency": "USD",
                "period_start": "2026-10-01", "period_end": "2026-11-01"}  # fmt: skip
        created = await commitments.create_commitment(TENANT, body, actor=ACTOR, now=T0)
        job = store.of("spend_jobs")[0]
        assert job.kind == "recompute_commitments" and job.params == {"provider": "openai"}
        job.status = "succeeded"
        await commitments.update_commitment(
            TENANT, uuid.UUID(created["id"]), {"reference": "PO-1"}, actor=ACTOR, now=T0
        )
        assert len([j for j in store.of("spend_jobs") if j.kind == "recompute_commitments"]) == 2


def test_rollups_constants():
    assert rollups.MAX_USAGE_DAYS == 31 and rollups.MAX_GAP_DAYS == 92
