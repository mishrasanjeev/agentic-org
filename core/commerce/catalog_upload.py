# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""A merchant's own catalog, uploaded as CSV or JSON, as OACP connector evidence.

For a merchant without Shopify. Columns (CSV header, or the keys of each JSON
object): ``sku``, ``title``, ``description``, ``price``, ``currency``,
``quantity``, ``image_url``, ``url``. ``sku``, ``title``, ``price``,
``currency`` and ``quantity`` are required; every row is one product with one
variant. Every row is validated before anything is stored, and the errors name
the row and the column.

The evidence says where the catalog came from: ``source_system`` is
``catalog_upload`` and ``merchant_system_source_of_record`` is true (the
merchant uploaded it; no store system stands behind it). ``uploaded_at`` is
when the merchant uploaded it; ``source_observed_at`` is when AgenticOrg last
read the stored upload for a Grantex request (the upload stays the merchant's
catalog until it uploads another).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from core.commerce.c6z_runtime_vertical import (
    CATALOG_UPLOAD_SOURCE_SYSTEM,
    contains_private_or_executable_value,
    validate_connector_evidence,
    validate_seller_onboarding_packet,
)

MAX_UPLOAD_BYTES = 1_000_000
MAX_ROWS = 2000
MAX_REPORTED_ERRORS = 20
COLUMNS: tuple[str, ...] = ("sku", "title", "description", "price", "currency", "quantity", "image_url", "url")
REQUIRED_COLUMNS: tuple[str, ...] = ("sku", "title", "price", "currency", "quantity")
FORMATS: frozenset[str] = frozenset({"csv", "json"})
MAX_TITLE = 200
MAX_DESCRIPTION = 2000
MAX_URL = 2048
MAX_QUANTITY = 10_000_000
# ISO 4217 currencies whose minor unit is not 2 (prices may carry at most this many decimals).
_CURRENCY_EXPONENTS: dict[str, int] = {
    "BHD": 3, "CLP": 0, "IQD": 3, "ISK": 0, "JOD": 3, "JPY": 0, "KRW": 0, "KWD": 3,
    "LYD": 3, "OMR": 3, "PYG": 0, "TND": 3, "UGX": 0, "VND": 0, "XAF": 0, "XOF": 0,
}  # fmt: skip
_SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_TAG_RE = re.compile(r"<[^>]+>")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class CatalogUploadError(ValueError):
    """The upload was refused; ``errors`` lists ``{"row", "column", "message"}`` (row 1 is the first product)."""

    def __init__(self, message: str, errors: Sequence[Mapping[str, Any]] = ()) -> None:
        super().__init__(message)
        self.errors = [dict(item) for item in errors][:MAX_REPORTED_ERRORS]


def currency_exponent(currency: str) -> int:
    return _CURRENCY_EXPONENTS.get(currency.upper(), 2)


def parse_catalog_upload(content: str, fmt: str) -> list[dict[str, Any]]:
    """The validated rows of an upload, or ``CatalogUploadError`` listing what is wrong."""
    if fmt not in FORMATS:
        raise CatalogUploadError("format must be csv or json")
    if len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise CatalogUploadError(f"the upload is larger than {MAX_UPLOAD_BYTES // 1000} KB")
    raw_rows = _csv_rows(content) if fmt == "csv" else _json_rows(content)
    if not raw_rows:
        raise CatalogUploadError("the upload has no products")
    if len(raw_rows) > MAX_ROWS:
        raise CatalogUploadError(f"the upload has more than {MAX_ROWS} products")
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_skus: set[str] = set()
    currencies: set[str] = set()
    for index, raw in enumerate(raw_rows, start=1):
        row, row_errors = _validate_row(index, raw)
        if row is not None:
            if row["sku"] in seen_skus:
                row_errors.append({"row": index, "column": "sku", "message": "repeats an earlier sku"})
            seen_skus.add(row["sku"])
            currencies.add(row["currency"])
        errors.extend(row_errors)
        if row is not None and not row_errors:
            rows.append(row)
    if len(currencies) > 1:
        errors.append({"row": None, "column": "currency", "message": "every product must use the same currency"})
    if errors:
        raise CatalogUploadError(f"{len(errors)} problem(s) in the upload", errors)
    return rows


def catalog_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    """SHA-256 of the validated rows: the evidence id changes only when the catalog does."""
    return hashlib.sha256(json.dumps(list(rows), sort_keys=True).encode("utf-8")).hexdigest()


