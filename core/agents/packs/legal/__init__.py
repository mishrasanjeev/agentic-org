"""Legal Industry Pack — pre-configured agents and workflows for law firms and legal departments."""

from typing import Any

LEGAL_PACK: dict[str, Any] = {
    "id": "legal",
    "name": "Legal Pack",
    "description": (
        "AI agents for contract review, case research, document drafting, "
        "and compliance checking for law firms and corporate legal teams."
    ),
    "version": "1.0.0",
    "pricing": {"inr_monthly_per_client": 7999, "usd_monthly_per_client": 95},
    "agents": [
        {
            "name": "Contract Review Agent",
            "domain": "ops",
            "description": (
                "Analyzes contracts for key clauses, risk exposure, "
                "missing terms, and liability provisions"
            ),
            "tools": [
                "knowledge_base_search",
                "composio:docusign:get_document",
                "composio:docusign:list_envelopes",
                "composio:google:search",
            ],
            "llm_model": "gpt-4o",
            "hitl_condition": "always_before_legal_opinion",
        },
        {
            "name": "Case Research Agent",
            "domain": "ops",
            "description": (
                "Researches case law, statutes, and legal precedents "
                "across jurisdictions with citation verification"
            ),
            "tools": [
                "knowledge_base_search",
                "composio:google:search",
            ],
            "llm_model": "gpt-4o",
            "hitl_condition": "always_before_legal_opinion",
        },
        {
            "name": "Document Drafting Agent",
            "domain": "ops",
            "description": (
                "Generates legal documents from firm templates, "
                "enforces style guides, and marks sections for attorney review"
            ),
            "tools": [
                "knowledge_base_search",
                "composio:google_docs:create_document",
                "composio:docusign:create_envelope",
            ],
            "llm_model": "gpt-4o",
            "hitl_condition": "always_before_finalization",
        },
    ],
    "workflows": [
        "contract_lifecycle",
        "litigation_support",
    ],
}
