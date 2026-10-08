# SPDX-License-Identifier: Apache-2.0
"""Personalisation: consents, profiles, rules, and content rendered only under a valid consent.

``PUT /personalisation/consents`` records a grant with its evidence and
expiry, ``POST /personalisation/consents/withdraw`` withdraws it (the row
stays), ``GET /personalisation/consents`` lists a subject's. Profiles are
kept encrypted (``PUT``/``GET /personalisation/profiles``). Rules select a
variant by conditions (``/personalisation/rules``).
``POST /personalisation/render`` renders content for a subject and
purpose, refusing without a valid consent, and records what it did and
which attributes it used; ``GET /personalisation/events`` reads that
record. Off, the status route says so and the rest is not found.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, get_current_user
from api.route_metadata import route_meta
from api.v1.agents import _user_uuid_from_claims
from core.personalisation import rules as checks
from core.personalisation import service
from core.personalisation.rules import PersonalisationError

router = APIRouter(prefix="/personalisation", tags=["Personalisation"])

SubjectRef = Annotated[str, Field(min_length=1, max_length=128)]
Purpose = Annotated[str, Field(min_length=1, max_length=64)]


class ConsentIn(BaseModel):
    model_config = {"extra": "forbid"}

    subject_ref: SubjectRef
    purpose: Purpose
    evidence: str = Field(..., min_length=1, max_length=checks.MAX_EVIDENCE)
    expires_at: datetime | None = None


class WithdrawIn(BaseModel):
    model_config = {"extra": "forbid"}

    subject_ref: SubjectRef
    purpose: Purpose


class ProfileIn(BaseModel):
    model_config = {"extra": "forbid"}

    subject_ref: SubjectRef
    attributes: dict[str, Any] = Field(..., max_length=checks.MAX_PROFILE_ATTRIBUTES)


class RuleIn(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1, max_length=checks.MAX_RULE_NAME)
    purpose: Purpose
    priority: int = Field(100, ge=0, le=checks.MAX_PRIORITY)
    enabled: bool = True
    conditions: list[dict[str, Any]] = Field(default_factory=list, max_length=checks.MAX_CONDITIONS)
    variant: dict[str, Any]
    allowed_attributes: list[str] = Field(default_factory=list, max_length=checks.MAX_ALLOWED)


class RulePatch(BaseModel):
    model_config = {"extra": "forbid"}

    purpose: Purpose | None = None
    priority: int | None = Field(None, ge=0, le=checks.MAX_PRIORITY)
    enabled: bool | None = None
    conditions: list[dict[str, Any]] | None = Field(None, max_length=checks.MAX_CONDITIONS)
    variant: dict[str, Any] | None = None
    allowed_attributes: list[str] | None = Field(None, max_length=checks.MAX_ALLOWED)


class RenderIn(BaseModel):
    model_config = {"extra": "forbid"}

    subject_ref: SubjectRef
    purpose: Purpose
    channel: str = Field(..., min_length=1, max_length=32)
    template: str | None = Field(None, min_length=1, max_length=checks.MAX_TEMPLATE)
    rule: str | None = Field(None, min_length=1, max_length=checks.MAX_RULE_NAME)
    preview: bool = False


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "personalisation_disabled",
            "message": "Personalisation is off (AGENTICORG_PERSONALISATION_ENABLED).",
        },
    )


def _refused(exc: PersonalisationError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


def _actor(user: Any) -> str:
    """The user's id from the session, or the empty string when the session names none."""
    found = _user_uuid_from_claims(user)
    return str(found) if found else ""


