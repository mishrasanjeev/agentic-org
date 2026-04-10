"""Insurance Industry Pack — pre-configured agents and workflows for insurance carriers and MGAs."""

from typing import Any

INSURANCE_PACK: dict[str, Any] = {
    "id": "insurance",
    "name": "Insurance Pack",
    "description": (
        "AI agents for underwriting risk assessment, claims adjudication, "
        "policy lifecycle management, and fraud detection for insurance "
        "carriers and managing general agents."
    ),
    "version": "1.0.0",
    "pricing": {"inr_monthly_per_client": 7999, "usd_monthly_per_client": 95},
    "agents": [
        {
            "name": "Underwriting Analyst Agent",
            "domain": "finance",
            "description": (
                "Risk assessment, policy underwriting, premium calculation, "
                "and referral routing for complex risks"
            ),
            "tools": [
                "knowledge_base_search",
                "composio:salesforce:get_account",
                "composio:salesforce:get_opportunity",
                "composio:salesforce:update_record",
            ],
            "llm_model": "gpt-4o",
            "hitl_condition": "high_value_or_complex_risk",
            "confidence_floor": 0.90,
        },
        {
            "name": "Claims Adjudicator Agent",
            "domain": "finance",
            "description": (
                "Claims evaluation, coverage verification, reserve estimation, "
                "settlement recommendation, and fraud flag routing"
            ),
            "tools": [
                "composio:salesforce:get_claim",
                "composio:salesforce:update_record",
                "knowledge_base_search",
            ],
            "llm_model": "gpt-4o",
            "hitl_condition": "high_value_or_fraud_indicator",
            "confidence_floor": 0.92,
        },
        {
            "name": "Policy Manager Agent",
            "domain": "ops",
            "description": (
                "Policy lifecycle from issuance through renewal, endorsement "
                "processing, premium adjustments, and cancellation handling"
            ),
            "tools": [
                "composio:salesforce:get_account",
                "composio:salesforce:update_record",
                "composio:stripe:get_subscription",
                "composio:stripe:update_subscription",
            ],
            "llm_model": "gpt-4o",
            "hitl_condition": "cancellation_or_major_endorsement",
            "confidence_floor": 0.90,
        },
        {
            "name": "Fraud Detector Agent",
            "domain": "ops",
            "description": (
                "Claims pattern analysis, anomaly scoring, "
                "evidence summaries for SIU review"
            ),
            "tools": [
                "knowledge_base_search",
                "composio:salesforce:get_claim",
            ],
            "llm_model": "gpt-4o",
            "confidence_floor": 0.88,
        },
    ],
    "workflows": [
        "new_policy_issuance",
        "claims_processing",
        "fraud_investigation",
    ],
}
