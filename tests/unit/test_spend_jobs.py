# SPDX-License-Identifier: Apache-2.0
"""Spend maintenance jobs: follow-up work merged or queued (never dropped), one running job per kind,
the heartbeat and the sweep, and retries of transient failures."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from core.config import settings
from core.spend import fx, jobs, locks, maintenance, meter, rates
from core.spend.errors import retryable
from tests.unit.spend_usage_fakes import ACTOR, NOW, T0, TENANT, install
from tests.unit.test_spend_usage import card, event

DAY = date(2026, 10, 1)
SPAN = {"start": "2026-10-01", "end": "2026-10-01"}


@pytest.fixture
def store(monkeypatch):
    return install(monkeypatch)


@pytest.fixture
def sent(monkeypatch):
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(jobs, "_dispatch", lambda tenant_id, job_id, **options: calls.append((str(job_id), options)))
    return calls


def job(store, job_id):
    return next(r for r in store.of("spend_jobs") if str(r.id) == str(job_id))


class _LockedError(Exception):
    sqlstate = "55P03"  # lock_not_available: what a 30 s lock timeout raises


def lock_timeout() -> DBAPIError:
    return DBAPIError("UPDATE spend_usage_records", {}, _LockedError("lock timeout"))


# ---------------------------------------------------------------- merging follow-up work


class TestMerge:
    def test_merge_params_cover_both_jobs_or_refuse(self):
        a = {"provider": "openai", "start": "2026-10-01", "end": "2026-10-05", "card_ids": ["c1"],
             "include_unpriced": True, "reason": "first change"}  # fmt: skip
        b = {"provider": "openai", "start": "2026-09-20", "end": "2026-10-02", "card_ids": ["c2"],
             "include_unpriced": False, "reason": "second change"}  # fmt: skip
        assert jobs.merge_params("restate", a, b) == {
            "provider": "openai", "start": "2026-09-20", "end": "2026-10-05", "card_ids": ["c1", "c2"],
            "include_unpriced": True, "reason": "first change; second change",
        }  # fmt: skip
        everything = {**b, "card_ids": [], "include_unpriced": False}  # every record of the provider
        merged = jobs.merge_params("restate", a, everything)
        assert merged["card_ids"] == [] and merged["include_unpriced"] is False
        assert jobs.merge_params("restate", a, {**b, "provider": "anthropic"}) is None
        assert jobs.merge_params("restate", a, {**b, "start": "2016-01-01"}) is None  # longer than a job runs
        assert jobs.merge_params("restate", a, a)["reason"] == "first change"
        assert len(jobs._reasons("x" * 400, "y" * 400)) == 500
        settle = jobs.merge_params(
            "settle_fx",
            {"start": "2026-10-01", "end": "2026-10-31", "force_dates": [["USD", "2026-10-01"]]},
            {"start": "2026-09-01", "end": "2026-09-30", "force_dates": [["EUR", "2026-09-02"], ["USD", "2026-10-01"]]},
        )
        assert settle == {
            "start": "2026-09-01", "end": "2026-10-31",
            "force_dates": [["EUR", "2026-09-02"], ["USD", "2026-10-01"]],
        }  # fmt: skip
        assert (
            jobs.merge_params("settle_fx", {**SPAN, "force_dates": []}, {"start": "2016-01-01", "end": "2016-01-01"})
            is None
        )
        recompute = jobs.merge_params
        assert recompute("recompute_commitments", {"provider": "openai"}, {"provider": "openai"}) == {
            "provider": "openai"
        }
        assert recompute("recompute_commitments", {"provider": "openai"}, {"provider": "anthropic"}) == {}
        assert recompute("recompute_commitments", {}, {"provider": "openai"}) == {}
        assert jobs.merge_params("rebuild", SPAN, dict(SPAN)) == SPAN
        assert jobs.merge_params("rebuild", SPAN, {"start": "2026-10-02", "end": "2026-10-02"}) is None

    @pytest.mark.asyncio
    async def test_a_second_correction_is_merged_into_the_queued_restatement_and_both_are_restated(self, store):
        """The finding: a second correction while the first restatement waited got the first job's id, and the
        records of the second card were never repriced."""
        inp = card(effective_to=date(2026, 12, 1))
        out = card(unit="1m_output_tokens", unit_price=Decimal("10"), effective_to=date(2026, 12, 1))
        store.add(inp)
        store.add(out)
        await meter.write_events(store, TENANT, [event(), event(unit="output_token", calls=0)], now=T0)
        first = await rates.correct_card(
            TENANT, inp.id, {"unit_price": "3"}, reason="Input price was wrong", actor=ACTOR, now=T0
        )
        second = await rates.correct_card(
            TENANT, out.id, {"unit_price": "12"}, reason="Output price was wrong", actor=ACTOR, now=T0
        )
        assert second["restate_job_id"] == first["restate_job_id"]
        row = job(store, first["restate_job_id"])
        assert row.params["card_ids"] == sorted([str(inp.id), str(out.id)])
        assert row.params["reason"] == "Input price was wrong; Output price was wrong"
        merges = [r for r in store.of("audit_log") if r.event_type == "spend.job.merge"]
        assert len(merges) == 1 and merges[0].details["before"]["card_ids"] == [str(inp.id)]
        assert merges[0].details["after"] == row.params
        done = await jobs.run(TENANT, row.id, now=T0)
        assert done["status"] == "succeeded" and done["result"]["changed"] == 2
        replacements = {uuid.UUID(first["card"]["id"]), uuid.UUID(second["card"]["id"])}
        assert {r.rate_card_id for r in store.of("spend_usage_records")} == replacements

    @pytest.mark.asyncio
    async def test_a_correction_while_a_restatement_runs_is_queued_and_sent_when_it_ends(
        self, store, sent, monkeypatch
    ):
        inp = card(effective_to=date(2026, 12, 1))
        out = card(unit="1m_output_tokens", unit_price=Decimal("10"), effective_to=date(2026, 12, 1))
        store.add(inp)
        store.add(out)
        await meter.write_events(store, TENANT, [event(), event(unit="output_token", calls=0)], now=T0)
        real_restate = maintenance.restate
        during: dict = {}

        async def restate_with_a_correction_meanwhile(*args, **kwargs):
            during["second"] = await rates.correct_card(
                TENANT, out.id, {"unit_price": "12"}, reason="Output price was wrong", actor=ACTOR, now=T0
            )
            during["claim"] = await jobs.run(TENANT, uuid.UUID(during["second"]["restate_job_id"]), now=T0)
            return await real_restate(*args, **kwargs)

        monkeypatch.setattr(maintenance, "restate", restate_with_a_correction_meanwhile)
        first = await rates.correct_card(
            TENANT, inp.id, {"unit_price": "3"}, reason="Input price was wrong", actor=ACTOR, now=T0
        )
        sent.clear()
        done = await jobs.run(TENANT, uuid.UUID(first["restate_job_id"]), now=T0)
        second_id = during["second"]["restate_job_id"]
        assert done["status"] == "succeeded" and second_id != first["restate_job_id"]
        assert during["claim"]["skipped"] == "not_claimable"  # one restatement runs at a time
        assert job(store, second_id).params["card_ids"] == [str(out.id)]
        assert (second_id, {}) in sent[-1:]  # the first job's end sent the next one
        monkeypatch.setattr(maintenance, "restate", real_restate)
        assert (await jobs.run(TENANT, uuid.UUID(second_id), now=T0))["status"] == "succeeded"
        replacements = {uuid.UUID(first["card"]["id"]), uuid.UUID(during["second"]["card"]["id"])}
        assert {r.rate_card_id for r in store.of("spend_usage_records")} == replacements

    @pytest.mark.asyncio
    async def test_a_forced_fx_date_is_merged_into_the_queued_settlement(self, store):
        store.add(card())
        created = await fx.put_rate(
            TENANT, {"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "83"}, actor=ACTOR, now=T0
        )
        await meter.write_events(store, TENANT, [event()], now=T0)
        record = store.of("spend_usage_records")[0]
        assert record.fx_rate == Decimal("83") and not record.fx_estimated
        corrected = await fx.put_rate(
            TENANT,
            {"rate_date": "2026-10-01", "currency": "USD", "rate_to_inr": "84", "restate": True},
            actor=ACTOR,
            now=T0,
        )
        assert corrected["settle_job_id"] == created["settle_job_id"]
        row = job(store, created["settle_job_id"])
        assert row.params["force_dates"] == [["USD", "2026-10-01"]]
        assert (await jobs.run(TENANT, row.id, now=T0))["status"] == "succeeded"
        assert record.fx_rate == Decimal("84") and record.amount_inr == Decimal("0.2100000000")

    @pytest.mark.asyncio
    async def test_a_rate_card_import_restates_every_provider_it_cut(self, store):
        store.add(card(source="list"))
        store.add(card(provider="anthropic", model_sku="claude-x", source="list"))
        await meter.write_events(
            store, TENANT, [event(), event(provider="anthropic", model="claude-x", calls=1)], now=T0
        )
        rows = [
            {"provider": provider, "usage_type": "llm_tokens", "model_sku": sku, "unit": "1m_input_tokens",
             "unit_price": "3", "currency": "USD", "effective_from": "2026-09-01", "source": "list",
             "supersede": "true", "restate": "true"}
            for provider, sku in (("openai", "gpt-4o"), ("anthropic", "claude-x"))
        ]  # fmt: skip
        report = await rates.import_cards(TENANT, rows, actor=ACTOR, dry_run=False, file_sha256="0" * 64, now=T0)
        queued = report["restate_jobs"]
        assert {q["provider"] for q in queued} == {"openai", "anthropic"}
        assert len({q["job_id"] for q in queued}) == 2  # one job per provider, neither dropped
        for q in queued:
            assert job(store, q["job_id"]).params["provider"] == q["provider"]


# ---------------------------------------------------------------- one running job per kind


class TestRunning:
    @pytest.mark.asyncio
    async def test_a_claim_waits_while_another_job_of_the_kind_runs(self, store, monkeypatch):
        first = await jobs.enqueue(TENANT, kind="reattribute", params=SPAN, actor=ACTOR)
        running = job(store, first["job_id"])
        running.status, running.started_at, running.heartbeat_at = "running", NOW, NOW
        later = await jobs.enqueue_followup(
            TENANT, kind="reattribute", params={"start": "2026-10-02", "end": "2026-10-02"}, actor=ACTOR
        )
        assert later["merged"] is False and later["job_id"] != first["job_id"]
        assert (await jobs.run(TENANT, uuid.UUID(later["job_id"])))["skipped"] == "not_claimable"
        assert job(store, later["job_id"]).status == "queued"

        def raced(params):
            raise IntegrityError("UPDATE spend_jobs", {}, Exception("ux_spend_jobs_running"))

        monkeypatch.setattr(store, "_claim", raced)
        assert (await jobs.run(TENANT, uuid.UUID(later["job_id"])))["skipped"] == "blocked"

    @pytest.mark.asyncio
    async def test_a_route_request_is_refused_while_a_job_of_the_kind_is_queued_behind_a_running_one(self, store):
        from core.spend.errors import SpendError

        first = await jobs.enqueue(TENANT, kind="settle_fx", params={**SPAN, "force_dates": []}, actor=ACTOR)
        job(store, first["job_id"]).status = "running"
        await jobs.enqueue_followup(TENANT, kind="settle_fx", params={**SPAN, "force_dates": []}, actor=ACTOR)
        with pytest.raises(SpendError) as info:
            await jobs.enqueue(TENANT, kind="settle_fx", params={**SPAN, "force_dates": []}, actor=ACTOR)
        assert info.value.code == "job_running" and first["job_id"] in info.value.message
        assert store.locks.count(locks.job_kind(TENANT, "settle_fx")) == 3

    @pytest.mark.asyncio
    async def test_a_failed_job_still_sends_the_next_one(self, store, sent, monkeypatch):
        async def broken(*args, **kwargs):
            raise RuntimeError("bad input")

        monkeypatch.setattr(maintenance, "reattribute", broken)
        first = await jobs.enqueue(TENANT, kind="reattribute", params=SPAN, actor=ACTOR)
        job(store, first["job_id"]).status = "running"
        nxt = await jobs.enqueue_followup(TENANT, kind="reattribute", params=SPAN, actor=ACTOR)
        job(store, first["job_id"]).status = "queued"
        sent.clear()
        out = await jobs.run(TENANT, uuid.UUID(first["job_id"]))
        assert out["status"] == "failed" and sent == [(nxt["job_id"], {})]

    @pytest.mark.asyncio
    async def test_a_next_job_lookup_failure_is_logged(self, store, monkeypatch):
        async def broken(*args, **kwargs):
            raise RuntimeError("database away")

        monkeypatch.setattr(jobs, "_queued", broken)
        assert await jobs._send_next(TENANT, "reattribute") is None

    @pytest.mark.asyncio
    async def test_a_followup_that_cannot_be_written_is_logged_and_none(self, store, monkeypatch):
        import core.database

        def broken(tenant_id):
            raise RuntimeError("database away")

        monkeypatch.setattr(core.database, "get_tenant_session", broken)
        assert await jobs.enqueue_followup(TENANT, kind="settle_fx", params=SPAN, actor=ACTOR) is None

    @pytest.mark.asyncio
    async def test_a_followup_whose_dispatch_fails_stays_queued_for_the_sweep(self, store, monkeypatch):
        def broker_down(tenant_id, job_id, **options):
            raise ConnectionError("broker down")

        monkeypatch.setattr(jobs, "_dispatch", broker_down)
        out = await jobs.enqueue_followup(TENANT, kind="settle_fx", params={**SPAN, "force_dates": []}, actor=ACTOR)
        assert out["status"] == "queued" and job(store, out["job_id"]).status == "queued"
        resent: list = []
        monkeypatch.setattr(jobs, "_dispatch", lambda tenant_id, job_id, **options: resent.append(str(job_id)))
        assert await jobs.sweep(TENANT, now=NOW) == {"requeued": 0, "sent": 1}
        assert resent == [out["job_id"]]


# ---------------------------------------------------------------- transient failures, heartbeat, sweep


class TestRecovery:
    def test_retryable_knows_lock_timeouts_deadlocks_and_dropped_connections(self):
        class DeadlockError(Exception):
            pgcode = "40P01"

        class UniqueError(Exception):
            sqlstate = "23505"

        assert retryable(lock_timeout())
        assert retryable(DBAPIError("x", {}, DeadlockError()))
        assert retryable(OperationalError("x", {}, Exception("connection refused")))
        assert retryable(OSError("reset")) and retryable(TimeoutError())
        assert not retryable(DBAPIError("x", {}, UniqueError()))
        assert not retryable(ValueError("bad"))

    @pytest.mark.asyncio
    async def test_a_lock_timeout_queues_the_job_again_with_a_backoff_then_fails(self, store, sent, monkeypatch):
        async def busy(*args, **kwargs):
            raise lock_timeout()

        monkeypatch.setattr(maintenance, "reattribute", busy)
        queued = await jobs.enqueue(TENANT, kind="reattribute", params=SPAN, actor=ACTOR)
        job_id = uuid.UUID(queued["job_id"])
        row = job(store, job_id)
        sent.clear()
        first = await jobs.run(TENANT, job_id)
        assert first == {"job_id": str(job_id), "status": "queued", "error_code": "DBAPIError", "attempts": 1}
        assert row.status == "queued" and row.result == {"attempts": 1, "last_error": "DBAPIError"}
        assert row.started_at is None and sent == [(str(job_id), {"countdown": 60})]
        second = await jobs.run(TENANT, job_id)
        assert second["attempts"] == 2 and sent[-1] == (str(job_id), {"countdown": 120})
        third = await jobs.run(TENANT, job_id)
        assert third["status"] == "failed" and row.status == "failed" and row.error_code == "DBAPIError"
        assert row.result == {"attempts": 3}

    @pytest.mark.asyncio
    async def test_a_job_that_succeeds_on_a_retry_records_its_attempts(self, store, monkeypatch):
        calls = {"n": 0}

        async def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise lock_timeout()
            return {"days": 1, "scanned": 0, "changed": 0}

        monkeypatch.setattr(maintenance, "reattribute", flaky)
        queued = await jobs.enqueue(TENANT, kind="reattribute", params=SPAN, actor=ACTOR)
        job_id = uuid.UUID(queued["job_id"])
        assert (await jobs.run(TENANT, job_id))["status"] == "queued"
        done = await jobs.run(TENANT, job_id)
        assert done["status"] == "succeeded" and job(store, job_id).result["attempts"] == 2

    @pytest.mark.asyncio
    async def test_a_running_job_writes_its_heartbeat(self, store, monkeypatch):
        monkeypatch.setattr(jobs, "HEARTBEAT_S", 0.01)
        seen: list = []

        async def slow(*args, **kwargs):
            row = store.of("spend_jobs")[0]
            row.heartbeat_at = None
            await asyncio.sleep(0.1)
            seen.append(row.heartbeat_at)
            return {"days": 1}

        monkeypatch.setattr(maintenance, "reattribute", slow)
        queued = await jobs.enqueue(TENANT, kind="reattribute", params=SPAN, actor=ACTOR)
        assert (await jobs.run(TENANT, uuid.UUID(queued["job_id"])))["status"] == "succeeded"
        assert seen == [NOW]
        assert (await jobs.get_job(TENANT, uuid.UUID(queued["job_id"])))["heartbeat_at"] == NOW.isoformat()

    @pytest.mark.asyncio
    async def test_a_missed_heartbeat_is_logged_and_the_job_keeps_running(self, store, monkeypatch):
        import core.database

        monkeypatch.setattr(jobs, "HEARTBEAT_S", 0.01)
        real = core.database.get_tenant_session
        away = {"on": False}

        def maybe(tenant_id):
            if away["on"]:
                raise RuntimeError("database away")
            return real(tenant_id)

        async def slow(*args, **kwargs):
            away["on"] = True
            await asyncio.sleep(0.05)
            away["on"] = False
            return {"days": 1}

        monkeypatch.setattr(core.database, "get_tenant_session", maybe)
        monkeypatch.setattr(maintenance, "reattribute", slow)
        queued = await jobs.enqueue(TENANT, kind="reattribute", params=SPAN, actor=ACTOR)
        assert (await jobs.run(TENANT, uuid.UUID(queued["job_id"])))["status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_the_sweep_requeues_a_lost_workers_job_and_resends_queued_jobs(self, store, sent):
        lost = await jobs.enqueue(TENANT, kind="reattribute", params=SPAN, actor=ACTOR)
        alive = await jobs.enqueue(TENANT, kind="settle_fx", params={**SPAN, "force_dates": []}, actor=ACTOR)
        waiting = await jobs.enqueue_followup(
            TENANT, kind="settle_fx", params={"start": "2016-01-01", "end": "2016-01-01"}, actor=ACTOR
        )  # cannot merge with the settlement queued before it (too far apart): its own job
        orphan = await jobs.enqueue(TENANT, kind="backfill", params=SPAN, actor=ACTOR)
        lost_row, alive_row = job(store, lost["job_id"]), job(store, alive["job_id"])
        lost_row.status, lost_row.started_at, lost_row.heartbeat_at = "running", NOW, NOW - timedelta(minutes=20)
        alive_row.status, alive_row.started_at, alive_row.heartbeat_at = "running", NOW, NOW - timedelta(minutes=5)
        sent.clear()
        assert await jobs.sweep(TENANT, now=NOW) == {"requeued": 1, "sent": 2}
        assert lost_row.status == "queued" and lost_row.heartbeat_at is None and alive_row.status == "running"
        assert sorted(job_id for job_id, _options in sent) == sorted([lost["job_id"], orphan["job_id"]])
        assert job(store, waiting["job_id"]).status == "queued"  # waits for the running settlement

    def test_the_sweep_task_is_registered_scheduled_and_isolates_tenants(self, store, monkeypatch):
        import core.tasks.spend_tasks as tasks
        from core.spend import tenants
        from core.tasks.celery_app import app

        app.loader.import_default_modules()
        assert "core.tasks.spend_tasks.sweep_jobs" in app.tasks
        beat = app.conf.beat_schedule["spend-sweep-jobs"]
        assert beat["task"] == "core.tasks.spend_tasks.sweep_jobs" and beat["schedule"] == 900.0
        assert beat["options"]["queue"] == "maintenance"
        other = uuid.UUID("99999999-9999-4999-8999-999999999999")

        async def two_tenants():
            return [TENANT, other]

        async def sweep(tenant_id, now=None):
            if tenant_id == other:
                raise RuntimeError("tenant session down")
            return {"requeued": 1, "sent": 2}

        def run(awaitable):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(awaitable)
            finally:
                loop.close()

        monkeypatch.setattr(tenants, "active_tenant_ids", two_tenants)
        monkeypatch.setattr(jobs, "sweep", sweep)
        monkeypatch.setattr(tasks, "run_async", run)
        assert tasks.sweep_jobs.run() == {"requeued": 1, "sent": 2}
        monkeypatch.setattr(settings, "spend_sweeps_enabled", False)
        assert tasks.sweep_jobs.run() == {"skipped": "spend_sweeps_disabled"}
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        assert tasks.sweep_jobs.run() == {"skipped": "spend_intelligence_disabled"}
