"""SOP Parser — reads business documents and extracts agent configuration.

Accepts PDF, markdown, or plain text documents describing a business process
(SOP, BRD, PRD). Uses an LLM to extract:
  - Process steps (sequential workflow)
  - Required connectors and tools
  - Approval/escalation gates (HITL conditions)
  - Confidence thresholds
  - Org hierarchy placement

The output is a draft agent config that a human reviews before deployment.
"""

from __future__ import annotations

import json
import os
from typing import Any

import structlog

from core.langgraph.llm_factory import create_chat_model
from core.langgraph.tool_adapter import _build_tool_index

logger = structlog.get_logger()

SOP_PARSER_SYSTEM_PROMPT = """You are an SOP Parser for AgenticOrg — an enterprise AI agent platform.

Your job: read a business process document (SOP, BRD, PRD, or process description)
and extract a structured agent configuration.

You MUST return valid JSON with this exact structure:
{
  "agent_name": "Human-readable name for this agent (e.g., 'Invoice Processor')",
  "agent_type": "snake_case type key (e.g., 'ap_processor', 'payroll_engine')",
  "domain": "one of: finance, hr, marketing, ops, backoffice",
  "description": "One-line description of what this agent does",
  "steps": [
    {
      "step_number": 1,
      "name": "Step name",
      "description": "What happens in this step",
      "required_tools": ["tool_name_1", "tool_name_2"],
      "hitl_required": false,
      "hitl_condition": ""
    }
  ],
  "required_tools": ["all_unique_tools_across_all_steps"],
  "hitl_conditions": ["condition1", "condition2"],
  "confidence_floor": 0.88,
  "escalation_chain": ["role1", "role2"],
  "suggested_prompt": "A system prompt for this agent based on the SOP content"
}

AVAILABLE TOOLS (use only from this list):
{available_tools}

RULES:
- Map every action in the SOP to a specific tool from the available list
- If the SOP mentions approvals, reviews, or sign-offs, mark those steps as hitl_required=true
- Set confidence_floor based on the risk level described (financial=0.90+, routine=0.85, low-risk=0.80)
- The suggested_prompt should follow the AgenticOrg prompt format with
  <processing_sequence>, <escalation_rules>, <anti_hallucination>, <output_format>
- If a tool doesn't exist in the available list, use the closest match and note it
- Always return valid JSON — no markdown wrapping, no explanation text
"""


def extract_text_from_document(file_path: str, content_type: str = "") -> str:
    """Extract text from a document file.

    Supports: PDF (.pdf), Markdown (.md), plain text (.txt).
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf" or "pdf" in content_type:
        return _extract_from_pdf(file_path)
    elif ext in (".md", ".markdown"):
        with open(file_path, encoding="utf-8") as f:
            return f.read()
    else:
        with open(file_path, encoding="utf-8") as f:
            return f.read()


def _extract_from_pdf(file_path: str) -> str:
    """Extract text from PDF using pypdf (BSD license)."""
    from pypdf import PdfReader

    reader = PdfReader(file_path)
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


async def parse_sop_document(
    document_text: str,
    llm_model: str = "",
    domain_hint: str = "",
) -> dict[str, Any]:
    """Parse an SOP document and return a draft agent configuration.

    Args:
        document_text: The full text of the SOP/BRD/PRD document.
        llm_model: LLM model to use for parsing.
        domain_hint: Optional hint for the domain (finance, hr, etc.)

    Returns:
        Dict with the parsed agent configuration (draft, needs human review).
    """
    # Build available tools list for the prompt
    tool_index = _build_tool_index()
    tools_by_connector: dict[str, list[str]] = {}
    for tool_name, (connector_name, desc) in tool_index.items():
        tools_by_connector.setdefault(connector_name, []).append(
            f"  - {tool_name}: {desc}" if desc else f"  - {tool_name}"
        )

    available_tools_text = ""
    for connector, tool_list in sorted(tools_by_connector.items()):
        available_tools_text += f"\n{connector}:\n" + "\n".join(tool_list) + "\n"

    system_prompt = SOP_PARSER_SYSTEM_PROMPT.replace(
        "{available_tools}", available_tools_text
    )

    # Build the user message
    user_message = "Parse this business process document and extract the agent configuration:\n\n"
    if domain_hint:
        user_message += f"Domain hint: {domain_hint}\n\n"
    user_message += f"--- DOCUMENT START ---\n{document_text[:15000]}\n--- DOCUMENT END ---"

    # Call LLM
    llm = create_chat_model(model=llm_model, temperature=0.1, max_tokens=4096)
    from langchain_core.messages import HumanMessage, SystemMessage

    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ])

    # Parse the response
    content = response.content or ""
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        content = "\n".join(lines).strip()

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("sop_parser_invalid_json", content_preview=content[:200])
        parsed = {
            "agent_name": "Parsed Agent",
            "agent_type": "custom_agent",
            "domain": domain_hint or "ops",
            "description": "Agent parsed from SOP (JSON parse failed — needs manual config)",
            "steps": [],
            "required_tools": [],
            "hitl_conditions": [],
            "confidence_floor": 0.88,
            "escalation_chain": [],
            "suggested_prompt": content,
            "parse_error": "LLM output was not valid JSON — raw output preserved in suggested_prompt",
        }

    # Validate tools against available tools
    parsed["_available_tools"] = list(tool_index.keys())
    unknown_tools = [
        t for t in parsed.get("required_tools", [])
        if t not in tool_index
    ]
    if unknown_tools:
        parsed["_unknown_tools"] = unknown_tools

    parsed["_parse_status"] = "draft"
    return parsed
