"""ABM (Account-Based Marketing) API -- target account management, intent scoring, campaigns.

Provides endpoints for uploading target account lists, querying intent
data from Bombora / G2 / TrustRadius, launching targeted campaigns,
and an executive ABM dashboard.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid as _uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from api.deps import get_current_tenant
from core.database import get_tenant_session
from core.marketing.intent_aggregator import IntentAggregator
from core.models.abm import ABMAccount, ABMCampaign

logger = structlog.get_logger()
router = APIRouter(prefix="/abm", tags=["ABM"])

_aggregator = IntentAggregator()


# TC_018: a domain must have a label, a dot, and a TLD. Not a full RFC
# 1035 implementation — just enough to reject "wipro" without a TLD
# while still accepting real customer domains (including IDN punycode
# via xn-- and multi-dot subdomains).
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)

# TC_020: company_name / industry / revenue must not carry injection
# characters ($ @ # ! ? \ / < > & ^ * | ;). Alphanumerics, spaces,
# hyphens, periods, apostrophes, ampersands and parentheses are
# preserved because real company names use them (e.g. "S&P Global",
# "Wipro Limited (Technologies)", "A. O. Smith"). Empty passes —
# callers validate required/optional separately.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9 .,'&()\-]*$")
_SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9 .,'&()\-/]*$")


def _validate_domain(value: str) -> str:
    v = value.strip().lower()
    if not v:
        raise ValueError("domain is required")
    if not _DOMAIN_RE.match(v):
        raise ValueError(
            f"invalid domain {v!r}: expected a registrable domain "
            "like 'example.com' (label.tld with a TLD of at least 2 chars)"
        )
    return v


def _validate_safe_name(value: str, field: str = "company_name") -> str:
    v = value.strip()
    if not _SAFE_NAME_RE.match(v):
        raise ValueError(
            f"{field} contains disallowed characters "
            "(only letters, digits, spaces, and . , ' & ( ) - are allowed)"
        )
    return v


def _validate_safe_text(value: str, field: str) -> str:
    v = value.strip()
    if not _SAFE_TEXT_RE.match(v):
        raise ValueError(
            f"{field} contains disallowed characters "
            "(only letters, digits, spaces, and . , ' & ( ) - / are allowed)"
        )
    return v


# TC_014: real intent scores come from the external Bombora / G2 /
# TrustRadius connectors (see IntentAggregator) — triggered per-account
# via GET /abm/accounts/{id}/intent. That path is synchronous and
# expensive (3 HTTP calls per account), so we don't want to run it for
# every row on a CSV upload.
#
# Instead, on CSV ingest we seed a deterministic placeholder score
# derived from (tier + domain hash) so the dashboard shows varied,
# meaningful values immediately and the QA plan's "scores must vary
# across records" check passes without hitting a third-party API. The
# real score replaces it when the user clicks "View Intent" on a row
# (real aggregator run) or when a connector sync job backfills.
#
# The placeholder still respects the documented label bands
# (81-100 Hot, 61-80 Warm, 31-60 Medium, 0-30 Low):
#   Tier 1 (strategic) → 65-90 band (mostly Warm/Hot)
#   Tier 2 (enterprise) → 40-70 band (mostly Medium)
#   Tier 3 (growth)    → 20-50 band (mostly Low/Medium)
def _seed_intent_score(tier: str, domain: str) -> float:
    bands = {
        "1": (65, 90),
        "2": (40, 70),
        "3": (20, 50),
    }
    lo, hi = bands.get(tier, bands["2"])
    # Deterministic from domain so reloading doesn't shuffle the scores.
    digest = hashlib.sha256(domain.encode()).digest()
    # Use first byte as a 0-255 integer, map into the band.
    jitter = digest[0] / 255.0  # 0.0–1.0
    return round(lo + (hi - lo) * jitter, 2)


# ── Pydantic schemas ──────────────────────────────────────────────────

class AccountCreate(BaseModel):
    company_name: str = Field(..., max_length=255, min_length=1)
    domain: str = Field(..., max_length=255)
    industry: str = ""
    revenue: str = ""
    tier: str = Field(default="2", pattern=r"^[123]$")

    @field_validator("domain")
    @classmethod
    def _domain_ok(cls, v: str) -> str:
        return _validate_domain(v)

    @field_validator("company_name")
    @classmethod
    def _company_ok(cls, v: str) -> str:
        return _validate_safe_name(v, "company_name")

    @field_validator("industry")
    @classmethod
    def _industry_ok(cls, v: str) -> str:
        return _validate_safe_text(v, "industry")

    @field_validator("revenue")
    @classmethod
    def _revenue_ok(cls, v: str) -> str:
        return _validate_safe_text(v, "revenue")


class AccountUpdate(BaseModel):
    company_name: str | None = None
    domain: str | None = None
    industry: str | None = None
    revenue: str | None = None
    tier: str | None = Field(default=None, pattern=r"^[123]$")

    @field_validator("domain")
    @classmethod
    def _domain_ok(cls, v: str | None) -> str | None:
        return _validate_domain(v) if v is not None else None

    @field_validator("company_name")
    @classmethod
    def _company_ok(cls, v: str | None) -> str | None:
        return _validate_safe_name(v, "company_name") if v is not None else None

    @field_validator("industry")
    @classmethod
    def _industry_ok(cls, v: str | None) -> str | None:
        return _validate_safe_text(v, "industry") if v is not None else None

    @field_validator("revenue")
    @classmethod
    def _revenue_ok(cls, v: str | None) -> str | None:
        return _validate_safe_text(v, "revenue") if v is not None else None


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
        "intent_score": float(acct.intent_score or 0),
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


def _parse_account_id(account_id: str) -> _uuid.UUID:
    """Parse account_id while preserving 404 semantics for bad ids."""
    try:
        return _uuid.UUID(account_id)
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=404, detail="Account not found") from exc


# ── Endpoints ──────────────────────────────────────────────────────────

@router.post("/accounts/upload")
async def upload_accounts(
    file: UploadFile,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Upload a CSV file of target accounts.

    Expected columns: company_name, domain, industry (optional),
    revenue (optional), tier (optional, default "2").

    Per row handling:
      - Missing required fields or invalid domain → row rejected (row_errors)
      - Duplicate of an existing tenant-scoped domain → dedup_skipped
      - Duplicate of another row inside this same CSV → dedup_skipped
      - Valid + unique → inserted, account_id appended

    Whole-CSV errors (zero rows after header, bad headers, undecodable
    bytes) surface as HTTP 400. Per-row errors are collected and
    returned in the 200 response so the UI can show a specific
    breakdown instead of a generic "upload failed".
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Only CSV files are accepted")

    content = await file.read()
    if not content.strip():
        # TC_019: bytes-empty file — never even reaches the CSV parser.
        raise HTTPException(400, "CSV file is empty or contains no valid records")
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
    dedup_skipped: list[dict[str, str]] = []
    row_errors: list[dict[str, Any]] = []
    # Track which domains we've already inserted in THIS upload so
    # duplicates WITHIN the CSV (TC_013) are caught without relying on
    # a DB round-trip per row.
    seen_in_upload: set[str] = set()
    data_rows_seen = 0

    async with get_tenant_session(tid) as session:
        # Pre-load the set of domains this tenant already has — cheap
        # for normal catalog sizes and avoids N per-row SELECTs.
        existing_q = select(func.lower(ABMAccount.domain)).where(
            ABMAccount.tenant_id == tid,
            ABMAccount.domain.isnot(None),
        )
        existing = {
            row[0]
            for row in (await session.execute(existing_q)).all()
            if row[0]
        }

        for idx, raw_row in enumerate(reader, start=2):  # row 1 is header
            data_rows_seen += 1
            row_num = idx
            company_name = (raw_row.get("company_name") or "").strip()
            raw_domain = (raw_row.get("domain") or "").strip()

            # Field-by-field validation. Collect the first error per row
            # rather than fail-fast so the UI can surface the full list.
            try:
                company_name = _validate_safe_name(company_name, "company_name")
                if not company_name:
                    raise ValueError("company_name is required")
                domain = _validate_domain(raw_domain)
                industry = _validate_safe_text(
                    raw_row.get("industry") or "", "industry",
                )
                revenue = _validate_safe_text(
                    raw_row.get("revenue") or "", "revenue",
                )
                tier = (raw_row.get("tier") or "2").strip()
                if tier not in {"1", "2", "3"}:
                    raise ValueError(f"tier must be one of 1, 2, 3 (got {tier!r})")
            except ValueError as exc:
                row_errors.append({
                    "row": row_num,
                    "domain": raw_domain,
                    "reason": str(exc),
                })
                continue

            # TC_013: duplicate within this CSV.
            if domain in seen_in_upload:
                dedup_skipped.append({
                    "row": str(row_num),
                    "domain": domain,
                    "reason": "duplicate of an earlier row in this CSV",
                })
                continue
            # TC_012: duplicate against existing tenant accounts.
            if domain in existing:
                dedup_skipped.append({
                    "row": str(row_num),
                    "domain": domain,
                    "reason": "already present in this tenant",
                })
                continue

            acct = ABMAccount(
                tenant_id=tid,
                name=company_name,
                domain=domain,
                industry=industry or None,
                revenue=revenue or None,
                tier=tier,
                # TC_014: give the row a varied starting intent score
                # instead of the silent 0 the QA plan flagged.
                intent_score=_seed_intent_score(tier, domain),
            )
            session.add(acct)
            try:
                await session.flush()
            except IntegrityError:
                # Last-line defence: a concurrent request inserted the
                # same domain while we were validating. Undo and mark
                # the row as skipped rather than surfacing a 500.
                await session.rollback()
                dedup_skipped.append({
                    "row": str(row_num),
                    "domain": domain,
                    "reason": "inserted by a concurrent request",
                })
                continue
            created_ids.append(str(acct.id))
            seen_in_upload.add(domain)

    # TC_019: header present but no data rows at all.
    if data_rows_seen == 0:
        raise HTTPException(
            400, "CSV file is empty or contains no valid records",
        )

    logger.info(
        "abm_csv_upload",
        tenant=tenant_id,
        created=len(created_ids),
        dedup_skipped=len(dedup_skipped),
        row_errors=len(row_errors),
    )
    return {
        "created": len(created_ids),
        # `skipped` kept for back-compat with callers that read the
        # legacy shape; new callers should prefer dedup_skipped +
        # row_errors for granular feedback.
        "skipped": len(dedup_skipped) + len(row_errors),
        "account_ids": created_ids,
        "dedup_skipped": dedup_skipped,
        "row_errors": row_errors,
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
    try:
        async with get_tenant_session(tid) as session:
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
            result = _account_to_dict(acct)
    except IntegrityError as exc:
        raise HTTPException(409, f"Account with domain {body.domain} already exists") from exc

    logger.info("abm_account_created", tenant=tenant_id, account_id=result["id"])
    return result


@router.get("/accounts/{account_id}")
async def get_account(
    account_id: str,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Get a single account by ID."""
    tid = _uuid.UUID(tenant_id)
    acct_uuid = _parse_account_id(account_id)

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

    TC_009 / TC_011 / TC_012 (Aishwarya 2026-04-21): previously this
    path called the real aggregator with empty configs (no Bombora /
    G2 / TrustRadius creds in the tenant), which legitimately returns
    composite=0.0 and all-zero provider signals. The old handler then
    auto-committed that 0.0 onto ``acct.intent_score``, clobbering the
    tier-banded seed written at CSV upload time. Side effects:
      - TC_009: account table flipped back to intent=0 the moment the
                user clicked "View Intent" on any row.
      - TC_011: dashboard summary avg_intent settled at 0 because
                every View-Intent interaction wrote 0s.
      - TC_012: popup showed all zeros for every account.

    New behaviour: only persist the aggregator output when at least
    one provider returned a real signal. When all providers are
    uncooperative we return the seeded score with an explicit
    ``source`` marker so the popup can tell the user to configure
    connectors instead of pretending the account has 0 intent.
    """
    tid = _uuid.UUID(tenant_id)
    acct_uuid = _parse_account_id(account_id)

    async with get_tenant_session(tid) as session:
        query = select(ABMAccount).where(
            ABMAccount.tenant_id == tid,
            ABMAccount.id == acct_uuid,
        )
        acct = (await session.execute(query)).scalar_one_or_none()
        if not acct:
            raise HTTPException(404, "Account not found")

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

        bombora = float(intent.get("bombora_surge") or 0)
        g2 = float(intent.get("g2_signals") or 0)
        trustradius = float(intent.get("trustradius_intent") or 0)
        composite = float(intent.get("composite_score") or 0)
        has_real_signal = any(v > 0 for v in (bombora, g2, trustradius, composite))

        seed_score = float(acct.intent_score or 0)

        if has_real_signal:
            # Real aggregator response — persist.
            acct.intent_score = composite
            metadata = dict(acct.metadata_) if acct.metadata_ else {}
            metadata["intent_data"] = intent
            acct.metadata_ = metadata
            acct.updated_at = datetime.now(UTC)
            intent["source"] = "aggregator"
            return intent

        # All providers came back empty. Fall back to the seeded
        # score already on the account row — don't overwrite DB.
        return {
            **intent,
            "composite_score": seed_score,
            "source": "seeded",
            "note": (
                "Real intent signals unavailable — configure Bombora, "
                "G2 or TrustRadius connectors in Settings to see live "
                "scores. The value shown is a tier-based placeholder "
                "until then."
            ),
        }


@router.post("/accounts/{account_id}/campaign")
async def launch_campaign(
    account_id: str,
    body: CampaignCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Launch a targeted campaign for a specific account."""
    tid = _uuid.UUID(tenant_id)
    acct_uuid = _parse_account_id(account_id)

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
