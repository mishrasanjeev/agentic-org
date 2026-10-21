# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The ACP product feed: ACP Feed ``Product`` JSON and the merchant-feed CSV (G33).

Two formats, both generated only from products the UCP catalog would serve: the
merchant's synced connector evidence, gated on verified (Phase 1 ES256), fresh,
buyer-agnostic OACP ``merchant_capability``, ``seller_agent_capability``,
``catalog_snapshot``, ``price`` and ``inventory`` artifacts
(``core.commerce.ucp.catalog.verified_catalog_source``). Without them there is no
feed (never a stale one).

* **ACP Feed JSON** (``schema.feed.json`` of ACP 2026-04-17): ``ProductsResponse``
  ``{products: [Product]}``, ``products.jsonl`` (one ``Product`` per line) and
  ``FeedMetadata``. A variant carries ``price`` / ``list_price`` (minor units),
  ``availability`` (``available`` and ``status``), ``variant_options``,
  ``categories``, ``media`` and ``seller`` (``name`` and ``links``:
  ``privacy_policy``, ``terms_of_service``, ``refund_policy``,
  ``shipping_policy``). The schema has no member for search or checkout
  eligibility or for return terms (``additionalProperties: false``); returns are
  the ``refund_policy`` link.
* **Merchant-feed CSV**: the flat field list of the merchant product-feed
  specification the ACP digest identifies (not part of the ACP repository): one row
  per variant, a header row, UTF-8, RFC 4180 quoting, booleans ``true``/``false``,
  ``price`` as ``"25.00 INR"``. Required columns are ``item_id``, ``title``,
  ``description``, ``url``, ``brand``, ``seller_name``, ``image_url``,
  ``availability`` and ``price``; a variant missing one is left out and counted.
  ``enable_search`` is the merchant's choice (default true); ``enable_checkout`` is
  true only when search is on, ``AGENTICORG_ACP_CHECKOUT_ENABLED`` is on, the
  merchant opted in to ACP checkout and both ``seller_privacy_policy`` and
  ``seller_tos`` are known. Return columns come from the verified OACP ``policy``
  artifact only. Free-text cells that start with ``=``, ``+``, ``-``, ``@``, tab
  or carriage return are prefixed with ``'`` (spreadsheet formula injection).

Item ids are the ids checkout accepts: ``Variant.id`` / ``item_id`` is the UCP
variant id that ACP ``line_items[].id`` names.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from core.commerce.ucp.catalog import _CURRENCY_EXPONENTS, ucp_product, verified_catalog_source

CSV_COLUMNS: tuple[str, ...] = (
    "item_id",
    "group_id",
    "title",
    "description",
    "url",
    "brand",
    "image_url",
    "price",
    "sale_price",
    "availability",
    "product_category",
    "seller_name",
    "seller_url",
    "seller_privacy_policy",
    "seller_tos",
    "accepts_returns",
    "return_deadline_in_days",
    "return_policy",
    "enable_search",
    "enable_checkout",
)
REQUIRED_CSV_COLUMNS: tuple[str, ...] = (
    "item_id",
    "title",
    "description",
    "url",
    "brand",
    "seller_name",
    "image_url",
    "availability",
    "price",
)
_TEXT_COLUMNS = frozenset({"title", "description", "brand", "seller_name", "product_category"})
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
TITLE_LIMIT = 150
DESCRIPTION_LIMIT = 5000


class FeedUnavailableError(Exception):
    """The verified artifacts the feed needs are missing or stale; no feed is produced."""


@dataclass(frozen=True)
class FeedSeller:
    """What the feed says about the merchant (display name, policy links, opt-ins), from its config."""

    name: str
    url: str | None = None
    privacy_policy: str | None = None
    terms_of_service: str | None = None
    shipping_policy: str | None = None
    refund_policy: str | None = None
    accepts_returns: bool | None = None
    return_deadline_in_days: int | None = None
    enable_search: bool = True
    checkout_available: bool = False
    product_url_template: str | None = None
    permalink_endpoint: str | None = None
    target_country: str | None = None


