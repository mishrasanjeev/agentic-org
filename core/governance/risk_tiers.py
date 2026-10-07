# SPDX-License-Identifier: Apache-2.0
"""Regulatory risk tiers: the controls each tier forces on an agent, and the checks that enforce them.

An agent's registry card carries a risk tier (``low``, ``medium``, ``high``,
``critical``). While ``AGENTICORG_GOVERNANCE_RISK_TIERS_ENABLED`` is on, the
tier decides what must be true before the agent runs in production and what
an owner may change:

* ``medium`` and above: the registry has **approved** the agent (a second
  person's decision);
* ``high`` and above: an **evaluation gate** is declared and passed, a
  **human oversight** condition is set (not empty, not ``never``), and the shadow
  evidence holds at least the tier's **minimum of scored samples**;
* ``critical``: prompt changes also go through **maker-checker** for the
  tenant.

The checks run at promotion and resume to ``active`` whatever the separate
registry, evaluation and maker-checker switches say, so an agent owner cannot
bypass them by leaving a gate unconfigured. A tier is changed by a tenant
administrator only, and lowering a ``high`` or ``critical`` tier needs a
person other than the agent's owner. On a ``high`` or ``critical`` agent, an
update that removes human oversight or the evaluation gate is refused.
``GET /governance/risk-tiers`` shows the policy and every agent's compliance
with its tier.

Off, nothing here runs and the switches that exist today decide alone.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

TIERS: tuple[str, ...] = ("low", "medium", "high", "critical")
REGULATED: tuple[str, ...] = ("high", "critical")
REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "low": (),
    "medium": ("registry_approval",),
    "high": ("registry_approval", "eval_gate", "human_oversight", "shadow_evidence"),
    "critical": ("registry_approval", "eval_gate", "human_oversight", "shadow_evidence", "maker_checker"),
}
MIN_SHADOW_SAMPLES: dict[str, int] = {"high": 50, "critical": 200}
NO_OVERSIGHT: frozenset[str] = frozenset({"", "never", "false", "0", "none", "off"})
DESCRIPTIONS: dict[str, str] = {
    "registry_approval": "The registry entry is approved or published: a second person has approved the agent.",
    "eval_gate": "An evaluation gate is declared on the agent and its newest run passes it.",
    "human_oversight": "The agent's HITL condition routes work to a person; it is not never.",
    "shadow_evidence": "The shadow phase has at least the tier's minimum of human-scored samples.",
    "maker_checker": "Prompt changes for the tenant wait for a second person (maker-checker).",
}
TRIGGER = "risk_tier"


class TierError(Exception):
    def __init__(self, code: str, message: str, *, tier: str | None, requirement: str | None = None, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.tier = tier
        self.requirement = requirement
        self.status = status


def enabled() -> bool:
    return bool(settings.governance_risk_tiers_enabled)


def tier_of(entry: Any) -> str | None:
    tier = getattr(entry, "risk_tier", None) if entry is not None else None
    return tier if tier in TIERS else None


def requirements(tier: str | None) -> tuple[str, ...]:
    return REQUIREMENTS.get(tier or "", ())


def is_oversight(condition: Any) -> bool:
    """Whether a HITL condition can route work to a person at all."""
    return str(condition or "").strip().lower() not in NO_OVERSIGHT


def policy() -> dict[str, Any]:
    """The tier policy as the console and the API show it."""
    return {
        "enabled": enabled(),
        "tiers": {
            tier: {
                "requirements": list(REQUIREMENTS[tier]),
                "min_shadow_samples": MIN_SHADOW_SAMPLES.get(tier, 0),
            }
            for tier in TIERS
        },
        "descriptions": dict(DESCRIPTIONS),
        "tier_changes": (
            "A tenant administrator changes a tier; lowering a high or critical tier needs a person "
            "other than the agent's owner."
        ),
    }


async def assess(session: Any, tenant_id: uuid.UUID, agent: Any, entry: Any = None) -> dict[str, Any]:
    """Each of the tier's requirements, met or not, with a short reason; ``compliant`` when all are met."""
    from core.agent_registry import lifecycle
    from core.evals import gates as eval_gates

    if entry is None:
        entry = await lifecycle.get_entry(session, tenant_id, agent.id)
    tier = tier_of(entry)
    checks: dict[str, dict[str, Any]] = {}
    for name in requirements(tier):
        if name == "registry_approval":
            state = getattr(entry, "state", None) if entry is not None else "draft"
            checks[name] = {"met": state in ("approved", "published"), "detail": f"registry state {state or 'draft'}"}
        elif name == "eval_gate":
            verdict = await eval_gates.evaluate(session, tenant_id, agent)
            met = bool(verdict.declared and verdict.ok)
            checks[name] = {
                "met": met,
                "detail": "no evaluation gate declared" if not verdict.declared else str(verdict.code or "passed"),
            }
        elif name == "human_oversight":
            condition = getattr(agent, "hitl_condition", None)
            checks[name] = {
                "met": is_oversight(condition),
                "detail": f"hitl condition {str(condition or '').strip() or 'unset'}",
            }
        elif name == "shadow_evidence":
            from core.feedback.shadow_learning import scored_shadow_sample_count

            scored = int(scored_shadow_sample_count(agent) or 0)
            needed = MIN_SHADOW_SAMPLES.get(tier or "", 0)
            checks[name] = {"met": scored >= needed, "detail": f"{scored} of {needed} scored samples"}
        elif name == "maker_checker":
            from core.prompts import activation as prompt_activation

            try:
                on = await prompt_activation.maker_checker_on(tenant_id)
            except prompt_activation.ActivationError:
                on = False
            checks[name] = {"met": bool(on), "detail": "maker-checker on" if on else "maker-checker off"}
    return {"tier": tier, "requirements": checks, "compliant": all(c["met"] for c in checks.values())}


