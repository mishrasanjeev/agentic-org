# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Run evidence (Level 4): one run fact and the records around it.

A run fact is found by its id and its ``started_at`` (the table is
partitioned by month, so the instant bounds the read to one partition). Around
it: the model gateway records with its correlation id; the run's own trace
spans as a waterfall (the root span with the fact's correlation id, else the
root span of its agent nearest its start, else every span of its trace); the
approvals raised for it (by correlation id or workflow run) with the delegation
and policy votes behind each decision; and the audit rows with its trace id or
workflow run, each with its HMAC check and chain position. Every read is in the
caller's tenant session; a fact outside the caller's domains or company is not
found (404), never shown. No output text, prompt or tool argument is returned:
the evidence is ids, timings, statuses, tokens and cost (span attributes hold
only those; see ``observability.timeline``).

The bundle (``run_bundle``) is the same evidence as one JSON document with a
SHA-256 over its canonical form; for administrators and auditors it also
carries each audit row's signed payload, signature and chain links, so the rows
can be checked offline with the tenant's audit key.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from core.insights.explorers import jsonable
from core.insights.metrics import DEBUG_ENTRYPOINT, MetricContext, money

MAX_RECORDS = 50
MAX_SPANS = 200
# ``at`` may be off by this much (rounding in links); the read stays inside one or two partitions.
AT_TOLERANCE = timedelta(seconds=1)
# Approvals and audit rows are matched on unindexed JSON fields, so only within this window after the run started
# (an approval may be decided days later, but it is raised during the run).
RELATED_BEFORE = timedelta(minutes=5)
RELATED_AFTER = timedelta(days=2)
ROOT_SPANS = ("agenticorg.agent.run", "agenticorg.agent.resume")
BUNDLE_FORMAT = "agenticorg-insights-run-bundle-v1"
# Run fact statuses that end a run without its outcome (``core.insights.run_facts.STATUSES``).
FAILED_STATUSES = frozenset({"failed", "limit_exceeded", "refused", "rejected"})


class EvidenceNotFoundError(LookupError):
    """No run fact with that id at that instant that this caller may see."""


def span_summary(attributes: dict[str, Any]) -> str:
    """One line from a span's attributes: model, tokens, connector tool and outcome, search results, run status."""
    a = attributes or {}
    parts: list[str] = []
    if a.get("llm.provider") or a.get("llm.model"):
        parts.append(f"{a.get('llm.provider') or ''}/{a.get('llm.model') or ''}")
    if a.get("llm.input_tokens") is not None or a.get("llm.output_tokens") is not None:
        parts.append(f"{a.get('llm.input_tokens') or 0} in / {a.get('llm.output_tokens') or 0} out")
    if a.get("tool.name"):
        parts.append(f"{a.get('connector.id') or ''}:{a['tool.name']} {a.get('tool.outcome') or ''}".strip())
    if a.get("search.results") is not None:
        parts.append(f"{a['search.results']} results")
    if a.get("agent.run.status"):
        parts.append(str(a["agent.run.status"]))
    return " · ".join(parts)


def _event_label(event: dict[str, Any]) -> str:
    a = event.get("attributes") or {}
    name = str(event.get("name") or "")
    if name == "guardrail.outcome":
        applied = "applied" if a.get("applied") else None
        parts = ("guardrail", a.get("stage"), a.get("detector"), a.get("action"), applied)
        return " ".join(str(p) for p in parts if p)
    if name == "model_gateway.decision":
        return f"gateway {a.get('provider') or ''}/{a.get('model') or ''} {a.get('reason') or ''}".strip()
    if name == "exception":
        return f"exception {a.get('exception.type') or ''}".strip()
    return name


