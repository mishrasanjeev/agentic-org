# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Read-only Shopify Admin GraphQL reads for UCP: customer loyalty metafields and delivery profiles.

Both reads reuse the merchant's existing Shopify connector credentials and the
same transport rules as the catalogue sync (``ShopifyAdminGraphQLClient``):
async HTTP only, the ``*.myshopify.com`` egress allowlist, a DNS-pinned
transport, no redirects and a bounded timeout. Nothing is written to Shopify.

``ShopifyReads`` is the interface the checkout engine depends on; tests use a
fake. Delivery profiles are cached per merchant for five minutes (rates change
rarely and checkout updates are frequent).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import structlog

from core.commerce.arbiter import amount_minor_units
from core.commerce.c6z_runtime_vertical import ShopifyCredentials

logger = structlog.get_logger()

DELIVERY_PROFILE_CACHE_SECONDS = 300
# enterprise-gate: process-local-ok reason=per-replica-five-minute-cache-of-merchant-shipping-rates-not-security-state
_delivery_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}

CUSTOMER_METAFIELDS_QUERY = """
query UcpCustomerLoyalty($query: String!, $namespace: String!) {
  customers(first: 1, query: $query) {
    nodes {
      id
      metafields(namespace: $namespace, first: 20) {
        nodes { key value type }
      }
    }
  }
}
"""

DELIVERY_PROFILES_QUERY = """
query UcpDeliveryProfiles {
  deliveryProfiles(first: 10) {
    nodes {
      id
      name
      profileLocationGroups {
        locationGroupZones(first: 50) {
          nodes {
            zone {
              id
              name
              countries { code { countryCode restOfWorld } provinces { code } }
            }
            methodDefinitions(first: 20) {
              nodes {
                id
                name
                description
                active
                rateProvider {
                  ... on DeliveryRateDefinition { price { amount currencyCode } }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class DeliveryRate:
    """One flat-rate shipping method of a delivery zone."""

    id: str
    title: str
    description: str | None
    amount_minor_units: int
    currency: str
    countries: tuple[str, ...]
    rest_of_world: bool

    def serves(self, country: str | None) -> bool:
        return self.rest_of_world or (country is not None and country.upper() in self.countries)


class ShopifyReads(Protocol):
    async def customer_loyalty_metafields(self, *, email: str, namespace: str) -> dict[str, str] | None: ...

    async def delivery_rates(self) -> list[DeliveryRate]: ...


class ShopifyAdminReads:
    """``ShopifyReads`` over the Shopify Admin GraphQL API (read scopes only)."""

    def __init__(
        self,
        credentials: ShopifyCredentials,
        *,
        cache_key: tuple[str, str],
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._credentials = credentials
        self._cache_key = cache_key
        self._timeout = timeout_seconds
        self._transport = transport

    @property
    def graphql_url(self) -> str:
        return f"https://{self._credentials.shop_domain}/admin/api/{self._credentials.api_version}/graphql.json"

    async def _query(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        from core.security.egress import (
            EgressValidationError,
            build_pinned_async_transport,
            egress_dns_validation_required,
            validate_public_url_async,
        )

        try:
            await validate_public_url_async(
                self.graphql_url,
                allowed_schemes=("https",),
                allowed_domains=("myshopify.com",),
                require_dns=self._transport is None and egress_dns_validation_required(),
            )
        except EgressValidationError as exc:
            raise ValueError("unsafe Shopify Admin API endpoint") from exc
        transport = self._transport or build_pinned_async_transport(require_dns=egress_dns_validation_required())
        async with httpx.AsyncClient(timeout=self._timeout, transport=transport, follow_redirects=False) as client:
            response = await client.post(
                self.graphql_url,
                json={"query": query, "variables": dict(variables)},
                headers={
                    "Content-Type": "application/json",
                    "X-Shopify-Access-Token": self._credentials.admin_access_token,
                },
            )
            response.raise_for_status()
            body = response.json()
        if not isinstance(body, Mapping) or body.get("errors"):
            raise ValueError("Shopify GraphQL returned errors")
        data = body.get("data")
        return dict(data) if isinstance(data, Mapping) else {}

    async def customer_loyalty_metafields(self, *, email: str, namespace: str) -> dict[str, str] | None:
        safe_email = email.replace('"', "").replace("\\", "").strip()
        data = await self._query(CUSTOMER_METAFIELDS_QUERY, {"query": f'email:"{safe_email}"', "namespace": namespace})
        nodes = ((data.get("customers") or {}).get("nodes")) or []
        if not nodes:
            return None
        fields = ((nodes[0].get("metafields") or {}).get("nodes")) or []
        return {str(f.get("key")): str(f.get("value")) for f in fields if isinstance(f, Mapping) and f.get("key")}

    async def delivery_rates(self) -> list[DeliveryRate]:
        now = time.monotonic()
        cached = _delivery_cache.get(self._cache_key)
        if cached is not None and cached[0] > now:
            return [DeliveryRate(**item) for item in cached[1]]
        data = await self._query(DELIVERY_PROFILES_QUERY, {})
        rates = normalize_delivery_profiles(data)
        _delivery_cache[self._cache_key] = (now + DELIVERY_PROFILE_CACHE_SECONDS, [rate.__dict__ for rate in rates])
        return rates


def normalize_delivery_profiles(data: Mapping[str, Any]) -> list[DeliveryRate]:
    """Flat-rate method definitions per zone from a ``deliveryProfiles`` response (inactive ones dropped)."""
    rates: list[DeliveryRate] = []
    seen: set[str] = set()
    for profile in ((data.get("deliveryProfiles") or {}).get("nodes")) or []:
        groups = profile.get("profileLocationGroups") or []
        for group in groups if isinstance(groups, list) else [groups]:
            zones = ((group or {}).get("locationGroupZones") or {}).get("nodes") or []
            for zone_entry in zones:
                zone = zone_entry.get("zone") or {}
                countries: list[str] = []
                rest_of_world = False
                for country in zone.get("countries") or []:
                    code = (country or {}).get("code") or {}
                    if code.get("restOfWorld"):
                        rest_of_world = True
                    elif code.get("countryCode"):
                        countries.append(str(code["countryCode"]).upper())
                for method in ((zone_entry.get("methodDefinitions") or {}).get("nodes")) or []:
                    if not method or method.get("active") is False:
                        continue
                    price = ((method.get("rateProvider") or {}).get("price")) or {}
                    currency = str(price.get("currencyCode") or "").upper()
                    amount = amount_minor_units(price.get("amount"), currency, 1) if currency else None
                    gid = str(method.get("id") or "")
                    if amount is None or not gid or gid in seen:
                        continue
                    seen.add(gid)
                    rates.append(
                        DeliveryRate(
                            id="ship-" + gid.rsplit("/", 1)[-1],
                            title=str(method.get("name") or "Shipping"),
                            description=str(method.get("description")) if method.get("description") else None,
                            amount_minor_units=amount,
                            currency=currency,
                            countries=tuple(sorted(set(countries))),
                            rest_of_world=rest_of_world,
                        )
                    )
    return rates


def clear_delivery_cache() -> None:
    _delivery_cache.clear()
