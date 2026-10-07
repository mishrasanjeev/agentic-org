# SPDX-License-Identifier: Apache-2.0
"""The policy console: every policy of the tenant in one list, written and dry-run in one place.

The console stores nothing of its own: a policy written here is the same row
the model gateway, the guardrails or the approval policies read at their
enforcement point (``core/governance/policy_console.py``).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from api.v1 import approval_policies as approval_api
from api.v1 import guardrails as guardrails_api
from api.v1 import model_gateway as gateway_api
from core.database import get_tenant_session
from core.governance import policy_console
from core.ownership import Caller, caller_from_request

logger = structlog.get_logger()
router = APIRouter()

_INPUTS: dict[str, type[BaseModel]] = {
    "model_routing": gateway_api.PolicyIn,
    "model_access": gateway_api.AccessPolicyIn,
    "model_limit": gateway_api.LimitIn,
    "guardrail": guardrails_api.RuleIn,
    "approval": approval_api.PolicyIn,
}


class PolicyWriteIn(BaseModel):
    model_config = {"extra": "forbid"}

    kind: str = Field(..., max_length=32)
    policy: dict[str, Any]


class PolicyEvaluateIn(BaseModel):
    model_config = {"extra": "forbid"}

    use_case: str = Field("console", min_length=1, max_length=64)
    agent_id: str | None = Field(None, max_length=64)
    sensitivity: str | None = Field(None, max_length=32)
    business_unit: str | None = Field(None, max_length=64)
    language: str | None = Field(None, max_length=16)
    requested_provider: str | None = Field(None, max_length=64)
    requested_model: str = Field("", max_length=128)
    stage: str | None = Field(None, max_length=16)
    text: str | None = Field(None, max_length=200_000)
    risk_tier: str | None = Field(None, max_length=16)
    context: list[Annotated[str, Field(max_length=50_000)]] | None = Field(None, max_length=50)
    tool: str | None = Field(None, max_length=200)
    domain: str | None = Field(None, max_length=50)
    workflow_id: str | None = Field(None, max_length=64)


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "governance_policy_console_disabled",
            "message": "The policy console is off for this deployment (AGENTICORG_GOVERNANCE_POLICY_CONSOLE_ENABLED).",
        },
    )


def _refused(exc: policy_console.PolicyConsoleError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


@router.get("/governance/policies", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.policies.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="governance.policies.list",
)
async def list_policies(
    kind: str | None = Query(None, max_length=32),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Every policy of the tenant in one shape: kind, name, enabled, priority, scope, effect and enforcement point."""
    if not policy_console.enabled():
        raise _off()
    if kind is not None and kind not in policy_console.KINDS:
        raise HTTPException(
            422, detail={"error": "unknown_kind", "message": f"kind is one of {', '.join(policy_console.KINDS)}"}
        )
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        entries = await policy_console.list_policies(session, tid, kind=kind)
    return {
        "policies": entries,
        "total": len(entries),
        "enforcement_points": dict(policy_console.ENFORCEMENT),
        "kinds": list(policy_console.KINDS),
    }


@router.post("/governance/policies", dependencies=[require_tenant_admin], status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.policies.sensitive.write",
    rate_limit="standard",
    idempotency="not-idempotent-create",
    audit_event="governance.policies.write",
)
async def write_policy(
    body: PolicyWriteIn,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> dict[str, Any]:
    """Write a policy of any kind; it lands in the store its enforcement point reads."""
    if not policy_console.enabled():
        raise _off()
    schema = _INPUTS.get(body.kind)
    if schema is None:
        raise HTTPException(422, detail={"error": "unknown_kind", "message": f"kind is one of {', '.join(_INPUTS)}"})
    try:
        fields = schema(**body.policy).model_dump()
    except ValidationError as exc:
        raise HTTPException(422, detail={"error": "invalid_policy", "message": str(exc)[:2000]}) from None
    actor_id = gateway_api._actor(request, caller)
    tid = uuid.UUID(tenant_id)
    try:
        async with get_tenant_session(tid) as session:
            entry = await policy_console.write_policy(session, tid, body.kind, fields, actor_id=actor_id)
    except policy_console.PolicyConsoleError as exc:
        raise _refused(exc) from None
    return entry


@router.delete("/governance/policies/{kind}/{policy_id}", dependencies=[require_tenant_admin], status_code=204)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.policies.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-delete",
    audit_event="governance.policies.delete",
)
async def delete_policy(
    kind: str,
    policy_id: uuid.UUID,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> None:
    """Remove a policy of any kind from the store its enforcement point reads."""
    if not policy_console.enabled():
        raise _off()
    if kind not in _INPUTS:
        raise HTTPException(422, detail={"error": "unknown_kind", "message": f"kind is one of {', '.join(_INPUTS)}"})
    actor_id = gateway_api._actor(request, caller)
    tid = uuid.UUID(tenant_id)
    try:
        async with get_tenant_session(tid) as session:
            removed = await policy_console.delete_policy(session, tid, kind, policy_id, actor_id=actor_id)
    except policy_console.PolicyConsoleError as exc:
        raise _refused(exc) from None
    if not removed:
        raise HTTPException(404, detail={"error": "not_found", "message": "No such policy"})
    return None


@router.post("/governance/policies/evaluate", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.policies.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="governance.policies.evaluate",
)
async def evaluate_policies(
    body: PolicyEvaluateIn,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """A dry run across every enforcement point: what the policies would do to this call, text, tool or workflow."""
    if not policy_console.enabled():
        raise _off()
    tid = uuid.UUID(tenant_id)
    try:
        async with get_tenant_session(tid) as session:
            return await policy_console.evaluate(session, tid, body.model_dump())
    except policy_console.PolicyConsoleError as exc:
        raise _refused(exc) from None
