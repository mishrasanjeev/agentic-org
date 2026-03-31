"""AgenticOrg Python SDK — run AI agents, create from SOP, manage connectors.

Quickstart:
    from agenticorg import AgenticOrg

    client = AgenticOrg(api_key="your-key")
    agents = client.agents.list()
    result = client.agents.run("ap_processor", inputs={"invoice_id": "INV-001"})
"""

from agenticorg.client import AgenticOrg

__all__ = ["AgenticOrg"]
__version__ = "0.1.0"
