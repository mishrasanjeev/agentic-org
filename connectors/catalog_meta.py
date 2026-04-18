"""Native connector catalog metadata.

Maps each runtime-registered connector (from `connectors.registry`) to the
display metadata that the UI needs to render a catalog card: human-friendly
name, one-line description, and category hint. Before PR-B2 this metadata
lived as a hardcoded array in `ui/src/pages/Connectors.tsx`, which meant
any connector add/rename required a UI code change. Here it's the single
source of truth; the UI fetches it via `GET /api/v1/connectors/catalog`.

Any connector present in the registry but missing from this map receives
a humanized default ("tally" → "Tally", category inferred from the
connector class's `.category` attribute).
"""

from __future__ import annotations

CATALOG_META: dict[str, dict[str, str]] = {
    "github": {"display_name": "GitHub", "description": "Code hosting, PRs, issues, CI/CD."},
    "gmail": {"display_name": "Gmail", "description": "Google email: read, send, label."},
    "google_calendar": {
        "display_name": "Google Calendar",
        "description": "Schedule events, find free/busy, invite attendees.",
    },
    "langsmith": {
        "display_name": "LangSmith",
        "description": "Observability for LLM runs, traces, datasets.",
    },
    "s3": {"display_name": "AWS S3", "description": "Object storage — upload, download, list."},
    "slack": {
        "display_name": "Slack",
        "description": "Post messages, DMs, channel ops, slash commands.",
    },
    "hubspot": {"display_name": "HubSpot", "description": "CRM, contacts, deals, marketing."},
    "salesforce": {
        "display_name": "Salesforce",
        "description": "Accounts, opportunities, cases, custom objects.",
    },
    "jira": {"display_name": "Jira", "description": "Issue tracking + project management."},
    "zendesk": {"display_name": "Zendesk", "description": "Customer support tickets."},
    "stripe": {"display_name": "Stripe", "description": "Online payments, subscriptions."},
    "razorpay": {
        "display_name": "Razorpay",
        "description": "India-first payment gateway (UPI, cards, net banking).",
    },
    "tally": {
        "display_name": "Tally",
        "description": "India's most-used SMB accounting package.",
    },
    "quickbooks": {"display_name": "QuickBooks", "description": "SMB accounting."},
    "xero": {"display_name": "Xero", "description": "Cloud accounting."},
    "sap": {"display_name": "SAP", "description": "Enterprise ERP — FI, MM, SD, HR."},
    "oracle_fusion": {
        "display_name": "Oracle Fusion",
        "description": "Oracle Cloud ERP — GL, AP, AR, FA.",
    },
    "sendgrid": {"display_name": "SendGrid", "description": "Transactional + bulk email."},
    "twilio": {"display_name": "Twilio", "description": "SMS, WhatsApp, voice messaging."},
    "whatsapp_cloud": {
        "display_name": "WhatsApp Cloud API",
        "description": "Meta's WhatsApp Business Cloud API.",
    },
    "gstn": {
        "display_name": "GSTN",
        "description": "India GST network — returns, ITC, e-invoicing.",
    },
    "darwinbox": {
        "display_name": "Darwinbox",
        "description": "India-first HCM / payroll / onboarding.",
    },
    "account_aggregator": {
        "display_name": "Account Aggregator",
        "description": "India AA framework for consented financial data.",
    },
    "pinelabs_plural": {
        "display_name": "Pine Labs (Plural)",
        "description": "Subscriptions + payments for India.",
    },
    "trustradius": {
        "display_name": "TrustRadius",
        "description": "B2B software review + intent data.",
    },
    "g2": {"display_name": "G2", "description": "B2B software review + buyer-intent signals."},
    "bombora": {"display_name": "Bombora", "description": "B2B intent data across the web."},
    "teams_bot": {
        "display_name": "Microsoft Teams",
        "description": "Teams channel + DM automation.",
    },
}


def get_meta(name: str) -> dict[str, str]:
    """Return display metadata for a connector name, with defaults."""
    meta = CATALOG_META.get(name)
    if meta:
        return meta
    # Humanized fallback: `pinelabs_plural` → "Pinelabs Plural".
    return {
        "display_name": name.replace("_", " ").title(),
        "description": f"{name.replace('_', ' ').title()} connector.",
    }
