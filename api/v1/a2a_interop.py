# SPDX-License-Identifier: Apache-2.0
"""A2A v1 HTTP+JSON synchronous messages with merchant-bound buyer access."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from api.v1 import a2a as legacy_a2a
from api.v1.commerce_runtime import _answer_buyer_question_for_scope
from core.commerce.a2a_buyer_access import _QUERYABLE_SELLER_STATES, mint_buyer_token
from core.database import get_tenant_session
from core.models.commerce_a2a_buyer_access import CommerceA2ABuyerAccess
from core.models.commerce_c6z_runtime import C6ZSellerOnboardingPacketRow

router = APIRouter(prefix="/a2a", tags=["A2A"])
discovery_router = APIRouter(tags=["A2A"])


def _version_error(request: Request) -> JSONResponse | None:
    # A2A v1 specification 3.6 treats an absent version as legacy 0.3.
    requested = request.headers.get("A2A-Version") or request.query_params.get("A2A-Version")
    if requested == "1.0":
        return None
    return JSONResponse(
        status_code=400,
        media_type="application/problem+json",
        content={
            "type": "https://a2a-protocol.org/errors/version-not-supported",
            "title": "Protocol Version Not Supported",
            "status": 400,
            "detail": f"The requested A2A protocol version {requested or '0.3'} is not supported",
            "supportedVersions": ["1.0"],
        },
    )


def _card(*, merchant_name: str | None = None) -> dict[str, Any]:
    base = os.getenv("AGENTICORG_BASE_URL", "https://app.agenticorg.ai").rstrip("/")
    skills = [
        {
            "id": skill["id"],
            "name": skill["name"],
            "description": skill["description"],
            "tags": [skill["domain"], "agenticorg"],
        }
        for skill in legacy_a2a._build_agent_skills()
        if skill["id"] != "commerce_sales_agent"
    ]
    if merchant_name is not None:
        skills = [{
            "id": "seller_commerce_query",
            "name": f"{merchant_name} Seller Commerce Agent",
            "description": "Non-binding product answers from scoped OACP cache with source and freshness labels.",
            "tags": ["commerce", "catalog", "read-only"],
        }]
    return {
        "name": "AgenticOrg Seller Commerce Agent" if merchant_name is not None else "AgenticOrg Agent Platform",
        "description": (
            "Authenticated agent collaboration. Merchant-specific skills require "
            "a seller-issued buyer credential."
        ),
        "supportedInterfaces": [{
            "url": f"{base}/api/v1/a2a",
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0",
        }],
        "version": "1.0.0",
        "capabilities": {"streaming": False, "pushNotifications": False, "extendedAgentCard": True},
        "securitySchemes": {"bearer": {"httpAuthSecurityScheme": {
            "scheme": "Bearer",
            "description": (
                "Seller-issued buyer credential for commerce; tenant-scoped agent grant "
                "or API key for other agents."
            ),
        }}},
        "securityRequirements": [{"schemes": {"bearer": {"list": []}}}],
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": skills,
    }


@discovery_router.get("/.well-known/agent-card.json")
@route_meta(
    auth_required=False, tenant_required=False, scope="public:a2a.discovery",
    rate_limit="a2a-discovery", idempotency="read-only",
    audit_event="none-public-discovery", public_reason="generic-card-no-merchant-data",
)
async def standard_agent_card() -> dict[str, Any]:
    return _card()


@router.get("/extendedAgentCard", response_model=dict[str, Any])
@route_meta(
    auth_required=True, tenant_required=True, scope="a2a.extended_card.read",
    rate_limit="a2a-task-read", idempotency="read-only",
    audit_event="a2a.extended_card.read",
)
async def extended_agent_card(request: Request) -> dict[str, Any] | JSONResponse:
    if (version_error := _version_error(request)) is not None:
        return version_error
    if getattr(request.state, "auth_mode", None) != "commerce_buyer":
        raise HTTPException(403, "Seller-specific card requires merchant-approved buyer access")
    state = request.state
    async with get_tenant_session(uuid.UUID(state.tenant_id)) as session:
        packet = await session.scalar(select(C6ZSellerOnboardingPacketRow).where(
            C6ZSellerOnboardingPacketRow.tenant_id == state.tenant_id,
            C6ZSellerOnboardingPacketRow.merchant_id == state.buyer_merchant_id,
            C6ZSellerOnboardingPacketRow.seller_agent_id == state.buyer_seller_agent_id,
        ))
        if packet is None or packet.status not in _QUERYABLE_SELLER_STATES:
            raise HTTPException(404, "Seller Commerce Agent not found")
        return _card(merchant_name=packet.merchant_display_name)


class BuyerAccessCreate(BaseModel):
    merchant_id: str = Field(min_length=1, max_length=160)
    seller_agent_id: str = Field(min_length=1, max_length=160)
    buyer_agent_id: str = Field(min_length=1, max_length=160)
    expires_days: int = Field(default=7, ge=1, le=30)


@router.post("/commerce/buyer-access", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True, tenant_required=True, scope="a2a.commerce_buyer_access.write",
    rate_limit="commerce-runtime-write", idempotency="one-time-credential-issuance",
    audit_event="a2a.commerce_buyer_access.create",
)
async def create_buyer_access(
    body: BuyerAccessCreate, tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    token, digest = mint_buyer_token(tenant_id)
    access_id = uuid.uuid4()
    expires_at = datetime.now(UTC) + timedelta(days=body.expires_days)
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        packet = await session.scalar(select(C6ZSellerOnboardingPacketRow).where(
            C6ZSellerOnboardingPacketRow.tenant_id == tenant_id,
            C6ZSellerOnboardingPacketRow.merchant_id == body.merchant_id,
            C6ZSellerOnboardingPacketRow.seller_agent_id == body.seller_agent_id,
        ))
        if packet is None or packet.status not in _QUERYABLE_SELLER_STATES:
            raise HTTPException(404, "Seller Commerce Agent not found")
        session.add(CommerceA2ABuyerAccess(
            id=access_id, tenant_id=tenant_id, merchant_id=body.merchant_id,
            seller_agent_id=body.seller_agent_id, buyer_agent_id=body.buyer_agent_id,
            token_hash=digest, status="active", expires_at=expires_at,
        ))
    return {
        "id": str(access_id), "buyer_agent_id": body.buyer_agent_id,
        "merchant_id": body.merchant_id, "seller_agent_id": body.seller_agent_id,
        "expires_at": expires_at.isoformat(), "token": token,
        "allowed_capability": "non_binding_product_question_only",
    }


@router.get("/commerce/buyer-access", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True, tenant_required=True, scope="a2a.commerce_buyer_access.read",
    rate_limit="standard", idempotency="read-only", audit_event="a2a.commerce_buyer_access.list",
)
async def list_buyer_access(
    merchant_id: str, tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        rows = (await session.scalars(select(CommerceA2ABuyerAccess).where(
            CommerceA2ABuyerAccess.tenant_id == tenant_id,
            CommerceA2ABuyerAccess.merchant_id == merchant_id,
        ).order_by(CommerceA2ABuyerAccess.created_at.desc()).limit(100))).all()
        return {"items": [{
            "id": str(row.id), "buyer_agent_id": row.buyer_agent_id,
            "merchant_id": row.merchant_id, "seller_agent_id": row.seller_agent_id,
            "status": row.status, "expires_at": row.expires_at.isoformat(),
        } for row in rows]}


@router.delete("/commerce/buyer-access/{access_id}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True, tenant_required=True, scope="a2a.commerce_buyer_access.write",
    rate_limit="commerce-runtime-write", idempotency="idempotent-revocation",
    audit_event="a2a.commerce_buyer_access.revoke",
)
async def revoke_buyer_access(
    access_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await session.get(CommerceA2ABuyerAccess, access_id)
        if row is None or row.tenant_id != tenant_id:
            raise HTTPException(404, "Buyer access not found")
        row.status = "revoked"
        row.revoked_at = datetime.now(UTC)
    return {"id": str(access_id), "status": "revoked"}


class A2ATextPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4096)


class A2AMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str = Field(alias="messageId", min_length=1, max_length=128)
    role: Literal["ROLE_USER"]
    parts: list[A2ATextPart] = Field(min_length=1, max_length=4)
    metadata: dict[str, Any] = Field(default_factory=dict)
    context_id: str | None = Field(default=None, alias="contextId", max_length=128)
    task_id: str | None = Field(default=None, alias="taskId", max_length=128)


class A2ASendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: A2AMessage
    configuration: dict[str, Any] = Field(default_factory=dict)


@router.post("/message:send", response_model=dict[str, Any])
@route_meta(
    auth_required=True, tenant_required=True, scope="a2a.message.send",
    rate_limit="commerce-a2a-buyer", idempotency="read-only-synchronous-message",
    audit_event="a2a.message.send",
)
async def send_message(
    body: A2ASendRequest, request: Request, response: Response,
) -> dict[str, Any] | JSONResponse:
    if (version_error := _version_error(request)) is not None:
        return version_error
    response.headers["Content-Type"] = "application/a2a+json"
    if body.message.task_id or body.configuration.get("taskPushNotificationConfig"):
        raise HTTPException(422, "Task continuation and push notifications are not supported")
    if set(body.configuration) - {"acceptedOutputModes"}:
        raise HTTPException(422, "Unsupported A2A configuration")
    modes = body.configuration.get("acceptedOutputModes")
    if modes is not None and (not isinstance(modes, list) or "text/plain" not in modes):
        raise HTTPException(422, "Only text/plain output is supported")
    if getattr(request.state, "auth_mode", None) == "commerce_buyer":
        result = await _answer_seller_message(body, request)
    else:
        result = await _run_internal_agent_message(body, request)
    return {"message": {
        "messageId": str(uuid.uuid4()), "role": "ROLE_AGENT",
        "parts": [{"text": result["text"]}],
        "metadata": result["metadata"],
        **({"contextId": body.message.context_id} if body.message.context_id else {}),
    }}


async def _answer_seller_message(body: A2ASendRequest, request: Request) -> dict[str, Any]:
    state = request.state
    metadata = body.message.metadata
    if set(metadata) - {"merchantId", "sellerAgentId", "buyerAgentId", "actionIntent"}:
        raise HTTPException(422, "Unsupported commerce A2A metadata")
    for key, expected in (
        ("merchantId", state.buyer_merchant_id),
        ("sellerAgentId", state.buyer_seller_agent_id),
        ("buyerAgentId", state.buyer_agent_id),
    ):
        if key in metadata and metadata[key] != expected:
            raise HTTPException(403, "A2A buyer scope mismatch")
    if metadata.get("actionIntent", "non_binding_preview") != "non_binding_preview":
        raise HTTPException(403, "Only non-binding product questions are permitted")
    question = "\n".join(part.text for part in body.message.parts)
    answer, _, _ = await _answer_buyer_question_for_scope(
        tenant_id=state.tenant_id,
        merchant_id=state.buyer_merchant_id,
        seller_agent_id=state.buyer_seller_agent_id,
        buyer_agent_id=state.buyer_agent_id,
        question=question,
        action_intent="non_binding_preview",
        grantex_available=False,
    )
    return {"text": answer["answer"], "metadata": {
        "status": answer["status"],
        "sourceLabel": answer["source_label"],
        "freshnessLabel": answer["freshness_label"],
        "refusalReason": answer["refusal_reason"],
        "allowedToExecute": False,
        "nonAuthoritativeForTransaction": True,
    }}


async def _run_internal_agent_message(body: A2ASendRequest, request: Request) -> dict[str, Any]:
    scopes = set(getattr(request.state, "scopes", []) or [])
    if "a2a:write" not in scopes and "agenticorg:admin" not in scopes:
        raise HTTPException(403, "A2A agent execution scope required")
    metadata = body.message.metadata
    agent_type = metadata.get("agentType")
    company_id = metadata.get("companyId")
    if not isinstance(agent_type, str) or not isinstance(company_id, str):
        raise HTTPException(422, "agentType and companyId are required for agent execution")
    if agent_type == "commerce_sales_agent":
        raise HTTPException(403, "Seller Commerce Agent requires merchant-approved buyer access")
    result = await legacy_a2a.create_task(
        legacy_a2a.A2ATaskRequest(
            agent_type=agent_type, company_id=company_id,
            inputs={"message": "\n".join(part.text for part in body.message.parts)},
        ),
        request,
        request.state.tenant_id,
    )
    if result.get("status") != "completed":
        return {"text": "Agent run did not complete.", "metadata": {"status": "failed"}}
    return {"text": json.dumps(result.get("output") or {}, ensure_ascii=True), "metadata": {"status": "completed"}}
