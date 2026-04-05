"""Billing tier definitions and limit enforcement.

Tiers:
  Free       — 3 agents, 1K runs/month, 1 GB storage
  Pro        — 15 agents, 10K runs/month, 50 GB storage
  Enterprise — unlimited

Soft warning at 80%, hard block at 100%.
"""

from __future__ import annotations

import os
from typing import Any, NamedTuple

import structlog

logger = structlog.get_logger()

# ── Tier definitions ─────────────────────────────────────────────────

TIERS: dict[str, dict[str, int]] = {
    "free": {
        "agent_count": 3,
        "agent_runs": 1_000,
        "storage_bytes": 1 * 1024 * 1024 * 1024,  # 1 GB
    },
    "pro": {
        "agent_count": 15,
        "agent_runs": 10_000,
        "storage_bytes": 50 * 1024 * 1024 * 1024,  # 50 GB
    },
    "enterprise": {
        "agent_count": -1,  # unlimited
        "agent_runs": -1,
        "storage_bytes": -1,
    },
}

SOFT_WARNING_THRESHOLD = 0.80  # 80%
HARD_BLOCK_THRESHOLD = 1.00  # 100%

# Pricing for plan listing
PLAN_PRICING: list[dict[str, Any]] = [
    {
        "plan": "free",
        "label": "Free",
        "price_usd": 0,
        "price_inr": 0,
        "agents": 3,
        "runs": "1,000/mo",
        "storage": "1 GB",
        "features": ["3 agents", "1K runs/month", "Community support"],
    },
    {
        "plan": "pro",
        "label": "Pro",
        "price_usd": 99,
        "price_inr": 9_999,
        "agents": 15,
        "runs": "10,000/mo",
        "storage": "50 GB",
        "features": [
            "15 agents",
            "10K runs/month",
            "50 GB storage",
            "Priority support",
            "Custom connectors",
        ],
    },
    {
        "plan": "enterprise",
        "label": "Enterprise",
        "price_usd": 499,
        "price_inr": 49_999,
        "agents": "Unlimited",
        "runs": "Unlimited",
        "storage": "Unlimited",
        "features": [
            "Unlimited agents",
            "Unlimited runs",
            "Unlimited storage",
            "24/7 support",
            "Custom SLAs",
            "Dedicated CSM",
            "SSO / SCIM",
        ],
    },
]


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