@router.get("/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="personalisation.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="personalisation.status",
)
async def status(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Whether personalisation is on, the purposes, the condition operators, the channels and the bounds."""
    return {
        "enabled": service.enabled(),
        "purposes": list(checks.PURPOSES),
        "ops": list(checks.OPS),
        "channels": list(checks.CHANNELS),
        "limits": {
            "template": checks.MAX_TEMPLATE,
            "output": checks.MAX_OUTPUT,
            "profile_attributes": checks.MAX_PROFILE_ATTRIBUTES,
            "profile_json": checks.MAX_PROFILE_JSON,
            "conditions": checks.MAX_CONDITIONS,
            "allowed_attributes": checks.MAX_ALLOWED,
            "rules": checks.MAX_RULES,
            "events": service.MAX_EVENTS,
        },
    }


# ---------------------------------------------------------------- consents


@router.put("/consents")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="personalisation.write",
    rate_limit="standard",
    idempotency="idempotent-upsert-by-subject-and-purpose",
    audit_event="personalisation.consents.grant",
)
async def grant_consent(
    body: ConsentIn, tenant_id: str = Depends(get_current_tenant), user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    """Record a subject's consent for a purpose, with where it was captured and when it expires."""
    if not service.enabled():
        raise _off()
    try:
        return await service.grant_consent(
            uuid.UUID(tenant_id),
            body.subject_ref,
            body.purpose,
            evidence=body.evidence,
            expires_at=body.expires_at,
            actor=_actor(user),
        )
    except PersonalisationError as exc:
        raise _refused(exc) from None


@router.post("/consents/withdraw")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="personalisation.write",
    rate_limit="standard",
    idempotency="idempotent-withdrawal",
    audit_event="personalisation.consents.withdraw",
)
async def withdraw_consent(
    body: WithdrawIn, tenant_id: str = Depends(get_current_tenant), user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    """Withdraw a subject's consent for a purpose; the record stays, marked withdrawn."""
    if not service.enabled():
        raise _off()
    try:
        return await service.withdraw_consent(uuid.UUID(tenant_id), body.subject_ref, body.purpose, actor=_actor(user))
    except PersonalisationError as exc:
        raise _refused(exc) from None


@router.get("/consents")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="personalisation.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="personalisation.consents.list",
)
async def list_consents(
    subject_ref: Annotated[str, Query(min_length=1, max_length=128)],
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """A subject's consents by purpose, each with whether it is valid now."""
    if not service.enabled():
        raise _off()
    try:
        found = await service.list_consents(uuid.UUID(tenant_id), subject_ref)
    except PersonalisationError as exc:
        raise _refused(exc) from None
    return {"consents": found, "total": len(found)}


# ---------------------------------------------------------------- profiles


@router.put("/profiles")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="personalisation.write",
    rate_limit="standard",
    idempotency="idempotent-replace-by-subject",
    audit_event="personalisation.profiles.put",
)
async def put_profile(
    body: ProfileIn, tenant_id: str = Depends(get_current_tenant), user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    """Replace a subject's attributes (flat names to values), kept encrypted; answers the names only."""
    if not service.enabled():
        raise _off()
    try:
        return await service.put_profile(uuid.UUID(tenant_id), body.subject_ref, body.attributes, actor=_actor(user))
    except PersonalisationError as exc:
        raise _refused(exc) from None


@router.get("/profiles")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="personalisation.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="personalisation.profiles.get",
)
async def get_profile(
    subject_ref: Annotated[str, Query(min_length=1, max_length=128)],
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """A subject's attributes, decrypted for an authorised reader."""
    if not service.enabled():
        raise _off()
    try:
        return await service.get_profile(uuid.UUID(tenant_id), subject_ref)
    except PersonalisationError as exc:
        raise _refused(exc) from None


# ---------------------------------------------------------------- rules


@router.get("/rules")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="personalisation.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="personalisation.rules.list",
)
async def list_rules(
    purpose: Annotated[str | None, Query(max_length=64)] = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """The tenant's rules by purpose and priority."""
    if not service.enabled():
        raise _off()
    try:
        found = await service.list_rules(uuid.UUID(tenant_id), purpose=purpose)
    except PersonalisationError as exc:
        raise _refused(exc) from None
    return {"rules": found, "total": len(found)}


@router.post("/rules", status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="personalisation.write",
    rate_limit="standard",
    idempotency="conflict-on-duplicate-name",
    audit_event="personalisation.rules.create",
)
async def create_rule(
    body: RuleIn, tenant_id: str = Depends(get_current_tenant), user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    """Keep a rule: its purpose, priority, conditions, variant and the attributes it may use."""
    if not service.enabled():
        raise _off()
    try:
        return await service.create_rule(uuid.UUID(tenant_id), body.model_dump(), actor=_actor(user))
    except PersonalisationError as exc:
        raise _refused(exc) from None


@router.patch("/rules/{rule_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="personalisation.write",
    rate_limit="standard",
    idempotency="idempotent-update",
    audit_event="personalisation.rules.update",
)
async def update_rule(
    rule_id: uuid.UUID,
    body: RulePatch,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Change a rule; the changed rule is checked whole."""
    if not service.enabled():
        raise _off()
    try:
        return await service.update_rule(
            uuid.UUID(tenant_id), rule_id, body.model_dump(exclude_unset=True), actor=_actor(user)
        )
    except PersonalisationError as exc:
        raise _refused(exc) from None


@router.delete("/rules/{rule_id}", status_code=204)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="personalisation.write",
    rate_limit="standard",
    idempotency="idempotent-delete",
    audit_event="personalisation.rules.delete",
)
async def delete_rule(
    rule_id: uuid.UUID, tenant_id: str = Depends(get_current_tenant), user: dict = Depends(get_current_user)
) -> Response:
    """Remove a rule; the events that name it stay, with the rule cleared."""
    if not service.enabled():
        raise _off()
    try:
        await service.delete_rule(uuid.UUID(tenant_id), rule_id, actor=_actor(user))
    except PersonalisationError as exc:
        raise _refused(exc) from None
    return Response(status_code=204)


# ---------------------------------------------------------------- rendering


@router.post("/render")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="personalisation.write",
    rate_limit="standard",
    idempotency="not_idempotent-each-render-is-recorded",
    audit_event="personalisation.render",
)
async def render(
    body: RenderIn, tenant_id: str = Depends(get_current_tenant), user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    """Content for a subject and purpose under a valid consent; a preview evaluates the same and records nothing."""
    if not service.enabled():
        raise _off()
    try:
        return await service.render(
            uuid.UUID(tenant_id),
            body.subject_ref,
            body.purpose,
            template=body.template,
            rule=body.rule,
            channel=body.channel,
            actor=_actor(user),
            preview=body.preview,
        )
    except PersonalisationError as exc:
        raise _refused(exc) from None


@router.get("/events")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="personalisation.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="personalisation.events.list",
)
async def list_events(
    subject_ref: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=service.MAX_EVENTS)] = 50,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """What was rendered or refused, newest first: the consent, the rule, the attribute names and the hash."""
    if not service.enabled():
        raise _off()
    try:
        found = await service.list_events(uuid.UUID(tenant_id), subject_ref=subject_ref, limit=limit)
    except PersonalisationError as exc:
        raise _refused(exc) from None
    return {"events": found, "total": len(found)}
