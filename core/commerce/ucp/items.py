# SPDX-License-Identifier: Apache-2.0
"""Item lookup for checkout and cart pricing: the authoritative title, price, stock and sale basis.

UCP ``item.id`` is the id the catalog capability publishes for a variant
(``core.commerce.ucp.catalog.ucp_variant_id``), so an item found through
``/ucp/catalog/search`` or ``/lookup`` checks out as is; the SKU, the source
variant id and the numeric part of a Shopify variant gid are accepted too.
Items are read from the merchant's synced product evidence and priced with the
catalog's ISO 4217 minor-unit conversion.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from core.commerce.ucp.catalog import minor_units, ucp_variant_id
from core.commerce.ucp.merchant import MerchantUcp
from core.commerce.ucp.quantity import EACH_UNIT, QuantityError, QuantityUnit, parse_quantity_unit


@dataclass(frozen=True)
class CatalogItem:
    id: str
    title: str
    price_minor_units: int
    currency: str
    inventory: int | None
    image_url: str | None
    quantity_unit: QuantityUnit
    product_ref: str | None = None
    variant_id: str | None = None
    sku: str | None = None

    @property
    def storefront_variant_token(self) -> str | None:
        """The numeric id of a Shopify variant gid (the storefront cart permalink token)."""
        if self.variant_id and self.variant_id.startswith("gid://shopify/ProductVariant/"):
            return self.variant_id.rsplit("/", 1)[-1]
        return None


class ItemCatalog(Protocol):
    async def lookup(self, merchant: MerchantUcp, item_ids: Sequence[str]) -> dict[str, CatalogItem]: ...


ProductLoader = Callable[[MerchantUcp], Awaitable[Sequence[Mapping[str, Any]]]]


class ProductEvidenceCatalog:
    """Items from the merchant's synced product evidence (loaded per request by ``loader``)."""

    def __init__(self, loader: ProductLoader) -> None:
        self._loader = loader

    async def lookup(self, merchant: MerchantUcp, item_ids: Sequence[str]) -> dict[str, CatalogItem]:
        wanted = {str(item_id) for item_id in item_ids if item_id}
        if not wanted:
            return {}
        products = await self._loader(merchant)
        found: dict[str, CatalogItem] = {}
        for product in products:
            for variant in product.get("variants") or ():
                if not isinstance(variant, Mapping):
                    continue
                keys = _variant_keys(variant)
                for item_id in wanted & keys:
                    item = _catalog_item(merchant, product, variant, item_id)
                    if item is not None:
                        found.setdefault(item_id, item)
        return found


def _variant_keys(variant: Mapping[str, Any]) -> set[str]:
    """The ids an item may be addressed by: the catalog's UCP variant id first, then the SKU and source ids."""
    keys = {str(ucp_variant_id(variant) or ""), str(variant.get("sku") or ""), str(variant.get("variant_id") or "")}
    gid = str(variant.get("variant_id") or "")
    if gid.startswith("gid://shopify/ProductVariant/"):
        keys.add(gid.rsplit("/", 1)[-1])
    return {key for key in keys if key}


def _catalog_item(
    merchant: MerchantUcp, product: Mapping[str, Any], variant: Mapping[str, Any], item_id: str
) -> CatalogItem | None:
    currency = str(variant.get("currency") or merchant.currency or "").upper()
    price = minor_units(variant.get("price"), currency) if currency else None
    if price is None:
        return None
    unit = EACH_UNIT
    configured = merchant.quantity_units.get(item_id) or merchant.quantity_units.get(str(variant.get("sku") or ""))
    if configured:
        try:
            unit = parse_quantity_unit(configured)
        except QuantityError:
            return None
    title = str(product.get("title") or item_id)
    variant_title = str(variant.get("title") or "")
    if variant_title and variant_title.lower() != "default" and variant_title.lower() != "default title":
        title = f"{title} ({variant_title})"
    images = product.get("images") or ()
    image = images[0].get("url") if images and isinstance(images[0], Mapping) else None
    inventory = variant.get("inventory_quantity_snapshot")
    return CatalogItem(
        id=item_id,
        title=title,
        price_minor_units=price,
        currency=currency,
        inventory=inventory if isinstance(inventory, int) and not isinstance(inventory, bool) else None,
        image_url=image if isinstance(image, str) and image.startswith("https://") else None,
        quantity_unit=unit,
        product_ref=str(product.get("product_ref") or "") or None,
        variant_id=str(variant.get("variant_id") or "") or None,
        sku=str(variant.get("sku") or "") or None,
    )
