"""Client-facing portal APIs for CA firm managed companies."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc, select

from api.deps import get_current_tenant, get_current_user, require_tenant_admin
from api.route_metadata import route_meta
from core.config import settings
from core.database import get_tenant_session
from core.models.ca_client_billing import CAClientInvoice
from core.models.client_portal import ClientPortalDocument, ClientPortalInvite
from core.models.company import Company
from core.models.compliance_deadline import ComplianceDeadline
from core.models.professional_tax import ProfessionalTaxReturn

router = APIRouter(prefix="/client-portal", tags=["Client Portal"])

INVITE_TOKEN_PREFIX = "aocpi_"
ACCESS_TOKEN_PREFIX = "aocpa_"
ACCESS_TOKEN_MINUTES = 8 * 60


class ClientPortalInviteCreate(BaseModel):
    company_id: uuid.UUID
    client_email: str = Field(..., min_length=3, max_length=255)
    client_name: str | None = Field(None, max_length=255)
    role: str = Field("client_admin", max_length=30)
    expires_days: int = Field(14, ge=1, le=60)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("client_email")
    @classmethod
    def _valid_email_shape(cls, value: str) -> str:
        email = value.strip().lower()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise ValueError("client_email must be a valid email address")
        return email


class ClientPortalInviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    client_email: str
    client_name: str | None
    role: str
    status: str
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    last_sent_at: datetime | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    invite_path: str | None = None
    invite_token: str | None = None


class ClientPortalDocumentIn(BaseModel):
    company_id: uuid.UUID
    title: str = Field(..., min_length=1, max_length=255)
    document_type: str = Field(..., min_length=1, max_length=80)
    filing_period: str | None = Field(None, max_length=20)
    source_type: str | None = Field(None, max_length=80)
    source_id: uuid.UUID | None = None
    file_url: str | None = Field(None, max_length=1000)
    visible_to_client: bool = True
    summary: str | None = Field(None, max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClientPortalDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    title: str
    document_type: str
    filing_period: str | None
    status: str
    source_type: str | None
    source_id: uuid.UUID | None
    file_url: str | None
    visible_to_client: bool
    summary: str | None
    uploaded_by: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ClientPortalAcceptRequest(BaseModel):
    token: str
    client_name: str | None = Field(None, max_length=255)


class ClientPortalDashboardRequest(BaseModel):
    access_token: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _sign_payload(prefix: str, payload: dict[str, Any]) -> str:
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    sig = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{prefix}{body}.{sig}"


def _decode_signed_token(token: str, prefix: str) -> dict[str, Any]:
    if not token.startswith(prefix):
        raise ValueError("token prefix is invalid")
    try:
        body, supplied_sig = token[len(prefix):].split(".", 1)
    except ValueError as exc:
        raise ValueError("token shape is invalid") from exc
    expected = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied_sig, expected):
        raise ValueError("token signature is invalid")
    payload = json.loads(_unb64(body))
    exp = int(payload.get("exp", 0))
    if exp <= int(datetime.now(UTC).timestamp()):
        raise ValueError("token has expired")
    return payload


def _new_invite_token(
    *,
    tenant_id: uuid.UUID,
    company_id: uuid.UUID,
    invite_id: uuid.UUID,
    expires_at: datetime,
) -> str:
    return _sign_payload(
        INVITE_TOKEN_PREFIX,
        {
            "kind": "client_portal_invite",
            "tenant_id": str(tenant_id),
            "company_id": str(company_id),
            "invite_id": str(invite_id),
            "nonce": secrets.token_urlsafe(18),
            "exp": int(expires_at.timestamp()),
        },
    )


def _new_access_token(invite: ClientPortalInvite) -> str:
    expires_at = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_MINUTES)
    return _sign_payload(
        ACCESS_TOKEN_PREFIX,
        {
            "kind": "client_portal_access",
            "tenant_id": str(invite.tenant_id),
            "company_id": str(invite.company_id),
            "invite_id": str(invite.id),
            "client_email": invite.client_email,
            "exp": int(expires_at.timestamp()),
        },
    )


async def _load_company_or_404(session, tenant_uuid: uuid.UUID, company_id: uuid.UUID) -> Company:
    result = await session.execute(
        select(Company).where(Company.tenant_id == tenant_uuid, Company.id == company_id)
    )
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(404, "Company not found")
    return company


def _invite_out(row: ClientPortalInvite, *, token: str | None = None) -> ClientPortalInviteOut:
    invite_path = f"/client-portal/accept?token={token}" if token else None
    return ClientPortalInviteOut(
        id=row.id,
        company_id=row.company_id,
        client_email=row.client_email,
        client_name=row.client_name,
        role=row.role,
        status=row.status,
        expires_at=row.expires_at,
        accepted_at=row.accepted_at,
        revoked_at=row.revoked_at,
        last_sent_at=row.last_sent_at,
        metadata=row.metadata_ or {},
        created_at=row.created_at,
        invite_path=invite_path,
        invite_token=token,
    )


def _document_out(row: ClientPortalDocument) -> ClientPortalDocumentOut:
    return ClientPortalDocumentOut(
        id=row.id,
        company_id=row.company_id,
        title=row.title,
        document_type=row.document_type,
        filing_period=row.filing_period,
        status=row.status,
        source_type=row.source_type,
        source_id=row.source_id,
        file_url=row.file_url,
        visible_to_client=row.visible_to_client,
        summary=row.summary,
        uploaded_by=row.uploaded_by,
        metadata=row.metadata_ or {},
        created_at=row.created_at,
    )


@router.post("/invites", response_model=ClientPortalInviteOut, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="client_portal.invites.write",
    rate_limit="standard",
    idempotency="invite-create-random-token",
    audit_event="client_portal.invites.create",
)
async def create_client_portal_invite(
    body: ClientPortalInviteCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> ClientPortalInviteOut:
    tenant_uuid = uuid.UUID(tenant_id)
    invite_id = uuid.uuid4()
    expires_at = datetime.now(UTC) + timedelta(days=body.expires_days)
    token = _new_invite_token(
        tenant_id=tenant_uuid,
        company_id=body.company_id,
        invite_id=invite_id,
        expires_at=expires_at,
    )
    async with get_tenant_session(tenant_uuid) as session:
        await _load_company_or_404(session, tenant_uuid, body.company_id)
        invite = ClientPortalInvite(
            id=invite_id,
            tenant_id=tenant_uuid,
            company_id=body.company_id,
            client_email=body.client_email.lower(),
            client_name=body.client_name,
            role=body.role,
            token_hash=_hash_token(token),
            status="pending",
            expires_at=expires_at,
            last_sent_at=datetime.now(UTC),
            metadata_=body.metadata,
        )
        session.add(invite)
        await session.flush()
        return _invite_out(invite, token=token)


@router.get("/invites", response_model=list[ClientPortalInviteOut])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="client_portal.invites.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="client_portal.invites.list",
)
async def list_client_portal_invites(
    company_id: uuid.UUID | None = Query(None),
    tenant_id: str = Depends(get_current_tenant),
) -> list[ClientPortalInviteOut]:
    tenant_uuid = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant_uuid) as session:
        stmt = select(ClientPortalInvite).where(ClientPortalInvite.tenant_id == tenant_uuid)
        if company_id:
            stmt = stmt.where(ClientPortalInvite.company_id == company_id)
        rows = (await session.execute(stmt.order_by(desc(ClientPortalInvite.created_at)))).scalars().all()
        return [_invite_out(row) for row in rows]


@router.post("/invites/{invite_id}/revoke", response_model=ClientPortalInviteOut, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="client_portal.invites.revoke",
    rate_limit="standard",
    idempotency="invite-terminal-revoke",
    audit_event="client_portal.invites.revoke",
)
async def revoke_client_portal_invite(
    invite_id: uuid.UUID,
    tenant_id: str = Depends(get_current_tenant),
) -> ClientPortalInviteOut:
    tenant_uuid = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant_uuid) as session:
        result = await session.execute(
            select(ClientPortalInvite).where(
                ClientPortalInvite.tenant_id == tenant_uuid,
                ClientPortalInvite.id == invite_id,
            )
        )
        invite = result.scalar_one_or_none()
        if invite is None:
            raise HTTPException(404, "Client portal invite not found")
        invite.status = "revoked"
        invite.revoked_at = datetime.now(UTC)
        invite.updated_at = datetime.now(UTC)
        await session.flush()
        return _invite_out(invite)


@router.post("/documents", response_model=ClientPortalDocumentOut, dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="client_portal.documents.write",
    rate_limit="standard",
    idempotency="client-document-create",
    audit_event="client_portal.documents.create",
)
async def publish_client_portal_document(
    body: ClientPortalDocumentIn,
    tenant_id: str = Depends(get_current_tenant),
    current_user: dict = Depends(get_current_user),
) -> ClientPortalDocumentOut:
    tenant_uuid = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant_uuid) as session:
        await _load_company_or_404(session, tenant_uuid, body.company_id)
        document = ClientPortalDocument(
            tenant_id=tenant_uuid,
            company_id=body.company_id,
            title=body.title,
            document_type=body.document_type,
            filing_period=body.filing_period,
            source_type=body.source_type,
            source_id=body.source_id,
            file_url=body.file_url,
            visible_to_client=body.visible_to_client,
            summary=body.summary,
            uploaded_by=current_user.get("email") or current_user.get("sub"),
            metadata_=body.metadata,
        )
        session.add(document)
        await session.flush()
        return _document_out(document)


@router.get("/documents", response_model=list[ClientPortalDocumentOut])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="client_portal.documents.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="client_portal.documents.list",
)
async def list_client_portal_documents(
    company_id: uuid.UUID | None = Query(None),
    tenant_id: str = Depends(get_current_tenant),
) -> list[ClientPortalDocumentOut]:
    tenant_uuid = uuid.UUID(tenant_id)
    async with get_tenant_session(tenant_uuid) as session:
        stmt = select(ClientPortalDocument).where(ClientPortalDocument.tenant_id == tenant_uuid)
        if company_id:
            stmt = stmt.where(ClientPortalDocument.company_id == company_id)
        rows = (await session.execute(stmt.order_by(desc(ClientPortalDocument.created_at)))).scalars().all()
        return [_document_out(row) for row in rows]


async def _load_invite_from_token(token: str, *, prefix: str) -> tuple[dict[str, Any], ClientPortalInvite]:
    try:
        payload = _decode_signed_token(token, prefix)
        tenant_uuid = uuid.UUID(payload["tenant_id"])
        invite_id = uuid.UUID(payload["invite_id"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(401, "Invalid or expired client portal token") from exc
    async with get_tenant_session(tenant_uuid) as session:
        result = await session.execute(
            select(ClientPortalInvite).where(
                ClientPortalInvite.tenant_id == tenant_uuid,
                ClientPortalInvite.id == invite_id,
            )
        )
        invite = result.scalar_one_or_none()
        if invite is None:
            raise HTTPException(401, "Client portal invite not found")
        if prefix == INVITE_TOKEN_PREFIX and not hmac.compare_digest(invite.token_hash, _hash_token(token)):
            raise HTTPException(401, "Client portal invite token was rotated or revoked")
        if invite.status == "revoked" or invite.revoked_at is not None:
            raise HTTPException(403, "Client portal invite has been revoked")
        if invite.expires_at <= datetime.now(UTC):
            invite.status = "expired"
            invite.updated_at = datetime.now(UTC)
            raise HTTPException(401, "Client portal invite has expired")
        return payload, invite


@router.post("/public/accept")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:client_portal.accept",
    rate_limit="client-portal-public",
    idempotency="invite-token-accept",
    audit_event="client_portal.public.accept",
    public_reason="signed-client-invite-token-authenticates-company-context",
)
async def accept_client_portal_invite(body: ClientPortalAcceptRequest) -> dict[str, Any]:
    payload, invite = await _load_invite_from_token(body.token, prefix=INVITE_TOKEN_PREFIX)
    tenant_uuid = uuid.UUID(payload["tenant_id"])
    async with get_tenant_session(tenant_uuid) as session:
        result = await session.execute(
            select(ClientPortalInvite).where(
                ClientPortalInvite.tenant_id == tenant_uuid,
                ClientPortalInvite.id == invite.id,
            )
        )
        invite = result.scalar_one()
        invite.status = "accepted"
        invite.accepted_at = invite.accepted_at or datetime.now(UTC)
        if body.client_name:
            invite.client_name = body.client_name
        invite.updated_at = datetime.now(UTC)
        company = await _load_company_or_404(session, tenant_uuid, invite.company_id)
        access_token = _new_access_token(invite)
        return {
            "status": "accepted",
            "access_token": access_token,
            "expires_in_minutes": ACCESS_TOKEN_MINUTES,
            "company": {
                "id": str(company.id),
                "name": company.name,
                "pan": company.pan,
                "gstin": company.gstin,
                "state_code": company.state_code,
            },
            "client": {
                "email": invite.client_email,
                "name": invite.client_name,
                "role": invite.role,
            },
        }


@router.post("/public/dashboard")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:client_portal.dashboard",
    rate_limit="client-portal-public",
    idempotency="read-only-token-scoped",
    audit_event="client_portal.public.dashboard",
    public_reason="signed-client-access-token-scopes-dashboard-to-one-company",
)
async def get_client_portal_dashboard(body: ClientPortalDashboardRequest) -> dict[str, Any]:
    payload, invite = await _load_invite_from_token(body.access_token, prefix=ACCESS_TOKEN_PREFIX)
    tenant_uuid = uuid.UUID(payload["tenant_id"])
    company_id = uuid.UUID(payload["company_id"])
    if company_id != invite.company_id:
        raise HTTPException(401, "Client portal token company mismatch")
    async with get_tenant_session(tenant_uuid) as session:
        company = await _load_company_or_404(session, tenant_uuid, company_id)
        docs = (await session.execute(
            select(ClientPortalDocument)
            .where(
                ClientPortalDocument.tenant_id == tenant_uuid,
                ClientPortalDocument.company_id == company_id,
                ClientPortalDocument.visible_to_client.is_(True),
            )
            .order_by(desc(ClientPortalDocument.created_at))
            .limit(25)
        )).scalars().all()
        invoices = (await session.execute(
            select(CAClientInvoice)
            .where(
                CAClientInvoice.tenant_id == tenant_uuid,
                CAClientInvoice.company_id == company_id,
                CAClientInvoice.status.in_(["sent", "part_paid", "overdue", "paid"]),
            )
            .order_by(desc(CAClientInvoice.issue_date))
            .limit(25)
        )).scalars().all()
        deadlines = (await session.execute(
            select(ComplianceDeadline)
            .where(
                ComplianceDeadline.tenant_id == tenant_uuid,
                ComplianceDeadline.company_id == company_id,
                ComplianceDeadline.filed.is_(False),
            )
            .order_by(ComplianceDeadline.due_date)
            .limit(10)
        )).scalars().all()
        pt_returns = (await session.execute(
            select(ProfessionalTaxReturn)
            .where(
                ProfessionalTaxReturn.tenant_id == tenant_uuid,
                ProfessionalTaxReturn.company_id == company_id,
            )
            .order_by(desc(ProfessionalTaxReturn.created_at))
            .limit(10)
        )).scalars().all()

        return {
            "company": {
                "id": str(company.id),
                "name": company.name,
                "pan": company.pan,
                "gstin": company.gstin,
                "state_code": company.state_code,
                "compliance_email": company.compliance_email,
            },
            "documents": [_document_out(row).model_dump(mode="json") for row in docs],
            "invoices": [
                {
                    "id": str(row.id),
                    "invoice_number": row.invoice_number,
                    "issue_date": row.issue_date.isoformat(),
                    "due_date": row.due_date.isoformat(),
                    "currency": row.currency,
                    "total": str(row.total),
                    "balance_due": str(row.balance_due),
                    "status": row.status,
                    "line_items": row.line_items or [],
                }
                for row in invoices
            ],
            "upcoming_deadlines": [
                {
                    "id": str(row.id),
                    "deadline_type": row.deadline_type,
                    "filing_period": row.filing_period,
                    "due_date": row.due_date.isoformat(),
                }
                for row in deadlines
            ],
            "professional_tax_returns": [
                {
                    "id": str(row.id),
                    "state_code": row.state_code,
                    "filing_period": row.filing_period,
                    "total_payable": str(row.total_payable),
                    "status": row.status,
                    "challan_number": row.challan_number,
                    "acknowledgement_number": row.acknowledgement_number,
                }
                for row in pt_returns
            ],
        }
