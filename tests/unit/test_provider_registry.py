# SPDX-License-Identifier: Apache-2.0
"""The providers registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from connectors.framework.verification_provider import Capability, VerificationProvider
from connectors.providers.registry import ProviderRegistry, ProviderRegistryError


class AcmeKybProvider(VerificationProvider):
    name = "acme_kyb"
    capabilities = frozenset({Capability.RESOLVE, Capability.VERIFY})


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ProviderRegistry, "_providers", dict(ProviderRegistry._providers))


def test_native_provider_registers_and_is_created_by_name() -> None:
    ProviderRegistry.register_native("acme_kyb", AcmeKybProvider)
    registration = ProviderRegistry.get("acme_kyb")
    assert registration is not None and registration.source == "native"
    assert isinstance(ProviderRegistry.create("acme_kyb"), AcmeKybProvider)
    assert "acme_kyb" in ProviderRegistry.names()


def test_registering_the_same_native_factory_twice_is_idempotent() -> None:
    ProviderRegistry.register_native("acme_kyb", AcmeKybProvider)
    ProviderRegistry.register_native("acme_kyb", AcmeKybProvider)
    with pytest.raises(ProviderRegistryError) as caught:
        ProviderRegistry.register_native("acme_kyb", lambda: AcmeKybProvider())
    assert caught.value.reason == "name_conflict"


def test_native_names_are_validated() -> None:
    with pytest.raises(ProviderRegistryError) as caught:
        ProviderRegistry.register_native("Acme-KYB", AcmeKybProvider)
    assert caught.value.reason == "invalid_name"


def test_plugin_cannot_replace_a_native_provider() -> None:
    ProviderRegistry.register_native("acme_kyb", AcmeKybProvider)
    impostor = type("Impostor", (AcmeKybProvider,), {})
    with pytest.raises(ProviderRegistryError) as caught:
        ProviderRegistry.register_plugin(impostor)
    assert caught.value.reason == "name_conflict"
    assert ProviderRegistry.create("acme_kyb").__class__ is AcmeKybProvider


@pytest.mark.parametrize(
    "candidate",
    [
        object,
        AcmeKybProvider(),
        type("NoName", (VerificationProvider,), {"capabilities": frozenset()}),
        type("BadName", (VerificationProvider,), {"name": "Acme KYB", "capabilities": frozenset()}),
        type("BadCaps", (VerificationProvider,), {"name": "acme_kyb", "capabilities": {"resolve"}}),
        type("StrCaps", (VerificationProvider,), {"name": "acme_kyb", "capabilities": frozenset({"resolve"})}),
    ],
)
def test_malformed_plugin_providers_are_rejected(candidate: Any) -> None:
    with pytest.raises(ProviderRegistryError) as caught:
        ProviderRegistry.register_plugin(candidate)
    assert caught.value.reason == "invalid_type"


def test_creating_an_unknown_provider_fails_closed() -> None:
    with pytest.raises(ProviderRegistryError) as caught:
        ProviderRegistry.create("nobody")
    assert caught.value.reason == "unknown_provider"


def test_a_factory_that_raises_fails_closed_with_a_reason() -> None:
    class NeedsConfig(AcmeKybProvider):
        name = "needs_config"

        def __init__(self) -> None:
            raise RuntimeError("ACME_KYB_API_URL is not set")

    ProviderRegistry.register_plugin(NeedsConfig)
    with pytest.raises(ProviderRegistryError) as caught:
        ProviderRegistry.create("needs_config")
    assert caught.value.reason == "construction_failed"
    assert "ACME_KYB_API_URL is not set" in caught.value.detail


@pytest.mark.parametrize(
    "factory",
    [
        lambda: object(),
        lambda: type("Renamed", (AcmeKybProvider,), {"name": "someone_else"})(),
        lambda: type("Uncapable", (AcmeKybProvider,), {"capabilities": ["resolve"]})(),
    ],
)
def test_a_factory_returning_a_malformed_provider_fails_closed(factory: Callable[[], Any]) -> None:
    ProviderRegistry.register_native("acme_kyb", factory)
    with pytest.raises(ProviderRegistryError) as caught:
        ProviderRegistry.create("acme_kyb")
    assert caught.value.reason == "invalid_provider"
