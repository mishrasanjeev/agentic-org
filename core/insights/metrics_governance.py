# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights metrics for the Compliance and Engineering views.

Compliance (administrators and auditors): whether the audit chain verifies,
how approvals were decided (directly, under a delegation, or so fast they were
a rubber stamp), data subject requests, provider residency attestations about
to lapse, and the change log (agents, prompts, governance, spend controls,
settings, access and packs). Engineering (administrators and developers): model
call failures and fallbacks, the slowest spans, the latest eval pass rate and
prompt edits.

Each resolver reads the tenant's own tables in the tenant session for the
period's tenant-local days, or now for snapshots, and answers ``no_data`` with a
hint when there is nothing recorded. No subject email, prompt text or note is
returned: only counts, durations, kinds and names.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from core.insights.metrics import MetricContext, no_data, pct
from core.insights.metrics_domain import _bounds, _compare_bounds, _daily_counts, _series

# An approval decided this soon after the reviewer opened it (or after it was raised, when it was never opened)
# counts as a rubber stamp (PRD: "decided in under 20 seconds").
RUBBER_STAMP_SECONDS = 20
ATTESTATION_HORIZON = timedelta(days=30)
TOP_SPANS = 8

# Change-log kinds by audit event prefix; prompt edits come from the prompt history tables and delegations from
# ``user_delegations`` (neither writes an audit row).
CHANGE_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "agents",
        (
            "agent.create",
            "agent.deleted",
            "agent.amendment_removed",
            "agent.route_scopes.updated",
            "agent.case_purposes.updated",
            "agent.generate_deploy",
        ),
    ),
    (
        "governance",
        (
            "governance_config.",
            "residency_attestation.",
            "operator_override.",
            "model_gateway_policy.",
            "model_gateway_access_policy.",
            "model_gateway_limit.",
        ),
    ),
    ("spend_controls", ("spend.",)),
    ("settings", ("config.", "insights.value.", "workbench.console.")),
    ("access", ("insights.bi_access.",)),
    ("packs", ("pack.install", "pack.uninstall")),
)
CHANGE_LABELS = ("agents", "prompts", "governance", "spend_controls", "settings", "access", "delegations", "packs")


async def _domain_agent_ids(ctx: MetricContext) -> list[str] | None:
    """The ids (text) of the agents in the caller's domains; None when the caller is not domain-limited."""
    if ctx.domains is None:
        return None
    if not ctx.domains:
        return []
    from sqlalchemy import select

    from core.models.agent import Agent

    rows = await ctx.session.execute(
        select(Agent.id).where(Agent.tenant_id == ctx.tenant_id, Agent.domain.in_(list(ctx.domains)))
    )
    return [str(agent_id) for (agent_id,) in rows]


# ------------------------------------------------------------------ compliance


async def resolve_audit_integrity(ctx: MetricContext) -> dict[str, Any]:
    """The audit chain's state now: rows verified among the newest 1,000 links, and any break."""
    from core.config import settings

    if not (settings.audit_chain_enabled or settings.audit_chain_default_on):
        return no_data("audit_log", "Audit chain sealing is off for this deployment, so there is no chain to verify.")
    from core.governance import audit_chain

    section = await audit_chain.evidence(ctx.tenant_id)
    checked = section.get("recent_verification")
    if not section.get("enabled") or not checked:
        hint = (
            "Audit chain sealing is off for this organisation."
            if not section.get("enabled")
            else "No audit row is sealed yet; sealing runs every five minutes."
        )
        return no_data("audit_log", hint)
    broken = checked.get("status") == "broken"
    first_break = checked.get("first_break") or {}
    rows = [
        {"label": "verified", "value": int(checked.get("verified") or 0)},
        {"label": "unsigned", "value": int(checked.get("unsigned") or 0)},
        {"label": "unsealed", "value": int(section.get("unsealed") or 0)},
    ]
    if broken:
        rows.append({"label": f"break: {first_break.get('reason') or 'unknown'}", "value": first_break.get("seq")})
    return {
        "value": int(checked.get("verified") or 0),
        "state": "act" if broken else "ok",
        "rows": rows,
        "source": "audit_log",
        "data_quality": "measured",
    }