@dataclass
class Feed:
    feed_id: str
    products: list[dict[str, Any]]
    csv_rows: list[dict[str, str]]
    skipped_rows: int
    artifact_ids: tuple[str, ...]
    updated_at: str
    target_country: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def products_response(self) -> dict[str, Any]:
        return {"products": self.products}

    def products_jsonl(self) -> str:
        return "".join(json.dumps(p, separators=(",", ":"), ensure_ascii=False) + "\n" for p in self.products)

    def feed_metadata(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.feed_id, "updated_at": self.updated_at}
        if self.target_country:
            out["target_country"] = self.target_country
        return out

    def csv_text(self) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(CSV_COLUMNS), lineterminator="\r\n")
        writer.writeheader()
        for row in self.csv_rows:
            writer.writerow(row)
        return buffer.getvalue()


def _https(value: Any) -> str | None:
    return value if isinstance(value, str) and value.startswith("https://") and "@" not in value.split("/")[2] else None


def seller_from_config(
    config: Mapping[str, Any],
    *,
    serving_host: str | None,
    verified_policy: Mapping[str, Any] | None,
    checkout_flag: bool,
) -> FeedSeller:
    """The feed's seller facts from a merchant commerce config and its verified policy payload."""
    policy = config.get("provider_policy") if isinstance(config.get("provider_policy"), Mapping) else {}
    ucp = policy.get("ucp") if isinstance(policy.get("ucp"), Mapping) else {}
    acp = policy.get("acp") if isinstance(policy.get("acp"), Mapping) else {}
    feed = acp.get("feed") if isinstance(acp.get("feed"), Mapping) else {}
    links = {
        str(link.get("type")): _https(link.get("url")) for link in ucp.get("links") or [] if isinstance(link, Mapping)
    }
    payload = verified_policy or {}
    refund = _https(payload.get("return_policy_url")) or links.get("refund_policy") or links.get("return_policy")
    accepts = payload.get("accepts_returns") if isinstance(payload.get("accepts_returns"), bool) else None
    days = payload.get("return_window_days")
    template = feed.get("product_url_template")
    country = str(feed.get("target_country") or "")
    return FeedSeller(
        name=str(config.get("merchant_display_name") or config.get("merchant_id") or ""),
        url=f"https://{serving_host}" if serving_host else None,
        privacy_policy=links.get("privacy_policy"),
        terms_of_service=links.get("terms_of_service") or links.get("terms_of_use"),
        shipping_policy=links.get("shipping_policy"),
        refund_policy=refund,
        accepts_returns=accepts,
        return_deadline_in_days=days if accepts and isinstance(days, int) and not isinstance(days, bool) else None,
        enable_search=feed.get("enable_search", True) is not False,
        checkout_available=checkout_flag and acp.get("checkout_enabled") is True,
        product_url_template=template if _https(template) and "{item_id}" in str(template) else None,
        permalink_endpoint=_https(ucp.get("permalink_endpoint")),
        target_country=country if _COUNTRY.match(country) else None,
    )


def _seller_object(seller: FeedSeller) -> dict[str, Any]:
    links = [
        {"type": kind, "url": url}
        for kind, url in (
            ("privacy_policy", seller.privacy_policy),
            ("terms_of_service", seller.terms_of_service),
            ("refund_policy", seller.refund_policy),
            ("shipping_policy", seller.shipping_policy),
        )
        if url
    ]
    out: dict[str, Any] = {"name": seller.name}
    if links:
        out["links"] = links
    return out


def _variant_url(seller: FeedSeller, item_id: str) -> str | None:
    from urllib.parse import quote

    if seller.product_url_template:
        return seller.product_url_template.replace("{item_id}", quote(item_id, safe=""))
    if seller.permalink_endpoint:
        token = item_id if re.match(r"^[A-Za-z0-9._-]+$", item_id) else None
        if token is None:
            import base64

            token = "~" + base64.urlsafe_b64encode(item_id.encode()).decode().rstrip("=")
        return f"{seller.permalink_endpoint}/{token}:1"
    return None


def acp_product(product: Mapping[str, Any], seller: FeedSeller) -> dict[str, Any] | None:
    """One synced product as an ACP Feed ``Product``; None when the UCP catalog would not list it."""
    rendered = ucp_product(product)
    if rendered is None:
        return None
    seller_object = _seller_object(seller)
    variants = []
    for item in rendered["variants"]:
        variant: dict[str, Any] = {
            "id": item["id"],
            "title": item["title"],
            "description": {"plain": item["description"]["plain"]},
            "price": dict(item["price"]),
        }
        url = _variant_url(seller, item["id"])
        if url:
            variant["url"] = url
        if item.get("list_price"):
            variant["list_price"] = dict(item["list_price"])
        if item.get("availability"):
            variant["availability"] = dict(item["availability"])
        if rendered.get("categories"):
            variant["categories"] = [dict(c) for c in rendered["categories"]]
        if item.get("options"):
            variant["variant_options"] = [{"name": o["name"], "value": o["label"]} for o in item["options"]]
        if rendered.get("media"):
            variant["media"] = [dict(m) for m in rendered["media"][:1]]
        variant["seller"] = seller_object
        variants.append(variant)
    out: dict[str, Any] = {
        "id": rendered["id"],
        "title": rendered["title"],
        "description": {"plain": rendered["description"]["plain"]},
        "variants": variants,
    }
    if rendered.get("media"):
        out["media"] = [dict(m) for m in rendered["media"]]
    return out


