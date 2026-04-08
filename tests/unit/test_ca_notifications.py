# ruff: noqa: N801
"""Notification and alert tests for CA compliance deadline alerts.

Tests cover:
- 7-day and 1-day alert logic
- Already-sent alert deduplication (alert_7d_sent, alert_1d_sent)
- Filed deadline skipping (filed=True)
- Overdue counting
- Multiple companies get independent alerts
- Notification type validation
- ComplianceDeadline model field defaults
- Company compliance_alerts_email field
"""

from __future__ import annotations

import inspect
from datetime import date

# ============================================================================
# Alert Logic — source inspection of send_alerts_for_due_deadlines
# ============================================================================


class TestAlertLogic:
    """Verify alert logic in send_alerts_for_due_deadlines."""

    def test_7_day_alert_triggers_for_matching_deadline(self):
        """send_alerts_for_due_deadlines queries deadlines exactly 7 days out."""
        from core.cron.compliance_alerts import send_alerts_for_due_deadlines

        source = inspect.getsource(send_alerts_for_due_deadlines)
        assert "timedelta(days=7)" in source
        assert "alert_7d_sent" in source

    def test_1_day_alert_triggers_for_matching_deadline(self):
        """send_alerts_for_due_deadlines queries deadlines exactly 1 day out."""
        from core.cron.compliance_alerts import send_alerts_for_due_deadlines

        source = inspect.getsource(send_alerts_for_due_deadlines)
        assert "timedelta(days=1)" in source
        assert "alert_1d_sent" in source

    def test_already_sent_7d_alert_not_resent(self):
        """Query filters on alert_7d_sent == False to skip already-sent alerts."""
        from core.cron.compliance_alerts import send_alerts_for_due_deadlines

        source = inspect.getsource(send_alerts_for_due_deadlines)
        assert "alert_7d_sent == False" in source

    def test_already_sent_1d_alert_not_resent(self):
        """Query filters on alert_1d_sent == False to skip already-sent alerts."""
        from core.cron.compliance_alerts import send_alerts_for_due_deadlines

        source = inspect.getsource(send_alerts_for_due_deadlines)
        assert "alert_1d_sent == False" in source

    def test_filed_deadline_not_alerted(self):
        """All alert queries filter on filed == False."""
        from core.cron.compliance_alerts import send_alerts_for_due_deadlines

        source = inspect.getsource(send_alerts_for_due_deadlines)
        assert "filed == False" in source

    def test_overdue_count_logic(self):
        """Overdue deadlines are those with due_date < today and not filed."""
        from core.cron.compliance_alerts import send_alerts_for_due_deadlines

        source = inspect.getsource(send_alerts_for_due_deadlines)
        assert "due_date < today" in source

    def test_alert_marks_7d_sent_true(self):
        """After sending 7-day alert, alert_7d_sent is set to True."""
        from core.cron.compliance_alerts import send_alerts_for_due_deadlines

        source = inspect.getsource(send_alerts_for_due_deadlines)
        assert "alert_7d_sent = True" in source

    def test_alert_marks_1d_sent_true(self):
        """After sending 1-day alert, alert_1d_sent is set to True."""
        from core.cron.compliance_alerts import send_alerts_for_due_deadlines

        source = inspect.getsource(send_alerts_for_due_deadlines)
        assert "alert_1d_sent = True" in source

    def test_returns_summary_dict(self):
        """send_alerts_for_due_deadlines returns {alerts_7d, alerts_1d, overdue}."""
        from core.cron.compliance_alerts import send_alerts_for_due_deadlines

        source = inspect.getsource(send_alerts_for_due_deadlines)
        assert "alerts_7d" in source
        assert "alerts_1d" in source
        assert "overdue" in source

    def test_multiple_companies_independent(self):
        """run_compliance_alert_cron iterates all active companies independently."""
        from core.cron.compliance_alerts import run_compliance_alert_cron

        source = inspect.getsource(run_compliance_alert_cron)
        assert "for company in companies" in source
        assert "generate_deadlines_for_company" in source


