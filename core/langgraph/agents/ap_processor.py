"""AP Processor agent — LangGraph implementation.

Migrated from core/agents/finance/ap_processor.py.
Uses the same prompt file, same tools, same HITL logic —
but runs on LangGraph StateGraph instead of custom BaseAgent.
"""

from __future__ import annotations

import os
from typing import Any

from core.langgraph.agent_graph import build_agent_graph
from core.langgraph.runner import run_agent

# Default tools for AP Processor (same as _AGENT_TYPE_DEFAULT_TOOLS)
AP_PROCESSOR_TOOLS = [
    "fetch_bank_statement",
    "create_charge",
    "initiate_neft",
    "create_payout",
    "get_settlement_report",
]

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "agents", "prompts")


def load_ap_processor_prompt(variables: dict[str, str] | None = None) -> str:
    """Load the AP Processor prompt template with variable substitution."""
    path = os.path.join(PROMPTS_DIR, "ap_processor.prompt.txt")
    with open(path) as f:
        template = f.read()
    for key, val in (variables or {}).items():
        template = template.replace("{{" + key + "}}", val)
    return template


def build_ap_processor_graph(
    prompt_variables: dict[str, str] | None = None,
    llm_model: str = "",
    confidence_floor: float = 0.88,
    hitl_condition: str = "",
    authorized_tools: list[str] | None = None,
    connector_config: dict[str, Any] | None = None,
):
    """Build a compiled LangGraph for the AP Processor agent.

    This can be used directly for testing or embedded in a workflow sub-graph.
    """
    prompt = load_ap_processor_prompt(prompt_variables)
    tools = authorized_tools or AP_PROCESSOR_TOOLS

    return build_agent_graph(
        system_prompt=prompt,
        authorized_tools=tools,
        llm_model=llm_model,
        confidence_floor=confidence_floor,
        hitl_condition=hitl_condition,
        connector_config=connector_config,
    )


async def run_ap_processor(
    agent_id: str,
    tenant_id: str,
    task_input: dict[str, Any],
    prompt_variables: dict[str, str] | None = None,
    llm_model: str = "",
    confidence_floor: float = 0.88,
    hitl_condition: str = "",
    authorized_tools: list[str] | None = None,
    grant_token: str = "",
    connector_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the AP Processor agent via LangGraph.

    This is the public API — called by the agent execution endpoint.
    """
    prompt = load_ap_processor_prompt(prompt_variables)
    tools = authorized_tools or AP_PROCESSOR_TOOLS

    return await run_agent(
        agent_id=agent_id,
        agent_type="ap_processor",
        domain="finance",
        tenant_id=tenant_id,
        system_prompt=prompt,
        authorized_tools=tools,
        task_input=task_input,
        llm_model=llm_model,
        confidence_floor=confidence_floor,
        hitl_condition=hitl_condition,
        grant_token=grant_token,
        connector_config=connector_config,
    )
