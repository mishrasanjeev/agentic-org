# SPDX-License-Identifier: Apache-2.0
"""The review store: processed documents kept with their file, corrections by field, page images, decisions.

A document the pipeline routed to review waits in ``review``; a reviewer
corrects fields (each correction keeps who and when, the original value
stays in the result), then approves or rejects. Page images are rendered
from the kept file on request, so the overlay draws boxes on the real page.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from core.idp.pages import MAX_BYTES, DocumentError, open_pdf

logger = structlog.get_logger()

STATUSES = ("processed", "review", "approved", "rejected")
IMAGE_DPI = 110
MAX_IMAGE_SIDE = 2400


def summary_dict(row: Any) -> dict[str, Any]:
    result = dict(row.result or {})
    return {
        "id": str(row.id),
        "filename": row.filename,
        "mime_type": row.mime_type,
        "size_bytes": int(row.size_bytes or 0),
        "status": row.status,
        "pages": int(row.pages or 0),
        "document_types": [d.get("document_type") for d in result.get("documents", [])],
        "review_reasons": list(row.review_reasons or []),
        "corrections": sum(len(v) for v in (row.corrections or {}).values() if isinstance(v, dict)),
        "created_by": row.created_by,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _apply_fixes(rows: list[dict[str, Any]], fixes: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            **f,
            "original_value": f.get("value"),
            "value": fixes[f["name"]]["value"],
            "corrected": True,
            "corrected_by": fixes[f["name"]].get("by"),
        }
        if f.get("name") in fixes
        else {**f, "corrected": False}
        for f in rows
    ]


def effective_documents(result: dict[str, Any], corrections: dict[str, Any]) -> list[dict[str, Any]]:
    """The result's documents with corrections applied to fields and extra fields; the original value stays beside.

    ``correct`` accepts a name from either list, so both lists carry the reviewer's value.
    """
    out = []
    for document in result.get("documents", []):
        fixes = corrections.get(str(document.get("index"))) or {}
        merged = dict(document)
        merged["fields"] = _apply_fixes(document.get("fields", []), fixes)
        merged["extra_fields"] = _apply_fixes(document.get("extra_fields", []), fixes)
        out.append(merged)
    return out


def detail_dict(row: Any) -> dict[str, Any]:
    result = dict(row.result or {})
    return {
        **summary_dict(row),
        "pages_detail": result.get("pages", []),
        "segments": result.get("segments", []),
        "documents": effective_documents(result, dict(row.corrections or {})),
        "review": result.get("review", {}),
        "ocr": result.get("ocr", {}),
        "review_notes": row.review_notes,
        "image_dpi": IMAGE_DPI,
    }


async def save(
    tenant_id: uuid.UUID,
    *,
    filename: str,
    mime_type: str,
    data: bytes,
    result: dict[str, Any],
    created_by: str | None,
) -> dict[str, Any]:
    """Keep the file and the result; a document the pipeline routed to review waits in ``review``."""
    from core.database import get_tenant_session
    from core.models.idp_document import IdpDocument

    if len(data) > MAX_BYTES:
        raise DocumentError(413, "too_large", "The file is too large to keep")
    kept = dict(result)
    kept["pages"] = [{k: v for k, v in p.items() if k != "word_boxes"} for p in result.get("pages", [])]
    needs_review = bool(result.get("review", {}).get("needed"))
    reasons = [r for d in result.get("documents", []) for r in d.get("review", {}).get("reasons", [])]
    row = IdpDocument(
        tenant_id=tenant_id,
        filename=(filename or "")[:255],
        mime_type=(mime_type or "application/pdf")[:100],
        size_bytes=len(data),
        content=data,
        status="review" if needs_review else "processed",
        pages=len(result.get("pages", [])),
        result=kept,
        corrections={},
        review_reasons=reasons[:50],
        created_by=(created_by or "")[:128] or None,
    )
    async with get_tenant_session(tenant_id) as session:
        session.add(row)
        await session.flush()
        answer = summary_dict(row)
    logger.info("idp_document_kept", status=answer["status"], pages=answer["pages"])
    return answer


async def _row(session: Any, tenant_id: uuid.UUID, document_id: uuid.UUID, *, lock: bool = False) -> Any:
    from core.models.idp_document import IdpDocument

    query = select(IdpDocument).where(IdpDocument.tenant_id == tenant_id, IdpDocument.id == document_id)
    if lock:
        query = query.with_for_update()
    return (await session.execute(query)).scalar_one_or_none()


async def list_documents(tenant_id: uuid.UUID, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    from core.database import get_tenant_session
    from core.models.idp_document import IdpDocument

    async with get_tenant_session(tenant_id) as session:
        query = select(IdpDocument).where(IdpDocument.tenant_id == tenant_id)
        if status:
            query = query.where(IdpDocument.status == status)
        rows = (await session.execute(query.order_by(IdpDocument.created_at.desc()).limit(limit))).scalars().all()
    return [summary_dict(row) for row in rows]


async def get_document(tenant_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, Any] | None:
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, document_id)
        return detail_dict(row) if row is not None else None


def pdf_render_scale(width: float, height: float, *, dpi: int = IMAGE_DPI, max_side: int = MAX_IMAGE_SIDE) -> float:
    """The PDFium scale for a ``width`` x ``height`` page: ``dpi``, lowered so neither side exceeds ``max_side``.

    The page size comes from the file, so it is bounded before any raster is
    allocated; a page with no usable size is refused.
    """
    longest = max(float(width), float(height))
    if not longest > 0 or longest == float("inf"):
        raise DocumentError(422, "page_size_invalid", "The page has no usable size")
    return min(dpi / 72.0, max_side / longest)


def render_page(data: bytes, mime_type: str, page_number: int, *, dpi: int = IMAGE_DPI) -> bytes:
    """The page as a PNG, from the PDF at ``dpi`` or the image itself, bounded in size."""
    from PIL import Image

    mime = (mime_type or "").split(";")[0].strip().lower()
    if mime == "application/pdf" or data[:5] == b"%PDF-":
        document = open_pdf(data)
        try:
            if page_number < 1 or page_number > len(document):
                raise DocumentError(404, "page_not_found", f"No page {page_number}")
            raw_page = document[page_number - 1]
            width, height = raw_page.get_size()
            scale = pdf_render_scale(width, height, dpi=dpi)
            rendered = raw_page.render(scale=scale).to_pil()
        finally:
            document.close()
        out = io.BytesIO()
        rendered.save(out, format="PNG")
        return out.getvalue()
    if page_number != 1:
        raise DocumentError(404, "page_not_found", f"No page {page_number}")
    image = Image.open(io.BytesIO(data)).convert("RGB")
    if max(image.size) > MAX_IMAGE_SIDE:
        ratio = MAX_IMAGE_SIDE / max(image.size)
        image = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


async def page_image(tenant_id: uuid.UUID, document_id: uuid.UUID, page_number: int) -> bytes:
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, document_id)
        if row is None:
            raise DocumentError(404, "not_found", "No such document")
        data, mime = bytes(row.content), row.mime_type
    return render_page(data, mime, page_number)


async def correct(
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    document_index: int,
    field: str,
    value: str,
    user_id: str,
) -> dict[str, Any]:
    """A reviewer's value for a field of one document in the file; the original stays in the result."""
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, document_id, lock=True)
        if row is None:
            raise DocumentError(404, "not_found", "No such document")
        if row.status in ("approved", "rejected"):
            raise DocumentError(409, "decided", f"The document is {row.status}; corrections are closed")
        documents = (row.result or {}).get("documents", [])
        target = next((d for d in documents if d.get("index") == document_index), None)
        if target is None:
            raise DocumentError(404, "document_index_unknown", f"No document {document_index} in this file")
        names = {f.get("name") for f in target.get("fields", [])} | {
            f.get("name") for f in target.get("extra_fields", [])
        }
        if field not in names:
            raise DocumentError(404, "field_unknown", f"No field {field!r} in document {document_index}")
        corrections = dict(row.corrections or {})
        bucket = dict(corrections.get(str(document_index)) or {})
        bucket[field] = {"value": str(value)[:500], "by": str(user_id)[:128], "at": datetime.now(UTC).isoformat()}
        corrections[str(document_index)] = bucket
        row.corrections = corrections
        row.updated_at = datetime.now(UTC)
        return detail_dict(row)


async def decide(
    tenant_id: uuid.UUID, document_id: uuid.UUID, *, decision: str, user_id: str, notes: str = ""
) -> dict[str, Any]:
    """Approve or reject a document under review (or a processed one, to sign it off)."""
    from core.database import get_tenant_session

    if decision not in ("approve", "reject"):
        raise DocumentError(422, "decision_unknown", "decision is approve or reject")
    async with get_tenant_session(tenant_id) as session:
        row = await _row(session, tenant_id, document_id, lock=True)
        if row is None:
            raise DocumentError(404, "not_found", "No such document")
        if row.status in ("approved", "rejected"):
            raise DocumentError(409, "decided", f"The document is already {row.status}")
        row.status = "approved" if decision == "approve" else "rejected"
        row.reviewed_by = str(user_id)[:128] or None
        row.reviewed_at = datetime.now(UTC)
        row.review_notes = notes[:2000] or None
        row.updated_at = datetime.now(UTC)
        answer = summary_dict(row)
    logger.info("idp_document_decided", decision=answer["status"])
    return answer
