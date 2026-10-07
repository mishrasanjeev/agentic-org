# SPDX-License-Identifier: Apache-2.0
"""Document analysis: cross-document reconciliation, stamp and seal checks, and the analysis report."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from core.idp import pipeline, reconcile, report, stamps, store
from core.idp.pages import DocumentError

router = APIRouter(prefix="/idp/documents", tags=["Documents"])


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "idp_disabled",
            "message": "Document processing is off for this deployment (AGENTICORG_IDP_ENABLED).",
        },
    )


async def _detail(tenant_id: str, document_id: uuid.UUID) -> dict[str, Any]:
    if not pipeline.enabled():
        raise _off()
    detail = await store.get_document(uuid.UUID(tenant_id), document_id)
    if detail is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "No such document"})
    return detail


async def stamp_check(tenant_id: str, document_id: uuid.UUID, detail: dict[str, Any]) -> dict[str, Any]:
    """Every page rendered and checked for ink regions; the document type on the page says whether one is expected."""
    pages_out = []
    types_by_page: dict[int, str] = {}
    for document in detail.get("documents", []):
        for number in document.get("pages", []):
            types_by_page[int(number)] = str(document.get("document_type") or "")
    for page in detail.get("pages_detail", []):
        number = int(page.get("number", 0))
        try:
            png = await store.page_image(uuid.UUID(tenant_id), document_id, number)
        except DocumentError:
            continue
        boxes = [tuple(line["bbox"]) for line in page.get("lines", []) if line.get("bbox")]
        candidates = stamps.detect_from_png(
            png,
            page_number=number,
            page_size=(float(page.get("width", 1)), float(page.get("height", 1))),
            text_boxes=boxes,
        )
        verdict = stamps.verify(types_by_page.get(number, ""), candidates)
        pages_out.append({"page": number, "document_type": types_by_page.get(number), **verdict})
    return {
        "pages": pages_out,
        "present_on": [p["page"] for p in pages_out if p["status"] == "present"],
        "missing_on": [p["page"] for p in pages_out if p["status"] == "missing"],
    }


@router.get("/{document_id}/reconcile")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="documents.review.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="idp.documents.reconcile",
)
async def reconcile_document(document_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Fields that should agree across the file's documents: agreements, and disagreements with every value and box."""
    detail = await _detail(tenant_id, document_id)
    return {"document_id": str(document_id), **reconcile.reconcile(detail.get("documents", []))}


@router.get("/{document_id}/stamps")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="documents.review.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="idp.documents.stamps",
)
async def stamps_of_document(document_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Ink regions consistent with a stamp or seal on every page, and whether one was expected there."""
    detail = await _detail(tenant_id, document_id)
    return {"document_id": str(document_id), **(await stamp_check(tenant_id, document_id, detail))}


@router.get("/{document_id}/report")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="documents.review.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="idp.documents.report",
)
async def report_of_document(
    document_id: uuid.UUID,
    output: Annotated[str, Query(alias="format")] = "json",
    with_stamps: bool = True,
    tenant_id: str = Depends(get_current_tenant),
) -> Any:
    """The analysis report: documents and key fields, reconciliation, stamps, review reasons and a narrative."""
    detail = await _detail(tenant_id, document_id)
    stamp_result = await stamp_check(tenant_id, document_id, detail) if with_stamps else None
    built = report.build(detail, stamps=stamp_result)
    if output == "markdown":
        return Response(content=report.to_markdown(built), media_type="text/markdown")
    return built