def _depths(rows: list[Any]) -> dict[str, int]:
    """Depth of every span by its parent links; a span whose parent is not in the run sits at the root."""
    by_id = {r.span_id: r for r in rows}
    depths: dict[str, int] = {}

    def depth(row: Any, guard: int) -> int:
        if row.span_id in depths:
            return depths[row.span_id]
        parent = by_id.get(row.parent_span_id) if row.parent_span_id else None
        value = depth(parent, guard + 1) + 1 if parent is not None and guard < 32 else 0
        depths[row.span_id] = value
        return value

    for row in rows:
        depth(row, 0)
    return depths


async def _run_spans(ctx: MetricContext, fact: Any) -> list[Any]:
    from sqlalchemy import func, select

    from core.models.run_span import RunSpan as S

    root = None
    if fact.correlation_id:
        root = (
            await ctx.session.execute(
                select(S)
                .where(S.tenant_id == ctx.tenant_id, S.correlation_id == fact.correlation_id, S.name.in_(ROOT_SPANS))
                .order_by(S.started_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if root is None and fact.trace_id:
        query = select(S).where(S.tenant_id == ctx.tenant_id, S.trace_id == fact.trace_id, S.name.in_(ROOT_SPANS))
        if fact.agent_id:
            query = query.where(S.agent_id == str(fact.agent_id))
        nearest = func.abs(func.extract("epoch", S.started_at) - fact.started_at.timestamp())
        root = (await ctx.session.execute(query.order_by(nearest.asc()).limit(1))).scalar_one_or_none()
    if root is not None:
        where = [S.tenant_id == ctx.tenant_id, S.run_span_id == root.run_span_id, S.trace_id == root.trace_id]
    elif fact.trace_id:
        where = [S.tenant_id == ctx.tenant_id, S.trace_id == fact.trace_id]
    else:
        return []
    return list(
        (
            await ctx.session.execute(
                select(S).where(*where).order_by(S.started_at.asc(), S.duration_ms.desc()).limit(MAX_SPANS)
            )
        ).scalars()
    )


def _spans_out(rows: list[Any]) -> tuple[list[dict[str, Any]], int]:
    if not rows:
        return [], 0
    start = min(r.started_at for r in rows)
    end = max(r.started_at.timestamp() * 1000 + int(r.duration_ms or 0) for r in rows)
    depths = _depths(rows)
    spans = [
        {
            "span_id": r.span_id,
            "parent_span_id": r.parent_span_id,
            "depth": depths.get(r.span_id, 0),
            "name": r.name,
            "kind": r.kind,
            "status": r.status,
            "started_at": r.started_at,
            "offset_ms": max(0, int((r.started_at - start).total_seconds() * 1000)),
            "duration_ms": int(r.duration_ms or 0),
            "summary": span_summary(dict(r.attributes or {})),
            "events": [
                {"name": e.get("name"), "offset_ms": e.get("offset_ms"), "label": _event_label(e)}
                for e in (r.events or [])
                if isinstance(e, dict)
            ],
        }
        for r in rows
    ]
    return spans, max(0, int(end - start.timestamp() * 1000))


def debug_ref(rows: list[Any], agent_id: Any) -> dict[str, str] | None:
    """The agent and LangGraph thread the run's root span names, for the run debugger (identifiers only)."""
    if not rows:
        return None
    root = next((r for r in rows if not r.parent_span_id), rows[0])
    attributes = dict(root.attributes or {})
    thread = attributes.get("agent.thread_id")
    agent = attributes.get("agent.id") or (str(agent_id) if agent_id else None)
    if not isinstance(thread, str) or not thread or not isinstance(agent, str) or not agent:
        return None
    return {"agent_id": agent, "thread_id": thread}


def first_failure(run: dict[str, Any], spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Why the run failed: the earliest error span with no erroring child (an error marks every span above it, so
    that span is where it started), else the run's own error class."""
    errors = [s for s in spans if s["status"] == "error"]
    if errors:
        parents = {s["parent_span_id"] for s in errors if s.get("parent_span_id")}
        causes = [s for s in errors if s["span_id"] not in parents] or errors
        span = min(causes, key=lambda s: (s["offset_ms"], -s["depth"]))
        exceptions = [e["label"] for e in span["events"] if e["name"] == "exception"]
        return {
            "source": "span",
            "span_id": span["span_id"],
            "name": span["name"],
            "summary": span["summary"],
            "exception": exceptions[0] if exceptions else None,
            "offset_ms": span["offset_ms"],
            "error_class": run.get("error_class"),
        }
    if run.get("status") in FAILED_STATUSES:
        return {"source": "run", "error_class": run.get("error_class"), "status": run.get("status")}
    return None


async def _load_fact(ctx: MetricContext, fact_id: uuid.UUID, at: datetime) -> Any:
    from sqlalchemy import select

    from core.models.insights import RunFact as F

    clauses = [
        F.tenant_id == ctx.tenant_id,
        F.id == fact_id,
        F.started_at >= at - AT_TOLERANCE,
        F.started_at <= at + AT_TOLERANCE,
        F.entrypoint != DEBUG_ENTRYPOINT,
    ]
    if ctx.company_id is not None:
        clauses.append(F.company_id == ctx.company_id)
    if ctx.domains is not None:
        clauses.append(F.domain.in_(list(ctx.domains)))
    fact = (await ctx.session.execute(select(F).where(*clauses))).scalar_one_or_none()
    if fact is None:
        raise EvidenceNotFoundError(str(fact_id))
    return fact


async def _collect(ctx: MetricContext, fact_id: uuid.UUID, at: datetime) -> tuple[dict[str, Any], list[Any]]:
    """The evidence document (not yet JSON-safe) and the raw audit rows behind it."""
    from sqlalchemy import or_, select

    from core.governance.model_gateway_records import verify_record
    from core.insights.explore_sources import user_names
    from core.insights.metrics import agent_names
    from core.models.audit import AuditLog as A
    from core.models.hitl import HITLQueue as H
    from core.models.model_gateway_record import ModelGatewayRecord as G
    from core.tool_gateway.audit_logger import verify_audit_row

    fact = await _load_fact(ctx, fact_id, at)
    names = await agent_names(ctx, [fact.agent_id] if fact.agent_id else [])
    run: dict[str, Any] = {
        "id": str(fact.id),
        "run_key": fact.run_key,
        "started_at": fact.started_at,
        "agent": names.get(str(fact.agent_id)) if fact.agent_id else fact.agent_ref,
        "agent_id": str(fact.agent_id) if fact.agent_id else None,
        "agent_type": fact.agent_type,
        "agent_version": fact.agent_version,
        "domain": fact.domain,
        "entrypoint": fact.entrypoint,
        "task_type": fact.task_type,
        "use_case": fact.use_case,
        "environment": fact.environment,
        "status": fact.status,
        "error_class": fact.error_class,
        "duration_ms": fact.duration_ms,
        "confidence": fact.confidence,
        "confidence_floor": fact.confidence_floor,
        "hitl_required": fact.hitl_required,
        "outcome_validated": fact.outcome_validated,
        "tokens": {
            "input": fact.tokens_in,
            "output": fact.tokens_out,
            "cached": fact.tokens_cached,
            "total": fact.tokens_total,
        },
        "cost_usd": money(fact.cost_usd, 6),
        "provider": fact.provider,
        "model": fact.model,
        "data_quality": fact.data_quality,
        "correlation_id": fact.correlation_id,
        "trace_id": fact.trace_id,
        "workflow_run_id": str(fact.workflow_run_id) if fact.workflow_run_id else None,
        "step_id": fact.step_id,
    }
    gateway: list[dict[str, Any]] = []
    if fact.correlation_id:
        for g in (
            await ctx.session.execute(
                select(G)
                .where(G.tenant_id == ctx.tenant_id, G.correlation_id == fact.correlation_id)
                .order_by(G.created_at.asc())
                .limit(MAX_RECORDS)
            )
        ).scalars():
            gateway.append(
                {
                    "id": str(g.id),
                    "created_at": g.created_at,
                    "provider": g.provider,
                    "model": g.model,
                    "requested_model": g.requested_model,
                    "fallback_from": g.fallback_from,
                    "restricted": g.restricted,
                    "outcome": g.outcome,
                    "error_type": g.error_type,
                    "latency_ms": g.latency_ms,
                    "admission_wait_ms": g.admission_wait_ms,
                    "input_tokens": g.input_tokens,
                    "output_tokens": g.output_tokens,
                    "cost_usd": money(g.cost_usd, 6),
                    "signed": bool(g.signature),
                    "verified": bool(g.signature) and _verifies(verify_record, g),
                }
            )
    span_rows = await _run_spans(ctx, fact)
    spans, span_total_ms = _spans_out(span_rows)
    approvals: list[dict[str, Any]] = []
    links = []
    if fact.correlation_id:
        links.append(H.context["correlation_id"].astext == fact.correlation_id)
    if fact.workflow_run_id:
        links.append(H.workflow_run_id == fact.workflow_run_id)
    if links:
        items = (
            (
                await ctx.session.execute(
                    select(H)
                    .where(
                        H.tenant_id == ctx.tenant_id,
                        H.created_at >= fact.started_at - RELATED_BEFORE,
                        H.created_at <= fact.started_at + RELATED_AFTER,
                        or_(*links),
                    )
                    .order_by(H.created_at.asc())
                    .limit(MAX_RECORDS)
                )
            )
            .scalars()
            .all()
        )
        # The decision rows say who decided under whose delegation (the queue row keeps only the decider).
        decided: dict[str, dict[str, Any]] = {}
        if items:
            for resource_id, details in await ctx.session.execute(
                select(A.resource_id, A.details).where(
                    A.tenant_id == ctx.tenant_id,
                    A.event_type == "hitl.decided",
                    A.resource_type == "hitl_item",
                    A.resource_id.in_([str(i.id) for i in items]),
                )
            ):
                decided[str(resource_id)] = details or {}
        votes_by_item = {
            str(i.id): list(((i.context or {}).get("policy_state") or {}).get("approvals") or []) for i in items
        }
        people = await user_names(
            ctx,
            [
                *(i.decision_by for i in items if i.decision_by),
                *(d.get("delegated_from") for d in decided.values() if d.get("delegated_from")),
                *(v.get("user_id") for vs in votes_by_item.values() for v in vs if isinstance(v, dict)),
            ],
        )
        for item in items:
            delegated = decided.get(str(item.id), {}).get("delegated_from")
            approvals.append(
                {
                    "id": str(item.id),
                    "title": item.title,
                    "trigger_type": item.trigger_type,
                    "status": item.status,
                    "decision": item.decision,
                    "decided_by": people.get(str(item.decision_by), "User") if item.decision_by else None,
                    "delegated_from": people.get(str(delegated), "User") if delegated else None,
                    "votes": [
                        {
                            "by": people.get(str(v.get("user_id")), "User"),
                            "decision": v.get("decision"),
                            "step": v.get("sequence"),
                            "at": v.get("at"),
                        }
                        for v in votes_by_item.get(str(item.id), [])
                        if isinstance(v, dict)
                    ],
                    "created_at": item.created_at,
                    "first_viewed_at": item.first_viewed_at,
                    "decision_at": item.decision_at,
                    "link": f"/dashboard/approvals?id={item.id}",
                }
            )
    audit: list[dict[str, Any]] = []
    audit_rows: list[Any] = []
    audit_links = []
    if fact.trace_id:
        audit_links.append(A.trace_id == fact.trace_id)
    if fact.workflow_run_id:
        audit_links.append(A.workflow_run_id == fact.workflow_run_id)
    if fact.correlation_id:
        audit_links.append(A.details["correlation_id"].astext == fact.correlation_id)
    if audit_links:
        audit_rows = list(
            (
                await ctx.session.execute(
                    select(A)
                    .where(
                        A.tenant_id == ctx.tenant_id,
                        A.created_at >= fact.started_at - RELATED_BEFORE,
                        A.created_at <= fact.started_at + RELATED_AFTER,
                        or_(*audit_links),
                    )
                    .order_by(A.created_at.asc())
                    .limit(MAX_RECORDS)
                )
            ).scalars()
        )
        for a in audit_rows:
            audit.append(
                {
                    "id": str(a.id),
                    "created_at": a.created_at,
                    "event_type": a.event_type,
                    "action": (a.action or "")[:200],
                    "outcome": a.outcome,
                    "signed": bool(a.signature),
                    "verified": verify_audit_row(a),
                    "chain_seq": a.chain_seq,
                    "sealed": a.sealed_at is not None,
                }
            )
    document = {
        "run": run,
        "failure": first_failure(run, spans),
        "gateway_records": gateway,
        "spans": spans,
        "span_total_ms": span_total_ms,
        "debug": debug_ref(span_rows, fact.agent_id),
        "approvals": approvals,
        "audit": audit,
        "links": {
            **({"agent": f"/dashboard/agents/{fact.agent_id}"} if fact.agent_id else {}),
        },
        "limits": {"records": MAX_RECORDS, "spans": MAX_SPANS},
    }
    return document, audit_rows


def _verifies(verify: Any, record: Any) -> bool:
    try:
        return bool(verify(record))
    # enterprise-gate: broad-except-ok reason=a-record-that-cannot-be-checked-fails-closed-as-not-verified
    except Exception:  # noqa: BLE001
        return False


async def run_evidence(ctx: MetricContext, fact_id: uuid.UUID, at: datetime) -> dict[str, Any]:
    document, _rows = await _collect(ctx, fact_id, at)
    return jsonable(document)


def canonical_json(document: dict[str, Any]) -> bytes:
    """A document's canonical JSON (sorted keys, no spaces, UTF-8): the bytes a bundle file holds."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False).encode("utf-8")


def canonical_sha256(document: dict[str, Any]) -> str:
    """SHA-256 of a document's canonical JSON: a bundle file's checksum (``sha256sum`` of the file gives the same)."""
    return hashlib.sha256(canonical_json(document)).hexdigest()


async def run_bundle(
    ctx: MetricContext, fact_id: uuid.UUID, at: datetime, *, include_audit_payloads: bool, generated_at: datetime
) -> dict[str, Any]:
    """The run's evidence as one document plus the SHA-256 of its canonical form (``sha256`` is not part of it)."""
    from core.governance.audit_chain_link import canonical_audit_payload

    document, rows = await _collect(ctx, fact_id, at)
    bundle: dict[str, Any] = {
        "format": BUNDLE_FORMAT,
        "generated_at": generated_at,
        "tenant_id": str(ctx.tenant_id),
        **document,
    }
    if include_audit_payloads:
        bundle["audit_payloads"] = [
            {
                "id": str(a.id),
                "payload": json.loads(canonical_audit_payload(a)),
                "signature": a.signature,
                "chain_seq": a.chain_seq,
                "chain_prev": a.chain_prev,
                "chain_hash": a.chain_hash,
            }
            for a in rows
        ]
        bundle["verification"] = {
            "algorithm": "HMAC-SHA256 over the canonical audit payload with the tenant audit key",
            "offline_verifiable": False,
        }
    safe = jsonable(bundle)
    return {"bundle": safe, "sha256": canonical_sha256(safe)}


def parse_at(raw: str | None) -> datetime | None:
    """The ``at`` query value as an aware instant, or None when it is not an ISO timestamp with an offset."""
    if not raw:
        return None
    # A "+" in an unencoded query string arrives as a space.
    try:
        value = datetime.fromisoformat(raw.strip().replace(" ", "+").replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo is not None else None
