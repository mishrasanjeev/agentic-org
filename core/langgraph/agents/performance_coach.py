"""Performance Coach agent -- LangGraph implementation."""



from __future__ import annotations

import os
from typing import Any

from core.langgraph.agent_graph import build_agent_graph
from core.langgraph.runner import run_agent

DEFAULT_TOOLS = ['update_performance', 'get_employee', 'get_org_chart', 'add_comment']



PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "agents", "prompts")





def load_prompt(variables: dict[str, str] | None = None) -> str:

    path = os.path.join(PROMPTS_DIR, "performance_coach.prompt.txt")

    with open(path) as f:

        template = f.read()

    for key, val in (variables or {}).items():

        template = template.replace("{{}" + key + "}}", val)

    return template





def build_graph(

    prompt_variables: dict[str, str] | None = None,

    llm_model: str = "",

    confidence_floor: float = 0.8,

    hitl_condition: str = "",

    authorized_tools: list[str] | None = None,

    connector_config: dict[str, Any] | None = None,

):

    return build_agent_graph(

        system_prompt=load_prompt(prompt_variables),

        authorized_tools=authorized_tools or DEFAULT_TOOLS,

        llm_model=llm_model,

        confidence_floor=confidence_floor,

        hitl_condition=hitl_condition,

        connector_config=connector_config,

    )





async def run(

    agent_id: str,

    tenant_id: str,

    task_input: dict[str, Any],

    prompt_variables: dict[str, str] | None = None,

    llm_model: str = "",

    confidence_floor: float = 0.8,

    hitl_condition: str = "",

    authorized_tools: list[str] | None = None,

    grant_token: str = "",

    connector_config: dict[str, Any] | None = None,

) -> dict[str, Any]:

    return await run_agent(

        agent_id=agent_id,

        agent_type="performance_coach",

        domain="hr",

        tenant_id=tenant_id,

        system_prompt=load_prompt(prompt_variables),

        authorized_tools=authorized_tools or DEFAULT_TOOLS,

        task_input=task_input,

        llm_model=llm_model,

        confidence_floor=confidence_floor,

        hitl_condition=hitl_condition,

        grant_token=grant_token,

        connector_config=connector_config,

    )

