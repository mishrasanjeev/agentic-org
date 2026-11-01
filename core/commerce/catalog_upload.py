# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""A merchant's own catalog, uploaded as CSV or JSON, as OACP connector evidence.

For a merchant without Shopify. Columns (CSV header, or the keys of each JSON
object): ``sku``, ``title``, ``description``, ``price``, ``currency``,
``quantity``, ``image_url``, ``url``. ``sku``, ``title``, ``price``,
``currency`` and ``quantity`` are required; every row is one product with one
variant. Every row is validated before anything is stored, and the errors name
the row and the column.

Optional named columns fill the fields every catalog source shares: ``brand``,
``category`` (or ``product_type``), ``tags`` (separated by ``;``, ``,`` or ``|``),
``compare_at_price`` (or ``mrp``) and ``gtin`` (or ``barcode``). Any other column
is a product detail buyers' AI assistants can read, such as ingredients,
allergens, shelf life, a food-safety licence number or a lab-report link: it is
stored as ``attributes`` [{name, label, value}] in column order, and empty cells
are left out. A JSON product may also carry an ``attributes`` object. Columns
whose name starts with ``_`` are skipped, so a merchant can keep internal
columns in the same file; a name that reads as private business data (cost,
margin, supplier and the like) is refused rather than published.

The evidence says where the catalog came from: ``source_system`` is
``catalog_upload`` and ``merchant_system_source_of_record`` is true (the
merchant uploaded it; no store system stands behind it). ``uploaded_at`` is
when the merchant uploaded it; ``source_observed_at`` is when AgenticOrg last
read the stored upload for a Grantex request (the upload stays the merchant's
catalog until it uploads another).
"""

from __future__ import annotations

import csv
import difflib
import functools
import hashlib
import io
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from core.commerce.c6z_runtime_vertical import (
    CATALOG_UPLOAD_SOURCE_SYSTEM,
    EXECUTION_MARKERS,
    PRIVATE_KEY_MARKERS,
    contains_private_or_executable_value,
    validate_connector_evidence,
    validate_seller_onboarding_packet,
)

MAX_UPLOAD_BYTES = 1_000_000
MAX_ROWS = 2000
MAX_REPORTED_ERRORS = 20
COLUMNS: tuple[str, ...] = ("sku", "title", "description", "price", "currency", "quantity", "image_url", "url")
REQUIRED_COLUMNS: tuple[str, ...] = ("sku", "title", "price", "currency", "quantity")
OPTIONAL_COLUMNS: tuple[str, ...] = ("brand", "category", "tags", "compare_at_price", "gtin")
COLUMN_ALIASES: dict[str, str] = {"product_type": "category", "mrp": "compare_at_price", "barcode": "gtin"}
KNOWN_COLUMNS: tuple[str, ...] = COLUMNS + OPTIONAL_COLUMNS
MAX_ATTRIBUTES = 30
MAX_ATTRIBUTE_NAME = 40
MAX_ATTRIBUTE_LABEL = 60
MAX_ATTRIBUTE_VALUE = 1000
MAX_BRAND = 120
MAX_CATEGORY = 120
MAX_TAGS = 20
MAX_TAG = 50
GTIN_LENGTHS = frozenset({8, 12, 13, 14})
# Column-name words that read as the merchant's own business data, never as a buyer-facing detail.
PRIVATE_BUSINESS_WORDS: frozenset[str] = frozenset(
    {"cost", "margin", "profit", "supplier", "purchase", "wholesale", "internal", "private", "confidential",
     "cogs", "markup", "landed"}
)  # fmt: skip
# The same, written as one word ("CostPrice", "buyingprice", "dealer price").
PRIVATE_BUSINESS_STEMS: tuple[str, ...] = (
    "cost", "margin", "profit", "supplier", "purchase", "wholesale", "cogs", "markup", "landed", "buyprice",
    "buyingprice", "dealerprice", "distributorprice", "vendorprice", "tradeprice", "netrate", "b2bprice", "b2brate",
)  # fmt: skip
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
_URL_IN_TEXT_RE = re.compile(r"[a-z][a-z0-9+.-]*://[^\s]+", re.I)
_TAG_SPLIT_RE = re.compile(r"[;,|]")


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


def detail_labels(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """The product-detail columns buyers' AI assistants will read, in first-seen order."""
    labels: list[str] = []
    for row in rows:
        for item in row.get("attributes") or ():
            label = str(item.get("label") or item.get("name") or "")
            if label and label not in labels:
                labels.append(label)
    return labels