async def _decisions(ctx: MetricContext, bounds: tuple[Any, Any]) -> list[tuple[Any, Any, Any, bool]]:
    """(decision_at, first_viewed_at, created_at, delegated) for each approval decided within ``bounds``."""
    from sqlalchemy import select

    from core.models.audit import AuditLog as A
    from core.models.hitl import HITLQueue as H

    lo, hi = bounds
    delegated = A.details["delegated_from"].astext.is_not(None) & (A.details["delegated_from"].astext != "")
    query = (
        select(H.decision_at, H.first_viewed_at, H.created_at, delegated)
        .select_from(H)
        .outerjoin(
            A,
            (A.tenant_id == H.tenant_id)
            & (A.event_type == "hitl.decided")
            & (A.resource_type == "hitl_item")
            & (A.resource_id == func_text(H.id)),
        )
        .where(H.tenant_id == ctx.tenant_id, H.decision_at.is_not(None), H.decision_at >= lo, H.decision_at < hi)
    )
    if ctx.domains is not None:
        agents = await _domain_agent_ids(ctx)
        if ctx.domains and agents is not None:
            query = query.where(H.agent_id.in_(agents))
    return [(d, v, c, bool(g)) for d, v, c, g in await ctx.session.execute(query)]


def func_text(column: Any) -> Any:
    from sqlalchemy import String, cast

    return cast(column, String)


async def resolve_approvals_decided(ctx: MetricContext) -> dict[str, Any]:
    """Approvals decided in the period; rows split them by decided directly or under a delegation."""
    from core.models.hitl import HITLQueue as H

    rows = await _decisions(ctx, _bounds(ctx))
    if not rows:
        return no_data("hitl_queue", "No approval was decided in this period.")
    delegated = sum(1 for *_x, d in rows if d)
    daily = await _daily_counts(ctx, H.decision_at, H.tenant_id == ctx.tenant_id, H.decision_at.is_not(None))
    out: dict[str, Any] = {
        "value": len(rows),
        "rows": [{"label": "direct", "value": len(rows) - delegated}, {"label": "delegated", "value": delegated}],
        "series": _series(ctx, daily),
        "source": "hitl_queue",
        "data_quality": "measured",
    }
    previous = _compare_bounds(ctx)
    if previous is not None:
        out["compare_value"] = len(await _decisions(ctx, previous))
    return out


def _stamp_share(rows: list[tuple[Any, Any, Any, bool]]) -> tuple[float | None, int]:
    fast = 0
    for decided_at, viewed_at, created_at, _delegated in rows:
        start = viewed_at or created_at
        if start is not None and (decided_at - start).total_seconds() < RUBBER_STAMP_SECONDS:
            fast += 1
    return pct(fast, len(rows)), fast


async def resolve_rubber_stamps(ctx: MetricContext) -> dict[str, Any]:
    """The share of approvals decided under 20 seconds after the reviewer opened them (or after they were raised)."""
    rows = await _decisions(ctx, _bounds(ctx))
    if not rows:
        return no_data("hitl_queue", "No approval was decided in this period.")
    share, fast = _stamp_share(rows)
    out: dict[str, Any] = {
        "value": share,
        "rows": [{"label": "under_20s", "value": fast}, {"label": "decided", "value": len(rows)}],
        "source": "hitl_queue",
        "data_quality": "measured",
    }
    previous = _compare_bounds(ctx)
    if previous is not None:
        before = await _decisions(ctx, previous)
        out["compare_value"] = _stamp_share(before)[0] if before else None
    return out


async def resolve_dsar_open(ctx: MetricContext) -> dict[str, Any]:
    """Data subject requests not completed now; rows count every request by status (no subject is shown)."""
    from sqlalchemy import func, select

    from core.models.dsar import DSARRequestRecord as D

    counts = {
        status: int(n)
        for status, n in await ctx.session.execute(
            select(D.status, func.count()).where(D.tenant_id == ctx.tenant_id).group_by(D.status)
        )
    }
    if not counts:
        return no_data("dsar_requests", "No data subject request has been received.")
    open_count = counts.get("received", 0) + counts.get("processing", 0)
    oldest = (
        await ctx.session.execute(
            select(func.min(D.created_at)).where(D.tenant_id == ctx.tenant_id, D.status.in_(("received", "processing")))
        )
    ).scalar_one_or_none()
    age_days = round((ctx.now - oldest).total_seconds() / 86400, 1) if oldest is not None else None
    return {
        "value": open_count,
        "rows": [
            {"label": status, "value": counts.get(status, 0)}
            for status in ("received", "processing", "completed", "failed")
        ]
        + ([{"label": "oldest_open_days", "value": age_days}] if age_days is not None else []),
        "source": "dsar_requests",
        "data_quality": "measured",
    }


