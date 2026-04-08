"""Compliance deadline alert cron job.

Runs daily via Celery Beat or Cloud Scheduler to send email alerts
for upcoming filing deadlines:
  - 7-day warning: first alert
  - 1-day warning: urgent reminder

Generates the statutory deadline calendar for each active company
based on Indian tax filing rules (GST, TDS, PF, ESI).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from core.database import async_session_factory
from core.models.company import Company
from core.models.compliance_deadline import ComplianceDeadline

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Indian statutory deadlines (day of month / quarter month)
# ---------------------------------------------------------------------------

# Monthly deadlines
MONTHLY_DEADLINES = {
    "gstr1": 11,       # GSTR-1 due on 11th of next month
    "gstr3b": 20,      # GSTR-3B due on 20th of next month
    "pf_ecr": 15,      # PF ECR due on 15th of next month
    "esi_return": 15,   # ESI due on 15th of next month
}

# Quarterly deadlines (month offsets from quarter end)
QUARTERLY_DEADLINES = {
    "tds_26q": (7, 31),     # TDS 26Q due on 31st July (Q1), 31 Oct, 31 Jan, 31 May
    "tds_24q": (7, 31),     # TDS 24Q same schedule
    "gstr9": (12, 31),      # GSTR-9 annual return due 31 Dec
}

# Quarter end months (Indian FY: Apr-Mar)
QUARTER_ENDS = {1: 6, 2: 9, 3: 12, 4: 3}  # Q1=Jun, Q2=Sep, Q3=Dec, Q4=Mar


def _compute_monthly_deadlines(
    company_id: str,
    tenant_id: str,
    today: date,
    months_ahead: int = 3,
) -> list[dict]:
    """Generate monthly deadline records for the next N months."""
    deadlines = []
    for month_offset in range(months_ahead):
        # Target month is next month + offset
        target = today.replace(day=1) + timedelta(days=32 * (month_offset + 1))
        target = target.replace(day=1)
        filing_period = target.strftime("%Y-%m")

        for dtype, day in MONTHLY_DEADLINES.items():
            try:
                due = target.replace(day=min(day, 28))
            except ValueError:
                due = target.replace(day=28)

            deadlines.append({
                "tenant_id": tenant_id,
                "company_id": company_id,
                "deadline_type": dtype,
                "filing_period": filing_period,
                "due_date": due,
            })

    return deadlines


def _compute_quarterly_deadlines(
    company_id: str,
    tenant_id: str,
    today: date,
) -> list[dict]:
    """Generate quarterly TDS deadline records for current FY."""
    deadlines = []
    fy_year = today.year if today.month >= 4 else today.year - 1

    for qtr, end_month in QUARTER_ENDS.items():
        year = fy_year if end_month >= 4 else fy_year + 1
        period = f"{fy_year}-Q{qtr}"

        # TDS due date: 31st of the month after quarter end
        due_month = end_month + 1
        due_year = year
        if due_month > 12:
            due_month -= 12
            due_year += 1

        try:
            due = date(due_year, due_month, 31)
        except ValueError:
            due = date(due_year, due_month, 30)

        for dtype in ["tds_26q", "tds_24q"]:
            deadlines.append({
                "tenant_id": tenant_id,
                "company_id": company_id,
                "deadline_type": dtype,
                "filing_period": period,
                "due_date": due,
            })

    return deadlines


async def generate_deadlines_for_company(
    session, company: Company
) -> int:
    """Generate all statutory deadlines for a company.  Returns count of new records."""
    today = datetime.now(UTC).date()
    company_id = str(company.id)
    tenant_id = str(company.tenant_id)

    all_deadlines = (
        _compute_monthly_deadlines(company_id, tenant_id, today)
        + _compute_quarterly_deadlines(company_id, tenant_id, today)
    )

    count = 0
    for dl in all_deadlines:
        # Check if deadline already exists (unique constraint)
        existing = await session.execute(
            select(ComplianceDeadline.id).where(
                ComplianceDeadline.company_id == dl["company_id"],
                ComplianceDeadline.deadline_type == dl["deadline_type"],
                ComplianceDeadline.filing_period == dl["filing_period"],
            ).limit(1)
        )
        if existing.scalar_one_or_none():
            continue

        record = ComplianceDeadline(**dl)
        session.add(record)
        count += 1

    if count:
        await session.flush()
    return count


async def send_alerts_for_due_deadlines(session, today: date | None = None) -> dict:
    """Check all unfiled deadlines and send alerts.

    Returns summary: {alerts_7d: N, alerts_1d: N, overdue: N}
    """
    if today is None:
        today = datetime.now(UTC).date()

    seven_days = today + timedelta(days=7)
    one_day = today + timedelta(days=1)

    summary = {"alerts_7d": 0, "alerts_1d": 0, "overdue": 0}

    # 7-day alerts: due in exactly 7 days, not yet sent
    result = await session.execute(
        select(ComplianceDeadline).where(
            ComplianceDeadline.filed == False,  # noqa: E712
            ComplianceDeadline.alert_7d_sent == False,  # noqa: E712
            ComplianceDeadline.due_date == seven_days,
        )
    )
    for deadline in result.scalars().all():
        # In production: send email via SendGrid to company.compliance_alerts_email
        logger.info(
            "7-day alert: %s %s due %s for company %s",
            deadline.deadline_type,
            deadline.filing_period,
            deadline.due_date,
            deadline.company_id,
        )
        deadline.alert_7d_sent = True
        deadline.updated_at = datetime.now(UTC)
        session.add(deadline)
        summary["alerts_7d"] += 1

    # 1-day alerts: due tomorrow, not yet sent
    result = await session.execute(
        select(ComplianceDeadline).where(
            ComplianceDeadline.filed == False,  # noqa: E712
            ComplianceDeadline.alert_1d_sent == False,  # noqa: E712
            ComplianceDeadline.due_date == one_day,
        )
    )
    for deadline in result.scalars().all():
        logger.info(
            "1-day URGENT alert: %s %s due %s for company %s",
            deadline.deadline_type,
            deadline.filing_period,
            deadline.due_date,
            deadline.company_id,
        )
        deadline.alert_1d_sent = True
        deadline.updated_at = datetime.now(UTC)
        session.add(deadline)
        summary["alerts_1d"] += 1

    # Count overdue
    result = await session.execute(
        select(ComplianceDeadline).where(
            ComplianceDeadline.filed == False,  # noqa: E712
            ComplianceDeadline.due_date < today,
        )
    )
    summary["overdue"] = len(result.scalars().all())

    await session.flush()
    return summary


async def run_compliance_alert_cron() -> dict:
    """Main entry point for the daily cron job.

    1. Generate missing deadlines for all active companies.
    2. Send 7-day and 1-day alerts.
    3. Return summary.
    """
    total_new_deadlines = 0
    alert_summary = {"alerts_7d": 0, "alerts_1d": 0, "overdue": 0}

    async with async_session_factory() as session:
        # Get all active companies
        result = await session.execute(
            select(Company).where(Company.is_active == True)  # noqa: E712
        )
        companies = result.scalars().all()

        for company in companies:
            new = await generate_deadlines_for_company(session, company)
            total_new_deadlines += new

        # Send alerts
        alert_summary = await send_alerts_for_due_deadlines(session)
        await session.commit()

    logger.info(
        "Compliance cron complete: new_deadlines=%d, 7d_alerts=%d, 1d_alerts=%d, overdue=%d",
        total_new_deadlines,
        alert_summary["alerts_7d"],
        alert_summary["alerts_1d"],
        alert_summary["overdue"],
    )

    return {
        "new_deadlines": total_new_deadlines,
        **alert_summary,
    }
