# SPDX-License-Identifier: Apache-2.0
"""Content services: the catalogue, drafting, summarisation, extraction, the drafts queue and dataset install."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, get_user_domains, require_tenant_admin
from api.route_metadata import route_meta
from core.content import drafting, drafts, extraction, services, summarisation

logger = structlog.get_logger()
router = APIRouter(prefix="/content", tags=["Content"])

_SERVICES = (drafting.SERVICE, summarisation.SERVICE, extraction.SERVICE)  # imported so they register


class DecisionIn(BaseModel):
    model_config = {"extra": "forbid"}

    decision: str = Field(..., pattern="^(approve|reject)$")
    notes: str = Field("", max_length=2000)


def _refused(exc: services.ContentError) -> HTTPException:
    detail: dict[str, Any] = {"error": exc.code, "message": exc.message}
    if exc.details is not None:
        detail["details"] = exc.details
    return HTTPException(exc.status, detail=detail)


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "content_services_disabled",
            "message": "Content services are off for this deployment (AGENTICORG_CONTENT_SERVICES_ENABLED).",
        },
    )


def _user_id(request: Request) -> str:
    claims = getattr(request.state, "claims", None) or {}
    return str(claims.get("agenticorg:user_id") or claims.get("sub") or getattr(request.state, "user_sub", "") or "")


async def _run(service: services.Service, payload: Any, tenant_id: str, domains: list[str] | None) -> services.Run:
    if not services.enabled():
        raise _off()
    try:
        return await services.run(service, uuid.UUID(tenant_id), payload, domains=domains)
    except services.ContentError as exc:
        raise _refused(exc) from None


@router.get("/services")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="content.services.list",
)
async def list_services(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Every content service with its input and output schemas, guardrail profile and evaluation dataset."""
    return {"enabled": services.enabled(), "services": services.catalogue()}


@router.post("/services/{name}/dataset", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.datasets.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-create-by-name",
    audit_event="content.services.dataset.install",
)
async def install_service_dataset(name: str, request: Request, tenant_id: str = Depends(get_current_tenant)) -> dict:
    """Install a service's evaluation dataset for the tenant; already installed is reported, not duplicated."""
    from core.database import get_tenant_session
    from core.evals import datasets

    if not services.enabled():
        raise _off()
    try:
        service = services.get(name)
    except services.ContentError as exc:
        raise _refused(exc) from None
    tid = uuid.UUID(tenant_id)
    actor: uuid.UUID | None = None
    try:
        actor = uuid.UUID(_user_id(request))
    except ValueError:
        actor = None
    try:
        async with get_tenant_session(tid) as session:
            dataset, version = await datasets.create(
                session,
                tid,
                name=service.dataset_name,
                description=f"Evaluation cases shipped with the {service.title.lower()} content service.",
                cases=service.dataset_cases,
                note="installed from the content service",
                actor=actor,
            )
            answer = {
                "installed": True,
                "dataset": datasets.dataset_dict(dataset),
                "version": datasets.version_dict(version),
            }
    except datasets.DatasetError as exc:
        if exc.code == "name_taken":
            return {"installed": False, "reason": "exists", "dataset_name": service.dataset_name}
        raise HTTPException(exc.status, detail={"error": exc.code, "message": exc.message}) from None
    return answer


@router.post("/draft")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.draft.sensitive.write",
    rate_limit="chat-query",
    idempotency="not-idempotent-create",
    audit_event="content.draft",
)
async def post_draft(
    body: drafting.DraftIn,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    domains: list[str] | None = Depends(get_user_domains),
) -> dict[str, Any]:
    """Draft a document from points and approved sources; notices and circulars wait in the drafts queue."""
    run = await _run(drafting.SERVICE, body, tenant_id, domains)
    needs = drafting.requires_approval(body)
    try:
        draft = await drafts.record(
            uuid.UUID(tenant_id),
            user_id=_user_id(request),
            run=run,
            payload=body.model_dump(),
            kind=body.kind,
            title=str(run.output.get("title") or body.subject),
            requires_approval=needs,
        )
    except (RuntimeError, OSError, ValueError, TypeError) as exc:
        logger.warning("content_draft_record_failed", error_type=type(exc).__name__)
        raise HTTPException(
            503, detail={"error": "draft_not_recorded", "message": "The draft could not be kept"}
        ) from None
    return {**run.to_dict(), "draft": {k: v for k, v in draft.items() if k not in ("output", "input")}}


@router.post("/summarise")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.summarise.sensitive.write",
    rate_limit="chat-query",
    idempotency="idempotent-pure",
    audit_event="content.summarise",
)
async def post_summarise(
    body: summarisation.SummariseIn,
    tenant_id: str = Depends(get_current_tenant),
    domains: list[str] | None = Depends(get_user_domains),
) -> dict[str, Any]:
    """A structured summary across documents, every key point citing its documents."""
    return (await _run(summarisation.SERVICE, body, tenant_id, domains)).to_dict()


@router.post("/extract")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.extract.sensitive.write",
    rate_limit="chat-query",
    idempotency="idempotent-pure",
    audit_event="content.extract",
)
async def post_extract(
    body: extraction.ExtractIn,
    tenant_id: str = Depends(get_current_tenant),
    domains: list[str] | None = Depends(get_user_domains),
) -> dict[str, Any]:
    """Obligations and deadlines, each quoting the text it comes from."""
    return (await _run(extraction.SERVICE, body, tenant_id, domains)).to_dict()


@router.get("/drafts")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.drafts.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="content.drafts.list",
)
async def list_drafts(
    status: Annotated[str | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The tenant's drafts, newest first, by status."""
    if not services.enabled():
        raise _off()
    if status is not None and status not in drafts.STATUSES:
        raise HTTPException(422, detail={"error": "status_unknown", "message": f"status is one of {drafts.STATUSES}"})
    rows = await drafts.list_drafts(uuid.UUID(tenant_id), status=status, limit=limit)
    return {"drafts": rows, "total": len(rows)}


@router.get("/drafts/{draft_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.drafts.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="content.drafts.read",
)
async def get_draft(draft_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """One draft with its output, input, sources and guardrail outcomes."""
    if not services.enabled():
        raise _off()
    row = await drafts.get_draft(uuid.UUID(tenant_id), draft_id)
    if row is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "No such draft"})
    return row


@router.post("/drafts/{draft_id}/decide", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.drafts.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-lifecycle-state",
    audit_event="content.drafts.decide",
)
async def decide_draft(
    draft_id: uuid.UUID, body: DecisionIn, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Approve or reject a waiting draft; its author may not decide it."""
    if not services.enabled():
        raise _off()
    try:
        return await drafts.decide(
            uuid.UUID(tenant_id), draft_id, user_id=_user_id(request), decision=body.decision, notes=body.notes
        )
    except services.ContentError as exc:
        raise _refused(exc) from None
