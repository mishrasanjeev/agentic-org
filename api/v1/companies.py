"""Multi-company support -- CA firm use case (one tenant, many client companies).

Database-backed CRUD with tenant-scoped RLS, role management, and onboarding.
"""

from __future__ import annotations

import enum
import logging
import uuid as _uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from api.deps import get_current_tenant, get_current_user
from core.database import get_tenant_session
from core.models.company import Company

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CompanyRole(enum.StrEnum):
    partner = "partner"
    manager = "manager"
    senior_associate = "senior_associate"
    associate = "associate"
    audit_reviewer = "audit_reviewer"


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CompanyCreate(BaseModel):
    name: str = Field(..., max_length=255)
    gstin: str | None = Field(None, max_length=15)
    pan: str = Field(..., max_length=10)
    tan: str | None = Field(None, max_length=10)
    cin: str | None = Field(None, max_length=21)
    state_code: str | None = Field(None, max_length=2)
    industry: str | None = Field(None, max_length=100)
    registered_address: str | None = None
    signatory_name: str | None = Field(None, max_length=255)
    signatory_designation: str | None = Field(None, max_length=100)
    signatory_email: str | None = Field(None, max_length=255)
    compliance_email: str | None = Field(None, max_length=255)
    dsc_serial: str | None = None
    dsc_expiry: str | None = None
    pf_registration: str | None = None
    esi_registration: str | None = None
    pt_registration: str | None = None
    bank_name: str | None = None
    bank_account_number: str | None = None
    bank_ifsc: str | None = None
    bank_branch: str | None = None
    tally_config: dict | None = None
    gst_auto_file: bool = False


class CompanyUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = None
    gstin: str | None = None
    pan: str | None = None
    tan: str | None = None
    cin: str | None = None
    state_code: str | None = None
    industry: str | None = None
    registered_address: str | None = None
    signatory_name: str | None = None
    signatory_designation: str | None = None
    signatory_email: str | None = None
    compliance_email: str | None = None
    dsc_serial: str | None = None
    dsc_expiry: str | None = None
    pf_registration: str | None = None
    esi_registration: str | None = None
    pt_registration: str | None = None
    bank_name: str | None = None
    bank_account_number: str | None = None
    bank_ifsc: str | None = None
    bank_branch: str | None = None
    tally_config: dict | None = None
    gst_auto_file: bool | None = None
    is_active: bool | None = None


class CompanyOnboard(BaseModel):
    """Onboarding request -- creates company + sets up default config."""

    name: str = Field(..., max_length=255)
    gstin: str | None = Field(None, max_length=15)
    pan: str = Field(..., max_length=10)
    tan: str | None = Field(None, max_length=10)
    cin: str | None = Field(None, max_length=21)
    state_code: str | None = Field(None, max_length=2)
    industry: str | None = Field(None, max_length=100)
    registered_address: str | None = None
    signatory_name: str | None = Field(None, max_length=255)
    signatory_designation: str | None = Field(None, max_length=100)
    compliance_email: str | None = Field(None, max_length=255)
    bank_name: str | None = None
    bank_account_number: str | None = None
    bank_ifsc: str | None = None
    pf_registration: str | None = None
    esi_registration: str | None = None


class RoleMapping(BaseModel):
    user_id: str
    role: CompanyRole


class RoleUpdateRequest(BaseModel):
    """Bulk role update for a company."""

    roles: list[RoleMapping]


class CompanyOut(BaseModel):
    id: str
    name: str
    gstin: str | None = None
    pan: str | None = None
    tan: str | None = None
    cin: str | None = None
    state_code: str | None = None
    industry: str | None = None
    registered_address: str | None = None
    signatory_name: str | None = None
    signatory_designation: str | None = None
    signatory_email: str | None = None
    compliance_email: str | None = None
    dsc_serial: str | None = None
    dsc_expiry: str | None = None
    pf_registration: str | None = None
    esi_registration: str | None = None
    pt_registration: str | None = None
    bank_name: str | None = None
    bank_account_number: str | None = None
    bank_ifsc: str | None = None
    bank_branch: str | None = None
    tally_config: dict | None = None
    gst_auto_file: bool = False
    is_active: bool = True
    subscription_status: str = "trial"
    client_health_score: int | None = None
    document_vault_enabled: bool = True
    compliance_alerts_email: str | None = None
    user_roles: dict = {}
    created_at: str
    updated_at: str | None = None


class CompanyListOut(BaseModel):
    items: list[CompanyOut]
    total: int
    page: int = 1
    per_page: int = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _company_to_out(c: Company) -> CompanyOut:
    return CompanyOut(
        id=str(c.id),
        name=c.name,
        gstin=c.gstin,
        pan=c.pan,
        tan=c.tan,
        cin=c.cin,
        state_code=c.state_code,
        industry=c.industry,
        registered_address=c.registered_address,
        signatory_name=c.signatory_name,
        signatory_designation=c.signatory_designation,
        signatory_email=c.signatory_email,
        compliance_email=c.compliance_email,
        dsc_serial=c.dsc_serial,
        dsc_expiry=str(c.dsc_expiry) if c.dsc_expiry else None,
        pf_registration=c.pf_registration,
        esi_registration=c.esi_registration,
        pt_registration=c.pt_registration,
        bank_name=c.bank_name,
        bank_account_number=c.bank_account_number,
        bank_ifsc=c.bank_ifsc,
        bank_branch=c.bank_branch,
        tally_config=c.tally_config,
        gst_auto_file=c.gst_auto_file,
        is_active=c.is_active,
        subscription_status=c.subscription_status or "trial",
        client_health_score=c.client_health_score,
        document_vault_enabled=c.document_vault_enabled if c.document_vault_enabled is not None else True,
        compliance_alerts_email=c.compliance_alerts_email,
        user_roles=c.user_roles or {},
        created_at=c.created_at.isoformat() if c.created_at else "",
        updated_at=c.updated_at.isoformat() if c.updated_at else None,
    )


# ---------------------------------------------------------------------------
# LIST
# ---------------------------------------------------------------------------


