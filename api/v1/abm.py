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

from api.deps import get_current_tenant
from core.marketing.intent_aggregator import IntentAggregator

logger = structlog.get_logger()
router = APIRouter(prefix="/abm", tags=["ABM"])

# ── In-memory store (MVP) ──────────────────────────────────────────────
# Keyed by tenant_id -> list of accounts
_accounts_store: dict[str, list[dict[str, Any]]] = {}
_campaigns_store: dict[str, list[dict[str, Any]]] = {}

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

def _tenant_accounts(tenant_id: str) -> list[dict[str, Any]]:
    return _accounts_store.setdefault(tenant_id, [])


def _find_account(tenant_id: str, account_id: str) -> dict[str, Any] | None:
    for acct in _tenant_accounts(tenant_id):
        if acct["id"] == account_id:
            return acct
    return None


def _account_dict(
    company_name: str,
    domain: str,
    industry: str = "",
    revenue: str = "",
    tier: str = "2",
) -> dict[str, Any]:
    return {
        "id": str(_uuid.uuid4()),
        "company_name": company_name,
        "domain": domain,
        "industry": industry,
        "revenue": revenue,
        "tier": tier,
        "intent_score": 0.0,
        "intent_data": None,
        "campaigns": [],
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
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

    accounts = _tenant_accounts(tenant_id)
    created_ids: list[str] = []
    skipped = 0

    for row in reader:
        company_name = (row.get("company_name") or "").strip()
        domain = (row.get("domain") or "").strip()
        if not company_name or not domain:
            skipped += 1
            continue

        acct = _account_dict(
            company_name=company_name,
            domain=domain,
            industry=(row.get("industry") or "").strip(),
            revenue=(row.get("revenue") or "").strip(),
            tier=(row.get("tier") or "2").strip(),
        )
        accounts.append(acct)
        created_ids.append(acct["id"])

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
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """List target accounts with optional filters."""
    accounts = _tenant_accounts(tenant_id)
    filtered = accounts

    if tier:
        filtered = [a for a in filtered if a["tier"] == tier]
    if industry:
        filtered = [a for a in filtered if a["industry"].lower() == industry.lower()]
    if min_intent_score is not None:
        filtered = [a for a in filtered if a["intent_score"] >= min_intent_score]

    # Sort by intent score descending
    filtered.sort(key=lambda a: a["intent_score"], reverse=True)

    return {
        "accounts": filtered,
        "total": len(filtered),
    }


@router.post("/accounts")
async def create_account(
    body: AccountCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Add a single target account."""
    accounts = _tenant_accounts(tenant_id)

    # Check duplicate domain
    for existing in accounts:
        if existing["domain"].lower() == body.domain.lower():
            raise HTTPException(409, f"Account with domain {body.domain} already exists")

    acct = _account_dict(
        company_name=body.company_name,
        domain=body.domain,
        industry=body.industry,
        revenue=body.revenue,
        tier=body.tier,
    )
    accounts.append(acct)
    logger.info("abm_account_created", tenant=tenant_id, account_id=acct["id"])
    return acct


@router.get("/accounts/{account_id}")
async def get_account(
    account_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Get a single account by ID."""
    acct = _find_account(tenant_id, account_id)
    if not acct:
        raise HTTPException(404, "Account not found")
    return acct


@router.get("/accounts/{account_id}/intent")
async def get_account_intent(
    account_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Get aggregated intent data for an account from all 3 providers.

    Queries Bombora, G2, and TrustRadius in parallel and returns a
    composite intent score.  The score is cached on the account record.
    """
    acct = _find_account(tenant_id, account_id)
    if not acct:
        raise HTTPException(404, "Account not found")

    # Use empty configs -- in production these would come from the
    # tenant's stored connector configurations.
    try:
        intent = await _aggregator.aggregate_intent(
            domain=acct["domain"],
            bombora_config={},
            g2_config={},
            trustradius_config={},
        )
    except Exception as exc:
        logger.error("abm_intent_error", account_id=account_id, error=str(exc))
        raise HTTPException(502, f"Intent aggregation failed: {exc}") from exc

    # Cache on the account
    acct["intent_score"] = intent["composite_score"]
    acct["intent_data"] = intent
    acct["updated_at"] = datetime.now(UTC).isoformat()

    return intent


@router.post("/accounts/{account_id}/campaign")
async def launch_campaign(
    account_id: str,
    body: CampaignCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Launch a targeted campaign for a specific account."""
    acct = _find_account(tenant_id, account_id)
    if not acct:
        raise HTTPException(404, "Account not found")

    campaign = {
        "id": str(_uuid.uuid4()),
        "account_id": account_id,
        "campaign_name": body.campaign_name,
        "channel": body.channel,
        "budget_usd": body.budget_usd,
        "message": body.message,
        "target_persona": body.target_persona,
        "status": "launched",
        "launched_at": datetime.now(UTC).isoformat(),
        "metrics": {
            "impressions": 0,
            "clicks": 0,
            "conversions": 0,
            "spend_usd": 0.0,
        },
    }

    acct["campaigns"].append(campaign)
    _campaigns_store.setdefault(tenant_id, []).append(campaign)

    logger.info(
        "abm_campaign_launched",
        tenant=tenant_id,
        account_id=account_id,
        campaign_id=campaign["id"],
        channel=body.channel,
    )
    return campaign


@router.get("/dashboard")
async def abm_dashboard(
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """ABM executive dashboard summary.

    Returns total accounts, counts by tier, average intent score,
    top 10 accounts by intent, and pipeline influenced value.
    """
    accounts = _tenant_accounts(tenant_id)

    total = len(accounts)
    by_tier: dict[str, int] = {"1": 0, "2": 0, "3": 0}
    intent_sum = 0.0
    pipeline_influenced = 0.0

    for acct in accounts:
        tier = acct.get("tier", "2")
        by_tier[tier] = by_tier.get(tier, 0) + 1
        intent_sum += acct.get("intent_score", 0.0)

        # Estimate pipeline influence from campaigns
        for campaign in acct.get("campaigns", []):
            metrics = campaign.get("metrics", {})
            pipeline_influenced += metrics.get("spend_usd", 0.0)

    avg_intent = round(intent_sum / total, 2) if total > 0 else 0.0

    # Top 10 by intent score
    sorted_accounts = sorted(accounts, key=lambda a: a.get("intent_score", 0), reverse=True)
    top_10 = [
        {
            "id": a["id"],
            "company_name": a["company_name"],
            "domain": a["domain"],
            "tier": a["tier"],
            "intent_score": a["intent_score"],
        }
        for a in sorted_accounts[:10]
    ]

    return {
        "total_accounts": total,
        "by_tier": by_tier,
        "avg_intent_score": avg_intent,
        "top_10_by_intent": top_10,
        "pipeline_influenced_usd": round(pipeline_influenced, 2),
        "total_campaigns": len(_campaigns_store.get(tenant_id, [])),
    }
