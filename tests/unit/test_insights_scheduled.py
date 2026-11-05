# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Scheduled Insights reports (part D): period presets, what a schedule may hold, and the cron evaluator."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from core.insights import scheduled
from core.reports import cron


def test_period_presets_count_whole_days_before_today() -> None:
    today = date(2026, 11, 4)  # a Wednesday
    assert scheduled.period_range("last_7_days", today) == (date(2026, 10, 28), date(2026, 11, 3))
    assert scheduled.period_range("last_30_days", today) == (date(2026, 10, 5), date(2026, 11, 3))
    assert scheduled.period_range("last_week", today) == (date(2026, 10, 26), date(2026, 11, 1))
    assert scheduled.period_range("last_month", today) == (date(2026, 10, 1), date(2026, 10, 31))
    assert scheduled.period_range("month_to_date", today) == (date(2026, 11, 1), date(2026, 11, 4))
    assert scheduled.period_range("last_month", date(2026, 1, 15)) == (date(2025, 12, 1), date(2025, 12, 31))
    with pytest.raises(scheduled.ScheduleParamsError):
        scheduled.period_range("yesterday", today)


def test_a_schedule_holds_only_what_its_role_may_read() -> None:
    assert scheduled.validate_params("insights_view", {"view": "finance", "x": 1}, "cfo", is_admin=False) == {
        "view": "finance",
        "period": "last_7_days",
    }
    with pytest.raises(PermissionError):
        scheduled.validate_params("insights_view", {"view": "executive"}, "cfo", is_admin=False)
    with pytest.raises(PermissionError):
        scheduled.validate_params("insights_finance_pack", {}, "cmo", is_admin=False)
    with pytest.raises(PermissionError):
        scheduled.validate_params("insights_evidence_pack", {}, "cfo", is_admin=False)
    assert scheduled.validate_params("insights_evidence_pack", {"period": "last_month"}, "", is_admin=True) == {
        "period": "last_month"
    }
    with pytest.raises(scheduled.ScheduleParamsError):
        scheduled.validate_params("insights_view", {"view": "nope"}, "", is_admin=True)
    with pytest.raises(scheduled.ScheduleParamsError):
        scheduled.validate_params("insights_view", {"view": "finance", "period": "forever"}, "cfo", is_admin=False)
    assert set(scheduled.REPORT_TYPES) == set(scheduled.FORMATS)


def test_cron_fields_and_the_day_rule() -> None:
    utc = ZoneInfo("UTC")
    start = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)  # Tuesday
    assert cron.next_after("0 9 * * 1", start, utc) == datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    assert cron.next_after("0 9 * * 7", start, utc) == datetime(2026, 11, 8, 9, 0, tzinfo=UTC)  # 7 is Sunday
    assert cron.next_after("*/20 10 * * *", start, utc) == datetime(2026, 11, 3, 10, 20, tzinfo=UTC)
    assert cron.next_after("0-30/15 11 * * *", start, utc) == datetime(2026, 11, 3, 11, 0, tzinfo=UTC)
    assert cron.next_after("0 0 29 2 *", start, utc) == datetime(2028, 2, 29, 0, 0, tzinfo=UTC)
    # Both day fields restricted: either matches (the 15th, or any Friday).
    assert cron.next_after("0 8 15 * 5", start, utc) == datetime(2026, 11, 6, 8, 0, tzinfo=UTC)
    # In the tenant's zone: 09:00 in Kolkata is 03:30 UTC.
    assert cron.next_after("0 9 * * *", start, ZoneInfo("Asia/Kolkata")) == datetime(2026, 11, 4, 3, 30, tzinfo=UTC)
    for bad in ("", "* * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *", "a * * * *", "*/0 * * * *"):
        with pytest.raises(cron.CronError):
            cron.next_after(bad, start, utc)


def test_the_migration_follows_v6z110() -> None:
    import importlib.util
    import re
    from pathlib import Path

    from core.models.report_schedule import RUN_STATUSES, RUN_TRIGGERS, ReportSchedule

    path = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z111_report_schedule_runs.py"
    spec = importlib.util.spec_from_file_location("_v6_z111_report_schedule_runs_unit", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "v6z111_report_schedule_runs" and len(migration.revision) <= 32
    assert migration.down_revision == "v6z110_insights_exports"
    children = [
        p.name
        for p in path.parent.glob("*.py")
        if re.search(r'^down_revision = "v6z110_insights_exports"$', p.read_text(encoding="utf-8"), re.M)
    ]
    assert children == [path.name]
    source = path.read_text(encoding="utf-8")
    assert ",".join(f"'{s}'" for s in RUN_STATUSES) in source and ",".join(f"'{t}'" for t in RUN_TRIGGERS) in source
    assert "created_by" in ReportSchedule.__table__.c and "FORCE ROW LEVEL SECURITY" in source
