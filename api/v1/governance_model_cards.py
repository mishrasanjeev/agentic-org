# SPDX-License-Identifier: Apache-2.0
"""Model cards: one standard card per model the tenant uses (core/governance/model_cards.py)."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, get_current_user, require_tenant_admin
from api.route_metadata import route_meta
from api.v1.agents import _user_uuid_from_claims
from core.database import get_tenant_session
from core.governance import model_cards

logger = structlog.get_logger()
router = APIRouter()


class ModelCardIn(BaseModel):
    model_config = {"extra": "forbid"}

    intended_use: str | None = Field(None, max_length=model_cards.MAX_TEXT)
    limitations: str | None = Field(None, max_length=model_cards.MAX_TEXT)
    data_handling: str | None = Field(None, max_length=model_cards.MAX_TEXT)
    notes: str | None = Field(None, max_length=model_cards.MAX_TEXT)
    owner_user_id: str | None = Field(None, max_length=64)


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "governance_model_cards_disabled",
            "message": "Model cards are off for this deployment (AGENTICORG_GOVERNANCE_MODEL_CARDS_ENABLED).",
        },
    )


def _refused(exc: model_cards.ModelCardError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


@router.get("/governance/model-cards", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.inventory.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="governance.model_cards.list",
)
async def list_model_cards(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """One summary per model the tenant uses: kind, roles, risk tier, callers, status and what is missing."""
    if not model_cards.enabled():
        raise _off()
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        cards = await model_cards.list_cards(session, tid)
    return {"cards": cards, "total": len(cards), "incomplete": sum(1 for c in cards if not c["complete"])}


@router.get("/governance/model-cards/{provider}/{model:path}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.inventory.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="governance.model_cards.read",
)
async def get_model_card(
    provider: str = Path(..., max_length=64),
    model: str = Path(..., max_length=128),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The standard card: facts, use, governance, economics, operations, evaluation, the written part, completeness."""
    if not model_cards.enabled():
        raise _off()
    tid = uuid.UUID(tenant_id)
    try:
        async with get_tenant_session(tid) as session:
            return await model_cards.collect_card(session, tid, provider, model)
    except model_cards.ModelCardError as exc:
        raise _refused(exc) from None


@router.put("/governance/model-cards/{provider}/{model:path}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.inventory.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-upsert-by-tenant-provider-model",
    audit_event="governance.model_cards.write",
)
async def write_model_card(
    body: ModelCardIn,
    provider: str = Path(..., max_length=64),
    model: str = Path(..., max_length=128),
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Write the administrator's part of the card; any edit returns it to draft."""
    if not model_cards.enabled():
        raise _off()
    tid = uuid.UUID(tenant_id)
    fields = body.model_dump(exclude_unset=True)
    try:
        async with get_tenant_session(tid) as session:
            row = await model_cards.write(session, tid, provider, model, fields, actor=_user_uuid_from_claims(user))
            written = model_cards.written_dict(row)
    except model_cards.ModelCardError as exc:
        raise _refused(exc) from None
    return {"provider": row.provider, "model": row.model, "written": written}


@router.post("/governance/model-cards/{provider}/{model:path}/approve", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.inventory.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-approval-by-second-person",
    audit_event="governance.model_cards.approve",
)
async def approve_model_card(
    provider: str = Path(..., max_length=64),
    model: str = Path(..., max_length=128),
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """A second person approves a complete card; the last editor cannot."""
    if not model_cards.enabled():
        raise _off()
    tid = uuid.UUID(tenant_id)
    try:
        async with get_tenant_session(tid) as session:
            row = await model_cards.approve(session, tid, provider, model, actor=_user_uuid_from_claims(user))
            written = model_cards.written_dict(row)
    except model_cards.ModelCardError as exc:
        raise _refused(exc) from None
    return {"provider": row.provider, "model": row.model, "written": written}
