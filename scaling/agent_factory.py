"""Agent Factory — create, clone, manage agents."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from auth.scopes import validate_clone_scopes

logger = structlog.get_logger()


class AgentFactory:
    async def create_agent(self, config: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(uuid.uuid4())
        return {"agent_id": agent_id, "status": "shadow", "token_issued": True}

    async def clone_agent(
        self, parent_id: str, parent_config: dict, overrides: dict
    ) -> dict[str, Any]:
        parent_scopes = parent_config.get("authorized_tools", [])
        child_scopes = overrides.get("authorized_tools", {}).get("add", [])
        violations = validate_clone_scopes(parent_scopes, parent_scopes + child_scopes)
        if violations:
            logger.warning("clone_scope_violation", violations=violations)
            return {"error": {"code": "E4003", "message": f"Scope ceiling violation: {violations}"}}
        clone_id = str(uuid.uuid4())
        return {"clone_id": clone_id, "parent_id": parent_id, "status": "shadow"}

    async def delete_agent(self, agent_id: str) -> dict[str, Any]:
        return {"agent_id": agent_id, "status": "deprecated", "retention_days": 30}
