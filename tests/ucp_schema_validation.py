# SPDX-License-Identifier: Apache-2.0
"""Validate emitted UCP documents against the published UCP JSON Schemas.

``tests/fixtures/ucp_schemas/<release>/`` holds the subset of the UCP schema
trees (Universal-Commerce-Protocol/ucp, Apache-2.0) that the profile, catalog,
location and error responses reference: 2026-04-08 from ``source/`` of
``release/2026-04-08`` (``$id`` ``https://ucp.dev/schemas/...``), 2026-08-25 as
published at ``https://ucp.dev/2026-08-25/...``. They are loaded into one
``referencing`` registry keyed by ``$id``; nothing is fetched over the network.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema
from referencing import Registry
from referencing.jsonschema import DRAFT202012

SCHEMA_ROOT = Path(__file__).resolve().parent / "fixtures" / "ucp_schemas"

R08_PROFILE = "https://ucp.dev/discovery/profile.json#/$defs/business_profile"
R08_CATALOG_SEARCH = "https://ucp.dev/schemas/shopping/catalog_search.json#/$defs/search_response"
R08_CATALOG_LOOKUP = "https://ucp.dev/schemas/shopping/catalog_lookup.json#/$defs/lookup_response"
R08_ERROR = "https://ucp.dev/schemas/shopping/types/error_response.json"
R25_PROFILE = "https://ucp.dev/2026-08-25/schemas/profile.json#/$defs/business_schema"
R25_CATALOG_SEARCH = "https://ucp.dev/2026-08-25/schemas/shopping/catalog_search.json#/$defs/search_response"
R25_CATALOG_LOOKUP = "https://ucp.dev/2026-08-25/schemas/shopping/catalog_lookup.json#/$defs/lookup_response"
R25_LOCATION_SEARCH = "https://ucp.dev/2026-08-25/schemas/common/location_search.json#/$defs/search_response"
R25_LOCATION_LOOKUP = "https://ucp.dev/2026-08-25/schemas/common/location_lookup.json#/$defs/lookup_response"
R25_ERROR = "https://ucp.dev/2026-08-25/schemas/common/types/error_response.json"
R25_POLICY = "https://ucp.dev/2026-08-25/schemas/common/types/policy.json"

PROFILE_SCHEMAS = {"2026-04-08": R08_PROFILE, "2026-08-25": R25_PROFILE}
CATALOG_SEARCH_SCHEMAS = {"2026-04-08": R08_CATALOG_SEARCH, "2026-08-25": R25_CATALOG_SEARCH}
CATALOG_LOOKUP_SCHEMAS = {"2026-04-08": R08_CATALOG_LOOKUP, "2026-08-25": R25_CATALOG_LOOKUP}
ERROR_SCHEMAS = {"2026-04-08": R08_ERROR, "2026-08-25": R25_ERROR}


@cache
def ucp_schema_registry() -> Registry:
    resources = []
    for path in sorted(SCHEMA_ROOT.rglob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        resources.append((document["$id"], DRAFT202012.create_resource(document)))
    # The 2026-04-08 source profile schema declares $id .../schemas/discovery/profile.json
    # but refers to "../schemas/ucp.json" relative to its source location
    # (source/discovery/ beside source/schemas/); register it at the id that
    # makes that reference resolve to the ucp.json shipped with it.
    profile = json.loads((SCHEMA_ROOT / "2026-04-08" / "discovery" / "profile_schema.json").read_text(encoding="utf-8"))
    profile["$id"] = "https://ucp.dev/discovery/profile.json"
    resources.append((profile["$id"], DRAFT202012.create_resource(profile)))
    return Registry().with_resources(resources)


def ucp_schema_errors(document: Any, schema_ref: str) -> list[str]:
    validator = jsonschema.Draft202012Validator(
        {"$ref": schema_ref},
        registry=ucp_schema_registry(),
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )
    return sorted(f"{list(error.absolute_path)}: {error.message}" for error in validator.iter_errors(document))


def assert_ucp_valid(document: Any, schema_ref: str) -> None:
    errors = ucp_schema_errors(document, schema_ref)
    assert not errors, "\n".join(errors[:20])