async def check_promotion(session: Any, tenant_id: uuid.UUID, agent: Any) -> dict[str, Any] | None:
    """Refuse a promotion or resume to active while a requirement of the agent's tier is not met."""
    if not enabled():
        return None
    assessment = await assess(session, tenant_id, agent)
    for name, check in assessment["requirements"].items():
        if not check["met"]:
            logger.warning(
                "risk_tier_promotion_refused",
                agent_id=str(getattr(agent, "id", "")),
                tier=assessment["tier"],
                requirement=name,
            )
            raise TierError(
                f"{name}_required",
                f"A {assessment['tier']} risk agent is promoted only when {DESCRIPTIONS[name][0].lower()}"
                f"{DESCRIPTIONS[name][1:]} ({check['detail']}).",
                tier=assessment["tier"],
                requirement=name,
            )
    return assessment


def check_tier_change(
    *, current: str | None, new: str | None, is_admin: bool, actor: uuid.UUID | None, owner_user_id: Any
) -> None:
    """A tier is changed by an administrator; lowering a regulated tier needs a person other than the owner."""
    if not enabled() or current == new:
        return
    if not is_admin:
        raise TierError("admin_only", "Only a tenant administrator changes an agent's risk tier.", tier=new, status=403)
    lowering = current in REGULATED and (new is None or TIERS.index(new) < TIERS.index(current))
    if lowering:
        if actor is None:
            raise TierError("no_actor", "A signed-in administrator lowers a risk tier.", tier=current, status=403)
        if owner_user_id is not None and str(owner_user_id) == str(actor):
            raise TierError(
                "second_person",
                "The agent's owner cannot lower its risk tier; another administrator does.",
                tier=current,
                status=403,
            )


def check_update(entry: Any, update_data: dict[str, Any]) -> None:
    """On a regulated agent, an update that removes human oversight is refused."""
    if not enabled():
        return
    tier = tier_of(entry)
    if tier not in REGULATED:
        return
    condition = update_data.get("hitl_condition")
    policy_block = update_data.get("hitl_policy")
    if isinstance(policy_block, dict) and "condition" in policy_block:
        condition = policy_block.get("condition")
    if condition is not None and not is_oversight(condition):
        raise TierError(
            "human_oversight_required",
            f"A {tier} risk agent keeps a human oversight condition; it cannot be set to never.",
            tier=tier,
            requirement="human_oversight",
        )


def check_gate_removal(entry: Any, gate: Any) -> None:
    """On a regulated agent, the evaluation gate cannot be removed."""
    if not enabled():
        return
    tier = tier_of(entry)
    if tier in REGULATED and not gate:
        raise TierError(
            "eval_gate_required",
            f"A {tier} risk agent keeps its evaluation gate; it cannot be removed.",
            tier=tier,
            requirement="eval_gate",
        )


async def overview(session: Any, tenant_id: uuid.UUID) -> dict[str, Any]:
    """The policy and every agent's tier and compliance, for ``GET /governance/risk-tiers``."""
    from sqlalchemy import select

    from core.agent_registry import lifecycle
    from core.models.agent import Agent

    agents = list((await session.execute(select(Agent).where(Agent.tenant_id == tenant_id))).scalars().all())
    rows = []
    by_tier: dict[str, int] = dict.fromkeys((*TIERS, "unset"), 0)
    for agent in agents:
        entry = await lifecycle.get_entry(session, tenant_id, agent.id)
        assessment = await assess(session, tenant_id, agent, entry)
        tier = assessment["tier"] or "unset"
        by_tier[tier] += 1
        rows.append(
            {
                "agent_id": str(agent.id),
                "name": agent.name,
                "status": agent.status,
                "registry_state": getattr(entry, "state", None) if entry is not None else "draft",
                "tier": assessment["tier"],
                "compliant": assessment["compliant"],
                "unmet": [name for name, check in assessment["requirements"].items() if not check["met"]],
            }
        )
    return {
        "policy": policy(),
        "agents": sorted(
            rows, key=lambda r: (r["tier"] is None, TIERS.index(r["tier"]) if r["tier"] else 99, r["name"])
        ),
        "by_tier": by_tier,
        "non_compliant": sum(1 for r in rows if not r["compliant"]),
    }
