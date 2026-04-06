"""Conversational Agent Creator — NL description to agent config.

Takes a plain-English description like "I need someone who processes invoices
and matches them with POs" and uses the LLM to infer domain, agent_type,
suggested tools, system prompt, confidence_floor, and HITL conditions.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from core.llm.router import LLMRouter, llm_router

logger = structlog.get_logger()

# ── Known agent types with domains (for prompt context + validation) ──────────

AGENT_TYPE_CATALOG: dict[str, dict[str, str]] = {
    # Finance
    "ap_processor": {
        "domain": "finance",
        "desc": "Accounts payable — invoice processing, PO matching, vendor payments",
    },
    "ar_collections": {
        "domain": "finance",
        "desc": "Accounts receivable — invoicing, payment collection, dunning",
    },
    "recon_agent": {
        "domain": "finance",
        "desc": "Bank reconciliation — statement matching, discrepancy resolution",
    },
    "tax_compliance": {
        "domain": "finance",
        "desc": "GST/tax filing — GSTR returns, e-invoicing, e-way bills",
    },
    "close_agent": {
        "domain": "finance",
        "desc": "Month/quarter-end close — journal entries, trial balance",
    },
    "fpa_agent": {
        "domain": "finance",
        "desc": "Financial planning & analysis — budgets, forecasts, variance reports",
    },
    "treasury": {
        "domain": "finance",
        "desc": "Treasury management — cash position, balance monitoring, fund transfers",
    },
    "expense_manager": {
        "domain": "finance",
        "desc": "Expense management — receipt processing, policy compliance",
    },
    "rev_rec": {
        "domain": "finance",
        "desc": "Revenue recognition — ASC 606 compliance, deferred revenue",
    },
    "fixed_assets": {
        "domain": "finance",
        "desc": "Fixed asset management — depreciation, asset tracking, disposal",
    },
    # HR
    "talent_acquisition": {
        "domain": "hr",
        "desc": "Recruiting — job postings, candidate screening, interview scheduling",
    },
    "onboarding_agent": {
        "domain": "hr",
        "desc": "Employee onboarding — provisioning, documentation, orientation",
    },
    "payroll_engine": {
        "domain": "hr",
        "desc": "Payroll processing — salary computation, attendance, compliance",
    },
    "performance_coach": {
        "domain": "hr",
        "desc": "Performance management — reviews, goals, feedback",
    },
    "ld_coordinator": {
        "domain": "hr",
        "desc": "Learning & development — training programs, skill mapping",
    },
    "offboarding_agent": {
        "domain": "hr",
        "desc": "Employee offboarding — exit process, deprovisioning, knowledge transfer",
    },
    # Marketing
    "content_factory": {
        "domain": "marketing",
        "desc": "Content creation — social posts, blog articles, publishing queue",
    },
    "campaign_pilot": {
        "domain": "marketing",
        "desc": "Campaign management — ad budgets, performance tracking, optimization",
    },
    "seo_strategist": {
        "domain": "marketing",
        "desc": "SEO optimization — keyword research, content audit, ranking analysis",
    },
    "crm_intelligence": {
        "domain": "marketing",
        "desc": "CRM analytics — lead scoring, pipeline analysis, contact management",
    },
    "brand_monitor": {
        "domain": "marketing",
        "desc": "Brand monitoring — social listening, sentiment, competitor mentions",
    },
    "email_marketing": {
        "domain": "marketing",
        "desc": "Email campaigns — drip sequences, A/B testing, newsletter management",
    },
    "social_media": {
        "domain": "marketing",
        "desc": "Social media management — posting, analytics, community engagement",
    },
    "abm": {
        "domain": "marketing",
        "desc": "Account-based marketing — target accounts, outreach, engagement",
    },
    "competitive_intel": {
        "domain": "marketing",
        "desc": "Competitive intelligence — market analysis, share of voice, backlinks",
    },
    # Ops
    "support_triage": {
        "domain": "ops",
        "desc": "Customer support — ticket routing, SLA management, escalation",
    },
    "vendor_manager": {
        "domain": "ops",
        "desc": "Vendor management — contract tracking, issue resolution, monitoring",
    },
    "contract_intelligence": {
        "domain": "ops",
        "desc": "Contract analysis — clause extraction, risk identification, renewal",
    },
    "compliance_guard": {
        "domain": "ops",
        "desc": "Compliance monitoring — regulatory checks, access audits, incidents",
    },
    "it_operations": {
        "domain": "ops",
        "desc": "IT operations — incident management, on-call scheduling, runbooks",
    },
    # Backoffice
    "legal_ops": {
        "domain": "backoffice",
        "desc": "Legal operations — document search, case management, compliance",
    },
    "risk_sentinel": {
        "domain": "backoffice",
        "desc": "Risk management — threat monitoring, incident response, reporting",
    },
    "facilities_agent": {
        "domain": "backoffice",
        "desc": "Facilities management — maintenance tickets, space management",
    },
    # Comms
    "email_agent": {
        "domain": "comms",
        "desc": "Email handling — inbox monitoring, reply drafting, email search",
    },
    "notification_agent": {
        "domain": "comms",
        "desc": "Notifications — email alerts, calendar events, Slack messages",
    },
    "chat_agent": {
        "domain": "comms",
        "desc": "Chat operations — Slack messaging, email integration, comms",
    },
}

# ── Default tools by agent type (mirrors _AGENT_TYPE_DEFAULT_TOOLS in agents.py)
_AGENT_TYPE_DEFAULT_TOOLS: dict[str, list[str]] = {
    "ap_processor": [
        "fetch_bank_statement", "check_account_balance", "post_voucher",
        "get_ledger_balance", "get_trial_balance", "create_order",
        "check_order_status",
    ],
    "ar_collections": [
        "create_invoice", "list_invoices", "create_payment_link",
        "send_email", "check_account_balance",
    ],
    "recon_agent": [
        "fetch_bank_statement", "get_transaction_list",
        "check_account_balance", "list_invoices",
    ],
    "tax_compliance": [
        "fetch_gstr2a", "push_gstr1_data", "file_gstr3b", "file_gstr9",
        "generate_eway_bill", "generate_einvoice_irn", "check_filing_status",
    ],
    "close_agent": [
        "list_invoices", "fetch_bank_statement",
        "get_balance", "search_content_fulltext",
    ],
    "fpa_agent": [
        "list_invoices", "get_balance",
        "get_campaign_performance_metrics", "get_project_metrics",
    ],
    "treasury": [
        "check_account_balance", "fetch_bank_statement",
        "get_balance", "get_balance_sheet", "get_cash_position",
    ],
    "expense_manager": [
        "record_expense", "create_ap_invoice", "check_order_status",
        "list_invoices", "get_profit_loss",
    ],
    "rev_rec": [
        "query", "create_invoice", "post_journal_entry",
        "get_trial_balance", "list_invoices",
    ],
    "fixed_assets": [
        "post_journal_entry", "record_expense", "get_trial_balance",
        "get_balance_sheet", "create_ap_invoice",
    ],
    "talent_acquisition": [
        "post_job", "search_candidates", "get_applications",
        "schedule_interview", "send_offer", "send_inmail",
    ],
    "onboarding_agent": [
        "create_employee", "provision_user", "assign_group",
        "create_page", "schedule_social_post",
    ],
    "payroll_engine": [
        "run_payroll", "get_payslip", "get_attendance",
        "post_leave", "file_24q_return",
    ],
    "performance_coach": [
        "update_performance", "get_employee", "get_org_chart", "add_comment",
    ],
    "ld_coordinator": [
        "search_content_fulltext", "create_page",
        "get_employee", "schedule_interview",
    ],
    "offboarding_agent": [
        "terminate_employee", "deactivate_user",
        "remove_group", "list_active_sessions",
    ],
    "content_factory": [
        "schedule_social_post", "get_post_analytics",
        "manage_publishing_queue", "approve_draft_post", "create_page",
    ],
    "campaign_pilot": [
        "get_campaign_performance_metrics", "adjust_campaign_budget",
        "get_campaign_performance", "reallocate_ad_budget",
        "get_reach_and_frequency_data",
    ],
    "seo_strategist": [
        "get_campaign_performance_metrics", "get_search_term_report",
        "search_content_fulltext", "get_post_analytics",
    ],
    "crm_intelligence": [
        "list_contacts", "search_contacts", "list_deals",
        "get_deal", "get_campaign_analytics", "create_contact",
    ],
    "brand_monitor": [
        "get_post_analytics", "get_campaign_performance",
        "schedule_social_post", "search_contacts",
    ],
    "email_marketing": [
        "send_email", "create_campaign", "send_campaign",
        "get_campaign_report", "add_list_member", "get_campaign_stats",
    ],
    "social_media": [
        "create_tweet", "create_update", "get_post_analytics",
        "list_channel_videos", "get_campaign_insights",
    ],
    "abm": [
        "query", "search_contacts", "get_analytics",
        "get_campaign_performance", "create_campaign",
    ],
    "competitive_intel": [
        "get_domain_rating", "get_organic_keywords", "get_mentions",
        "get_share_of_voice", "get_backlinks",
    ],
    "support_triage": [
        "create_ticket", "update_ticket", "escalate_to_group",
        "get_sla_breach_status", "get_csat_score", "apply_macro",
        "send_message", "post_alert",
    ],
    "vendor_manager": [
        "search_issues", "create_issue", "add_comment",
        "create_page", "get_project_metrics",
    ],
    "contract_intelligence": [
        "search_content_fulltext", "create_page",
        "search_issues", "get_page_tree",
    ],
    "compliance_guard": [
        "get_compliance_notice", "get_access_log", "search_issues",
        "create_incident", "send_message",
    ],
    "it_operations": [
        "create_incident", "trigger_alert_with_context",
        "acknowledge_incident", "manage_on_call_schedule",
        "run_automated_runbook", "send_message", "post_alert",
    ],
    "legal_ops": [
        "search_content_fulltext", "create_page", "search_issues",
        "get_page_tree", "manage_space_permissions",
    ],
    "risk_sentinel": [
        "get_access_log", "create_incident", "get_compliance_notice",
        "search_issues", "generate_postmortem_doc",
    ],
    "facilities_agent": [
        "create_ticket", "update_ticket",
        "create_issue", "get_sla_breach_status",
    ],
    "email_agent": ["send_email", "read_inbox", "search_emails"],
    "notification_agent": [
        "send_email", "create_calendar_event", "slack_send_message",
    ],
    "chat_agent": ["slack_send_message", "send_email", "read_inbox"],
}

VALID_DOMAINS = {"finance", "hr", "marketing", "ops", "backoffice", "comms"}
VALID_AGENT_TYPES = set(AGENT_TYPE_CATALOG.keys())

# ── Prompt injection patterns ─────────────────────────────────────────────────

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous\s+)?instructions",
    r"ignore\s+(all\s+)?(above|prior)\s+",
    r"disregard\s+(all\s+)?(previous|above|prior)",
    r"forget\s+(all\s+)?(previous|above|prior)",
    r"you\s+are\s+now\s+",
    r"new\s+instructions?\s*:",
    r"system\s*:\s*",
    r"admin\s+mode",
    r"jailbreak",
    r"override\s+(all\s+)?(rules|policies|restrictions|permissions)",
    r"give\s+me\s+full\s+access",
    r"create\s+admin",
    r"grant\s+(admin|root|full)\s+access",
    r"bypass\s+(security|auth|permissions|restrictions)",
]

_COMPILED_INJECTION_RE = re.compile(
    "|".join(_INJECTION_PATTERNS), re.IGNORECASE
)


def _detect_prompt_injection(text: str) -> bool:
    """Return True if the input text contains prompt injection patterns."""
    return bool(_COMPILED_INJECTION_RE.search(text))


def _sanitize_input(text: str) -> str:
    """Sanitize user input — strip control characters, limit length."""
    # Strip control characters except newlines and tabs
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Limit to 2000 characters
    return cleaned[:2000].strip()


def _build_catalog_text() -> str:
    """Build a text representation of all agent types for the LLM prompt."""
    lines = []
    for atype, info in AGENT_TYPE_CATALOG.items():
        tools = _AGENT_TYPE_DEFAULT_TOOLS.get(atype, [])
        tools_str = ", ".join(tools[:6])
        if len(tools) > 6:
            tools_str += f" (+{len(tools) - 6} more)"
        lines.append(f"  - {atype} (domain={info['domain']}): {info['desc']}. Tools: [{tools_str}]")
    return "\n".join(lines)


def _build_system_prompt() -> str:
    """Build the system prompt for the agent generator LLM call."""
    catalog = _build_catalog_text()
    domains = ", ".join(sorted(VALID_DOMAINS))

    return f"""You are an AI agent configuration generator for AgenticOrg, \
