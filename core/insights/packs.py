# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""View PDFs, the finance pack and the period evidence pack (built by the export worker).

``view_pdf``: any view the caller may read, as a PDF (tiles as a table, each
chart as its data with a bar per row; ``core.insights.pdf``).

``finance_pack`` (administrators and the CFO): a ZIP of ``finance.xlsx``
(Summary, Spend by day, Spend by agent, Spend by model, Budgets, Tokens, Money
saved, Assumptions, Filters, Definitions), ``finance.pdf`` (the Finance view)
and ``manifest.json``.

``evidence_pack`` (administrators and auditors), for a date range of at most
366 days: a ZIP of ``summary.pdf``; CSV appendices of the period's audit rows
(each with its chain position and whether its signature verifies), approval
decisions (who decided, under whose delegation, each vote, seconds from
opening to decision), delegations, data subject requests (no subject),
provider residency attestations and the change log (no prompt text);
``verification.json`` (every period row's HMAC checked, the audit chain
re-verified over the sequence range that covers the period, the anchor and
witness checkpoints); and ``manifest.json`` (each file's SHA-256 and an
HMAC-SHA256 of the manifest with the platform audit key). An auditor or
administrator then signs the pack (``sign``): a signed audit row records who,
the statement and the manifest's SHA-256.

Every file follows the tenant's PII masking setting unless an administrator or
auditor asked for raw values.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import uuid
import zipfile
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from core.insights.export_jobs import Built, ExportJobError, builder, caller_from_snapshot

FINANCE_ROLES = frozenset({"cfo"})
# The partner client pack carries the firm's billing: administrators and the CFO.
PARTNER_PACK_ROLES = frozenset({"cfo"})
MAX_PACK_CLIENTS = 500
EVIDENCE_ROLES = frozenset({"auditor"})
MAX_EVIDENCE_DAYS = 366
MAX_AUDIT_ROWS = 200_000
MANIFEST_FORMAT = "agenticorg-insights-evidence-pack-v1"
KEY_ID = "platform-audit-key"
SIGN_STATEMENT_MAX = 1000


def _day(raw: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        raise ExportJobError("invalid_period", f"{field} is a date (YYYY-MM-DD)") from None


def validate(kind: str, params: dict[str, Any], caller: Any) -> dict[str, Any]:
    from core.insights import registry

    role = (caller.role or "").lower()
    if kind == "view_pdf":
        view = str(params.get("view") or "")
        if view not in registry.VIEWS:
            raise ExportJobError("unknown_view", f"no view {view!r}", 404)
        if view not in registry.allowed_views(caller.role, is_admin=caller.is_admin):
            raise ExportJobError("insights_view_forbidden", "you may not read this view", 403)
        return {"view": view, "compare": "none" if params.get("compare") == "none" else "previous"}
    if kind == "finance_pack":
        if not (caller.is_admin or role in FINANCE_ROLES):
            raise ExportJobError("insights_pack_forbidden", "the finance pack is for administrators and the CFO", 403)
        return {"view": "finance", "compare": "previous"}
    if kind == "partner_pack":
        if not (caller.is_admin or role in PARTNER_PACK_ROLES):
            raise ExportJobError(
                "insights_pack_forbidden", "the partner client pack is for administrators and the CFO", 403
            )
        return {"view": "partner", "compare": "none"}
    if kind == "evidence_pack":
        if not (caller.is_admin or role in EVIDENCE_ROLES):
            raise ExportJobError("insights_pack_forbidden", "the evidence pack is for administrators and auditors", 403)
        start, end = _day(params.get("from"), "from"), _day(params.get("to"), "to")
        if end < start or (end - start).days >= MAX_EVIDENCE_DAYS:
            raise ExportJobError("invalid_period", f"from must not be after to, and at most {MAX_EVIDENCE_DAYS} days")
        return {}
    raise ExportJobError("unknown_kind", f"no export kind {kind!r}")


# ------------------------------------------------------------------ shared


async def _organisation(session: Any, tenant_id: uuid.UUID) -> str:
    from sqlalchemy import select

    from core.models.tenant import Tenant

    name = (await session.execute(select(Tenant.name).where(Tenant.id == tenant_id))).scalar_one_or_none()
    return str(name or "Your organisation")


async def _resolve_view(session: Any, job: Any, params: dict[str, Any], view: str) -> tuple[Any, dict[str, Any]]:
    from api.v1.insights import view_domains
    from core.insights import registry
    from core.insights.export_jobs import _metric_context

    caller = caller_from_snapshot(params["caller"])
    ctx = await _metric_context(session, job.tenant_id, caller, params, compare=params.get("compare") or "previous")
    ctx.domains = view_domains(view, caller.domains)
    resolved = {key: await registry.resolve(registry.METRICS[key], ctx) for key in registry.VIEWS[view].keys}
    return ctx, resolved


async def _view_pdf_bytes(
    session: Any, job: Any, params: dict[str, Any], view: str
) -> tuple[bytes, Any, dict[str, Any]]:
    from core.insights import pdf, registry

    ctx, resolved = await _resolve_view(session, job, params, view)
    spec = registry.VIEWS[view]
    content = pdf.view_pdf(
        title=f"Insights - {spec.title}",
        organisation=await _organisation(session, job.tenant_id),
        period=ctx.period.as_dict(),
        filters={"company": params.get("company_id"), "compare": params.get("compare")},
        tiles=[resolved[k] for k in spec.tiles],
        charts=[resolved[k] for k in spec.charts],
        currency=ctx.reporting.currency,
        computed_at=datetime.now(UTC),
    )
    return content, ctx, resolved


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict | list):
        return json.dumps(value, default=str, sort_keys=True)
    return str(value)


