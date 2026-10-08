# SPDX-License-Identifier: Apache-2.0
"""The policy console: every policy of a tenant in one shape, written and dry-run in one place.

Policies already live where they are enforced: routing, access and limit
policies in the model gateway (``core/governance/model_gateway.py``, every
model call), guardrail rules in the guardrail engine (the input, retrieval,
output and action stages), approval policies in the approvals store (workflow
execution), and the action taxonomy for tool invocation. The console stores
nothing of its own. ``list_policies`` reads every store and gives each policy
one shape (kind, id, name, enabled, priority, scope, effect, enforcement
point); ``write_policy`` and ``delete_policy`` dispatch to the store's own
writer with the same actor, so caches and audit rows behave as for a write
through the kind's own API; ``evaluate`` is a dry run across the enforcement
points for a described call, text, tool or workflow, and meters, logs and
audits nothing.

Off (``AGENTICORG_GOVERNANCE_POLICY_CONSOLE_ENABLED``), the endpoints are
not found and nothing here reads or writes.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

KINDS: tuple[str, ...] = ("model_routing", "model_access", "model_limit", "guardrail", "approval", "action")
WRITABLE: tuple[str, ...] = ("model_routing", "model_access", "model_limit", "guardrail", "approval")
ENFORCEMENT: dict[str, str] = {
    "model_routing": "model call (model gateway)",
    "model_access": "model call (model gateway)",
    "model_limit": "model call (model gateway)",
    "guardrail": "input, retrieval, output and action stages (guardrail engine)",
    "approval": "workflow execution (approval policies)",
    "action": "tool invocation (action taxonomy)",
}
VERDICTS: tuple[str, ...] = ("blocked", "flagged", "routed", "allowed")


class PolicyConsoleError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def enabled() -> bool:
    return bool(settings.governance_policy_console_enabled)


def _scope(**fields: Any) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value not in (None, "", [], ())}


def entry_of(kind: str, policy: Any) -> dict[str, Any]:
    """One policy in the console's shape, whatever store it came from."""
    if kind == "model_routing":
        target = _scope(provider=policy.provider, model=policy.model, tier=policy.tier)
        if policy.targets:
            target["targets"] = [dict(t) for t in policy.targets]
        return {
            "kind": kind,
            "id": str(policy.id),
            "name": policy.name,
            "enabled": bool(policy.enabled),
            "priority": policy.priority,
            "scope": _scope(
                use_case=policy.use_case,
                sensitivity=policy.sensitivity,
                agent_id=policy.agent_id,
                business_unit=policy.business_unit,
                language=policy.language,
            ),
            "effect": "route",
            "detail": {
                **target,
                "in_region_only": bool(policy.in_region_only),
                "allowed_providers": list(policy.allowed_providers or []) or None,
                "cost_aware": bool(getattr(policy, "cost_aware", False)),
            },
            "enforcement_point": ENFORCEMENT[kind],
            "reason": policy.reason or "",
        }
    if kind == "model_access":
        return {
            "kind": kind,
            "id": str(policy.id),
            "name": policy.name,
            "enabled": bool(policy.enabled),
            "priority": policy.priority,
            "scope": _scope(
                use_case=policy.use_case,
                sensitivity=policy.sensitivity,
                agent_id=policy.agent_id,
                business_unit=policy.business_unit,
                language=policy.language,
                application=policy.application,
                principal=policy.principal,
                provider=policy.provider,
                model=policy.model,
            ),
            "effect": policy.effect,
            "detail": {
                "allowed_providers": list(policy.allowed_providers or []) or None,
                "allowed_models": list(policy.allowed_models or []) or None,
            },
            "enforcement_point": ENFORCEMENT[kind],
            "reason": getattr(policy, "reason", "") or "",
        }
    if kind == "model_limit":
        return {
            "kind": kind,
            "id": str(policy.id),
            "name": f"{policy.provider}/{policy.model}" if policy.model else f"{policy.provider} (all models)",
            "enabled": bool(policy.enabled),
            "priority": None,
            "scope": _scope(provider=policy.provider, model=policy.model),
            "effect": "limit",
            "detail": {"max_concurrency": policy.max_concurrency, "requests_per_minute": policy.requests_per_minute},
            "enforcement_point": ENFORCEMENT[kind],
            "reason": policy.reason or "",
        }
    if kind == "guardrail":
        return {
            "kind": kind,
            "id": str(policy.id),
            "name": policy.name,
            "enabled": bool(policy.enabled),
            "priority": policy.priority,
            "scope": _scope(
                stage=policy.stage, agent_id=policy.agent_id, use_case=policy.use_case, risk_tier=policy.risk_tier
            ),
            "effect": policy.action,
            "detail": {"detector": policy.detector, "threshold": policy.threshold},
            "enforcement_point": ENFORCEMENT[kind],
            "reason": policy.reason or "",
        }
    if kind == "approval":
        steps = list(getattr(policy, "steps", None) or [])
        return {
            "kind": kind,
            "id": str(policy.id),
            "name": policy.name,
            "enabled": bool(getattr(policy, "is_active", True)),
            "priority": None,
            "scope": _scope(
                workflow_id=str(policy.workflow_id) if getattr(policy, "workflow_id", None) else None,
                agent_id=str(policy.agent_id) if getattr(policy, "agent_id", None) else None,
            ),
            "effect": "require_approval",
            "detail": {
                "steps": len(steps),
                "approver_roles": [str(getattr(s, "approver_role", "")) for s in steps],
                "description": getattr(policy, "description", None),
            },
            "enforcement_point": ENFORCEMENT[kind],
            "reason": getattr(policy, "description", None) or "",
        }
    raise PolicyConsoleError(422, "unknown_kind", f"kind is one of {', '.join(KINDS)}")


