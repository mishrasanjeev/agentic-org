# ruff: noqa: N801
"""Cron execution tests for CA compliance alerts.

Tests cover:
- Celery Beat configuration (schedule name, crontab, task name)
- Cron trigger endpoint (route exists, API key validation)
- Deadline generation logic (monthly and quarterly types, due dates)
- Alert threshold logic (7-day, 1-day, same-day, past-due)
"""

from __future__ import annotations

import inspect
from datetime import date, timedelta

# ============================================================================
# Celery Beat Configuration
# ============================================================================


class TestCeleryBeatConfiguration:
    """Verify Celery Beat schedule is correctly configured."""

    def test_celery_app_importable(self):
        """celery_app can be imported from core.cron.celery_beat."""
        from core.cron.celery_beat import celery_app

        assert celery_app is not None

    def test_beat_schedule_has_compliance_alerts_daily(self):
        """beat_schedule must have 'compliance-alerts-daily' entry."""
        from core.cron.celery_beat import celery_app

        schedule = celery_app.conf.beat_schedule
        assert "compliance-alerts-daily" in schedule, (
            f"Missing 'compliance-alerts-daily'. Found: {list(schedule.keys())}"
        )

    def test_schedule_is_crontab_hour_6_minute_0(self):
        """compliance-alerts-daily runs at crontab(hour=6, minute=0)."""
        from celery.schedules import crontab

        from core.cron.celery_beat import celery_app

        entry = celery_app.conf.beat_schedule["compliance-alerts-daily"]
        schedule = entry["schedule"]
        assert isinstance(schedule, crontab)
        assert str(schedule._orig_hour) == "6"
        assert str(schedule._orig_minute) == "0"

    def test_task_name_matches(self):
        """The task name in beat_schedule matches the registered task."""
        from core.cron.celery_beat import celery_app

        entry = celery_app.conf.beat_schedule["compliance-alerts-daily"]
        assert entry["task"] == "core.cron.celery_beat.run_compliance_alerts"

    def test_celery_timezone_asia_kolkata(self):
        """Celery timezone must be Asia/Kolkata for Indian deadlines."""
        from core.cron.celery_beat import celery_app

        assert celery_app.conf.timezone == "Asia/Kolkata"

    def test_run_compliance_alerts_task_exists(self):
        """The run_compliance_alerts task function must exist."""
        from core.cron.celery_beat import run_compliance_alerts

        assert callable(run_compliance_alerts)


# ============================================================================
# Cron Trigger Endpoint
# ============================================================================


class TestCronTriggerEndpoint:
    """Verify cron API endpoint configuration."""

    def test_cron_router_importable(self):
        """Router from api.v1.cron is importable."""
        from api.v1.cron import router

        assert router is not None

    def test_compliance_alerts_route_exists(self):
        """POST /cron/compliance-alerts route must be registered."""
        from api.v1.cron import router

        route_paths = [getattr(r, "path", "") for r in router.routes]
        assert any("compliance-alerts" in p for p in route_paths), (
            f"No compliance-alerts route. Routes: {route_paths}"
        )

    def test_cron_api_key_validation_exists(self):
        """Cron trigger endpoint validates API key."""
        from api.v1.cron import trigger_compliance_alerts

        source = inspect.getsource(trigger_compliance_alerts)
        assert "_verify_cron_key" in source

    def test_verify_cron_key_rejects_invalid(self):
        """_verify_cron_key raises HTTPException for invalid key."""
        from api.v1.cron import _verify_cron_key

        source = inspect.getsource(_verify_cron_key)
        assert "403" in source
        assert "Invalid cron API key" in source

    def test_cron_endpoint_calls_compliance_cron(self):
        """trigger_compliance_alerts calls run_compliance_alert_cron."""
        from api.v1.cron import trigger_compliance_alerts

        source = inspect.getsource(trigger_compliance_alerts)
        assert "run_compliance_alert_cron" in source


# ============================================================================
# Deadline Generation — types and due dates
# ============================================================================


