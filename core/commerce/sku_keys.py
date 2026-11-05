# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""SKUs that more than one variant of one catalog source carries, so they cannot name one item.

Such variants stay on sale under their own ids (the UCP variant id, the source variant id).
Only the SKU stops working as a key: a catalog lookup or a checkout item addressed by that
SKU is not resolved (rather than picking one of the variants), and the merchant's per-SKU
price and stock overrides (``core.commerce.source_precedence``) are matched on each
variant's own id instead. Different sources (a Shopify store and an upload kept for source
precedence) may carry the same SKU on purpose; only a SKU repeated within one source counts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

_INACTIVE_STATUSES = frozenset({"DRAFT", "ARCHIVED"})


def sku_of(variant: Mapping[str, Any]) -> str:
    value = variant.get("sku")
    return value.strip() if isinstance(value, str) else ""


def source_of(product: Mapping[str, Any]) -> str:
    return str(product.get("source_system") or "")


def ambiguous_skus(products: Iterable[Any]) -> set[tuple[str, str]]:
    """``(source_system, sku)`` pairs that more than one variant of one catalog source uses.

    Draft and archived products are not counted: they are not offered, so they take no SKU away.
    """
    seen: dict[tuple[str, str], int] = {}
    for product in products:
        if not isinstance(product, Mapping):
            continue
        if str(product.get("status") or "").upper() in _INACTIVE_STATUSES:
            continue
        for variant in product.get("variants") or ():
            if isinstance(variant, Mapping) and (sku := sku_of(variant)):
                key = (source_of(product), sku)
                seen[key] = seen.get(key, 0) + 1
    return {key for key, count in seen.items() if count > 1}


def sku_is_ambiguous(product: Mapping[str, Any], variant: Mapping[str, Any], ambiguous: set[tuple[str, str]]) -> bool:
    sku = sku_of(variant)
    return bool(sku) and (source_of(product), sku) in ambiguous