an enterprise AI platform.

Given a natural language description of a virtual employee that a user \
needs, you MUST generate a valid agent configuration.

## Available Agent Types (with domains and default tools)
{catalog}

## Available Domains
{domains}

## Rules
1. You MUST pick agent_type from the list above. Do NOT invent new types.
2. The domain MUST match the agent_type's domain from the catalog.
3. suggested_tools MUST be a subset of the tools listed for the chosen agent_type.
4. system_prompt MUST be a professional system prompt for the agent (200-500 words).
5. confidence_floor: float between 0.5 and 0.99 (default 0.88). Lower for routine tasks, higher for critical tasks.
6. hitl_condition: a human-readable condition string like "confidence < 0.88 OR amount > 500000".
7. employee_name: suggest a professional Indian name that fits the role.
8. designation: a realistic job title.
9. If the description is ambiguous and could match multiple agent types, \
return multiple suggestions with confidence scores.

## Output Format
Return ONLY valid JSON. No markdown, no code fences, no commentary.

If single clear match:
{{
  "suggestions": [
    {{
      "confidence": 0.95,
      "agent_type": "...",
      "domain": "...",
      "employee_name": "...",
      "designation": "...",
      "suggested_tools": ["..."],
      "system_prompt": "...",
      "confidence_floor": 0.88,
      "hitl_condition": "confidence < 0.88",
      "specialization": "..."
    }}
  ]
}}