def skipped_columns(content: str, fmt: str) -> list[str]:
    """CSV columns, or JSON keys, the upload left out because their name starts with ``_``."""
    names: list[str] = []
    try:
        if fmt == "csv":
            reader = csv.reader(io.StringIO(content.lstrip("\ufeff")))
            names = [str(name).strip() for name in next(reader, [])]
        elif fmt == "json":
            parsed = json.loads(content)
            items = parsed.get("products") if isinstance(parsed, Mapping) else parsed
            for item in items if isinstance(items, list) else []:
                if isinstance(item, Mapping):
                    names.extend(str(key).strip() for key in item)
                    nested = item.get("attributes")
                    if isinstance(nested, Mapping):
                        names.extend(
                            f"attributes.{str(key).strip()}" for key in nested if str(key).strip().startswith("_")
                        )
    except (csv.Error, ValueError):
        return []
    return [name for name in dict.fromkeys(names) if name.startswith(("_", "attributes._"))]


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
            "vendor": row.get("brand"),
            "product_type": row.get("category"),
            "status": "ACTIVE",
            "images": [{"url": row["image_url"], "alt_text": row["title"]}] if row.get("image_url") else [],
            "variants": [
                {
                    "variant_id": f"{CATALOG_UPLOAD_SOURCE_SYSTEM}:variant:{sku}",
                    "sku": sku,
                    "title": "Default",
                    "price": row["price"],
                    "compare_at_price": row.get("compare_at_price"),
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
        if row.get("gtin"):
            product["variants"][0]["gtin"] = row["gtin"]
        if row.get("tags"):
            product["tags"] = list(row["tags"])
        if row.get("attributes"):
            product["attributes"] = [dict(item) for item in row["attributes"]]
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
        row: dict[str, Any] = {
            "sku": variant.get("sku"),
            "title": product.get("title"),
            "description": product.get("description"),
            "price": variant.get("price"),
            "currency": variant.get("currency"),
            "quantity": variant.get("inventory_quantity_snapshot"),
            "image_url": image.get("url"),
            "url": product.get("url"),
        }
        # The optional fields only when set, so a catalog uploaded before they existed keeps its digest.
        optional = {
            "brand": product.get("vendor"),
            "category": product.get("product_type"),
            "tags": list(product.get("tags") or []),
            "compare_at_price": variant.get("compare_at_price"),
            "gtin": variant.get("gtin"),
            "attributes": [dict(item) for item in product.get("attributes") or [] if isinstance(item, Mapping)],
        }
        row.update({key: value for key, value in optional.items() if value})
        rows.append(row)
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
    labels = {str(name or "").strip().lower(): " ".join(str(name or "").split()) for name in reader.fieldnames or []}
    _check_header(header, labels)
    rows: list[dict[str, Any]] = []
    try:
        for record in reader:
            if len(rows) > MAX_ROWS:
                break
            # Excel often adds empty columns at the end: a column without a name holds nothing.
            values = {
                str(key).strip().lower(): value for key, value in record.items() if key is not None and str(key).strip()
            }
            if not any(str(value or "").strip() for key, value in values.items() if key in COLUMNS):
                continue
            values["__labels__"] = labels
            rows.append(values)
    except csv.Error as exc:
        raise CatalogUploadError(f"the CSV could not be read: {exc}") from exc
    return rows


def _check_header(header: Sequence[str], labels: Mapping[str, str]) -> None:
    """Refuse detail columns that are misspelled known columns, unsafe names or private business data."""
    errors: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    known: dict[str, str] = {}
    details = 0
    for name in header:
        if not name or name.startswith("_"):
            continue
        if name in KNOWN_COLUMNS or name in COLUMN_ALIASES:
            canonical = COLUMN_ALIASES.get(name, name)
            if canonical in known:
                other = known[canonical]
                message = "appears twice" if other == name else f"use one of {other} or {name}, not both"
                errors.append({"row": None, "column": labels.get(name, name), "message": message})
            known[canonical] = name
            continue
        label = labels.get(name, name)
        problem = _detail_name_problem(name)
        if problem:
            errors.append({"row": None, "column": label, "message": problem})
            continue
        key = _attribute_key(name)
        if key in seen:
            errors.append({"row": None, "column": label, "message": f"reads the same as the column {seen[key]}"})
        seen[key] = label
        details += 1
    if details > MAX_ATTRIBUTES:
        errors.append({"row": None, "column": None, "message": f"more than {MAX_ATTRIBUTES} product-detail columns"})
    if errors:
        raise CatalogUploadError(f"{len(errors)} problem(s) in the header", errors)


@functools.lru_cache(maxsize=4096)
def _attribute_key(name: str) -> str:
    """Letters (any script), their marks and digits; everything else becomes one underscore."""
    folded = "".join(char if unicodedata.category(char)[0] in "LMN" else "_" for char in name.casefold())
    return re.sub(r"_+", "_", folded).strip("_")


@functools.lru_cache(maxsize=4096)
def _detail_name_problem(name: str, upload: bool = True) -> str | None:
    """Why a column or key (``upload``), or a detail name a shop typed or Shopify holds, cannot be a detail."""
    key = _attribute_key(name)
    if not key or unicodedata.category(key[0])[0] != "L" or len(key) > MAX_ATTRIBUTE_NAME:
        return f"name it with letters, digits and spaces, starting with a letter, up to {MAX_ATTRIBUTE_NAME} characters"
    if upload:
        close = difflib.get_close_matches(key, KNOWN_COLUMNS + tuple(COLUMN_ALIASES), n=1, cutoff=0.85)
        if close:
            return f"looks like a misspelled {close[0]}; use {close[0]}, or rename it if it is a different detail"
    reserved = _reserved_name(key)
    if reserved == "action":
        return "this name is reserved for credentials or actions; rename " + ("the column" if upload else "it")
    if reserved == "private":
        return "looks like private business data that buyers' AI assistants would see; " + (
            "start the column name with _ to keep it out of the catalog, or rename it"
            if upload
            else "leave it out, or rename it"
        )
    return None


def _reserved_name(key: str) -> str | None:
    """``"action"`` for a credential or action name, ``"private"`` for private business data, else None."""
    key = unicodedata.normalize("NFKC", key).casefold()
    compact = key.replace("_", "")
    markers = PRIVATE_KEY_MARKERS + EXECUTION_MARKERS + ("api_key", "access_key")
    if any(marker in key or marker.replace(".", "").replace("_", "") in compact for marker in markers):
        return "action"
    words = {word[:-1] if word.endswith("s") and len(word) > 3 else word for word in key.split("_")}
    if PRIVATE_BUSINESS_WORDS & words or any(stem in compact for stem in PRIVATE_BUSINESS_STEMS):
        return "private"
    return None


def reads_private_or_reserved(name: str) -> bool:
    """True when a name (a Shopify metafield key, a tag) reads as private business data or a credential."""
    return _reserved_name(_attribute_key(name)) is not None


def declared_detail(
    name: str, label: str, value: Any, *, max_value: int = MAX_ATTRIBUTE_VALUE
) -> tuple[dict[str, str] | None, str | None]:
    """A detail declared outside the upload (a shop detail, a Shopify metafield), checked as upload details are.

    ``(detail, None)``; ``(None, None)`` when the value is empty; ``(None, problem)`` when it cannot be published.
    """
    problem = _detail_name_problem(name, False) or (_detail_name_problem(label, False) if label != name else None)
    if problem:
        return None, problem
    if contains_private_or_executable_value(name) or contains_private_or_executable_value(label):
        # The whole product is scanned before it is stored; a name that reads as a credential would fail it.
        return None, "this name looks like a credential or an executable instruction; rename it"
    problems: list[str] = []
    text = _detail_text(value, label, lambda _column, message: problems.append(message), max_value)
    if text is None:
        return None, problems[0] if problems else "cannot be read"
    if not text:
        return None, None
    key = _attribute_key(name)
    return {"name": key, "label": _plain(label)[:MAX_ATTRIBUTE_LABEL] or key, "value": text}, None


# Details that hold for the whole shop (a food-safety licence, a certification), typed once on the setup page.
MAX_SHOP_DETAILS = 10
MAX_SHOP_DETAIL_VALUE = 500


def shop_details_from_entries(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """The shop details a merchant typed as ``{label, value}`` pairs, checked as product details are.

    Rows with neither a name nor a value are ignored. Raises ``CatalogUploadError``
    naming the row and the detail when any row cannot be published.
    """
    errors: list[dict[str, Any]] = []
    details: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        label = _plain(entry.get("label"))
        value = entry.get("value")
        if not label and not _plain(value):
            continue
        if not label:
            errors.append({"row": index, "column": "label", "message": "needs a name"})
            continue
        detail, problem = declared_detail(label, label, value, max_value=MAX_SHOP_DETAIL_VALUE)
        if problem or detail is None:
            errors.append({"row": index, "column": label, "message": problem or "needs a value"})
            continue
        if detail["name"] in seen:
            errors.append({"row": index, "column": label, "message": "repeats another detail"})
            continue
        seen.add(detail["name"])
        details.append(detail)
    if len(details) > MAX_SHOP_DETAILS:
        errors.append({"row": None, "column": None, "message": f"at most {MAX_SHOP_DETAILS} shop details"})
    if errors:
        raise CatalogUploadError(f"{len(errors)} problem(s) in the shop details", errors)
    return details


def shop_details_of(provider_policy: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """The stored shop details (``provider_policy.shop_details``) that still pass the shop-detail checks.

    They are checked again here because a whole-config save can write ``provider_policy`` without the
    setup page's checks; an entry that fails is left out.
    """
    stored = (provider_policy or {}).get("shop_details")
    if not isinstance(stored, Sequence) or isinstance(stored, str | bytes):
        return []
    details: list[dict[str, str]] = []
    for item in stored:
        if not isinstance(item, Mapping) or not isinstance(item.get("label"), str):
            continue
        detail, problem = declared_detail(
            item["label"], item["label"], item.get("value"), max_value=MAX_SHOP_DETAIL_VALUE
        )
        if detail is not None and problem is None and detail["name"] not in {d["name"] for d in details}:
            details.append(detail)
    return details[:MAX_SHOP_DETAILS]


def safe_https_link(value: Any) -> str | None:
    """An https link without a user name or password (host in lower case), else None."""
    if not isinstance(value, str) or contains_private_or_executable_value(value):
        return None
    return _https_url(value, "url", lambda *_args: None)


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
        row = {
            str(key).strip().lower(): value
            for key, value in item.items()
            if str(key).strip() and not str(key).strip().startswith("_")
        }
        row["__conflicts__"] = [(alias, name) for alias, name in COLUMN_ALIASES.items() if alias in row and name in row]
        row["__labels__"] = {str(key).strip().lower(): " ".join(str(key).split()) for key in item}
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
    for alias, name in raw.get("__conflicts__") or ():
        fail(alias, f"use one of {name} or {alias}, not both")
    raw = {COLUMN_ALIASES.get(key, key): value for key, value in raw.items()}
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
    optional = _optional_fields(raw, currency, price, fail)
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
    row.update({key: value for key, value in optional.items() if value})
    if errors:
        return (row if sku else None), errors
    return row, errors


def _optional_fields(raw: Mapping[str, Any], currency: str, price: str | None, fail: Any) -> dict[str, Any]:
    """The optional named fields and the product details of one row; unset ones are left empty."""
    for column in ("brand", "category"):
        if isinstance(raw.get(column), (Mapping, list)):
            fail(column, "must be text")
    brand = _plain(raw.get("brand"))
    if len(brand) > MAX_BRAND:
        fail("brand", f"is longer than {MAX_BRAND} characters")
    category = _plain(raw.get("category"))
    if len(category) > MAX_CATEGORY:
        fail("category", f"is longer than {MAX_CATEGORY} characters")
    compare_at_price = None
    if _text(raw.get("compare_at_price")):
        compare_at_price = _price(
            raw.get("compare_at_price"), currency, lambda _c, message: fail("compare_at_price", message)
        )
    gtin = _gtin(raw.get("gtin"), fail)
    tags = _tags(raw.get("tags"), fail)
    attributes = _attributes(raw, fail)
    for column, value in (("brand", brand), ("category", category)):
        if value and contains_private_or_executable_value(value):
            fail(column, "looks like a credential, a token or an executable instruction; remove it")
    for tag in tags:
        if contains_private_or_executable_value(tag):
            fail("tags", "looks like a credential, a token or an executable instruction; remove it")
    return {
        "brand": brand[:MAX_BRAND] or None,
        "category": category[:MAX_CATEGORY] or None,
        "tags": tags,
        "compare_at_price": compare_at_price,
        "gtin": gtin,
        "attributes": attributes,
    }


def _gtin(value: Any, fail: Any) -> str | None:
    text = _text(value).replace(" ", "")
    if not text:
        return None
    if not text.isdigit() or len(text) not in GTIN_LENGTHS:
        fail("gtin", "must be a barcode number of 8, 12, 13 or 14 digits")
        return None
    digits = [int(char) for char in text]
    total = sum(digit * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(digits[:-1])))
    if (10 - total % 10) % 10 != digits[-1]:
        fail("gtin", "has a wrong check digit; copy the number from the barcode again")
        return None
    return text


def gtin_or_none(value: Any) -> str | None:
    """A barcode number of 8, 12, 13 or 14 digits with a valid check digit, else None."""
    return _gtin(value, lambda *_args: None)


def public_tags(values: Any) -> list[str]:
    """Tags held in another store system (Shopify), kept as upload tags are, leaving out private-looking ones."""
    tags: list[str] = []
    for item in values if isinstance(values, list) else []:
        tag = _plain(item)
        if not tag or len(tag) > MAX_TAG or reads_private_or_reserved(tag):
            continue
        if contains_private_or_executable_value(tag) or tag.lower() in {existing.lower() for existing in tags}:
            continue
        tags.append(tag)
    return tags[:MAX_TAGS]


def _tags(value: Any, fail: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else _TAG_SPLIT_RE.split(_text(value))
    tags: list[str] = []
    for item in items:
        tag = _plain(item)
        if not tag:
            continue
        if len(tag) > MAX_TAG:
            fail("tags", f"each tag can be at most {MAX_TAG} characters")
            continue
        if tag.lower() not in {existing.lower() for existing in tags}:
            tags.append(tag)
    if len(tags) > MAX_TAGS:
        fail("tags", f"at most {MAX_TAGS} tags per product")
    return tags[:MAX_TAGS]


def _attributes(raw: Mapping[str, Any], fail: Any) -> list[dict[str, str]]:
    """Every column that is not a known one, plus a JSON ``attributes`` object, as product details."""
    labels = raw.get("__labels__") or {}
    pairs: list[tuple[str, str, Any]] = []
    for key, value in raw.items():
        if key.startswith("_") or key in KNOWN_COLUMNS or key == "attributes":
            continue
        pairs.append((key, str(labels.get(key) or key), value))
    nested = raw.get("attributes")
    if isinstance(nested, Mapping):
        pairs.extend(
            (str(key).strip().lower(), " ".join(str(key).split()), value)
            for key, value in nested.items()
            if str(key).strip() and not str(key).strip().startswith("_")
        )
    elif nested not in (None, ""):
        fail("attributes", 'must be an object such as {"ingredients": "..."}')
    details: list[dict[str, str]] = []
    seen: set[str] = set()
    for key, label, value in pairs:
        problem = _detail_name_problem(key)
        if problem:
            fail(label, problem)
            continue
        name = _attribute_key(key)
        if name in seen:
            fail(label, "repeats another detail")
            continue
        seen.add(name)
        text = _detail_text(value, label, fail, MAX_ATTRIBUTE_VALUE)
        if not text:
            continue
        details.append({"name": name, "label": _plain(label)[:MAX_ATTRIBUTE_LABEL] or name, "value": text})
    if len(details) > MAX_ATTRIBUTES:
        fail(None, f"has more than {MAX_ATTRIBUTES} product details")
    return details[:MAX_ATTRIBUTES]


def _detail_text(value: Any, label: str, fail: Any, max_value: int) -> str | None:
    """A detail value as checked plain text: "" when empty, None after ``fail`` named the problem."""
    text = _detail_value(value)
    if text is None:
        fail(label, "must be text, a number or a list of them")
        return None
    if not text:
        return ""
    if len(text) > max_value:
        fail(label, f"is longer than {max_value} characters")
        return None
    if text.lower().startswith(("http://", "https://")):
        normalized = _https_url(text, label, fail)
        if normalized is None:
            return None
        text = normalized
    if any(_has_userinfo(match.group(0)) for match in _URL_IN_TEXT_RE.finditer(text)):
        fail(label, "has a link with a user name or password; remove them")
        return None
    if contains_private_or_executable_value(text):
        fail(label, "looks like a credential, a token or an executable instruction; remove it")
        return None
    return text


def _has_userinfo(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return True
    return bool(parts.username or parts.password)


def _detail_value(value: Any) -> str | None:
    """A detail as plain text: strings, numbers and booleans as they are, lists joined with "; "."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, str):
        return _plain(value)
    if isinstance(value, list) and all(
        isinstance(item, (str, int, float)) and not isinstance(item, bool) for item in value
    ):
        return "; ".join(text for item in value if (text := _plain(str(item))))
    return None


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
    # "HTTPS://Shop.Example" is the same address; buyers' assistants are served the lower-case form.
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query, parts.fragment))
