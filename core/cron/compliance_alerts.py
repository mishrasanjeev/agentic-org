"""Compliance deadline alert cron job.

Runs daily via Celery Beat or Cloud Scheduler to send email alerts
for upcoming filing deadlines:
  - 7-day warning: first alert
  - 1-day warning: urgent reminder

Generates the statutory deadline calendar for each active company
based on Indian tax filing rules (GST, TDS, PF, ESI).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select, text

from core.database import async_session_factory, get_tenant_session
from core.models.company import Company
from core.models.compliance_deadline import ComplianceDeadline
from core.models.tenant import Tenant

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

        # TDS due date: 31st of the month after quarter end (Q1-Q3).
        # Q4 (Jan-Mar) is the exception: 24Q/26Q are due 31 May, not 30 Apr
        # (Rule 31A, Income-tax Rules).
        due_month = end_month + 1
        due_year = year
        if due_month > 12:
            due_month -= 12
            due_year += 1

        if qtr == 4:
            due = date(due_year, 5, 31)
        else:
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


_DEADLINE_LABELS = {
    "gstr1": "GSTR-1",
    "gstr3b": "GSTR-3B",
    "pf_ecr": "PF ECR",
    "esi_return": "ESI return",
    "tds_26q": "TDS 26Q",
    "tds_24q": "TDS 24Q",
    "gstr9": "GSTR-9",
}

# Distinct advisory-lock key for this job (arbitrary constant, bigint).
_CRON_LOCK_KEY = 7_204_301_001


async def _alert_recipient(session, company_id: Any, cache: dict[str, str | None]) -> str | None:
    """Resolve the company's compliance alert email (cached per run)."""
    key = str(company_id)
    if key not in cache:
        row = await session.execute(
            select(Company.compliance_alerts_email).where(Company.id == company_id)
        )
        cache[key] = (row.scalar_one_or_none() or "").strip() or None
    return cache[key]


async def _send_deadline_alert(deadline: ComplianceDeadline, to: str, urgent: bool) -> bool:
    """Deliver one alert email. Returns True only when the transport accepted it."""
    from html import escape

    from core.email import send_email

    label = _DEADLINE_LABELS.get(deadline.deadline_type, deadline.deadline_type.upper())
    when = "tomorrow" if urgent else "in 7 days"
    subject = f"{'URGENT: ' if urgent else ''}{label} for {deadline.filing_period} due {when}"
    body = (
        f"<h2>{'Urgent filing reminder' if urgent else 'Filing reminder'}</h2>"
        f"<p><b>{escape(label)}</b> for period <b>{escape(str(deadline.filing_period))}</b> "
        f"is due on <b>{deadline.due_date.isoformat()}</b> ({when}).</p>"
        "<p>Mark the filing as complete in AgenticOrg once submitted.</p>"
    )
    # send_email is synchronous SMTP; keep the event loop free.
    return bool(await asyncio.to_thread(send_email, to, subject, body))


