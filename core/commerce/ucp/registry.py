# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The UCP capability, service and payment-handler registry.

A business profile lists only what is actually served (UCP 2026-08-25
overview, Services and Capabilities). Each surface registers here with a
``served`` predicate over the request's :class:`UcpServeContext` (feature flags
and merchant state); the profile renderer asks every registration and emits the
ones that answer true for the requested release. Phase 3 surfaces register
themselves at import time:

* catalog search and lookup (this package) are served whenever the profile is;
* location search and lookup (2026-08-25 only) when the merchant has a store;
* checkout, cart and order register from the transaction module behind
  ``AGENTICORG_UCP_CHECKOUT_ENABLED``;
* payment handlers register a renderer for their profile entries.

A registration is keyed by ``name`` (and ``transport`` for services);
registering the same key twice raises, so two modules cannot silently publish
conflicting entries.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from core.commerce.ucp import v2026_04_08, v2026_08_25
from core.commerce.ucp.authority import entry_authority_valid, is_ucp_governed, name_bound_to_host
from core.commerce.ucp.versions import UCP_2026_04_08, UCP_2026_08_25

logger = structlog.get_logger()

SHOPPING_SERVICE = "dev.ucp.shopping"
COMMON_SERVICE = "dev.ucp.common"
CATALOG_SEARCH = "dev.ucp.shopping.catalog.search"
CATALOG_LOOKUP = "dev.ucp.shopping.catalog.lookup"
LOCATION_SEARCH = "dev.ucp.common.location.search"
LOCATION_LOOKUP = "dev.ucp.common.location.lookup"
TRANSPORTS: frozenset[str] = frozenset({"rest", "mcp", "a2a", "embedded"})


@dataclass(frozen=True)
class UcpServeContext:
    """What a ``served`` predicate may consult for one profile render."""

    host: str
    version: str
    tenant_id: str
    merchant_id: str
    seller_agent_id: str
    namespace: str | None
    location_count: int = 0
    # The merchant commerce config (core.commerce.ucp.merchant.config_from_row) for predicates that
    # depend on merchant settings, such as the transaction extensions; pure data, no I/O.
    merchant_config: Mapping[str, Any] | None = None

    @property
    def rest_endpoint(self) -> str:
        """The UCP REST base for this serving host (no trailing slash)."""
        return f"https://{self.host}/ucp"


def _always(_ctx: UcpServeContext) -> bool:
    return True


@dataclass(frozen=True)
class UcpCapabilityRegistration:
    """One capability or extension. ``spec_urls`` / ``schema_urls`` are keyed by UCP release."""

    name: str
    service: str
    spec_urls: Mapping[str, str]
    schema_urls: Mapping[str, str]
    extends: tuple[str, ...] = ()
    served: Callable[[UcpServeContext], bool] = _always
    config: Callable[[UcpServeContext], Mapping[str, Any] | None] | None = None
    # Parents in releases where they differ from ``extends`` (buyer consent: checkout only in 2026-04-08).
    extends_by_version: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def parents(self, version: str) -> tuple[str, ...]:
        return tuple(self.extends_by_version.get(version, self.extends))

    def entry(self, ctx: UcpServeContext) -> dict[str, Any] | None:
        schema = self.schema_urls.get(ctx.version)
        if schema is None or not self.served(ctx):
            return None
        entry: dict[str, Any] = {"version": ctx.version}
        spec = self.spec_urls.get(ctx.version)
        if spec is not None:
            entry["spec"] = spec
        entry["schema"] = schema
        parents = self.parents(ctx.version)
        if parents:
            entry["extends"] = parents[0] if len(parents) == 1 else list(parents)
        if self.config is not None:
            config = self.config(ctx)
            if config:
                entry["config"] = dict(config)
        return entry


@dataclass(frozen=True)
class UcpServiceRegistration:
    """One service binding (service name + one transport)."""

    name: str
    transport: str
    spec_urls: Mapping[str, str]
    schema_urls: Mapping[str, str]
    endpoint: Callable[[UcpServeContext], str | None]
    served: Callable[[UcpServeContext], bool] = _always

    def entry(self, ctx: UcpServeContext) -> dict[str, Any] | None:
        if ctx.version not in self.schema_urls or not self.served(ctx):
            return None
        entry: dict[str, Any] = {"version": ctx.version}
        spec = self.spec_urls.get(ctx.version)
        if spec is not None:
            entry["spec"] = spec
        entry["transport"] = self.transport
        endpoint = self.endpoint(ctx)
        if endpoint is not None:
            entry["endpoint"] = endpoint
        elif self.transport != "embedded":
            return None
        entry["schema"] = self.schema_urls[ctx.version]
        return entry