async def _dsar_completion(ctx: MetricContext, bounds: tuple[Any, Any]) -> tuple[int, float | None]:
    from sqlalchemy import extract, func, select

    from core.models.dsar import DSARRequestRecord as D

    lo, hi = bounds
    millis = extract("epoch", D.completed_at - D.created_at) * 1000
    count, median = (
        await ctx.session.execute(
            select(func.count(), func.percentile_cont(0.5).within_group(millis)).where(
                D.tenant_id == ctx.tenant_id,
                D.completed_at.is_not(None),
                D.completed_at >= lo,
                D.completed_at < hi,
            )
        )
    ).first() or (0, None)
    return int(count or 0), (round(float(median), 1) if median is not None else None)


async def resolve_dsar_completion_time(ctx: MetricContext) -> dict[str, Any]:
    """The median time from a data subject request to its completion, for requests completed in the period."""
    count, median = await _dsar_completion(ctx, _bounds(ctx))
    if not count:
        return no_data("dsar_requests", "No data subject request was completed in this period.")
    out: dict[str, Any] = {
        "value": median,
        "rows": [{"label": "completed", "value": count}],
        "source": "dsar_requests",
        "data_quality": "measured",
    }
    previous = _compare_bounds(ctx)
    if previous is not None:
        prev_count, prev_median = await _dsar_completion(ctx, previous)
        out["compare_value"] = prev_median if prev_count else None
    return out


async def resolve_attestations_expiring(ctx: MetricContext) -> dict[str, Any]:
    """Provider residency attestations in force that expire within 30 days or have expired, now."""
    from sqlalchemy import select

    from core.models.provider_attestation import ProviderAttestation as P

    rows = (
        await ctx.session.execute(
            select(P.provider, P.data_region, P.expires_at).where(P.tenant_id == ctx.tenant_id, P.revoked_at.is_(None))
        )
    ).all()
    if not rows:
        return no_data(
            "provider_residency_attestations",
            "No provider residency attestation is recorded. Add them under Governance, Residency.",
        )
    due = sorted(
        (r for r in rows if r.expires_at is not None and r.expires_at <= ctx.now + ATTESTATION_HORIZON),
        key=lambda r: r.expires_at,
    )
    return {
        "value": len(due),
        "rows": [
            {
                "label": f"{r.provider} ({r.data_region})",
                "expires_at": r.expires_at.isoformat(),
                "expired": r.expires_at <= ctx.now,
            }
            for r in due[:10]
        ],
        "source": "provider_residency_attestations",
        "data_quality": "measured",
    }


def _change_kind(event_type: str) -> str | None:
    for kind, prefixes in CHANGE_KINDS:
        if any(event_type == p or (p.endswith(".") and event_type.startswith(p)) for p in prefixes):
            return kind
    return None


async def _changes(ctx: MetricContext, bounds: tuple[Any, Any]) -> dict[str, int]:
    from sqlalchemy import func, or_, select

    from core.models.audit import AuditLog as A
    from core.models.delegation import UserDelegation as U
    from core.models.prompt_template import PromptEditHistory as AgentPromptEdit
    from core.models.prompt_template import PromptTemplateEditHistory as TemplateEdit

    lo, hi = bounds
    counts: dict[str, int] = defaultdict(int)
    matches = [
        A.event_type == p if not p.endswith(".") else A.event_type.like(f"{p}%")
        for _kind, prefixes in CHANGE_KINDS
        for p in prefixes
    ]
    for event_type, n in await ctx.session.execute(
        select(A.event_type, func.count())
        .where(A.tenant_id == ctx.tenant_id, A.created_at >= lo, A.created_at < hi, or_(*matches))
        .group_by(A.event_type)
    ):
        kind = _change_kind(str(event_type))
        if kind:
            counts[kind] += int(n)
    for model in (AgentPromptEdit, TemplateEdit):
        counts["prompts"] += int(
            (
                await ctx.session.execute(
                    select(func.count()).where(
                        model.tenant_id == ctx.tenant_id, model.created_at >= lo, model.created_at < hi
                    )
                )
            ).scalar_one()
            or 0
        )
    counts["delegations"] += int(
        (
            await ctx.session.execute(
                select(func.count()).where(
                    U.tenant_id == ctx.tenant_id,
                    or_(U.created_at.between(lo, hi), U.revoked_at.between(lo, hi)),
                )
            )
        ).scalar_one()
        or 0
    )
    return dict(counts)


