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


class _Session:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.added = []
        self.deleted = []

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
        return None


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
        monkeypatch.setattr(settings, "finops_thresholds_enabled", True)

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
        monkeypatch.setattr(settings, "finops_thresholds_enabled", True)
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


class TestHooks:
    def test_the_run_checks_thresholds_after_the_budget_and_says_what_it_did(self):
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        run = src[src.index('@router.post("/agents/{agent_id}/run")') :]
        run = run[: run.index("_record_cost_ledger(tid, agent_id, perf)")]
        assert run.index("Monthly budget exceeded") < run.index("finops_thresholds.check_run(")
        assert run.index("finops_thresholds.check_run(") < run.index("# 5b. Execute via LangGraph runner")
        assert "finops_thresholds.refusal(decision" in run and "asyncio.sleep(decision.delay_seconds)" in run
        assert '"finops_action": finops_action,' in src

    def test_the_migration_and_the_model_are_shaped(self):
        migration = (ROOT / "migrations" / "versions" / "v6_z56_finops_thresholds.py").read_text(encoding="utf-8")
        assert 'down_revision = "v6z55_finops_attribution"' in migration
        assert "ux_finops_thresholds_tenant_name" in migration and "finops_thresholds_tenant_isolation" in migration
        assert "CHECK (scope_kind IN ('organisation', 'application', 'use_case', 'business_unit'))" in migration
        from core.models.finops_threshold import FinopsThreshold

        assert FinopsThreshold.__tablename__ == "finops_thresholds"
