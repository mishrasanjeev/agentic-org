# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant onboarding desk: leads, consents, opt-outs, the contact policy and signed lead intake.

Off by default (``AGENTICORG_ONBOARDING_DESK_ENABLED``): ``GET /status`` says
so and every other route is not found. Leads arrive from an operator, a CSV of
opted-in leads with an attestation of how consent was captured, or a signed
webhook from a partner form. ``GET /leads/{id}/contact-decision`` says whether
the desk may contact a lead on a channel now and, if not, why and when.
Contact details are stored encrypted; lists show masked forms only.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, get_current_user, require_tenant_admin
from api.route_metadata import route_meta
from api.v1.agents import _user_uuid_from_claims
from core.onboarding_desk import leads as intake
from core.onboarding_desk import rules, store
from core.onboarding_desk.leads import LeadError
from core.onboarding_desk.store import DeskError

router = APIRouter(prefix="/onboarding-desk", tags=["Onboarding Desk"])

MAX_WEBHOOK_BYTES = 256 * 1024
SIGNATURE_TOLERANCE_SECONDS = 300
SIGNATURE_HEADER = "x-agenticorg-signature"
PUBLIC_REASON = "partner-form-signed-webhook-hmac-verified-by-the-route-with-the-source-secret"


class PolicyIn(BaseModel):
    model_config = {"extra": "forbid"}

    channels: list[str] = Field(default_factory=lambda: list(rules.OUTREACH_CHANNELS))
    start: str | None = None
    end: str | None = None
    attempts_per_day: int | None = None
    max_reminders: int = rules.MAX_REMINDERS
    min_hours_between_reminders: int = 24
    weekends: bool = True


class SourceIn(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1, max_length=120)
    kind: str = Field(..., max_length=16)


class WithdrawIn(BaseModel):
    model_config = {"extra": "forbid"}

    channel: str = Field("all", max_length=16)
    via: str = Field("request", max_length=32)


class OptOutIn(BaseModel):
    model_config = {"extra": "forbid"}

    email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=32)
    channel: str = Field("all", max_length=16)
    reason: str = Field("request", max_length=32)


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "onboarding_desk_disabled",
            "message": "The merchant onboarding desk is off (AGENTICORG_ONBOARDING_DESK_ENABLED).",
        },
    )


def _require_on() -> None:
    if not store.enabled():
        raise _off()