async def send_alerts_for_due_deadlines(session, today: date | None = None) -> dict:
    """Check all unfiled deadlines and send alerts.

    Returns summary: {alerts_7d: N, alerts_1d: N, overdue: N, skipped_no_recipient: N, failed: N}

    ``alert_7d_sent`` / ``alert_1d_sent`` are set ONLY after ``send_email``
    reports success, so a delivery failure is retried on the next run instead
    of being silently recorded as sent.
    """
    if today is None:
        today = datetime.now(UTC).date()

    seven_days = today + timedelta(days=7)
    one_day = today + timedelta(days=1)

    summary = {"alerts_7d": 0, "alerts_1d": 0, "overdue": 0, "skipped_no_recipient": 0, "failed": 0}
    recipients: dict[str, str | None] = {}

    async def _process(deadlines, *, urgent: bool, counter: str) -> None:
        for deadline in deadlines:
            to = await _alert_recipient(session, deadline.company_id, recipients)
            if not to:
                summary["skipped_no_recipient"] += 1
                logger.warning(
                    "compliance_alert_no_recipient deadline_type=%s period=%s company=%s",
                    deadline.deadline_type, deadline.filing_period, deadline.company_id,
                )
                continue
            try:
                sent = await _send_deadline_alert(deadline, to, urgent)
            # enterprise-gate: broad-except-ok reason=alert-delivery-failure-leaves-flag-unset-for-retry
            except Exception as exc:  # noqa: BLE001
                sent = False
                logger.error("compliance_alert_send_error company=%s error=%s", deadline.company_id, exc)
            if not sent:
                summary["failed"] += 1
                continue
            logger.info(
                "%s alert sent: %s %s due %s for company %s",
                "1-day URGENT" if urgent else "7-day",
                deadline.deadline_type, deadline.filing_period, deadline.due_date, deadline.company_id,
            )
            if urgent:
                deadline.alert_1d_sent = True
            else:
                deadline.alert_7d_sent = True
            deadline.updated_at = datetime.now(UTC)
            session.add(deadline)
            summary[counter] += 1

    # 7-day alerts: due in exactly 7 days, not yet sent
    result = await session.execute(
        select(ComplianceDeadline).where(
            ComplianceDeadline.filed == False,  # noqa: E712
            ComplianceDeadline.alert_7d_sent == False,  # noqa: E712
            ComplianceDeadline.due_date == seven_days,
        )
    )
    await _process(result.scalars().all(), urgent=False, counter="alerts_7d")

    # 1-day alerts: due tomorrow, not yet sent
    result = await session.execute(
        select(ComplianceDeadline).where(
            ComplianceDeadline.filed == False,  # noqa: E712
            ComplianceDeadline.alert_1d_sent == False,  # noqa: E712
            ComplianceDeadline.due_date == one_day,
        )
    )
    await _process(result.scalars().all(), urgent=True, counter="alerts_1d")

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
    alert_summary: dict[str, Any] = {"alerts_7d": 0, "alerts_1d": 0, "overdue": 0}

    async with async_session_factory() as lock_session:
        # Beat and the /cron/compliance-alerts endpoint can overlap; only one
        # run may generate deadlines + send alerts at a time (the lock is
        # released with the transaction, so this session stays open for the
        # whole run).
        locked = (
            await lock_session.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _CRON_LOCK_KEY}
            )
        ).scalar_one()
        if not locked:
            logger.info("Compliance cron skipped: another run holds the lock")
            return {"new_deadlines": 0, **alert_summary, "skipped": "locked"}

        # ``companies`` / ``compliance_deadlines`` are FORCE-RLS: a raw
        # cross-tenant SELECT returns no rows and INSERT/UPDATE fail WITH
        # CHECK under a non-BYPASSRLS role. Enumerate tenants (failing loudly
        # if the role cannot bypass RLS) and do every read/write inside that
        # tenant's exact RLS context.
        await lock_session.execute(text("SET LOCAL row_security = off"))
        tenant_ids = [
            row[0]
            for row in (
                await lock_session.execute(select(Tenant.id).where(Tenant.deleted_at.is_(None)))
            ).all()
        ]

        for tenant_id in tenant_ids:
            async with get_tenant_session(tenant_id) as session:
                # Get all active companies for this tenant
                result = await session.execute(
                    select(Company).where(
                        Company.tenant_id == tenant_id,
                        Company.is_active == True,  # noqa: E712
                    )
                )
                companies = result.scalars().all()

                for company in companies:
                    new = await generate_deadlines_for_company(session, company)
                    total_new_deadlines += new

                # Send alerts (deadlines visible in this tenant's session)
                tenant_summary = await send_alerts_for_due_deadlines(session)
                for key, value in tenant_summary.items():
                    alert_summary[key] = alert_summary.get(key, 0) + value
                # get_tenant_session commits on exit.

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
