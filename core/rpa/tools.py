"""LangChain tool wrapper for Browser RPA execution.

Registers ``browser_rpa_execute`` as a tool that agents can call,
and hooks it into the ConnectorRegistry as the ``rpa`` connector.
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.tools import StructuredTool

logger = structlog.get_logger()


async def _browser_rpa_execute(
    script_name: str,
    params: dict[str, Any] | None = None,
    timeout_s: int = 60,
) -> dict[str, Any]:
    """Execute a browser RPA script and return structured results.

    Parameters
    ----------
    script_name : str
        Name of the RPA script module (e.g., ``epfo_ecr_download``).
    params : dict | None
        Parameters to pass to the script.
    timeout_s : int
        Maximum execution time in seconds.
    """
    from core.rpa.executor import execute_rpa_script

    result = await execute_rpa_script(
        script_name=script_name,
        params=params or {},
        timeout_s=timeout_s,
    )
    return result


# LangChain StructuredTool that agents can invoke
browser_rpa_execute = StructuredTool.from_function(
    coroutine=_browser_rpa_execute,
    name="browser_rpa_execute",
    description=(
        "Execute a browser RPA (Robotic Process Automation) script in a headless "
        "Playwright browser. Used for automating government portal interactions "
        "(EPFO, MCA, GST), downloading reports, and scraping legacy web applications. "
        "Provide the script_name (e.g., 'epfo_ecr_download', 'mca_company_search') "
        "and any required params as a dict."
    ),
)


def register_rpa_connector() -> None:
    """Register the RPA tool in the ConnectorRegistry.

    This is called during application startup so that agents
    with ``rpa`` in their authorized_tools can invoke browser automation.
    """
    try:
        from connectors.registry import ConnectorRegistry

        # Register as a composio-style tool entry so the tool adapter can find it
        ConnectorRegistry._composio_tools["browser_rpa_execute"] = {
            "tool_name": "browser_rpa_execute",
            "app": "rpa",
            "description": "Browser RPA automation via headless Playwright",
            "category": "automation",
        }
        logger.info("rpa_connector_registered")
    except ImportError:
        logger.debug("rpa_connector_registry_unavailable")