async def resolve_change_log(ctx: MetricContext) -> dict[str, Any]:
    """Changes made in the period, by kind: agents, prompts, governance, spend controls, settings, access, packs."""
    counts = await _changes(ctx, _bounds(ctx))
    total = sum(counts.values())
    if not total:
        return no_data("audit_log", "Nothing was changed in this period.")
    out: dict[str, Any] = {
        "value": total,
        "rows": [{"label": label, "value": counts.get(label, 0)} for label in CHANGE_LABELS if counts.get(label)],
        "source": "audit_log",
        "data_quality": "measured",
    }
    previous = _compare_bounds(ctx)
    if previous is not None:
        out["compare_value"] = sum((await _changes(ctx, previous)).values())
    return out


# ------------------------------------------------------------------ engineering


async def _gateway_counts(ctx: MetricContext, bounds: tuple[Any, Any]) -> dict[str, int]:
    from sqlalchemy import case, func, select

    from core.models.model_gateway_record import ModelGatewayRecord as G

    lo, hi = bounds
    query = select(
        func.count(),
        func.sum(case((G.outcome == "failed", 1), else_=0)),
        func.sum(case((G.fallback_from.is_not(None), 1), else_=0)),
        func.sum(case((G.restricted.is_(True), 1), else_=0)),
    ).where(G.tenant_id == ctx.tenant_id, G.created_at >= lo, G.created_at < hi)
    agents = await _domain_agent_ids(ctx)
    if agents is not None:
        query = query.where(G.agent_id.in_(agents))
    total, failed, fallback, restricted = (await ctx.session.execute(query)).first() or (0, 0, 0, 0)
    return {
        "calls": int(total or 0),
        "failed": int(failed or 0),
        "fallback": int(fallback or 0),
        "restricted": int(restricted or 0),
    }


async def resolve_gateway_issues(ctx: MetricContext) -> dict[str, Any]:
    """Model calls in the period that failed or fell back to another model; rows add the calls and restricted ones."""
    counts = await _gateway_counts(ctx, _bounds(ctx))
    if not counts["calls"]:
        return no_data(
            "model_gateway_records",
            "No model call was recorded in this period. Model gateway records must be on for the deployment.",
        )
    out: dict[str, Any] = {
        "value": counts["failed"] + counts["fallback"],
        "rows": [{"label": label, "value": counts[label]} for label in ("failed", "fallback", "restricted", "calls")],
        "source": "model_gateway_records",
        "data_quality": "measured",
    }
    previous = _compare_bounds(ctx)
    if previous is not None:
        before = await _gateway_counts(ctx, previous)
        out["compare_value"] = before["failed"] + before["fallback"] if before["calls"] else None
    return out


async def resolve_slow_spans(ctx: MetricContext) -> dict[str, Any]:
    """The span names that took the most time in the period, with calls, total time and p95; value = the top p95."""
    from sqlalchemy import case, func, select

    from core.models.run_span import RunSpan as S

    lo, hi = _bounds(ctx)
    total = func.sum(S.duration_ms)
    p95 = func.percentile_cont(0.95).within_group(S.duration_ms)
    query = (
        select(S.name, func.count(), total, p95, func.sum(case((S.status == "error", 1), else_=0)))
        .where(S.tenant_id == ctx.tenant_id, S.started_at >= lo, S.started_at < hi)
        .group_by(S.name)
        .order_by(total.desc())
        .limit(TOP_SPANS)
    )
    agents = await _domain_agent_ids(ctx)
    if agents is not None:
        query = query.where(S.agent_id.in_(agents))
    rows = (await ctx.session.execute(query)).all()
    if not rows:
        return no_data("run_spans", "No trace span was stored in this period. The run timeline must be on.")
    out_rows = [
        {
            "label": str(name).removeprefix("agenticorg."),
            "calls": int(calls or 0),
            "total_ms": int(total_ms or 0),
            "p95_ms": round(float(p95_ms), 1) if p95_ms is not None else None,
            "errors": int(errors or 0),
            "value": round(float(p95_ms), 1) if p95_ms is not None else None,
        }
        for name, calls, total_ms, p95_ms, errors in rows
    ]
    slowest = max((r["p95_ms"] or 0.0 for r in out_rows), default=None)
    return {"value": slowest, "rows": out_rows, "source": "run_spans", "data_quality": "measured"}