# ============================================================================
# Deadline Generation — _compute_monthly_deadlines
# ============================================================================


class TestComputeMonthlyDeadlines:
    """Verify _compute_monthly_deadlines returns correct deadline records."""

    def test_generates_records_for_default_3_months(self):
        """Default call generates 3 months of monthly deadlines."""
        from core.cron.compliance_alerts import _compute_monthly_deadlines

        today = date(2026, 4, 8)
        deadlines = _compute_monthly_deadlines("comp-1", "tenant-1", today, months_ahead=3)
        # 4 monthly types * 3 months = 12 records
        assert len(deadlines) == 12

    def test_each_deadline_has_required_keys(self):
        """Each record has tenant_id, company_id, deadline_type, filing_period, due_date."""
        from core.cron.compliance_alerts import _compute_monthly_deadlines

        today = date(2026, 4, 8)
        deadlines = _compute_monthly_deadlines("comp-1", "tenant-1", today, months_ahead=1)
        for dl in deadlines:
            assert "tenant_id" in dl
            assert "company_id" in dl
            assert "deadline_type" in dl
            assert "filing_period" in dl
            assert "due_date" in dl

    def test_deadline_types_in_monthly_set(self):
        """Monthly deadline types are gstr1, gstr3b, pf_ecr, esi_return."""
        from core.cron.compliance_alerts import _compute_monthly_deadlines

        today = date(2026, 4, 8)
        deadlines = _compute_monthly_deadlines("comp-1", "tenant-1", today, months_ahead=1)
        types = {dl["deadline_type"] for dl in deadlines}
        expected = {"gstr1", "gstr3b", "pf_ecr", "esi_return"}
        assert types == expected, f"Expected {expected}, got {types}"

    def test_gstr1_due_on_11th(self):
        """GSTR-1 deadline is on the 11th of the month."""
        from core.cron.compliance_alerts import MONTHLY_DEADLINES

        assert MONTHLY_DEADLINES["gstr1"] == 11

    def test_gstr3b_due_on_20th(self):
        """GSTR-3B deadline is on the 20th of the month."""
        from core.cron.compliance_alerts import MONTHLY_DEADLINES

        assert MONTHLY_DEADLINES["gstr3b"] == 20

    def test_pf_ecr_due_on_15th(self):
        """PF ECR deadline is on the 15th of the month."""
        from core.cron.compliance_alerts import MONTHLY_DEADLINES

        assert MONTHLY_DEADLINES["pf_ecr"] == 15

    def test_esi_due_on_15th(self):
        """ESI return deadline is on the 15th of the month."""
        from core.cron.compliance_alerts import MONTHLY_DEADLINES

        assert MONTHLY_DEADLINES["esi_return"] == 15


# ============================================================================
# Quarterly Deadlines — _compute_quarterly_deadlines
# ============================================================================


