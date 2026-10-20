# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Compose a merchant's UCP business profile (``/.well-known/ucp``).

Shared logic lives here; the release-specific document shape is rendered by
:mod:`core.commerce.ucp.v2026_04_08` or :mod:`core.commerce.ucp.v2026_08_25`.
Only a merchant that opted in (published), whose serving host is attested,
who holds an active signing key, and whose verified ``public_discovery_state``
artifact allows publication on that host gets a profile; everything else is
"not found".
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import ModuleType
from typing import Any

from core.commerce.merchant_keys import active_key_record, merchant_public_jwks
from core.commerce.ucp import v2026_04_08, v2026_08_25
from core.commerce.ucp.locations import store_locations
from core.commerce.ucp.merchant import config_from_row
from core.commerce.ucp.registry import UcpRegistry, UcpServeContext, ucp_registry
from core.commerce.ucp.state import attested_host, is_published, public_discovery_allows, ucp_state
from core.commerce.ucp.versions import UCP_2026_04_08, UCP_2026_08_25, current_ucp_version

RENDERERS: dict[str, ModuleType] = {UCP_2026_04_08: v2026_04_08, UCP_2026_08_25: v2026_08_25}
PROFILE_CACHE_CONTROL = "public, max-age=300"


@dataclass(frozen=True)
class UcpMerchantServeState:
    """Everything a UCP response for one merchant on one host needs from its config."""

    host: str
    tenant_id: str
    merchant_id: str
    seller_agent_id: str
    namespace: str | None
    jwks: tuple[dict[str, str], ...]
    locations: tuple[dict[str, Any], ...]
    ucp: Mapping[str, Any]
    config: Mapping[str, Any] = field(default_factory=dict)

    def context(self, version: str) -> UcpServeContext:
        return UcpServeContext(
            host=self.host,
            version=version,
            tenant_id=self.tenant_id,
            merchant_id=self.merchant_id,
            seller_agent_id=self.seller_agent_id,
            namespace=self.namespace,
            location_count=len(self.locations),
            merchant_config=self.config,
        )


def merchant_serve_state(
    row: Any,
    *,
    host: str,
    now: datetime,
    live_public_discovery_ids: set[str] | None,
) -> UcpMerchantServeState | None:
    """The serve state when ``row`` may be served on ``host``; otherwise None.

    ``live_public_discovery_ids`` are the ids of the scope's verified, fresh
    ``public_discovery`` cache records; ``None`` skips that check (operator
    previews of what publishing would serve, never a public response).
    """
    if row is None:
        return None
    state = ucp_state(row.public_publishing)
    if not is_published(state) or attested_host(state) != host:
        return None
    if live_public_discovery_ids is not None and not public_discovery_allows(
        state, host=host, live_artifact_ids=live_public_discovery_ids
    ):
        return None
    keys = state.get("signing_keys") or []
    if active_key_record(keys) is None:
        return None
    jwks = merchant_public_jwks(keys, now=now)
    if not jwks:
        return None
    namespace = state.get("namespace") if isinstance(state.get("namespace"), str) else None
    return UcpMerchantServeState(
        host=host,
        tenant_id=str(row.tenant_id),
        merchant_id=str(row.merchant_id),
        seller_agent_id=str(row.seller_agent_id),
        namespace=namespace,
        jwks=tuple(jwks),
        locations=tuple(store_locations(row.offline_pos_stores or [])),
        ucp=state,
        config=config_from_row(row),
    )


def leaf_profile_url(host: str, version: str) -> str:
    return f"https://{host}/.well-known/ucp/{version}"


def build_profile(
    serve: UcpMerchantServeState,
    *,
    version: str,
    versions: tuple[str, ...],
    registry: UcpRegistry = ucp_registry,
) -> dict[str, Any]:
    """The business profile for ``version``: the current release carries ``supported_versions``; leaves do not."""
    if version not in versions or version not in RENDERERS:
        raise ValueError("UCP version not served")
    registries = registry.render(serve.context(version))
    supported: dict[str, str] | None = None
    if version == current_ucp_version(versions) and len(versions) > 1:
        supported = {item: leaf_profile_url(serve.host, item) for item in versions if item != version}
    return RENDERERS[version].render_profile(
        registries=registries,
        jwks=serve.jwks,
        supported_versions=supported,
    )


def profile_etag(document: Mapping[str, Any]) -> str:
    """A strong ETag over the canonical JSON of the document."""
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return '"' + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32] + '"'
