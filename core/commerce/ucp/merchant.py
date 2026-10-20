# SPDX-License-Identifier: Apache-2.0
"""The merchant a UCP request is for: Host resolution and the merchant's UCP settings.

UCP endpoints are served at ``https://<merchant serving host>/ucp``; the
merchant is chosen by the request's ``Host`` header, never by a path alias.
``PublishedHostResolver`` uses the profile publisher's host index: the host
must be the merchant's attested serving host, the merchant must be published
(an active signing key, a live ``public_discovery`` artifact), exactly as for
``/.well-known/ucp``, and the merchant must belong to the tenant of the
platform's credential. Anything else is unknown (404).

Merchant transaction settings live in the merchant commerce config under
``provider_policy.ucp`` (a free-form mapping the config normaliser keeps):

``links``
    ``[{type, url, title?}]``; UCP requires ``privacy_policy`` and
    ``terms_of_service`` links for buyer-facing checkouts.
``fulfillment``
    ``{shipping: bool, pickup: bool, multi_destination: [method...],
    method_combinations: [[method...]...], free_shipping_min_subtotal_minor_units?}``.
``loyalty``
    ``{claim, name, metafield_namespace, reward_currency: {name, code, decimal_places},
    earn_points_per_major_unit?}``; memberships are read from Shopify customer metafields.
``split_payments``
    ``{allowed_combinations: [[{types, min?, max?}...]...]}`` (2026-08-25 only).
``payment_terms``
    ``[{id, title, schedules: [{id, type, description, amount?, due_in_days?}]}]`` (2026-08-25 only).
``payment_authentication``
    ``{allowed_origins: [https origins]}`` (2026-08-25 only).
``mandate_capability``
    ``{rails: [...], max_amount_minor_units, currency, instrument_types: [...]}``:
    what the merchant's payment rail accepts for agent-initiated payments.
``executor``
    The registered executor that completes this merchant's sessions (none by default).
``currency``
    The ISO 4217 currency for items whose source carries none.
``quantity_units``
    ``{item_id: {unit, scale?, display_text, increment?}}``: the sale basis of
    items not sold each (2026-08-25 quantity model).
``permalink_endpoint``
    An https browser endpoint the merchant operates that implements the UCP
    permalink binding; cart and escalation ``continue_url`` values are built on it.
``checkout_ttl_seconds``
    Session lifetime (default 6 hours, the UCP default).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_CHECKOUT_TTL_SECONDS = 6 * 60 * 60
_HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$")


def normalize_host(value: str | None) -> str | None:
    """Lower-case host name without port or trailing dot; None unless it is a multi-label DNS name."""
    if not isinstance(value, str):
        return None
    host = value.strip().lower()
    if host.startswith("["):
        return None
    host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    host = host.rstrip(".")
    return host if _HOST_RE.match(host) else None


@dataclass(frozen=True)
class FulfillmentSettings:
    shipping: bool = True
    pickup: bool = False
    multi_destination: tuple[str, ...] = ()
    method_combinations: tuple[tuple[str, ...], ...] = ()
    free_shipping_min_subtotal_minor_units: int | None = None


@dataclass(frozen=True)
class LoyaltySettings:
    claim: str
    name: str
    metafield_namespace: str
    reward_currency: dict[str, Any]
    earn_points_per_major_unit: int | None = None


@dataclass(frozen=True)
class MandateCapability:
    rails: tuple[str, ...]
    max_amount_minor_units: int | None
    currency: str | None
    instrument_types: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rails": list(self.rails),
            "max_amount_minor_units": self.max_amount_minor_units,
            "currency": self.currency,
            "instrument_types": list(self.instrument_types),
            "evidence": "merchant_config",
        }


@dataclass(frozen=True)
class MerchantUcp:
    """One merchant's UCP context, resolved from its commerce config."""

    tenant_id: str
    merchant_id: str
    seller_agent_id: str
    serving_host: str
    display_name: str
    links: tuple[dict[str, Any], ...] = ()
    fulfillment: FulfillmentSettings = field(default_factory=FulfillmentSettings)
    pickup_locations: tuple[dict[str, Any], ...] = ()
    loyalty: LoyaltySettings | None = None
    split_payments: dict[str, Any] | None = None
    payment_terms: tuple[dict[str, Any], ...] = ()
    payment_authentication: dict[str, Any] | None = None
    mandate_capability: MandateCapability | None = None
    payment_providers: tuple[dict[str, Any], ...] = ()
    executor: str | None = None
    currency: str | None = None
    quantity_units: dict[str, dict[str, Any]] = field(default_factory=dict)
    permalink_endpoint: str | None = None
    checkout_ttl_seconds: int = DEFAULT_CHECKOUT_TTL_SECONDS
    # The attested reverse-DNS namespace and whether an active merchant signing key exists.
    namespace: str | None = None
    signing_available: bool = False
    raw_config: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def base_url(self) -> str:
        return f"https://{self.serving_host}"

    @property
    def endpoint(self) -> str:
        """The UCP REST endpoint advertised in ``services[]`` (no trailing slash)."""
        return f"{self.base_url}/ucp"

    @property
    def profile_url(self) -> str:
        return f"{self.base_url}/.well-known/ucp"


