# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The transaction capabilities (checkout, cart, order and their extensions) and their payment handlers.

Each ``CapabilityEntry`` says, per merchant, whether the capability is
available (an extension whose settings are missing, such as no loyalty
programme, no split-payment combinations or no signing key for AP2, is not),
its parents, its business config and which responses carry it.
``publish_to_profile_registry`` registers every entry and every payment
handler with the profile registry (``core.commerce.ucp.registry.ucp_registry``),
served only while ``AGENTICORG_UCP_CHECKOUT_ENABLED`` is on, so
``/.well-known/ucp`` advertises exactly what the transaction handlers
negotiate and serve.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.commerce.ucp import releases as v
from core.commerce.ucp.merchant import MerchantUcp


@dataclass(frozen=True)
class CapabilityEntry:
    """A capability or extension this server implements."""

    name: str
    versions: tuple[str, ...]
    extends: tuple[str, ...] = ()
    # The resources whose responses carry it (UCP response capability selection).
    resources: tuple[str, ...] = ()
    available: Callable[[MerchantUcp, RegistryContext], bool] = lambda _m, _c: True
    config: Callable[[MerchantUcp, str], dict[str, Any] | None] = lambda _m, _v: None
    # Parents in releases where they differ from ``extends``.
    extends_by_version: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # A vendor extension's schema on its own registrable domain (dev.ucp.* entries use ucp.dev).
    schema: str | None = None

    def schema_for(self, version: str) -> str:
        return self.schema or v.schema_url(version, self.name)

    def extends_for(self, version: str) -> tuple[str, ...]:
        return dict(self.extends_by_version).get(version, self.extends)

    def response_entry(self, version: str) -> dict[str, Any]:
        """The entry in a response's ``ucp.capabilities`` (version and parents only)."""
        entry: dict[str, Any] = {"version": version}
        parents = self.extends_for(version)
        if parents:
            entry["extends"] = parents[0] if len(parents) == 1 else list(parents)
        return entry

    def profile_entry(self, merchant: MerchantUcp, version: str) -> dict[str, Any]:
        entry: dict[str, Any] = {**self.response_entry(version), "schema": self.schema_for(version)}
        config = self.config(merchant, version)
        if config:
            entry["config"] = config
        return entry


@dataclass(frozen=True)
class PaymentHandlerEntry:
    """A payment handler instance a merchant accepts (``ucp.payment_handlers[name][]``)."""

    name: str
    id: str
    version: str
    schema: str | None
    spec: str | None
    available_instruments: tuple[dict[str, Any], ...]
    config: dict[str, Any] = field(default_factory=dict)

    def render(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "version": self.version}
        if self.spec:
            out["spec"] = self.spec
        if self.schema:
            out["schema"] = self.schema
        if self.available_instruments:
            out["available_instruments"] = [dict(item) for item in self.available_instruments]
        if self.config:
            out["config"] = dict(self.config)
        return out


@dataclass(frozen=True)
class RegistryContext:
    """Runtime facts the availability predicates read (no I/O inside a predicate)."""

    merchant_signing_available: bool = False


PaymentHandlerProvider = Callable[[MerchantUcp, str], Sequence[PaymentHandlerEntry]]


class CapabilityRegistry(Protocol):
    def capabilities(self, merchant: MerchantUcp, version: str, context: RegistryContext) -> list[CapabilityEntry]: ...

    def payment_handlers(self, merchant: MerchantUcp, version: str) -> list[PaymentHandlerEntry]: ...


class Registry:
    """The in-process registry; registration happens at import time, nothing is per request."""

    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilityEntry] = {}
        self._handler_providers: list[PaymentHandlerProvider] = []

    def register_capability(self, entry: CapabilityEntry) -> None:
        self._capabilities[entry.name] = entry

    def register_payment_handler_provider(self, provider: PaymentHandlerProvider) -> None:
        if provider not in self._handler_providers:
            self._handler_providers.append(provider)

    def capabilities(self, merchant: MerchantUcp, version: str, context: RegistryContext) -> list[CapabilityEntry]:
        return [
            entry
            for entry in self._capabilities.values()
            if version in entry.versions and entry.available(merchant, context)
        ]

    def payment_handlers(self, merchant: MerchantUcp, version: str) -> list[PaymentHandlerEntry]:
        handlers: list[PaymentHandlerEntry] = []
        for provider in self._handler_providers:
            handlers.extend(provider(merchant, version))
        return handlers

    def business_capabilities(
        self, merchant: MerchantUcp, version: str, context: RegistryContext
    ) -> dict[str, list[dict[str, Any]]]:
        """``ucp.capabilities`` for the merchant's profile in one release."""
        return {
            entry.name: [entry.profile_entry(merchant, version)]
            for entry in self.capabilities(merchant, version, context)
        }

    def business_payment_handlers(self, merchant: MerchantUcp, version: str) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for handler in self.payment_handlers(merchant, version):
            out.setdefault(handler.name, []).append(handler.render())
        return out


