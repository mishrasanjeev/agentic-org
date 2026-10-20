# SPDX-License-Identifier: Apache-2.0
"""UCP catalog search and lookup (``dev.ucp.shopping.catalog.search`` / ``.lookup``).

Products come from the merchant's synced connector evidence and are served
only while the scope holds verified (Phase 1 ES256) and fresh OACP
``merchant_capability``, ``seller_agent_capability``, ``catalog_snapshot``,
``price`` and ``inventory`` artifacts. Without them the
response is the business outcome "catalog unavailable": HTTP 200 with no
products and an ``unrecoverable`` error message, never a stale listing.

Item shape: ``schemas/shopping/types/product.json`` and ``variant.json`` of the
targeted release (both require ``id``, ``title``, ``description``; products
``price_range`` and ``variants``; variants ``price``). Prices are integer
minor units of the ISO 4217 currency (``amount.json``), converted from the
connector's decimal text without binary floating point.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from core.commerce.c6z_runtime_vertical import (
    DEMO_SOURCE_SYSTEM,
    _redacted_ref,
    contains_private_or_executable_value,
    evaluate_runtime_cache_record,
)
from core.commerce.oacp_artifacts import OacpPersistentArtifactCacheRecord, is_verified_oacp_cache_record

# The artifact types (Grantex families merchant_profile, seller_agent_card, catalog_snapshot,
# offer_price_snapshot, inventory_snapshot) that back catalog search and lookup, as in the
# Grantex ucp-profile renderer.
REQUIRED_CATALOG_ARTIFACTS: tuple[str, ...] = (
    "merchant_capability",
    "seller_agent_capability",
    "catalog_snapshot",
    "price",
    "inventory",
)
MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 10
MAX_LOOKUP_IDS = 50
# ISO 4217 minor-unit exponents that are not 2.
_CURRENCY_EXPONENTS: dict[str, int] = {
    "BIF": 0, "CLP": 0, "DJF": 0, "GNF": 0, "ISK": 0, "JPY": 0, "KMF": 0, "KRW": 0, "PYG": 0,
    "RWF": 0, "UGX": 0, "UYI": 0, "VND": 0, "VUV": 0, "XAF": 0, "XOF": 0, "XPF": 0,
    "BHD": 3, "IQD": 3, "JOD": 3, "KWD": 3, "LYD": 3, "OMR": 3, "TND": 3,
    "CLF": 4, "UYW": 4,
}  # fmt: skip
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_EXCLUDED_PRODUCT_STATUSES = frozenset({"DRAFT", "ARCHIVED"})
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class VerifiedCatalogSource:
    """Products usable for a UCP catalog response, or the reason there are none."""

    products: tuple[Mapping[str, Any], ...]
    artifact_ids: tuple[str, ...]
    unavailable_reason: str | None


def _usable_shared_record(record: OacpPersistentArtifactCacheRecord, now_iso: str) -> bool:
    """Verified by the ES256 verifier, buyer-agnostic, fresh and unrevoked (strict, whatever the runtime)."""
    if record.buyer_agent_id is not None or not is_verified_oacp_cache_record(record):
        return False
    result = evaluate_runtime_cache_record(
        record=record,
        action_intent="non_binding_preview",
        now_iso=now_iso,
        grantex_available=False,
        expected_scope={
            "tenant_id": record.tenant_id,
            "merchant_id": record.merchant_id,
            "seller_agent_id": record.seller_agent_id,
            "buyer_agent_id": None,
        },
        strict=True,
    )
    return result.get("status") == "usable_for_non_binding_cache"


def verified_catalog_source(
    *,
    cache_records: Sequence[OacpPersistentArtifactCacheRecord],
    products: Sequence[Mapping[str, Any]],
    now_iso: str,
) -> VerifiedCatalogSource:
    """Gate the products on verified, fresh, buyer-agnostic catalog, price and inventory artifacts."""
    usable: dict[str, list[OacpPersistentArtifactCacheRecord]] = {}
    for record in cache_records:
        if _usable_shared_record(record, now_iso):
            usable.setdefault(record.artifact_type, []).append(record)
    missing = [artifact_type for artifact_type in REQUIRED_CATALOG_ARTIFACTS if artifact_type not in usable]
    if missing:
        return VerifiedCatalogSource((), (), "verified_artifacts_missing:" + ",".join(missing))
    artifact_ids = tuple(sorted({record.artifact_id for records in usable.values() for record in records}))
    return VerifiedCatalogSource(tuple(products), artifact_ids, None)


def usable_artifact_ids(
    *,
    cache_records: Sequence[OacpPersistentArtifactCacheRecord],
    now_iso: str,
    artifact_type: str,
) -> set[str]:
    """Ids of verified, fresh, buyer-agnostic records of one type (e.g. the ``policy`` the catalog may map)."""
    return {
        record.artifact_id
        for record in cache_records
        if record.artifact_type == artifact_type and _usable_shared_record(record, now_iso)
    }


def minor_units(price: Any, currency: str) -> int | None:
    """Decimal price text in major units -> integer minor units (ISO 4217 exponent), or None if unusable."""
    if price is None or isinstance(price, bool):
        return None
    try:
        value = Decimal(str(price).strip())
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value < 0:
        return None
    exponent = _CURRENCY_EXPONENTS.get(currency, 2)
    amount = (value * (Decimal(10) ** exponent)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    if amount > 2**53 - 1:
        return None
    return int(amount)


def ucp_product_id(product: Mapping[str, Any]) -> str | None:
    ref = product.get("product_ref")
    return ref if isinstance(ref, str) and ref else None


def ucp_variant_id(variant: Mapping[str, Any]) -> str | None:
    """The opaque UCP variant id (``item.id`` in cart and checkout) for a synced variant."""
    variant_id = variant.get("variant_id")
    if not isinstance(variant_id, str) or not variant_id:
        return None
    if variant_id.startswith(f"{DEMO_SOURCE_SYSTEM}:"):
        # The demo merchant's synthetic variants never carry a Shopify-looking id.
        return _redacted_ref("synthetic_demo_variant", variant_id)
    return _redacted_ref("shopify_variant", variant_id)


def find_catalog_variant(
    products: Sequence[Mapping[str, Any]],
    item_id: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """The synced ``(product, variant)`` whose UCP variant id is ``item_id`` (cart and checkout resolve items here)."""
    for product in products:
        for variant in product.get("variants") or ():
            if isinstance(variant, Mapping) and ucp_variant_id(variant) == item_id:
                return product, variant
    return None


def _plain(value: Any, limit: int = 4000) -> str | None:
    if not isinstance(value, str):
        return None
    text = _TAG_RE.sub(" ", value).replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] or None


def _https_media(value: Any) -> str | None:
    if not isinstance(value, str) or not value.startswith("https://") or contains_private_or_executable_value(value):
        return None
    return value


def _currency_for(variant: Mapping[str, Any], product: Mapping[str, Any]) -> str | None:
    for candidate in (variant.get("currency"), product.get("_evidence_currency")):
        if isinstance(candidate, str) and _CURRENCY_RE.fullmatch(candidate.strip().upper()):
            return candidate.strip().upper()
    return None


def _ucp_variant(variant: Mapping[str, Any], product: Mapping[str, Any]) -> dict[str, Any] | None:
    variant_id = ucp_variant_id(variant)
    currency = _currency_for(variant, product)
    if variant_id is None or currency is None:
        return None
    amount = minor_units(variant.get("price"), currency)
    if amount is None:
        return None
    title = _plain(variant.get("title"), 255) or "Default"
    product_title = _plain(product.get("title"), 255) or title
    item: dict[str, Any] = {"id": variant_id}
    sku = _plain(variant.get("sku"), 255)
    if sku:
        item["sku"] = sku
    item["title"] = title
    item["description"] = {"plain": product_title if title == "Default" else f"{product_title} - {title}"}
    item["price"] = {"amount": amount, "currency": currency}
    list_amount = minor_units(variant.get("compare_at_price"), currency)
    if list_amount is not None and list_amount > amount:
        item["list_price"] = {"amount": list_amount, "currency": currency}
    inventory = variant.get("inventory_quantity_snapshot")
    if isinstance(inventory, int) and not isinstance(inventory, bool):
        item["availability"] = {
            "available": inventory > 0,
            "status": "in_stock" if inventory > 0 else "out_of_stock",
        }
    options = [
        {"name": name, "label": label}
        for option in variant.get("selected_options") or ()
        if isinstance(option, Mapping)
        and (name := _plain(option.get("name"), 120))
        and (label := _plain(option.get("value"), 120))
    ]
    if options:
        item["options"] = options
    return item


def ucp_product(product: Mapping[str, Any]) -> dict[str, Any] | None:
    """One synced product as a UCP ``product``; None when it has no priceable variant or is not public."""
    product_id = ucp_product_id(product)
    status = str(product.get("status") or "").upper()
    if product_id is None or status in _EXCLUDED_PRODUCT_STATUSES:
        return None
    variants = [
        item
        for variant in product.get("variants") or ()
        if isinstance(variant, Mapping) and (item := _ucp_variant(variant, product)) is not None
    ]
    if not variants:
        return None
    currencies = {item["price"]["currency"] for item in variants}
    if len(currencies) != 1:
        return None
    currency = currencies.pop()
    amounts = [item["price"]["amount"] for item in variants]
    title = _plain(product.get("title"), 255) or "Untitled product"
    result: dict[str, Any] = {
        "id": product_id,
        "title": title,
        "description": {"plain": _plain(product.get("description")) or title},
        "price_range": {
            "min": {"amount": min(amounts), "currency": currency},
            "max": {"amount": max(amounts), "currency": currency},
        },
    }
    category = _plain(product.get("product_type"), 255)
    if category:
        result["categories"] = [{"value": category}]
    media = []
    for image in product.get("images") or ():
        if not isinstance(image, Mapping):
            continue
        url = _https_media(image.get("url"))
        if url is None:
            continue
        entry: dict[str, Any] = {"type": "image", "url": url}
        alt = _plain(image.get("alt_text"), 255)
        if alt:
            entry["alt_text"] = alt
        media.append(entry)
        if len(media) >= 8:
            break
    if media:
        result["media"] = media
    option_values: dict[str, list[str]] = {}
    for variant in variants:
        for option in variant.get("options") or ():
            labels = option_values.setdefault(option["name"], [])
            if option["label"] not in labels:
                labels.append(option["label"])
    if option_values:
        result["options"] = [
            {"name": name, "values": [{"label": label} for label in labels]} for name, labels in option_values.items()
        ]
    result["variants"] = variants
    return result


def _search_text(product: Mapping[str, Any]) -> str:
    parts = [product.get("title"), product.get("description"), product.get("vendor"), product.get("product_type")]
    for variant in product.get("variants") or ():
        if isinstance(variant, Mapping):
            parts.extend([variant.get("title"), variant.get("sku")])
    return " ".join(_plain(part) or "" for part in parts).lower()


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> int | None:
    """Offset from an opaque cursor; None when malformed."""
    if cursor is None:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return None
    offset = data.get("o") if isinstance(data, Mapping) else None
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        return None
    return offset


def paginate(items: list[Any], *, offset: int, limit: int | None) -> tuple[list[Any], dict[str, Any]]:
    size = max(1, min(int(limit or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    page = items[offset : offset + size]
    has_next = offset + size < len(items)
    pagination: dict[str, Any] = {"has_next_page": has_next, "total_count": len(items)}
    if has_next:
        pagination["cursor"] = _encode_cursor(offset + size)
    return page, pagination


def search_catalog(
    products: Sequence[Mapping[str, Any]],
    *,
    query: str | None,
    categories: Sequence[str] | None,
    price_min: int | None,
    price_max: int | None,
    price_currency: str | None = None,
) -> list[dict[str, Any]]:
    """Matching UCP products, ordered by title. Every query token must match (case-insensitive).

    The price filter is denominated in ``price_currency`` (the request's
    ``context.currency``) and applies only to products priced in it; there is no
    currency conversion (price_filter.json allows ignoring it, with a message).
    """
    tokens = [token for token in re.split(r"\s+", (query or "").lower()) if token]
    wanted_categories = {item.strip().lower() for item in categories or () if isinstance(item, str) and item.strip()}
    matches: list[tuple[str, dict[str, Any]]] = []
    for product in products:
        rendered = ucp_product(product)
        if rendered is None:
            continue
        haystack = _search_text(product)
        if tokens and not all(token in haystack for token in tokens):
            continue
        if wanted_categories and str(product.get("product_type") or "").strip().lower() not in wanted_categories:
            continue
        low = rendered["price_range"]["min"]["amount"]
        high = rendered["price_range"]["max"]["amount"]
        applies = price_currency is not None and rendered["price_range"]["min"]["currency"] == price_currency
        if applies and price_min is not None and high < price_min:
            continue
        if applies and price_max is not None and low > price_max:
            continue
        matches.append((rendered["title"].lower(), rendered))
    matches.sort(key=lambda pair: (pair[0], pair[1]["id"]))
    return [rendered for _title, rendered in matches]


def lookup_catalog(
    products: Sequence[Mapping[str, Any]],
    ids: Sequence[str],
) -> tuple[list[dict[str, Any]], list[int]]:
    """Products for ``ids`` (product id, variant id or SKU) and the positions of ids that matched nothing.

    A variant id or SKU resolves ``exact`` to that variant; a product id resolves
    ``featured`` to its first available variant (else its first variant). Every
    returned variant carries ``inputs`` naming the request ids it answers.
    """
    rendered_by_id: dict[str, dict[str, Any]] = {}
    for product in products:
        rendered = ucp_product(product)
        if rendered is not None:
            rendered_by_id[rendered["id"]] = rendered
    sku_index: dict[str, tuple[str, str]] = {}
    variant_index: dict[str, str] = {}
    for product_id, rendered in rendered_by_id.items():
        for variant in rendered["variants"]:
            variant_index[variant["id"]] = product_id
            if variant.get("sku"):
                sku_index.setdefault(variant["sku"], (product_id, variant["id"]))
    selected: dict[str, dict[str, list[dict[str, str]]]] = {}
    order: list[str] = []
    not_found: list[int] = []
    for index, request_id in enumerate(ids):
        if request_id in rendered_by_id:
            variants = rendered_by_id[request_id]["variants"]
            featured = next(
                (item for item in variants if (item.get("availability") or {}).get("available")), variants[0]
            )
            target = (request_id, featured["id"], "featured")
        elif request_id in variant_index:
            target = (variant_index[request_id], request_id, "exact")
        elif request_id in sku_index:
            product_id, variant_id = sku_index[request_id]
            target = (product_id, variant_id, "exact")
        else:
            not_found.append(index)
            continue
        product_id, variant_id, match = target
        if product_id not in selected:
            selected[product_id] = {}
            order.append(product_id)
        selected[product_id].setdefault(variant_id, []).append({"id": request_id, "match": match})
    results: list[dict[str, Any]] = []
    for product_id in order:
        rendered = dict(rendered_by_id[product_id])
        chosen = selected[product_id]
        rendered["variants"] = [
            {**variant, "inputs": chosen[variant["id"]]} for variant in rendered["variants"] if variant["id"] in chosen
        ]
        results.append(rendered)
    return results, not_found


def catalog_unavailable_message() -> dict[str, Any]:
    return {
        "type": "error",
        "code": "catalog_unavailable",
        "content": "The catalog is temporarily unavailable; its verified source data is missing or stale.",
        "severity": "unrecoverable",
    }


def not_found_message(index: int) -> dict[str, Any]:
    return {
        "type": "info",
        "code": "not_found",
        "content": f"No product or variant matches requested identifier {index + 1}.",
    }


def price_filter_ignored_message() -> dict[str, Any]:
    return {
        "type": "info",
        "code": "price_filter_ignored",
        "content": "The price filter applies only to products priced in context.currency; set it to filter by price.",
    }
