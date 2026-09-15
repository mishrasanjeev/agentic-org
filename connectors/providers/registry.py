# SPDX-License-Identifier: Apache-2.0
"""Registry of verification providers.

Native providers (shipped in this repository) register when this module is imported. Plugin
providers are added afterwards by ``connectors.plugins`` from the ``agenticorg.providers``
entry-point group and can never replace a native provider of the same name.

Creating a provider fails closed: an unknown name, a factory that raises or returns something
that is not a well-formed :class:`VerificationProvider` raises :class:`ProviderRegistryError` with
a reason code.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from connectors.framework.verification_provider import Capability, VerificationProvider
from connectors.providers.mock import create_mock_provider

ProviderFactory = Callable[[], VerificationProvider]

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class ProviderRegistryError(ValueError):
    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


@dataclass(frozen=True)
class ProviderRegistration:
    name: str
    factory: ProviderFactory
    source: Literal["native", "plugin"]


def _well_formed_identity(name: Any, capabilities: Any) -> str | None:
    """Return why a provider's declared identity is malformed, or ``None`` when it is fine."""
    if not isinstance(name, str) or not _NAME.match(name):
        return f"name must match {_NAME.pattern}, got {name!r}"
    if not isinstance(capabilities, frozenset) or not all(isinstance(c, Capability) for c in capabilities):
        return f"capabilities must be a frozenset of Capability, got {capabilities!r}"
    return None


class ProviderRegistry:
    _providers: ClassVar[dict[str, ProviderRegistration]] = {}

    @classmethod
    def register_native(cls, name: str, factory: ProviderFactory) -> None:
        if not _NAME.match(name):
            raise ProviderRegistryError("invalid_name", f"provider name must match {_NAME.pattern}, got {name!r}")
        existing = cls._providers.get(name)
        if existing is not None and existing.factory is not factory:
            raise ProviderRegistryError("name_conflict", f"provider {name!r} is already registered")
        cls._providers[name] = ProviderRegistration(name=name, factory=factory, source="native")

    @classmethod
    def register_plugin(cls, provider_cls: Any) -> str:
        """Register a :class:`VerificationProvider` subclass from a plugin; returns its name.

        ``name`` and ``capabilities`` must be declared on the class itself: they are checked here,
        before the class is ever constructed. :meth:`create` constructs it with no arguments; an
        instance may narrow its capabilities, but registration validates the class-level declaration.
        """
        if not (isinstance(provider_cls, type) and issubclass(provider_cls, VerificationProvider)):
            raise ProviderRegistryError(
                "invalid_type", f"expected a VerificationProvider subclass, got {provider_cls!r}"
            )
        problem = _well_formed_identity(
            getattr(provider_cls, "name", None), getattr(provider_cls, "capabilities", None)
        )
        if problem:
            raise ProviderRegistryError("invalid_type", f"{provider_cls.__name__}: {problem}")
        name: str = provider_cls.name
        if name in cls._providers:
            raise ProviderRegistryError("name_conflict", f"provider {name!r} is already registered")
        cls._providers[name] = ProviderRegistration(name=name, factory=provider_cls, source="plugin")
        return name

    @classmethod
    def get(cls, name: str) -> ProviderRegistration | None:
        return cls._providers.get(name)

    @classmethod
    def names(cls) -> list[str]:
        return sorted(cls._providers)

    @classmethod
    def create(cls, name: str) -> VerificationProvider:
        registration = cls._providers.get(name)
        if registration is None:
            raise ProviderRegistryError("unknown_provider", f"no provider named {name!r}")
        try:
            provider = registration.factory()
        # enterprise-gate: broad-except-ok reason=provider-factory-failure-is-reported-as-construction-failed
        except Exception as exc:  # noqa: BLE001
            # Only the exception type: a constructor's message can carry configuration or credentials.
            raise ProviderRegistryError("construction_failed", f"{name}: {type(exc).__name__}") from exc
        if not isinstance(provider, VerificationProvider):
            raise ProviderRegistryError("invalid_provider", f"{name}: factory returned {type(provider).__name__}")
        problem = _well_formed_identity(getattr(provider, "name", None), getattr(provider, "capabilities", None))
        if problem:
            raise ProviderRegistryError("invalid_provider", f"{name}: {problem}")
        if provider.name != name:
            raise ProviderRegistryError(
                "invalid_provider", f"{name}: factory returned provider named {provider.name!r}"
            )
        return provider


#: Native providers, registered on import. ``connectors.plugins`` imports this module before
#: registering plugins, so natives are always present first.
NATIVE_PROVIDERS: tuple[tuple[str, ProviderFactory], ...] = (("mock", create_mock_provider),)


def register_native_providers() -> None:
    for name, factory in NATIVE_PROVIDERS:
        ProviderRegistry.register_native(name, factory)


register_native_providers()
