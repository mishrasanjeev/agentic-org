"""LangSmith integration for agent trace logging."""
from __future__ import annotations
from typing import Any
from core.config import external_keys

async def log_trace(agent_id: str, run_data: dict[str, Any]) -> None:
    if not external_keys.langsmith_api_key:
        return
    # In production, use langsmith SDK to log traces
    pass
