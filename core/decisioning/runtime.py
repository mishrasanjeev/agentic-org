"""Configuration-aware construction for the optional Jev provider."""

from __future__ import annotations

import uuid

from core.ai_providers.resolver import get_provider_credential
from core.config import external_keys, settings
from core.decisioning.jev import JevDecisionProvider


async def build_jev_provider(
    tenant_id: uuid.UUID | str | None = None,
) -> JevDecisionProvider:
    """Build Jev with tenant-scoped credentials where configured.

    The provider is never built implicitly by an agent. A caller must opt in
    explicitly, which preserves the current runtime behavior when Jev is off.
    """

    credential = await get_provider_credential(
        tenant_id,
        "typesafe",
        "decision",
        require_tenant_token=False,
    )
    return JevDecisionProvider(
        credential.secret,
        base_url=external_keys.typesafe_api_base_url,
        model=external_keys.typesafe_model,
        timeout_seconds=settings.jev_timeout_seconds,
    )
