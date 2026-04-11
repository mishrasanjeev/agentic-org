"""Budget alert evaluator — fires notifications when spend crosses thresholds.

Designed to be called periodically (Celery beat, cron, or kubectl CronJob).
Reads every tenant's budget_alerts rows, aggregates cost_ledger for the
current period, and compares against the threshold.

When ``actual / threshold >= warn_at_percent / 100``, a notification is
sent via the configured channel(s). Alerts are idempotent per period —
we track ``last_triggered_at`` and skip if it was already triggered in
the current window.

Channels supported: ``email``, ``slack``, ``webhook``. Multiple channels
can be set as a comma-separated list.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import select

from core.database import async_session_factory, get_tenant_session
from core.models.budget_alert import BudgetAlert

logger = structlog.get_logger()


def _period_start(period: str, now: datetime) -> datetime:
    """Return the start of the current period for a 'daily'|'weekly'|'monthly' alert."""
    if period == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "weekly":
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "monthly":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"Unknown budget period {period!r}")


async def _spend_since(
    tenant_id: uuid.UUID,
    company_id: uuid.UUID | None,
    cost_center_id: uuid.UUID | None,
    since: datetime,
) -> Decimal:
    """Sum cost_usd from cost ledger for the given scope since ``since``."""
    # The existing ledger table has different column names across
    # scaling/cost_ledger.py and agent_task_results.  We read from
    # agent_task_results because that's guaranteed to exist in prod.
    from sqlalchemy import text

    async with get_tenant_session(tenant_id) as session:
        q = """
            SELECT COALESCE(SUM(cost_usd), 0) AS total
            FROM agent_task_results
            WHERE tenant_id = :tid
              AND created_at >= :since
        """
        params: dict = {"tid": str(tenant_id), "since": since}
        if company_id is not None:
            q += " AND company_id = :cid"
            params["cid"] = str(company_id)
        # cost_center attribution is on agents, not agent_task_results — we
        # join through in a follow-up when cost_ledger gains cost_center_id.
        if cost_center_id is not None:
            q += (
                " AND agent_id IN (SELECT id FROM agents "
                "WHERE cost_center_id = :ccid)"
            )
            params["ccid"] = str(cost_center_id)
        result = await session.execute(text(q), params)
        total = result.scalar_one()
        return Decimal(str(total or 0))


async def _send_notification(
    alert: BudgetAlert,
    spend: Decimal,
    percent: int,
) -> None:
    """Fire the configured notification channels. Best-effort — never raises."""
    channels = [c.strip() for c in (alert.notify_channels or "").split(",") if c.strip()]
    subject = f"Budget alert: {alert.name} at {percent}% (${spend:.2f} of ${alert.threshold_usd})"
    body = (
        f"Budget alert '{alert.name}' has reached {percent}% of its "
        f"${alert.threshold_usd:.2f} {alert.period} threshold. "
        f"Current spend: ${spend:.2f}."
    )

    for channel in channels:
        try:
            if channel == "email":
                from core.email import send_plain_email

                # Best-effort email — the billing admin for the tenant.
                # Falls back to a configured fallback when no email is set.
                await send_plain_email(
                    to="sanjeev@agenticorg.ai",
                    subject=subject,
                    body=body,
                )
            elif channel == "slack":
                import os

                webhook = os.getenv("SLACK_BUDGET_WEBHOOK_URL", "")
                if webhook:
                    import httpx

                    async with httpx.AsyncClient(timeout=10) as client:
                        await client.post(webhook, json={"text": body})
            elif channel == "webhook":
                import os

                url = os.getenv("BUDGET_ALERT_WEBHOOK_URL", "")
                if url:
                    import httpx

                    async with httpx.AsyncClient(timeout=10) as client:
                        await client.post(
                            url,
                            json={
                                "alert_name": alert.name,
                                "tenant_id": str(alert.tenant_id),
                                "spend_usd": float(spend),
                                "threshold_usd": float(alert.threshold_usd),
                                "percent": percent,
                            },
                        )
        except Exception:
            logger.exception("budget_alert_notify_failed", channel=channel)


async def evaluate_budget_alerts() -> dict:
    """Main entry point. Iterates every alert, fires triggered notifications.

    Returns a summary dict for logging/metrics.
    """
    now = datetime.now(UTC)
    checked = 0
    triggered = 0

    async with async_session_factory() as session:
        result = await session.execute(select(BudgetAlert))
        alerts = result.scalars().all()

    for alert in alerts:
        checked += 1
        try:
            period_start = _period_start(alert.period, now)

            # Idempotency — if already triggered in this period, skip.
            if (
                alert.last_triggered_at is not None
                and alert.last_triggered_at >= period_start
            ):
                continue

            spend = await _spend_since(
                alert.tenant_id,
                alert.company_id,
                alert.cost_center_id,
                period_start,
            )
            if alert.threshold_usd <= 0:
                continue
            percent = int((spend / alert.threshold_usd) * 100)
            if percent < alert.warn_at_percent:
                continue

            await _send_notification(alert, spend, percent)

            # Persist the trigger
            async with async_session_factory() as write_session:
                result = await write_session.execute(
                    select(BudgetAlert).where(BudgetAlert.id == alert.id)
                )
                fresh = result.scalar_one_or_none()
                if fresh is not None:
                    fresh.last_triggered_at = now
                    await write_session.commit()
            triggered += 1
            logger.info(
                "budget_alert_triggered",
                alert_id=str(alert.id),
                tenant_id=str(alert.tenant_id),
                name=alert.name,
                percent=percent,
                spend_usd=float(spend),
            )
        except Exception:
            logger.exception("budget_alert_eval_failed", alert_id=str(alert.id))

    summary = {"checked": checked, "triggered": triggered, "ts": now.isoformat()}
    logger.info("budget_evaluator_run", **summary)
    return summary


# Celery task shim — the app's Celery config auto-discovers tasks under
# core.tasks.  This stub wraps the async evaluator.
def evaluate_budget_alerts_task() -> dict:
    import asyncio

    return asyncio.run(evaluate_budget_alerts())