def upload_products(
    *,
    rows: Sequence[Mapping[str, Any]],
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
    synced_at: str,
    uploaded_at: str,
) -> list[dict[str, Any]]:
    """The rows in the normalized connector-evidence product shape (one variant per product)."""
    products: list[dict[str, Any]] = []
    for row in rows:
        sku = str(row["sku"])
        sku_digest = hashlib.sha256(f"{merchant_id}|{sku}".encode()).hexdigest()
        product: dict[str, Any] = {
            "product_ref": f"{CATALOG_UPLOAD_SOURCE_SYSTEM}_product:{sku_digest[:24]}",
            "source_product_id": f"{CATALOG_UPLOAD_SOURCE_SYSTEM}:product:{sku}",
            "tenant_id": tenant_id,
            "merchant_id": merchant_id,
            "seller_agent_id": seller_agent_id,
            "title": row["title"],
            "description": row.get("description"),
            "vendor": None,
            "product_type": None,
            "status": "ACTIVE",
            "images": [{"url": row["image_url"], "alt_text": row["title"]}] if row.get("image_url") else [],
            "variants": [
                {
                    "variant_id": f"{CATALOG_UPLOAD_SOURCE_SYSTEM}:variant:{sku}",
                    "sku": sku,
                    "title": "Default",
                    "price": row["price"],
                    "compare_at_price": None,
                    "currency": row["currency"],
                    "inventory_quantity_snapshot": row["quantity"],
                    "inventory_item_ref": f"{CATALOG_UPLOAD_SOURCE_SYSTEM}_inventory_item:{sku_digest[24:48]}",
                    "updated_at": synced_at,
                    "selected_options": [],
                }
            ],
            "updated_at": synced_at,
            "synced_at": synced_at,
            "uploaded_at": uploaded_at,
            "source_system": CATALOG_UPLOAD_SOURCE_SYSTEM,
            "source_mode": "read_only",
            "merchant_system_source_of_record": True,
        }
        if row.get("url"):
            product["url"] = row["url"]
        products.append(product)
    return products


def build_catalog_upload_evidence(
    *,
    packet: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    uploaded_at: str,
    observed_at: str,
) -> dict[str, Any]:
    """Connector evidence for the uploaded catalog, validated like Shopify evidence.

    The evidence id and ``source_evidence_ref`` are stable for a tenant, merchant,
    seller and catalog, so reading the same upload again updates one row.
    """
    validate_seller_onboarding_packet(packet)
    if packet.get("connector_choice") != CATALOG_UPLOAD_SOURCE_SYSTEM:
        raise CatalogUploadError("the onboarding packet is not a catalog upload packet")
    if not rows:
        raise CatalogUploadError("the upload has no products")
    tenant_id = str(packet["tenant_id"])
    merchant_id = str(packet["merchant_id"])
    seller_agent_id = str(packet["seller_agent_id"])
    products = upload_products(
        rows=rows,
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        synced_at=observed_at,
        uploaded_at=uploaded_at,
    )
    seed = hashlib.sha256(
        "|".join((tenant_id, merchant_id, seller_agent_id, catalog_digest(rows))).encode("utf-8")
    ).hexdigest()
    evidence = {
        "evidence_id": f"c6z_upload_evidence_{seed[:24]}",
        "packet_id": packet["packet_id"],
        "tenant_id": tenant_id,
        "merchant_id": merchant_id,
        "seller_agent_id": seller_agent_id,
        "source_system": CATALOG_UPLOAD_SOURCE_SYSTEM,
        "source_mode": "read_only",
        "source_evidence_ref": f"agenticorg:{CATALOG_UPLOAD_SOURCE_SYSTEM}:evidence:{seed[24:48]}",
        "source_observed_at": observed_at,
        "synced_at": observed_at,
        "uploaded_at": uploaded_at,
        "currency": str(rows[0]["currency"]),
        "products": products,
        "product_count": len(products),
        "variant_count": len(products),
        "idempotency_key": None,
        "merchant_system_source_of_record": True,
        "raw_payload_stored": False,
        "no_payment_execution": True,
        "no_public_discovery_enablement": True,
        "allowed_to_execute": False,
        "non_authoritative_for_transaction": True,
    }
    validate_connector_evidence(evidence)
    return evidence


def uploaded_at_of(products: Sequence[Mapping[str, Any]]) -> str | None:
    """When the merchant uploaded the stored catalog (recorded on every product)."""
    for product in products:
        value = product.get("uploaded_at")
        if isinstance(value, str) and value:
            return value
    return None


