# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The providers registry and loading providers from the ``agenticorg.providers`` entry-point group."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest

from connectors import plugins
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
    assert caught.value.detail == "needs_config: RuntimeError"
    assert "ACME_KYB_API_URL" not in str(caught.value)


def test_scoped_plugin_requires_an_authenticated_uuid_and_never_uses_default_credentials() -> None:
    seen: list[UUID] = []

    class ScopedProvider(AcmeKybProvider):
        name = "scoped_kyb"

        def __init__(self) -> None:
            raise AssertionError("unscoped constructor must never run")

        @classmethod
        def for_tenant(cls, tenant_id: UUID) -> VerificationProvider:
            seen.append(tenant_id)
            return object.__new__(cls)

    ProviderRegistry.register_plugin(ScopedProvider)
    with pytest.raises(ProviderRegistryError, match="tenant_required"):
        ProviderRegistry.create("scoped_kyb")
    with pytest.raises(ProviderRegistryError, match="tenant_required"):
        ProviderRegistry.create("scoped_kyb", tenant_id=str(uuid4()))  # type: ignore[arg-type]
    assert not seen
    first, second = uuid4(), uuid4()
    ProviderRegistry.create("scoped_kyb", tenant_id=first)
    ProviderRegistry.create("scoped_kyb", tenant_id=second)
    assert seen == [first, second]


def test_scoped_plugin_configuration_failure_does_not_leak_or_fall_back() -> None:
    class ScopedProvider(AcmeKybProvider):
        name = "scoped_kyb"

        @classmethod
        def for_tenant(cls, tenant_id: UUID) -> VerificationProvider:
            raise ValueError("sensitive configuration")

    ProviderRegistry.register_plugin(ScopedProvider)
    with pytest.raises(ProviderRegistryError) as caught:
        ProviderRegistry.create("scoped_kyb", tenant_id=uuid4())
    assert caught.value.reason == "construction_failed"
    assert "sensitive configuration" not in str(caught.value)


def test_case_runtime_passes_its_tenant_to_scoped_plugin() -> None:
    from core.cases.runtime import CaseRuntime

    seen: list[UUID] = []

    class ScopedProvider(AcmeKybProvider):
        name = "scoped_kyb"

        @classmethod
        def for_tenant(cls, tenant_id: UUID) -> VerificationProvider:
            seen.append(tenant_id)
            return cls()

    ProviderRegistry.register_plugin(ScopedProvider)
    tenant = uuid4()
    assert CaseRuntime().provider_for("scoped_kyb", tenant).name == "scoped_kyb"
    assert seen == [tenant]


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


# --- entry-point loading ------------------------------------------------------------------------


@dataclass
class _Dist:
    name: str


class _EntryPoint:
    def __init__(self, name: str, dist: str, target: Callable[[], Any]) -> None:
        self.name = name
        self.dist = _Dist(dist)
        self._target = target
        self.loaded = False

    def load(self) -> Any:
        self.loaded = True
        return self._target()


def _providers_group(*eps: _EntryPoint) -> Callable[..., list[_EntryPoint]]:
    return lambda *, group: list(eps) if group == plugins.PROVIDERS_GROUP else []


def test_allowlisted_provider_plugin_is_registered() -> None:
    ep = _EntryPoint("acme_kyb", "acme-kyb-agenticorg", lambda: AcmeKybProvider)
    results = plugins.load_plugins(enabled=True, allowlist={"acme-kyb-agenticorg"}, entry_points=_providers_group(ep))
    assert [(r.loaded, r.reason, r.detail) for r in results] == [(True, "loaded", "acme_kyb")]
    registration = ProviderRegistry.get("acme_kyb")
    assert registration is not None and registration.source == "plugin"


def test_provider_plugin_outside_the_allowlist_is_not_imported() -> None:
    ep = _EntryPoint("acme_kyb", "acme-kyb-agenticorg", lambda: AcmeKybProvider)
    results = plugins.load_plugins(enabled=True, allowlist=set(), entry_points=_providers_group(ep))
    assert results[0].reason == "not_allowlisted" and ep.loaded is False
    assert ProviderRegistry.get("acme_kyb") is None


def test_provider_plugin_of_the_wrong_type_is_rejected() -> None:
    ep = _EntryPoint("acme_kyb", "acme-kyb-agenticorg", lambda: object)
    results = plugins.load_plugins(enabled=True, allowlist={"acme-kyb-agenticorg"}, entry_points=_providers_group(ep))
    assert results[0].reason == "invalid_type"


def test_native_provider_keeps_priority_over_a_provider_plugin() -> None:
    ProviderRegistry.register_native("acme_kyb", AcmeKybProvider)
    impostor = type("Impostor", (AcmeKybProvider,), {})
    ep = _EntryPoint("impostor", "acme-kyb-agenticorg", lambda: impostor)
    results = plugins.load_plugins(enabled=True, allowlist={"acme-kyb-agenticorg"}, entry_points=_providers_group(ep))
    assert results[0].reason == "name_conflict"
    assert ProviderRegistry.create("acme_kyb").__class__ is AcmeKybProvider
