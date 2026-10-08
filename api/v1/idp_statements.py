# SPDX-License-Identifier: Apache-2.0
"""Document processing, part 4: bank statement line items and version comparison of kept documents."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from core.idp import compare, pipeline, statements, store

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
        raise HTTPException(404, detail={"error": "not_found", "message": f"No document {document_id}"})
    return detail


@router.get("/{document_id}/statement")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="documents.review.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="idp.documents.statement",
)
async def statement_lines(
    document_id: uuid.UUID,
    document_index: Annotated[int, Query(ge=0, le=500)] = 0,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The transactions of a bank statement in the file, with the running balance checked and a summary."""
    detail = await _detail(tenant_id, document_id)
    document = next((d for d in detail.get("documents", []) if d.get("index") == document_index), None)
    if document is None:
        raise HTTPException(404, detail={"error": "document_index_unknown", "message": f"No document {document_index}"})
    if document.get("document_type") != "bank_statement":
        raise HTTPException(
            422,
            detail={
                "error": "not_a_statement",
                "message": f"Document {document_index} is a {document.get('document_type')}",
            },
        )
    return {"document_id": str(document_id), **statements.analyse(document)}


@router.get("/{document_id}/compare/{other_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="documents.review.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="idp.documents.compare",
)
async def compare_documents(
    document_id: uuid.UUID,
    other_id: uuid.UUID,
    document_index: Annotated[int, Query(ge=0, le=500)] = 0,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Two kept files compared: the fields, page text and tables of one document in each (the earlier one first)."""
    before = await _detail(tenant_id, document_id)
    after = await _detail(tenant_id, other_id)
    return {
        "before": str(document_id),
        "after": str(other_id),
        **compare.compare(before, after, document_index=document_index),
    }
