"""KPI endpoints — CFO & CMO executive dashboards.

Returns demo data for now; in production these aggregate from agent task
outputs, connector sync results, and the analytics warehouse.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.deps import get_current_tenant

router = APIRouter()


# ---------------------------------------------------------------------------
# CFO KPIs
# ---------------------------------------------------------------------------

@router.get("/kpis/cfo")
def get_cfo_kpis(
    tenant_id: str = Depends(get_current_tenant),
    company_id: str = Query("default", description="Multi-company selector"),
):
    """Finance KPIs for the CFO executive dashboard (demo data)."""
    return {
        "demo": True,
        "company_id": company_id,
        "cash_runway_months": 14.2,
        "cash_runway_trend": 1.8,
        "burn_rate": 18_50_000,
        "burn_rate_trend": -3.2,
        "dso_days": 42,
        "dso_trend": -5.1,
        "dpo_days": 38,
        "dpo_trend": 2.4,
        "ar_aging": {
            "0_30": 32_00_000,
            "31_60": 14_50_000,
            "61_90": 6_80_000,
            "90_plus": 2_10_000,
        },
        "ap_aging": {
            "0_30": 22_00_000,
            "31_60": 9_50_000,
            "61_90": 3_20_000,
            "90_plus": 80_000,
        },
        "monthly_pl": [
            {
                "month": "2026-01",
                "revenue": 68_00_000,
                "cogs": 19_00_000,
                "gross_margin": 49_00_000,
                "opex": 34_00_000,
                "net_income": 15_00_000,
            },
            {
                "month": "2026-02",
                "revenue": 72_50_000,
                "cogs": 20_00_000,
                "gross_margin": 52_50_000,
                "opex": 35_50_000,
                "net_income": 17_00_000,
            },
            {
                "month": "2026-03",
                "revenue": 78_00_000,
                "cogs": 21_50_000,
                "gross_margin": 56_50_000,
                "opex": 37_00_000,
                "net_income": 19_50_000,
            },
        ],
        "bank_balances": [
            {"account": "HDFC Current A/c", "balance": 1_45_00_000, "currency": "INR"},
            {"account": "ICICI Savings A/c", "balance": 62_00_000, "currency": "INR"},
            {"account": "SBI FD", "balance": 50_00_000, "currency": "INR"},
            {"account": "Wise USD A/c", "balance": 48_500, "currency": "USD"},
        ],
        "pending_approvals_count": 7,
        "tax_calendar": [
            {
                "filing": "GST-3B (March)",
                "due_date": "2026-04-20",
                "status": "pending",
            },
            {
                "filing": "TDS 26Q (Q4)",
                "due_date": "2026-05-15",
                "status": "upcoming",
            },
            {
                "filing": "Advance Tax Q1",
                "due_date": "2026-06-15",
                "status": "upcoming",
            },
            {
                "filing": "ROC Annual Filing",
                "due_date": "2026-09-30",
                "status": "upcoming",
            },
        ],
    }


# ---------------------------------------------------------------------------
# CMO KPIs
# ---------------------------------------------------------------------------

@router.get("/kpis/cmo")
def get_cmo_kpis(
    tenant_id: str = Depends(get_current_tenant),
    company_id: str = Query("default", description="Multi-company selector"),
):
    """Marketing KPIs for the CMO executive dashboard (demo data)."""
    return {
        "demo": True,
        "company_id": company_id,
        "cac": 3_200,
        "cac_trend": -8.5,
        "mqls": 284,
        "mqls_trend": 12.3,
        "sqls": 67,
        "sqls_trend": 9.1,
        "pipeline_value": 1_42_00_000,
        "pipeline_trend": 15.6,
        "roas_by_channel": {
            "Google Ads": 4.2,
            "Meta Ads": 3.1,
            "LinkedIn": 2.8,
            "Organic": 7.6,
        },
        "email_performance": {
            "open_rate": 34.2,
            "click_rate": 4.8,
            "unsubscribe_rate": 0.3,
        },
        "social_engagement": {
            "Twitter": 12_400,
            "LinkedIn": 8_900,
            "Instagram": 15_600,
        },
        "website_traffic": {
            "sessions": 48_200,
            "users": 31_500,
            "bounce_rate": 42.1,
            "sessions_trend": [
                {"date": "2026-03-01", "sessions": 1_420},
                {"date": "2026-03-04", "sessions": 1_580},
                {"date": "2026-03-07", "sessions": 1_350},
                {"date": "2026-03-10", "sessions": 1_720},
                {"date": "2026-03-13", "sessions": 1_890},
                {"date": "2026-03-16", "sessions": 1_640},
                {"date": "2026-03-19", "sessions": 2_010},
                {"date": "2026-03-22", "sessions": 1_950},
                {"date": "2026-03-25", "sessions": 2_200},
                {"date": "2026-03-28", "sessions": 2_350},
            ],
        },
        "content_top_pages": [
            {"page": "/blog/ai-virtual-employees-guide", "views": 4_820, "avg_time_sec": 245},
            {"page": "/blog/automate-accounts-payable", "views": 3_150, "avg_time_sec": 198},
            {"page": "/pricing", "views": 2_900, "avg_time_sec": 130},
            {"page": "/blog/gst-compliance-automation", "views": 2_340, "avg_time_sec": 210},
            {"page": "/case-studies/enterprise-roi", "views": 1_870, "avg_time_sec": 310},
        ],
        "brand_sentiment_score": 78,
        "brand_sentiment_trend": 3.2,
        "pending_content_approvals": 4,
    }
