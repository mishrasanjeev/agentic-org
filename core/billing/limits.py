"""Billing tier definitions and limit enforcement.

Tiers:
  Free       — 3 agents, 1K runs/month, 1 GB storage
  Pro        — 15 agents, 10K runs/month, 50 GB storage
  Enterprise — unlimited

Soft warning at 80%, hard block at 100%.
"""

from __future__ import annotations

import os
from typing import NamedTuple

import structlog

from core.billing.catalog import PUBLIC_PLAN_CATALOG

logger = structlog.get_logger()

# ── Tier definitions ─────────────────────────────────────────────────

def _runtime_limit(value: int | None) -> int:
    return -1 if value is None else value


TIERS: dict[str, dict[str, int]] = {
    plan.plan_id: {
        "agent_count": _runtime_limit(plan.limits.agent_count),
        "agent_runs": _runtime_limit(plan.limits.agent_runs),
        "storage_bytes": _runtime_limit(plan.limits.storage_bytes),
    }
    for plan in PUBLIC_PLAN_CATALOG.plans
}


def _major_price(plan_id: str, currency: str) -> int:
    plan = next(plan for plan in PUBLIC_PLAN_CATALOG.plans if plan.plan_id == plan_id)
    amount_minor = next(price.amount_minor for price in plan.prices if price.currency == currency)
    if amount_minor % 100:
        raise ValueError(f"{plan_id}:{currency} is not a whole-unit public list price")
    return amount_minor // 100


def _storage_label(storage_bytes: int | None) -> str:
    if storage_bytes is None:
        return "Unlimited"
    gibibyte = 1024**3
    if storage_bytes % gibibyte:
        raise ValueError("public storage limits must be whole GiB values")
    return f"{storage_bytes // gibibyte} GB"


# Backward-compatible view for public metadata consumers. Values are derived
# from PUBLIC_PLAN_CATALOG; do not add or override plan facts here.
PLAN_PRICING: list[dict[str, object]] = [
    {
        "plan": plan.plan_id,
        "label": plan.display_name,
        "price_usd": _major_price(plan.plan_id, "USD"),
        "price_inr": _major_price(plan.plan_id, "INR"),
        "agents": plan.limits.agent_count if plan.limits.agent_count is not None else "Unlimited",
        "runs": plan.limits.agent_runs if plan.limits.agent_runs is not None else "Unlimited",
        "storage": _storage_label(plan.limits.storage_bytes),
        "features": [],
    }
    for plan in PUBLIC_PLAN_CATALOG.plans
]

SOFT_WARNING_THRESHOLD = 0.80  # 80%
HARD_BLOCK_THRESHOLD = 1.00  # 100%

# ── Limit check ──────────────────────────────────────────────────────


class LimitResult(NamedTuple):
    allowed: bool
    usage: int
    limit: int
    warning: bool  # True if usage >= 80% but < 100%


def _get_tenant_tier(tenant_id: str) -> str:
    """Look up tenant tier from Redis or DB.

    Falls back to 'free' if not found.
    """
    try:
        import redis

        r = redis.from_url(
            os.getenv("AGENTICORG_REDIS_URL", "redis://localhost:6379/1"),
            decode_responses=True,
        )
        tier = r.get(f"tenant_tier:{tenant_id}")
        if tier and tier in TIERS:
            return tier
    # enterprise-gate: broad-except-ok reason=tier-lookup-failure-defaults-to-free-fail-closed
    except Exception:
        logger.debug("tier_lookup_failed", tenant_id=tenant_id)
    return "free"


def check_limit(tenant_id: str, metric: str) -> LimitResult:
    """Check whether a tenant is within their tier limit for a given metric.

    Parameters
    ----------
    tenant_id : str
        Tenant to check.
    metric : str
        One of: agent_count, agent_runs, storage_bytes.

    Returns
    -------
    LimitResult(allowed, usage, limit, warning)
        allowed=True if usage < 100% of limit.
        warning=True if usage >= 80% of limit.
    """
    from core.billing.usage_tracker import get_usage

    tier = _get_tenant_tier(tenant_id)
    limits = TIERS.get(tier, TIERS["free"])
    limit_val = limits.get(metric, 0)

    # Unlimited tier
    if limit_val == -1:
        usage_data = get_usage(tenant_id)
        current = usage_data.get(metric, 0)
        return LimitResult(allowed=True, usage=current, limit=-1, warning=False)

    usage_data = get_usage(tenant_id)
    current = usage_data.get(metric, 0)

    if limit_val == 0:
        return LimitResult(allowed=False, usage=current, limit=0, warning=False)

    ratio = current / limit_val
    allowed = ratio < HARD_BLOCK_THRESHOLD
    warning = ratio >= SOFT_WARNING_THRESHOLD and ratio < HARD_BLOCK_THRESHOLD

    if warning:
        logger.warning(
            "usage_soft_warning",
            tenant_id=tenant_id,
            metric=metric,
            usage=current,
            limit=limit_val,
            pct=f"{ratio:.0%}",
        )

    if not allowed:
        logger.warning(
            "usage_hard_block",
            tenant_id=tenant_id,
            metric=metric,
            usage=current,
            limit=limit_val,
        )

    return LimitResult(allowed=allowed, usage=current, limit=limit_val, warning=warning)
