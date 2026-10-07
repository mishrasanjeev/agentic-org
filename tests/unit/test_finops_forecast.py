# SPDX-License-Identifier: Apache-2.0
"""Cost forecasting from history and growth assumptions, and comparison across models and providers."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

from api.v1 import finops as api
from core.config import settings
from core.finops import forecast

ROOT = Path(__file__).resolve().parents[2]
END = date(2026, 10, 7)


def _days(values, end=END):
    start = end - timedelta(days=len(values) - 1)
    return [(start + timedelta(days=i), int(v * 1000), float(v)) for i, v in enumerate(values)]


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)


class _Session:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return _Result(self.answers.pop(0) if self.answers else [])


class TestProject:
    def test_a_flat_history_projects_the_same_daily_cost_with_no_band(self):
        out = forecast.project(_days([2.0] * 30), history_days=30, horizon_days=90)
        assert out["projected_cost_usd"] == 180.0 and out["baseline_cost_usd"] == 180.0
        assert out["projected_cost_low_usd"] == 180.0 and out["projected_cost_high_usd"] == 180.0
        assert out["trend_cost_usd_per_day"] == 0.0 and out["daily_mean_cost_usd"] == 2.0
        assert out["projected_tokens"] == 2000 * 90 and out["history_tokens"] == 60000

    def test_a_rising_history_projects_the_trend_and_growth_compounds(self):
        rising = forecast.project(_days([float(i) for i in range(1, 31)]), history_days=30, horizon_days=10)
        assert rising["trend_cost_usd_per_day"] == 1.0
        assert rising["projected_cost_usd"] == sum(float(30 + s) for s in range(1, 11))
        grown = forecast.project(_days([1.0] * 30), history_days=30, horizon_days=30, growth_monthly_pct=100.0)
        assert 30.0 < grown["projected_cost_usd"] < 60.0 and grown["projected_cost_usd"] > 43.0
        shrinking = forecast.project(_days([float(30 - i) for i in range(30)]), history_days=30, horizon_days=60)
        assert shrinking["projected_cost_usd"] == 0.0 and shrinking["projected_cost_low_usd"] == 0.0

    def test_missing_days_count_as_zero_and_scatter_widens_the_band(self):
        sparse = forecast.project(_days([10.0])[:1], history_days=10, horizon_days=10)
        assert sparse["history_cost_usd"] == 10.0 and sparse["daily_mean_cost_usd"] == 1.0
        noisy = forecast.project(_days([0.0, 4.0] * 15), history_days=30, horizon_days=30)
        assert noisy["projected_cost_high_usd"] > noisy["projected_cost_usd"] > noisy["projected_cost_low_usd"] >= 0.0
        empty = forecast.project([], history_days=30, horizon_days=30)
        assert empty["projected_cost_usd"] == 0.0 and empty["history_tokens"] == 0

    def test_off_by_default(self):
        assert settings.finops_forecast_enabled is False and forecast.enabled() is False


@pytest.mark.asyncio
async def test_history_reads_daily_rows_per_key_and_the_forecast_assembles_them():
    tid = uuid.uuid4()
    today = datetime.now(UTC).date()
    rows = [("kyc", today - timedelta(days=1), 1000, 1.0), ("kyc", today, 1000, 1.0), ("loans", today, 500, 0.5)]
    session = _Session(rows)
    series = await forecast.history(session, tid, days=30, group_by="use_case")
    assert set(series) == {"kyc", "loans"} and len(series["kyc"]) == 2
    sql, params = session.calls[0]
    assert sql.startswith("SELECT use_case, period_date, SUM(tokens), SUM(cost_usd)") and params["tid"] == str(tid)
    with pytest.raises(ValueError):
        await forecast.history(_Session(), tid, group_by="password")
    answer = await forecast.forecast(
        _Session(rows), tid, days=30, horizon_days=90, group_by="use_case", growth_monthly_pct=10
    )
    assert answer["assumptions"] == {
        "history_days": 30,
        "horizon_days": 90,
        "growth_monthly_pct": 10.0,
        "method": "linear trend on daily cost, compounded monthly by the growth assumption, floored at zero",
    }
    assert [r["use_case"] for r in answer["rows"]] == ["kyc", "loans"]
    assert answer["totals"]["projected_cost_usd"] == round(sum(r["projected_cost_usd"] for r in answer["rows"]), 2)
    clamped = await forecast.forecast(_Session([]), tid, days=5000, horizon_days=0, growth_monthly_pct=9999)
    assert clamped["assumptions"]["history_days"] == forecast.MAX_HISTORY_DAYS
    assert (
        clamped["assumptions"]["horizon_days"] == 1
        and clamped["assumptions"]["growth_monthly_pct"] == forecast.MAX_GROWTH_PCT
    )


class TestComparison:
    def test_alternatives_are_priced_from_the_catalogue_cheapest_first(self):
        priced = forecast.alternatives(1_000_000, 750_000, 250_000, current_cost=10.0)
        assert 1 <= len(priced) <= forecast.MAX_ALTERNATIVES
        assert priced == sorted(priced, key=lambda r: (r["cost_usd"], r["provider"], r["model"]))
        assert all(set(r) == {"provider", "model", "cost_usd", "saving_usd", "price_source"} for r in priced)
        assert all(round(10.0 - r["cost_usd"], 6) == r["saving_usd"] for r in priced)

    @pytest.mark.asyncio
    async def test_the_comparison_folds_each_use_case_with_its_mix_and_alternatives(self):
        tid = uuid.uuid4()
        rows = [
            ("kyc", "openai", "gpt-4o", 600_000, 450_000, 150_000, 6.0, 30),
            ("kyc", "openai", "gpt-4o-mini", 400_000, 300_000, 100_000, 0.4, 40),
            ("loans", "anthropic", "claude-sonnet-4-5-20250929", 100_000, 80_000, 20_000, 0.9, 5),
        ]
        session = _Session(rows)
        answer = await forecast.comparison(session, tid, days=30)
        assert [u["use_case"] for u in answer["use_cases"]] == ["kyc", "loans"]
        kyc = answer["use_cases"][0]
        assert kyc["tokens"] == 1_000_000 and kyc["cost_usd"] == 6.4 and kyc["calls"] == 70
        assert kyc["models"][0]["model"] == "gpt-4o" and kyc["models"][0]["share"] == 0.9375
        assert kyc["alternatives"] and kyc["alternatives"][0]["cost_usd"] <= kyc["alternatives"][-1]["cost_usd"]
        assert answer["totals"] == {"cost_usd": 7.3, "tokens": 1_100_000, "calls": 75}
        sql, params = session.calls[0]
        assert "FROM model_gateway_records" in sql and "outcome = 'completed'" in sql and params["tid"] == str(tid)
        assert "change" not in answer

    @pytest.mark.asyncio
    async def test_before_and_after_a_change_date_compares_daily_cost_per_model(self):
        tid = uuid.uuid4()
        before = [("kyc", "openai", "gpt-4o", 300_000, None, None, 30.0, 30)]
        after = [("kyc", "openai", "gpt-4o-mini", 300_000, None, None, 3.0, 30)]
        session = _Session(before, after)
        changed = datetime.now(UTC).date() - timedelta(days=9)
        change = await forecast.before_after(session, tid, days=30, changed_at=changed)
        assert change["before_days"] == 30 and change["after_days"] == 10
        models = {m["model"]: m for m in change["models"]}
        assert models["openai/gpt-4o"]["before"]["daily_cost_usd"] == 1.0 and models["openai/gpt-4o"]["after"] is None
        assert (
            models["openai/gpt-4o-mini"]["after"]["daily_cost_usd"] == 0.3
            and models["openai/gpt-4o-mini"]["daily_cost_delta_usd"] == 0.3
        )
        assert change["daily_cost_delta_usd"] == -0.7
        first_sql, first_params = session.calls[0]
        assert (
            "created_at < :until" in first_sql
            and first_params["until"] == changed
            and "until" not in session.calls[1][1]
        )
        combined = await forecast.comparison(_Session(before, before, after), tid, days=30, changed_at=changed)
        assert combined["change"]["changed_at"] == changed.isoformat()


class TestEndpoints:
    @pytest.mark.asyncio
    async def test_off_the_endpoints_are_not_found(self):
        tid = str(uuid.uuid4())
        with pytest.raises(HTTPException) as refused:
            await api.cost_forecast(
                days=90, horizon_days=90, group_by="use_case", growth_monthly_pct=None, tenant_id=tid
            )
        assert refused.value.status_code == 404 and refused.value.detail["error"] == "finops_forecast_disabled"
        with pytest.raises(HTTPException) as refused:
            await api.cost_comparison(days=30, changed_at=None, tenant_id=tid)
        assert refused.value.status_code == 404

    @pytest.mark.asyncio
    async def test_on_they_answer_and_a_bad_dimension_is_refused(self, monkeypatch):
        monkeypatch.setattr(settings, "finops_forecast_enabled", True)
        tid = str(uuid.uuid4())
        with pytest.raises(HTTPException) as refused:
            await api.cost_forecast(days=90, horizon_days=90, group_by="owner", growth_monthly_pct=None, tenant_id=tid)
        assert refused.value.status_code == 422
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: _Session([]))
        answer = await api.cost_forecast(
            days=30, horizon_days=90, group_by="use_case", growth_monthly_pct=5, tenant_id=tid
        )
        assert answer["assumptions"]["growth_monthly_pct"] == 5.0 and answer["rows"] == []
        monkeypatch.setattr(api, "get_tenant_session", lambda _tid: _Session([]))
        compared = await api.cost_comparison(days=30, changed_at=None, tenant_id=tid)
        assert compared["use_cases"] == [] and compared["totals"]["calls"] == 0

    def test_the_router_declares_both_routes(self):
        src = (ROOT / "api" / "v1" / "finops.py").read_text(encoding="utf-8")
        assert '@router.get("/finops/forecast", dependencies=[require_tenant_admin])' in src
        assert '@router.get("/finops/comparison", dependencies=[require_tenant_admin])' in src