@router.get("/companies", response_model=CompanyListOut)
async def list_companies(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    industry: str | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
):
    """List all companies for the current tenant with optional filters."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        base = select(Company).where(Company.tenant_id == tid)
        count_q = select(func.count()).select_from(Company).where(Company.tenant_id == tid)

        if industry:
            base = base.where(Company.industry.ilike(f"%{industry}%"))
            count_q = count_q.where(Company.industry.ilike(f"%{industry}%"))

        if is_active is not None:
            base = base.where(Company.is_active == is_active)
            count_q = count_q.where(Company.is_active == is_active)

        if search:
            pattern = f"%{search}%"
            search_filter = Company.name.ilike(pattern) | Company.gstin.ilike(pattern)
            base = base.where(search_filter)
            count_q = count_q.where(search_filter)

        total = (await session.execute(count_q)).scalar() or 0

        query = (
            base.order_by(Company.name)
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        result = await session.execute(query)
        companies = result.scalars().all()

    return CompanyListOut(
        items=[_company_to_out(c) for c in companies],
        total=total,
        page=page,
        per_page=per_page,
    )


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------


@router.post("/companies", response_model=CompanyOut, status_code=201)
async def create_company(
    body: CompanyCreate,
    tenant_id: str = Depends(get_current_tenant),
):
    """Create a new client company under this tenant."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        # Check GSTIN uniqueness within tenant
        if body.gstin:
            existing = await session.execute(
                select(Company.id).where(
                    Company.tenant_id == tid,
                    Company.gstin == body.gstin,
                ).limit(1)
            )
            if existing.scalar_one_or_none():
                raise HTTPException(409, f"Company with GSTIN {body.gstin} already exists")

        company = Company(
            tenant_id=tid,
            name=body.name,
            gstin=body.gstin,
            pan=body.pan,
            tan=body.tan,
            cin=body.cin,
            state_code=body.state_code,
            industry=body.industry,
            registered_address=body.registered_address,
            signatory_name=body.signatory_name,
            signatory_designation=body.signatory_designation,
            signatory_email=body.signatory_email,
            compliance_email=body.compliance_email,
            dsc_serial=body.dsc_serial,
            pf_registration=body.pf_registration,
            esi_registration=body.esi_registration,
            pt_registration=body.pt_registration,
            bank_name=body.bank_name,
            bank_account_number=body.bank_account_number,
            bank_ifsc=body.bank_ifsc,
            bank_branch=body.bank_branch,
            tally_config=body.tally_config,
            gst_auto_file=body.gst_auto_file,
        )
        session.add(company)
        await session.flush()
        from core.agents.packs.installer import (
            is_pack_installed_for_session,
            sync_company_pack_assets_for_session,
        )

        if await is_pack_installed_for_session(session, tid, "ca-firm"):
            await sync_company_pack_assets_for_session(
                session,
                tid,
                "ca-firm",
                company.id,
                company.name,
            )
        await session.refresh(company)
        out = _company_to_out(company)

    return out


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


@router.get("/companies/{company_id}", response_model=CompanyOut)
async def get_company(
    company_id: str,
    tenant_id: str = Depends(get_current_tenant),
):
    """Get details of a specific company."""
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid company_id format") from e

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Company).where(Company.id == cid, Company.tenant_id == tid)
        )
        company = result.scalar_one_or_none()
        if not company:
            raise HTTPException(404, "Company not found")
        return _company_to_out(company)


# ---------------------------------------------------------------------------
# UPDATE (PATCH)
# ---------------------------------------------------------------------------


@router.patch("/companies/{company_id}", response_model=CompanyOut)
async def update_company(
    company_id: str,
    body: CompanyUpdate,
    tenant_id: str = Depends(get_current_tenant),
):
    """Update a company's details (partial update)."""
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid company_id format") from e

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Company).where(Company.id == cid, Company.tenant_id == tid)
        )
        company = result.scalar_one_or_none()
        if not company:
            raise HTTPException(404, "Company not found")

        updates = body.model_dump(exclude_unset=True)
        if not updates:
            return _company_to_out(company)

        # If GSTIN is being changed, check uniqueness
        if "gstin" in updates and updates["gstin"] and updates["gstin"] != company.gstin:
            dup = await session.execute(
                select(Company.id).where(
                    Company.tenant_id == tid,
                    Company.gstin == updates["gstin"],
                    Company.id != cid,
                ).limit(1)
            )
            if dup.scalar_one_or_none():
                raise HTTPException(409, f"Company with GSTIN {updates['gstin']} already exists")

        for key, value in updates.items():
            setattr(company, key, value)

        # Manually set updated_at since onupdate only fires on session-level flush
        company.updated_at = datetime.utcnow()  # noqa: DTZ003
        session.add(company)
        await session.flush()
        await session.refresh(company)
        return _company_to_out(company)


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


@router.delete("/companies/{company_id}", status_code=204)
async def delete_company(
    company_id: str,
    tenant_id: str = Depends(get_current_tenant),
):
    """Soft-delete a company (sets is_active=False).

    Hard delete is intentionally not supported to preserve audit trails.
    """
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid company_id format") from e

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Company).where(Company.id == cid, Company.tenant_id == tid)
        )
        company = result.scalar_one_or_none()
        if not company:
            raise HTTPException(404, "Company not found")

        company.is_active = False
        company.updated_at = datetime.utcnow()  # noqa: DTZ003
        session.add(company)


# ---------------------------------------------------------------------------
# ROLES -- GET
# ---------------------------------------------------------------------------


@router.get("/companies/{company_id}/roles")
async def get_company_roles(
    company_id: str,
    tenant_id: str = Depends(get_current_tenant),
):
    """Get user-company role mappings for a company."""
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid company_id format") from e

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Company).where(Company.id == cid, Company.tenant_id == tid)
        )
        company = result.scalar_one_or_none()
        if not company:
            raise HTTPException(404, "Company not found")

        roles = company.user_roles or {}
        return {
            "company_id": str(company.id),
            "company_name": company.name,
            "roles": [
                {"user_id": uid, "role": role}
                for uid, role in roles.items()
            ],
            "valid_roles": [r.value for r in CompanyRole],
        }


# ---------------------------------------------------------------------------
# ROLES -- PUT (bulk update)
# ---------------------------------------------------------------------------


@router.put("/companies/{company_id}/roles")
async def update_company_roles(
    company_id: str,
    body: RoleUpdateRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    """Update user-company role mappings (replaces existing mapping for
    each listed user; unlisted users keep their current role).
    """
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid company_id format") from e

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Company).where(Company.id == cid, Company.tenant_id == tid)
        )
        company = result.scalar_one_or_none()
        if not company:
            raise HTTPException(404, "Company not found")

        current_roles = dict(company.user_roles or {})
        for mapping in body.roles:
            current_roles[mapping.user_id] = mapping.role.value

        company.user_roles = current_roles
        company.updated_at = datetime.utcnow()  # noqa: DTZ003
        session.add(company)
        await session.flush()
        await session.refresh(company)

        return {
            "company_id": str(company.id),
            "company_name": company.name,
            "roles": [
                {"user_id": uid, "role": role}
                for uid, role in company.user_roles.items()
            ],
        }


# ---------------------------------------------------------------------------
# ONBOARD -- POST (create company + default config)
# ---------------------------------------------------------------------------


