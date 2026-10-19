# SPDX-License-Identifier: Apache-2.0
"""UCP business profile: shape per release, schema validity, served-only registry (G17, G18)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from core.commerce.merchant_keys import add_active_key
from core.commerce.ucp import registry as registry_module
from core.commerce.ucp.profile import build_profile, leaf_profile_url, merchant_serve_state, profile_etag
from core.commerce.ucp.registry import (
    UcpCapabilityRegistration,
    UcpPaymentHandlerRegistration,
    UcpRegistry,
    UcpServiceRegistration,
    ucp_registry,
)
from core.commerce.ucp.versions import parse_ucp_agent, parse_ucp_versions
from tests.ucp_fixtures import (
    HOST,
    NOW,
    fake_tenant_encryption,
    key_record,
    merchant_row,
    published_state,
    serve_state,
    store_with_hours,
)
from tests.ucp_schema_validation import PROFILE_SCHEMAS, assert_ucp_valid, ucp_schema_errors

R08 = "2026-04-08"
R25 = "2026-08-25"


@pytest.fixture()
def serve(monkeypatch: pytest.MonkeyPatch):
    fake_tenant_encryption(monkeypatch)
    row = merchant_row(published_state([key_record()]), stores=[store_with_hours()])
    state = serve_state(row, host=HOST, now=NOW)
    assert state is not None
    return state


def test_default_profile_is_2026_04_08_with_catalog_only_and_signing_keys(serve) -> None:
    document = build_profile(serve, version=R08, versions=(R08,))

    assert_ucp_valid(document, PROFILE_SCHEMAS[R08])
    ucp = document["ucp"]
    assert ucp["version"] == R08
    assert "supported_versions" not in ucp
    assert set(ucp["capabilities"]) == {"dev.ucp.shopping.catalog.search", "dev.ucp.shopping.catalog.lookup"}
    assert ucp["services"] == {
        "dev.ucp.shopping": [
            {
                "version": R08,
                "spec": "https://ucp.dev/2026-04-08/specification/overview",
                "transport": "rest",
                "endpoint": f"https://{HOST}/ucp",
                "schema": "https://ucp.dev/2026-04-08/services/shopping/rest.openapi.json",
            }
        ]
    }
    assert ucp["payment_handlers"] == {}
    for entries in [*ucp["capabilities"].values(), *ucp["services"].values()]:
        assert all(entry["version"] == R08 for entry in entries)
    # 2026-04-08: signing_keys[] canonical, keys[] mirrors the same kids.
    assert document["signing_keys"] == document["keys"]
    jwk = document["signing_keys"][0]
    assert {"kty": "EC", "crv": "P-256", "use": "sig", "alg": "ES256"}.items() <= jwk.items()
    assert not {"d", "private_key_ciphertext"} & set(jwk)
    # No location capability in 2026-04-08 even though the merchant has a store.
    assert not any(name.startswith("dev.ucp.common.location") for name in ucp["capabilities"])


def test_both_versions_serve_2026_08_25_current_with_2026_04_08_leaf(serve) -> None:
    versions = parse_ucp_versions("2026-08-25, 2026-04-08")
    current = build_profile(serve, version=R25, versions=versions)
    leaf = build_profile(serve, version=R08, versions=versions)

    assert_ucp_valid(current, PROFILE_SCHEMAS[R25])
    assert_ucp_valid(leaf, PROFILE_SCHEMAS[R08])
    assert current["ucp"]["supported_versions"] == {R08: leaf_profile_url(HOST, R08)}
    assert leaf_profile_url(HOST, R08) == f"https://{HOST}/.well-known/ucp/2026-04-08"
    assert "supported_versions" not in leaf["ucp"]
    assert leaf["ucp"]["version"] == R08
    assert "signing_keys" not in current and current["keys"]
    assert set(current["ucp"]["capabilities"]) == {
        "dev.ucp.shopping.catalog.search",
        "dev.ucp.shopping.catalog.lookup",
        "dev.ucp.common.location.search",
        "dev.ucp.common.location.lookup",
    }
    assert set(current["ucp"]["services"]) == {"dev.ucp.shopping", "dev.ucp.common"}
    for name, entries in current["ucp"]["capabilities"].items():
        assert entries[0]["schema"].startswith("https://ucp.dev/2026-08-25/"), name


def test_location_capability_needs_a_store(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tenant_encryption(monkeypatch)
    state = serve_state(merchant_row(published_state([key_record()])), host=HOST, now=NOW)
    document = build_profile(state, version=R25, versions=(R08, R25))
    assert "dev.ucp.common" not in document["ucp"]["services"]
    assert not any(name.startswith("dev.ucp.common.") for name in document["ucp"]["capabilities"])


def test_unpublished_unattested_other_host_or_keyless_merchants_get_no_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tenant_encryption(monkeypatch)
    keys = [key_record()]
    state = published_state(keys)
    assert serve_state(merchant_row(state), host="other.merchant.example", now=NOW) is None
    assert serve_state(merchant_row({**state, "published": None}), host=HOST, now=NOW) is None
    assert serve_state(merchant_row({**state, "attestation": None}), host=HOST, now=NOW) is None
    assert serve_state(merchant_row({**state, "signing_keys": []}), host=HOST, now=NOW) is None
    assert serve_state(None, host=HOST, now=NOW) is None


def test_rotated_key_stays_published_for_the_grace_window(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tenant_encryption(monkeypatch)
    first = key_record()
    second = key_record(NOW + timedelta(days=1))
    keys = add_active_key([first], second, now=NOW + timedelta(days=1), grace_days=7)
    within = serve_state(merchant_row(published_state(keys)), host=HOST, now=NOW + timedelta(days=3))
    after = serve_state(merchant_row(published_state(keys)), host=HOST, now=NOW + timedelta(days=9))
    assert [jwk["kid"] for jwk in within.jwks] == [second["kid"], first["kid"]]
    assert [jwk["kid"] for jwk in after.jwks] == [second["kid"]]
    document = build_profile(within, version=R08, versions=(R08,))
    assert_ucp_valid(document, PROFILE_SCHEMAS[R08])


def test_registry_emits_only_served_capabilities_and_prunes_orphan_extensions(serve) -> None:
    registry = UcpRegistry()
    registry.services.update(ucp_registry.services)
    # The read capabilities only: the transaction ones are registered by api/v1/commerce_ucp.py.
    registry.capabilities.update(
        {name: reg for name, reg in ucp_registry.capabilities.items() if ".catalog." in name or ".location." in name}
    )
    checkout_on = {"value": False}
    registry.register_capability(
        UcpCapabilityRegistration(
            name="dev.ucp.shopping.checkout",
            service="dev.ucp.shopping",
            spec_urls={R08: "https://ucp.dev/2026-04-08/specification/checkout"},
            schema_urls={R08: "https://ucp.dev/2026-04-08/schemas/shopping/checkout.json"},
            served=lambda _ctx: checkout_on["value"],
        )
    )
    registry.register_capability(
        UcpCapabilityRegistration(
            name="dev.ucp.shopping.fulfillment",
            service="dev.ucp.shopping",
            spec_urls={R08: "https://ucp.dev/2026-04-08/specification/fulfillment"},
            schema_urls={R08: "https://ucp.dev/2026-04-08/schemas/shopping/fulfillment.json"},
            extends=("dev.ucp.shopping.checkout",),
        )
    )
    off = build_profile(serve, version=R08, versions=(R08,), registry=registry)
    assert "dev.ucp.shopping.checkout" not in off["ucp"]["capabilities"]
    assert "dev.ucp.shopping.fulfillment" not in off["ucp"]["capabilities"]
    checkout_on["value"] = True
    on = build_profile(serve, version=R08, versions=(R08,), registry=registry)
    assert on["ucp"]["capabilities"]["dev.ucp.shopping.fulfillment"][0]["extends"] == "dev.ucp.shopping.checkout"
    assert_ucp_valid(on, PROFILE_SCHEMAS[R08])
    with pytest.raises(ValueError):
        registry.register_capability(registry.capabilities["dev.ucp.shopping.checkout"])


def test_vendor_entries_need_authority_binding_and_the_attested_namespace(serve) -> None:
    registry = UcpRegistry()
    registry.services.update(ucp_registry.services)
    registry.capabilities.update(ucp_registry.capabilities)
    bound = "example.merchant.shop.gift_wrap"
    registry.register_capability(
        UcpCapabilityRegistration(
            name=bound,
            service="dev.ucp.shopping",
            spec_urls={R08: f"https://{HOST}/ucp/spec/gift-wrap"},
            schema_urls={R08: f"https://{HOST}/ucp/schemas/gift_wrap.json"},
            extends=("dev.ucp.shopping.catalog.search",),
        )
    )
    # Schema on a host that does not authorize the name: treated as absent.
    registry.register_capability(
        UcpCapabilityRegistration(
            name="example.merchant.shop.loyalty",
            service="dev.ucp.shopping",
            spec_urls={R08: "https://cdn.example.com/spec"},
            schema_urls={R08: "https://cdn.example.com/loyalty.json"},
        )
    )
    registry.register_payment_handler(
        UcpPaymentHandlerRegistration(
            name="com.example.pay",
            entries=lambda ctx: [
                {
                    "id": "example_pay_1",
                    "version": "2026-01-01",
                    "spec": "https://pay.example.com/ucp",
                    "schema": "https://pay.example.com/ucp/config.json",
                    "available_instruments": [{"type": "card"}],
                }
            ],
        )
    )
    document = build_profile(serve, version=R08, versions=(R08,), registry=registry)
    assert bound in document["ucp"]["capabilities"]
    assert "example.merchant.shop.loyalty" not in document["ucp"]["capabilities"]
    assert document["ucp"]["payment_handlers"]["com.example.pay"][0]["id"] == "example_pay_1"
    assert_ucp_valid(document, PROFILE_SCHEMAS[R08])

    without_namespace = type(serve)(**{**serve.__dict__, "namespace": None})
    document = build_profile(without_namespace, version=R08, versions=(R08,), registry=registry)
    assert bound not in document["ucp"]["capabilities"]


def test_dev_ucp_entry_with_a_foreign_schema_host_is_dropped(serve) -> None:
    registry = UcpRegistry()
    registry.services.update(ucp_registry.services)
    registry.register_capability(
        UcpCapabilityRegistration(
            name="dev.ucp.shopping.catalog.search",
            service="dev.ucp.shopping",
            spec_urls={R08: "https://ucp.dev/2026-04-08/specification/catalog/search"},
            schema_urls={R08: "https://schemas.example.com/catalog_search.json"},
        )
    )
    document = build_profile(serve, version=R08, versions=(R08,), registry=registry)
    assert document["ucp"]["capabilities"] == {}
    assert document["ucp"]["services"] == {}


def test_service_registrations_reject_unknown_transports() -> None:
    with pytest.raises(ValueError):
        UcpRegistry().register_service(
            UcpServiceRegistration(
                name="dev.ucp.shopping",
                transport="jsonrpc",
                spec_urls={},
                schema_urls={},
                endpoint=lambda ctx: None,
            )
        )


def test_schema_validation_catches_a_private_key_member(serve) -> None:
    document = build_profile(serve, version=R08, versions=(R08,))
    document["signing_keys"][0]["d"] = "AAAA"
    assert ucp_schema_errors(document, PROFILE_SCHEMAS[R08])
    broken = build_profile(serve, version=R25, versions=(R08, R25))
    del broken["ucp"]["payment_handlers"]
    assert ucp_schema_errors(broken, PROFILE_SCHEMAS[R25])


def test_etag_is_stable_and_content_bound(serve) -> None:
    first = build_profile(serve, version=R08, versions=(R08,))
    second = build_profile(serve, version=R08, versions=(R08,))
    assert profile_etag(first) == profile_etag(second)
    second["ucp"]["capabilities"].pop("dev.ucp.shopping.catalog.lookup")
    assert profile_etag(first) != profile_etag(second)


def test_versions_setting_and_ucp_agent_header() -> None:
    assert parse_ucp_versions(None) == (R08,)
    assert parse_ucp_versions("draft, 2026-01-11") == (R08,)
    assert parse_ucp_versions("2026-08-25") == (R25,)
    header = parse_ucp_agent('profile="https://agent.example/profile.json"; version="2026-04-08"')
    assert header.profile == "https://agent.example/profile.json"
    assert header.version == R08
    assert parse_ucp_agent('profile="https://agent.example/p.json"').version is None
    assert parse_ucp_agent(None) is None


def test_default_registry_is_the_module_singleton() -> None:
    assert registry_module.ucp_registry is ucp_registry
    assert {"dev.ucp.shopping.catalog.search", "dev.ucp.shopping.catalog.lookup"} <= set(ucp_registry.capabilities)


def test_profile_needs_a_live_public_discovery_artifact_allowing_this_host(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tenant_encryption(monkeypatch)
    state = published_state([key_record()])
    row = merchant_row(state)
    assert serve_state(row) is not None
    assert serve_state(row, live=set()) is None  # artifact expired, revoked or unverified
    discovery = state["verified_public_discovery"]
    for change in (
        {"publication_allowed": False},
        {"serving_host": "other.merchant.example"},
        {"namespace_attestation_ref": "agenticorg:ucp-attestation:someone-else"},
    ):
        assert serve_state(merchant_row({**state, "verified_public_discovery": {**discovery, **change}})) is None
    assert serve_state(merchant_row({k: v for k, v in state.items() if k != "verified_public_discovery"})) is None
    # Operator previews skip the artifact check; public routes never pass None.
    assert merchant_serve_state(row, host=HOST, now=NOW, live_public_discovery_ids=None) is not None