async def resolve_eval_pass_rate(ctx: MetricContext) -> dict[str, Any]:
    """The pass rate of the newest eval run in the period; rows give the newest run of each dataset."""
    if ctx.domains is not None:
        return no_data("eval_runs", "Eval runs cover the whole organisation; an administrator sees them.")
    from sqlalchemy import select

    from core.models.eval_dataset import EvalDataset
    from core.models.eval_run import EvalRun as E

    lo, hi = _bounds(ctx)
    runs = (
        await ctx.session.execute(
            select(E.dataset_id, E.version, E.pass_rate, E.cases_run, E.created_at, EvalDataset.name)
            .join(EvalDataset, EvalDataset.id == E.dataset_id)
            .where(E.tenant_id == ctx.tenant_id, E.created_at >= lo, E.created_at < hi, E.pass_rate.is_not(None))
            .order_by(E.created_at.desc())
        )
    ).all()
    if not runs:
        return no_data("eval_runs", "No eval run finished in this period. Run one from Evals.")
    newest: dict[Any, Any] = {}
    for r in runs:
        newest.setdefault(r.dataset_id, r)
    return {
        "value": round(float(runs[0].pass_rate) * 100, 1),
        "rows": [
            {
                "label": f"{r.name} v{r.version}",
                "value": round(float(r.pass_rate) * 100, 1),
                "cases": int(r.cases_run or 0),
                "at": r.created_at.isoformat(),
            }
            for r in list(newest.values())[:10]
        ],
        "source": "eval_runs",
        "data_quality": "measured",
    }


async def _prompt_edits(ctx: MetricContext, bounds: tuple[Any, Any]) -> tuple[int, int]:
    from sqlalchemy import func, select

    from core.models.prompt_template import PromptEditHistory as AgentPromptEdit
    from core.models.prompt_template import PromptTemplateEditHistory as TemplateEdit

    lo, hi = bounds
    agent_query = select(func.count()).where(
        AgentPromptEdit.tenant_id == ctx.tenant_id, AgentPromptEdit.created_at >= lo, AgentPromptEdit.created_at < hi
    )
    agents = await _domain_agent_ids(ctx)
    if agents is not None:
        agent_query = agent_query.where(func_text(AgentPromptEdit.agent_id).in_(agents))
    agent_edits = int((await ctx.session.execute(agent_query)).scalar_one() or 0)
    template_edits = 0
    if agents is None:
        template_edits = int(
            (
                await ctx.session.execute(
                    select(func.count()).where(
                        TemplateEdit.tenant_id == ctx.tenant_id,
                        TemplateEdit.created_at >= lo,
                        TemplateEdit.created_at < hi,
                    )
                )
            ).scalar_one()
            or 0
        )
    return agent_edits, template_edits


async def resolve_prompt_edits(ctx: MetricContext) -> dict[str, Any]:
    """Agent prompt and prompt template edits in the period."""
    agent_edits, template_edits = await _prompt_edits(ctx, _bounds(ctx))
    if not agent_edits and not template_edits:
        return no_data("prompt_edit_history", "No prompt was edited in this period.")
    out: dict[str, Any] = {
        "value": agent_edits + template_edits,
        "rows": [{"label": "agent_prompts", "value": agent_edits}, {"label": "templates", "value": template_edits}],
        "source": "prompt_edit_history",
        "data_quality": "measured",
    }
    previous = _compare_bounds(ctx)
    if previous is not None:
        out["compare_value"] = sum(await _prompt_edits(ctx, previous))
    return out
