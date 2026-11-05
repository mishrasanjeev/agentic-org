# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Money saved and alerts: the v6z109 migration, the estimate's arithmetic and guards, the seeded defaults
and the alert helpers."""

from __future__ import annotations

import importlib.util
import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.insights import alerts, value
from core.insights.value_rates import IN_RATES, US_RATES
from core.models import InsightsAlert, ValueBaseline, ValueRate

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z109_insights_value_alerts.py"
MODELS = (ValueBaseline, ValueRate, InsightsAlert)


def _migration():
    spec = importlib.util.spec_from_file_location("_v6_z109_insights_value_alerts", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Op:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, sql) -> None:
        self.sql.append(" ".join(str(sql).split()))


def _run(fn_name: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    migration = _migration()
    op = _Op()
    monkeypatch.setattr(migration, "op", op)
    getattr(migration, fn_name)()
    return op.sql


# ------------------------------------------------------------------ migration


def test_revision_chain_and_single_head() -> None:
    migration = _migration()
    assert migration.revision == "v6z109_insights_value_alerts" and len(migration.revision) <= 32
    assert migration.down_revision == "v6z108_insights_bi"
    assert migration.TABLES == tuple(model.__tablename__ for model in MODELS)
    children = [
        path.name
        for path in MIGRATION.parent.glob("*.py")
        if re.search(r'^down_revision = "v6z108_insights_bi"$', path.read_text(encoding="utf-8"), re.M)
    ]
    assert children == [MIGRATION.name]


def test_every_table_has_a_forced_tenant_policy_and_the_statements_are_idempotent(monkeypatch) -> None:
    sql = _run("upgrade", monkeypatch)
    for name in _migration().TABLES:
        assert f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY;" in sql, name
        assert f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY;" in sql, name
        policy = next(s for s in sql if s.startswith(f"CREATE POLICY {name}_tenant_isolation "))
        assert "USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))" in policy
        assert "WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))" in policy
    for statement in sql:
        if statement.startswith("CREATE TABLE"):
            assert statement.startswith("CREATE TABLE IF NOT EXISTS "), statement
        elif statement.startswith(("CREATE INDEX", "CREATE UNIQUE INDEX")):
            assert " INDEX IF NOT EXISTS " in statement, statement
        elif statement.startswith("ALTER TABLE hitl_queue"):
            assert statement == "ALTER TABLE hitl_queue ADD COLUMN IF NOT EXISTS first_viewed_at TIMESTAMPTZ NULL;"
    # The live-alert index is partial: a resolved alert does not block a new one.
    assert any("ux_insights_alerts_live" in s and "WHERE state <> 'resolved'" in s for s in sql)


def test_downgrade_drops_the_column_and_the_tables(monkeypatch) -> None:
    sql = _run("downgrade", monkeypatch)
    assert sql == [
        "ALTER TABLE hitl_queue DROP COLUMN IF EXISTS first_viewed_at;",
        "DROP TABLE IF EXISTS insights_alerts;",
        "DROP TABLE IF EXISTS value_rates;",
        "DROP TABLE IF EXISTS value_baselines;",
    ]


def test_migration_columns_match_the_models(monkeypatch) -> None:
    sql = " ".join(_run("upgrade", monkeypatch))
    for model in MODELS:
        create = re.search(rf"CREATE TABLE IF NOT EXISTS {model.__tablename__} \((.*?)\);", sql)
        assert create is not None, model.__tablename__
        body = create.group(1)
        for column in model.__table__.columns:
            assert re.search(rf"\b{column.name} ", body), (model.__tablename__, column.name)
        for constraint in model.__table__.constraints:
            if constraint.name and not constraint.name.startswith(("pk_", f"{model.__tablename__}_pkey")):
                if constraint.name.startswith(("ck_", "uq_")):
                    assert constraint.name in body, (model.__tablename__, constraint.name)
        assert [fk.referred_table.name for fk in model.__table__.foreign_key_constraints] == ["tenants"]


# ------------------------------------------------------------------ seeds


def test_every_seeded_role_has_a_cited_rate_in_both_currencies() -> None:
    roles = {role for _minutes, role in value.SEED_BASELINES.values()} | {value.REVIEWER_ROLE}
    for rates in (US_RATES, IN_RATES):
        assert roles <= set(rates), roles - set(rates)
        for role, seed in rates.items():
            assert seed.hourly_cost > 0, role
            assert seed.citation and len(seed.citation) <= 500, role
    assert all("BLS OEWS May 2025" in seed.citation for seed in US_RATES.values())
    assert all("PLFS Annual Report 2025" in seed.citation for seed in IN_RATES.values())
    # Loaded rates: the US median times 1.43, the Indian monthly earnings over 208 hours times 1.17.
    assert US_RATES["accountant"].hourly_cost == Decimal("57.53")  # 40.23 x 1.43
    assert IN_RATES["accountant"].hourly_cost == Decimal("216.37")  # 38,466 / 208 x 1.17
    assert value.seed_rates("INR") is IN_RATES and value.seed_rates("USD") is US_RATES


def test_seeded_baselines_are_sensible() -> None:
    for agent_type, (minutes, role) in value.SEED_BASELINES.items():
        assert 0 < minutes <= 1440, agent_type
        assert re.fullmatch(r"[a-z][a-z0-9_]*", role), role
    assert value.SEED_BASELINES["ap_processor"] == (12, "finance_associate")
    assert value.SEED_BASELINES["talent_acquisition"] == (20, "recruiter")


# ------------------------------------------------------------------ the estimate


def _baseline(agent_type: str, minutes: int, role: str, task: str = "*", confirmed: bool = True):
    return SimpleNamespace(
        id=f"b-{agent_type}-{task}",
        agent_type=agent_type,
        task_type=task,
        baseline_minutes=Decimal(minutes),
        role=role,
        confirmed_at=object() if confirmed else None,
        source="seeded",
    )


def test_a_specific_task_baseline_wins_over_the_agent_types() -> None:
    a = value.Assumptions(
        baselines=[_baseline("ap_processor", 12, "clerk"), _baseline("ap_processor", 30, "clerk", task="dispute")],
        rates={},
        roi_floor=Decimal("0"),
        roi_floor_custom=False,
    )
    assert a.baseline_for("ap_processor", "dispute").baseline_minutes == 30
    assert a.baseline_for("ap_processor", "match_invoice").baseline_minutes == 12
    assert a.baseline_for("ap_processor", None).baseline_minutes == 12
    assert a.baseline_for("unknown", None) is None


def _line(completed, failed, minutes, rate, *, b_ok=True, r_ok=True, agent="ap_processor"):
    line = value.ValueLine(agent_type=agent, task_type="", completed=completed, failed=failed)
    if minutes is not None:
        line.baseline_minutes = Decimal(minutes)
        line.hours_saved = Decimal(completed) * Decimal(minutes) / 60
        line.baseline_confirmed = b_ok
    if rate is not None:
        line.rate = Decimal(rate)
        line.rate_confirmed = r_ok
    if line.priced:
        line.gross = line.hours_saved * line.rate
        line.error_cost = Decimal(failed) * line.baseline_minutes / 60 * line.rate
    return line


def _estimate(lines, *, spend="1000", review_hours="2", reviewer="500", reviewer_ok=True, floor="5000"):
    return value.ValueEstimate(
        currency="INR",
        lines=lines,
        spend=Decimal(spend) if spend is not None else None,
        spend_source="x",
        review_hours=Decimal(review_hours),
        review_items=4,
        reviewer_rate=Decimal(reviewer),
        reviewer_confirmed=reviewer_ok,
        roi_floor=Decimal(floor),
    )


def test_the_estimate_adds_up() -> None:
    # 600 invoices x 12 min = 120 h at 150/h = 18,000 gross; 10 failed x 12 min x 150 = 300 error cost;
    # 2 review hours at 500 = 1,000; spend 6,000; net 18,000 - 6,000 - 1,000 - 300 = 10,700.
    est = _estimate([_line(600, 10, 12, 150)], spend="6000")
    assert est.hours_saved == Decimal(120) and est.gross == Decimal(18000)
    assert est.error_cost == Decimal(300) and est.review_cost == Decimal(1000)
    assert est.net == Decimal(10700)
    assert est.roi == Decimal(10700) / Decimal(6000)
    assert [r["label"] for r in value.waterfall(est)] == [
        "gross_saved",
        "spend",
        "review_cost",
        "error_cost",
        "net_saved",
    ]
    assert [r["value"] for r in value.waterfall(est)] == [18000.0, -6000.0, -1000.0, -300.0, 10700.0]
    assert est.confirmed and est.confidence == "high"


def test_roi_waits_for_the_spend_floor() -> None:
    assert _estimate([_line(600, 0, 12, 150)], spend="4999", floor="5000").roi is None
    assert _estimate([_line(600, 0, 12, 150)], spend="5000", floor="5000").roi is not None
    assert _estimate([_line(600, 0, 12, 150)], spend=None).roi is None


def test_savings_need_every_used_baseline_and_rate_confirmed() -> None:
    assert not _estimate([_line(10, 0, 12, 150, b_ok=False)]).confirmed
    assert not _estimate([_line(10, 0, 12, 150, r_ok=False)]).confirmed
    # The reviewer rate counts only when people spent time reviewing.
    assert not _estimate([_line(10, 0, 12, 150)], reviewer_ok=False).confirmed
    assert _estimate([_line(10, 0, 12, 150)], review_hours="0", reviewer_ok=False).confirmed
    # Nothing priced: nothing to confirm, nothing shown.
    assert not _estimate([_line(10, 0, None, None)]).confirmed


def test_unpriced_lines_are_listed_and_never_valued() -> None:
    est = _estimate(
        [_line(100, 0, 12, 150), _line(50, 0, None, None, agent="custom_bot"), _line(0, 0, None, None, agent="idle")]
    )
    assert [line.agent_type for line in est.unpriced] == ["custom_bot"]
    assert est.gross == Decimal(100) * 12 / 60 * 150
    rows = value.lines_as_rows(est)
    assert [r["agent_type"] for r in rows] == ["ap_processor", "custom_bot"]
    assert rows[1]["priced"] is False and rows[1]["gross_saved"] is None and rows[1]["confirmed"] is False


def test_confidence_follows_the_share_of_hours_from_confirmed_baselines() -> None:
    def est(confirmed_hours_share):
        a = _line(int(confirmed_hours_share * 60), 0, 60, 100, agent="a")
        b = _line(int((1 - confirmed_hours_share) * 60), 0, 60, 100, b_ok=False, agent="b")
        return _estimate([a, b])

    assert est(0.9).confidence == "high"
    assert est(0.6).confidence == "medium"
    assert est(0.3).confidence == "low"


@pytest.mark.asyncio
async def test_resolvers_hide_the_figures_until_confirmed(monkeypatch) -> None:
    unconfirmed = _estimate([_line(600, 10, 12, 150, b_ok=False)], spend="6000")
    confirmed = _estimate([_line(600, 10, 12, 150)], spend="6000")

    async def fake(ctx, period):
        return ctx.cache["est"]

    monkeypatch.setattr(value, "estimate_for", fake)
    ctx = SimpleNamespace(cache={"est": unconfirmed}, period=None, compare=None)
    for resolver in (value.resolve_money_saved, value.resolve_hours_saved, value.resolve_roi):
        out = await resolver(ctx)
        assert out["value"] is None and out["data_quality"] == "estimated" and out["hint"] == value.CONFIRM_HINT
        assert out["confirmation"]["confirmed"] is False
    ctx.cache["est"] = confirmed
    money = await value.resolve_money_saved(ctx)
    assert money["value"] == 10700.0 and money["rows"][-1] == {"label": "net_saved", "value": 10700.0}
    assert money["currency"] == "INR" and money["unit"] == "inr"
    assert (await value.resolve_hours_saved(ctx))["value"] == 120.0
    assert (await value.resolve_roi(ctx))["value"] == round(10700 / 6000 * 100, 1)
    ctx.cache["est"] = _estimate([], spend="6000")
    assert (await value.resolve_money_saved(ctx))["data_quality"] == "no_data"


# ------------------------------------------------------------------ alerts


def test_alert_owners_and_window() -> None:
    from datetime import date

    assert alerts.owner_for_domain("finance") == "cfo"
    assert alerts.owner_for_domain("ops") == "coo" and alerts.owner_for_domain("operations") == "coo"
    assert alerts.owner_for_domain("hr") == "chro" and alerts.owner_for_domain("marketing") == "cmo"
    assert alerts.owner_for_domain(None) == "admin" and alerts.owner_for_domain("legal") == "admin"
    current, previous = alerts.window(date(2026, 11, 2))
    assert (current.start, current.end) == (date(2026, 10, 27), date(2026, 11, 2))
    assert (previous.start, previous.end) == (date(2026, 10, 20), date(2026, 10, 26))


def test_alert_visibility_clause() -> None:
    from sqlalchemy.dialects import postgresql

    admin = alerts.visible_clause(InsightsAlert, "admin", is_admin=True)
    cfo = alerts.visible_clause(InsightsAlert, "CFO", is_admin=False)
    assert str(admin.compile(dialect=postgresql.dialect())) == "true"
    compiled = cfo.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    assert str(compiled) == "insights_alerts.owner_role = 'cfo'"


def test_rules_list_matches_what_the_sweep_can_raise() -> None:
    source = Path(alerts.__file__).read_text(encoding="utf-8")
    raised = set(re.findall(r'Candidate\(\s*"([a-z_.]+)"', source))
    assert raised == set(alerts.RULES)


def test_confirm_all_includes_the_reviewer_rate_when_people_reviewed() -> None:
    line = _line(10, 0, 12, 150, b_ok=False, r_ok=False)
    line.baseline_id, line.rate_id = "b1", "r1"
    est = _estimate([line], reviewer_ok=False)
    est.reviewer_rate_id = "rev"
    assert est.unconfirmed_ids() == (["b1"], ["r1", "rev"])
    est.review_hours = Decimal(0)
    assert est.unconfirmed_ids() == (["b1"], ["r1"])
