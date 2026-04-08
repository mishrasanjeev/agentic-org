# ruff: noqa: N801
"""Dedicated unit tests for the compliance deadline cron job logic.

Tests cover:
- _compute_monthly_deadlines: generates 3 months ahead, 4 types per month
- _compute_quarterly_deadlines: generates 4 quarters x 2 TDS types
- Deadline due dates are correct for Indian FY (Apr-Mar)
- Idempotency: duplicate deadlines are skipped
- GSTR-1 on 11th, GSTR-3B on 20th, PF/ESI on 15th
- Monthly deadline type names from expected set
"""

from __future__ import annotations

import uuid
from datetime import date

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TENANT_ID = str(uuid.UUID("00000000-0000-0000-0000-000000000001"))
COMPANY_ID = str(uuid.UUID("00000000-0000-0000-0000-000000000010"))

EXPECTED_MONTHLY_TYPES = {"gstr1", "gstr3b", "pf_ecr", "esi_return"}


# ============================================================================
# Monthly deadline tests
# ============================================================================


class TestComputeMonthlyDeadlines:
    """Test _compute_monthly_deadlines for various dates."""

    def _compute(self, today: date, months_ahead: int = 3) -> list[dict]:
        from core.cron.compliance_alerts import _compute_monthly_deadlines

        return _compute_monthly_deadlines(
            company_id=COMPANY_ID,
            tenant_id=TENANT_ID,
            today=today,
            months_ahead=months_ahead,
        )

    def test_generates_12_records_for_3_months(self):
        """3 months x 4 types = 12 records."""
        results = self._compute(date(2026, 4, 8))
        assert len(results) == 12

    def test_filing_periods_are_future_months(self):
        """Deadlines should be for May, Jun, Jul 2026 when today is April 8."""
        results = self._compute(date(2026, 4, 8))
        periods = sorted({r["filing_period"] for r in results})
        assert "2026-05" in periods
        assert "2026-06" in periods
        assert "2026-07" in periods

    def test_each_month_has_4_types(self):
        """Every month should have exactly 4 deadline types."""
        results = self._compute(date(2026, 4, 8))
        by_period: dict[str, set[str]] = {}
        for r in results:
            by_period.setdefault(r["filing_period"], set()).add(r["deadline_type"])
        for period, types in by_period.items():
            assert types == EXPECTED_MONTHLY_TYPES, (
                f"Period {period} has types {types}, expected {EXPECTED_MONTHLY_TYPES}"
            )

    def test_gstr1_always_on_11th(self):
        results = self._compute(date(2026, 4, 8))
        for r in results:
            if r["deadline_type"] == "gstr1":
                assert r["due_date"].day == 11, (
                    f"GSTR-1 for {r['filing_period']} due on day {r['due_date'].day}, expected 11"
                )

    def test_gstr3b_always_on_20th(self):
        results = self._compute(date(2026, 4, 8))
        for r in results:
            if r["deadline_type"] == "gstr3b":
                assert r["due_date"].day == 20, (
                    f"GSTR-3B for {r['filing_period']} due on day {r['due_date'].day}, expected 20"
                )

    def test_pf_ecr_always_on_15th(self):
        results = self._compute(date(2026, 4, 8))
        for r in results:
            if r["deadline_type"] == "pf_ecr":
                assert r["due_date"].day == 15

    def test_esi_return_always_on_15th(self):
        results = self._compute(date(2026, 4, 8))
        for r in results:
            if r["deadline_type"] == "esi_return":
                assert r["due_date"].day == 15

    def test_deadline_type_names_from_expected_set(self):
        """All deadline_type values must be from the known set."""
        results = self._compute(date(2026, 4, 8))
        for r in results:
            assert r["deadline_type"] in EXPECTED_MONTHLY_TYPES, (
                f"Unexpected deadline type: {r['deadline_type']}"
            )

    def test_company_and_tenant_ids_propagated(self):
        results = self._compute(date(2026, 4, 8))
        for r in results:
            assert r["company_id"] == COMPANY_ID
            assert r["tenant_id"] == TENANT_ID

    def test_single_month_generates_4_records(self):
        results = self._compute(date(2026, 4, 8), months_ahead=1)
        assert len(results) == 4

    def test_february_handles_short_month(self):
        """Feb has 28/29 days -- due dates should not crash."""
        results = self._compute(date(2026, 1, 15), months_ahead=1)
        # Target month is Feb 2026
        assert len(results) == 4
        for r in results:
            assert r["due_date"].month == 2


# ============================================================================
# Quarterly deadline tests
# ============================================================================