@router.post("/companies/onboard", response_model=CompanyOut, status_code=201)
async def onboard_company(
    body: CompanyOnboard,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
):
    """Onboard a new client company -- creates the company record and
    sets up default configuration (FY dates, assigns current user as
    partner, enables standard compliance settings).
    """
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        # Check GSTIN uniqueness
        if body.gstin:
            existing = await session.execute(
                select(Company.id).where(
                    Company.tenant_id == tid,
                    Company.gstin == body.gstin,
                ).limit(1)
            )
            if existing.scalar_one_or_none():
                raise HTTPException(409, f"Company with GSTIN {body.gstin} already exists")

        # Assign current user as partner by default
        user_email = user.get("sub", "")
        user_roles: dict[str, str] = {}
        if user_email:
            user_roles[user_email] = CompanyRole.partner.value

        # Default Tally config stub
        default_tally: dict = {
            "bridge_url": "",
            "bridge_id": "",
            "company_name": body.name,
            "auto_sync": False,
        }

        company = Company(
            tenant_id=tid,
            name=body.name,
            gstin=body.gstin,
            pan=body.pan,
            tan=body.tan,
            cin=body.cin,
            state_code=body.state_code,
            industry=body.industry,
            registered_address=body.registered_address,
            signatory_name=body.signatory_name,
            signatory_designation=body.signatory_designation,
            compliance_email=body.compliance_email,
            bank_name=body.bank_name,
            bank_account_number=body.bank_account_number,
            bank_ifsc=body.bank_ifsc,
            pf_registration=body.pf_registration,
            esi_registration=body.esi_registration,
            tally_config=default_tally,
            user_roles=user_roles,
            # India FY defaults
            fy_start_month="04",
            fy_end_month="03",
        )
        session.add(company)
        await session.flush()
        from core.agents.packs.installer import (
            is_pack_installed_for_session,
            sync_company_pack_assets_for_session,
        )

        if await is_pack_installed_for_session(session, tid, "ca-firm"):
            await sync_company_pack_assets_for_session(
                session,
                tid,
                "ca-firm",
                company.id,
                company.name,
            )
        await session.refresh(company)
        out = _company_to_out(company)

    logger.info("Onboarded company %s (%s) for tenant %s", body.name, body.gstin, tenant_id)
    return out


# ===========================================================================
# Filing Approval schemas
# ===========================================================================

from core.models.ca_subscription import CASubscription  # noqa: E402
from core.models.filing_approval import FilingApproval  # noqa: E402
from core.models.gstn_upload import GSTNUpload  # noqa: E402


class FilingApprovalCreate(BaseModel):
    filing_type: str = Field(..., max_length=50)
    filing_period: str = Field(..., max_length=20)
    filing_data: dict = Field(default_factory=dict)


class FilingApprovalOut(BaseModel):
    id: str
    company_id: str
    filing_type: str
    filing_period: str
    filing_data: dict = {}
    status: str
    requested_by: str
    approved_by: str | None = None
    approved_at: str | None = None
    rejection_reason: str | None = None
    auto_approved: bool = False
    created_at: str
    updated_at: str | None = None


class FilingApprovalListOut(BaseModel):
    items: list[FilingApprovalOut]
    total: int


def _approval_to_out(a: FilingApproval) -> FilingApprovalOut:
    return FilingApprovalOut(
        id=str(a.id),
        company_id=str(a.company_id),
        filing_type=a.filing_type,
        filing_period=a.filing_period,
        filing_data=a.filing_data or {},
        status=a.status,
        requested_by=a.requested_by,
        approved_by=a.approved_by,
        approved_at=a.approved_at.isoformat() if a.approved_at else None,
        rejection_reason=a.rejection_reason,
        auto_approved=a.auto_approved,
        created_at=a.created_at.isoformat() if a.created_at else "",
        updated_at=a.updated_at.isoformat() if a.updated_at else None,
    )


# ===========================================================================
# GSTN Upload schemas
# ===========================================================================


class GSTNUploadCreate(BaseModel):
    upload_type: str = Field(..., max_length=50)
    filing_period: str = Field(..., max_length=20)


class GSTNUploadStatusUpdate(BaseModel):
    status: str = Field(..., max_length=20)
    gstn_arn: str | None = Field(None, max_length=100)


class GSTNUploadOut(BaseModel):
    id: str
    company_id: str
    upload_type: str
    filing_period: str
    file_name: str
    file_path: str | None = None
    file_size_bytes: int | None = None
    status: str
    gstn_arn: str | None = None
    uploaded_at: str | None = None
    uploaded_by: str | None = None
    created_at: str
    updated_at: str | None = None


class GSTNUploadListOut(BaseModel):
    items: list[GSTNUploadOut]
    total: int


def _gstn_upload_to_out(u: GSTNUpload) -> GSTNUploadOut:
    return GSTNUploadOut(
        id=str(u.id),
        company_id=str(u.company_id),
        upload_type=u.upload_type,
        filing_period=u.filing_period,
        file_name=u.file_name,
        file_path=u.file_path,
        file_size_bytes=u.file_size_bytes,
        status=u.status,
        gstn_arn=u.gstn_arn,
        uploaded_at=u.uploaded_at.isoformat() if u.uploaded_at else None,
        uploaded_by=u.uploaded_by,
        created_at=u.created_at.isoformat() if u.created_at else "",
        updated_at=u.updated_at.isoformat() if u.updated_at else None,
    )


# ===========================================================================
# CA Subscription schemas
# ===========================================================================


class CASubscriptionOut(BaseModel):
    id: str
    tenant_id: str
    plan: str
    status: str
    max_clients: int
    price_inr: int
    price_usd: int
    billing_cycle: str
    trial_ends_at: str | None = None
    current_period_start: str | None = None
    current_period_end: str | None = None
    cancelled_at: str | None = None
    created_at: str
    updated_at: str | None = None


class CASubscriptionActivate(BaseModel):
    plan: str = Field(default="ca_pro", max_length=50)
    billing_cycle: str = Field(default="monthly", max_length=20)


def _subscription_to_out(s: CASubscription) -> CASubscriptionOut:
    return CASubscriptionOut(
        id=str(s.id),
        tenant_id=str(s.tenant_id),
        plan=s.plan,
        status=s.status,
        max_clients=s.max_clients,
        price_inr=s.price_inr,
        price_usd=s.price_usd,
        billing_cycle=s.billing_cycle,
        trial_ends_at=s.trial_ends_at.isoformat() if s.trial_ends_at else None,
        current_period_start=s.current_period_start.isoformat() if s.current_period_start else None,
        current_period_end=s.current_period_end.isoformat() if s.current_period_end else None,
        cancelled_at=s.cancelled_at.isoformat() if s.cancelled_at else None,
        created_at=s.created_at.isoformat() if s.created_at else "",
        updated_at=s.updated_at.isoformat() if s.updated_at else None,
    )


# ---------------------------------------------------------------------------
# FILING APPROVALS -- LIST
# ---------------------------------------------------------------------------


@router.get(
    "/companies/{company_id}/approvals",
    response_model=FilingApprovalListOut,
)
async def list_filing_approvals(
    company_id: str,
    status: str | None = None,
    filing_type: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
):
    """List filing approvals for a company."""
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid company_id format") from e

    async with get_tenant_session(tid) as session:
        # Verify company belongs to tenant
        co = await session.execute(
            select(Company.id).where(Company.id == cid, Company.tenant_id == tid)
        )
        if not co.scalar_one_or_none():
            raise HTTPException(404, "Company not found")

        base = select(FilingApproval).where(
            FilingApproval.tenant_id == tid,
            FilingApproval.company_id == cid,
        )
        count_q = select(func.count()).select_from(FilingApproval).where(
            FilingApproval.tenant_id == tid,
            FilingApproval.company_id == cid,
        )

        if status:
            base = base.where(FilingApproval.status == status)
            count_q = count_q.where(FilingApproval.status == status)
        if filing_type:
            base = base.where(FilingApproval.filing_type == filing_type)
            count_q = count_q.where(FilingApproval.filing_type == filing_type)

        total = (await session.execute(count_q)).scalar() or 0
        result = await session.execute(base.order_by(FilingApproval.created_at.desc()))
        approvals = result.scalars().all()

    return FilingApprovalListOut(
        items=[_approval_to_out(a) for a in approvals],
        total=total,
    )


