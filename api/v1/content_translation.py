# SPDX-License-Identifier: Apache-2.0
"""Content services, part 3: document translation, one text or a bounded batch, with checks and back-translation."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, get_user_domains
from api.route_metadata import route_meta
from api.v1.content import _off, _refused, _run
from core.content import services, translation

router = APIRouter(prefix="/content", tags=["Content"])

_SERVICES = (translation.SERVICE,)  # imported so it registers


class BatchItem(BaseModel):
    model_config = {"extra": "forbid"}

    id: str = Field(..., min_length=1, max_length=64)
    text: str = Field(..., min_length=1, max_length=20_000)


class BatchIn(BaseModel):
    model_config = {"extra": "forbid"}

    items: list[BatchItem] = Field(..., min_length=1, max_length=translation.MAX_BATCH)
    target_language: str = Field(..., min_length=2, max_length=5)
    source_language: str = Field("auto", min_length=2, max_length=5)
    glossary: list[translation.GlossaryEntry] = Field(default_factory=list, max_length=50)
    preserve: list[str] = Field(default_factory=list, max_length=50)
    register: str = "formal"
    format: str = "plain"


async def _translate_one(payload: translation.TranslateIn, tenant_id: str, domains: list[str] | None) -> dict[str, Any]:
    run = await _run(translation.SERVICE, payload, tenant_id, domains)
    answer = run.to_dict()
    if payload.verify:
        try:
            answer["verification"] = await translation.back_translate(uuid.UUID(tenant_id), payload, run.output)
        except services.ContentError as exc:
            answer["verification"] = {"error": exc.code, "message": exc.message}
    return answer


@router.get("/languages")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="content.languages.list",
)
async def list_languages(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The languages the translation service supports, with their scripts."""
    return {
        "languages": [
            {"code": code, "name": info["name"], "script": info["script"]}
            for code, info in translation.LANGUAGES.items()
        ],
        "max_batch": translation.MAX_BATCH,
        "enabled": services.enabled(),
    }


@router.post("/translate")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.translate.sensitive.write",
    rate_limit="chat-query",
    idempotency="idempotent-pure",
    audit_event="content.translate",
)
async def post_translate(
    body: translation.TranslateIn,
    tenant_id: str = Depends(get_current_tenant),
    domains: list[str] | None = Depends(get_user_domains),
) -> dict[str, Any]:
    """A translation with its checks (figures, glossary, verbatim terms, script) and an optional back-translation."""
    try:
        translation.check_language(body.target_language)
        translation.check_language(body.source_language, allow_auto=True)
    except services.ContentError as exc:
        raise _refused(exc) from None
    return await _translate_one(body, tenant_id, domains)


@router.post("/translate/batch")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="content.translate.sensitive.write",
    rate_limit="chat-query",
    idempotency="idempotent-pure",
    audit_event="content.translate.batch",
)
async def post_translate_batch(
    body: BatchIn,
    tenant_id: str = Depends(get_current_tenant),
    domains: list[str] | None = Depends(get_user_domains),
) -> dict[str, Any]:
    """Up to twenty texts translated one by one with the same settings; an item's failure is reported in place."""
    if not services.enabled():
        raise _off()
    try:
        translation.check_language(body.target_language)
        translation.check_language(body.source_language, allow_auto=True)
    except services.ContentError as exc:
        raise _refused(exc) from None
    results = []
    for item in body.items:
        payload = translation.TranslateIn(
            text=item.text,
            target_language=body.target_language,
            source_language=body.source_language,
            glossary=body.glossary,
            preserve=body.preserve,
            register=body.register if body.register in ("formal", "neutral") else "formal",
            format=body.format if body.format in ("plain", "markdown") else "plain",
        )
        try:
            answer = await _translate_one(payload, tenant_id, domains)
            results.append({"id": item.id, "ok": True, **answer})
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"error": "failed", "message": str(exc.detail)}
            results.append({"id": item.id, "ok": False, **detail})
    return {"results": results, "total": len(results), "failed": sum(1 for r in results if not r["ok"])}
