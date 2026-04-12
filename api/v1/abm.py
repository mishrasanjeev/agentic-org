"""ABM (Account-Based Marketing) API -- target account management, intent scoring, campaigns.

Provides endpoints for uploading target account lists, querying intent
data from Bombora / G2 / TrustRadius, launching targeted campaigns,
and an executive ABM dashboard.
"""

from __future__ import annotations

import csv
import io
import uuid as _uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from api.deps import get_current_tenant
from core.database import get_tenant_session
from core.marketing.intent_aggregator import IntentAggregator
from core.models.abm import ABMAccount, ABMCampaign

logger = structlog.get_logger()
router = APIRouter(prefix="/abm", tags=["ABM"])

_aggregator = IntentAggregator()


# ── Pydantic schemas ──────────────────────────────────────────────────

class AccountCreate(BaseModel):
    company_name: str = Field(..., max_length=255)
    domain: str = Field(..., max_length=255)
    industry: str = ""
    revenue: str = ""
    tier: str = Field(default="2", pattern=r"^[123]$")


class AccountUpdate(BaseModel):
    company_name: str | None = None
    domain: str | None = None
    industry: str | None = None
    revenue: str | None = None
    tier: str | None = Field(default=None, pattern=r"^[123]$")


class CampaignCreate(BaseModel):
    campaign_name: str = Field(..., max_length=255)
    channel: str = Field(default="linkedin", pattern=r"^(linkedin|email|display|multi)$")
    budget_usd: float | None = None
    message: str = ""
    target_persona: str = ""


# ── Helpers ────────────────────────────────────────────────────────────

def _account_to_dict(acct: ABMAccount) -> dict[str, Any]:
    """Serialize an ABMAccount ORM instance to the API response dict."""
    metadata = acct.metadata_ or {}
    return {
        "id": str(acct.id),
        "company_name": acct.name,
        "domain": acct.domain or "",
        "industry": acct.industry or "",
        "revenue": acct.revenue or "",
        "tier": acct.tier or "2",
        "intent_score": float(acct.intent_score),
        "intent_data": metadata.get("intent_data"),
        "campaigns": metadata.get("campaigns", []),
        "created_at": acct.created_at.isoformat() if acct.created_at else None,
        "updated_at": acct.updated_at.isoformat() if acct.updated_at else None,
    }


def _campaign_to_dict(camp: ABMCampaign) -> dict[str, Any]:
    """Serialize an ABMCampaign ORM instance to the API response dict."""
    config = camp.config or {}
    results = camp.results or {}
    return {
        "id": str(camp.id),
        "account_id": str(camp.account_id),
        "campaign_name": camp.name,
        "channel": camp.channel or "linkedin",
        "budget_usd": float(camp.budget) if camp.budget is not None else None,
        "message": config.get("message", ""),
        "target_persona": config.get("target_persona", ""),
        "status": camp.status or "draft",
        "launched_at": camp.created_at.isoformat() if camp.created_at else None,
        "metrics": results.get("metrics", {
            "impressions": 0,
            "clicks": 0,
            "conversions": 0,
            "spend_usd": 0.0,
        }),
    }


# ── Endpoints ──────────────────────────────────────────────────────────