async def _approval_rows(session: Any, tenant_id: uuid.UUID) -> list[Any]:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from core.models.approval_policy import ApprovalPolicy

    return list(
        (
            await session.execute(
                select(ApprovalPolicy)
                .where(ApprovalPolicy.tenant_id == tenant_id)
                .options(selectinload(ApprovalPolicy.steps))
                .order_by(ApprovalPolicy.name)
            )
        )
        .scalars()
        .all()
    )


def _action_entries() -> list[dict[str, Any]]:
    from core.governance import action_policy

    return [
        {
            "kind": "action",
            "id": f"action:{risk.value}",
            "name": f"{risk.value} actions",
            "enabled": True,
            "priority": None,
            "scope": {"risk": risk.value},
            "effect": "contain" if risk in action_policy.UNSAFE_ACTION_RISKS else "allow",
            "detail": {"taxonomy_version": action_policy.ACTION_TAXONOMY_VERSION},
            "enforcement_point": ENFORCEMENT["action"],
            "reason": "the action taxonomy; an unsafe class runs read-only, draft or shadow unless a flag allows",
        }
        for risk in action_policy.ActionRisk
    ]


async def _store_rows(session: Any, tenant_id: uuid.UUID, kind: str) -> list[Any]:
    """Every row of a store, enabled or not, in the store's own value type.

    The enforcement points read only enabled rows (``active_policy_set``,
    ``active_rules``); the console lists the disabled ones too, as the kinds'
    own list endpoints do, so an administrator can see and manage them.
    """
    from sqlalchemy import select

    from core.governance import model_gateway
    from core.governance.guardrails import engine as guardrail_engine
    from core.models.guardrail_rule import GuardrailRule
    from core.models.model_access_policy import ModelAccessPolicy
    from core.models.model_limit import ModelLimit
    from core.models.model_routing_policy import ModelRoutingPolicy

    stores: dict[str, tuple[Any, Any, tuple[Any, ...]]] = {
        "model_routing": (ModelRoutingPolicy, model_gateway._policy, ("priority", "name")),
        "model_access": (ModelAccessPolicy, model_gateway._access_policy, ("priority", "name")),
        "model_limit": (ModelLimit, model_gateway._limit, ("provider", "model")),
        "guardrail": (GuardrailRule, guardrail_engine._rule, ("priority", "name")),
    }
    table, convert, order = stores[kind]
    query = select(table).where(table.tenant_id == tenant_id).order_by(*(getattr(table, c) for c in order))
    return [convert(row) for row in (await session.execute(query)).scalars().all()]