def _refused(exc: DeskError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


def _invalid(exc: LeadError) -> HTTPException:
    return HTTPException(422, detail={"error": "lead_invalid", "field": exc.field, "message": exc.message})


def _actor(user: Any) -> str:
    found = _user_uuid_from_claims(user)
    return str(found) if found else ""


def _uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(404, detail={"error": f"{what}_not_found"}) from None


@router.get("/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="onboarding_desk.status",
)
async def status(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Whether the desk is on, its channels, countries, consent methods and import limits."""
    return {
        "enabled": store.enabled(),
        "channels": list(rules.CHANNELS),
        "outreach_channels": list(rules.OUTREACH_CHANNELS),
        "countries": list(rules.COUNTRIES),
        "consent_methods": list(intake.METHODS),
        "opt_out_reasons": list(store.OPT_OUT_REASONS),
        "limits": {
            "csv_rows": intake.MAX_CSV_ROWS,
            "csv_bytes": intake.MAX_CSV_BYTES,
            "webhook_leads": intake.MAX_LEADS_PER_WEBHOOK,
        },
    }


@router.get("/policy")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.policy.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="onboarding_desk.policy.read",
)
async def get_policy(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    _require_on()
    return (await store.get_policy(uuid.UUID(tenant_id))).to_dict()


@router.put("/policy", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.policy.write",
    rate_limit="standard",
    idempotency="idempotent-replaces-the-tenant-policy",
    audit_event="onboarding_desk.policy.set",
)
async def put_policy(
    body: PolicyIn, tenant_id: str = Depends(get_current_tenant), user: Any = Depends(get_current_user)
) -> dict[str, Any]:
    """Narrow the contact policy; the legal floor for hours and attempts cannot be widened."""
    _require_on()
    try:
        policy = await store.put_policy(uuid.UUID(tenant_id), body.model_dump(), actor=_actor(user))
    except DeskError as exc:
        raise _refused(exc) from exc
    return policy.to_dict()


@router.post("/lead-sources", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.sources.write",
    rate_limit="standard",
    idempotency="not_idempotent-creates-a-lead-source",
    audit_event="onboarding_desk.source.create",
)
async def create_source(
    body: SourceIn, tenant_id: str = Depends(get_current_tenant), user: Any = Depends(get_current_user)
) -> dict[str, Any]:
    """A lead source; a webhook source's signing secret is returned once and never again."""
    _require_on()
    try:
        row, secret = await store.create_source(uuid.UUID(tenant_id), body.name, body.kind, actor=_actor(user))
    except DeskError as exc:
        raise _refused(exc) from exc
    view = store.source_view(row)
    if secret:
        view["signing_secret"] = secret
        view["webhook_path"] = f"/api/v1/onboarding-desk/webhooks/leads/{tenant_id}/{row.id}"
    return view


@router.get("/lead-sources")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.sources.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="onboarding_desk.source.list",
)
async def list_sources(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    _require_on()
    return {"sources": [store.source_view(r) for r in await store.list_sources(uuid.UUID(tenant_id))]}


@router.post("/leads")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.leads.write",
    rate_limit="standard",
    idempotency="idempotent-per-source-and-external-ref",
    audit_event="onboarding_desk.lead.create",
)
async def create_lead(
    body: dict[str, Any], tenant_id: str = Depends(get_current_tenant), user: Any = Depends(get_current_user)
) -> dict[str, Any]:
    """Record one lead and the consents the person gave."""
    _require_on()
    try:
        lead = intake.lead_from(body)
    except LeadError as exc:
        raise _invalid(exc) from exc
    outcome = await store.create_lead(uuid.UUID(tenant_id), lead, source_kind="manual", actor=_actor(user))
    return {"lead": store.lead_view(outcome.lead), "created": outcome.created}


@router.post("/leads/import")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.leads.write",
    rate_limit="standard",
    idempotency="idempotent-per-source-and-external-ref",
    audit_event="onboarding_desk.lead.import",
)
async def import_leads(
    file: UploadFile,
    consent_wording: str = Form(""),
    consent_method: str = Form("csv_attestation"),
    consent_written: bool = Form(False),
    tenant_id: str = Depends(get_current_tenant),
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Import opted-in leads from CSV; consents are recorded only with an attestation of the wording."""
    _require_on()
    declared = getattr(file, "size", None)
    if isinstance(declared, int) and declared > intake.MAX_CSV_BYTES:
        raise HTTPException(413, detail={"error": "too_large"})
    data = await file.read(intake.MAX_CSV_BYTES + 1)
    attestation = None
    try:
        if consent_wording.strip():
            attestation = intake.attestation_from(
                {"wording": consent_wording, "method": consent_method, "written": consent_written}
            )
        return await store.import_csv(uuid.UUID(tenant_id), data, attestation, actor=_actor(user), source_id=None)
    except LeadError as exc:
        raise _invalid(exc) from exc


@router.get("/leads")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.leads.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="onboarding_desk.lead.list",
)
async def list_leads(
    tenant_id: str = Depends(get_current_tenant),
    status: str | None = Query(None, max_length=16),
    limit: int = Query(50, ge=1, le=store.MAX_LIST),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    _require_on()
    rows = await store.list_leads(uuid.UUID(tenant_id), status=status, limit=limit, offset=offset)
    return {"leads": [store.lead_view(r) for r in rows]}


@router.get("/leads/{lead_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.leads.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="onboarding_desk.lead.read",
)
async def get_lead(lead_id: str, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    _require_on()
    try:
        lead, consents = await store.get_lead(uuid.UUID(tenant_id), _uuid(lead_id, "lead"))
    except DeskError as exc:
        raise _refused(exc) from exc
    return {**store.lead_view(lead), "consents": [store.consent_view(c) for c in consents]}


@router.post("/leads/{lead_id}/consents")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.consents.write",
    rate_limit="standard",
    idempotency="not_idempotent-records-a-consent",
    audit_event="onboarding_desk.consent.record",
)
async def record_consent(
    lead_id: str,
    body: dict[str, Any],
    tenant_id: str = Depends(get_current_tenant),
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Record a consent the person gave on a channel, with its wording and how it was captured."""
    _require_on()
    try:
        consent = intake.consent_from(body, now=datetime.now(UTC))
        row = await store.record_consent(uuid.UUID(tenant_id), _uuid(lead_id, "lead"), consent, actor=_actor(user))
    except LeadError as exc:
        raise _invalid(exc) from exc
    except DeskError as exc:
        raise _refused(exc) from exc
    return store.consent_view(row)


@router.post("/leads/{lead_id}/consents/withdraw")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.consents.write",
    rate_limit="standard",
    idempotency="idempotent-withdrawn-consents-stay-withdrawn",
    audit_event="onboarding_desk.consent.withdraw",
)
async def withdraw_consents(
    lead_id: str,
    body: WithdrawIn,
    tenant_id: str = Depends(get_current_tenant),
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    _require_on()
    try:
        count = await store.withdraw_consents(
            uuid.UUID(tenant_id), _uuid(lead_id, "lead"), body.channel, via=body.via, actor=_actor(user)
        )
    except DeskError as exc:
        raise _refused(exc) from exc
    return {"withdrawn": count}


@router.post("/opt-outs")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.consents.write",
    rate_limit="standard",
    idempotency="idempotent-a-suppression-is-kept-once",
    audit_event="onboarding_desk.opt_out",
)
async def opt_out(body: OptOutIn, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Suppress an email or phone on a channel or everywhere, and withdraw the matching consents."""
    _require_on()
    try:
        return await store.opt_out(
            uuid.UUID(tenant_id),
            email=body.email,
            phone=body.phone,
            channel=body.channel,
            reason=body.reason,
            source="operator",
        )
    except DeskError as exc:
        raise _refused(exc) from exc


@router.get("/leads/{lead_id}/contact-decision")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.leads.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="onboarding_desk.contact_decision",
)
async def contact_decision(
    lead_id: str,
    channel: str = Query(..., max_length=16),
    reminder: bool = Query(False),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Whether the desk may contact the lead on the channel now; if not, the reason and the next permitted time."""
    _require_on()
    try:
        decision = await store.contact_decision(
            uuid.UUID(tenant_id), _uuid(lead_id, "lead"), channel.lower(), is_reminder=reminder
        )
    except DeskError as exc:
        raise _refused(exc) from exc
    return decision.to_dict()


def verify_signature(secret: str, header: str, body: bytes, *, now: float | None = None) -> bool:
    """``t=<unix seconds>,v1=<hex HMAC-SHA256 of "t." + body>`` within the tolerance."""
    parts: dict[str, list[str]] = {}
    for piece in (header or "").split(","):
        key, _, value = piece.strip().partition("=")
        if key and value:
            parts.setdefault(key, []).append(value)
    try:
        stamp = int(parts["t"][0])
    except (KeyError, ValueError, IndexError):
        return False
    if abs((now if now is not None else time.time()) - stamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in parts.get("v1", []))


@router.post("/webhooks/leads/{tenant_id}/{source_id}")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:onboarding_desk.webhook",
    rate_limit="onboarding-desk-public",
    idempotency="idempotent-per-source-and-external-ref",
    audit_event="onboarding_desk.webhook.leads",
    public_reason=PUBLIC_REASON,
)
async def webhook_leads(tenant_id: str, source_id: str, request: Request) -> dict[str, Any]:
    """Leads from a partner form, signed with the source's secret; refused unless the signature is current and valid."""
    if not store.enabled():
        raise _off()
    tid, sid = _uuid(tenant_id, "source"), _uuid(source_id, "source")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, detail={"error": "too_large"})
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, detail={"error": "too_large"})
    found = await store.webhook_secret(tid, sid)
    if found is None or not verify_signature(found[1], request.headers.get(SIGNATURE_HEADER, ""), body):
        raise HTTPException(401, detail={"error": "signature_invalid"})
    import json

    try:
        parsed = json.loads(body or b"{}")
        leads = intake.leads_from_webhook(parsed)
    except ValueError as exc:
        if isinstance(exc, LeadError):
            raise _invalid(exc) from exc
        raise HTTPException(400, detail={"error": "body_invalid"}) from exc
    created = existing = 0
    for lead in leads:
        outcome = await store.create_lead(tid, lead, source_id=sid, source_kind="webhook", actor=f"source:{sid}")
        created += int(outcome.created)
        existing += int(not outcome.created)
    return {"created": created, "existing": existing}
