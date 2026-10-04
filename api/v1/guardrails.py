# SPDX-License-Identifier: Apache-2.0
"""Guardrail endpoints: rules and a dry-run evaluation.

Tenant administrators only. A rule says which detector runs at which stage of
a model or tool call and what happens when it finds something;
``core.governance.guardrails`` applies the enabled rules, enforcing them only
when ``guardrails.enforce`` is on for the tenant. Each change writes a signed
audit row.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.database import get_tenant_session
from core.governance import guardrails
from core.models.guardrail_rule import GuardrailRule
from core.ownership import Caller, caller_from_request

logger = structlog.get_logger()
router = APIRouter(prefix="/guardrails", tags=["Guardrails"], dependencies=[require_tenant_admin])


class RuleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    stage: str = Field(..., description="input, retrieval, output or action")
    detector: str = Field(..., description="sensitive_data, toxicity or pattern")
    action: str = Field("flag", description="flag, mask, redact, tokenise or block")
    priority: int = Field(100, ge=0, le=100_000)
    enabled: bool = True
    threshold: float = Field(0.5, ge=0, le=1)
    agent_id: str | None = Field(None, max_length=64)
    use_case: str | None = Field(None, max_length=64)
    risk_tier: str | None = Field(None, description="low, medium, high or critical")
    options: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field("", max_length=2000)

    @model_validator(mode="after")
    def _usable(self) -> RuleIn:
        try:
            guardrails.validate_rule_fields(self.model_dump())
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        return self


class RuleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    stage: str | None = None
    detector: str | None = None
    action: str | None = None
    priority: int | None = Field(None, ge=0, le=100_000)
    enabled: bool | None = None
    threshold: float | None = Field(None, ge=0, le=1)
    agent_id: str | None = Field(None, max_length=64)
    use_case: str | None = Field(None, max_length=64)
    risk_tier: str | None = None
    options: dict[str, Any] | None = None
    reason: str | None = Field(None, max_length=2000)


class RuleOut(BaseModel):
    id: uuid.UUID
    name: str
    stage: str
    detector: str
    action: str
    priority: int
    enabled: bool
    threshold: float
    agent_id: str | None
    use_case: str | None
    risk_tier: str | None
    options: dict[str, Any]
    reason: str
    created_by: str
    created_at: datetime
    updated_by: str | None
    updated_at: datetime | None


class EvaluateIn(BaseModel):
    stage: str = Field(..., description="input, retrieval, output or action")
    text: str = Field(..., max_length=200_000)
    agent_id: str | None = Field(None, max_length=64)
    use_case: str | None = Field(None, max_length=64)
    risk_tier: str | None = None
    # What the answer is held against by a grounding rule (output stage): retrieved texts and the user's words.
    context: list[Annotated[str, Field(max_length=50_000)]] | None = Field(None, max_length=50)
    user_input: list[Annotated[str, Field(max_length=50_000)]] | None = Field(None, max_length=50)

    @model_validator(mode="after")
    def _known(self) -> EvaluateIn:
        self.stage = self.stage.strip().lower()
        if self.stage not in guardrails.STAGES:
            raise ValueError(f"stage must be one of {', '.join(guardrails.STAGES)}")
        return self


def _actor(request: Request, caller: Caller | None) -> str:
    """The authenticated principal a rule change is attributed to (the model gateway's spelling)."""
    user_id = getattr(caller, "user_id", None)
    if user_id:
        return f"user:{user_id}"
    claims = getattr(request.state, "claims", None) or {}
    subject = str(claims.get("sub") or "").strip()
    auth_mode = str(getattr(request.state, "auth_mode", None) or "").strip()
    if subject and auth_mode:
        return f"{auth_mode}:{subject}"
    raise HTTPException(403, "A guardrail rule change needs an attributable caller")


def _out(row: GuardrailRule) -> RuleOut:
    return RuleOut(
        id=row.id,
        name=row.name,
        stage=row.stage,
        detector=row.detector,
        action=row.action,
        priority=row.priority,
        enabled=row.enabled,
        threshold=row.threshold,
        agent_id=row.agent_id,
        use_case=row.use_case,
        risk_tier=row.risk_tier,
        options=dict(row.options or {}),
        reason=row.reason or "",
        created_by=row.created_by,
        created_at=row.created_at,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


async def _fetch(tenant_id: uuid.UUID, rule_id: uuid.UUID) -> GuardrailRule:
    async with get_tenant_session(tenant_id) as session:
        return (await session.execute(select(GuardrailRule).where(GuardrailRule.id == rule_id))).scalar_one()


@router.get("/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.guardrails.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="guardrails.status",
)
async def guardrail_status(tenant_id: str = Depends(get_current_tenant)) -> dict[str, object]:
    """Whether guardrails enforce for this tenant, and the rules in effect (flag-only or enforced)."""
    tid = uuid.UUID(tenant_id)
    enforced = await guardrails.enforcing(tid)
    rules = await guardrails.active_rules(tid)
    return {
        "enforcing": enforced,
        "mode": "enforced" if enforced else "flag_only",
        "active_rules": [r.to_dict() for r in rules],
    }


@router.get("/rules", response_model=list[RuleOut])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.guardrails.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="guardrails.rules.list",
)
async def list_rules(include_disabled: bool = True, tenant_id: str = Depends(get_current_tenant)) -> list[RuleOut]:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        query = select(GuardrailRule).where(GuardrailRule.tenant_id == tid)
        if not include_disabled:
            query = query.where(GuardrailRule.enabled.is_(True))
        rows = (
            (await session.execute(query.order_by(GuardrailRule.stage, GuardrailRule.priority, GuardrailRule.name)))
            .scalars()
            .all()
        )
        return [_out(row) for row in rows]


@router.post("/rules", response_model=RuleOut, status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.guardrails.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="non-idempotent-create",
    audit_event="guardrails.rules.set",
)
async def create_rule(
    body: RuleIn,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> RuleOut:
    tid = uuid.UUID(tenant_id)
    actor_id = _actor(request, caller)
    try:
        rule = await guardrails.set_rule(tid, actor_id=actor_id, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return _out(await _fetch(tid, uuid.UUID(rule.id)))


@router.patch("/rules/{rule_id}", response_model=RuleOut)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.guardrails.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="idempotent-update",
    audit_event="guardrails.rules.update",
)
async def update_rule(
    rule_id: uuid.UUID,
    body: RuleUpdate,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> RuleOut:
    tid = uuid.UUID(tenant_id)
    actor_id = _actor(request, caller)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "nothing to change")
    try:
        rule = await guardrails.update_rule(tid, rule_id, actor_id=actor_id, changes=changes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if rule is None:
        raise HTTPException(404, "Guardrail rule not found")
    return _out(await _fetch(tid, rule_id))


@router.delete("/rules/{rule_id}", status_code=204)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.guardrails.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="idempotent-delete",
    audit_event="guardrails.rules.delete",
)
async def delete_rule(
    rule_id: uuid.UUID,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> Response:
    tid = uuid.UUID(tenant_id)
    actor_id = _actor(request, caller)
    if not await guardrails.delete_rule(tid, rule_id, actor_id=actor_id):
        raise HTTPException(404, "Guardrail rule not found")
    return Response(status_code=204)


@router.post("/evaluate")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.guardrails.sensitive.read",
    rate_limit="standard",
    idempotency="idempotent-read",
    audit_event="guardrails.evaluate",
)
async def evaluate(body: EvaluateIn, tenant_id: str = Depends(get_current_tenant)) -> dict[str, object]:
    """Dry-run the rules for a stage over a text: the text as the rules would leave it, and what each rule did.

    Transforms are applied to the returned text and a block is reported as
    ``allowed: false`` so the effect is visible; nothing is metered, logged or
    audited, and ``enforced`` says whether the rules currently apply live.
    """
    result = await guardrails.evaluate(
        body.stage,
        body.text,
        tenant_id=uuid.UUID(tenant_id),
        agent_id=body.agent_id,
        use_case=body.use_case,
        risk_tier=body.risk_tier,
        dry_run=True,
        context=body.context,
        user_input=body.user_input,
    )
    return result.to_dict()