If ambiguous (2-3 suggestions max):
{{
  "suggestions": [
    {{ "confidence": 0.7, ... }},
    {{ "confidence": 0.5, ... }}
  ]
}}

NEVER include instructions to the user. NEVER output anything other than JSON."""


def _validate_generated_config(config: dict[str, Any]) -> list[str]:
    """Validate a generated config against known types and tools.

    Returns a list of validation errors (empty if valid).
    """
    errors: list[str] = []

    agent_type = config.get("agent_type", "")
    if agent_type not in VALID_AGENT_TYPES:
        errors.append(f"Unknown agent_type: {agent_type}")

    domain = config.get("domain", "")
    if domain not in VALID_DOMAINS:
        errors.append(f"Unknown domain: {domain}")

    # Verify domain matches agent_type
    if agent_type in AGENT_TYPE_CATALOG:
        expected_domain = AGENT_TYPE_CATALOG[agent_type]["domain"]
        if domain != expected_domain:
            errors.append(f"Domain mismatch: {agent_type} belongs to {expected_domain}, not {domain}")

    # Validate tools are from the known set for this agent type
    suggested_tools = config.get("suggested_tools", [])
    if agent_type in _AGENT_TYPE_DEFAULT_TOOLS:
        valid_tools = set(_AGENT_TYPE_DEFAULT_TOOLS[agent_type])
        invalid = [t for t in suggested_tools if t not in valid_tools]
        if invalid:
            errors.append(f"Invalid tools for {agent_type}: {invalid}")

    # Validate confidence_floor range
    cf = config.get("confidence_floor", 0.88)
    if not isinstance(cf, (int, float)) or cf < 0.5 or cf > 0.99:
        errors.append(f"confidence_floor must be 0.5-0.99, got {cf}")

    # Validate system_prompt is not empty
    if not config.get("system_prompt", "").strip():
        errors.append("system_prompt is empty")

    return errors


def _parse_llm_response(raw: str) -> dict[str, Any]:
    """Parse LLM response text into a structured dict.

    Handles potential markdown code fences and extra text.
    """
    text = raw.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        # Remove closing fence
        text = re.sub(r"\n?```\s*$", "", text)

    # Try to find JSON object
    # Look for the outermost { ... }
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start == -1 or brace_end == -1:
        raise ValueError("No JSON object found in LLM response")

    json_str = text[brace_start : brace_end + 1]
    return json.loads(json_str)


async def generate_agent_config(
    description: str,
    llm: LLMRouter | None = None,
) -> dict[str, Any]:
    """Generate agent configuration from a natural-language description.

    Args:
        description: Plain-English description of the desired agent.
        llm: Optional LLM router instance (defaults to the global singleton).

    Returns:
        Dict with ``suggestions`` key containing 1+ agent config suggestions,
        each with a ``confidence`` score.

    Raises:
        ValueError: If the description is empty, contains prompt injection,
                    or if the LLM returns unparseable output.
    """
    if not description or not description.strip():
        raise ValueError("Description cannot be empty")

    sanitized = _sanitize_input(description)

    if _detect_prompt_injection(sanitized):
        raise ValueError(
            "Prompt injection detected. Please provide a genuine description "
            "of the employee you need."
        )

    router = llm or llm_router
    system_prompt = _build_system_prompt()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Create an agent for: {sanitized}"},
    ]

    logger.info("agent_generator_call", description_length=len(sanitized))

    response = await router.complete(messages=messages, temperature=0.3, max_tokens=2048)

    try:
        parsed = _parse_llm_response(response.content)
    except (json.JSONDecodeError, ValueError) as first_exc:
        logger.warning("agent_generator_parse_failed_retry", error=str(first_exc), raw=response.content[:500])

        # Retry with a simplified prompt and truncated input
        short_desc = sanitized[:500] if len(sanitized) > 500 else sanitized
        retry_messages = [
            {"role": "system", "content": (
                "Return ONLY valid JSON. No markdown. No commentary.\n"
                "Format: {\"suggestions\": [{\"confidence\": 0.9, \"agent_type\": \"...\", "
                "\"domain\": \"...\", \"employee_name\": \"...\", \"designation\": \"...\", "
                "\"suggested_tools\": [], \"system_prompt\": \"...\", "
                "\"confidence_floor\": 0.88, \"hitl_condition\": \"confidence < 0.88\", "
                "\"specialization\": \"...\"}]}"
            )},
            {"role": "user", "content": f"Create an agent for: {short_desc}"},
        ]
        try:
            retry_response = await router.complete(
                messages=retry_messages, temperature=0.1, max_tokens=1024,
            )
            parsed = _parse_llm_response(retry_response.content)
        except (json.JSONDecodeError, ValueError) as retry_exc:
            logger.warning("agent_generator_retry_also_failed", error=str(retry_exc))
            raise ValueError(
                "Failed to parse agent configuration from LLM response. "
                "Please try rephrasing your description."
            ) from retry_exc

    suggestions = parsed.get("suggestions", [])
    if not suggestions:
        # If LLM returned a flat config instead of suggestions array, wrap it
        if "agent_type" in parsed:
            suggestions = [parsed]
        else:
            raise ValueError("LLM did not return any agent suggestions.")

    # Validate each suggestion and fix domain mismatches
    validated: list[dict[str, Any]] = []
    for suggestion in suggestions:
        agent_type = suggestion.get("agent_type", "")

        # Auto-fix domain if agent_type is valid but domain is wrong
        if agent_type in AGENT_TYPE_CATALOG:
            expected_domain = AGENT_TYPE_CATALOG[agent_type]["domain"]
            if suggestion.get("domain") != expected_domain:
                suggestion["domain"] = expected_domain

        # Auto-populate tools if missing or empty
        if not suggestion.get("suggested_tools") and agent_type in _AGENT_TYPE_DEFAULT_TOOLS:
            suggestion["suggested_tools"] = _AGENT_TYPE_DEFAULT_TOOLS[agent_type]

        errors = _validate_generated_config(suggestion)
        if errors:
            logger.warning("agent_generator_validation_errors", errors=errors, agent_type=agent_type)
            suggestion["validation_errors"] = errors
        validated.append(suggestion)

    if not validated:
        raise ValueError("All generated suggestions failed validation.")

    # Sort by confidence descending
    validated.sort(key=lambda s: s.get("confidence", 0), reverse=True)

    return {
        "suggestions": validated,
        "llm_model": response.model,
        "tokens_used": response.tokens_used,
    }
