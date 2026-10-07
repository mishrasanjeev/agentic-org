# SPDX-License-Identifier: Apache-2.0
"""Transaction intelligence: records in, entities aggregated, detectors run, findings under human disposition.

``POST /txn/records`` takes a batch of movements (idempotent under each
record's reference); ``POST /txn/import/document/{id}`` books a kept bank
statement's line items. ``GET /txn/entities`` and
``GET /txn/entities/{kind}/{ref}`` give the entity-centric view.
``POST /txn/detect`` runs the structuring and pass-through detectors over
the recent records and keeps every new finding; a person dispositions a
finding (dismiss with a reason, confirm, escalate to a case). Off, the
status route says so and the rest is not found.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from core.ownership import caller_from_request
from core.txn import aggregate, detectors, findings, records
from core.txn.records import TxnError

router = APIRouter(prefix="/txn", tags=["Transactions"])


class RecordsIn(BaseModel):
    model_config = {"extra": "forbid"}

    records: list[dict[str, Any]] = Field(..., min_length=1, max_length=records.MAX_BATCH)
    source: str | None = Field(None, max_length=64)


class DetectIn(BaseModel):
    model_config = {"extra": "forbid"}

    account: str | None = Field(None, max_length=64)
    since_days: int = Field(90, ge=1, le=730)
    kinds: list[str] | None = Field(None, max_length=4)


class DispositionIn(BaseModel):
    model_config = {"extra": "forbid"}

    outcome: str = Field(..., pattern="^(dismiss|confirm|escalate)$")
    notes: str = Field("", max_length=2000)
    case_ref: str | None = Field(None, max_length=128)


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "txn_disabled",
            "message": "Transaction intelligence is off (AGENTICORG_TRANSACTION_INTELLIGENCE_ENABLED).",
        },
    )


def _refused(exc: TxnError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


def _user_id(request: Request) -> str:
    claims = getattr(request.state, "claims", None) or {}
    return str(claims.get("agenticorg:user_id") or claims.get("sub") or getattr(request.state, "user_sub", "") or "")


@router.get("/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="txn.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="txn.status",
)
async def status(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Whether transaction intelligence is on, the detectors, and the thresholds in force."""
    thresholds = await findings.thresholds_for(uuid.UUID(tenant_id)) if records.enabled() else detectors.Thresholds()
    return {
        "enabled": records.enabled(),
        "detectors": list(detectors.KINDS),
        "entity_kinds": list(aggregate.ENTITY_KINDS),
        "channels": list(records.CHANNELS),
        "thresholds": thresholds.to_dict(),
        "limits": {"batch": records.MAX_BATCH, "records": records.MAX_LIST},
    }