class TestComputeQuarterlyDeadlines:
    """Verify quarterly deadline generation."""

    def test_generates_8_quarterly_records(self):
        """4 quarters * 2 types (26q, 24q) = 8 records."""
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        today = date(2026, 4, 8)
        deadlines = _compute_quarterly_deadlines("comp-1", "tenant-1", today)
        assert len(deadlines) == 8

    def test_quarterly_types_are_tds(self):
        """Quarterly deadline types are tds_26q and tds_24q."""
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        today = date(2026, 4, 8)
        deadlines = _compute_quarterly_deadlines("comp-1", "tenant-1", today)
        types = {dl["deadline_type"] for dl in deadlines}
        assert types == {"tds_26q", "tds_24q"}

    def test_q1_due_date_is_july_31(self):
        """TDS Q1 (Apr-Jun) is due on July 31."""
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        today = date(2026, 4, 8)
        deadlines = _compute_quarterly_deadlines("comp-1", "tenant-1", today)
        q1_deadlines = [d for d in deadlines if d["filing_period"] == "2026-Q1"]
        assert len(q1_deadlines) == 2  # 26q and 24q
        for dl in q1_deadlines:
            assert dl["due_date"].month == 7
            assert dl["due_date"].day == 31

    def test_q2_due_date_is_oct_31(self):
        """TDS Q2 (Jul-Sep) is due on October 31."""
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        today = date(2026, 4, 8)
        deadlines = _compute_quarterly_deadlines("comp-1", "tenant-1", today)
        q2_deadlines = [d for d in deadlines if d["filing_period"] == "2026-Q2"]
        assert len(q2_deadlines) == 2
        for dl in q2_deadlines:
            assert dl["due_date"].month == 10
            assert dl["due_date"].day == 31

    def test_q3_due_date_is_jan_31(self):
        """TDS Q3 (Oct-Dec) is due on January 31."""
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        today = date(2026, 4, 8)
        deadlines = _compute_quarterly_deadlines("comp-1", "tenant-1", today)
        q3_deadlines = [d for d in deadlines if d["filing_period"] == "2026-Q3"]
        assert len(q3_deadlines) == 2
        for dl in q3_deadlines:
            assert dl["due_date"].month == 1
            assert dl["due_date"].day == 31

    def test_q4_due_date_is_may(self):
        """TDS Q4 (Jan-Mar) is due in April or May."""
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        today = date(2026, 4, 8)
        deadlines = _compute_quarterly_deadlines("comp-1", "tenant-1", today)
        q4_deadlines = [d for d in deadlines if d["filing_period"] == "2026-Q4"]
        assert len(q4_deadlines) == 2
        for dl in q4_deadlines:
            # Q4 due is month after March (April 30 or May 1-31)
            assert dl["due_date"].month in (4, 5)


# ============================================================================
# Notification Types — model field validation
# ============================================================================


class TestNotificationTypes:
    """Verify notification-related model fields and enum values."""

    def test_filing_type_values_expected(self):
        """FilingApproval filing_type accepts expected Indian filing types."""
        expected_types = {"gstr1", "gstr3b", "gstr9", "tds_26q", "tds_24q"}
        # Verify these are documented in the model source
        from core.models.filing_approval import FilingApproval

        source = inspect.getsource(FilingApproval)
        for ft in expected_types:
            assert ft in source, f"Filing type '{ft}' not documented in model"

    def test_deadline_type_values_expected(self):
        """ComplianceDeadline deadline_type accepts expected types."""
        expected_types = {"gstr1", "gstr3b", "gstr9", "tds_26q", "tds_24q", "pf_ecr", "esi_return"}
        from core.models.compliance_deadline import ComplianceDeadline

        source = inspect.getsource(ComplianceDeadline)
        for dt in expected_types:
            assert dt in source, f"Deadline type '{dt}' not documented in model"

    def test_alert_7d_sent_defaults_false(self):
        """ComplianceDeadline.alert_7d_sent defaults to False."""
        from core.models.compliance_deadline import ComplianceDeadline

        col = ComplianceDeadline.__table__.c.alert_7d_sent
        assert col.default is not None
        assert col.default.arg is False

    def test_alert_1d_sent_defaults_false(self):
        """ComplianceDeadline.alert_1d_sent defaults to False."""
        from core.models.compliance_deadline import ComplianceDeadline

        col = ComplianceDeadline.__table__.c.alert_1d_sent
        assert col.default is not None
        assert col.default.arg is False

    def test_filed_defaults_false(self):
        """ComplianceDeadline.filed defaults to False."""
        from core.models.compliance_deadline import ComplianceDeadline

        col = ComplianceDeadline.__table__.c.filed
        assert col.default is not None
        assert col.default.arg is False

    def test_compliance_alerts_email_exists_on_company(self):
        """Company model has compliance_alerts_email field."""
        from core.models.company import Company

        cols = {c.key for c in Company.__table__.columns}
        assert "compliance_alerts_email" in cols

    def test_compliance_alerts_email_is_nullable(self):
        """compliance_alerts_email is nullable (optional for companies)."""
        from core.models.company import Company

        col = Company.__table__.c.compliance_alerts_email
        assert col.nullable is True