def merchant_ucp_from_config(
    config: Mapping[str, Any],
    *,
    tenant_id: str,
    serving_host: str | None,
    namespace: str | None = None,
    signing_available: bool = False,
) -> MerchantUcp | None:
    """The merchant's UCP context on ``serving_host``, or None when the host is not a valid DNS name."""
    policy = config.get("provider_policy") if isinstance(config.get("provider_policy"), Mapping) else {}
    raw_ucp = policy.get("ucp") if isinstance(policy, Mapping) else None
    ucp: Mapping[str, Any] = raw_ucp if isinstance(raw_ucp, Mapping) else {}
    host = normalize_host(serving_host)
    if host is None:
        return None
    stores = [
        store
        for store in (config.get("offline_pos_stores") or [])
        if isinstance(store, Mapping) and store.get("enabled") is True and store.get("store_id")
    ]
    fulfillment_raw = ucp.get("fulfillment") if isinstance(ucp.get("fulfillment"), Mapping) else {}
    fulfillment = FulfillmentSettings(
        shipping=fulfillment_raw.get("shipping", True) is not False,
        pickup=bool(fulfillment_raw.get("pickup", False)) and bool(stores),
        multi_destination=tuple(str(m) for m in _seq(fulfillment_raw.get("multi_destination")) if m),
        method_combinations=tuple(
            tuple(str(m) for m in combo if m)
            for combo in _seq(fulfillment_raw.get("method_combinations"))
            if isinstance(combo, Sequence) and not isinstance(combo, str)
        ),
        free_shipping_min_subtotal_minor_units=_int(fulfillment_raw.get("free_shipping_min_subtotal_minor_units")),
    )
    loyalty_raw = ucp.get("loyalty") if isinstance(ucp.get("loyalty"), Mapping) else None
    loyalty = None
    if loyalty_raw and loyalty_raw.get("claim") and loyalty_raw.get("name"):
        currency = loyalty_raw.get("reward_currency") if isinstance(loyalty_raw.get("reward_currency"), Mapping) else {}
        loyalty = LoyaltySettings(
            claim=str(loyalty_raw["claim"]),
            name=str(loyalty_raw["name"]),
            metafield_namespace=str(loyalty_raw.get("metafield_namespace") or "loyalty"),
            reward_currency={
                "name": str(currency.get("name") or "Points"),
                "code": str(currency.get("code") or "PTS"),
                "decimal_places": _int(currency.get("decimal_places")) or 0,
            },
            earn_points_per_major_unit=_int(loyalty_raw.get("earn_points_per_major_unit")),
        )
    mandate_raw = ucp.get("mandate_capability") if isinstance(ucp.get("mandate_capability"), Mapping) else None
    mandate = None
    if mandate_raw:
        mandate = MandateCapability(
            rails=tuple(str(r) for r in _seq(mandate_raw.get("rails")) if r),
            max_amount_minor_units=_int(mandate_raw.get("max_amount_minor_units")),
            currency=str(mandate_raw.get("currency") or "").upper() or None,
            instrument_types=tuple(str(t) for t in _seq(mandate_raw.get("instrument_types")) if t),
        )
    links = tuple(
        {k: str(v) for k, v in link.items() if k in {"type", "url", "title"} and v}
        for link in _seq(ucp.get("links"))
        if isinstance(link, Mapping) and link.get("type") and str(link.get("url") or "").startswith("https://")
    )
    terms = tuple(term for term in _seq(ucp.get("payment_terms")) if isinstance(term, Mapping) and term.get("id"))
    return MerchantUcp(
        tenant_id=tenant_id,
        merchant_id=str(config.get("merchant_id") or ""),
        seller_agent_id=str(config.get("seller_agent_id") or "default"),
        serving_host=host,
        display_name=str(config.get("merchant_display_name") or config.get("merchant_id") or ""),
        links=links,
        fulfillment=fulfillment,
        pickup_locations=tuple(
            {
                "id": str(store["store_id"]),
                "name": str(store.get("display_name") or store["store_id"]),
                "address": {
                    k: v
                    for k, v in {
                        "address_locality": store.get("city") or None,
                        "address_country": store.get("country_code") or None,
                    }.items()
                    if v
                },
            }
            for store in stores
        ),
        loyalty=loyalty,
        split_payments=dict(ucp["split_payments"]) if isinstance(ucp.get("split_payments"), Mapping) else None,
        payment_terms=tuple(dict(term) for term in terms),
        payment_authentication=(
            dict(ucp["payment_authentication"]) if isinstance(ucp.get("payment_authentication"), Mapping) else None
        ),
        mandate_capability=mandate,
        payment_providers=tuple(dict(p) for p in (config.get("payment_providers") or []) if isinstance(p, Mapping)),
        executor=str(ucp.get("executor")) if ucp.get("executor") else None,
        currency=str(ucp.get("currency") or "").upper()[:3] or None,
        quantity_units={
            str(k): dict(val) for k, val in (ucp.get("quantity_units") or {}).items() if isinstance(val, Mapping)
        }
        if isinstance(ucp.get("quantity_units"), Mapping)
        else {},
        permalink_endpoint=_permalink_endpoint(ucp.get("permalink_endpoint")),
        checkout_ttl_seconds=max(60, min(_int(ucp.get("checkout_ttl_seconds")) or DEFAULT_CHECKOUT_TTL_SECONDS, 86400)),
        namespace=namespace,
        signing_available=signing_available,
        raw_config=dict(config),
    )


