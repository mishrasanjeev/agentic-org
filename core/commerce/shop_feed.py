# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""A network shop's product feed: RSS 2.0 with the Google Merchant Center ``g:`` namespace.

Served at ``https://<shop host>/feeds/google.xml``. Google Merchant Center reads it as a scheduled
fetch data source, and Microsoft Merchant Center accepts the same Google feed file, so one feed
serves both (docs/runbooks/network-registrations.md cites the specifications read).

One ``<item>`` per sellable variant, with ``g:item_group_id`` when a product has several:

* ``g:id`` - the variant's SKU when it is a plain code of at most 50 characters, else a stable
  24-character digest of the variant id; ``title`` (at most 150 characters), ``description``
  (at most 5000), ``link`` - the product page on the shop's own host (``core.commerce.shop_site``);
* ``g:image_link`` and up to ten ``g:additional_image_link``;
* ``g:availability`` ``in_stock`` or ``out_of_stock``; ``g:price`` ``449.00 INR`` (with
  ``g:sale_price`` when the catalog gives a higher compare-at price);
* ``g:brand`` when the catalog names one; ``g:gtin`` when the variant has a barcode with a valid
  check digit, else ``g:identifier_exists`` ``no`` (the catalog holds no manufacturer part number);
* ``g:condition`` (``new`` unless the product declares a ``condition`` detail of ``used`` or
  ``refurbished``) and ``g:product_type`` from the catalog category;
* ``g:shipping`` per country for each of the shop's own flat rates in the item's currency. A shop
  whose rates come from Shopify (read live at checkout) gets none here, so its shipping is set in
  the Merchant Center account instead.

A variant without a usable price (none, or zero), without a stock figure or without an image is
left out, with the count per reason in ``FeedBuild.excluded``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from core.commerce.shop_site import ShopSite, SiteProduct, SiteVariant, clean, feed_url, money, product_url

GOOGLE_NAMESPACE = "http://base.google.com/ns/1.0"
MAX_ID = 50
MAX_TITLE = 150
MAX_DESCRIPTION = 5000
MAX_BRAND = 70
MAX_ADDITIONAL_IMAGES = 10
_PLAIN_ID = re.compile(r"^[A-Za-z0-9._-]{1,50}$")
# Characters XML 1.0 does not allow at all.
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿]")

NO_PRICE = "no_price"
NO_STOCK_FIGURE = "no_stock_figure"
NO_IMAGE = "no_image"


@dataclass(frozen=True)
class FeedItem:
    fields: tuple[tuple[str, str], ...]
    shipping: tuple[tuple[tuple[str, str], ...], ...] = ()

    def get(self, name: str) -> str | None:
        return next((value for key, value in self.fields if key == name), None)


@dataclass(frozen=True)
class FeedBuild:
    items: tuple[FeedItem, ...]
    excluded: dict[str, int] = field(default_factory=dict)


def xml_text(value: Any) -> str:
    text = _XML_ILLEGAL.sub("", str(value))
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def item_id(variant: SiteVariant) -> str:
    if variant.sku and _PLAIN_ID.match(variant.sku):
        return variant.sku
    return hashlib.sha256(variant.id.encode()).hexdigest()[:24]


def _shipping(site: ShopSite, currency: str) -> tuple[tuple[tuple[str, str], ...], ...]:
    out = []
    for rate in site.rates:
        if rate.currency != currency:
            continue
        for country in rate.countries:
            out.append(
                (
                    ("g:country", country),
                    ("g:service", clean(rate.title, 100) or "Standard shipping"),
                    ("g:price", f"{money(rate.amount_minor_units, rate.currency)} {rate.currency}"),
                )
            )
    return tuple(out[:100])


def _item(site: ShopSite, product: SiteProduct, variant: SiteVariant) -> FeedItem:
    several = len(product.variants) > 1
    title = product.title if not several or variant.title == "Default" else f"{product.title} - {variant.title}"
    fields: list[tuple[str, str]] = [
        ("g:id", item_id(variant)),
        ("title", clean(title, MAX_TITLE)),
        ("description", clean(product.description, MAX_DESCRIPTION)),
        ("link", product_url(site, product, variant)),
        ("g:image_link", product.images[0][0]),
    ]
    fields += [("g:additional_image_link", url) for url, _alt in product.images[1 : 1 + MAX_ADDITIONAL_IMAGES]]
    fields.append(("g:availability", "in_stock" if variant.available else "out_of_stock"))
    if variant.list_amount is not None:
        fields.append(("g:price", f"{money(variant.list_amount, variant.currency)} {variant.currency}"))
        fields.append(("g:sale_price", f"{money(variant.amount, variant.currency)} {variant.currency}"))
    else:
        fields.append(("g:price", f"{money(variant.amount, variant.currency)} {variant.currency}"))
    if product.brand:
        fields.append(("g:brand", clean(product.brand, MAX_BRAND)))
    if variant.gtin:
        fields.append(("g:gtin", variant.gtin))
    else:
        fields.append(("g:identifier_exists", "no"))
    fields.append(("g:condition", product.condition))
    if product.category:
        fields.append(("g:product_type", clean(product.category, 750)))
    if several:
        fields.append(("g:item_group_id", product.handle[-10:]))
    return FeedItem(fields=tuple(fields), shipping=_shipping(site, variant.currency))


def build_feed(site: ShopSite) -> FeedBuild:
    """The feed items for every sellable variant, and the count of variants left out per reason."""
    items: list[FeedItem] = []
    excluded: dict[str, int] = {}
    seen: set[str] = set()
    for product in site.products:
        for variant in product.variants:
            reason = None
            if variant.amount <= 0:
                reason = NO_PRICE
            elif variant.available is None:
                reason = NO_STOCK_FIGURE
            elif not product.images:
                reason = NO_IMAGE
            if reason is not None:
                excluded[reason] = excluded.get(reason, 0) + 1
                continue
            item = _item(site, product, variant)
            identifier = item.get("g:id") or ""
            if identifier in seen:
                continue
            seen.add(identifier)
            items.append(item)
    return FeedBuild(items=tuple(items), excluded=excluded)


def render_google_feed(site: ShopSite, build: FeedBuild | None = None) -> str:
    feed = build or build_feed(site)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        f'<rss version="2.0" xmlns:g="{GOOGLE_NAMESPACE}">\n<channel>\n',
        f"<title>{xml_text(clean(site.name, 150))}</title>\n",
        f"<link>{xml_text(site.base_url)}/</link>\n",
        f"<description>{xml_text(clean('Products from ' + site.name, 5000))}</description>\n",
    ]
    for item in feed.items:
        parts.append("<item>\n")
        parts.extend(f"<{name}>{xml_text(value)}</{name}>\n" for name, value in item.fields if value)
        for shipping in item.shipping:
            inner = "".join(f"<{name}>{xml_text(value)}</{name}>" for name, value in shipping)
            parts.append(f"<g:shipping>{inner}</g:shipping>\n")
        parts.append("</item>\n")
    parts.append("</channel>\n</rss>\n")
    return "".join(parts)


__all__ = ["FeedBuild", "FeedItem", "build_feed", "feed_url", "item_id", "render_google_feed", "xml_text"]