@router.post("/records")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="txn.records.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-by-record-reference",
    audit_event="txn.records.ingest",
)
async def ingest_records(body: RecordsIn, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Keep a batch of movements; a record already kept under its reference is skipped."""
    if not records.enabled():
        raise _off()
    try:
        return await records.ingest(uuid.UUID(tenant_id), body.records, source=body.source)
    except TxnError as exc:
        raise _refused(exc) from None


@router.get("/records")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="txn.records.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="txn.records.list",
)
async def list_records(
    account: Annotated[str | None, Query(max_length=64)] = None,
    customer_ref: Annotated[str | None, Query(max_length=64)] = None,
    counterparty: Annotated[str | None, Query(max_length=64)] = None,
    since_days: Annotated[int, Query(ge=1, le=730)] = 90,
    limit: Annotated[int, Query(ge=1, le=records.MAX_LIST)] = 500,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The kept movements, oldest first, narrowed by account, customer or counterparty."""
    if not records.enabled():
        raise _off()
    rows = await records.list_records(
        uuid.UUID(tenant_id),
        account=account,
        customer_ref=customer_ref,
        counterparty=counterparty,
        since=records.window_start(since_days),
        limit=limit,
    )
    return {"records": rows, "total": len(rows)}


@router.post("/import/document/{document_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="txn.records.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-by-record-reference",
    audit_event="txn.records.import",
)
async def import_document(document_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Book the line items of a kept bank statement as movements on its account."""
    if not records.enabled():
        raise _off()
    try:
        return await records.import_document(uuid.UUID(tenant_id), document_id)
    except TxnError as exc:
        raise _refused(exc) from None


@router.get("/entities")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="txn.records.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="txn.entities.list",
)
async def list_entities(
    kind: Annotated[str | None, Query(max_length=16)] = None,
    q: Annotated[str, Query(max_length=100)] = "",
    since_days: Annotated[int, Query(ge=1, le=730)] = 90,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The accounts, customers and counterparties seen in the recent records, newest activity first."""
    if not records.enabled():
        raise _off()
    if kind is not None and kind not in aggregate.ENTITY_KINDS:
        raise HTTPException(
            422, detail={"error": "kind_unknown", "message": f"kind is one of {', '.join(aggregate.ENTITY_KINDS)}"}
        )
    rows = await records.list_records(
        uuid.UUID(tenant_id), since=records.window_start(since_days), limit=records.MAX_LIST
    )
    found = aggregate.entities(rows, kind=kind, query=q)
    return {"entities": found[:500], "total": len(found)}


@router.get("/entities/{kind}/{ref}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="txn.records.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="txn.entities.read",
)
async def get_entity(
    kind: str,
    ref: str,
    since_days: Annotated[int, Query(ge=1, le=730)] = 365,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """One entity with everything booked against it: totals, channels, branches, counterparties, series, findings."""
    if not records.enabled():
        raise _off()
    try:
        return await findings.entity(uuid.UUID(tenant_id), kind, ref[:64], since_days=since_days)
    except TxnError as exc:
        raise _refused(exc) from None


@router.post("/detect")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="txn.findings.sensitive.write",
    rate_limit="chat-query",
    idempotency="idempotent-by-finding-fingerprint",
    audit_event="txn.detect",
)
async def detect(body: DetectIn | None = None, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Run the detectors over the recent records and keep every new finding; a known finding is not raised twice."""
    if not records.enabled():
        raise _off()
    body = body or DetectIn()
    try:
        return await findings.detect(
            uuid.UUID(tenant_id), account=body.account, since_days=body.since_days, kinds=body.kinds
        )
    except TxnError as exc:
        raise _refused(exc) from None


@router.get("/findings")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="txn.findings.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="txn.findings.list",
)
async def list_findings(
    status: Annotated[str | None, Query(max_length=16)] = None,
    kind: Annotated[str | None, Query(max_length=32)] = None,
    entity_ref: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=findings.MAX_LIST)] = 100,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The findings, newest first, by status, kind or entity."""
    if not records.enabled():
        raise _off()
    if status is not None and status not in findings.STATUSES:
        raise HTTPException(422, detail={"error": "status_unknown", "message": f"status is one of {findings.STATUSES}"})
    rows = await findings.list_findings(
        uuid.UUID(tenant_id), status=status, kind=kind, entity_ref=entity_ref, limit=limit
    )
    return {"findings": rows, "total": len(rows)}


@router.get("/findings/{finding_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="txn.findings.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="txn.findings.read",
)
async def get_finding(finding_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """One finding with the rows that support it."""
    if not records.enabled():
        raise _off()
    found = await findings.get_finding(uuid.UUID(tenant_id), finding_id)
    if found is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "No such finding"})
    return found


@router.post("/findings/{finding_id}/disposition")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="txn.findings.sensitive.write",
    rate_limit="approval-decision",
    idempotency="terminal-state-conflict-prevents-duplicate-decision",
    audit_event="txn.findings.disposition",
)
async def disposition(
    finding_id: uuid.UUID, body: DispositionIn, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """A person's decision on a finding: dismiss with a reason, confirm, or escalate to a governed case."""
    if not records.enabled():
        raise _off()
    caller = caller_from_request(request)
    if caller.is_machine or not _user_id(request):
        # A disposition is a person's decision: an API key or an agent token holding the scope does not take it.
        raise HTTPException(403, detail={"error": "human_required", "message": "A finding is dispositioned by a signed-in person"})
    try:
        return await findings.disposition(
            uuid.UUID(tenant_id),
            finding_id,
            outcome=body.outcome,
            notes=body.notes,
            user_id=_user_id(request) or "unknown",
            case_ref=body.case_ref,
        )
    except TxnError as exc:
        raise _refused(exc) from None