def config_from_row(row: Any) -> dict[str, Any]:
    """The parts of a merchant commerce config row the UCP transaction settings read."""
    return {
        "merchant_id": getattr(row, "merchant_id", None),
        "seller_agent_id": getattr(row, "seller_agent_id", None),
        "merchant_display_name": getattr(row, "merchant_display_name", None),
        "offline_pos_stores": list(getattr(row, "offline_pos_stores", None) or []),
        "payment_providers": list(getattr(row, "payment_providers", None) or []),
        "provider_policy": dict(getattr(row, "provider_policy", None) or {}),
        "public_publishing": dict(getattr(row, "public_publishing", None) or {}),
    }


_PERMALINK_ENDPOINT = re.compile(r"^https://[^/?#\s\\@]+(?:/[^?#\s\\]*[^/?#\s\\])?$")


def _permalink_endpoint(value: Any) -> str | None:
    """The permalink ``config.endpoint`` pattern: https, no query or fragment, no trailing slash."""
    return value if isinstance(value, str) and _PERMALINK_ENDPOINT.match(value) else None


def _seq(value: Any) -> list[Any]:
    return list(value) if isinstance(value, Sequence) and not isinstance(value, str | bytes) else []


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


class ServingHostResolver(Protocol):
    """Maps a request ``Host`` to the merchant it serves, inside the authenticated tenant."""

    async def resolve(self, *, host: str, tenant_id: str) -> MerchantUcp | None: ...


class PublishedHostResolver:
    """Host -> merchant through the profile publisher's host index; the merchant must be served on that host."""

    async def resolve(self, *, host: str, tenant_id: str) -> MerchantUcp | None:
        from datetime import UTC, datetime

        from core.commerce.ucp.authority import normalize_host as attested_form
        from core.commerce.ucp.catalog import usable_artifact_ids
        from core.commerce.ucp.profile import merchant_serve_state
        from core.commerce.ucp.store import find_serving_binding, load_merchant_config_row, load_shared_cache_records

        wanted = attested_form(host)
        if wanted is None:
            return None
        binding = await find_serving_binding(wanted)
        if binding is None or binding.tenant_id != str(tenant_id):
            return None
        row = await load_merchant_config_row(
            tenant_id=binding.tenant_id, merchant_id=binding.merchant_id, seller_agent_id=binding.seller_agent_id
        )
        if row is None or str(row.tenant_id) != binding.tenant_id:
            return None
        now = datetime.now(UTC)
        records = await load_shared_cache_records(
            tenant_id=binding.tenant_id, merchant_id=binding.merchant_id, seller_agent_id=binding.seller_agent_id
        )
        live = usable_artifact_ids(
            cache_records=records, now_iso=now.isoformat().replace("+00:00", "Z"), artifact_type="public_discovery"
        )
        serve = merchant_serve_state(row, host=wanted, now=now, live_public_discovery_ids=live)
        if serve is None:
            return None
        return merchant_ucp_from_config(
            config_from_row(row),
            tenant_id=str(tenant_id),
            serving_host=wanted,
            namespace=serve.namespace,
            signing_available=True,
        )