def _major_units(amount: int, currency: str) -> str:
    exponent = _CURRENCY_EXPONENTS.get(currency, 2)
    value = Decimal(amount).scaleb(-exponent)
    return f"{value:.{exponent}f} {currency}"


def _cell(column: str, value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if column in _TEXT_COLUMNS and text.startswith(_FORMULA_START):
        return "'" + text
    return text


def csv_rows(
    product: Mapping[str, Any], raw: Mapping[str, Any], seller: FeedSeller
) -> tuple[list[dict[str, str]], int]:
    """Merchant-feed rows for one ACP product (one per variant) and the number left out."""
    rows: list[dict[str, str]] = []
    skipped = 0
    enable_checkout = (
        seller.enable_search and seller.checkout_available and bool(seller.privacy_policy and seller.terms_of_service)
    )
    image = next((m["url"] for m in product.get("media") or [] if m.get("type") == "image"), None)
    brand = str(raw.get("vendor") or "").strip() or seller.name
    for variant in product["variants"]:
        price = variant["price"]
        list_price = variant.get("list_price")
        availability = (variant.get("availability") or {}).get("status")
        row: dict[str, Any] = {
            "item_id": variant["id"],
            "group_id": product["id"],
            "title": (variant["title"] if variant["title"] != "Default" else product["title"])[:TITLE_LIMIT],
            "description": variant["description"]["plain"][:DESCRIPTION_LIMIT],
            "url": variant.get("url"),
            "brand": brand,
            "image_url": ((variant.get("media") or [{}])[0]).get("url") or image,
            "price": _major_units(list_price["amount"], list_price["currency"])
            if list_price
            else _major_units(price["amount"], price["currency"]),
            "sale_price": _major_units(price["amount"], price["currency"]) if list_price else None,
            "availability": availability if availability in ("in_stock", "out_of_stock") else "unknown",
            "product_category": (variant.get("categories") or [{}])[0].get("value"),
            "seller_name": seller.name,
            "seller_url": seller.url,
            "seller_privacy_policy": seller.privacy_policy,
            "seller_tos": seller.terms_of_service,
            "accepts_returns": seller.accepts_returns,
            "return_deadline_in_days": seller.return_deadline_in_days,
            "return_policy": seller.refund_policy,
            "enable_search": seller.enable_search,
            "enable_checkout": enable_checkout,
        }
        if any(not row.get(column) for column in REQUIRED_CSV_COLUMNS):
            skipped += 1
            continue
        rows.append({column: _cell(column, row.get(column)) for column in CSV_COLUMNS})
    return rows, skipped


def build_feed(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
    cache_records: Sequence[Any],
    products: Sequence[Mapping[str, Any]],
    seller: FeedSeller,
    now_iso: str,
) -> Feed:
    """The feed from verified, fresh artifacts; ``FeedUnavailableError`` (with the reason) otherwise."""
    source = verified_catalog_source(cache_records=cache_records, products=products, now_iso=now_iso)
    if source.unavailable_reason is not None:
        raise FeedUnavailableError(source.unavailable_reason)
    items: list[dict[str, Any]] = []
    rows: list[dict[str, str]] = []
    skipped = 0
    for raw in source.products:
        product = acp_product(raw, seller)
        if product is None:
            continue
        items.append(product)
        product_rows, product_skipped = csv_rows(product, raw, seller)
        rows.extend(product_rows)
        skipped += product_skipped
    digest = hashlib.sha256(f"{tenant_id}:{merchant_id}:{seller_agent_id}".encode()).hexdigest()[:20]
    return Feed(
        feed_id=f"feed_{digest}",
        products=items,
        csv_rows=rows,
        skipped_rows=skipped,
        artifact_ids=source.artifact_ids,
        updated_at=now_iso,
        target_country=seller.target_country,
    )