async def list_policies(session: Any, tenant_id: uuid.UUID, *, kind: str | None = None) -> list[dict[str, Any]]:
    """Every policy of the tenant, enabled or not, in one shape, by kind then priority then name."""
    entries: list[dict[str, Any]] = []
    wanted = (kind,) if kind else KINDS
    for store in ("model_routing", "model_access", "model_limit", "guardrail"):
        if store in wanted:
            entries.extend(entry_of(store, row) for row in await _store_rows(session, tenant_id, store))
    if "approval" in wanted:
        entries.extend(entry_of("approval", row) for row in await _approval_rows(session, tenant_id))
    if "action" in wanted:
        entries.extend(_action_entries())
    return sorted(
        entries,
        key=lambda e: (KINDS.index(e["kind"]), e["priority"] if e["priority"] is not None else 10**9, e["name"]),
    )


async def write_policy(
    session: Any, tenant_id: uuid.UUID, kind: str, fields: dict[str, Any], *, actor_id: str
) -> dict[str, Any]:
    """Write a policy through the writer of its own store; the entry written comes back in the console's shape."""
    if kind not in WRITABLE:
        raise PolicyConsoleError(422, "unknown_kind", f"a writable kind is one of {', '.join(WRITABLE)}")
    if not actor_id:
        raise PolicyConsoleError(403, "no_actor", "A policy is written by an attributable actor")
    from core.governance import model_gateway
    from core.governance.guardrails import engine as guardrail_engine

    try:
        if kind == "model_routing":
            return entry_of(kind, await model_gateway.set_policy(tenant_id, actor_id=actor_id, **fields))
        if kind == "model_access":
            return entry_of(kind, await model_gateway.set_access_policy(tenant_id, actor_id=actor_id, **fields))
        if kind == "model_limit":
            return entry_of(kind, await model_gateway.set_limit(tenant_id, actor_id=actor_id, **fields))
        if kind == "guardrail":
            return entry_of(kind, await guardrail_engine.set_rule(tenant_id, actor_id=actor_id, **fields))
    except (ValueError, TypeError) as exc:
        raise PolicyConsoleError(422, "invalid_policy", str(exc)[:500]) from None
    return entry_of(kind, await _write_approval(session, tenant_id, fields))


def _validate_approval_steps(steps: list[dict[str, Any]]) -> None:
    """The checks of the approval policies API: a step names an approver role and a quorum it can reach."""
    from core.rbac import is_approval_policy_role

    for raw in steps:
        sequence = raw.get("sequence")
        if not is_approval_policy_role(str(raw.get("approver_role") or "")):
            raise PolicyConsoleError(422, "invalid_policy", f"step {sequence}: invalid approver_role")
        if int(raw.get("quorum_required", 1)) > int(raw.get("quorum_total", 1)):
            raise PolicyConsoleError(422, "invalid_policy", f"step {sequence}: quorum_required > quorum_total")


