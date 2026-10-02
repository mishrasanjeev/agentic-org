# SPDX-License-Identifier: Apache-2.0
"""Residency endpoints: status, provider attestations and their revocation.

Tenant administrators only. An attestation records, per provider and data region,
that processing stays in the region and that the provider has committed not to
train on the institution's data. With residency enforcement on
(``core.governance.residency``) a provider without an active attestation for the
tenant's region is refused at the credential resolver and the other egress points.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core.database import get_tenant_session
from core.governance import residency
from core.models.provider_attestation import ProviderAttestation
from core.ownership import Caller, caller_from_request

logger = structlog.get_logger()
router = APIRouter(prefix="/residency", tags=["Residency"], dependencies=[require_tenant_admin])


class AttestationIn(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    data_region: str = Field(..., description="IN, EU or US")
    in_region: bool = False
    no_training: bool = False
    evidence_ref: str = Field("", max_length=2000)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _consistent(self) -> AttestationIn:
        self.data_region = residency.normalise_region(self.data_region)
        if self.data_region not in residency.DATA_REGIONS:
            raise ValueError(f"data_region must be one of {', '.join(residency.DATA_REGIONS)}")
        if self.expires_at is not None:
            expires = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=UTC)
            if expires <= datetime.now(UTC):
                raise ValueError("expires_at must be in the future")
            self.expires_at = expires
        return self


class AttestationOut(BaseModel):
    id: uuid.UUID
    provider: str
    data_region: str
    in_region: bool
    no_training: bool
    evidence_ref: str
    attested_by: str
    attested_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    revoked_by: str | None
    active: bool


def _actor(request: Request, caller: Caller | None) -> str:
    """The authenticated principal an attestation change is attributed to.

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
    raise HTTPException(403, "A residency attestation needs an attributable caller")


def _out(row: ProviderAttestation) -> AttestationOut:
    now = datetime.now(UTC)
    active = row.revoked_at is None and (row.expires_at is None or row.expires_at > now)
    return AttestationOut(
        id=row.id,
        provider=row.provider,
        data_region=row.data_region,
        in_region=row.in_region,
        no_training=row.no_training,
        evidence_ref=row.evidence_ref or "",
        attested_by=row.attested_by,
        attested_at=row.attested_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        revoked_by=row.revoked_by,
        active=active,
    )


@router.get("/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.residency.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="residency.status",
)
async def residency_status(tenant_id: str = Depends(get_current_tenant)) -> dict[str, object]:
    """The tenant's region, whether enforcement is on, deployment conformance and active attestations."""
    tid = uuid.UUID(tenant_id)
    return await residency.report_section(tid)


@router.get("/attestations", response_model=list[AttestationOut])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.residency.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="residency.attestations.list",
)
async def list_attestations(
    include_revoked: bool = False,
    tenant_id: str = Depends(get_current_tenant),
) -> list[AttestationOut]:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        query = select(ProviderAttestation).where(ProviderAttestation.tenant_id == tid)
        if not include_revoked:
            query = query.where(ProviderAttestation.revoked_at.is_(None))
        rows = (await session.execute(query.order_by(ProviderAttestation.attested_at.desc()))).scalars().all()
        return [_out(row) for row in rows]


@router.post("/attestations", response_model=AttestationOut, status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.residency.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="non-idempotent-create",
    audit_event="residency.attestations.set",
)
async def set_attestation(
    body: AttestationIn,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> AttestationOut:
    tid = uuid.UUID(tenant_id)
    actor_id = _actor(request, caller)
    try:
        placed = await residency.set_attestation(
            tid,
            provider=body.provider,
            data_region=body.data_region,
            in_region=body.in_region,
            no_training=body.no_training,
            evidence_ref=body.evidence_ref,
            actor_id=actor_id,
            expires_at=body.expires_at,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    async with get_tenant_session(tid) as session:
        row = (
            await session.execute(select(ProviderAttestation).where(ProviderAttestation.id == uuid.UUID(placed.id)))
        ).scalar_one()
        return _out(row)


@router.post("/attestations/{attestation_id}/revoke", response_model=AttestationOut)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="governance.residency.sensitive.write",
    rate_limit="security-admin-write",
    idempotency="idempotent-lifecycle-state",
    audit_event="residency.attestations.revoke",
)
async def revoke_attestation(
    attestation_id: uuid.UUID,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
    caller: Caller | None = Depends(caller_from_request),
) -> AttestationOut:
    tid = uuid.UUID(tenant_id)
    revoked = await residency.revoke_attestation(tid, attestation_id, actor_id=_actor(request, caller))
    if revoked is None:
        raise HTTPException(404, "No active attestation with that id")
    async with get_tenant_session(tid) as session:
        row = (
            await session.execute(select(ProviderAttestation).where(ProviderAttestation.id == attestation_id))
        ).scalar_one()
        return _out(row)
