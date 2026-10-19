# SPDX-License-Identifier: Apache-2.0
"""Regression: the merchant UCP profile and catalog/location reads on the serving host (G17, G23, G26, G28).

Failure replayed: there was no ``/.well-known/ucp``; a platform discovering a
merchant found nothing, and the only "UCP" output was an internal adapter
preview with free-text capability names. Now the profile is served on the
merchant's verified serving host (``Host`` header only; unknown host, flag off,
unpublished merchant => 404), with UCP caching headers and no redirect, and the
catalog and location routes answer in the schema of the requested release.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.v1 import ucp_discovery
from core.commerce.ucp.store import UcpServingBinding
from core.config import settings
from tests.ucp_fixtures import (
    HOST,
    MERCHANT_ID,
    SELLER_AGENT_ID,
    TENANT_ID,
    cache_record,
    catalog_records,
    fake_tenant_encryption,
    key_record,
    merchant_row,
    published_state,
    store_with_hours,
    synced_products,
)
from tests.ucp_schema_validation import (
    CATALOG_LOOKUP_SCHEMAS,
    CATALOG_SEARCH_SCHEMAS,
    ERROR_SCHEMAS,
    PROFILE_SCHEMAS,
    R25_LOCATION_SEARCH,
    assert_ucp_valid,
)

R08 = "2026-04-08"
R25 = "2026-08-25"


@pytest.fixture()
def merchant(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    fake_tenant_encryption(monkeypatch)
    state = published_state([key_record(datetime.now(UTC))])
    state["verified_policy"] = {
        "artifact_id": "oacp_art_policy",
        "captured_at": "2026-10-20T08:00:00Z",
        "policy": {"return_policy_summary": "Synthetic 7 day return window."},
    }
    holder: dict[str, Any] = {
        "row": merchant_row(state, stores=[store_with_hours()]),
        "records": [*catalog_records(), cache_record("policy"), cache_record("public_discovery")],
        "products": synced_products(),
        "lookups": [],
    }

    async def find(host: str):
        holder["lookups"].append(host)
        if host != HOST:
            return None
        return UcpServingBinding(
            host=HOST, tenant_id=TENANT_ID, merchant_id=MERCHANT_ID, seller_agent_id=SELLER_AGENT_ID
        )

    async def load_row(**scope: str):
        assert scope == {"tenant_id": TENANT_ID, "merchant_id": MERCHANT_ID, "seller_agent_id": SELLER_AGENT_ID}
        return holder["row"]

    async def load_inputs(**_scope: str):
        return holder["records"], holder["products"]

    async def load_records(**_scope: str):
        return holder["records"]

    monkeypatch.setattr(ucp_discovery, "find_serving_binding", find)
    monkeypatch.setattr(ucp_discovery, "load_merchant_config_row", load_row)
    monkeypatch.setattr(ucp_discovery, "load_catalog_inputs", load_inputs)
    monkeypatch.setattr(ucp_discovery, "load_shared_cache_records", load_records)
    monkeypatch.setattr(settings, "ucp_profile_enabled", True)
    monkeypatch.setattr(settings, "ucp_versions", R08)
    return holder


@pytest.fixture()
def rate_classes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The route_meta rate-limit windows checked (in-memory; no Redis)."""
    seen: list[str] = []

    async def check(key: str, principal: str, limit: int, window: int) -> bool:
        seen.append(f"{key}|{principal.split(':')[0]}|{limit}/{window}")
        return False

    monkeypatch.setattr("core.auth_state.check_window_rate", check)
    return seen


@pytest.fixture()
def client(rate_classes: list[str]) -> TestClient:
    return TestClient(app, base_url=f"https://{HOST}", follow_redirects=False)