async def _write_approval(session: Any, tenant_id: uuid.UUID, fields: dict[str, Any]) -> Any:
    from sqlalchemy import select

    from core.models.approval_policy import ApprovalPolicy, ApprovalStep

    name = str(fields.get("name") or "").strip()
    _validate_approval_steps(list(fields.get("steps") or []))
    existing = (
        await session.execute(
            select(ApprovalPolicy).where(ApprovalPolicy.tenant_id == tenant_id, ApprovalPolicy.name == name)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise PolicyConsoleError(409, "exists", f"Approval policy {name!r} already exists")
    policy = ApprovalPolicy(
        tenant_id=tenant_id,
        name=name,
        description=fields.get("description"),
        workflow_id=fields.get("workflow_id"),
        agent_id=fields.get("agent_id"),
    )
    session.add(policy)
    await session.flush()
    steps = []
    for raw in fields.get("steps") or []:
        step = ApprovalStep(
            policy_id=policy.id,
            sequence=raw["sequence"],
            approver_role=raw["approver_role"],
            quorum_required=raw.get("quorum_required", 1),
            quorum_total=raw.get("quorum_total", 1),
            mode=raw.get("mode", "sequential"),
            condition=raw.get("condition"),
            step_metadata=raw.get("step_metadata") or {},
        )
        session.add(step)
        steps.append(step)
    await session.flush()
    policy.steps = steps
    return policy


async def delete_policy(session: Any, tenant_id: uuid.UUID, kind: str, policy_id: uuid.UUID, *, actor_id: str) -> bool:
    """Remove a policy through the deleter of its own store."""
    if kind not in WRITABLE:
        raise PolicyConsoleError(422, "unknown_kind", f"a writable kind is one of {', '.join(WRITABLE)}")
    if not actor_id:
        raise PolicyConsoleError(403, "no_actor", "A policy is removed by an attributable actor")
    from core.governance import model_gateway
    from core.governance.guardrails import engine as guardrail_engine

    if kind == "model_routing":
        return bool(await model_gateway.delete_policy(tenant_id, policy_id, actor_id=actor_id))
    if kind == "model_access":
        return bool(await model_gateway.delete_access_policy(tenant_id, policy_id, actor_id=actor_id))
    if kind == "model_limit":
        return bool(await model_gateway.delete_limit(tenant_id, policy_id, actor_id=actor_id))
    if kind == "guardrail":
        return bool(await guardrail_engine.delete_rule(tenant_id, policy_id, actor_id=actor_id))
    from sqlalchemy import select

    from core.models.approval_policy import ApprovalPolicy

    row = (
        await session.execute(
            select(ApprovalPolicy).where(ApprovalPolicy.tenant_id == tenant_id, ApprovalPolicy.id == policy_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


def _model_section(evaluation: Any) -> dict[str, Any]:
    refusal = getattr(evaluation, "refusal", None)
    if refusal is not None:
        return {
            "outcome": "refused",
            "reason": str(getattr(refusal, "reason", "") or refusal),
            "policy_id": getattr(refusal, "policy_id", None),
            "policy_name": getattr(refusal, "policy_name", None),
            "gateway_enabled": bool(getattr(evaluation, "enabled", False)),
        }
    decision = getattr(evaluation, "decision", None)
    return {
        "outcome": "routed" if getattr(decision, "applied", False) else "passthrough",
        "provider": getattr(decision, "provider", None),
        "model": getattr(decision, "model", None),
        "reason": getattr(decision, "reason", ""),
        "policy_id": getattr(decision, "policy_id", None),
        "policy_name": getattr(decision, "policy_name", None),
        "access_policy_id": getattr(decision, "access_policy_id", None),
        "restricted": bool(getattr(decision, "restricted", False)),
        "gateway_enabled": bool(getattr(evaluation, "enabled", False)),
    }


def _resolve_approval(rows: list[Any], workflow_id: Any, agent_id: Any) -> tuple[str, Any] | None:
    """The policy live execution picks, with ``core.approvals.policy_engine.resolve_policy``'s precedence.

    Workflow-scoped first, then agent-scoped, then the tenant-global policy
    named ``default`` (no workflow, no agent). Like the live resolver, the
    active flag is not consulted.
    """

    def scoped(field: str, wanted: Any) -> Any:
        if not wanted:
            return None
        return next((row for row in rows if str(getattr(row, field, None) or "") == str(wanted)), None)

    by_workflow = scoped("workflow_id", workflow_id)
    if by_workflow is not None:
        return "workflow", by_workflow
    by_agent = scoped("agent_id", agent_id)
    if by_agent is not None:
        return "agent", by_agent
    fallback = next(
        (
            row
            for row in rows
            if row.name == "default"
            and getattr(row, "workflow_id", None) is None
            and getattr(row, "agent_id", None) is None
        ),
        None,
    )
    return ("default", fallback) if fallback is not None else None


def _action_section(tool: str, domain: Any) -> dict[str, Any]:
    """The tool against the action taxonomy, failing closed as ``action_policy.evaluate_action`` does.

    A missing or unknown domain, a tool the taxonomy does not know and a tool
    outside its domain are blocked at runtime, so the dry run blocks them too.
    """
    from core.governance import action_policy

    canonical_domain = action_policy._domain(domain) if domain else None
    risk = action_policy.classify_action(tool)
    reason = None
    if not str(domain or "").strip():
        reason = "context_domain_missing"
    elif canonical_domain is None:
        reason = "context_domain_unknown"
    elif risk is None:
        reason = "action_unknown"
    elif action_policy.classify_action(tool, domain=canonical_domain) is None:
        reason = "action_domain_mismatch"
    unsafe = reason is None and risk in action_policy.UNSAFE_ACTION_RISKS
    if reason is not None:
        containment = "blocked"
    elif unsafe:
        containment = "read-only, draft or shadow unless a capability flag allows live"
    else:
        containment = "none"
    return {
        "tool": tool,
        "domain": canonical_domain.value if canonical_domain is not None else None,
        "risk": risk.value if risk is not None else None,
        "blocked": reason is not None,
        "reason": reason,
        "unsafe": unsafe,
        "containment": containment,
    }


async def evaluate(session: Any, tenant_id: uuid.UUID, subject: dict[str, Any]) -> dict[str, Any]:
    """A dry run across the enforcement points; the verdict is blocked, flagged, routed or allowed."""
    from core.governance import model_gateway
    from core.governance.guardrails import engine as guardrail_engine

    sections: dict[str, Any] = {}
    reasons: list[str] = []
    verdict = "allowed"

    def raise_to(level: str) -> None:
        nonlocal verdict
        if VERDICTS.index(level) < VERDICTS.index(verdict):
            verdict = level

    if subject.get("requested_model") or subject.get("requested_provider"):
        request = model_gateway.RouteRequest(
            tenant_id=tenant_id,
            use_case=str(subject.get("use_case") or "console"),
            requested_provider=subject.get("requested_provider"),
            requested_model=str(subject.get("requested_model") or ""),
            sensitivity=subject.get("sensitivity"),
            agent_id=subject.get("agent_id"),
            business_unit=subject.get("business_unit"),
            language=subject.get("language"),
            application=subject.get("application"),
            principal=subject.get("principal"),
        )
        section = _model_section(await model_gateway.evaluate(request))
        sections["model"] = section
        if section["outcome"] == "refused":
            raise_to("blocked")
            reasons.append(f"model call refused: {section['reason']}")
        elif section["outcome"] == "routed":
            raise_to("routed")
            reasons.append(f"model call routed by {section.get('policy_name') or section.get('policy_id')}")

    if subject.get("stage") and subject.get("text") is not None:
        result = await guardrail_engine.evaluate(
            str(subject["stage"]),
            str(subject["text"]),
            tenant_id=tenant_id,
            agent_id=subject.get("agent_id"),
            use_case=subject.get("use_case"),
            risk_tier=subject.get("risk_tier"),
            dry_run=True,
            context=subject.get("context"),
        )
        outcomes = [
            {
                "rule_id": o.rule_id,
                "rule_name": o.rule_name,
                "action": o.action,
                "detector": o.detector,
                "findings": o.findings,
                "score": o.score,
            }
            for o in result.outcomes
        ]
        sections["guardrails"] = {
            "allowed": bool(result.allowed),
            "enforced": bool(result.enforced),
            "outcomes": outcomes,
            "transformed": result.text != subject["text"],
            "unverifiable": list(result.unverifiable),
        }
        if not result.allowed:
            raise_to("blocked")
            reasons.append("guardrail block: " + ", ".join(o["rule_name"] for o in outcomes if o["action"] == "block"))
        elif outcomes:
            raise_to("flagged")
            reasons.append("guardrail findings: " + ", ".join(o["rule_name"] for o in outcomes))

    if subject.get("workflow_id") or subject.get("agent_id"):
        resolved = _resolve_approval(
            await _approval_rows(session, tenant_id), subject.get("workflow_id"), subject.get("agent_id")
        )
        matches = [entry_of("approval", resolved[1])] if resolved else []
        sections["approval"] = {"policies": matches, "resolved_by": resolved[0] if resolved else None}
        if matches:
            raise_to("routed")
            reasons.append("approval required: " + ", ".join(m["name"] for m in matches))

    if subject.get("tool"):
        sections["action"] = _action_section(str(subject["tool"]), subject.get("domain"))
        if sections["action"]["blocked"]:
            raise_to("blocked")
            reasons.append(f"tool {subject['tool']} is blocked: {sections['action']['reason']}")
        elif sections["action"]["unsafe"]:
            raise_to("routed")
            reasons.append(f"tool {subject['tool']} is a {sections['action']['risk']} action and is contained")

    return {"verdict": verdict, "reasons": reasons, "sections": sections}
