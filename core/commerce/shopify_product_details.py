# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Product details from a Shopify store: tags, barcodes, the store link and public metafields.

Only metafields whose definition gives the storefront ``PUBLIC_READ`` access are
read: the shop already shows them to anyone visiting its store. Reference types
(files, other products, metaobjects), JSON and links are left out; the rest
become plain text. Every detail is checked as catalog-upload details are (names
that read as private business data or credentials, credential-like values,
http links), and one that fails is left out, never the product or the sync.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from core.commerce.catalog_upload import (
    MAX_ATTRIBUTES,
    declared_detail,
    gtin_or_none,
    public_tags,
    reads_private_or_reserved,
    safe_https_link,
)

# Matches ``metafields(first: ...)`` in the product query.
MAX_METAFIELDS = 20
PUBLIC_STOREFRONT_ACCESS = "PUBLIC_READ"
_SIMPLE_TYPES = frozenset(
    {"single_line_text_field", "multi_line_text_field", "number_integer", "number_decimal", "date", "date_time",
     "boolean", "color", "url"}
)  # fmt: skip
_MEASUREMENT_TYPES = frozenset({"weight", "volume", "dimension"})


def metafield_details(nodes: Sequence[Any]) -> list[dict[str, str]]:
    """``attributes`` [{name, label, value}] from a product's metafield nodes, public ones only."""
    details: list[dict[str, str]] = []
    seen: set[str] = set()
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        definition = node.get("definition")
        if not isinstance(definition, Mapping):
            continue
        access = definition.get("access")
        if not isinstance(access, Mapping) or access.get("storefront") != PUBLIC_STOREFRONT_ACCESS:
            continue
        key = str(node.get("key") or "")
        label = " ".join(str(definition.get("name") or key.replace("_", " ")).split())
        if not label or reads_private_or_reserved(key) or reads_private_or_reserved(str(node.get("namespace") or "")):
            continue
        text = metafield_text(str(node.get("type") or ""), node.get("value"))
        if not text:
            continue
        detail, problem = declared_detail(label, label, text)
        if problem or detail is None or detail["name"] in seen:
            continue
        seen.add(detail["name"])
        details.append(detail)
        if len(details) >= MAX_ATTRIBUTES:
            break
    return details


def metafield_text(kind: str, value: Any) -> str | None:
    """A metafield value as plain text, or None for types that are not read."""
    if not isinstance(value, str) or not value.strip():
        return None
    if kind.startswith("list."):
        items = _json(value)
        if not isinstance(items, list):
            return None
        texts = [_typed_text(kind[5:], item) for item in items]
        joined = "; ".join(text for text in texts if text)
        return joined or None
    if kind in _SIMPLE_TYPES:
        if kind == "boolean":
            return {"true": "yes", "false": "no"}.get(value.strip().lower())
        return value.strip()
    return _typed_text(kind, _json(value))


def _typed_text(kind: str, value: Any) -> str | None:
    """One parsed value of ``kind`` (a list item, or a JSON metafield) as text."""
    if kind in _SIMPLE_TYPES:
        if isinstance(value, bool):
            return "yes" if value else "no"
        return str(value).strip() if isinstance(value, str | int | float) else None
    if not isinstance(value, Mapping):
        return None
    if kind == "rating":
        rating, scale = _number(value.get("value")), _number(value.get("scale_max"))
        return f"{rating} out of {scale}" if rating and scale else None
    if kind in _MEASUREMENT_TYPES and isinstance(value.get("unit"), str):
        amount = _number(value.get("value"))
        return f"{amount} {value['unit'].replace('_', ' ').lower()}" if amount else None
    if kind == "money" and value.get("amount") is not None and isinstance(value.get("currency_code"), str):
        return f"{value['amount']} {value['currency_code'].upper()}"
    if kind == "rich_text_field":
        return " ".join(_rich_text(value).split()) or None
    return None


def _rich_text(node: Any) -> str:
    """The text nodes of a Shopify rich-text tree, block by block."""
    if not isinstance(node, Mapping):
        return ""
    if node.get("type") == "text":
        return str(node.get("value") or "")
    children = node.get("children")
    parts = [_rich_text(child) for child in children] if isinstance(children, list) else []
    return " ".join(part for part in parts if part)


def _number(value: Any) -> str | None:
    """A finite number as plain decimal text, else None."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return format(number.normalize(), "f") if number.is_finite() else None


def _json(value: str) -> Any:
    try:
        return json.loads(value)
    except ValueError:
        return None


def product_extras(node: Mapping[str, Any]) -> dict[str, Any]:
    """``tags``, ``url`` (the product page on the online store) and ``attributes`` of a Shopify product node.

    Keys appear only when set, as in catalog-upload products.
    """
    extras: dict[str, Any] = {}
    tags = public_tags(node.get("tags"))
    if tags:
        extras["tags"] = tuple(tags)
    url = safe_https_link(node.get("onlineStoreUrl"))
    if url:
        extras["url"] = url
    metafields = node.get("metafields")
    nodes = metafields.get("nodes") if isinstance(metafields, Mapping) else None
    details = metafield_details(nodes if isinstance(nodes, list) else [])
    if details:
        extras["attributes"] = tuple(details)
    return extras


def variant_gtin(variant: Mapping[str, Any]) -> str | None:
    """The variant's barcode when it is a valid GTIN (Shopify also holds UPCs, ISBNs and store codes)."""
    return gtin_or_none(variant.get("barcode"))
