# SPDX-License-Identifier: Apache-2.0
"""Document review: processed documents, their pages as images for the overlay, corrections and decisions."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from core.idp import pipeline, store
from core.idp.pages import DocumentError

router = APIRouter(prefix="/idp/documents", tags=["Documents"])


class CorrectionIn(BaseModel):
    model_config = {"extra": "forbid"}

    document_index: int = Field(0, ge=0, le=500)
    field: str = Field(..., min_length=1, max_length=64)
    value: str = Field("", max_length=500)


class DecisionIn(BaseModel):
    model_config = {"extra": "forbid"}

    decision: str = Field(..., pattern="^(approve|reject)$")
    notes: str = Field("", max_length=2000)


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "idp_disabled",
            "message": "Document processing is off for this deployment (AGENTICORG_IDP_ENABLED).",
        },
    )


def _refused(exc: DocumentError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


def _user_id(request: Request) -> str:
    claims = getattr(request.state, "claims", None) or {}
    return str(claims.get("agenticorg:user_id") or claims.get("sub") or getattr(request.state, "user_sub", "") or "")


@router.get("")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="documents.review.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="idp.documents.list",
)
async def list_documents(
    status: Annotated[str | None, Query(max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The kept documents, newest first, by status (review is what a person must look at)."""
    if not pipeline.enabled():
        raise _off()
    if status is not None and status not in store.STATUSES:
        raise HTTPException(422, detail={"error": "status_unknown", "message": f"status is one of {store.STATUSES}"})
    rows = await store.list_documents(uuid.UUID(tenant_id), status=status, limit=limit)
    return {"documents": rows, "total": len(rows)}


@router.get("/{document_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="documents.review.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="idp.documents.read",
)
async def get_document(document_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """One kept file: its pages, segments, documents with corrected fields, tables, boxes and review reasons."""
    if not pipeline.enabled():
        raise _off()
    row = await store.get_document(uuid.UUID(tenant_id), document_id)
    if row is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "No such document"})
    return row


@router.get("/{document_id}/pages/{page_number}.png")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="documents.review.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="idp.documents.page_image",
)
async def page_image(
    document_id: uuid.UUID, page_number: int, tenant_id: str = Depends(get_current_tenant)
) -> Response:
    """A page rendered as PNG at the store's dpi, for the review overlay."""
    if not pipeline.enabled():
        raise _off()
    try:
        data = await store.page_image(uuid.UUID(tenant_id), document_id, page_number)
    except DocumentError as exc:
        raise _refused(exc) from None
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.post("/{document_id}/fields")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="documents.review.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-full-replace",
    audit_event="idp.documents.correct",
)
async def correct_field(
    document_id: uuid.UUID, body: CorrectionIn, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """A reviewer's value for one field; the extracted value stays beside it."""
    if not pipeline.enabled():
        raise _off()
    try:
        return await store.correct(
            uuid.UUID(tenant_id),
            document_id,
            document_index=body.document_index,
            field=body.field,
            value=body.value,
            user_id=_user_id(request),
        )
    except DocumentError as exc:
        raise _refused(exc) from None


@router.post("/{document_id}/decide")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="documents.review.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-lifecycle-state",
    audit_event="idp.documents.decide",
)
async def decide_document(
    document_id: uuid.UUID, body: DecisionIn, request: Request, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """Approve or reject a document; a decided document is closed to further corrections."""
    if not pipeline.enabled():
        raise _off()
    try:
        return await store.decide(
            uuid.UUID(tenant_id), document_id, decision=body.decision, user_id=_user_id(request), notes=body.notes
        )
    except DocumentError as exc:
        raise _refused(exc) from None