@dataclass(frozen=True)
class UcpPaymentHandlerRegistration:
    """A payment handler family. ``entries`` renders its profile entries (each needs ``id`` and ``version``)."""

    name: str
    entries: Callable[[UcpServeContext], Sequence[Mapping[str, Any]]]
    versions: tuple[str, ...] = (UCP_2026_04_08, UCP_2026_08_25)


@dataclass
class UcpRegistry:
    capabilities: dict[str, UcpCapabilityRegistration] = field(default_factory=dict)
    services: dict[tuple[str, str], UcpServiceRegistration] = field(default_factory=dict)
    payment_handlers: dict[str, UcpPaymentHandlerRegistration] = field(default_factory=dict)

    def register_capability(self, registration: UcpCapabilityRegistration) -> None:
        if registration.name in self.capabilities:
            raise ValueError(f"UCP capability already registered: {registration.name}")
        self.capabilities[registration.name] = registration

    def register_service(self, registration: UcpServiceRegistration) -> None:
        if registration.transport not in TRANSPORTS:
            raise ValueError(f"unknown UCP transport: {registration.transport}")
        key = (registration.name, registration.transport)
        if key in self.services:
            raise ValueError(f"UCP service already registered: {registration.name} {registration.transport}")
        self.services[key] = registration

    def register_payment_handler(self, registration: UcpPaymentHandlerRegistration) -> None:
        if registration.name in self.payment_handlers:
            raise ValueError(f"UCP payment handler already registered: {registration.name}")
        self.payment_handlers[registration.name] = registration

    def unregister(self, name: str) -> None:
        """Remove every registration under ``name`` (tests only)."""
        self.capabilities.pop(name, None)
        self.payment_handlers.pop(name, None)
        for key in [key for key in self.services if key[0] == name]:
            self.services.pop(key, None)

    def served_capability_names(self, ctx: UcpServeContext) -> set[str]:
        return set(self.render(ctx)["capabilities"])

    def render(self, ctx: UcpServeContext) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """The ``services``, ``capabilities`` and ``payment_handlers`` registries for ``ctx``.

        Entries whose URLs are not authority-bound to their name are dropped;
        vendor entries in the merchant's own namespace additionally need the
        attested namespace. Extensions whose parents are all absent are pruned
        until stable (overview, Intersection Algorithm step 3 applied to the
        business's own list). A service is emitted only when a capability it
        carries is served.
        """
        capabilities: dict[str, list[dict[str, Any]]] = {}
        parents: dict[str, tuple[str, ...]] = {}
        service_of: dict[str, str] = {}
        for name, registration in sorted(self.capabilities.items()):
            entry = registration.entry(ctx)
            if entry is None:
                continue
            if not self._entry_allowed(name, entry, ctx):
                continue
            capabilities[name] = [entry]
            parents[name] = registration.parents(ctx.version)
            service_of[name] = registration.service
        changed = True
        while changed:
            changed = False
            for name in list(capabilities):
                extends = parents.get(name) or ()
                if extends and not any(parent in capabilities for parent in extends):
                    capabilities.pop(name)
                    changed = True
        active_services = {service_of[name] for name in capabilities}
        services: dict[str, list[dict[str, Any]]] = {}
        for (name, _transport), registration in sorted(self.services.items()):
            if name not in active_services:
                continue
            entry = registration.entry(ctx)
            if entry is None or not self._entry_allowed(name, entry, ctx):
                continue
            services.setdefault(name, []).append(entry)
        payment_handlers: dict[str, list[dict[str, Any]]] = {}
        for name, registration in sorted(self.payment_handlers.items()):
            if ctx.version not in registration.versions:
                continue
            entries = []
            for raw in registration.entries(ctx):
                entry = dict(raw)
                if not isinstance(entry.get("id"), str) or not isinstance(entry.get("version"), str):
                    logger.warning("ucp_payment_handler_entry_invalid", handler=name)
                    continue
                if not self._entry_allowed(name, entry, ctx, handler=True):
                    continue
                entries.append(entry)
            if entries:
                payment_handlers[name] = entries
        return {"services": services, "capabilities": capabilities, "payment_handlers": payment_handlers}

    @staticmethod
    def _entry_allowed(name: str, entry: Mapping[str, Any], ctx: UcpServeContext, *, handler: bool = False) -> bool:
        if not entry_authority_valid(name, entry, ctx.version):
            logger.warning("ucp_entry_authority_unbound", entry_name=name, version=ctx.version)
            return False
        if is_ucp_governed(name):
            # Release snapshot rule: every dev.ucp.* entry declares the profile's version.
            return handler or entry.get("version") == ctx.version
        if name_bound_to_host(name, ctx.host) and (
            ctx.namespace is None or not (name == ctx.namespace or name.startswith(ctx.namespace + "."))
        ):
            logger.warning("ucp_vendor_entry_without_namespace_attestation", entry_name=name)
            return False
        return True


