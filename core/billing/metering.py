"""Agent-run metering hooks for ``core.langgraph.runner``.

Two calls, both guarded so the runner never fails because billing is
degraded:

* ``gate_agent_run`` — before the graph runs: returns a structured
  ``limit_exceeded`` result when the tenant's monthly ``agent_runs`` limit
  is exhausted, else ``None``. A metering/Redis outage does not block runs
  (usage is unknown, not exceeded) — it is logged instead.
* ``meter_agent_run`` — after a completed run: increments the monthly run
  counter that ``/billing/usage`` reports.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()

METERING_ENV = "AGENTICORG_BILLING_METERING"


def _metering_enabled() -> bool:
    import os

    return os.getenv(METERING_ENV, "1").strip().lower() not in {"0", "false", "no", "off"}


def limit_exceeded_result(tenant_id: str, usage: int, limit: int) -> dict[str, Any]:
    """Structured runner result for a blocked run (same shape as a failed run)."""
    return {
        "status": "limit_exceeded",
        "output": {},
        "confidence": 0.0,
        "reasoning_trace": [],
        "tool_calls_log": [],
        "tool_calls": [],
        "hitl_trigger": "",
        "error": (
            f"Monthly agent-run limit reached ({usage}/{limit}). "
            "Upgrade the plan or wait for the monthly reset."
        ),
        "limit": {"metric": "agent_runs", "usage": usage, "limit": limit},
        "performance": {"total_latency_ms": 0, "llm_tokens_used": 0, "llm_cost_usd": 0.0},
    }


async def gate_agent_run(tenant_id: str) -> dict[str, Any] | None:
    """Return a ``limit_exceeded`` result when the tenant is over its run limit."""
    if not tenant_id or not _metering_enabled():
        return None
    try:
        from core.billing.limits import check_limit

        verdict = await check_limit(tenant_id, "agent_runs")
    # enterprise-gate: broad-except-ok reason=metering-outage-must-not-block-or-crash-agent-runs
    except Exception:
        logger.warning("agent_run_limit_check_unavailable", tenant_id=tenant_id)
        return None
    if verdict.allowed:
        return None
    logger.warning(
        "agent_run_blocked_by_plan_limit",
        tenant_id=tenant_id,
        usage=verdict.usage,
        limit=verdict.limit,
        audit=True,
    )
    return limit_exceeded_result(tenant_id, verdict.usage, verdict.limit)


async def meter_agent_run(tenant_id: str) -> None:
    """Count one completed run against the tenant's monthly usage. Never raises."""
    if not tenant_id or not _metering_enabled():
        return
    try:
        from core.billing.usage_tracker import increment_agent_runs

        await increment_agent_runs(tenant_id)
    # enterprise-gate: broad-except-ok reason=usage-metering-is-best-effort-after-a-completed-run
    except Exception:
        logger.warning("agent_run_metering_failed", tenant_id=tenant_id)