def rows_from_products(products: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The validated rows back from stored upload evidence (to read the same upload again)."""
    rows: list[dict[str, Any]] = []
    for product in products:
        variants = product.get("variants") or []
        variant = variants[0] if variants and isinstance(variants[0], Mapping) else {}
        images = product.get("images") or []
        image = images[0] if images and isinstance(images[0], Mapping) else {}
        rows.append(
            {
                "sku": variant.get("sku"),
                "title": product.get("title"),
                "description": product.get("description"),
                "price": variant.get("price"),
                "currency": variant.get("currency"),
                "quantity": variant.get("inventory_quantity_snapshot"),
                "image_url": image.get("url"),
                "url": product.get("url"),
            }
        )
    return rows


def _csv_rows(content: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(content.lstrip("﻿")), restkey="__extra__")
    header = [str(name or "").strip().lower() for name in (reader.fieldnames or [])]
    missing = [name for name in REQUIRED_COLUMNS if name not in header]
    if missing:
        raise CatalogUploadError(
            "the CSV header is missing " + ", ".join(missing),
            [{"row": None, "column": name, "message": "column missing from the header"} for name in missing],
        )
    unknown = [name for name in header if name and name not in COLUMNS]
    if unknown:
        raise CatalogUploadError(
            "the CSV header has unknown columns: " + ", ".join(unknown[:10]),
            [{"row": None, "column": name, "message": "unknown column"} for name in unknown],
        )
    rows: list[dict[str, Any]] = []
    try:
        for record in reader:
            if len(rows) > MAX_ROWS:
                break
            values = {str(key or "").strip().lower(): value for key, value in record.items() if key is not None}
            if not any(str(value or "").strip() for key, value in values.items() if key in COLUMNS):
                continue
            rows.append(values)
    except csv.Error as exc:
        raise CatalogUploadError(f"the CSV could not be read: {exc}") from exc
    return rows


def _json_rows(content: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(content)
    except ValueError as exc:
        raise CatalogUploadError("the JSON could not be read") from exc
    items = parsed.get("products") if isinstance(parsed, Mapping) else parsed
    if not isinstance(items, list):
        raise CatalogUploadError('the JSON must be a list of products or {"products": [...]}')
    rows: list[dict[str, Any]] = []
    for item in items[: MAX_ROWS + 1]:
        if not isinstance(item, Mapping):
            rows.append({"__not_object__": True})
            continue
        unknown = [str(key) for key in item if str(key).lower() not in COLUMNS]
        row = {str(key).lower(): value for key, value in item.items()}
        if unknown:
            row["__unknown__"] = unknown
        rows.append(row)
    return rows


def _validate_row(index: int, raw: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []

    def fail(column: str | None, message: str) -> None:
        errors.append({"row": index, "column": column, "message": message})

    if raw.get("__not_object__"):
        fail(None, "must be an object")
        return None, errors
    if raw.get("__extra__"):
        fail(None, "has more values than the header has columns")
    for name in raw.get("__unknown__") or ():
        fail(str(name), "unknown column")
    sku = _text(raw.get("sku"))
    if not sku:
        fail("sku", "is required")
    elif not _SKU_RE.match(sku):
        fail("sku", "use 1-64 letters, digits, dots, hyphens or underscores")
    title = _plain(raw.get("title"))
    if not title:
        fail("title", "is required")
    elif len(title) > MAX_TITLE:
        fail("title", f"is longer than {MAX_TITLE} characters")
    description = _plain(raw.get("description"))
    if description and len(description) > MAX_DESCRIPTION:
        fail("description", f"is longer than {MAX_DESCRIPTION} characters")
    currency = _text(raw.get("currency")).upper()
    if not _CURRENCY_RE.match(currency):
        fail("currency", "must be a three-letter ISO 4217 code such as INR or USD")
    price = _price(raw.get("price"), currency, fail)
    quantity = _quantity(raw.get("quantity"), fail)
    image_url = _https_url(raw.get("image_url"), "image_url", fail)
    url = _https_url(raw.get("url"), "url", fail)
    row = {
        "sku": sku,
        "title": title,
        "description": description or None,
        "price": price,
        "currency": currency,
        "quantity": quantity,
        "image_url": image_url,
        "url": url,
    }
    for column, value in row.items():
        if isinstance(value, str) and contains_private_or_executable_value(value):
            fail(column, "looks like a credential, a token or an executable instruction; remove it")
    if errors:
        return (row if sku else None), errors
    return row, errors


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return _CONTROL_RE.sub("", str(value)).strip()


def _plain(value: Any) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", _text(value))).strip()


def _price(value: Any, currency: str, fail: Any) -> str | None:
    text = _text(value)
    if not text:
        fail("price", "is required")
        return None
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        fail("price", "must be a number such as 499.00")
        return None
    if not amount.is_finite() or amount <= 0:
        fail("price", "must be greater than zero")
        return None
    exponent = currency_exponent(currency) if _CURRENCY_RE.match(currency) else 2
    raw_exponent = amount.as_tuple().exponent
    if isinstance(raw_exponent, int) and -raw_exponent > exponent:
        fail("price", f"has more than {exponent} decimal places for {currency or 'this currency'}")
        return None
    if amount >= Decimal(10) ** 12:
        fail("price", "is too large")
        return None
    return str(amount.quantize(Decimal(1).scaleb(-exponent)))


def _quantity(value: Any, fail: Any) -> int | None:
    if isinstance(value, bool):
        fail("quantity", "must be a whole number")
        return None
    text = _text(value)
    if not text:
        fail("quantity", "is required")
        return None
    if not re.fullmatch(r"\d+", text):
        fail("quantity", "must be a whole number of 0 or more")
        return None
    quantity = int(text)
    if quantity > MAX_QUANTITY:
        fail("quantity", "is too large")
        return None
    return quantity


def _https_url(value: Any, column: str, fail: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    if len(text) > MAX_URL:
        fail(column, f"is longer than {MAX_URL} characters")
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        fail(column, "must be an https:// address")
        return None
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or " " in text:
        fail(column, "must be an https:// address without a user name or password")
        return None
    return text
