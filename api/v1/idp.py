# SPDX-License-Identifier: Apache-2.0
"""Intelligent document processing: analyse a PDF or image into typed documents with fields, tables and boxes."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from core.idp import classify, fields, pipeline
from core.idp.pages import MAX_BYTES, MAX_PAGES, DocumentError, ocr_available

logger = structlog.get_logger()
router = APIRouter(prefix="/idp", tags=["Documents"])


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "idp_disabled",
            "message": "Document processing is off for this deployment (AGENTICORG_IDP_ENABLED).",
        },
    )


def _too_large() -> HTTPException:
    return HTTPException(
        413, detail={"error": "too_large", "message": f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB"}
    )


async def _read_bounded(file: UploadFile) -> bytes:
    """The upload's bytes, refused with 413 as soon as it is known to exceed the limit.

    The declared size is checked first; the read itself asks for at most one
    byte more than the limit, so an oversized upload is never held in memory whole.
    """
    declared = getattr(file, "size", None)
    if isinstance(declared, int) and declared > MAX_BYTES:
        raise _too_large()
    stream = await file.read(MAX_BYTES + 1)
    if len(stream) > MAX_BYTES:
        raise _too_large()
    return stream


@router.get("/document-types")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="documents.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="idp.document_types.list",
)
async def document_types(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The document types the classifier knows, the fields each one yields, and whether OCR is installed."""
    return {
        "enabled": pipeline.enabled(),
        "ocr_available": ocr_available(),
        "limits": {"max_pages": MAX_PAGES, "max_bytes": MAX_BYTES},
        "document_types": [
            {
                **item,
                "fields": [
                    {"name": f.name, "required": f.required, "kind": f.kind} for f in fields.SPECS.get(item["name"], ())
                ],
            }
            for item in classify.catalogue()
        ],
        "floors": {"document_type": pipeline.TYPE_FLOOR, "field": pipeline.FIELD_FLOOR},
    }


@router.post("/analyse")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="documents.analyse.sensitive.write",
    rate_limit="chat-query",
    idempotency="idempotent-pure",
    audit_event="idp.analyse",
)
async def analyse(
    file: UploadFile,
    request: Request,
    ocr: bool = True,
    with_words: Annotated[bool, Query()] = False,
    store: Annotated[bool, Query()] = False,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Split a file into documents, type each, extract fields and tables with boxes, and say what needs review."""
    if not pipeline.enabled():
        raise _off()
    stream = await _read_bounded(file)
    try:
        # Rasterising and OCR are blocking and can take minutes on a scanned file: run them off the event loop.
        result = await asyncio.to_thread(
            pipeline.process, stream, file.content_type or "", ocr=ocr, with_words=with_words
        )
    except DocumentError as exc:
        raise HTTPException(exc.status, detail={"error": exc.code, "message": exc.message}) from None
    answer: dict[str, Any] = {"filename": file.filename, **result}
    if store:
        # Kept for review: the file, the result, and who sent it (core/idp/store.py).
        from core.idp import store as review_store

        claims = getattr(request.state, "claims", None) or {}
        user_id = str(claims.get("agenticorg:user_id") or claims.get("sub") or "")
        try:
            kept = await review_store.save(
                uuid.UUID(tenant_id),
                filename=file.filename or "",
                mime_type=file.content_type or "",
                data=stream,
                result=result,
                created_by=user_id or None,
            )
        except DocumentError as exc:
            raise HTTPException(exc.status, detail={"error": exc.code, "message": exc.message}) from None
        answer["document_id"] = kept["id"]
        answer["status"] = kept["status"]
    return answer


@router.post("/classify-text")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="documents.read",
    rate_limit="standard",
    idempotency="idempotent-pure",
    audit_event="idp.classify_text",
)
async def classify_text(body: dict[str, Any], tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The document type of a text (a dry run of the classifier)."""
    if not pipeline.enabled():
        raise _off()
    text = str(body.get("text") or "")
    if not text.strip() or len(text) > 50_000:
        raise HTTPException(
            422, detail={"error": "text_invalid", "message": "text is non-empty and at most 50000 characters"}
        )
    return classify.classify(text).to_dict()
