# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Load a network shop's storefront (``core.commerce.shop_site``) from the database.

The products are read exactly as ``/ucp/catalog/search`` reads them on the same host: the newest
connector-evidence snapshot of every catalog source (``core.commerce.ucp.store.load_catalog_inputs``),
the shop's source precedence, and the verified-artifact gate (``verified_catalog_source``), after
one on-demand refresh of the short-lived catalog artifacts. While the gate is closed the site is
``catalog_verified=False`` with no products, and the routes answer 503 so a crawler keeps what it
already holds instead of dropping the shop's items.

Built sites are cached in-process for a minute per host.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from core.commerce import network as net
from core.commerce.catalog_upload import shop_details_of
from core.commerce.network_registrations import FeedSummary
from core.commerce.shop_feed import build_feed
from core.commerce.shop_site import ShopSite, clean, feed_url, site_products
from core.commerce.source_precedence import products_with_precedence
from core.commerce.ucp.catalog import ucp_product, verified_catalog_source
from core.commerce.ucp.flat_rates import configured_delivery_rates
from core.commerce.ucp.store import load_catalog_inputs, load_merchant_config_row

SITE_TTL_SECONDS = 60.0
# enterprise-gate: process-local-ok reason=storefront cache expires on its ttl; the database is the source of truth
_SITE_CACHE: dict[str, tuple[float, ShopSite]] = {}


def reset_site_cache() -> None:
    _SITE_CACHE.clear()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def build_site(
    *,
    slug: str,
    name: str,
    host: str,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
    config: Any,
) -> ShopSite:
    """The storefront for one shop scope on ``host`` (reads its catalog inputs; no cache)."""
    from core.commerce.artifact_freshness import CATALOG_TYPES, ensure_fresh  # noqa: PLC0415

    records, products = await load_catalog_inputs(
        tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id
    )
    if await ensure_fresh(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        records=records,
        required=CATALOG_TYPES,
    ):
        records, products = await load_catalog_inputs(
            tenant_id=tenant_id, merchant_id=merchant_id, seller_agent_id=seller_agent_id
        )
    source = verified_catalog_source(cache_records=records, products=products, now_iso=_now_iso())
    policy: Mapping[str, Any] = (getattr(config, "provider_policy", None) or {}) if config is not None else {}
    stored = policy.get("merchant_policies") if isinstance(policy.get("merchant_policies"), Mapping) else {}
    categories = getattr(config, "commerce_categories", None) if config is not None else None
    site_items = ()
    if source.unavailable_reason is None:
        site_items = site_products(
            [
                item
                for product in products_with_precedence(source.products, policy)
                if (item := ucp_product(product)) is not None
            ]
        )
    return ShopSite(
        slug=slug,
        name=clean(name, 200) or slug,
        host=host,
        catalog_verified=source.unavailable_reason is None,
        products=site_items,
        rates=tuple(configured_delivery_rates({"provider_policy": dict(policy)})),
        returns_policy=clean(stored.get("returns"), 2000) or None,
        shipping_policy=clean(stored.get("shipping"), 2000) or None,
        pickup_policy=clean(stored.get("pickup"), 2000) or None,
        shop_details=tuple((d["name"], d["value"]) for d in shop_details_of(policy)),
        telegram_link=net.telegram_link(slug),
        categories=tuple(clean(c, 120) for c in categories or () if isinstance(c, str))[:10],
    )


async def load_site(host: str) -> ShopSite | None:
    """The storefront of the listed network shop served on ``host``, or None (not a shop host, network off)."""
    shop = await net.shop_for_host(host)
    if shop is None:
        return None
    now = time.monotonic()
    cached = _SITE_CACHE.get(host)
    if cached is not None and cached[0] > now and cached[1].slug == shop.slug:
        return cached[1]
    config = await load_merchant_config_row(
        tenant_id=shop.tenant_id, merchant_id=shop.merchant_id, seller_agent_id=shop.seller_agent_id
    )
    site = await build_site(
        slug=shop.slug,
        name=shop.display_name,
        host=host,
        tenant_id=shop.tenant_id,
        merchant_id=shop.merchant_id,
        seller_agent_id=shop.seller_agent_id,
        config=config,
    )
    _SITE_CACHE[host] = (now + SITE_TTL_SECONDS, site)
    return site


def feed_summary_of(site: ShopSite) -> FeedSummary:
    return FeedSummary(
        catalog_verified=site.catalog_verified,
        eligible_items=len(build_feed(site).items) if site.catalog_verified else 0,
        feed_url=feed_url(site),
    )


async def feed_summary(
    *, slug: str, name: str, host: str, tenant_id: str, merchant_id: str, seller_agent_id: str, config: Any
) -> FeedSummary:
    """What the shop's feed on ``host`` holds now, for the Sell everywhere page (uses the site cache)."""
    now = time.monotonic()
    cached = _SITE_CACHE.get(host)
    if cached is not None and cached[0] > now and cached[1].slug == slug:
        return feed_summary_of(cached[1])
    site = await build_site(
        slug=slug,
        name=name,
        host=host,
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        config=config,
    )
    _SITE_CACHE[host] = (now + SITE_TTL_SECONDS, site)
    return feed_summary_of(site)