def _merchant_has_locations(ctx: UcpServeContext) -> bool:
    return ctx.location_count > 0


def _rest_endpoint(ctx: UcpServeContext) -> str:
    return ctx.rest_endpoint


def _build_default_registry() -> UcpRegistry:
    registry = UcpRegistry()
    registry.register_service(
        UcpServiceRegistration(
            name=SHOPPING_SERVICE,
            transport="rest",
            spec_urls={UCP_2026_04_08: v2026_04_08.OVERVIEW_SPEC, UCP_2026_08_25: v2026_08_25.OVERVIEW_SPEC},
            schema_urls={
                UCP_2026_04_08: v2026_04_08.SHOPPING_REST_SCHEMA,
                UCP_2026_08_25: v2026_08_25.SHOPPING_REST_SCHEMA,
            },
            endpoint=_rest_endpoint,
        )
    )
    registry.register_service(
        UcpServiceRegistration(
            name=COMMON_SERVICE,
            transport="rest",
            spec_urls={UCP_2026_08_25: v2026_08_25.OVERVIEW_SPEC},
            schema_urls={UCP_2026_08_25: v2026_08_25.COMMON_REST_SCHEMA},
            endpoint=_rest_endpoint,
        )
    )
    registry.register_capability(
        UcpCapabilityRegistration(
            name=CATALOG_SEARCH,
            service=SHOPPING_SERVICE,
            spec_urls={
                UCP_2026_04_08: v2026_04_08.CATALOG_SEARCH_SPEC,
                UCP_2026_08_25: v2026_08_25.CATALOG_SEARCH_SPEC,
            },
            schema_urls={
                UCP_2026_04_08: v2026_04_08.CATALOG_SEARCH_SCHEMA,
                UCP_2026_08_25: v2026_08_25.CATALOG_SEARCH_SCHEMA,
            },
        )
    )
    registry.register_capability(
        UcpCapabilityRegistration(
            name=CATALOG_LOOKUP,
            service=SHOPPING_SERVICE,
            spec_urls={
                UCP_2026_04_08: v2026_04_08.CATALOG_LOOKUP_SPEC,
                UCP_2026_08_25: v2026_08_25.CATALOG_LOOKUP_SPEC,
            },
            schema_urls={
                UCP_2026_04_08: v2026_04_08.CATALOG_LOOKUP_SCHEMA,
                UCP_2026_08_25: v2026_08_25.CATALOG_LOOKUP_SCHEMA,
            },
        )
    )
    registry.register_capability(
        UcpCapabilityRegistration(
            name=LOCATION_SEARCH,
            service=COMMON_SERVICE,
            spec_urls={UCP_2026_08_25: v2026_08_25.LOCATION_SEARCH_SPEC},
            schema_urls={UCP_2026_08_25: v2026_08_25.LOCATION_SEARCH_SCHEMA},
            served=_merchant_has_locations,
        )
    )
    registry.register_capability(
        UcpCapabilityRegistration(
            name=LOCATION_LOOKUP,
            service=COMMON_SERVICE,
            spec_urls={UCP_2026_08_25: v2026_08_25.LOCATION_LOOKUP_SPEC},
            schema_urls={UCP_2026_08_25: v2026_08_25.LOCATION_LOOKUP_SCHEMA},
            served=_merchant_has_locations,
        )
    )
    return registry


ucp_registry: UcpRegistry = _build_default_registry()
"""The process-wide registry the profile renders from; transaction surfaces register into it at import."""