def _csv(header: list[str], rows: Iterable[dict[str, Any]]) -> bytes:
    from core.insights.export import guard_cell

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([guard_cell(_text(row.get(name))) for name in header])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def _canonical(body: dict[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def manifest(files: dict[str, bytes], meta: dict[str, Any]) -> dict[str, Any]:
    """Each file's SHA-256 and size, the pack's facts, and an HMAC-SHA256 over the canonical manifest."""
    from core.config import settings

    body: dict[str, Any] = {
        "format": MANIFEST_FORMAT,
        **meta,
        "files": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in files.items()
        },
    }
    value = hmac.new(settings.secret_key.encode(), _canonical(body), hashlib.sha256).hexdigest()
    body["signature"] = {
        "algorithm": "HMAC-SHA256",
        "key_id": KEY_ID,
        "value": value,
        "covers": "the manifest without this signature, as canonical JSON (sorted keys, no spaces)",
    }
    return body


def verify_manifest(body: dict[str, Any]) -> bool:
    from core.config import settings

    signature = (body.get("signature") or {}).get("value")
    unsigned = {k: v for k, v in body.items() if k != "signature"}
    expected = hmac.new(settings.secret_key.encode(), _canonical(unsigned), hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(str(signature), expected)


async def _mask(session: Any, job: Any, rows: list[dict[str, Any]], caller: Any) -> tuple[list[dict[str, Any]], bool]:
    from core.insights import pii

    return await pii.apply(
        session, job.tenant_id, rows, include_pii=bool(job.include_pii), role=caller.role, is_admin=caller.is_admin
    )


# ------------------------------------------------------------------ view PDF


@builder("view_pdf")
async def _build_view_pdf(session: Any, job: Any, params: dict[str, Any]) -> Built:
    content, ctx, _resolved = await _view_pdf_bytes(session, job, params, params["view"])
    period = ctx.period.as_dict()
    return Built(content, f"insights-{params['view']}-{period['from']}-{period['to']}.pdf", None, False)


# ------------------------------------------------------------------ finance pack


def _sheet_rows(metric: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    from core.insights.export import _cell, export_rows, guard_cell

    rows = export_rows(metric)
    header: list[str] = []
    for row in rows:
        for name in row:
            if name not in header and name != "links":
                header.append(name)
    return header, [[guard_cell(_cell(row.get(name))) for name in header] for row in rows]


@builder("finance_pack")
async def _build_finance_pack(session: Any, job: Any, params: dict[str, Any]) -> Built:
    from openpyxl import Workbook

    from core.insights import value
    from core.insights.export import definition_rows, guard_cell

    caller = caller_from_snapshot(params["caller"])
    pdf_bytes, ctx, resolved = await _view_pdf_bytes(session, job, params, "finance")
    workbook = Workbook(write_only=True)
    summary = workbook.create_sheet("Summary")
    summary.append(["metric", "title", "value", "unit", "state", "compare_value", "data_quality", "hint"])
    for key, m in resolved.items():
        summary.append(
            [
                key,
                guard_cell(m.get("title")),
                m.get("value"),
                m.get("unit"),
                m.get("state"),
                m.get("compare_value"),
                m.get("data_quality"),
                guard_cell(m.get("hint")),
            ]
        )
    for sheet_name, key in (
        ("Spend by day", "cost.spend"),
        ("Spend by agent", "cost.by_agent"),
        ("Spend by model", "cost.by_model"),
        ("Budgets", "cost.budgets"),
        ("Tokens", "tokens.by_unit"),
    ):
        sheet = workbook.create_sheet(sheet_name)
        header, rows = _sheet_rows(resolved.get(key) or {})
        if not header:
            hint = (resolved.get(key) or {}).get("hint") or "nothing recorded in this period"
            sheet.append([guard_cell(f"No data: {hint}")])
            continue
        sheet.append(header)
        for row in rows:
            sheet.append(row)
    estimate = await value.estimate_for(ctx, ctx.period)
    saved_rows, _saved_masked = await _mask(session, job, value.lines_as_rows(estimate), caller)
    sheet = workbook.create_sheet("Money saved")
    columns = [c for c in (saved_rows[0].keys() if saved_rows else []) if c != "links"]
    sheet.append(columns or ["No completed tasks in this period"])
    for row in saved_rows:
        sheet.append([guard_cell(row.get(c)) for c in columns])
    assumptions = await value.load_assumptions(session, job.tenant_id, ctx.reporting.currency)
    sheet = workbook.create_sheet("Assumptions")
    sheet.append(
        ["kind", "agent_type", "task_type", "role", "minutes_or_hourly_cost", "currency", "source", "confirmed"]
    )
    for b in assumptions.baselines:
        sheet.append(
            [
                "baseline",
                b.agent_type,
                b.task_type,
                b.role,
                float(b.baseline_minutes),
                None,
                b.source,
                b.confirmed_at is not None,
            ]
        )
    for role, rate in sorted(assumptions.rates.items()):
        sheet.append(
            [
                "rate",
                None,
                None,
                role,
                float(rate.hourly_cost),
                rate.currency,
                guard_cell(rate.citation or rate.source),
                rate.confirmed_at is not None,
            ]
        )
    period = ctx.period.as_dict()
    sheet = workbook.create_sheet("Filters")
    sheet.append(["filter", "value"])
    for name, val in (("from", period["from"]), ("to", period["to"]), ("company_id", params.get("company_id"))):
        sheet.append([name, val])
    sheet = workbook.create_sheet("Definitions")
    sheet.append(["metric", "field", "value"])
    for key, m in resolved.items():
        for name, val in definition_rows(m):
            sheet.append([key, name, guard_cell(_text(val))])
    buffer = io.BytesIO()
    workbook.save(buffer)
    files = {"finance.xlsx": buffer.getvalue(), "finance.pdf": pdf_bytes}
    meta = {
        "kind": "finance_pack",
        "tenant_id": str(job.tenant_id),
        "period": period,
        "generated_at": datetime.now(UTC),
    }
    files["manifest.json"] = json.dumps(manifest(files, meta), indent=2, default=str).encode("utf-8")
    return Built(
        _zip(files),
        f"insights-finance-pack-{period['from']}-{period['to']}.zip",
        None,
        False,
        meta={"manifest_sha256": hashlib.sha256(files["manifest.json"]).hexdigest()},
    )


# ------------------------------------------------------------------ partner client pack


def _slug(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned[:40] or "client"


@builder("partner_pack")
async def _build_partner_pack(session: Any, job: Any, params: dict[str, Any]) -> Built:
    """One PDF per client company and ``partner.xlsx`` (clients, deadlines, filings, billing) with a manifest."""
    from datetime import timedelta

    from openpyxl import Workbook
    from sqlalchemy import select

    from core.insights import pdf
    from core.insights.export import definition_rows, guard_cell
    from core.models.ca_client_billing import CAClientInvoice as I
    from core.models.compliance_deadline import ComplianceDeadline as D

    ctx, resolved = await _resolve_view(session, job, params, "partner")
    clients = (resolved.get("partner.clients") or {}).get("rows") or []
    if not clients:
        raise ExportJobError("no_clients", "no client company is recorded")
    if len(clients) > MAX_PACK_CLIENTS:
        raise ExportJobError("too_many_clients", f"at most {MAX_PACK_CLIENTS} client companies in one pack")
    organisation = await _organisation(session, job.tenant_id)
    period = ctx.period.as_dict()
    period_text = f"{period['from']} to {period['to']}"
    today = ctx.today
    deadlines = (
        await session.execute(
            select(D.company_id, D.deadline_type, D.filing_period, D.due_date)
            .where(
                D.tenant_id == job.tenant_id,
                D.filed.is_(False),
                D.due_date <= today + timedelta(days=30),
            )
            .order_by(D.due_date)
        )
    ).all()
    invoices = (
        await session.execute(
            select(I.company_id, I.invoice_number, I.issue_date, I.total, I.balance_due, I.status, I.currency)
            .where(
                I.tenant_id == job.tenant_id,
                I.issue_date >= ctx.period.start,
                I.issue_date <= ctx.period.end,
                I.status.notin_(("void", "cancelled", "draft")),
            )
            .order_by(I.issue_date)
        )
    ).all()
    by_company_deadlines: dict[str, list[list[str]]] = {}
    for company_id, kind, filing_period, due in deadlines:
        state = "overdue" if due < today else "due"
        by_company_deadlines.setdefault(str(company_id), []).append([kind, filing_period, due.isoformat(), state])
    by_company_invoices: dict[str, list[list[str]]] = {}
    for company_id, number, issued, total, balance, status, currency in invoices:
        by_company_invoices.setdefault(str(company_id), []).append(
            [
                number,
                issued.isoformat(),
                pdf.format_value(float(total or 0), "inr" if currency == "INR" else "usd"),
                pdf.format_value(float(balance or 0), "inr" if currency == "INR" else "usd"),
                status,
            ]
        )
    computed = datetime.now(UTC)
    files: dict[str, bytes] = {}
    used: set[str] = set()
    for client in clients:
        company_id = str(client.get("company_id"))
        name = f"clients/{_slug(str(client.get('label')))}-{company_id[:8]}.pdf"
        while name in used:
            name = name.replace(".pdf", "-1.pdf")
        used.add(name)
        rate = client.get("success_rate")
        files[name] = pdf.client_pdf(
            client=str(client.get("label")),
            organisation=organisation,
            period=period_text,
            facts=[
                ("Active", "yes" if client.get("active") else "no"),
                ("Health (0 to 100)", str(client.get("health"))),
                ("Agent runs in the period", str(client.get("runs"))),
                ("Run success", "-" if rate is None else f"{rate}%"),
                ("Overdue deadlines", str(client.get("overdue_deadlines"))),
                ("Filings waiting for approval", str(client.get("pending_filings"))),
                ("Balance outstanding", pdf.format_value(client.get("outstanding"), "inr")),
            ],
            deadlines=by_company_deadlines.get(company_id, []),
            invoices=by_company_invoices.get(company_id, []),
            computed_at=computed,
        )
    workbook = Workbook(write_only=True)
    for sheet_name, key in (
        ("Clients", "partner.clients"),
        ("Deadlines", "backoffice.deadlines"),
        ("Filings", "partner.filings"),
        ("Billing", "partner.billing"),
    ):
        sheet = workbook.create_sheet(sheet_name)
        header, rows = _sheet_rows(resolved.get(key) or {})
        if not header:
            hint = (resolved.get(key) or {}).get("hint") or "nothing recorded"
            sheet.append([guard_cell(f"No data: {hint}")])
            continue
        sheet.append(header)
        for row in rows:
            sheet.append(row)
    sheet = workbook.create_sheet("Filters")
    sheet.append(["filter", "value"])
    for name, val in (("from", period["from"]), ("to", period["to"]), ("company_id", params.get("company_id"))):
        sheet.append([name, val])
    sheet = workbook.create_sheet("Definitions")
    sheet.append(["metric", "field", "value"])
    for key, m in resolved.items():
        for name, val in definition_rows(m):
            sheet.append([key, name, guard_cell(_text(val))])
    buffer = io.BytesIO()
    workbook.save(buffer)
    files["partner.xlsx"] = buffer.getvalue()
    meta = {
        "kind": "partner_pack",
        "tenant_id": str(job.tenant_id),
        "period": period,
        "clients": len(clients),
        "generated_at": computed,
    }
    files["manifest.json"] = json.dumps(manifest(files, meta), indent=2, default=str).encode("utf-8")
    return Built(
        _zip(files),
        f"insights-partner-pack-{period['from']}-{period['to']}.zip",
        len(clients),
        False,
        meta={"manifest_sha256": hashlib.sha256(files["manifest.json"]).hexdigest()},
    )


# ------------------------------------------------------------------ evidence pack


async def _period_bounds(session: Any, tenant_id: uuid.UUID, params: dict[str, Any]) -> tuple[datetime, datetime]:
    from core.insights.metrics import day_bounds
    from core.insights.reporting_settings import load_reporting_settings

    zone = (await load_reporting_settings(session, tenant_id)).zone()
    start, end = date.fromisoformat(params["from"]), date.fromisoformat(params["to"])
    return day_bounds(start, zone)[0], day_bounds(end, zone)[1]


async def _names(session: Any, tenant_id: uuid.UUID, ids: Iterable[Any]) -> dict[str, str]:
    from sqlalchemy import select

    from core.models.user import User

    wanted = set()
    for raw in ids:
        try:
            wanted.add(uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    if not wanted:
        return {}
    rows = await session.execute(select(User.id, User.name).where(User.tenant_id == tenant_id, User.id.in_(wanted)))
    return {str(user_id): (name or "User") for user_id, name in rows}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def _verification(session: Any, tid: uuid.UUID, audit_rows: list[Any], checks: list[tuple[bool, bool]]) -> dict:
    from sqlalchemy import select

    from core.config import settings
    from core.governance import audit_chain
    from core.governance.audit_witness import list_checkpoints
    from core.models.audit import AuditChainAnchor

    signed = sum(1 for s, _ok in checks if s)
    verifies = sum(1 for s, ok in checks if s and ok)
    seqs = [row.chain_seq for row in audit_rows if row.chain_seq is not None]
    chain_on = bool(settings.audit_chain_enabled or settings.audit_chain_default_on) and bool(seqs)
    chain: dict[str, Any]
    if chain_on:
        first, last = min(seqs), max(seqs)
        start = max(1, first - 1)  # the link before the period, so its first row's link is checked too
        checked = await audit_chain.verify(tid, from_seq=start, limit=last - start + 1)
        chain = {"enabled": True, "range": {"from_seq": first, "to_seq": last}, **checked.to_dict()}
    else:
        chain = {
            "enabled": False,
            "note": "No row of the period is sealed into the audit chain; each row's signature is checked.",
        }
    anchor = (
        await session.execute(select(AuditChainAnchor).where(AuditChainAnchor.tenant_id == tid))
    ).scalar_one_or_none()
    witnesses = [
        {
            "head_seq": w.head_seq,
            "head_hash": w.head_hash,
            "status": w.status,
            "witness_id": w.witness_id,
            "witnessed_at": _iso(w.witnessed_at),
        }
        for w in await list_checkpoints(session, tid, limit=20)
    ]
    broken = (signed and verifies < signed) or chain.get("status") == "broken"
    return {
        "status": "broken" if broken else ("verified" if chain_on else "signatures_verified"),
        "rows": {
            "total": len(audit_rows),
            "signed": signed,
            "signature_verifies": verifies,
            "unsigned": len(audit_rows) - signed,
        },
        "chain": chain,
        "anchor": {"seq": anchor.head_seq, "hash": anchor.head_hash, "sealed_at": _iso(anchor.sealed_at)}
        if anchor
        else None,
        "witness_checkpoints": witnesses,
        "algorithm": "HMAC-SHA256 over each row's canonical signed columns with the platform audit key",
    }


@builder("evidence_pack")
async def _build_evidence_pack(session: Any, job: Any, params: dict[str, Any]) -> Built:
    from sqlalchemy import func, or_, select

    from core.insights import pdf
    from core.insights.metrics_governance import CHANGE_LABELS, _change_kind
    from core.models.audit import AuditLog
    from core.models.delegation import UserDelegation
    from core.models.dsar import DSARRequestRecord
    from core.models.hitl import HITLQueue
    from core.models.prompt_template import PromptEditHistory as AgentPromptEdit
    from core.models.provider_attestation import ProviderAttestation
    from core.tool_gateway.audit_logger import verify_audit_row

    caller = caller_from_snapshot(params["caller"])
    tid = job.tenant_id
    lo, hi = await _period_bounds(session, tid, params)
    in_period = (AuditLog.tenant_id == tid, AuditLog.created_at >= lo, AuditLog.created_at < hi)
    total = int((await session.execute(select(func.count()).where(*in_period))).scalar_one() or 0)
    if total > MAX_AUDIT_ROWS:
        raise ExportJobError(
            "export_too_large", f"{total:,} audit rows; at most {MAX_AUDIT_ROWS:,} in one pack. Use a shorter period."
        )
    audit_rows = list(
        (await session.execute(select(AuditLog).where(*in_period).order_by(AuditLog.created_at, AuditLog.id))).scalars()
    )
    checks = [(bool(row.signature), verify_audit_row(row) if row.signature else False) for row in audit_rows]
    audit_out = [
        {
            "id": str(row.id),
            "created_at": row.created_at.isoformat(),
            "event_type": row.event_type,
            "actor_type": row.actor_type,
            "actor_id": row.actor_id,
            "agent_id": str(row.agent_id) if row.agent_id else None,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "action": row.action,
            "outcome": row.outcome,
            "details": row.details or {},
            "chain_seq": row.chain_seq,
            "chain_hash": row.chain_hash,
            "signed": signed,
            "signature_verifies": ok,
        }
        for row, (signed, ok) in zip(audit_rows, checks, strict=True)
    ]
    audit_out, masked = await _mask(session, job, audit_out, caller)

    items = list(
        (
            await session.execute(
                select(HITLQueue)
                .where(HITLQueue.tenant_id == tid, HITLQueue.decision_at >= lo, HITLQueue.decision_at < hi)
                .order_by(HITLQueue.decision_at)
            )
        ).scalars()
    )
    decided: dict[str, dict[str, Any]] = {}
    if items:
        for resource_id, details in await session.execute(
            select(AuditLog.resource_id, AuditLog.details).where(
                AuditLog.tenant_id == tid,
                AuditLog.event_type == "hitl.decided",
                AuditLog.resource_type == "hitl_item",
                AuditLog.resource_id.in_([str(i.id) for i in items]),
            )
        ):
            decided[str(resource_id)] = details or {}
    delegations = list(
        (
            await session.execute(
                select(UserDelegation).where(
                    UserDelegation.tenant_id == tid,
                    UserDelegation.starts_at < hi,
                    or_(UserDelegation.ends_at.is_(None), UserDelegation.ends_at >= lo),
                )
            )
        ).scalars()
    )
    edits = list(
        (
            await session.execute(
                select(AgentPromptEdit).where(
                    AgentPromptEdit.tenant_id == tid, AgentPromptEdit.created_at >= lo, AgentPromptEdit.created_at < hi
                )
            )
        ).scalars()
    )

    def votes_of(item: Any) -> list[dict[str, Any]]:
        raw = ((item.context or {}).get("policy_state") or {}).get("approvals") or []
        return [v for v in raw if isinstance(v, dict)]

    people = await _names(
        session,
        tid,
        [
            *(i.decision_by for i in items),
            *(d.get("delegated_from") for d in decided.values()),
            *(v.get("user_id") for i in items for v in votes_of(i)),
            *(d.delegator_id for d in delegations),
            *(d.delegate_id for d in delegations),
            *(e.edited_by for e in edits),
        ],
    )

    def person(raw: Any) -> str | None:
        return people.get(str(raw), "User") if raw else None

    approvals_out = []
    for item in items:
        start = item.first_viewed_at or item.created_at
        approvals_out.append(
            {
                "id": str(item.id),
                "title": item.title,
                "trigger_type": item.trigger_type,
                "assignee_role": item.assignee_role,
                "status": item.status,
                "decision": item.decision,
                "decided_by": person(item.decision_by),
                "delegated_from": person(decided.get(str(item.id), {}).get("delegated_from")),
                "votes": [
                    {"by": person(v.get("user_id")), "decision": v.get("decision"), "step": v.get("sequence")}
                    for v in votes_of(item)
                ],
                "created_at": _iso(item.created_at),
                "first_viewed_at": _iso(item.first_viewed_at),
                "decision_at": _iso(item.decision_at),
                "seconds_to_decide": round((item.decision_at - start).total_seconds(), 1)
                if start and item.decision_at
                else None,
            }
        )
    approvals_out, _m = await _mask(session, job, approvals_out, caller)
    delegations_out = [
        {
            "id": str(d.id),
            "delegator": person(d.delegator_id),
            "delegate": person(d.delegate_id),
            "reason": d.reason,
            "starts_at": _iso(d.starts_at),
            "ends_at": _iso(d.ends_at),
            "revoked_at": _iso(d.revoked_at),
        }
        for d in delegations
    ]
    dsar_rows = (
        await session.execute(
            select(DSARRequestRecord).where(
                DSARRequestRecord.tenant_id == tid,
                or_(
                    DSARRequestRecord.created_at.between(lo, hi),
                    DSARRequestRecord.completed_at.between(lo, hi),
                    DSARRequestRecord.status.in_(("received", "processing")),
                ),
            )
        )
    ).scalars()
    dsar_out = [
        {
            "id": str(r.id),
            "request_type": r.request_type,
            "status": r.status,
            "created_at": _iso(r.created_at),
            "completed_at": _iso(r.completed_at),
            "hours_to_complete": round((r.completed_at - r.created_at).total_seconds() / 3600, 1)
            if r.completed_at and r.created_at
            else None,
        }
        for r in dsar_rows
    ]
    attestation_rows = (
        await session.execute(select(ProviderAttestation).where(ProviderAttestation.tenant_id == tid))
    ).scalars()
    attestations_out = [
        {
            "provider": a.provider,
            "data_region": a.data_region,
            "in_region": a.in_region,
            "no_training": a.no_training,
            "evidence_ref": a.evidence_ref,
            "attested_by": a.attested_by,
            "attested_at": _iso(a.attested_at),
            "expires_at": _iso(a.expires_at),
            "revoked_at": _iso(a.revoked_at),
        }
        for a in attestation_rows
    ]
    attestations_out, _m = await _mask(session, job, attestations_out, caller)
    changes = [
        {
            "at": row["created_at"],
            "kind": _change_kind(row["event_type"]),
            "event_type": row["event_type"],
            "actor": row["actor_id"],
            "resource": f"{row['resource_type'] or ''}:{row['resource_id'] or ''}".strip(":"),
            "audit_row": row["id"],
        }
        for row in audit_out
        if _change_kind(row["event_type"])
    ]
    changes += [
        {
            "at": e.created_at.isoformat(),
            "kind": "prompts",
            "event_type": "prompt.edit",
            "actor": person(e.edited_by),
            "resource": f"agent:{e.agent_id}",
            "audit_row": None,
        }
        for e in edits
    ]
    changes += [
        {
            "at": d["revoked_at"] or d["starts_at"] or "",
            "kind": "delegations",
            "event_type": "delegation.revoked" if d["revoked_at"] else "delegation.created",
            "actor": d["delegator"],
            "resource": f"delegation:{d['id']}",
            "audit_row": None,
        }
        for d in delegations_out
    ]
    changes.sort(key=lambda c: str(c["at"]))

    verification = await _verification(session, tid, audit_rows, checks)
    verification["period"] = {
        "from": params["from"],
        "to": params["to"],
        "from_utc": lo.isoformat(),
        "to_utc": hi.isoformat(),
    }
    verification["failed_rows"] = [r["id"] for r in audit_out if r["signed"] and not r["signature_verifies"]][:1000]
    verification["pii"] = "masked" if masked else "raw"
    status = verification["status"]
    rows = verification["rows"]
    chain = verification["chain"]
    organisation = await _organisation(session, tid)
    chain_line = (
        f"Audit chain re-verified from sequence {chain.get('checked_from')} to {chain.get('checked_to')}: "
        f"{chain.get('status')}."
        if chain.get("enabled")
        else str(chain.get("note"))
    )
    summary = pdf.summary_pdf(
        title="Insights - Period evidence pack",
        organisation=organisation,
        period=f"{params['from']} to {params['to']}",
        sections=[
            (
                "Verification",
                [
                    f"Status: {status}.",
                    f"Audit rows in the period: {rows['total']:,}; signed {rows['signed']:,}; signature verifies "
                    f"{rows['signature_verifies']:,}; unsigned {rows['unsigned']:,}.",
                    chain_line,
                    f"Witness checkpoints recorded: {len(verification['witness_checkpoints'])}.",
                ],
            ),
            (
                "What is in this pack",
                [
                    f"audit_log.csv: {len(audit_out):,} rows with chain position and signature check.",
                    f"approvals.csv: {len(approvals_out):,} decisions with delegation, votes and time to decide.",
                    f"delegations.csv: {len(delegations_out):,}. dsar.csv: {len(dsar_out):,} (no subject). "
                    f"residency_attestations.csv: {len(attestations_out):,}. change_log.csv: {len(changes):,}.",
                    "verification.json, and manifest.json with each file's SHA-256 and an HMAC of the manifest.",
                    f"Personal data: {'masked by the organisation setting' if masked else 'raw, as asked for'}.",
                    "Kinds of change: " + ", ".join(CHANGE_LABELS) + ".",
                ],
            ),
        ],
        computed_at=datetime.now(UTC),
    )

    def table(rows_: list[dict[str, Any]], fallback: list[str]) -> bytes:
        return _csv(list(rows_[0].keys()) if rows_ else fallback, rows_)

    files = {
        "summary.pdf": summary,
        "audit_log.csv": table(audit_out, ["id"]),
        "approvals.csv": table(approvals_out, ["id"]),
        "delegations.csv": table(delegations_out, ["id"]),
        "dsar.csv": table(dsar_out, ["id"]),
        "residency_attestations.csv": table(attestations_out, ["provider"]),
        "change_log.csv": _csv(["at", "kind", "event_type", "actor", "resource", "audit_row"], changes),
        "verification.json": json.dumps(verification, indent=2, default=str).encode("utf-8"),
    }
    meta = {
        "kind": "evidence_pack",
        "tenant_id": str(tid),
        "organisation": organisation,
        "period": {"from": params["from"], "to": params["to"]},
        "generated_at": datetime.now(UTC),
        "verification_status": status,
    }
    files["manifest.json"] = json.dumps(manifest(files, meta), indent=2, default=str).encode("utf-8")
    return Built(
        _zip(files),
        f"insights-evidence-pack-{params['from']}-{params['to']}.zip",
        len(audit_rows),
        masked,
        meta={"manifest_sha256": hashlib.sha256(files["manifest.json"]).hexdigest(), "verification_status": status},
    )


# ------------------------------------------------------------------ sign-off


class SignOffError(ExportJobError):
    """A sign-off the pack refuses (who, which pack, its state, or the statement)."""


def sign(job: Any, caller: Any, *, name: str, statement: str, now: datetime) -> dict[str, Any]:
    """The sign-off an administrator or auditor records on a ready evidence pack (once)."""
    role = (caller.role or "").lower()
    if not (caller.is_admin or role in EVIDENCE_ROLES):
        raise SignOffError("insights_signoff_forbidden", "only an auditor or administrator signs a pack", 403)
    if caller.is_machine or caller.user_id is None:
        raise SignOffError("insights_signoff_needs_a_person", "a person signs a pack", 403)
    if job.kind != "evidence_pack":
        raise SignOffError("not_an_evidence_pack", "only an evidence pack is signed")
    if job.status != "ready":
        raise SignOffError("export_not_ready", f"the pack is {job.status}", 409)
    if job.signed_at is not None:
        raise SignOffError("already_signed", "this pack is already signed", 409)
    text = " ".join(str(statement or "").split())
    if not text or len(text) > SIGN_STATEMENT_MAX:
        raise SignOffError("invalid_statement", f"a statement of 1 to {SIGN_STATEMENT_MAX} characters")
    params = dict(job.params or {})
    return {
        "by": name or "User",
        "role": "admin" if caller.is_admin else role,
        "user_id": str(caller.user_id),
        "statement": text,
        "signed_at": now.isoformat(),
        "pack_sha256": job.sha256,
        "manifest_sha256": params.get("manifest_sha256"),
        "verification_status": params.get("verification_status"),
        "period": {"from": params.get("from"), "to": params.get("to")},
    }
