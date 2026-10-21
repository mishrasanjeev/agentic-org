# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""UCP 2026-04-08 document shapes (the release Google's Merchant Center guides target).

Differences from 2026-08-25 that live here: signing keys are published in
``signing_keys[]`` with ``keys[]`` as a mirror carrying the same ``kid`` set
(2026-04-08 overview, Profile Structure); there is no Location capability and
no ``policies[]`` on catalog responses.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

VERSION = "2026-04-08"
SPEC_BASE = f"https://ucp.dev/{VERSION}/specification"
SCHEMA_BASE = f"https://ucp.dev/{VERSION}"

OVERVIEW_SPEC = f"{SPEC_BASE}/overview"
SHOPPING_REST_SCHEMA = f"{SCHEMA_BASE}/services/shopping/rest.openapi.json"
CATALOG_SEARCH_SPEC = f"{SPEC_BASE}/catalog/search"
CATALOG_SEARCH_SCHEMA = f"{SCHEMA_BASE}/schemas/shopping/catalog_search.json"
CATALOG_LOOKUP_SPEC = f"{SPEC_BASE}/catalog/lookup"
CATALOG_LOOKUP_SCHEMA = f"{SCHEMA_BASE}/schemas/shopping/catalog_lookup.json"


def render_profile(
    *,
    registries: Mapping[str, Any],
    jwks: Sequence[Mapping[str, Any]],
    supported_versions: Mapping[str, str] | None,
) -> dict[str, Any]:
    ucp: dict[str, Any] = {"version": VERSION}
    if supported_versions:
        ucp["supported_versions"] = dict(supported_versions)
    ucp["services"] = registries["services"]
    ucp["capabilities"] = registries["capabilities"]
    ucp["payment_handlers"] = registries["payment_handlers"]
    keys = [dict(key) for key in jwks]
    # signing_keys[] is canonical in 2026-04-08; keys[] mirrors it so the
    # profile is also a JWK Set (same kids, same thumbprints).
    return {"ucp": ucp, "signing_keys": keys, "keys": [dict(key) for key in keys]}


def render_catalog_response(
    *,
    capability: str,
    products: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    pagination: Mapping[str, Any] | None,
    policies: list[dict[str, Any]],
) -> dict[str, Any]:
    del policies  # catalog responses carry no policies[] in 2026-04-08
    body: dict[str, Any] = {
        "ucp": {"version": VERSION, "capabilities": {capability: [{"version": VERSION}]}},
        "products": products,
    }
    if pagination is not None:
        body["pagination"] = dict(pagination)
    if messages:
        body["messages"] = messages
    return body
