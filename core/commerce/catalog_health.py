# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Catalog health: what in a merchant's stored catalog keeps a product from being sold well.

``catalog_health_report`` reads the products of the stored catalog snapshot (the
newest connector evidence of the merchant's catalog source) and counts, per check,
the variants that fail it, with a few example product names and a one-line fix.
It is for the merchant only: the shopping network never ranks or filters shops by it.

Two tiers:

* **Problems** (``severity: blocking``): the variant is not offered to buyers.
  A variant with no usable price (missing, unreadable, negative, or no currency)
  is never listed and never checks out. A negative stock count is shown as out
  of stock, so the variant cannot be ordered.
* **Warnings**: the variant is offered, but buyers or their AI assistants get
  less to go on: a SKU shared by several variants of one catalog (the variants
  stay on sale under their own ids, but the SKU cannot name one of them: a lookup
  or checkout by that SKU is not resolved and per-SKU overrides do not apply,
  ``core.commerce.sku_keys``), no SKU, stock not tracked (buyers cannot be told
  what is in stock), zero stock, no image, no description, no barcode (GTIN), a
  very short title.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.commerce.catalog_upload import gtin_or_none
from core.commerce.sku_keys import ambiguous_skus, sku_of, source_of
from core.commerce.ucp.catalog import minor_units

MAX_EXAMPLES = 3
SHORT_TITLE_CHARS = 8
_INACTIVE_STATUSES = frozenset({"DRAFT", "ARCHIVED"})


@dataclass(frozen=True)
class HealthCheck:
    code: str
    severity: str  # "blocking" or "warning"
    label: str
    effect: str
    fix_shopify: str
    fix_upload: str


CHECKS: tuple[HealthCheck, ...] = (
    HealthCheck(
        "no_price",
        "blocking",
        "No usable price",
        "Not offered to buyers.",
        "In Shopify, open the product and enter a price for each variant, then read the catalog again.",
        "Fill the price column for these products and upload the file again.",
    ),
    HealthCheck(
        "negative_stock",
        "blocking",
        "Stock below zero",
        "Shown as out of stock, so buyers cannot order it.",
        "In Shopify, open the product and correct the available quantity under Inventory.",
        "Correct the quantity column (0 or more) and upload the file again.",
    ),
    HealthCheck(
        "duplicate_sku",
        "warning",
        "SKU used by more than one variant",
        "Offered under each variant's own id, but a lookup or order by this SKU is refused as ambiguous, "
        "and price or stock you set per SKU does not apply to these variants.",
        "In Shopify, open each product and give every variant its own SKU, then read the catalog again.",
        "Give every row its own sku and upload the file again.",
    ),
    HealthCheck(
        "no_sku",
        "warning",
        "No SKU",
        "Offered, but orders are harder to match to your own records.",
        "In Shopify, open the product and enter a SKU for each variant.",
        "Fill the sku column for these products.",
    ),
    HealthCheck(
        "stock_unknown",
        "warning",
        "Stock not tracked",
        "Offered, but buyers cannot be told whether it is in stock.",
        "In Shopify, open the product, tick Track quantity under Inventory and enter the count.",
        "Fill the quantity column for these products.",
    ),
    HealthCheck(
        "zero_stock",
        "warning",
        "Out of stock",
        "Listed as out of stock until you restock it.",
        "Restock it, or correct the quantity under Inventory in Shopify if the count is wrong.",
        "Correct the quantity column if the count is wrong.",
    ),
    HealthCheck(
        "no_image",
        "warning",
        "No image",
        "Buyers' AI assistants show no photo.",
        "In Shopify, open the product and add a photo under Media.",
        "Add an https:// link in the image_url column.",
    ),
    HealthCheck(
        "no_description",
        "warning",
        "No description",
        "Buyers' AI assistants have only the title to go on.",
        "In Shopify, open the product and write a short description.",
        "Fill the description column.",
    ),
    HealthCheck(
        "no_barcode",
        "warning",
        "No barcode (GTIN)",
        "Buyers' AI assistants cannot match it to the same product at other shops.",
        "In Shopify, open the product and enter the barcode (GTIN, EAN or UPC) of each variant.",
        "Add a barcode column with the product's GTIN, EAN or UPC.",
    ),
    HealthCheck(
        "short_title",
        "warning",
        "Very short title",
        "Harder for buyers to find by search.",
        "In Shopify, open the product and give it a fuller title, such as brand, product and size.",
        "Give the title column a fuller name, such as brand, product and size.",
    ),
)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _variants(product: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [v for v in product.get("variants") or () if isinstance(v, Mapping)]


def _active(product: Mapping[str, Any]) -> bool:
    return str(product.get("status") or "").upper() not in _INACTIVE_STATUSES


def _price_usable(variant: Mapping[str, Any], product: Mapping[str, Any], currency: str | None) -> bool:
    chosen = _text(variant.get("currency")) or _text(product.get("_evidence_currency")) or (currency or "")
    chosen = chosen.upper()
    if len(chosen) != 3 or not chosen.isalpha():
        return False
    return minor_units(variant.get("price"), chosen) is not None


def _stock(variant: Mapping[str, Any]) -> int | None:
    value = variant.get("inventory_quantity_snapshot")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _example(product: Mapping[str, Any], variant: Mapping[str, Any] | None) -> str:
    title = _text(product.get("title")) or "Untitled product"
    variant_title = _text((variant or {}).get("title"))
    if variant_title and variant_title.lower() not in ("default", "default title"):
        title = f"{title} ({variant_title})"
    return title[:120]


def _has_image(product: Mapping[str, Any]) -> bool:
    for image in product.get("images") or ():
        if isinstance(image, Mapping) and _text(image.get("url")).startswith("https://"):
            return True
    return False


def catalog_health_report(
    products: Sequence[Mapping[str, Any]],
    *,
    source: str | None,
    currency: str | None = None,
) -> dict[str, Any]:
    """The health of a stored catalog: counts, problems (not offered) and warnings, each with a fix.

    ``currency`` is the evidence currency, the fallback for variants that carry none (as the
    catalog uses it). Draft and archived products are counted apart and not checked: they are
    not offered anyway.
    """
    counts: dict[str, int] = {check.code: 0 for check in CHECKS}
    examples: dict[str, list[str]] = {check.code: [] for check in CHECKS}

    def flag(code: str, product: Mapping[str, Any], variant: Mapping[str, Any] | None) -> None:
        counts[code] += 1
        example = _example(product, variant)
        if len(examples[code]) < MAX_EXAMPLES and example not in examples[code]:
            examples[code].append(example)

    active = [p for p in products if isinstance(p, Mapping) and _active(p)]
    ambiguous = ambiguous_skus(active)
    variant_count = 0
    offered = 0
    for product in active:
        variants = _variants(product)
        title = _text(product.get("title"))
        if not _has_image(product):
            flag("no_image", product, None)
        if not _text(product.get("description")):
            flag("no_description", product, None)
        if len(title) < SHORT_TITLE_CHARS:
            flag("short_title", product, None)
        for variant in variants:
            variant_count += 1
            blocked = False
            if not _price_usable(variant, product, currency):
                flag("no_price", product, variant)
                blocked = True
            sku = sku_of(variant)
            if not sku:
                flag("no_sku", product, variant)
            elif (source_of(product), sku) in ambiguous:
                flag("duplicate_sku", product, variant)
            stock = _stock(variant)
            if stock is None or variant.get("inventory_tracked") is False:
                flag("stock_unknown", product, variant)
            elif stock < 0:
                flag("negative_stock", product, variant)
                blocked = True
            elif stock == 0:
                flag("zero_stock", product, variant)
            if gtin_or_none(variant.get("gtin")) is None:
                flag("no_barcode", product, variant)
            if not blocked:
                offered += 1
    upload = source == "catalog_upload"
    problems: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for check in CHECKS:
        if not counts[check.code]:
            continue
        item = {
            "code": check.code,
            "label": check.label,
            "count": counts[check.code],
            # Product-level checks count products; the rest count variants.
            "counts": "products" if check.code in ("no_image", "no_description", "short_title") else "variants",
            "examples": examples[check.code],
            "effect": check.effect,
            "fix": check.fix_upload if upload else check.fix_shopify,
        }
        (problems if check.severity == "blocking" else warnings).append(item)
    return {
        "source": source,
        "product_count": len(active),
        "inactive_product_count": len(products) - len(active),
        "variant_count": variant_count,
        "offered_variant_count": offered,
        "status": "problems" if problems else ("warnings" if warnings else "ok"),
        "problems": problems,
        "warnings": warnings,
    }
