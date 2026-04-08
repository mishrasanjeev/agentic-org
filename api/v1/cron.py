"""Cron trigger endpoints for scheduled tasks.

These endpoints are called by Cloud Scheduler (GCP) or Celery Beat.
Protected by API key authentication (not user JWT).
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Header

router = APIRouter()
logger = logging.getLogger(__name__)

CRON_API_KEY = os.environ.get("AGENTICORG_CRON_API_KEY", "dev-cron-key")


def _verify_cron_key(x_cron_key: str = Header(default="")) -> None:
    """Verify cron API key from header."""
    if x_cron_key != CRON_API_KEY:
        raise HTTPException(403, "Invalid cron API key")


@router.post("/cron/compliance-alerts")
async def trigger_compliance_alerts(
    x_cron_key: str = Header(default=""),
):
    """Trigger the daily compliance alert cron job.

    Called by Cloud Scheduler or Celery Beat at 6:00 AM IST.
    """
    _verify_cron_key(x_cron_key)

    from core.cron.compliance_alerts import run_compliance_alert_cron

    try:
        result = await run_compliance_alert_cron()
        logger.info("Compliance cron triggered: %s", result)
        return {"status": "ok", **result}
    except Exception as exc:
        logger.exception("Compliance cron failed: %s", exc)
        raise HTTPException(500, f"Cron failed: {exc}") from exc
