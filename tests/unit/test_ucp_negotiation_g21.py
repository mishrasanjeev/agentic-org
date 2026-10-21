# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""G21: UCP-Agent parsing (RFC 8941), platform profile checks and the capability intersection."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from core.commerce.ucp import negotiation as n
from core.commerce.ucp.capabilities import DEFAULT_REGISTRY, RegistryContext
from tests import ucp_txn_fixtures as uf

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000a3"))


def test_version_parameter_on_the_profile_member_is_read() -> None:
    agent = n.parse_ucp_agent('profile="https://a.example/p.json"; version="2026-04-08"')
    assert (agent.profile_url, agent.declared_version) == ("https://a.example/p.json", "2026-04-08")


def test_ucp_agent_requires_absolute_profile_url() -> None:
    agent = n.parse_ucp_agent('profile="https://platform.example/profile.json"')
    assert agent.profile_url == "https://platform.example/profile.json" and agent.declared_version is None
    for header in (None, "", 'profile="..."', "profile=token", 'profile="https://user:pw@x.example/p"', "garbage"):
        with pytest.raises(n.NegotiationError) as exc:
            n.parse_ucp_agent(header)
        assert exc.value.code == "invalid_profile_url" and exc.value.status == 400


async def _negotiate(profile: Any, header: str | None = None, versions=("2026-04-08", "2026-08-25"), signing=False):
    async def _fetch(_url: str) -> Any:
        if isinstance(profile, Exception):
            raise profile
        return profile

    return await n.negotiate(
        ucp_agent=header or f'profile="{uf.PLATFORM_PROFILE}"',
        merchant=uf.merchant(TENANT),
        registry=DEFAULT_REGISTRY,
        context=RegistryContext(merchant_signing_available=signing),
        resource="checkout",
        fetcher=_fetch,
        supported_versions=versions,
    )


@pytest.mark.asyncio
async def test_version_negotiation_errors() -> None:
    with pytest.raises(n.NegotiationError) as declared:
        await _negotiate(uf.platform_profile(), f'profile="{uf.PLATFORM_PROFILE}"; version="2099-01-01"')
    assert (declared.value.code, declared.value.status) == ("version_unsupported", 422)
    with pytest.raises(n.NegotiationError) as fetched:
        await _negotiate(uf.platform_profile("2026-08-25"), versions=("2026-04-08",))
    assert fetched.value.code == "version_unsupported"
    with pytest.raises(n.NegotiationError) as malformed:
        await _negotiate({"not": "a profile"})
    assert (malformed.value.code, malformed.value.status) == ("profile_malformed", 422)
    with pytest.raises(n.NegotiationError) as unreachable:
        await _negotiate(OSError("down"))
    assert (unreachable.value.code, unreachable.value.status) == ("profile_unreachable", 424)


@pytest.mark.asyncio
async def test_missing_root_capability_is_capabilities_incompatible_business_outcome() -> None:
    with pytest.raises(n.NegotiationError) as exc:
        await _negotiate(uf.platform_profile(capabilities=["dev.ucp.shopping.order"]))
    assert (exc.value.code, exc.value.status) == ("capabilities_incompatible", 200)


@pytest.mark.asyncio
async def test_intersection_prunes_orphan_extensions_and_version_mismatches() -> None:
    profile = uf.platform_profile(
        capabilities=[
            "dev.ucp.shopping.checkout",
            "dev.ucp.shopping.fulfillment",
            "dev.ucp.common.loyalty",
            "dev.ucp.shopping.ap2_mandate",
        ]
    )
    # A capability declared at another release is not shared.
    profile["ucp"]["capabilities"]["dev.ucp.common.loyalty"][0]["version"] = "2026-01-23"
    negotiated = await _negotiate(profile)
    assert set(negotiated.active) == {"dev.ucp.shopping.checkout", "dev.ucp.shopping.fulfillment"}
    # AP2 needs a merchant signing key; with one it is negotiated.
    signed = await _negotiate(profile, signing=True)
    assert "dev.ucp.shopping.ap2_mandate" in signed.active
    # Fulfillment without checkout is pruned (its only parent is absent) -> root missing.
    with pytest.raises(n.NegotiationError):
        await _negotiate(uf.platform_profile(capabilities=["dev.ucp.shopping.fulfillment"]))


@pytest.mark.asyncio
async def test_response_capabilities_echo_release_and_relevant_capabilities_only() -> None:
    negotiated = await _negotiate(uf.platform_profile("2026-08-25"))
    checkout = negotiated.response_capabilities("checkout")
    assert all(
        entries
        == [{"version": "2026-08-25", **({"extends": entries[0]["extends"]} if "extends" in entries[0] else {})}]
        for entries in checkout.values()
    )
    assert "dev.ucp.shopping.order" not in checkout and "dev.ucp.shopping.cart" not in checkout
    assert set(negotiated.response_capabilities("order")) >= {"dev.ucp.shopping.order"}


def test_profile_cache_ttl_floor_and_ceiling() -> None:
    assert n._cache_ttl(None) == 60
    assert n._cache_ttl("public, max-age=5") == 60
    assert n._cache_ttl("max-age=600") == 600
    assert n._cache_ttl("max-age=999999") == 3600


@pytest.mark.asyncio
async def test_default_fetcher_refuses_non_https_and_private_hosts() -> None:
    n.clear_profile_cache()
    for url in ("http://platform.example/p.json", "https://127.0.0.1/p.json", "https://localhost/p.json"):
        with pytest.raises(n.NegotiationError) as exc:
            await n.default_profile_fetcher(url)
        assert exc.value.code == "invalid_profile_url"