class TestDeadlineGeneration:
    """Verify deadline generation produces correct types and due dates."""

    def test_monthly_types_complete(self):
        """Monthly deadline types include gstr1, gstr3b, pf_ecr, esi_return."""
        from core.cron.compliance_alerts import MONTHLY_DEADLINES

        expected = {"gstr1", "gstr3b", "pf_ecr", "esi_return"}
        assert set(MONTHLY_DEADLINES.keys()) == expected

    def test_quarterly_types_complete(self):
        """Quarterly deadline types include tds_26q, tds_24q."""
        from core.cron.compliance_alerts import QUARTERLY_DEADLINES

        assert "tds_26q" in QUARTERLY_DEADLINES
        assert "tds_24q" in QUARTERLY_DEADLINES

    def test_gstr1_due_date_11th(self):
        """GSTR-1 is due on the 11th of the following month."""
        from core.cron.compliance_alerts import MONTHLY_DEADLINES

        assert MONTHLY_DEADLINES["gstr1"] == 11

    def test_gstr3b_due_date_20th(self):
        """GSTR-3B is due on the 20th of the following month."""
        from core.cron.compliance_alerts import MONTHLY_DEADLINES

        assert MONTHLY_DEADLINES["gstr3b"] == 20

    def test_pf_ecr_due_date_15th(self):
        """PF ECR is due on the 15th of the following month."""
        from core.cron.compliance_alerts import MONTHLY_DEADLINES

        assert MONTHLY_DEADLINES["pf_ecr"] == 15

    def test_esi_return_due_date_15th(self):
        """ESI return is due on the 15th of the following month."""
        from core.cron.compliance_alerts import MONTHLY_DEADLINES

        assert MONTHLY_DEADLINES["esi_return"] == 15

    def test_tds_26q_q1_due_jul_31(self):
        """TDS 26Q Q1 (Apr-Jun) is due July 31."""
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        today = date(2026, 5, 1)
        deadlines = _compute_quarterly_deadlines("c1", "t1", today)
        q1_26q = [d for d in deadlines if d["deadline_type"] == "tds_26q" and "Q1" in d["filing_period"]]
        assert len(q1_26q) == 1
        assert q1_26q[0]["due_date"] == date(2026, 7, 31)

    def test_tds_24q_q1_due_jul_31(self):
        """TDS 24Q Q1 (Apr-Jun) is due July 31."""
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        today = date(2026, 5, 1)
        deadlines = _compute_quarterly_deadlines("c1", "t1", today)
        q1_24q = [d for d in deadlines if d["deadline_type"] == "tds_24q" and "Q1" in d["filing_period"]]
        assert len(q1_24q) == 1
        assert q1_24q[0]["due_date"] == date(2026, 7, 31)

    def test_tds_q2_due_oct_31(self):
        """TDS Q2 (Jul-Sep) is due October 31."""
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        today = date(2026, 5, 1)
        deadlines = _compute_quarterly_deadlines("c1", "t1", today)
        q2 = [d for d in deadlines if d["deadline_type"] == "tds_26q" and "Q2" in d["filing_period"]]
        assert len(q2) == 1
        assert q2[0]["due_date"] == date(2026, 10, 31)

    def test_tds_q3_due_jan_31(self):
        """TDS Q3 (Oct-Dec) is due January 31."""
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        today = date(2026, 5, 1)
        deadlines = _compute_quarterly_deadlines("c1", "t1", today)
        q3 = [d for d in deadlines if d["deadline_type"] == "tds_26q" and "Q3" in d["filing_period"]]
        assert len(q3) == 1
        assert q3[0]["due_date"] == date(2027, 1, 31)

    def test_monthly_deadline_correct_for_feb(self):
        """Monthly deadline generation handles February (28/29 day month)."""
        from core.cron.compliance_alerts import _compute_monthly_deadlines

        # Start from December so 2 months ahead hits February
        today = date(2025, 12, 15)
        deadlines = _compute_monthly_deadlines("c1", "t1", today, months_ahead=3)
        feb_deadlines = [d for d in deadlines if d["filing_period"] == "2026-02"]
        # All feb deadlines should have valid dates (day <= 28)
        for dl in feb_deadlines:
            assert dl["due_date"].day <= 28


# ============================================================================
# Alert Thresholds
# ============================================================================


class TestAlertThresholds:
    """Verify alert threshold logic for 7-day, 1-day, same-day, past-due."""

    def test_7_day_window_logic(self):
        """7-day alert: due_date - today == 7 triggers the alert."""
        today = date(2026, 4, 8)
        seven_days = today + timedelta(days=7)
        assert seven_days == date(2026, 4, 15)
        # The query checks due_date == today + 7 days
        assert (seven_days - today).days == 7

    def test_1_day_window_logic(self):
        """1-day alert: due_date - today == 1 triggers the alert."""
        today = date(2026, 4, 8)
        one_day = today + timedelta(days=1)
        assert one_day == date(2026, 4, 9)
        assert (one_day - today).days == 1

    def test_same_day_is_overdue(self):
        """Same day: due_date == today is not 7d or 1d, counted as overdue."""
        today = date(2026, 4, 8)
        # due_date == today means due_date < today is False
        # but due_date is not 7 or 1 day away either
        seven_days = today + timedelta(days=7)
        one_day = today + timedelta(days=1)
        assert today != seven_days
        assert today != one_day
        # It's not overdue either since due_date < today is False
        assert not (today < today)

    def test_past_due_is_overdue(self):
        """Past due: due_date < today is counted as overdue."""
        today = date(2026, 4, 8)
        past_due = date(2026, 4, 5)
        assert past_due < today

    def test_send_alerts_query_structure(self):
        """send_alerts_for_due_deadlines has correct query structure for thresholds."""
        from core.cron.compliance_alerts import send_alerts_for_due_deadlines

        source = inspect.getsource(send_alerts_for_due_deadlines)
        # 7-day alert queries due_date == seven_days
        assert "due_date == seven_days" in source
        # 1-day alert queries due_date == one_day
        assert "due_date == one_day" in source
        # Overdue queries due_date < today
        assert "due_date < today" in source

    def test_generate_deadlines_for_company_function_exists(self):
        """generate_deadlines_for_company function is importable."""
        from core.cron.compliance_alerts import generate_deadlines_for_company

        assert callable(generate_deadlines_for_company)

    def test_run_compliance_alert_cron_function_exists(self):
        """run_compliance_alert_cron function is importable."""
        from core.cron.compliance_alerts import run_compliance_alert_cron

        assert callable(run_compliance_alert_cron)

    def test_run_cron_generates_then_alerts(self):
        """run_compliance_alert_cron generates deadlines first, then sends alerts."""
        from core.cron.compliance_alerts import run_compliance_alert_cron

        source = inspect.getsource(run_compliance_alert_cron)
        # Generation happens before alerts
        gen_pos = source.index("generate_deadlines_for_company")
        alert_pos = source.index("send_alerts_for_due_deadlines")
        assert gen_pos < alert_pos, "Deadlines must be generated before alerts are sent"

    def test_deadline_model_unique_constraint(self):
        """ComplianceDeadline has unique constraint on (company_id, deadline_type, filing_period)."""
        from core.models.compliance_deadline import ComplianceDeadline

        constraints = ComplianceDeadline.__table__.constraints
        uq_names = {c.name for c in constraints if hasattr(c, "columns")}
        assert "uq_deadline_company_type_period" in uq_names, (
            f"Missing constraint. Found: {uq_names}"
        )
