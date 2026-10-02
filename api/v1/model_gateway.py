# SPDX-License-Identifier: Apache-2.0
"""Model gateway endpoints: routing policies and a dry-run evaluation.

Tenant administrators only. A policy says which provider, model or cost tier a
kind of request gets, which providers it may use and whether it must stay in the
tenant's data region; ``core.governance.model_gateway`` applies the enabled
policies before every tenant-scoped model call once the gateway is on. Each
change writes a signed audit row.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.database import get_tenant_session
from core.governance import model_gateway as gateway
from core.models.model_routing_policy import ModelRoutingPolicy
from core.ownership import Caller, caller_from_request

logger = structlog.get_logger()
router = APIRouter(prefix="/model-gateway", tags=["Model Gateway"], dependencies=[require_tenant_admin])


class PolicyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    priority: int = Field(100, ge=0, le=100_000)
    enabled: bool = True
    use_case: str | None = Field(None, max_length=64)
    sensitivity: str | None = Field(None, description="public, internal, confidential or restricted")
    agent_id: str | None = Field(None, max_length=64)
    business_unit: str | None = Field(None, max_length=64)
    language: str | None = Field(None, max_length=16)
    provider: str | None = Field(None, max_length=64)
    model: str | None = Field(None, max_length=128)
    tier: str | None = Field(None, description="tier1, tier2 or tier3")
    allowed_providers: list[str] | None = Field(None, max_length=32)
    in_region_only: bool = False
    reason: str = Field("", max_length=2000)

    @model_validator(mode="after")
    def _usable(self) -> PolicyIn:
        try:
            gateway.validate_policy_fields(self.model_dump())
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        return self


class PolicyUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    priority: int | None = Field(None, ge=0, le=100_000)
    enabled: bool | None = None
    use_case: str | None = Field(None, max_length=64)
    sensitivity: str | None = None
    agent_id: str | None = Field(None, max_length=64)
    business_unit: str | None = Field(None, max_length=64)
    language: str | None = Field(None, max_length=16)
    provider: str | None = Field(None, max_length=64)
    model: str | None = Field(None, max_length=128)
    tier: str | None = None
    allowed_providers: list[str] | None = Field(None, max_length=32)
    in_region_only: bool | None = None
    reason: str | None = Field(None, max_length=2000)


class PolicyOut(BaseModel):
    id: uuid.UUID
    name: str
    priority: int
    enabled: bool
    use_case: str | None
    sensitivity: str | None
    agent_id: str | None
    business_unit: str | None
    language: str | None
    provider: str | None
    model: str | None
    tier: str | None
    allowed_providers: list[str] | None
    in_region_only: bool
    reason: str
    created_by: str
    created_at: datetime
    updated_by: str | None
    updated_at: datetime | None


class EvaluateIn(BaseModel):
    use_case: str = Field(..., min_length=1, max_length=64)
    requested_provider: str | None = Field(None, max_length=64)
    requested_model: str = Field("", max_length=128)
    sensitivity: str | None = None
    agent_id: str | None = Field(None, max_length=64)
    business_unit: str | None = Field(None, max_length=64)
    language: str | None = Field(None, max_length=16)

    @model_validator(mode="after")
    def _known_sensitivity(self) -> EvaluateIn:
        if self.sensitivity is not None:
            self.sensitivity = self.sensitivity.strip().lower()
            if self.sensitivity not in gateway.SENSITIVITIES:
                raise ValueError(f"sensitivity must be one of {', '.join(gateway.SENSITIVITIES)}")
        return self


def _actor(request: Request, caller: Caller | None) -> str:
    """The authenticated principal a policy change is attributed to.

    A human administrator is recorded by user id; a machine caller by its
    authenticated subject, prefixed with its auth mode. A request with neither is
    refused: a control-plane action with no attributable actor is not performed.
    """
    user_id = getattr(caller, "user_id", None)
    if user_id:
        return f"user:{user_id}"
    claims = getattr(request.state, "claims", None) or {}
    subject = str(claims.get("sub") or "").strip()
    auth_mode = str(getattr(request.state, "auth_mode", None) or "").strip()
    if subject and auth_mode:
        return f"{auth_mode}:{subject}"
    raise HTTPException(403, "A routing policy change needs an attributable caller")


def _out(row: ModelRoutingPolicy) -> PolicyOut:
    return PolicyOut(
        id=row.id,
        name=row.name,
        priority=row.priority,
        enabled=row.enabled,
        use_case=row.use_case,
        sensitivity=row.sensitivity,
        agent_id=row.agent_id,
        business_unit=row.business_unit,
        language=row.language,
        provider=row.provider,
        model=row.model,
        tier=row.tier,
        allowed_providers=list(row.allowed_providers) if row.allowed_providers is not None else None,
        in_region_only=row.in_region_only,
        reason=row.reason or "",
        created_by=row.created_by,
        created_at=row.created_at,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


@router.get("/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="model_gateway.status",
)
async def gateway_status(tenant_id: str = Depends(get_current_tenant)) -> dict[str, object]:
    """Whether the gateway is on for this tenant and which policies are active."""
    tid = uuid.UUID(tenant_id)
    on = await gateway.enabled(tid)
    policies = await gateway.active_policies(tid) if on else []
    return {"enabled": on, "active_policies": [p.to_dict() for p in policies]}


@router.get("/policies", response_model=list[PolicyOut])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="model_gateway.policies.list",
)
async def list_policies(
    include_disabled: bool = True,
    tenant_id: str = Depends(get_current_tenant),
) -> list[PolicyOut]:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        query = select(ModelRoutingPolicy).where(ModelRoutingPolicy.tenant_id == tid)
        if not include_disabled:
            query = query.where(ModelRoutingPolicy.enabled.is_(True))
        rows = (
            (await session.execute(query.order_by(ModelRoutingPolicy.priority, ModelRoutingPolicy.name)))
            .scalars()
            .all()
        )
        return [_out(row) for row in rows]


@router.post("/policies", response_model=PolicyOut, status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="non-idempotent-create",
    audit_event="model_gateway.policies.set",
)
async def create_policy(
    body: PolicyIn,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> PolicyOut:
    tid = uuid.UUID(tenant_id)
    actor_id = _actor(request, caller)
    try:
        policy = await gateway.set_policy(tid, actor_id=actor_id, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    async with get_tenant_session(tid) as session:
        row = (
            await session.execute(select(ModelRoutingPolicy).where(ModelRoutingPolicy.id == uuid.UUID(policy.id)))
        ).scalar_one()
        return _out(row)


@router.patch("/policies/{policy_id}", response_model=PolicyOut)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="idempotent-update",
    audit_event="model_gateway.policies.update",
)
async def update_policy(
    policy_id: uuid.UUID,
    body: PolicyUpdate,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> PolicyOut:
    tid = uuid.UUID(tenant_id)
    actor_id = _actor(request, caller)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "nothing to change")
    try:
        policy = await gateway.update_policy(tid, policy_id, actor_id=actor_id, changes=changes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if policy is None:
        raise HTTPException(404, "Routing policy not found")
    async with get_tenant_session(tid) as session:
        row = (await session.execute(select(ModelRoutingPolicy).where(ModelRoutingPolicy.id == policy_id))).scalar_one()
        return _out(row)


@router.delete("/policies/{policy_id}", status_code=204)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="idempotent-delete",
    audit_event="model_gateway.policies.delete",
)
async def delete_policy(
    policy_id: uuid.UUID,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> Response:
    tid = uuid.UUID(tenant_id)
    actor_id = _actor(request, caller)
    if not await gateway.delete_policy(tid, policy_id, actor_id=actor_id):
        raise HTTPException(404, "Routing policy not found")
    return Response(status_code=204)


@router.post("/evaluate")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="model_gateway.evaluate",
)
async def evaluate(body: EvaluateIn, tenant_id: str = Depends(get_current_tenant)) -> dict[str, object]:
    """Dry-run the gateway for a described request: the decision it would make, or the refusal, as data."""
    request = gateway.RouteRequest(tenant_id=uuid.UUID(tenant_id), **body.model_dump())
    try:
        decision = await gateway.decide(request)
    except gateway.ModelGatewayRefused as exc:
        return {"refused": True, **exc.to_error()}
    return {"refused": False, "decision": decision.to_dict()}