ALL = v.KNOWN_VERSIONS
R25_ONLY = (v.V2026_08_25,)


def _fulfillment_config(merchant: MerchantUcp, version: str) -> dict[str, Any] | None:
    settings = merchant.fulfillment
    methods = [m for m, on in (("shipping", settings.shipping), ("pickup", settings.pickup)) if on]
    combos = [list(c) for c in settings.method_combinations if set(c) <= set(methods)]
    if version < v.V2026_08_25:
        config: dict[str, Any] = {
            "allows_multi_destination": {m: m in settings.multi_destination for m in ("shipping", "pickup")},
        }
        if combos:
            config["allows_method_combinations"] = combos
        return config
    config = {"multi_destination": [{"method": m} for m in settings.multi_destination if m in methods]}
    if combos:
        config["method_combinations"] = combos
    return config


def _split_config(merchant: MerchantUcp, _version: str) -> dict[str, Any] | None:
    split = merchant.split_payments or {}
    combos = split.get("allowed_combinations")
    return {"allowed_combinations": combos} if combos else None


def _permalink_config(merchant: MerchantUcp, _version: str) -> dict[str, Any]:
    return {"endpoint": merchant.permalink_endpoint}


def _ap2_config(_merchant: MerchantUcp, _version: str) -> dict[str, Any]:
    return {"vp_formats_supported": {"dc+sd-jwt": {}}}


def _upi_mandate_available(merchant: MerchantUcp, _context: RegistryContext) -> bool:
    from core.commerce.india_rails import INSTRUMENT_UPI_AUTOPAY

    capability = merchant.mandate_capability
    return (
        capability is not None
        and capability.upi_autopay is not None
        and INSTRUMENT_UPI_AUTOPAY in capability.instrument_types
    )


def _register_defaults(registry: Registry) -> None:
    registry.register_capability(CapabilityEntry(name=v.CHECKOUT, versions=ALL, resources=("checkout",)))
    registry.register_capability(CapabilityEntry(name=v.CART, versions=ALL, resources=("cart",)))
    registry.register_capability(CapabilityEntry(name=v.ORDER, versions=ALL, resources=("order",)))
    registry.register_capability(
        CapabilityEntry(
            name=v.PERMALINK,
            versions=ALL,
            resources=(),
            available=lambda m, _c: m.permalink_endpoint is not None,
            config=_permalink_config,
        )
    )
    registry.register_capability(
        CapabilityEntry(
            name=v.FULFILLMENT,
            versions=ALL,
            extends=(v.CHECKOUT,),
            resources=("checkout",),
            available=lambda m, _c: m.fulfillment.shipping or m.fulfillment.pickup,
            config=_fulfillment_config,
        )
    )
    # 2026-04-08 extends checkout only; 2026-08-25 extends checkout and cart.
    registry.register_capability(
        CapabilityEntry(
            name=v.BUYER_CONSENT,
            versions=ALL,
            extends=(v.CHECKOUT, v.CART),
            extends_by_version=((v.V2026_04_08, (v.CHECKOUT,)),),
            resources=("checkout", "cart"),
        )
    )
    registry.register_capability(
        CapabilityEntry(
            name=v.LOYALTY,
            versions=ALL,
            extends=(v.CHECKOUT, v.CART),
            resources=("checkout", "cart"),
            available=lambda m, _c: m.loyalty is not None,
        )
    )
    registry.register_capability(
        CapabilityEntry(
            name=v.PAYMENT_AUTHENTICATION,
            versions=R25_ONLY,
            extends=(v.CHECKOUT,),
            resources=("checkout",),
            available=lambda m, _c: bool((m.payment_authentication or {}).get("allowed_origins")),
        )
    )
    registry.register_capability(
        CapabilityEntry(
            name=v.SPLIT_PAYMENTS,
            versions=R25_ONLY,
            extends=(v.CHECKOUT,),
            resources=("checkout",),
            available=lambda m, _c: bool((m.split_payments or {}).get("allowed_combinations")),
            config=_split_config,
        )
    )
    registry.register_capability(
        CapabilityEntry(
            name=v.PAYMENT_TERMS,
            versions=R25_ONLY,
            extends=(v.CHECKOUT, v.ORDER),
            resources=("checkout", "order"),
            available=lambda m, _c: bool(m.payment_terms),
        )
    )
    # UPI AutoPay e-mandates: declares the RBI AFA Action type (core.commerce.india_rails).
    from core.commerce.india_rails import UCP_UPI_MANDATE_EXTENSION, UCP_UPI_MANDATE_SCHEMA

    registry.register_capability(
        CapabilityEntry(
            name=UCP_UPI_MANDATE_EXTENSION,
            versions=R25_ONLY,
            extends=(v.CHECKOUT,),
            resources=("checkout",),
            available=_upi_mandate_available,
            schema=UCP_UPI_MANDATE_SCHEMA,
        )
    )
    # AP2 needs the merchant's signing key for ap2.merchant_authorization on every response.
    for name, releases in ((v.AP2_MANDATE_R08, (v.V2026_04_08,)), (v.AP2_MANDATE_R25, R25_ONLY)):
        registry.register_capability(
            CapabilityEntry(
                name=name,
                versions=releases,
                extends=(v.CHECKOUT,),
                resources=("checkout",),
                available=lambda _m, c: c.merchant_signing_available,
                config=_ap2_config,
            )
        )