# ---------------------------------------------------------------------------
# FILING APPROVALS -- CREATE
# ---------------------------------------------------------------------------


@router.post(
    "/companies/{company_id}/approvals",
    response_model=FilingApprovalOut,
    status_code=201,
)
async def create_filing_approval(
    company_id: str,
    body: FilingApprovalCreate,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
):
    """Create a filing approval request.

    If the company has gst_auto_file=True, the approval is auto-approved
    immediately.
    """
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid company_id format") from e

    user_email = user.get("sub", "")
    if not user_email:
        raise HTTPException(401, "User identity required")

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Company).where(Company.id == cid, Company.tenant_id == tid)
        )
        company = result.scalar_one_or_none()
        if not company:
            raise HTTPException(404, "Company not found")

        # Determine auto-approval
        is_auto = company.gst_auto_file is True

        approval = FilingApproval(
            tenant_id=tid,
            company_id=cid,
            filing_type=body.filing_type,
            filing_period=body.filing_period,
            filing_data=body.filing_data,
            status="approved" if is_auto else "pending",
            requested_by=user_email,
            approved_by=user_email if is_auto else None,
            approved_at=datetime.utcnow() if is_auto else None,  # noqa: DTZ003
            auto_approved=is_auto,
        )
        session.add(approval)
        await session.flush()
        await session.refresh(approval)
        out = _approval_to_out(approval)

    return out


# ---------------------------------------------------------------------------
# FILING APPROVALS -- APPROVE (partner self-approval)
# ---------------------------------------------------------------------------


