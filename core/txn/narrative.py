# SPDX-License-Identifier: Apache-2.0
"""Suspicious-transaction narratives: a draft from a finding and its evidence, by a model or from the facts.

A narrative tells an investigator what was seen, over which period, on
which accounts, through which counterparties, and why the detector
raised it, with a recommendation; it is a draft for a person to review,
never a filing. The model path goes through the content services'
checked JSON call so the draft has one shape whoever wrote it; the
extractive path writes the same sections from the finding's facts and
the supporting rows, so a draft exists without a model. The evidence
package behind a draft is the finding, the rows, the entity view and the
fund-flow rows, with a digest over the whole so a reviewer can tell it
was not altered.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

import structlog

from core.spend import context as spend_context

logger = structlog.get_logger()

METHODS = ("auto", "model", "extractive")
MAX_ROWS = 200
SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title", "summary", "basis", "recommendation"],
    "properties": {
        "title": {"type": "string", "maxLength": 160},
        "summary": {"type": "string", "maxLength": 2000},
        "timeline": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 300}},
        "parties": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 200}},
        "basis": {"type": "array", "maxItems": 10, "items": {"type": "string", "maxLength": 400}},
        "recommendation": {"type": "string", "enum": ["dismiss", "confirm", "escalate"]},
        "gaps": {"type": "array", "maxItems": 10, "items": {"type": "string", "maxLength": 300}},
    },
}


def _when(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value or "")
    return text[:10]


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return str(value)


def timeline_of(rows: list[dict[str, Any]], *, limit: int = 30) -> list[str]:
    """One line per supporting movement, oldest first."""
    ordered = sorted(rows, key=lambda r: str(r.get("booked_at") or ""))
    out = []
    for row in ordered[:limit]:
        side = "in" if row.get("direction") == "credit" else "out"
        who = row.get("counterparty") or row.get("counterparty_name") or "unknown"
        place = f" at {row['branch']}" if row.get("branch") else ""
        out.append(
            f"{_when(row.get('booked_at'))}: {_money(row.get('amount'))} {side} by {row.get('channel') or 'other'}"
            f"{place}, {'from' if side == 'in' else 'to'} {who}"
        )
    return out


def parties_of(finding: dict[str, Any], rows: list[dict[str, Any]], view: dict[str, Any] | None) -> list[str]:
    out = [f"{finding.get('entity_kind', 'account')} {finding.get('entity_ref')}"]
    for row in rows:
        who = row.get("counterparty") or row.get("counterparty_name")
        if who and who not in out:
            out.append(str(who))
    for customer in (view or {}).get("customers") or []:
        if customer not in out:
            out.append(f"customer {customer}")
    return out[:20]


def basis_of(finding: dict[str, Any]) -> list[str]:
    facts = finding.get("facts") or {}
    kind = finding.get("kind")
    out = []
    if kind == "structuring":
        out.append(
            f"{facts.get('count')} cash deposits each under the reporting threshold "
            f"of {_money(facts.get('threshold'))} "
            f"within {facts.get('window_days')} days, together {_money(facts.get('total'))}"
        )
        if len(facts.get("branches") or []) >= 2:
            out.append(f"deposited across {len(facts['branches'])} branches: {', '.join(facts['branches'])}")
        if facts.get("largest") is not None:
            out.append(f"the largest single deposit was {_money(facts['largest'])}")
    elif kind == "pass_through":
        out.append(
            f"{_money(facts.get('inflow'))} received from {facts.get('from')} and {_money(facts.get('outflow'))} "
            f"({int(round(float(facts.get('ratio') or 0) * 100))} per cent) paid out within {facts.get('hours')} hours"
        )
        if facts.get("to"):
            out.append(f"paid to {len(facts['to'])} counterpart(ies): {', '.join(str(t) for t in facts['to'])}")
    else:
        out.append(str(finding.get("summary") or ""))
    out.append(f"severity {finding.get('severity')}; detected {_when(finding.get('detected_at'))}")
    return out


def extractive(
    finding: dict[str, Any], rows: list[dict[str, Any]], view: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A draft from the facts alone, in the same shape the model gives."""
    kind = str(finding.get("kind") or "finding").replace("_", " ")
    entity = f"{finding.get('entity_kind', 'account')} {finding.get('entity_ref')}"
    facts = finding.get("facts") or {}
    period = ""
    if facts.get("window_start"):
        period = f" between {_when(facts.get('window_start'))} and {_when(facts.get('window_end'))}"
    elif rows:
        ordered = sorted(str(r.get("booked_at") or "") for r in rows)
        period = f" between {ordered[0][:10]} and {ordered[-1][:10]}"
    summary = (
        f"The {kind} detector raised {entity}{period}: {finding.get('summary')}. "
        f"{len(rows)} movement(s) support the finding."
    )
    if view:
        totals = view.get("totals") or {}
        summary += (
            f" Over the period reviewed the {view.get('kind')} received {_money(totals.get('in'))} "
            f"and paid out {_money(totals.get('out'))}"
        )
        if view.get("cash_share") is not None:
            summary += f", {int(round(float(view['cash_share']) * 100))} per cent of receipts in cash"
        summary += "."
    recommendation = "escalate" if finding.get("severity") == "high" else "confirm"
    gaps = []
    if not any(r.get("customer_ref") for r in rows):
        gaps.append("no customer is linked to the account in the records")
    if any(not r.get("counterparty") and not r.get("counterparty_name") for r in rows):
        gaps.append("some movements name no counterparty")
    return {
        "method": "extractive",
        "title": f"{kind.capitalize()} on {entity}",
        "summary": summary,
        "timeline": timeline_of(rows),
        "parties": parties_of(finding, rows, view),
        "basis": basis_of(finding),
        "recommendation": recommendation,
        "gaps": gaps,
    }