def test_profile_is_served_on_the_verified_host_with_caching_headers(merchant, client: TestClient) -> None:
    response = client.get("/.well-known/ucp")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    cache_control = response.headers["cache-control"]
    assert cache_control.startswith("public") and "max-age=300" in cache_control
    assert "private" not in cache_control and "no-store" not in cache_control
    etag = response.headers["etag"]
    document = response.json()
    assert_ucp_valid(document, PROFILE_SCHEMAS[R08])
    assert document["ucp"]["version"] == R08
    assert document["ucp"]["services"]["dev.ucp.shopping"][0]["endpoint"] == f"https://{HOST}/ucp"
    assert set(document["ucp"]["capabilities"]) == {
        "dev.ucp.shopping.catalog.search",
        "dev.ucp.shopping.catalog.lookup",
    }
    assert document["signing_keys"][0]["kid"] == merchant["row"].public_publishing["ucp"]["signing_keys"][0]["kid"]
    assert "private_key_ciphertext" not in response.text
    cached = client.get("/.well-known/ucp", headers={"If-None-Match": etag})
    assert cached.status_code == 304 and cached.headers["etag"] == etag


def test_public_routes_are_rate_limited_per_client_ip(merchant, client: TestClient, rate_classes: list[str]) -> None:
    client.get("/.well-known/ucp")
    client.post("/ucp/catalog/search", json={})
    assert rate_classes == ["rl:ucp-discovery|ip|600/60", "rl:ucp-catalog-read|ip|120/60"]


