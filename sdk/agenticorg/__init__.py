"""AgenticOrg Python SDK - run agents, generate agents/workflows, use KB and MCP.

Quickstart:
    from agenticorg import AgenticOrg

    client = AgenticOrg(api_key="your-key")
    agents = client.agents.list()
    result = client.agents.run("ap_processor", inputs={"invoice_id": "INV-001"})
    workflow = client.workflows.generate("Review vendor renewal risk")
"""

from agenticorg.client import AgentRunResult, AgenticOrg

__all__ = ["AgenticOrg", "AgentRunResult"]
__version__ = "0.2.0"
