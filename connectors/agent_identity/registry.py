# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Registry of agent identity providers.

The native ``mock`` provider registers on import and can only be created where ``AGENTICORG_ENV`` is
explicitly local, development or test. Plugin providers are added by ``connectors.plugins`` from the
``agenticorg.agent_identity_providers`` entry-point group and never replace a native provider.

Creating a provider fails closed: an unknown name, a provider unavailable here, or a factory that
raises or returns something that is not a well-formed :class:`AgentIdentityProvider` raises
:class:`AgentIdentityRegistryError` with a reason code.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from connectors.agent_identity.mock import create_mock_agent_identity_provider, mock_agent_identity_available
from connectors.framework.agent_identity_provider import AgentIdentityProvider

IdentityProviderFactory = Callable[[], AgentIdentityProvider]

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class AgentIdentityRegistryError(ValueError):
    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


@dataclass(frozen=True)
class IdentityProviderRegistration:
    name: str
    factory: IdentityProviderFactory
    source: Literal["native", "plugin"]
    available: Callable[[], bool] | None = None

    def is_available(self) -> bool:
        return self.available is None or bool(self.available())


class AgentIdentityProviderRegistry:
    _providers: ClassVar[dict[str, IdentityProviderRegistration]] = {}

    @classmethod
    def register_native(
        cls, name: str, factory: IdentityProviderFactory, *, available: Callable[[], bool] | None = None
    ) -> None:
        if not _NAME.match(name):
            raise AgentIdentityRegistryError("invalid_name", f"provider name must match {_NAME.pattern}")
        existing = cls._providers.get(name)
        if existing is not None and existing.factory is not factory:
            raise AgentIdentityRegistryError("name_conflict", f"identity provider {name!r} is already registered")
        cls._providers[name] = IdentityProviderRegistration(name, factory, "native", available)

    @classmethod
    def register_plugin(cls, provider_cls: Any) -> str:
        """Register an :class:`AgentIdentityProvider` subclass from a plugin; returns its name."""
        if not (isinstance(provider_cls, type) and issubclass(provider_cls, AgentIdentityProvider)):
            raise AgentIdentityRegistryError(
                "invalid_type", f"expected an AgentIdentityProvider subclass, got {provider_cls!r}"
            )
        name = getattr(provider_cls, "name", None)
        if not isinstance(name, str) or not _NAME.match(name):
            raise AgentIdentityRegistryError(
                "invalid_type", f"{provider_cls.__name__}: name must match {_NAME.pattern}"
            )
        if name in cls._providers:
            raise AgentIdentityRegistryError("name_conflict", f"identity provider {name!r} is already registered")
        cls._providers[name] = IdentityProviderRegistration(name, provider_cls, "plugin")
        return name

    @classmethod
    def get(cls, name: str) -> IdentityProviderRegistration | None:
        return cls._providers.get(name)

    @classmethod
    def names(cls, *, include_unavailable: bool = False) -> list[str]:
        return sorted(n for n, r in cls._providers.items() if include_unavailable or r.is_available())

    @classmethod
    def create(cls, name: str) -> AgentIdentityProvider:
        registration = cls._providers.get(name)
        if registration is None:
            raise AgentIdentityRegistryError("unknown_provider", f"no identity provider named {name!r}")
        if not registration.is_available():
            raise AgentIdentityRegistryError("unavailable", f"identity provider {name!r} cannot run here")
        try:
            provider = registration.factory()
        # enterprise-gate: broad-except-ok reason=identity-provider-factory-failure-is-reported-as-construction-failed
        except Exception as exc:  # noqa: BLE001
            # Only the type: a constructor's message can carry configuration or credentials.
            raise AgentIdentityRegistryError("construction_failed", f"{name}: {type(exc).__name__}") from exc
        if not isinstance(provider, AgentIdentityProvider) or getattr(provider, "name", None) != name:
            raise AgentIdentityRegistryError("invalid_provider", f"{name}: factory returned {type(provider).__name__}")
        return provider


NATIVE_IDENTITY_PROVIDERS: tuple[tuple[str, IdentityProviderFactory, Callable[[], bool] | None], ...] = (
    ("mock", create_mock_agent_identity_provider, mock_agent_identity_available),
)


def register_native_identity_providers() -> None:
    for name, factory, available in NATIVE_IDENTITY_PROVIDERS:
        AgentIdentityProviderRegistry.register_native(name, factory, available=available)


register_native_identity_providers()