@router.post(
    "/companies/{company_id}/approvals/{approval_id}/approve",
    response_model=FilingApprovalOut,
)
async def approve_filing(
    company_id: str,
    approval_id: str,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
):
    """Approve a filing.  Partners can self-approve their own requests."""
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
        aid = _uuid.UUID(approval_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid ID format") from e

    user_email = user.get("sub", "")
    if not user_email:
        raise HTTPException(401, "User identity required")

    async with get_tenant_session(tid) as session:
        # Verify company
        co_result = await session.execute(
            select(Company).where(Company.id == cid, Company.tenant_id == tid)
        )
        company = co_result.scalar_one_or_none()
        if not company:
            raise HTTPException(404, "Company not found")

        # Check the user has partner role on this company
        # user_roles keys can be user_id (UUID) or email — check both
        roles = company.user_roles or {}
        user_role = roles.get(user_email)
        if not user_role:
            # Try matching by iterating values (key might be UUID)
            for _key, _role in roles.items():
                if _role == CompanyRole.partner.value:
                    user_role = _role
                    break
        # Also check if user is platform admin (role from JWT)
        jwt_role = user.get("role", "")
        if user_role != CompanyRole.partner.value and jwt_role != "admin":
            raise HTTPException(403, "Only partners can approve filings")

        # Fetch approval
        ap_result = await session.execute(
            select(FilingApproval).where(
                FilingApproval.id == aid,
                FilingApproval.company_id == cid,
                FilingApproval.tenant_id == tid,
            )
        )
        approval = ap_result.scalar_one_or_none()
        if not approval:
            raise HTTPException(404, "Filing approval not found")

        if approval.status != "pending":
            raise HTTPException(
                409, f"Approval is already '{approval.status}', cannot approve"
            )

        approval.status = "approved"
        approval.approved_by = user_email
        approval.approved_at = datetime.utcnow()  # noqa: DTZ003
        approval.updated_at = datetime.utcnow()  # noqa: DTZ003
        session.add(approval)
        await session.flush()
        await session.refresh(approval)
        out = _approval_to_out(approval)

    return out


# ---------------------------------------------------------------------------
# FILING APPROVALS -- REJECT
# ---------------------------------------------------------------------------


@router.post(
    "/companies/{company_id}/approvals/{approval_id}/reject",
    response_model=FilingApprovalOut,
)
async def reject_filing(
    company_id: str,
    approval_id: str,
    reason: str = Query("", max_length=1000),
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
):
    """Reject a filing approval."""
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
        aid = _uuid.UUID(approval_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid ID format") from e

    user_email = user.get("sub", "")
    if not user_email:
        raise HTTPException(401, "User identity required")

    async with get_tenant_session(tid) as session:
        # Verify company and user role
        co_result = await session.execute(
            select(Company).where(Company.id == cid, Company.tenant_id == tid)
        )
        company = co_result.scalar_one_or_none()
        if not company:
            raise HTTPException(404, "Company not found")

        roles = company.user_roles or {}
        user_role = roles.get(user_email)
        if not user_role:
            for _key, _role in roles.items():
                if _role in (CompanyRole.partner.value, CompanyRole.manager.value):
                    user_role = _role
                    break
        jwt_role = user.get("role", "")
        if user_role not in (CompanyRole.partner.value, CompanyRole.manager.value) and jwt_role != "admin":
            raise HTTPException(403, "Only partners or managers can reject filings")

        # Fetch approval
        ap_result = await session.execute(
            select(FilingApproval).where(
                FilingApproval.id == aid,
                FilingApproval.company_id == cid,
                FilingApproval.tenant_id == tid,
            )
        )
        approval = ap_result.scalar_one_or_none()
        if not approval:
            raise HTTPException(404, "Filing approval not found")

        if approval.status != "pending":
            raise HTTPException(
                409, f"Approval is already '{approval.status}', cannot reject"
            )

        approval.status = "rejected"
        approval.rejection_reason = reason or None
        approval.updated_at = datetime.utcnow()  # noqa: DTZ003
        session.add(approval)
        await session.flush()
        await session.refresh(approval)
        out = _approval_to_out(approval)

    return out


# ---------------------------------------------------------------------------
# GSTN UPLOADS -- LIST
# ---------------------------------------------------------------------------


@router.get(
    "/companies/{company_id}/gstn-uploads",
    response_model=GSTNUploadListOut,
)
async def list_gstn_uploads(
    company_id: str,
    status: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
):
    """List GSTN upload records for a company."""
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid company_id format") from e

    async with get_tenant_session(tid) as session:
        co = await session.execute(
            select(Company.id).where(Company.id == cid, Company.tenant_id == tid)
        )
        if not co.scalar_one_or_none():
            raise HTTPException(404, "Company not found")

        base = select(GSTNUpload).where(
            GSTNUpload.tenant_id == tid,
            GSTNUpload.company_id == cid,
        )
        count_q = select(func.count()).select_from(GSTNUpload).where(
            GSTNUpload.tenant_id == tid,
            GSTNUpload.company_id == cid,
        )

        if status:
            base = base.where(GSTNUpload.status == status)
            count_q = count_q.where(GSTNUpload.status == status)

        total = (await session.execute(count_q)).scalar() or 0
        result = await session.execute(base.order_by(GSTNUpload.created_at.desc()))
        uploads = result.scalars().all()

    return GSTNUploadListOut(
        items=[_gstn_upload_to_out(u) for u in uploads],
        total=total,
    )


# ---------------------------------------------------------------------------
# GSTN UPLOADS -- CREATE (generate JSON file for manual upload)
# ---------------------------------------------------------------------------


@router.post(
    "/companies/{company_id}/gstn-uploads",
    response_model=GSTNUploadOut,
    status_code=201,
)
async def create_gstn_upload(
    company_id: str,
    body: GSTNUploadCreate,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
):
    """Generate a GSTN JSON file for manual upload to the GSTN portal.

    This is used when the company has gst_auto_file=False.
    """
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid company_id format") from e

    async with get_tenant_session(tid) as session:
        co_result = await session.execute(
            select(Company).where(Company.id == cid, Company.tenant_id == tid)
        )
        company = co_result.scalar_one_or_none()
        if not company:
            raise HTTPException(404, "Company not found")

        # Build file name from company + filing info
        gstin_part = company.gstin or company.pan
        file_name = f"{gstin_part}_{body.upload_type}_{body.filing_period}.json"

        upload = GSTNUpload(
            tenant_id=tid,
            company_id=cid,
            upload_type=body.upload_type,
            filing_period=body.filing_period,
            file_name=file_name,
            file_path=None,  # filled when file is actually generated by agent
            file_size_bytes=None,
            status="generated",
        )
        session.add(upload)
        await session.flush()
        await session.refresh(upload)
        out = _gstn_upload_to_out(upload)

    return out


# ---------------------------------------------------------------------------
# GSTN UPLOADS -- PATCH (mark as uploaded + ARN)
# ---------------------------------------------------------------------------


@router.patch(
    "/companies/{company_id}/gstn-uploads/{upload_id}",
    response_model=GSTNUploadOut,
)
async def update_gstn_upload(
    company_id: str,
    upload_id: str,
    body: GSTNUploadStatusUpdate,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
):
    """Update a GSTN upload status and optional ARN after manual upload."""
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
        uid = _uuid.UUID(upload_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid ID format") from e

    user_email = user.get("sub", "")

    valid_statuses = {"generated", "downloaded", "uploaded", "acknowledged"}
    if body.status not in valid_statuses:
        raise HTTPException(
            422,
            f"Invalid status '{body.status}'. Must be one of: {', '.join(sorted(valid_statuses))}",
        )

    async with get_tenant_session(tid) as session:
        co = await session.execute(
            select(Company.id).where(Company.id == cid, Company.tenant_id == tid)
        )
        if not co.scalar_one_or_none():
            raise HTTPException(404, "Company not found")

        result = await session.execute(
            select(GSTNUpload).where(
                GSTNUpload.id == uid,
                GSTNUpload.company_id == cid,
                GSTNUpload.tenant_id == tid,
            )
        )
        upload = result.scalar_one_or_none()
        if not upload:
            raise HTTPException(404, "GSTN upload record not found")

        upload.status = body.status
        if body.gstn_arn:
            upload.gstn_arn = body.gstn_arn
        if body.status in ("uploaded", "acknowledged"):
            upload.uploaded_at = datetime.utcnow()  # noqa: DTZ003
            upload.uploaded_by = user_email
        upload.updated_at = datetime.utcnow()  # noqa: DTZ003
        session.add(upload)
        await session.flush()
        await session.refresh(upload)
        out = _gstn_upload_to_out(upload)

    return out


# ---------------------------------------------------------------------------
# CA SUBSCRIPTION -- GET
# ---------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402


@router.get("/ca-subscription", response_model=CASubscriptionOut)
async def get_ca_subscription(
    tenant_id: str = Depends(get_current_tenant),
):
    """Get the current tenant's CA subscription."""
    tid = _uuid.UUID(tenant_id)

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(CASubscription).where(CASubscription.tenant_id == tid)
        )
        sub = result.scalar_one_or_none()
        if not sub:
            raise HTTPException(404, "No CA subscription found for this tenant")

        return _subscription_to_out(sub)


# ---------------------------------------------------------------------------
# CA SUBSCRIPTION -- ACTIVATE
# ---------------------------------------------------------------------------


@router.post(
    "/ca-subscription/activate",
    response_model=CASubscriptionOut,
    status_code=201,
)
async def activate_ca_subscription(
    body: CASubscriptionActivate | None = None,
    tenant_id: str = Depends(get_current_tenant),
):
    """Activate a CA subscription or start a 14-day trial.

    If no subscription exists, creates one in trial status with
    14 days free and max 7 clients.  If a subscription already exists
    and is in trial/expired/cancelled, reactivates it.
    """
    tid = _uuid.UUID(tenant_id)
    plan = body.plan if body else "ca_pro"
    billing_cycle = body.billing_cycle if body else "monthly"

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(CASubscription).where(CASubscription.tenant_id == tid)
        )
        sub = result.scalar_one_or_none()

        now = datetime.utcnow()  # noqa: DTZ003

        if sub is None:
            # Create new trial subscription
            sub = CASubscription(
                tenant_id=tid,
                plan=plan,
                status="trial",
                max_clients=7,
                price_inr=4999,
                price_usd=59,
                billing_cycle=billing_cycle,
                trial_ends_at=now + timedelta(days=14),
                current_period_start=now,
                current_period_end=now + timedelta(days=14),
            )
            session.add(sub)
        elif sub.status in ("trial", "expired", "cancelled"):
            # Reactivate
            sub.status = "active"
            sub.plan = plan
            sub.billing_cycle = billing_cycle
            sub.current_period_start = now
            if billing_cycle == "annual":
                sub.current_period_end = now + timedelta(days=365)
                sub.max_clients = 50
            else:
                sub.current_period_end = now + timedelta(days=30)
                sub.max_clients = 25
            sub.cancelled_at = None
            sub.updated_at = now
            session.add(sub)
        elif sub.status == "active":
            raise HTTPException(409, "Subscription is already active")

        await session.flush()
        await session.refresh(sub)
        out = _subscription_to_out(sub)

    return out


# ===========================================================================
# GSTN Credential Vault schemas
# ===========================================================================

from core.crypto import (  # noqa: E402
    decrypt_for_tenant,
    encrypt_for_tenant,
)
from core.models.gstn_credential import GSTNCredential  # noqa: E402


class GSTNCredentialCreate(BaseModel):
    gstin: str
    username: str
    password: str
    portal_type: str = "gstn"


class GSTNCredentialOut(BaseModel):
    id: str
    company_id: str
    gstin: str
    username: str
    portal_type: str
    is_active: bool
    last_verified_at: str | None = None
    created_at: str


class GSTNCredentialListOut(BaseModel):
    items: list[GSTNCredentialOut]
    total: int


def _credential_to_out(c: GSTNCredential) -> GSTNCredentialOut:
    return GSTNCredentialOut(
        id=str(c.id),
        company_id=str(c.company_id),
        gstin=c.gstin,
        username=c.username,
        portal_type=c.portal_type,
        is_active=c.is_active,
        last_verified_at=c.last_verified_at.isoformat() if c.last_verified_at else None,
        created_at=c.created_at.isoformat() if c.created_at else "",
    )


# ---------------------------------------------------------------------------
# GSTN CREDENTIALS -- LIST
# ---------------------------------------------------------------------------


@router.get(
    "/companies/{company_id}/credentials",
    response_model=GSTNCredentialListOut,
)
async def list_gstn_credentials(
    company_id: str,
    tenant_id: str = Depends(get_current_tenant),
):
    """List stored GSTN credentials for a company (never returns passwords)."""
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid company_id format") from e

    async with get_tenant_session(tid) as session:
        co = await session.execute(
            select(Company.id).where(Company.id == cid, Company.tenant_id == tid)
        )
        if not co.scalar_one_or_none():
            raise HTTPException(404, "Company not found")

        base = select(GSTNCredential).where(
            GSTNCredential.tenant_id == tid,
            GSTNCredential.company_id == cid,
        )
        count_q = select(func.count()).select_from(GSTNCredential).where(
            GSTNCredential.tenant_id == tid,
            GSTNCredential.company_id == cid,
        )

        total = (await session.execute(count_q)).scalar() or 0
        result = await session.execute(base.order_by(GSTNCredential.created_at.desc()))
        credentials = result.scalars().all()

    return GSTNCredentialListOut(
        items=[_credential_to_out(c) for c in credentials],
        total=total,
    )


# ---------------------------------------------------------------------------
# GSTN CREDENTIALS -- CREATE
# ---------------------------------------------------------------------------


@router.post(
    "/companies/{company_id}/credentials",
    response_model=GSTNCredentialOut,
    status_code=201,
)
async def create_gstn_credential(
    company_id: str,
    body: GSTNCredentialCreate,
    tenant_id: str = Depends(get_current_tenant),
):
    """Store a new GSTN credential (password is encrypted at rest)."""
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid company_id format") from e

    async with get_tenant_session(tid) as session:
        co = await session.execute(
            select(Company.id).where(Company.id == cid, Company.tenant_id == tid)
        )
        if not co.scalar_one_or_none():
            raise HTTPException(404, "Company not found")

        # Encrypt the password before storage. Uses the customer-managed
        # KEK if the tenant has BYOK enabled, otherwise the platform KEK
        # via Cloud KMS, otherwise legacy Fernet — all transparent.
        encrypted_pw = await encrypt_for_tenant(body.password, tid)

        credential = GSTNCredential(
            tenant_id=tid,
            company_id=cid,
            gstin=body.gstin,
            username=body.username,
            password_encrypted=encrypted_pw,
            portal_type=body.portal_type,
        )
        session.add(credential)
        await session.flush()
        await session.refresh(credential)
        out = _credential_to_out(credential)

    return out


# ---------------------------------------------------------------------------
# GSTN CREDENTIALS -- DELETE (deactivate)
# ---------------------------------------------------------------------------


@router.delete(
    "/companies/{company_id}/credentials/{credential_id}",
    status_code=204,
)
async def deactivate_gstn_credential(
    company_id: str,
    credential_id: str,
    tenant_id: str = Depends(get_current_tenant),
):
    """Deactivate a stored GSTN credential (sets is_active=False)."""
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
        cred_id = _uuid.UUID(credential_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid ID format") from e

    async with get_tenant_session(tid) as session:
        co = await session.execute(
            select(Company.id).where(Company.id == cid, Company.tenant_id == tid)
        )
        if not co.scalar_one_or_none():
            raise HTTPException(404, "Company not found")

        result = await session.execute(
            select(GSTNCredential).where(
                GSTNCredential.id == cred_id,
                GSTNCredential.company_id == cid,
                GSTNCredential.tenant_id == tid,
            )
        )
        credential = result.scalar_one_or_none()
        if not credential:
            raise HTTPException(404, "Credential not found")

        credential.is_active = False
        credential.updated_at = datetime.utcnow()  # noqa: DTZ003
        session.add(credential)


# ---------------------------------------------------------------------------
# GSTN CREDENTIALS -- VERIFY (test decryption)
# ---------------------------------------------------------------------------


@router.post(
    "/companies/{company_id}/credentials/{credential_id}/verify",
)
async def verify_gstn_credential(
    company_id: str,
    credential_id: str,
    tenant_id: str = Depends(get_current_tenant),
):
    """Test that the stored credential can be decrypted successfully."""
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
        cred_id = _uuid.UUID(credential_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid ID format") from e

    async with get_tenant_session(tid) as session:
        co = await session.execute(
            select(Company.id).where(Company.id == cid, Company.tenant_id == tid)
        )
        if not co.scalar_one_or_none():
            raise HTTPException(404, "Company not found")

        result = await session.execute(
            select(GSTNCredential).where(
                GSTNCredential.id == cred_id,
                GSTNCredential.company_id == cid,
                GSTNCredential.tenant_id == tid,
            )
        )
        credential = result.scalar_one_or_none()
        if not credential:
            raise HTTPException(404, "Credential not found")

        try:
            decrypt_for_tenant(credential.password_encrypted)
            success = True
        except Exception:
            success = False

        # Update last_verified_at on success
        if success:
            credential.last_verified_at = datetime.utcnow()  # noqa: DTZ003
            credential.updated_at = datetime.utcnow()  # noqa: DTZ003
            session.add(credential)

    return {"credential_id": str(cred_id), "verified": success}


# ===========================================================================
# Bulk Filing Approval schemas
# ===========================================================================


class BulkApproveRequest(BaseModel):
    approval_ids: list[str]


class BulkApproveResponse(BaseModel):
    approved: list[str]
    failed: list[dict]


# ---------------------------------------------------------------------------
# BULK APPROVE -- POST
# ---------------------------------------------------------------------------


@router.post(
    "/companies/bulk-approve",
    response_model=BulkApproveResponse,
)
async def bulk_approve_filings(
    body: BulkApproveRequest,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
):
    """Approve multiple filing approvals at once.

    For each approval ID: checks pending status, verifies the user has
    partner role on the associated company, then approves.  Returns a
    list of successfully approved IDs and a list of failures with reasons.
    """
    tid = _uuid.UUID(tenant_id)
    user_email = user.get("sub", "")
    if not user_email:
        raise HTTPException(401, "User identity required")

    approved: list[str] = []
    failed: list[dict] = []

    async with get_tenant_session(tid) as session:
        for aid_str in body.approval_ids:
            try:
                aid = _uuid.UUID(aid_str)
            except ValueError:
                failed.append({"id": aid_str, "reason": "Invalid approval ID format"})
                continue

            # Fetch approval
            ap_result = await session.execute(
                select(FilingApproval).where(
                    FilingApproval.id == aid,
                    FilingApproval.tenant_id == tid,
                )
            )
            approval = ap_result.scalar_one_or_none()
            if not approval:
                failed.append({"id": aid_str, "reason": "Approval not found"})
                continue

            if approval.status != "pending":
                failed.append({
                    "id": aid_str,
                    "reason": f"Approval is already '{approval.status}'",
                })
                continue

            # Check partner role on the associated company
            co_result = await session.execute(
                select(Company).where(
                    Company.id == approval.company_id,
                    Company.tenant_id == tid,
                )
            )
            company = co_result.scalar_one_or_none()
            if not company:
                failed.append({"id": aid_str, "reason": "Associated company not found"})
                continue

            roles = company.user_roles or {}
            user_role = roles.get(user_email)
            if user_role != CompanyRole.partner.value:
                failed.append({
                    "id": aid_str,
                    "reason": "Only partners can approve filings",
                })
                continue

            # Approve
            approval.status = "approved"
            approval.approved_by = user_email
            approval.approved_at = datetime.utcnow()  # noqa: DTZ003
            approval.updated_at = datetime.utcnow()  # noqa: DTZ003
            session.add(approval)
            approved.append(aid_str)

        await session.flush()

    return BulkApproveResponse(approved=approved, failed=failed)


# ===========================================================================
# Compliance Deadline schemas
# ===========================================================================

from core.models.compliance_deadline import ComplianceDeadline  # noqa: E402


class ComplianceDeadlineOut(BaseModel):
    id: str
    company_id: str
    deadline_type: str
    filing_period: str
    due_date: str
    alert_7d_sent: bool
    alert_1d_sent: bool
    filed: bool
    filed_at: str | None = None
    created_at: str


class ComplianceDeadlineListOut(BaseModel):
    items: list[ComplianceDeadlineOut]
    total: int


def _deadline_to_out(d: ComplianceDeadline) -> ComplianceDeadlineOut:
    return ComplianceDeadlineOut(
        id=str(d.id),
        company_id=str(d.company_id),
        deadline_type=d.deadline_type,
        filing_period=d.filing_period,
        due_date=d.due_date.isoformat() if d.due_date else "",
        alert_7d_sent=d.alert_7d_sent,
        alert_1d_sent=d.alert_1d_sent,
        filed=d.filed,
        filed_at=d.filed_at.isoformat() if d.filed_at else None,
        created_at=d.created_at.isoformat() if d.created_at else "",
    )


# ---------------------------------------------------------------------------
# COMPLIANCE DEADLINES -- LIST
# ---------------------------------------------------------------------------


@router.get(
    "/companies/{company_id}/deadlines",
    response_model=ComplianceDeadlineListOut,
)
async def list_compliance_deadlines(
    company_id: str,
    filed: bool | None = None,
    deadline_type: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
):
    """List upcoming compliance deadlines for a company."""
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid company_id format") from e

    async with get_tenant_session(tid) as session:
        co = await session.execute(
            select(Company.id).where(Company.id == cid, Company.tenant_id == tid)
        )
        if not co.scalar_one_or_none():
            raise HTTPException(404, "Company not found")

        base = select(ComplianceDeadline).where(
            ComplianceDeadline.tenant_id == tid,
            ComplianceDeadline.company_id == cid,
        )
        count_q = select(func.count()).select_from(ComplianceDeadline).where(
            ComplianceDeadline.tenant_id == tid,
            ComplianceDeadline.company_id == cid,
        )

        if filed is not None:
            base = base.where(ComplianceDeadline.filed == filed)
            count_q = count_q.where(ComplianceDeadline.filed == filed)
        if deadline_type:
            base = base.where(ComplianceDeadline.deadline_type == deadline_type)
            count_q = count_q.where(ComplianceDeadline.deadline_type == deadline_type)

        total = (await session.execute(count_q)).scalar() or 0
        result = await session.execute(base.order_by(ComplianceDeadline.due_date))
        deadlines = result.scalars().all()

    return ComplianceDeadlineListOut(
        items=[_deadline_to_out(d) for d in deadlines],
        total=total,
    )


# ---------------------------------------------------------------------------
# COMPLIANCE DEADLINES -- GENERATE
# ---------------------------------------------------------------------------

from datetime import UTC  # noqa: E402
from datetime import date as _date  # noqa: E402

# Standard Indian compliance calendar: deadline_type -> day-of-month
_DEADLINE_CALENDAR: dict[str, int] = {
    "gstr1": 11,
    "gstr3b": 20,
    "tds_26q": 31,  # quarterly (last day of following month)
    "tds_24q": 31,
    "pf_ecr": 15,
    "esi_return": 15,
}


@router.post(
    "/companies/{company_id}/deadlines/generate",
    response_model=ComplianceDeadlineListOut,
    status_code=201,
)
async def generate_compliance_deadlines(
    company_id: str,
    months_ahead: int = Query(3, ge=1, le=12),
    tenant_id: str = Depends(get_current_tenant),
):
    """Generate compliance deadlines for the upcoming months.

    Creates deadline rows based on the standard Indian compliance
    calendar.  Skips any deadlines that already exist (unique constraint).
    """
    import calendar  # noqa: E402

    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid company_id format") from e

    async with get_tenant_session(tid) as session:
        co_result = await session.execute(
            select(Company).where(Company.id == cid, Company.tenant_id == tid)
        )
        company = co_result.scalar_one_or_none()
        if not company:
            raise HTTPException(404, "Company not found")

        today = datetime.now(tz=UTC).date()
        created: list[ComplianceDeadline] = []

        for offset in range(months_ahead):
            month = today.month + offset
            year = today.year
            while month > 12:
                month -= 12
                year += 1

            filing_period = f"{year}-{month:02d}"

            for dtype, day in _DEADLINE_CALENDAR.items():
                # Clamp day to valid range for the month
                max_day = calendar.monthrange(year, month)[1]
                actual_day = min(day, max_day)
                due = _date(year, month, actual_day)

                # Check if already exists
                existing = await session.execute(
                    select(ComplianceDeadline.id).where(
                        ComplianceDeadline.company_id == cid,
                        ComplianceDeadline.deadline_type == dtype,
                        ComplianceDeadline.filing_period == filing_period,
                    ).limit(1)
                )
                if existing.scalar_one_or_none():
                    continue

                dl = ComplianceDeadline(
                    tenant_id=tid,
                    company_id=cid,
                    deadline_type=dtype,
                    filing_period=filing_period,
                    due_date=due,
                )
                session.add(dl)
                created.append(dl)

        await session.flush()
        for dl in created:
            await session.refresh(dl)

    return ComplianceDeadlineListOut(
        items=[_deadline_to_out(d) for d in created],
        total=len(created),
    )


# ---------------------------------------------------------------------------
# COMPLIANCE DEADLINES -- MARK FILED
# ---------------------------------------------------------------------------


@router.patch(
    "/companies/{company_id}/deadlines/{deadline_id}/filed",
    response_model=ComplianceDeadlineOut,
)
async def mark_deadline_filed(
    company_id: str,
    deadline_id: str,
    tenant_id: str = Depends(get_current_tenant),
):
    """Mark a compliance deadline as filed."""
    tid = _uuid.UUID(tenant_id)
    try:
        cid = _uuid.UUID(company_id)
        did = _uuid.UUID(deadline_id)
    except ValueError as e:
        raise HTTPException(400, "Invalid ID format") from e

    async with get_tenant_session(tid) as session:
        co = await session.execute(
            select(Company.id).where(Company.id == cid, Company.tenant_id == tid)
        )
        if not co.scalar_one_or_none():
            raise HTTPException(404, "Company not found")

        result = await session.execute(
            select(ComplianceDeadline).where(
                ComplianceDeadline.id == did,
                ComplianceDeadline.company_id == cid,
                ComplianceDeadline.tenant_id == tid,
            )
        )
        deadline = result.scalar_one_or_none()
        if not deadline:
            raise HTTPException(404, "Deadline not found")

        if deadline.filed:
            raise HTTPException(409, "Deadline is already marked as filed")

        deadline.filed = True
        deadline.filed_at = datetime.utcnow()  # noqa: DTZ003
        deadline.updated_at = datetime.utcnow()  # noqa: DTZ003
        session.add(deadline)
        await session.flush()
        await session.refresh(deadline)
        return _deadline_to_out(deadline)


# ===========================================================================
# Tally Auto-Detect schemas
# ===========================================================================


class TallyDetectRequest(BaseModel):
    tally_bridge_url: str
    tally_bridge_id: str = ""


class TallyDetectResponse(BaseModel):
    detected: bool
    company_name: str | None = None
    gstin: str | None = None
    pan: str | None = None
    address: str | None = None
    fy_start: str | None = None
    fy_end: str | None = None


# ---------------------------------------------------------------------------
# TALLY AUTO-DETECT -- POST
# ---------------------------------------------------------------------------


@router.post(
    "/companies/tally-detect",
    response_model=TallyDetectResponse,
)
async def tally_detect(
    body: TallyDetectRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    """Attempt to connect to a Tally bridge and detect company info.

    Requires the AgenticOrg Bridge agent to be running on the client's
    network with access to the Tally Prime instance.
    """
    if not body.tally_bridge_url:
        raise HTTPException(422, "tally_bridge_url is required")

    # Try to connect to the real Tally bridge
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{body.tally_bridge_url}/api/company-info")
            resp.raise_for_status()
            data = resp.json()
            return TallyDetectResponse(
                detected=True,
                company_name=data.get("company_name", ""),
                gstin=data.get("gstin", ""),
                pan=data.get("pan", ""),
                address=data.get("address", ""),
                fy_start=data.get("fy_start", ""),
                fy_end=data.get("fy_end", ""),
            )
    except Exception as exc:
        return TallyDetectResponse(
            detected=False,
            company_name="",
            gstin="",
            pan="",
            address=f"Could not connect to Tally bridge at {body.tally_bridge_url}: {exc}",
            fy_start="",
            fy_end="",
        )


# ===========================================================================
# Partner Dashboard KPI schemas
# ===========================================================================


class PartnerDashboardOut(BaseModel):
    total_clients: int
    active_clients: int
    avg_health_score: float
    total_pending_filings: int
    total_overdue: int
    revenue_per_month_inr: int
    clients: list[dict]
    upcoming_deadlines: list[dict]


# ---------------------------------------------------------------------------
# PARTNER DASHBOARD -- GET
# ---------------------------------------------------------------------------


@router.get(
    "/partner-dashboard",
    response_model=PartnerDashboardOut,
)
async def get_partner_dashboard(
    tenant_id: str = Depends(get_current_tenant),
):
    """Aggregate KPIs across all companies for the tenant.

    Queries companies, filing_approvals (pending count), and
    compliance_deadlines (upcoming + overdue).
    """
    tid = _uuid.UUID(tenant_id)
    today = datetime.now(tz=UTC).date()

    async with get_tenant_session(tid) as session:
        # All companies for this tenant
        co_result = await session.execute(
            select(Company).where(Company.tenant_id == tid)
        )
        companies = co_result.scalars().all()

        total_clients = len(companies)
        active_clients = sum(1 for c in companies if c.is_active)

        # Average health score (skip None values)
        health_scores = [c.client_health_score for c in companies if c.client_health_score is not None]
        avg_health = round(sum(health_scores) / len(health_scores), 1) if health_scores else 0.0

        # Pending filings count
        pending_q = select(func.count()).select_from(FilingApproval).where(
            FilingApproval.tenant_id == tid,
            FilingApproval.status == "pending",
        )
        total_pending = (await session.execute(pending_q)).scalar() or 0

        # Overdue deadlines: not filed and due_date < today
        overdue_q = select(func.count()).select_from(ComplianceDeadline).where(
            ComplianceDeadline.tenant_id == tid,
            ComplianceDeadline.filed == False,  # noqa: E712
            ComplianceDeadline.due_date < today,
        )
        total_overdue = (await session.execute(overdue_q)).scalar() or 0

        # Per-client summary
        clients_list: list[dict] = []
        for c in companies:
            # Pending filings for this company
            cp_q = select(func.count()).select_from(FilingApproval).where(
                FilingApproval.tenant_id == tid,
                FilingApproval.company_id == c.id,
                FilingApproval.status == "pending",
            )
            pending_count = (await session.execute(cp_q)).scalar() or 0

            clients_list.append({
                "id": str(c.id),
                "name": c.name,
                "health_score": c.client_health_score,
                "pending_filings": pending_count,
                "subscription_status": c.subscription_status or "trial",
            })

        # Revenue estimate: count of active companies * base price
        revenue_per_month_inr = active_clients * 4999

        # Upcoming deadlines (next 30 days, unfiled)
        upcoming_q = select(ComplianceDeadline).where(
            ComplianceDeadline.tenant_id == tid,
            ComplianceDeadline.filed == False,  # noqa: E712
            ComplianceDeadline.due_date >= today,
            ComplianceDeadline.due_date <= today + timedelta(days=30),
        ).order_by(ComplianceDeadline.due_date).limit(20)
        upcoming_result = await session.execute(upcoming_q)
        upcoming_deadlines = upcoming_result.scalars().all()

        # Map company_id -> name for the deadlines
        company_map = {c.id: c.name for c in companies}

        deadlines_list: list[dict] = []
        for dl in upcoming_deadlines:
            deadlines_list.append({
                "deadline_type": dl.deadline_type,
                "filing_period": dl.filing_period,
                "due_date": dl.due_date.isoformat() if dl.due_date else "",
                "company_name": company_map.get(dl.company_id, "Unknown"),
            })

    return PartnerDashboardOut(
        total_clients=total_clients,
        active_clients=active_clients,
        avg_health_score=avg_health,
        total_pending_filings=total_pending,
        total_overdue=total_overdue,
        revenue_per_month_inr=revenue_per_month_inr,
        clients=clients_list,
        upcoming_deadlines=deadlines_list,
    )