@router.post("/accounts/upload")
async def upload_accounts(
    file: UploadFile,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Upload a CSV file of target accounts.

    Expected columns: company_name, domain, industry, revenue, tier.
    Returns the list of created account IDs.
    """
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(400, "Only CSV files are accepted")

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    required_cols = {"company_name", "domain"}
    if reader.fieldnames is None:
        raise HTTPException(400, "CSV file is empty or has no headers")

    actual_cols = {c.strip().lower() for c in reader.fieldnames}
    missing = required_cols - actual_cols
    if missing:
        raise HTTPException(400, f"Missing required columns: {', '.join(sorted(missing))}")

    tid = _uuid.UUID(tenant_id)
    created_ids: list[str] = []
    skipped = 0

    async with get_tenant_session(tid) as session:
        for row in reader:
            company_name = (row.get("company_name") or "").strip()
            domain = (row.get("domain") or "").strip()
            if not company_name or not domain:
                skipped += 1
                continue

            acct = ABMAccount(
                tenant_id=tid,
                name=company_name,
                domain=domain,
                industry=(row.get("industry") or "").strip() or None,
                revenue=(row.get("revenue") or "").strip() or None,
                tier=(row.get("tier") or "2").strip(),
            )
            session.add(acct)
            await session.flush()
            created_ids.append(str(acct.id))

    logger.info("abm_csv_upload", tenant=tenant_id, created=len(created_ids), skipped=skipped)
    return {
        "created": len(created_ids),
        "skipped": skipped,
        "account_ids": created_ids,
    }


@router.get("/accounts")
async def list_accounts(
    tier: str | None = None,
    industry: str | None = None,
    min_intent_score: float | None = None,
    page: int = 1,
    per_page: int = 50,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """List target accounts with optional filters and pagination."""
    if page < 1:
        raise HTTPException(422, "page must be >= 1")
    per_page = min(max(per_page, 1), 200)

    tid = _uuid.UUID(tenant_id)

    async with get_tenant_session(tid) as session:
        query = select(ABMAccount).where(ABMAccount.tenant_id == tid)
        count_query = select(func.count()).select_from(ABMAccount).where(ABMAccount.tenant_id == tid)

        if tier:
            query = query.where(ABMAccount.tier == tier)
            count_query = count_query.where(ABMAccount.tier == tier)
        if industry:
            query = query.where(func.lower(ABMAccount.industry) == industry.lower())
            count_query = count_query.where(func.lower(ABMAccount.industry) == industry.lower())
        if min_intent_score is not None:
            query = query.where(ABMAccount.intent_score >= min_intent_score)
            count_query = count_query.where(ABMAccount.intent_score >= min_intent_score)

        total = (await session.execute(count_query)).scalar() or 0

        query = query.order_by(ABMAccount.intent_score.desc())
        query = query.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(query)
        accounts = [_account_to_dict(a) for a in result.scalars().all()]

    pages = max(1, (total + per_page - 1) // per_page)
    return {
        "accounts": accounts,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@router.post("/accounts")
async def create_account(
    body: AccountCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Add a single target account."""
    tid = _uuid.UUID(tenant_id)

    async with get_tenant_session(tid) as session:
        # Check duplicate domain
        dup_query = select(ABMAccount).where(
            ABMAccount.tenant_id == tid,
            func.lower(ABMAccount.domain) == body.domain.lower(),
        )
        existing = (await session.execute(dup_query)).scalar_one_or_none()
        if existing:
            raise HTTPException(409, f"Account with domain {body.domain} already exists")

        acct = ABMAccount(
            tenant_id=tid,
            name=body.company_name,
            domain=body.domain,
            industry=body.industry or None,
            revenue=body.revenue or None,
            tier=body.tier,
        )
        session.add(acct)
        await session.flush()

        logger.info("abm_account_created", tenant=tenant_id, account_id=str(acct.id))
        return _account_to_dict(acct)


@router.get("/accounts/{account_id}")
async def get_account(
    account_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Get a single account by ID."""
    tid = _uuid.UUID(tenant_id)
    acct_uuid = _uuid.UUID(account_id)

    async with get_tenant_session(tid) as session:
        query = select(ABMAccount).where(
            ABMAccount.tenant_id == tid,
            ABMAccount.id == acct_uuid,
        )
        acct = (await session.execute(query)).scalar_one_or_none()
        if not acct:
            raise HTTPException(404, "Account not found")
        return _account_to_dict(acct)


@router.get("/accounts/{account_id}/intent")
async def get_account_intent(
    account_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Get aggregated intent data for an account from all 3 providers.

    Queries Bombora, G2, and TrustRadius in parallel and returns a
    composite intent score.  The score is cached on the account record.
    """
    tid = _uuid.UUID(tenant_id)
    acct_uuid = _uuid.UUID(account_id)

    async with get_tenant_session(tid) as session:
        query = select(ABMAccount).where(
            ABMAccount.tenant_id == tid,
            ABMAccount.id == acct_uuid,
        )
        acct = (await session.execute(query)).scalar_one_or_none()
        if not acct:
            raise HTTPException(404, "Account not found")

        # Use empty configs -- in production these would come from the
        # tenant's stored connector configurations.
        try:
            intent = await _aggregator.aggregate_intent(
                domain=acct.domain or "",
                bombora_config={},
                g2_config={},
                trustradius_config={},
            )
        except Exception as exc:
            logger.error("abm_intent_error", account_id=account_id, error=str(exc))
            raise HTTPException(502, f"Intent aggregation failed: {exc}") from exc

        # Cache on the account
        acct.intent_score = intent["composite_score"]
        metadata = dict(acct.metadata_) if acct.metadata_ else {}
        metadata["intent_data"] = intent
        acct.metadata_ = metadata
        acct.updated_at = datetime.now(UTC)

    return intent


@router.post("/accounts/{account_id}/campaign")
async def launch_campaign(
    account_id: str,
    body: CampaignCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Launch a targeted campaign for a specific account."""
    tid = _uuid.UUID(tenant_id)
    acct_uuid = _uuid.UUID(account_id)

    async with get_tenant_session(tid) as session:
        query = select(ABMAccount).where(
            ABMAccount.tenant_id == tid,
            ABMAccount.id == acct_uuid,
        )
        acct = (await session.execute(query)).scalar_one_or_none()
        if not acct:
            raise HTTPException(404, "Account not found")

        campaign = ABMCampaign(
            tenant_id=tid,
            account_id=acct_uuid,
            name=body.campaign_name,
            channel=body.channel,
            budget=body.budget_usd,
            status="launched",
            config={
                "message": body.message,
                "target_persona": body.target_persona,
            },
            results={
                "metrics": {
                    "impressions": 0,
                    "clicks": 0,
                    "conversions": 0,
                    "spend_usd": 0.0,
                },
            },
        )
        session.add(campaign)
        await session.flush()

        logger.info(
            "abm_campaign_launched",
            tenant=tenant_id,
            account_id=account_id,
            campaign_id=str(campaign.id),
            channel=body.channel,
        )
        return _campaign_to_dict(campaign)


@router.get("/dashboard")
async def abm_dashboard(
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """ABM executive dashboard summary.

    Returns total accounts, counts by tier, average intent score,
    top 10 accounts by intent, and pipeline influenced value.
    """
    tid = _uuid.UUID(tenant_id)

    async with get_tenant_session(tid) as session:
        # Total accounts
        total_q = select(func.count()).select_from(ABMAccount).where(ABMAccount.tenant_id == tid)
        total = (await session.execute(total_q)).scalar() or 0

        # Counts by tier
        tier_q = (
            select(ABMAccount.tier, func.count().label("count"))
            .where(ABMAccount.tenant_id == tid)
            .group_by(ABMAccount.tier)
        )
        tier_result = await session.execute(tier_q)
        by_tier: dict[str, int] = {"1": 0, "2": 0, "3": 0}
        for row in tier_result:
            tier_key = str(row.tier) if row.tier else "unclassified"
            by_tier[tier_key] = row.count

        # Average intent score
        avg_q = select(func.avg(ABMAccount.intent_score)).where(ABMAccount.tenant_id == tid)
        avg_intent_raw = (await session.execute(avg_q)).scalar()
        avg_intent = round(float(avg_intent_raw), 2) if avg_intent_raw else 0.0

        # Top 10 by intent score
        top_q = (
            select(ABMAccount)
            .where(ABMAccount.tenant_id == tid)
            .order_by(ABMAccount.intent_score.desc())
            .limit(10)
        )
        top_result = await session.execute(top_q)
        top_10 = [
            {
                "id": str(a.id),
                "company_name": a.name,
                "domain": a.domain or "",
                "tier": a.tier or "2",
                "intent_score": float(a.intent_score) if a.intent_score is not None else 0.0,
            }
            for a in top_result.scalars().all()
        ]

        # Pipeline influenced: sum of spend_usd across all campaigns
        camp_q = select(ABMCampaign).where(ABMCampaign.tenant_id == tid)
        camp_result = await session.execute(camp_q)
        pipeline_influenced = 0.0
        total_campaigns = 0
        for camp in camp_result.scalars().all():
            total_campaigns += 1
            results = camp.results or {}
            metrics = results.get("metrics", {})
            pipeline_influenced += metrics.get("spend_usd", 0.0)

    return {
        "total_accounts": total,
        "by_tier": by_tier,
        "avg_intent_score": avg_intent,
        "top_10_by_intent": top_10,
        "pipeline_influenced_usd": round(pipeline_influenced, 2),
        "total_campaigns": total_campaigns,
    }