def test_flag_off_unknown_host_ip_host_and_unpublished_merchant_are_404(
    merchant, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.get("/.well-known/ucp", headers={"Host": "other.merchant.example"}).status_code == 404
    assert client.get("/.well-known/ucp", headers={"Host": "192.0.2.10"}).status_code == 404
    assert client.post("/ucp/catalog/search", json={}, headers={"Host": "other.merchant.example"}).status_code == 404
    merchant["row"].public_publishing["ucp"]["published"] = None
    assert client.get("/.well-known/ucp").status_code == 404
    monkeypatch.setattr(settings, "ucp_profile_enabled", False)
    assert client.get("/.well-known/ucp").status_code == 404
    assert client.post("/ucp/catalog/search", json={}).status_code == 404


def test_profile_is_404_unless_the_verified_public_discovery_artifact_allows_it(merchant, client: TestClient) -> None:
    discovery = merchant["row"].public_publishing["ucp"]["verified_public_discovery"]
    assert client.get("/.well-known/ucp").status_code == 200
    merchant["records"] = [*catalog_records(), cache_record("public_discovery", ref=None)]
    assert client.get("/.well-known/ucp").status_code == 404
    assert client.post("/ucp/catalog/search", json={}).status_code == 404
    merchant["records"] = [*catalog_records(), cache_record("public_discovery")]
    discovery["publication_allowed"] = False
    assert client.get("/.well-known/ucp").status_code == 404
    discovery["publication_allowed"] = True
    assert client.get("/.well-known/ucp").status_code == 200


def test_both_releases_serve_the_current_profile_and_the_leaf(merchant, client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ucp_versions", f"{R08},{R25}")
    current = client.get("/.well-known/ucp")
    assert current.status_code == 200
    assert_ucp_valid(current.json(), PROFILE_SCHEMAS[R25])
    leaf_url = current.json()["ucp"]["supported_versions"][R08]
    assert leaf_url == f"https://{HOST}/.well-known/ucp/{R08}"
    leaf = client.get(f"/.well-known/ucp/{R08}")
    assert leaf.status_code == 200
    assert_ucp_valid(leaf.json(), PROFILE_SCHEMAS[R08])
    assert "supported_versions" not in leaf.json()["ucp"]
    assert client.get(f"/.well-known/ucp/{R25}").status_code == 404
    assert client.get("/.well-known/ucp/2026-01-11").status_code == 404


def test_catalog_search_and_lookup_answer_in_the_release_schema(merchant, client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ucp_versions", f"{R08},{R25}")
    for version in (R08, R25):
        headers = {"UCP-Agent": f'profile="https://agent.example/profile.json"; version="{version}"'}
        search = client.post("/ucp/catalog/search", json={"query": "tote", "pagination": {"limit": 5}}, headers=headers)
        assert search.status_code == 200
        body = search.json()
        assert_ucp_valid(body, CATALOG_SEARCH_SCHEMAS[version])
        assert body["ucp"]["version"] == version
        assert [product["title"] for product in body["products"]] == ["Canvas Tote"]
        if version == R25:
            assert body["policies"] == [
                {"type": "dev.ucp.shopping.policy.return", "description": {"plain": "Synthetic 7 day return window."}}
            ]
        else:
            assert "policies" not in body
        variant_id = body["products"][0]["variants"][0]["id"]
        lookup = client.post("/ucp/catalog/lookup", json={"ids": [variant_id, "missing"]}, headers=headers)
        assert lookup.status_code == 200
        assert_ucp_valid(lookup.json(), CATALOG_LOOKUP_SCHEMAS[version])
        assert lookup.json()["messages"][0]["code"] == "not_found"


def test_catalog_refuses_unverified_or_stale_sources(merchant, client: TestClient) -> None:
    merchant["records"] = [
        *(cache_record(kind, ref=None) for kind in ("catalog_snapshot", "price", "inventory")),
        cache_record("public_discovery"),
    ]
    response = client.post("/ucp/catalog/search", json={"query": "tote"})
    assert response.status_code == 200
    body = response.json()
    assert_ucp_valid(body, CATALOG_SEARCH_SCHEMAS[R08])
    assert body["products"] == []
    assert body["messages"][0]["code"] == "catalog_unavailable"


def test_version_and_request_errors(merchant, client: TestClient) -> None:
    unsupported = client.post(
        "/ucp/catalog/search",
        json={},
        headers={"UCP-Agent": 'profile="https://agent.example/p.json"; version="2026-08-25"'},
    )
    assert unsupported.status_code == 422
    assert_ucp_valid(unsupported.json(), ERROR_SCHEMAS[R08])
    assert unsupported.json()["messages"][0]["code"] == "version_unsupported"
    malformed = client.post("/ucp/catalog/search", json={}, headers={"UCP-Agent": "profile=,,"})
    assert malformed.status_code == 400
    invalid = client.post("/ucp/catalog/lookup", json={"ids": []})
    assert invalid.status_code == 400
    assert_ucp_valid(invalid.json(), ERROR_SCHEMAS[R08])
    bad_cursor = client.post("/ucp/catalog/search", json={"pagination": {"cursor": "!!"}})
    assert bad_cursor.status_code == 400


def test_location_routes_are_2026_08_25_only(merchant, client: TestClient, monkeypatch) -> None:
    incompatible = client.post("/ucp/locations/search", json={})
    assert incompatible.status_code == 200
    assert incompatible.json()["messages"][0]["code"] == "capabilities_incompatible"
    monkeypatch.setattr(settings, "ucp_versions", f"{R08},{R25}")
    response = client.post(
        "/ucp/locations/search", json={"filters": {"hours": {"open_at": "2026-10-19T12:00:00+05:30"}}}
    )
    assert response.status_code == 200
    assert_ucp_valid(response.json(), R25_LOCATION_SEARCH)
    assert [item["id"] for item in response.json()["locations"]] == ["store_pune_1"]
    rejected = client.post(
        "/ucp/locations/search", json={"distance": {"center": {"latitude": 0, "longitude": 0}, "max": 10}}
    )
    assert rejected.json()["messages"][0]["code"] == "unsupported_filter"
    assert_ucp_valid(rejected.json(), ERROR_SCHEMAS[R25])
    lookup = client.post("/ucp/locations/lookup", json={"ids": ["store_pune_1"]})
    assert lookup.status_code == 200 and lookup.json()["locations"][0]["inputs"] == [{"id": "store_pune_1"}]


def test_only_the_public_ucp_paths_skip_tenant_authentication(merchant, client: TestClient) -> None:
    # Not exempt: a transaction route under /ucp still needs credentials.
    assert client.post("/ucp/checkout-sessions", json={}).status_code == 401
    assert client.get("/.well-known/ucp").status_code == 200