class TestComputeQuarterlyDeadlines:
    """Test _compute_quarterly_deadlines for Indian FY quarters."""

    def _compute(self, today: date) -> list[dict]:
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        return _compute_quarterly_deadlines(
            company_id=COMPANY_ID,
            tenant_id=TENANT_ID,
            today=today,
        )

    def test_generates_8_records(self):
        """4 quarters x 2 TDS types = 8 records."""
        results = self._compute(date(2026, 4, 8))
        assert len(results) == 8

    def test_tds_types_only(self):
        results = self._compute(date(2026, 4, 8))
        types = {r["deadline_type"] for r in results}
        assert types == {"tds_26q", "tds_24q"}

    def test_four_quarter_periods(self):
        results = self._compute(date(2026, 4, 8))
        periods = {r["filing_period"] for r in results}
        assert len(periods) == 4
        # FY starts Apr 2026 -> fy_year = 2026
        for qtr in range(1, 5):
            assert f"2026-Q{qtr}" in periods

    def test_q1_due_july_31(self):
        """Q1 (Apr-Jun) TDS due 31 July of same year."""
        results = self._compute(date(2026, 4, 8))
        q1 = [r for r in results if r["filing_period"] == "2026-Q1"]
        assert len(q1) == 2
        for r in q1:
            assert r["due_date"] == date(2026, 7, 31)

    def test_q2_due_october_31(self):
        """Q2 (Jul-Sep) TDS due 31 October."""
        results = self._compute(date(2026, 4, 8))
        q2 = [r for r in results if r["filing_period"] == "2026-Q2"]
        assert len(q2) == 2
        for r in q2:
            assert r["due_date"] == date(2026, 10, 31)

    def test_q3_due_january_31(self):
        """Q3 (Oct-Dec) TDS due 31 January of next year."""
        results = self._compute(date(2026, 4, 8))
        q3 = [r for r in results if r["filing_period"] == "2026-Q3"]
        assert len(q3) == 2
        for r in q3:
            assert r["due_date"] == date(2027, 1, 31)

    def test_q4_due_after_march(self):
        """Q4 (Jan-Mar) TDS due in April (30th, since Apr has 30 days)."""
        results = self._compute(date(2026, 4, 8))
        q4 = [r for r in results if r["filing_period"] == "2026-Q4"]
        assert len(q4) == 2
        for r in q4:
            # Q4 end month = March (3), next month = April
            assert r["due_date"].month == 4
            assert r["due_date"].day == 30

    def test_fy_detection_pre_april(self):
        """When today is Jan-Mar, FY year is previous calendar year."""
        results = self._compute(date(2027, 2, 15))
        periods = {r["filing_period"] for r in results}
        # FY 2026-27 -> fy_year = 2026
        for qtr in range(1, 5):
            assert f"2026-Q{qtr}" in periods


# ============================================================================
# Idempotency tests (logic-level, no DB)
# ============================================================================


class TestDeadlineIdempotency:
    """Verify that repeated computation produces identical records.

    The actual idempotency (skip on unique constraint) requires a DB session,
    but we can verify the computed data is deterministic.
    """

    def test_monthly_deadlines_are_deterministic(self):
        from core.cron.compliance_alerts import _compute_monthly_deadlines

        run1 = _compute_monthly_deadlines(COMPANY_ID, TENANT_ID, date(2026, 4, 8))
        run2 = _compute_monthly_deadlines(COMPANY_ID, TENANT_ID, date(2026, 4, 8))
        assert run1 == run2

    def test_quarterly_deadlines_are_deterministic(self):
        from core.cron.compliance_alerts import _compute_quarterly_deadlines

        run1 = _compute_quarterly_deadlines(COMPANY_ID, TENANT_ID, date(2026, 4, 8))
        run2 = _compute_quarterly_deadlines(COMPANY_ID, TENANT_ID, date(2026, 4, 8))
        assert run1 == run2

    def test_unique_key_per_deadline(self):
        """Each deadline should have a unique (company_id, deadline_type, filing_period) tuple."""
        from core.cron.compliance_alerts import (
            _compute_monthly_deadlines,
            _compute_quarterly_deadlines,
        )

        monthly = _compute_monthly_deadlines(COMPANY_ID, TENANT_ID, date(2026, 4, 8))
        quarterly = _compute_quarterly_deadlines(COMPANY_ID, TENANT_ID, date(2026, 4, 8))
        all_deadlines = monthly + quarterly

        keys = [
            (d["company_id"], d["deadline_type"], d["filing_period"])
            for d in all_deadlines
        ]
        assert len(keys) == len(set(keys)), "Duplicate (company, type, period) keys found"


# ============================================================================
# Constants validation
# ============================================================================


class TestCronConstants:
    """Verify the statutory deadline constants are correctly defined."""

    def test_monthly_deadline_types_complete(self):
        from core.cron.compliance_alerts import MONTHLY_DEADLINES

        assert set(MONTHLY_DEADLINES.keys()) == {"gstr1", "gstr3b", "pf_ecr", "esi_return"}

    def test_monthly_deadline_days(self):
        from core.cron.compliance_alerts import MONTHLY_DEADLINES

        assert MONTHLY_DEADLINES["gstr1"] == 11
        assert MONTHLY_DEADLINES["gstr3b"] == 20
        assert MONTHLY_DEADLINES["pf_ecr"] == 15
        assert MONTHLY_DEADLINES["esi_return"] == 15

    def test_quarter_ends_indian_fy(self):
        from core.cron.compliance_alerts import QUARTER_ENDS

        assert QUARTER_ENDS[1] == 6   # Q1 = Apr-Jun
        assert QUARTER_ENDS[2] == 9   # Q2 = Jul-Sep
        assert QUARTER_ENDS[3] == 12  # Q3 = Oct-Dec
        assert QUARTER_ENDS[4] == 3   # Q4 = Jan-Mar