def _messages(finding: dict[str, Any], rows: list[dict[str, Any]], view: dict[str, Any] | None) -> list[dict[str, str]]:
    system = (
        "You draft suspicious-transaction narratives for a bank's investigators from the facts given; never add "
        "facts, names or amounts that are not in them, and say what is missing. Answer with one JSON object and "
        "nothing else: {title, summary (what was seen, when, on which accounts, through which counterparties, why "
        "it was raised), timeline: lines oldest first, parties, basis: the facts the finding rests on, "
        "recommendation: one of dismiss, confirm, escalate, gaps: what an investigator should still check}."
    )
    payload = {
        "finding": {
            k: finding.get(k)
            for k in ("kind", "entity_kind", "entity_ref", "severity", "summary", "facts", "detected_at")
        },
        "movements": [
            {
                k: r.get(k)
                for k in (
                    "booked_at",
                    "direction",
                    "amount",
                    "channel",
                    "branch",
                    "counterparty",
                    "counterparty_name",
                    "description",
                )
            }
            for r in sorted(rows, key=lambda r: str(r.get("booked_at") or ""))[:MAX_ROWS]
        ],
        "entity": {
            k: (view or {}).get(k)
            for k in ("kind", "ref", "totals", "by_channel", "by_branch", "cash_share", "customers")
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)[:12_000]},
    ]


async def model(
    tenant_id: uuid.UUID,
    finding: dict[str, Any],
    rows: list[dict[str, Any]],
    view: dict[str, Any] | None = None,
    *,
    complete: Any = None,
) -> dict[str, Any]:
    from core.content import services

    with spend_context.scope(application="txn", default_use_case="txn.narrative"):
        answer, usage = await services.ask_model(tenant_id, _messages(finding, rows, view), SCHEMA, complete=complete)
    return {
        "method": "model",
        "title": str(answer.get("title") or "")[:160],
        "summary": str(answer.get("summary") or "")[:2000],
        "timeline": [str(t)[:300] for t in answer.get("timeline") or []][:30],
        "parties": [str(p)[:200] for p in answer.get("parties") or []][:20],
        "basis": [str(b)[:400] for b in answer.get("basis") or []][:10],
        "recommendation": str(answer.get("recommendation") or "confirm"),
        "gaps": [str(g)[:300] for g in answer.get("gaps") or []][:10],
        "model": usage,
    }


async def draft(
    tenant_id: uuid.UUID,
    finding: dict[str, Any],
    rows: list[dict[str, Any]],
    view: dict[str, Any] | None = None,
    *,
    method: str = "auto",
    complete: Any = None,
) -> dict[str, Any]:
    """The narrative by the method asked: the model, the facts, or the model with the facts as the fallback."""
    if method not in METHODS:
        raise ValueError(f"method is one of {', '.join(METHODS)}")
    if method == "extractive":
        return extractive(finding, rows, view)
    try:
        return await model(tenant_id, finding, rows, view, complete=complete)
    # enterprise-gate: broad-except-ok reason=model-boundary-falls-back-to-the-extractive-draft-and-says-so
    except Exception as exc:  # noqa: BLE001 - the model boundary; the facts stand in and the draft says so
        if method == "model":
            raise
        logger.warning("txn_narrative_model_failed", error_type=type(exc).__name__)
        return {**extractive(finding, rows, view), "fallback_from": "model"}


def digest(package: dict[str, Any]) -> str:
    """A digest over the evidence package without its own digest, so a reviewer can tell it was not altered."""
    body = {k: v for k, v in package.items() if k != "digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()