DEFAULT_REGISTRY = Registry()
_register_defaults(DEFAULT_REGISTRY)


# --- publication through the profile registry ----------------------------------------------------

_ROOT_SPEC_PAGES = {
    v.CHECKOUT: ("checkout", "shopping/checkout"),
    v.CART: ("cart", "shopping/cart"),
    v.ORDER: ("order", "shopping/order"),
}


def merchant_for_context(ctx: Any) -> MerchantUcp | None:
    """The merchant settings a profile render context carries (pure: no I/O)."""
    from core.commerce.ucp.merchant import merchant_ucp_from_config

    if not ctx.merchant_config:
        return None
    return merchant_ucp_from_config(
        ctx.merchant_config,
        tenant_id=ctx.tenant_id,
        serving_host=ctx.host,
        namespace=ctx.namespace,
        signing_available=True,
    )


def _checkout_enabled() -> bool:
    from core.config import settings

    return bool(getattr(settings, "ucp_checkout_enabled", False))


def publish_to_profile_registry(source: Registry = DEFAULT_REGISTRY, target: Any = None) -> None:
    """Register the transaction capabilities and payment handlers with the profile registry (idempotent)."""
    from core.commerce.ucp.registry import (
        SHOPPING_SERVICE,
        UcpCapabilityRegistration,
        UcpPaymentHandlerRegistration,
        ucp_registry,
    )

    registry = target if target is not None else ucp_registry
    for entry in source._capabilities.values():
        if entry.name in registry.capabilities:
            continue

        def _served(ctx: Any, entry: CapabilityEntry = entry) -> bool:
            if not _checkout_enabled() or ctx.version not in entry.versions:
                return False
            merchant = merchant_for_context(ctx)
            return merchant is not None and entry.available(merchant, RegistryContext(merchant_signing_available=True))

        def _config(ctx: Any, entry: CapabilityEntry = entry) -> dict[str, Any] | None:
            merchant = merchant_for_context(ctx)
            return None if merchant is None else entry.config(merchant, ctx.version)

        pages = _ROOT_SPEC_PAGES.get(entry.name)
        spec_urls = (
            {
                v.V2026_04_08: f"https://ucp.dev/{v.V2026_04_08}/specification/{pages[0]}",
                v.V2026_08_25: f"https://ucp.dev/{v.V2026_08_25}/specification/{pages[1]}",
            }
            if pages
            else {}
        )
        registry.register_capability(
            UcpCapabilityRegistration(
                name=entry.name,
                service=SHOPPING_SERVICE,
                spec_urls={k: val for k, val in spec_urls.items() if k in entry.versions},
                schema_urls={version: entry.schema_for(version) for version in entry.versions},
                extends=entry.extends,
                extends_by_version=dict(entry.extends_by_version),
                served=_served,
                config=_config,
            )
        )
    from core.commerce.payment_handlers import registered_handlers

    for name, handler in registered_handlers().items():
        if name in registry.payment_handlers:
            continue

        def _entries(ctx: Any, handler: Any = handler) -> list[dict[str, Any]]:
            merchant = merchant_for_context(ctx)
            if not _checkout_enabled() or merchant is None:
                return []
            return [entry.render() for entry in handler.entries(merchant, ctx.version)]

        registry.register_payment_handler(UcpPaymentHandlerRegistration(name=name, entries=_entries))
