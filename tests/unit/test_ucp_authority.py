# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""UCP namespace authority binding and the serving-host DNS attestation (G20)."""

from __future__ import annotations

import asyncio

import pytest

from api.v1 import commerce_ucp_publishing as publishing
from core.commerce.ucp.authority import (
    DNS_CHALLENGE_LABEL,
    authority_prefix,
    dns_challenge_satisfied,
    entry_authority_valid,
    merchant_namespace_for_host,
    name_bound_to_url,
    new_dns_challenge,
    normalize_host,
)
from core.commerce.ucp.registry import UcpCapabilityRegistration, UcpRegistry
from tests.ucp_fixtures import HOST, fake_tenant_encryption, key_record, merchant_row, published_state


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("dev.ucp.shopping.checkout", "https://ucp.dev/2026-08-25/schemas/shopping/checkout.json"),
        ("dev.ucp.shopping.checkout", "https://shopping.ucp.dev/schemas/checkout.json"),
        ("com.example.pay", "https://pay.example.com/schema.json"),
        ("com.example.pay", "https://example.com/schema.json"),
        ("com.example.pay", "https://EXAMPLE.com./schema.json"),
    ],
)
def test_spec_accepted_bindings(name: str, url: str) -> None:
    assert name_bound_to_url(name, url)


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("com.examplecorp.pay", "https://example.com/schema.json"),
        ("com.example.pay", "https://cdn.example.com/schema.json"),
        ("com.example.pay", "http://example.com/schema.json"),
        ("com.example.pay", "https://user@example.com/schema.json"),
        ("com.example.pay", "https://192.0.2.1/schema.json"),
        ("localhost.pay", "https://localhost/schema.json"),
        ("dev.ucp.shopping.checkout", "https://ucp.dev.example.com/checkout.json"),
    ],
)
def test_spec_rejected_bindings(name: str, url: str) -> None:
    assert not name_bound_to_url(name, url)


def test_2026_04_08_binds_spec_and_schema_while_2026_08_25_binds_schema_only() -> None:
    entry = {
        "version": "2026-04-08",
        "spec": "https://docs.example.net/spec",
        "schema": "https://example.com/schema.json",
    }
    assert not entry_authority_valid("com.example.pay", entry, "2026-04-08")
    assert entry_authority_valid("com.example.pay", {**entry, "version": "2026-08-25"}, "2026-08-25")
    assert not entry_authority_valid("com.example.pay", {"version": "2026-08-25"}, "2026-08-25")


def test_host_normalization() -> None:
    assert normalize_host("Shop.Merchant.Example:443") == HOST
    assert normalize_host("shop.merchant.example.") == HOST
    assert normalize_host("bücher.example") == "xn--bcher-kva.example"
    for bad in (None, "", "localhost", "192.0.2.10", "[2001:db8::1]", "a..example", "-bad.example", "x.example/p"):
        assert normalize_host(bad) is None
    assert authority_prefix(HOST) == "example.merchant.shop"


def test_merchant_namespace_must_extend_the_reversed_host() -> None:
    assert merchant_namespace_for_host(HOST, None) == "example.merchant.shop"
    assert merchant_namespace_for_host(HOST, "example.merchant.shop.store") == "example.merchant.shop.store"
    for bad in ("example.merchant", "example.merchantshop", "dev.ucp.shopping", "Example Merchant"):
        with pytest.raises(ValueError):
            merchant_namespace_for_host(HOST, bad)


def test_dns_challenge_is_checked_against_the_txt_record() -> None:
    challenge = new_dns_challenge(HOST)
    assert challenge["record_name"] == f"{DNS_CHALLENGE_LABEL}.{HOST}"
    assert challenge["record_value"].startswith("agenticorg-ucp-verify=")
    assert new_dns_challenge(HOST)["record_value"] != challenge["record_value"]
    seen: list[str] = []

    async def resolver_with(values: list[str]):
        async def resolve(name: str) -> list[str]:
            seen.append(name)
            return values

        return resolve

    present = asyncio.run(resolver_with(["v=spf1 -all", f" {challenge['record_value']} "]))
    assert asyncio.run(dns_challenge_satisfied(challenge, resolver=present))
    absent = asyncio.run(resolver_with(["agenticorg-ucp-verify=someone-else"]))
    assert not asyncio.run(dns_challenge_satisfied(challenge, resolver=absent))
    assert seen == [challenge["record_name"], challenge["record_name"]]
    assert not asyncio.run(dns_challenge_satisfied({}, resolver=present))


def test_publish_requires_attestation_key_and_bound_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tenant_encryption(monkeypatch)
    keys = [key_record()]
    state = published_state(keys)
    assert publishing.publish_refusal(merchant_row(state), None) == (None, "example.merchant.shop")
    assert publishing.publish_refusal(merchant_row({**state, "attestation": None}), None)[0] == (
        "serving_host_not_attested"
    )
    assert publishing.publish_refusal(merchant_row({**state, "signing_keys": []}), None)[0] == "signing_key_missing"
    assert publishing.publish_refusal(merchant_row(state), "example.merchant")[0] == "namespace_not_authority_bound"


def test_publish_refuses_vendor_capabilities_outside_the_attested_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tenant_encryption(monkeypatch)
    registry = UcpRegistry()
    registry.register_capability(
        UcpCapabilityRegistration(
            name="example.merchant.shop.gift_wrap",
            service="dev.ucp.shopping",
            spec_urls={"2026-04-08": f"https://{HOST}/spec"},
            schema_urls={"2026-04-08": f"https://{HOST}/gift_wrap.json"},
        )
    )
    monkeypatch.setattr(publishing, "ucp_registry", registry)
    state = published_state([key_record()])
    assert publishing.publish_refusal(merchant_row(state), None)[0] is None
    assert publishing.publish_refusal(merchant_row(state), "example.merchant.shop.store")[0] == (
        "vendor_entry_outside_namespace"
    )
