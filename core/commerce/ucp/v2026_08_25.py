# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""UCP 2026-08-25 document shapes.

Differences from 2026-04-08 that live here: signing keys are published only in
``keys[]`` (2026-08-25 overview, Profile Structure); catalog responses may carry
``policies[]``; the ``dev.ucp.common`` service and the
``dev.ucp.common.location.search`` / ``.lookup`` capabilities exist.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

VERSION = "2026-08-25"
SPEC_BASE = f"https://ucp.dev/{VERSION}/specification"
SCHEMA_BASE = f"https://ucp.dev/{VERSION}"

OVERVIEW_SPEC = f"{SPEC_BASE}/overview"
SHOPPING_REST_SCHEMA = f"{SCHEMA_BASE}/services/shopping/rest.openapi.json"
COMMON_REST_SCHEMA = f"{SCHEMA_BASE}/services/common/rest.openapi.json"
CATALOG_SEARCH_SPEC = f"{SPEC_BASE}/shopping/catalog/search"
CATALOG_SEARCH_SCHEMA = f"{SCHEMA_BASE}/schemas/shopping/catalog_search.json"
CATALOG_LOOKUP_SPEC = f"{SPEC_BASE}/shopping/catalog/lookup"
CATALOG_LOOKUP_SCHEMA = f"{SCHEMA_BASE}/schemas/shopping/catalog_lookup.json"
LOCATION_SEARCH_SPEC = f"{SPEC_BASE}/common/location/search"
LOCATION_SEARCH_SCHEMA = f"{SCHEMA_BASE}/schemas/common/location_search.json"
LOCATION_LOOKUP_SPEC = f"{SPEC_BASE}/common/location/lookup"
LOCATION_LOOKUP_SCHEMA = f"{SCHEMA_BASE}/schemas/common/location_lookup.json"


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
    return {"ucp": ucp, "keys": [dict(key) for key in jwks]}


def render_catalog_response(
    *,
    capability: str,
    products: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    pagination: Mapping[str, Any] | None,
    policies: list[dict[str, Any]],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ucp": {"version": VERSION, "capabilities": {capability: [{"version": VERSION}]}},
        "products": products,
    }
    if pagination is not None:
        body["pagination"] = dict(pagination)
    if messages:
        body["messages"] = messages
    if policies:
        body["policies"] = policies
    return body


def render_location_response(
    *,
    capability: str,
    locations: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    pagination: Mapping[str, Any] | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ucp": {"version": VERSION, "capabilities": {capability: [{"version": VERSION}]}},
        "locations": locations,
    }
    if pagination is not None:
        body["pagination"] = dict(pagination)
    if messages:
        body["messages"] = messages
    return body
