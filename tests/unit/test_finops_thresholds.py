# SPDX-License-Identifier: Apache-2.0
"""Cost thresholds: alert, throttle or suspend a use case, application, business unit or the organisation."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1 import finops as api
from core.config import settings
from core.finops import attribution, thresholds

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
REAL_CLAIM = thresholds.claim_breach  # the check-run tests replace it with an in-memory claim


def _row(**over):
    base = {
        "id": uuid.uuid4(),
        "name": "KYC monthly",
        "scope_kind": "use_case",
        "scope_value": "kyc",
        "period": "monthly",
        "threshold_usd": 100.0,
        "action": "alert",
        "throttle_seconds": 5,
        "enabled": True,
        "notify_channels": "log",
        "last_breach_period": None,
        "last_breach_at": None,
        "last_breach_spend_usd": None,
        "lifted_until": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalar(self):
        return self.rows[0] if self.rows else None


class _Session:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.added = []
        self.deleted = []
        self.commits = 0
        self.flush_error = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return _Result(self.answers.pop(0) if self.answers else [])

    def add(self, row):
        self.added.append(row)

    async def delete(self, row):
        self.deleted.append(row)

    async def flush(self):
        if self.flush_error is not None:
            raise self.flush_error
        return None

    async def commit(self):
        self.commits += 1


def _both_on(monkeypatch):
    monkeypatch.setattr(settings, "finops_attribution_enabled", True)
    monkeypatch.setattr(settings, "finops_thresholds_enabled", True)


class TestFields:
    def test_a_threshold_is_parsed_and_bounded(self):
        fields = thresholds.parse_fields(
            {
                "name": " KYC monthly ",
                "scope_kind": "Use_Case",
                "scope_value": "KYC Onboarding",
                "threshold_usd": "100",
                "action": "throttle",
                "throttle_seconds": 10,
                "notify_channels": ["email", "log", "email"],
            }
        )
        assert fields == {
            "name": "KYC monthly",
            "scope_kind": "use_case",
            "scope_value": "kyc-onboarding",
            "period": "monthly",
            "threshold_usd": 100.0,
            "action": "throttle",
            "throttle_seconds": 10,
            "enabled": True,
            "notify_channels": "email,log",
        }
        organisation = thresholds.parse_fields(
            {"name": "all", "scope_kind": "organisation", "scope_value": "ignored", "threshold_usd": 1}
        )
        assert (
            organisation["scope_value"] == ""
            and organisation["action"] == "alert"
            and organisation["throttle_seconds"] == 5
        )
        lifted = thresholds.parse_fields({"enabled": False, "lifted_until": "2026-10-08T00:00:00"}, partial=True)
        assert lifted == {"enabled": False, "lifted_until": datetime(2026, 10, 8, tzinfo=UTC)}

    @pytest.mark.parametrize(
        ("raw", "code"),
        [
            ({"name": "", "scope_kind": "organisation", "threshold_usd": 1}, "name"),
            ({"name": "x", "scope_kind": "team", "threshold_usd": 1}, "scope_kind"),
            ({"name": "x", "scope_kind": "use_case", "threshold_usd": 1}, "scope_value"),
            ({"name": "x", "scope_kind": "organisation", "threshold_usd": 0}, "threshold_usd"),
            ({"name": "x", "scope_kind": "organisation", "threshold_usd": "lots"}, "threshold_usd"),
            ({"name": "x", "scope_kind": "organisation", "threshold_usd": 1, "period": "weekly"}, "period"),
            ({"name": "x", "scope_kind": "organisation", "threshold_usd": 1, "action": "halt"}, "action"),
            (
                {"name": "x", "scope_kind": "organisation", "threshold_usd": 1, "throttle_seconds": 60},
                "throttle_seconds",
            ),
            (
                {"name": "x", "scope_kind": "organisation", "threshold_usd": 1, "notify_channels": ["pager"]},
                "notify_channels",
            ),
            ({"name": "x", "scope_kind": "organisation", "threshold_usd": 1, "colour": "red"}, "unknown_field"),
            ({"lifted_until": "soon"}, "lifted_until"),
            ({"scope_value": "x"}, "scope_value"),
        ],
    )
    def test_bad_fields_are_refused(self, raw, code):
        with pytest.raises(thresholds.ThresholdError) as refused:
            thresholds.parse_fields(raw, partial="name" not in raw)
        assert refused.value.code == code and refused.value.status == 422

    def test_periods_and_matching(self):
        assert thresholds.period_start("monthly", NOW) == date(2026, 10, 1) and thresholds.period_start(
            "daily", NOW
        ) == date(2026, 10, 7)
        assert (
            thresholds.period_key("monthly", NOW) == "2026-10" and thresholds.period_key("daily", NOW) == "2026-10-07"
        )
        given = attribution.Attribution(use_case="kyc", application="chat", business_unit="retail")
        assert thresholds.matches(_row(), given) and thresholds.matches(
            _row(scope_kind="organisation", scope_value=""), given
        )
        assert thresholds.matches(_row(scope_kind="application", scope_value="chat"), given)
        assert not thresholds.matches(_row(scope_kind="business_unit", scope_value="ops"), given)
        assert not thresholds.matches(_row(scope_value="loans"), given)
        assert settings.finops_thresholds_enabled is False and thresholds.enabled() is False


@pytest.mark.asyncio
async def test_spend_reads_the_ledger_for_the_scope_and_period():
    tid = uuid.uuid4()
    session = _Session([(42.5,)])
    assert await thresholds.spend(session, tid, _row(), now=NOW) == 42.5
    sql, params = session.calls[0]
    assert "FROM finops_cost_ledger" in sql and sql.endswith("AND use_case = :value")
    assert params == {"tid": str(tid), "since": date(2026, 10, 1), "value": "kyc"}
    whole = _Session([(None,)])
    assert (
        await thresholds.spend(whole, tid, _row(scope_kind="organisation", scope_value="", period="daily"), now=NOW)
        == 0.0
    )
    assert "value" not in whole.calls[0][1] and whole.calls[0][1]["since"] == date(2026, 10, 7)


class TestCheckRun:
    @pytest.fixture
    def on(self, monkeypatch):
        _both_on(monkeypatch)

    @pytest.fixture(autouse=True)
    def claims(self, monkeypatch):
        """The conditional update, in memory: a period already recorded is not claimed again."""
        made = []

        async def _claim(_session, _tid, row, key, *, now, spent):
            if row.last_breach_period == key:
                return False
            row.last_breach_period, row.last_breach_at, row.last_breach_spend_usd = key, now, spent
            made.append((row.name, key))
            return True

        monkeypatch.setattr(thresholds, "claim_breach", _claim)
        return made

    @pytest.fixture
    def rows(self, monkeypatch):
        holder = {"rows": []}

        async def _rows(_session, _tid):
            return list(holder["rows"])

        monkeypatch.setattr(thresholds, "enabled_rows", _rows)
        return holder

    @pytest.fixture
    def spends(self, monkeypatch):
        table = {}

        async def _spend(_session, _tid, row, *, now=None):
            return table.get(row.name, 0.0)

        monkeypatch.setattr(thresholds, "spend", _spend)
        return table

    @pytest.mark.asyncio
    async def test_off_or_unbreached_nothing_happens(self, rows, spends, monkeypatch):
        rows["rows"] = [_row()]
        spends["KYC monthly"] = 500.0
        assert (
            await thresholds.check_run(_Session(), uuid.uuid4(), attribution.Attribution(use_case="kyc"))
            == thresholds.NONE
        )
        monkeypatch.setattr(settings, "finops_thresholds_enabled", True)
        assert (
            await thresholds.check_run(_Session(), uuid.uuid4(), attribution.Attribution(use_case="kyc"))
            == thresholds.NONE
        ), "thresholds without attribution never run"
        monkeypatch.setattr(settings, "finops_attribution_enabled", True)
        spends["KYC monthly"] = 99.99
        assert (
            await thresholds.check_run(_Session(), uuid.uuid4(), attribution.Attribution(use_case="kyc"), now=NOW)
        ).action is None
        assert (
            await thresholds.check_run(_Session(), uuid.uuid4(), attribution.Attribution(use_case="loans"), now=NOW)
        ).action is None

    @pytest.mark.asyncio
    async def test_the_strongest_breached_action_wins_and_the_breach_is_recorded_and_notified_once(
        self, on, rows, spends
    ):
        alert, throttle, suspend = (
            _row(name="alert"),
            _row(name="throttle", action="throttle", throttle_seconds=7),
            _row(name="suspend", action="suspend", scope_kind="organisation", scope_value=""),
        )
        rows["rows"] = [alert, throttle, suspend]
        spends.update({"alert": 150.0, "throttle": 120.0, "suspend": 50.0})
        given = attribution.Attribution(use_case="kyc")
        first = await thresholds.check_run(_Session(), uuid.uuid4(), given, now=NOW)
        assert first.action == "throttle" and first.name == "throttle" and first.delay_seconds == 7
        assert first.spend_usd == 120.0 and first.threshold_usd == 100.0 and first.notified is True
        assert (
            alert.last_breach_period == "2026-10"
            and throttle.last_breach_spend_usd == 120.0
            and suspend.last_breach_period is None
        )
        again = await thresholds.check_run(_Session(), uuid.uuid4(), given, now=NOW)
        assert again.notified is False
        spends["suspend"] = 200.0
        stopped = await thresholds.check_run(_Session(), uuid.uuid4(), given, now=NOW)
        assert stopped.action == "suspend" and stopped.scope_kind == "organisation" and stopped.delay_seconds == 0
        suspend.lifted_until = NOW + timedelta(hours=1)
        lifted = await thresholds.check_run(_Session(), uuid.uuid4(), given, now=NOW)
        assert lifted.action == "throttle" and lifted.name == "throttle"
        later = await thresholds.check_run(_Session(), uuid.uuid4(), given, now=NOW + timedelta(days=40))
        assert later.notified is True and alert.last_breach_period == "2026-11"

    @pytest.mark.asyncio
    async def test_concurrent_runs_notify_once_and_the_claim_is_committed_first(self, on, rows, spends, monkeypatch):
        row = _row(name="alert")
        rows["rows"] = [row]
        spends["alert"] = 150.0
        told = []
        order = []

        async def _notify(session, _tid, notified_row, spent):
            order.append(("notify", session.commits))
            told.append(notified_row.name)
            return True

        monkeypatch.setattr(thresholds, "notify", _notify)
        given = attribution.Attribution(use_case="kyc")
        first_session, second_session = _Session(), _Session()
        first = await thresholds.check_run(first_session, uuid.uuid4(), given, now=NOW)
        # A second run that read the row before the first committed still sees the old period;
        # the conditional update finds it recorded and claims nothing.
        row.last_breach_period = "2026-10"
        stale = _row(name="alert", id=row.id, last_breach_period=None)
        rows["rows"] = [stale]

        async def _lost(_session, _tid, _row, _key, *, now, spent):
            return False

        monkeypatch.setattr(thresholds, "claim_breach", _lost)
        second = await thresholds.check_run(second_session, uuid.uuid4(), given, now=NOW)
        assert first.notified is True and second.notified is False and told == ["alert"]
        assert order == [("notify", 1)] and first_session.commits == 1 and second_session.commits == 0
        assert first.action == second.action == "alert"

    @pytest.mark.asyncio
    async def test_the_claim_is_one_conditional_update(self):
        tid = uuid.uuid4()
        row = _row()
        won = _Session([(row.id,)])
        assert await REAL_CLAIM(won, tid, row, "2026-10", now=NOW, spent=150.0) is True
        sql, params = won.calls[0]
        assert sql.startswith("UPDATE finops_thresholds SET last_breach_period = :key")
        assert (
            "last_breach_period IS DISTINCT FROM :key RETURNING id" in sql and "tenant_id = CAST(:tid AS uuid)" in sql
        )
        assert params == {"key": "2026-10", "at": NOW, "spent": 150.0, "id": str(row.id), "tid": str(tid)}
        assert row.last_breach_period == "2026-10" and row.last_breach_spend_usd == 150.0
        other = _row()
        assert await REAL_CLAIM(_Session([]), tid, other, "2026-10", now=NOW, spent=150.0) is False
        assert other.last_breach_period is None

    @pytest.mark.asyncio
    async def test_every_enabled_threshold_is_evaluated(self):
        session = _Session([_row(name=f"t{i:03d}") for i in range(thresholds.MAX_THRESHOLDS + 5)])
        loaded = await thresholds.enabled_rows(session, uuid.uuid4())
        assert len(loaded) == thresholds.MAX_THRESHOLDS + 5
        assert "LIMIT" not in session.calls[0][0].upper()

    @pytest.mark.asyncio
    async def test_notification_uses_the_channels_and_never_raises(self, monkeypatch):
        delivered = []

        def _send(to, subject, html):
            delivered.append((to, subject))
            return True

        monkeypatch.setattr("core.email.send_email", _send)
        session = _Session(["admin@example.test"])
        assert await thresholds.notify(session, uuid.uuid4(), _row(notify_channels="email,log"), 150.0) is True
        assert delivered and delivered[0][0] == "admin@example.test" and "$150.00" in delivered[0][1]
        assert await thresholds.notify(_Session([None]), uuid.uuid4(), _row(notify_channels="email"), 1.0) is False

        def _broken(to, subject, html):
            raise RuntimeError("smtp down")

        monkeypatch.setattr("core.email.send_email", _broken)
        assert (
            await thresholds.notify(_Session(["admin@example.test"]), uuid.uuid4(), _row(notify_channels="email"), 1.0)
            is False
        )

    def test_the_refusal_takes_the_budget_refusal_shape(self):
        decision = thresholds.Decision(
            action="suspend",
            threshold_id="t",
            name="KYC monthly",
            scope_kind="use_case",
            scope_value="kyc",
            period="monthly",
            spend_usd=120.0,
            threshold_usd=100.0,
        )
        refused = thresholds.refusal(decision, agent_id="a1")
        assert refused["status"] == "threshold_suspended" and refused["error"]["code"] == "E1009"
        assert "use case kyc are suspended" in refused["error"]["message"] and refused["finops"]["action"] == "suspend"
        assert refused["output"] == {} and refused["confidence"] == 0 and refused["agent_id"] == "a1"


@pytest.mark.asyncio
async def test_status_lists_every_threshold_with_spend_and_share():
    row = _row(last_breach_period="2026-10", last_breach_at=NOW, last_breach_spend_usd=120.0)
    session = _Session([row], [(120.0,)])
    listed = await thresholds.status(session, uuid.uuid4(), now=NOW)
    assert listed[0]["name"] == "KYC monthly" and listed[0]["spend_usd"] == 120.0 and listed[0]["share"] == 1.2
    assert listed[0]["breached"] and listed[0]["period_key"] == "2026-10" and listed[0]["notify_channels"] == ["log"]
    assert listed[0]["last_breach_at"] == NOW.isoformat()


class TestEndpoints:
    @pytest.mark.asyncio
    async def test_off_the_endpoints_are_not_found(self):
        tid = str(uuid.uuid4())
        with pytest.raises(HTTPException) as refused:
            await api.list_thresholds(tenant_id=tid)
        assert refused.value.status_code == 404 and refused.value.detail["error"] == "finops_thresholds_disabled"
        with pytest.raises(HTTPException) as refused:
            await api.create_threshold(
                api.ThresholdIn(name="x", scope_kind="organisation", threshold_usd=1), tenant_id=tid, user={}
            )
        assert refused.value.status_code == 404

    @pytest.mark.asyncio
    async def test_on_a_threshold_is_created_updated_and_deleted(self, monkeypatch):
        _both_on(monkeypatch)
        session = _Session([], [], [])
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: session)
        tid = str(uuid.uuid4())
        user = {"agenticorg:user_id": str(uuid.uuid4())}
        created = await api.create_threshold(
            api.ThresholdIn(
                name="KYC monthly", scope_kind="use_case", scope_value="KYC", threshold_usd=100, action="suspend"
            ),
            tenant_id=tid,
            user=user,
        )
        assert created["name"] == "KYC monthly" and created["scope_value"] == "kyc" and created["action"] == "suspend"
        assert len(session.added) == 1 and session.added[0].created_by is not None
        with pytest.raises(HTTPException) as refused:
            await api.create_threshold(
                api.ThresholdIn(name="x", scope_kind="use_case", threshold_usd=1), tenant_id=tid, user=user
            )
        assert refused.value.status_code == 422 and refused.value.detail["error"] == "scope_value"
        row = session.added[0]
        update_session = _Session([row])
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: update_session)
        updated = await api.update_threshold(
            row.id, api.ThresholdPatch(enabled=False, lifted_until="2026-10-08T00:00:00"), tenant_id=tid, user=user
        )
        assert updated["enabled"] is False and updated["lifted_until"] == "2026-10-08T00:00:00+00:00"
        with pytest.raises(HTTPException) as refused:
            await api.update_threshold(uuid.uuid4(), api.ThresholdPatch(enabled=True), tenant_id=tid, user=user)
        assert refused.value.status_code == 404
        delete_session = _Session([row])
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: delete_session)
        assert await api.delete_threshold(row.id, tenant_id=tid, user=user) is None and delete_session.deleted == [row]


class TestEndpointRefusals:
    @pytest.mark.asyncio
    async def test_creation_is_refused_at_the_cap(self, monkeypatch):
        _both_on(monkeypatch)
        session = _Session([thresholds.MAX_THRESHOLDS])
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: session)
        with pytest.raises(HTTPException) as refused:
            await api.create_threshold(
                api.ThresholdIn(name="one more", scope_kind="organisation", threshold_usd=1),
                tenant_id=str(uuid.uuid4()),
                user={},
            )
        assert refused.value.status_code == 409 and refused.value.detail["error"] == "threshold_limit"
        assert session.added == [] and "count" in session.calls[0][0].lower()
        below = _Session([thresholds.MAX_THRESHOLDS - 1])
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: below)
        created = await api.create_threshold(
            api.ThresholdIn(name="last", scope_kind="organisation", threshold_usd=1),
            tenant_id=str(uuid.uuid4()),
            user={},
        )
        assert created["name"] == "last" and len(below.added) == 1

    @pytest.mark.asyncio
    async def test_a_duplicate_name_is_a_conflict(self, monkeypatch):
        from sqlalchemy.exc import IntegrityError

        _both_on(monkeypatch)
        duplicate = IntegrityError(
            "INSERT", {}, Exception('duplicate key value violates unique constraint "ux_finops_thresholds_tenant_name"')
        )
        session = _Session([0])
        session.flush_error = duplicate
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: session)
        with pytest.raises(HTTPException) as refused:
            await api.create_threshold(
                api.ThresholdIn(name="KYC monthly", scope_kind="organisation", threshold_usd=1),
                tenant_id=str(uuid.uuid4()),
                user={},
            )
        assert refused.value.status_code == 409 and refused.value.detail["error"] == "duplicate_name"
        row = _row(name="loans")
        renamed = _Session([row])
        renamed.flush_error = duplicate
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: renamed)
        with pytest.raises(HTTPException) as refused:
            await api.update_threshold(
                row.id, api.ThresholdPatch(name="KYC monthly"), tenant_id=str(uuid.uuid4()), user={}
            )
        assert refused.value.status_code == 409 and refused.value.detail["error"] == "duplicate_name"
        other = _Session([0])
        other.flush_error = IntegrityError("INSERT", {}, Exception("violates check constraint"))
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: other)
        with pytest.raises(IntegrityError):
            await api.create_threshold(
                api.ThresholdIn(name="x", scope_kind="organisation", threshold_usd=1),
                tenant_id=str(uuid.uuid4()),
                user={},
            )


class TestConfiguration:
    def test_thresholds_without_attribution_refuse_to_load(self):
        from pydantic import ValidationError

        from core.config import Settings

        with pytest.raises(ValidationError) as refused:
            Settings(_env_file=None, finops_thresholds_enabled=True, finops_attribution_enabled=False)
        assert "AGENTICORG_FINOPS_ATTRIBUTION_ENABLED" in str(refused.value)
        both = Settings(_env_file=None, finops_thresholds_enabled=True, finops_attribution_enabled=True)
        assert both.finops_thresholds_enabled and both.finops_attribution_enabled
        assert Settings(_env_file=None).finops_thresholds_enabled is False

    def test_thresholds_run_only_with_attribution(self, monkeypatch):
        monkeypatch.setattr(settings, "finops_thresholds_enabled", True)
        monkeypatch.setattr(settings, "finops_attribution_enabled", False)
        assert thresholds.enabled() is False
        monkeypatch.setattr(settings, "finops_attribution_enabled", True)
        assert thresholds.enabled() is True


class TestHooks:
    def test_the_run_checks_thresholds_after_the_budget_and_says_what_it_did(self):
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        run = src[src.index('@router.post("/agents/{agent_id}/run")') :]
        run = run[: run.index("_record_cost_ledger(tid, agent_id, perf)")]
        assert run.index("Monthly budget exceeded") < run.index("finops_thresholds.check_run(")
        assert run.index("finops_thresholds.check_run(") < run.index("# 5b. Execute via LangGraph runner")
        assert "finops_thresholds.refusal(decision" in run and "asyncio.sleep(decision.delay_seconds)" in run
        assert '"finops_action": finops_action,' in src

    def test_enforced_labels_are_server_owned(self):
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        run = src[src.index('@router.post("/agents/{agent_id}/run")') :]
        bind = run[run.index("attribution_token = None") : run.index("finops_thresholds.check_run(")]
        assert "caller_labels = not finops_thresholds.enabled()" in bind
        assert 'use_case=payload.get("use_case") if caller_labels else None' in bind
        assert 'business_unit=payload.get("business_unit") if caller_labels else None' in bind
        assert "finops_thresholds.check_run(session, tid, cost_attribution.current())" in run

    @pytest.mark.asyncio
    async def test_the_agent_labels_decide_when_the_caller_sends_none(self):
        agent = SimpleNamespace(
            id=uuid.uuid4(), config={"business_unit": "Retail"}, agent_type="kyc", domain="ops", cost_center_id=None
        )
        server = await attribution.resolve_for_agent(None, agent, use_case=None, application="agents")
        assert server.use_case == "kyc" and server.business_unit == "retail" and server.application == "agents"
        given = attribution.Attribution(use_case="loans")
        assert thresholds.matches(_row(scope_value="kyc"), server) and not thresholds.matches(
            _row(scope_value="kyc"), given
        )

    def test_the_migration_and_the_model_are_shaped(self):
        migration = (ROOT / "migrations" / "versions" / "v6_z56_finops_thresholds.py").read_text(encoding="utf-8")
        assert 'down_revision = "v6z55_finops_attribution"' in migration
        assert "ux_finops_thresholds_tenant_name" in migration and "finops_thresholds_tenant_isolation" in migration
        assert "CHECK (scope_kind IN ('organisation', 'application', 'use_case', 'business_unit'))" in migration
        from core.models.finops_threshold import FinopsThreshold

        assert FinopsThreshold.__tablename__ == "finops_thresholds"
