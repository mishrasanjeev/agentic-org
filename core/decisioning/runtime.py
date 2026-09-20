# SPDX-License-Identifier: Apache-2.0

"""Configuration-aware construction for the optional Jev provider."""

from __future__ import annotations

import os
import uuid

from core.config import external_keys, settings
from core.decisioning.jev import JevDecisionProvider


async def build_jev_provider(
    tenant_id: uuid.UUID | str | None = None,
) -> JevDecisionProvider:
    """Build Jev from a server-side platform credential when explicitly called.

    The provider is never built implicitly by an agent. A caller must opt in
    explicitly, which preserves the current runtime behavior when Jev is off.
    """
    del tenant_id
    secret = os.getenv("TYPESAFE_API_KEY", "").strip()
    if not secret:
        raise ValueError("TYPESAFE_API_KEY is required for explicit Jev use")
    return JevDecisionProvider(
        secret,
        base_url=external_keys.typesafe_api_base_url,
        model=external_keys.typesafe_model,
        timeout_seconds=settings.jev_timeout_seconds,
    )
