"""Integration configuration status — backs the Settings page status badges.

Returns boolean flags for each third-party integration indicating whether
the environment is configured. NEVER returns the secret values themselves.

Prior to this endpoint, `ui/src/pages/Settings.tsx` rendered a hardcoded
green "Configured" dot next to Grantex regardless of actual config state.
The P1.2 decorative-state rule requires either a real source of truth or
an explicit "Not configured" label — this endpoint provides the former.
"""

from __future__ import annotations

import os

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class IntegrationsStatus(BaseModel):
    grantex_configured: bool
    composio_configured: bool
    ragflow_configured: bool


def _env_set(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


@router.get("/integrations/status", response_model=IntegrationsStatus)
async def integrations_status() -> IntegrationsStatus:
    """Boolean config-state report for each third-party integration."""
    return IntegrationsStatus(
        grantex_configured=_env_set("GRANTEX_API_KEY"),
        composio_configured=_env_set("COMPOSIO_API_KEY"),
        ragflow_configured=_env_set("RAGFLOW_API_URL"),
    )
