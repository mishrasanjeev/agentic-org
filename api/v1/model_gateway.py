# SPDX-License-Identifier: Apache-2.0
"""Model gateway endpoints: routing policies, access policies, per-model limits and a dry-run evaluation.

Tenant administrators only. A routing policy says which provider, model, cost
tier or weighted targets a kind of request gets, which providers it may use and
whether it must stay in the tenant's data region; an access policy says which
application, principal, agent or business unit may use which provider or model;
a limit caps the calls in flight and the calls per minute on a provider or a
model. ``core.governance.model_gateway`` applies them to every tenant-scoped
model call once the gateway is on. Each change writes a signed audit row.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.database import get_tenant_session
from core.governance import model_gateway as gateway
from core.models.model_access_policy import ModelAccessPolicy
from core.models.model_gateway_record import ModelGatewayRecord
from core.models.model_limit import ModelLimit
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
    targets: list[dict[str, object]] | None = Field(
        None,
        max_length=16,
        description="weighted split: [{provider, model, weight}], instead of provider, model or tier",
    )
    allowed_providers: list[str] | None = Field(None, max_length=32)
    in_region_only: bool = False
    cost_aware: bool = Field(False, description="choose the cheapest healthy target")
    max_failure_rate: float | None = Field(
        None, ge=0, le=1, description="a target above this observed failure rate is skipped"
    )
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
    targets: list[dict[str, object]] | None = Field(None, max_length=16)
    allowed_providers: list[str] | None = Field(None, max_length=32)
    in_region_only: bool | None = None
    cost_aware: bool | None = None
    max_failure_rate: float | None = Field(None, ge=0, le=1)
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
    targets: list[dict[str, object]] | None
    allowed_providers: list[str] | None
    in_region_only: bool
    cost_aware: bool
    max_failure_rate: float | None
    reason: str
    created_by: str
    created_at: datetime
    updated_by: str | None
    updated_at: datetime | None


class AccessPolicyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    priority: int = Field(100, ge=0, le=100_000)
    enabled: bool = True
    use_case: str | None = Field(None, max_length=64)
    sensitivity: str | None = Field(None, description="public, internal, confidential or restricted")
    agent_id: str | None = Field(None, max_length=64)
    business_unit: str | None = Field(None, max_length=64)
    language: str | None = Field(None, max_length=16)
    application: str | None = Field(
        None, max_length=128, description="the calling application, as the identity names it"
    )
    principal: str | None = Field(None, max_length=255, description="the calling principal, as the audit rows name it")
    provider: str | None = Field(None, max_length=64, description="matches the provider the routing chose")
    model: str | None = Field(None, max_length=128, description="matches the model the routing chose")
    effect: str = Field("allow", description="allow or deny")
    allowed_providers: list[str] | None = Field(None, max_length=32)
    allowed_models: list[str] | None = Field(None, max_length=64)
    reason: str = Field("", max_length=2000)

    @model_validator(mode="after")
    def _usable(self) -> AccessPolicyIn:
        try:
            gateway.validate_access_policy_fields(self.model_dump())
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        return self


class AccessPolicyUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    priority: int | None = Field(None, ge=0, le=100_000)
    enabled: bool | None = None
    use_case: str | None = Field(None, max_length=64)
    sensitivity: str | None = None
    agent_id: str | None = Field(None, max_length=64)
    business_unit: str | None = Field(None, max_length=64)
    language: str | None = Field(None, max_length=16)
    application: str | None = Field(None, max_length=128)
    principal: str | None = Field(None, max_length=255)
    provider: str | None = Field(None, max_length=64)
    model: str | None = Field(None, max_length=128)
    effect: str | None = None
    allowed_providers: list[str] | None = Field(None, max_length=32)
    allowed_models: list[str] | None = Field(None, max_length=64)
    reason: str | None = Field(None, max_length=2000)


class AccessPolicyOut(BaseModel):
    id: uuid.UUID
    name: str
    priority: int
    enabled: bool
    use_case: str | None
    sensitivity: str | None
    agent_id: str | None
    business_unit: str | None
    language: str | None
    application: str | None
    principal: str | None
    provider: str | None
    model: str | None
    effect: str
    allowed_providers: list[str] | None
    allowed_models: list[str] | None
    reason: str
    created_by: str
    created_at: datetime
    updated_by: str | None
    updated_at: datetime | None


class LimitIn(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    model: str | None = Field(None, max_length=128, description="empty for a provider-wide limit")
    enabled: bool = True
    max_concurrency: int | None = Field(None, ge=1, le=100_000)
    requests_per_minute: int | None = Field(None, ge=1, le=10_000_000)
    reason: str = Field("", max_length=2000)

    @model_validator(mode="after")
    def _usable(self) -> LimitIn:
        try:
            gateway.validate_limit_fields(self.model_dump())
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        return self


class LimitUpdate(BaseModel):
    provider: str | None = Field(None, min_length=1, max_length=64)
    model: str | None = Field(None, max_length=128)
    enabled: bool | None = None
    max_concurrency: int | None = Field(None, ge=1, le=100_000)
    requests_per_minute: int | None = Field(None, ge=1, le=10_000_000)
    reason: str | None = Field(None, max_length=2000)


class LimitOut(BaseModel):
    id: uuid.UUID
    provider: str
    model: str | None
    enabled: bool
    max_concurrency: int | None
    requests_per_minute: int | None
    reason: str
    created_by: str
    created_at: datetime
    updated_by: str | None
    updated_at: datetime | None


class RecordOut(BaseModel):
    id: uuid.UUID
    correlation_id: str
    use_case: str
    agent_id: str | None
    policy_id: str | None
    access_policy_id: str | None
    requested_provider: str | None
    requested_model: str | None
    provider: str
    model: str
    fallback_from: str | None
    restricted: bool
    outcome: str
    error_type: str | None
    latency_ms: int
    admission_wait_ms: int | None
    tokens: int
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float
    signed: bool
    created_at: datetime


class EvaluateIn(BaseModel):
    use_case: str = Field(..., min_length=1, max_length=64)
    requested_provider: str | None = Field(None, max_length=64)
    requested_model: str = Field("", max_length=128)
    sensitivity: str | None = None
    agent_id: str | None = Field(None, max_length=64)
    business_unit: str | None = Field(None, max_length=64)
    language: str | None = Field(None, max_length=16)
    application: str | None = Field(None, max_length=128)
    principal: str | None = Field(None, max_length=255)

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
        targets=[dict(t) for t in row.targets] if getattr(row, "targets", None) is not None else None,
        allowed_providers=list(row.allowed_providers) if row.allowed_providers is not None else None,
        in_region_only=row.in_region_only,
        cost_aware=bool(getattr(row, "cost_aware", False)),
        max_failure_rate=getattr(row, "max_failure_rate", None),
        reason=row.reason or "",
        created_by=row.created_by,
        created_at=row.created_at,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


def _access_out(row: ModelAccessPolicy) -> AccessPolicyOut:
    return AccessPolicyOut(
        id=row.id,
        name=row.name,
        priority=row.priority,
        enabled=row.enabled,
        use_case=row.use_case,
        sensitivity=row.sensitivity,
        agent_id=row.agent_id,
        business_unit=row.business_unit,
        language=row.language,
        application=row.application,
        principal=row.principal,
        provider=row.provider,
        model=row.model,
        effect=row.effect,
        allowed_providers=list(row.allowed_providers) if row.allowed_providers is not None else None,
        allowed_models=list(row.allowed_models) if row.allowed_models is not None else None,
        reason=row.reason or "",
        created_by=row.created_by,
        created_at=row.created_at,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


def _limit_out(row: ModelLimit) -> LimitOut:
    return LimitOut(
        id=row.id,
        provider=row.provider,
        model=row.model,
        enabled=row.enabled,
        max_concurrency=row.max_concurrency,
        requests_per_minute=row.requests_per_minute,
        reason=row.reason or "",
        created_by=row.created_by,
        created_at=row.created_at,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


async def _fetch(tenant_id: uuid.UUID, model: type, row_id: uuid.UUID):
    async with get_tenant_session(tenant_id) as session:
        return (await session.execute(select(model).where(model.id == row_id))).scalar_one()


def _record_out(row: ModelGatewayRecord) -> RecordOut:
    from core.governance.model_gateway_records import verify_record

    return RecordOut(
        id=row.id,
        correlation_id=row.correlation_id,
        use_case=row.use_case,
        agent_id=row.agent_id,
        policy_id=row.policy_id,
        access_policy_id=row.access_policy_id,
        requested_provider=row.requested_provider,
        requested_model=row.requested_model,
        provider=row.provider,
        model=row.model,
        fallback_from=row.fallback_from,
        restricted=row.restricted,
        outcome=row.outcome,
        error_type=row.error_type,
        latency_ms=row.latency_ms,
        admission_wait_ms=row.admission_wait_ms,
        tokens=row.tokens,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cost_usd=row.cost_usd,
        signed=verify_record(row),
        created_at=row.created_at,
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
    """Whether the gateway is on for this tenant and which policies and limits are active."""
    tid = uuid.UUID(tenant_id)
    on = await gateway.enabled(tid)
    policy_set = await gateway.active_policy_set(tid) if on else gateway.PolicySet()
    return {
        "enabled": on,
        "active_policies": [p.to_dict() for p in policy_set.routing],
        "active_access_policies": [p.to_dict() for p in policy_set.access],
        "active_limits": [limit.to_dict() for limit in policy_set.limits],
    }


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


@router.get("/access-policies", response_model=list[AccessPolicyOut])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="model_gateway.access_policies.list",
)
async def list_access_policies(
    include_disabled: bool = True,
    tenant_id: str = Depends(get_current_tenant),
) -> list[AccessPolicyOut]:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        query = select(ModelAccessPolicy).where(ModelAccessPolicy.tenant_id == tid)
        if not include_disabled:
            query = query.where(ModelAccessPolicy.enabled.is_(True))
        rows = (
            (await session.execute(query.order_by(ModelAccessPolicy.priority, ModelAccessPolicy.name))).scalars().all()
        )
        return [_access_out(row) for row in rows]


@router.post("/access-policies", response_model=AccessPolicyOut, status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="non-idempotent-create",
    audit_event="model_gateway.access_policies.set",
)
async def create_access_policy(
    body: AccessPolicyIn,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> AccessPolicyOut:
    tid = uuid.UUID(tenant_id)
    actor_id = _actor(request, caller)
    try:
        policy = await gateway.set_access_policy(tid, actor_id=actor_id, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return _access_out(await _fetch(tid, ModelAccessPolicy, uuid.UUID(policy.id)))


@router.patch("/access-policies/{policy_id}", response_model=AccessPolicyOut)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="idempotent-update",
    audit_event="model_gateway.access_policies.update",
)
async def update_access_policy(
    policy_id: uuid.UUID,
    body: AccessPolicyUpdate,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> AccessPolicyOut:
    tid = uuid.UUID(tenant_id)
    actor_id = _actor(request, caller)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "nothing to change")
    try:
        policy = await gateway.update_access_policy(tid, policy_id, actor_id=actor_id, changes=changes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if policy is None:
        raise HTTPException(404, "Access policy not found")
    return _access_out(await _fetch(tid, ModelAccessPolicy, policy_id))


@router.delete("/access-policies/{policy_id}", status_code=204)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="idempotent-delete",
    audit_event="model_gateway.access_policies.delete",
)
async def delete_access_policy(
    policy_id: uuid.UUID,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> Response:
    tid = uuid.UUID(tenant_id)
    actor_id = _actor(request, caller)
    if not await gateway.delete_access_policy(tid, policy_id, actor_id=actor_id):
        raise HTTPException(404, "Access policy not found")
    return Response(status_code=204)


@router.get("/limits", response_model=list[LimitOut])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="model_gateway.limits.list",
)
async def list_limits(
    include_disabled: bool = True,
    tenant_id: str = Depends(get_current_tenant),
) -> list[LimitOut]:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        query = select(ModelLimit).where(ModelLimit.tenant_id == tid)
        if not include_disabled:
            query = query.where(ModelLimit.enabled.is_(True))
        rows = (await session.execute(query.order_by(ModelLimit.provider, ModelLimit.model))).scalars().all()
        return [_limit_out(row) for row in rows]


@router.post("/limits", response_model=LimitOut, status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="non-idempotent-create",
    audit_event="model_gateway.limits.set",
)
async def create_limit(
    body: LimitIn,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> LimitOut:
    tid = uuid.UUID(tenant_id)
    actor_id = _actor(request, caller)
    try:
        limit = await gateway.set_limit(tid, actor_id=actor_id, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return _limit_out(await _fetch(tid, ModelLimit, uuid.UUID(limit.id)))


@router.patch("/limits/{limit_id}", response_model=LimitOut)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="idempotent-update",
    audit_event="model_gateway.limits.update",
)
async def update_limit(
    limit_id: uuid.UUID,
    body: LimitUpdate,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> LimitOut:
    tid = uuid.UUID(tenant_id)
    actor_id = _actor(request, caller)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "nothing to change")
    try:
        limit = await gateway.update_limit(tid, limit_id, actor_id=actor_id, changes=changes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if limit is None:
        raise HTTPException(404, "Limit not found")
    return _limit_out(await _fetch(tid, ModelLimit, limit_id))


@router.delete("/limits/{limit_id}", status_code=204)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="idempotent-delete",
    audit_event="model_gateway.limits.delete",
)
async def delete_limit(
    limit_id: uuid.UUID,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> Response:
    tid = uuid.UUID(tenant_id)
    actor_id = _actor(request, caller)
    if not await gateway.delete_limit(tid, limit_id, actor_id=actor_id):
        raise HTTPException(404, "Limit not found")
    return Response(status_code=204)


@router.get("/costs")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="model_gateway.costs",
)
async def compare_costs(
    window_hours: Annotated[
        int | None, Query(ge=1, le=720, description="observation window; the quality window by default")
    ] = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, object]:
    """Every catalogue model, and every model seen in the records, with its list price and what the records observed."""
    from core.ai_providers.catalog import LLM_CATALOG
    from core.governance.model_gateway_records import model_health
    from core.governance.model_pricing import price_for

    tid = uuid.UUID(tenant_id)
    hours = window_hours or gateway.settings.model_gateway_quality_window_hours
    health = await model_health(tid, window_hours=hours)
    names: list[tuple[str, str]] = [(entry.provider, entry.model) for entry in LLM_CATALOG if entry.model != "*"]
    names += [key for key in health if key not in names]
    rows = []
    for provider, model in names:
        price = price_for(provider, model)
        observed = health.get((provider, model))
        rows.append(
            {
                "provider": provider,
                "model": model,
                "list_price": price.to_dict() if price is not None else None,
                "blended_per_million_usd": round(price.blended_per_million, 4) if price is not None else None,
                "observed": observed.to_dict() if observed is not None else None,
            }
        )
    rows.sort(
        key=lambda r: (
            r["blended_per_million_usd"] is None,
            r["blended_per_million_usd"] or 0.0,
            r["provider"],
            r["model"],
        )
    )
    return {"window_hours": hours, "models": rows}


@router.get("/records", response_model=list[RecordOut])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.model_gateway.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="model_gateway.records.list",
)
async def list_records(
    correlation_id: Annotated[str | None, Query(max_length=128)] = None,
    agent_id: Annotated[str | None, Query(max_length=64)] = None,
    outcome: Annotated[str | None, Query(pattern="^(completed|failed)$")] = None,
    before: Annotated[datetime | None, Query(description="only records created before this instant")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    tenant_id: str = Depends(get_current_tenant),
) -> list[RecordOut]:
    """The routing records, newest first; ``signed`` says each row's signature still matches its fields."""
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        query = select(ModelGatewayRecord).where(ModelGatewayRecord.tenant_id == tid)
        if correlation_id:
            query = query.where(ModelGatewayRecord.correlation_id == correlation_id)
        if agent_id:
            query = query.where(ModelGatewayRecord.agent_id == agent_id)
        if outcome:
            query = query.where(ModelGatewayRecord.outcome == outcome)
        if before is not None:
            query = query.where(ModelGatewayRecord.created_at < before)
        rows = (
            (await session.execute(query.order_by(ModelGatewayRecord.created_at.desc()).limit(limit))).scalars().all()
        )
        return [_record_out(row) for row in rows]


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
    """Dry-run the routing and access policies for a described request, whether or not the gateway is on.

    The answer is the decision the policies would make, or the refusal, as data,
    with ``enabled`` saying whether the gateway currently applies it. Limits are
    not applied: a dry run starts no model work.
    """
    request = gateway.RouteRequest(tenant_id=uuid.UUID(tenant_id), **body.model_dump())
    evaluation = await gateway.evaluate(request)
    if evaluation.refusal is not None:
        return {"refused": True, "enabled": evaluation.enabled, **evaluation.refusal.to_error()}
    assert evaluation.decision is not None
    return {"refused": False, "enabled": evaluation.enabled, "decision": evaluation.decision.to_dict()}
