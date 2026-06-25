"""Public-safe OACP catalog publishing helpers.

These helpers turn AgenticOrg-owned Shopify/OACP evidence summaries into
buyer-safe public catalog views. They never publish raw provider payloads,
tokens, private merchant API references, or execution targets.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from html import escape
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from core.commerce.c6z_runtime_vertical import (
    EXECUTION_MARKERS,
    PRIVATE_KEY_MARKERS,
    PRIVATE_VALUE_MARKERS,
    contains_private_or_executable_value,
)

PUBLIC_CATALOG_SURFACES: tuple[str, ...] = (
    "seller_profile_html",
    "catalog_json",
    "product_detail_html",
    "schema_org_jsonld",
    "sitemap_xml",
    "llms_txt",
)

_TAG_RE = re.compile(r"<[^>]+>")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class OacpPublicPublishingError(ValueError):
    """Raised when public publishing input is missing or unsafe."""


def public_catalog_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return str(source.get("OACP_PUBLIC_CATALOG_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}


def build_public_catalog_snapshot(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str | None,
    merchant_display_name: str,
    public_brand_profile: Mapping[str, Any] | None,
    commerce_categories: Sequence[str],
    connector_metadata_redacted: Mapping[str, Any] | None,
    evidence_records: Sequence[Mapping[str, Any]],
    base_url: str = "https://agenticorg.ai",
    public_enabled: bool = True,
) -> dict[str, Any]:
    """Build a public-safe catalog snapshot from stored read-only evidence."""

    if not public_enabled:
        return _blocked_snapshot(
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            reason="public_catalog_disabled",
            message="Public OACP catalog publishing is disabled until the merchant enables public publishing.",
        )
    if not evidence_records:
        return _blocked_snapshot(
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            reason="missing_connector_evidence",
            message="Public OACP catalog publishing requires at least one Shopify read-only evidence snapshot.",
        )

    merchant_name = _safe_text(merchant_display_name, fallback="Seller Commerce Agent")
    brand_profile = _safe_mapping(public_brand_profile or {})
    metadata = _safe_mapping(connector_metadata_redacted or {})
    products = _public_products_from_evidence(evidence_records)
    if not products:
        return _blocked_snapshot(
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            reason="empty_public_catalog",
            message="No public-safe products are available in the latest Shopify evidence snapshot.",
        )

    latest = max(evidence_records, key=lambda item: str(item.get("synced_at") or item.get("source_observed_at") or ""))
    synced_at = _safe_text(latest.get("synced_at") or latest.get("source_observed_at"), fallback="unknown")
    source_observed_at = _safe_text(latest.get("source_observed_at") or synced_at, fallback=synced_at)
    clean_base = _clean_base_url(base_url)
    seller_query = _seller_query(tenant_id=tenant_id, seller_agent_id=seller_agent_id)
    profile_path = f"/api/v1/public/commerce/sellers/{quote(merchant_id)}"
    profile_url = f"{clean_base}{profile_path}?{seller_query}"

    snapshot: dict[str, Any] = {
        "status": "public_catalog_ready",
        "tenant_id": tenant_id,
        "merchant_id": merchant_id,
        "seller_agent_id": seller_agent_id,
        "merchant_display_name": merchant_name,
        "public_brand_profile": brand_profile,
        "commerce_categories": [_safe_text(item) for item in commerce_categories if _safe_text(item)],
        "seller_agent_card": {
            "name": f"{merchant_name} Seller Commerce Agent",
            "agent_id": seller_agent_id,
            "source_of_record": "merchant Shopify Admin API evidence via AgenticOrg read-only connector",
            "grantex_role": "OACP trust, policy, artifact, and verification authority",
            "agenticorg_role": "seller and buyer agent runtime, artifact cache, and public-safe channel bridge",
        },
        "publishing": {
            "surface_names": list(PUBLIC_CATALOG_SURFACES),
            "public_catalog_enabled": True,
            "oacp_public_discovery_certification": "none",
            "external_platform_approval": "not_claimed",
            "allowed_to_execute": False,
            "no_payment_execution": True,
            "non_authoritative_for_transaction": True,
        },
        "source_label": "Source: Shopify via Grantex OACP artifact/evidence",
        "freshness_label": f"Freshness: Shopify snapshot observed at {source_observed_at}; synced at {synced_at}",
        "source_observed_at": source_observed_at,
        "synced_at": synced_at,
        "channel_preferences": _safe_mapping(metadata.get("channel_capability_preferences") or {}),
        "products": products,
        "links": {
            "seller_profile": profile_url,
            "catalog_json": f"{clean_base}{profile_path}/catalog.json?{seller_query}",
            "schema_org_jsonld": f"{clean_base}{profile_path}/schema-org.jsonld?{seller_query}",
            "sitemap_xml": f"{clean_base}{profile_path}/sitemap.xml?{seller_query}",
            "llms_txt": f"{clean_base}{profile_path}/llms.txt?{seller_query}",
        },
    }
    snapshot["schema_org_jsonld"] = build_schema_org_jsonld(snapshot)
    _assert_public_safe(snapshot, "public catalog snapshot")
    return snapshot


def build_schema_org_jsonld(snapshot: Mapping[str, Any], product_slug: str | None = None) -> dict[str, Any]:
    products = list(snapshot.get("products") or [])
    if product_slug:
        products = [product for product in products if product.get("slug") == product_slug]
    graph: list[dict[str, Any]] = [
        {
            "@type": "Organization",
            "@id": f"{snapshot.get('links', {}).get('seller_profile', '')}#organization",
            "name": snapshot.get("merchant_display_name"),
            "url": snapshot.get("links", {}).get("seller_profile"),
        }
    ]
    for product in products:
        offers = []
        for variant in product.get("variants", []):
            offer: dict[str, Any] = {
                "@type": "Offer",
                "sku": variant.get("sku"),
                "price": variant.get("price"),
                "priceCurrency": variant.get("currency"),
                "availability": _schema_availability(variant.get("inventory_quantity_snapshot")),
                "itemCondition": "https://schema.org/NewCondition",
            }
            offers.append({key: value for key, value in offer.items() if value is not None})
        item: dict[str, Any] = {
            "@type": "Product",
            "@id": product.get("public_url"),
            "url": product.get("public_url"),
            "name": product.get("title"),
            "description": product.get("description"),
            "brand": product.get("vendor"),
            "category": product.get("product_type"),
            "image": [image.get("url") for image in product.get("images", []) if image.get("url")],
            "offers": offers,
        }
        graph.append({key: value for key, value in item.items() if value not in (None, "", [])})
    return {
        "@context": "https://schema.org",
        "@graph": graph,
        "oacp_source_label": snapshot.get("source_label"),
        "oacp_freshness_label": snapshot.get("freshness_label"),
        "oacp_certification_status": "compatibility_mapping_only_not_external_certification",
        "allowed_to_execute": False,
        "no_payment_execution": True,
    }


def build_public_catalog_html(snapshot: Mapping[str, Any], product_slug: str | None = None) -> str:
    product = find_public_product(snapshot, product_slug) if product_slug else None
    title = product.get("title") if product else snapshot.get("merchant_display_name")
    jsonld = build_schema_org_jsonld(snapshot, product_slug=product_slug)
    jsonld_text = json.dumps(jsonld, separators=(",", ":")).replace("</", "<\\/")
    meta_description = (
        "Public-safe OACP catalog output with source and freshness labels. No checkout, payment, mandate, "
        "order, or inventory hold execution."
    )
    cards = [_product_card(product)] if product else [_product_card(item) for item in snapshot.get("products", [])]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(str(title))} | OACP public-safe catalog</title>
  <meta name="description" content="{escape(meta_description)}" />
  <script type="application/ld+json">{jsonld_text}</script>
  <style>
    :root {{
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    body {{ margin: 0; background: #f7faf9; color: #10201b; }}
    header {{ background: #0f241f; color: #f4fffb; padding: 44px 24px; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 28px 20px 56px; }}
    .eyebrow {{ color: #7be0c4; font-weight: 700; font-size: 13px; letter-spacing: .08em; text-transform: uppercase; }}
    h1 {{ margin: 10px 0 12px; font-size: clamp(32px, 6vw, 54px); line-height: 1.02; letter-spacing: 0; }}
    .labels {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; }}
    .chip {{
      border: 1px solid #b9d7cd;
      border-radius: 999px;
      padding: 8px 12px;
      background: #fff;
      font-size: 13px;
      color: #234239;
    }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 16px; margin-top: 24px; }}
    article {{
      background: #fff;
      border: 1px solid #d9e7e1;
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 8px 26px rgba(16,32,27,.07);
    }}
    article img {{ width: 100%; aspect-ratio: 4/3; object-fit: cover; background: #e8f2ee; display: block; }}
    .body {{ padding: 16px; }}
    h2 {{ margin: 0 0 8px; font-size: 20px; letter-spacing: 0; }}
    .meta {{ margin: 0 0 10px; color: #4c665d; font-size: 14px; }}
    .variant {{ border-top: 1px solid #e3eee9; padding-top: 10px; margin-top: 10px; font-size: 14px; }}
    .notice {{
      background: #fff7dc;
      border: 1px solid #ead38a;
      border-radius: 8px;
      padding: 14px;
      margin-top: 24px;
      color: #4e4215;
    }}
    a {{ color: #146b57; }}
  </style>
</head>
<body>
  <header>
    <div class="eyebrow">AgenticOrg Seller Commerce Agent</div>
    <h1>{escape(str(title))}</h1>
    <p>{escape(str(snapshot.get("seller_agent_card", {}).get("source_of_record", "")))}</p>
  </header>
  <main>
    <div class="labels">
      <span class="chip">{escape(str(snapshot.get("source_label")))}</span>
      <span class="chip">{escape(str(snapshot.get("freshness_label")))}</span>
      <span class="chip">Compatibility mapping only; no certification claim</span>
      <span class="chip">No checkout or payment execution</span>
    </div>
    <section class="grid">{"".join(cards)}</section>
    <section class="notice">
      Merchant systems remain the operational source of record. Price, inventory, delivery, tax, warranty,
      return, mandate, payment, and order commitments require fresh source confirmation and provider-owned execution.
    </section>
  </main>
</body>
</html>"""


def build_public_sitemap_xml(snapshot: Mapping[str, Any]) -> str:
    urls = [snapshot.get("links", {}).get("seller_profile")]
    urls.extend(product.get("public_url") for product in snapshot.get("products", []) if product.get("public_url"))
    body = "".join(f"<url><loc>{escape(str(url))}</loc></url>" for url in urls if url)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'


def build_public_llms_txt(snapshot: Mapping[str, Any]) -> str:
    lines = [
        f"# {snapshot.get('merchant_display_name')} OACP Public-Safe Catalog",
        "",
        str(snapshot.get("source_label")),
        str(snapshot.get("freshness_label")),
        "",
        "This catalog is generated from AgenticOrg cached Shopify/OACP evidence.",
        (
            "It is not a checkout, payment, order, mandate, inventory-hold, refund, shipping, certification, "
            "or external-standardization claim."
        ),
        "",
        "## Products",
    ]
    for product in snapshot.get("products", []):
        prices = ", ".join(
            _variant_price_line(variant)
            for variant in product.get("variants", [])
        )
        lines.append(f"- {product.get('title')} ({product.get('public_url')}): {prices}")
    return "\n".join(lines).strip() + "\n"


def find_public_product(snapshot: Mapping[str, Any], product_slug: str | None) -> dict[str, Any] | None:
    if not product_slug:
        return None
    for product in snapshot.get("products", []):
        if product.get("slug") == product_slug or product.get("product_ref") == product_slug:
            return dict(product)
    return None


def _public_products_from_evidence(evidence_records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    products_by_ref: dict[str, dict[str, Any]] = {}
    for evidence in evidence_records:
        base_url = _clean_base_url(str(evidence.get("base_url") or "https://agenticorg.ai"))
        tenant_id = str(evidence.get("tenant_id") or "")
        merchant_id = str(evidence.get("merchant_id") or "")
        seller_agent_id = str(evidence.get("seller_agent_id") or "")
        query = _seller_query(tenant_id=tenant_id, seller_agent_id=seller_agent_id or None)
        for raw_product in evidence.get("products") or []:
            if not isinstance(raw_product, Mapping):
                continue
            product = _public_product(raw_product, base_url=base_url, merchant_id=merchant_id, query=query)
            products_by_ref[product["product_ref"]] = product
    return sorted(products_by_ref.values(), key=lambda item: str(item.get("title", "")).lower())


def _public_product(product: Mapping[str, Any], *, base_url: str, merchant_id: str, query: str) -> dict[str, Any]:
    title = _safe_text(product.get("title"), fallback="Untitled product")
    product_ref = _safe_text(product.get("product_ref"), fallback=_public_ref("product", title))
    slug = _slug(title, product_ref)
    public_url = f"{base_url}/api/v1/public/commerce/sellers/{quote(merchant_id)}/products/{quote(slug)}?{query}"
    variants = []
    for raw_variant in product.get("variants") or []:
        if not isinstance(raw_variant, Mapping):
            continue
        variants.append(
            {
                "variant_ref": _public_ref("variant", raw_variant.get("variant_id") or raw_variant.get("sku") or title),
                "sku": _safe_optional(raw_variant.get("sku")),
                "title": _safe_text(raw_variant.get("title"), fallback="Default"),
                "price": _safe_optional(raw_variant.get("price")),
                "compare_at_price": _safe_optional(raw_variant.get("compare_at_price")),
                "currency": _safe_optional(raw_variant.get("currency")),
                "inventory_quantity_snapshot": _safe_int(raw_variant.get("inventory_quantity_snapshot")),
                "inventory_label": _inventory_label(raw_variant.get("inventory_quantity_snapshot")),
                "selected_options": [
                    {"name": _safe_optional(option.get("name")), "value": _safe_optional(option.get("value"))}
                    for option in raw_variant.get("selected_options") or []
                    if isinstance(option, Mapping)
                ],
            }
        )
    images = [
        {"url": _safe_text(image.get("url")), "alt_text": _safe_optional(image.get("alt_text"))}
        for image in product.get("images") or []
        if isinstance(image, Mapping) and _safe_text(image.get("url"))
    ][:8]
    public_product = {
        "product_ref": product_ref,
        "slug": slug,
        "public_url": public_url,
        "title": title,
        "description": _clean_description(product.get("description")),
        "vendor": _safe_optional(product.get("vendor")),
        "product_type": _safe_optional(product.get("product_type")),
        "status": _safe_optional(product.get("status")),
        "images": images,
        "variants": variants,
        "updated_at": _safe_optional(product.get("updated_at")),
        "synced_at": _safe_optional(product.get("synced_at")),
    }
    _assert_public_safe(public_product, "public product")
    return public_product


def _product_card(product: Mapping[str, Any]) -> str:
    image: Mapping[str, Any] = next(iter(product.get("images") or []), {})
    image_src = escape(str(image.get("url")))
    image_alt = escape(str(image.get("alt_text") or product.get("title")))
    product_title = escape(str(product.get("title")))
    vendor = escape(str(product.get("vendor") or "Merchant source"))
    product_type = escape(str(product.get("product_type") or "Catalog item"))
    image_html = (
        f'<img src="{image_src}" alt="{image_alt}" />'
        if image.get("url")
        else ""
    )
    variants = "".join(
        "<div class=\"variant\">"
        f"<strong>{escape(str(variant.get('title') or 'Default'))}</strong><br />"
        f"SKU {escape(str(variant.get('sku') or variant.get('variant_ref')))}<br />"
        f"{escape(str(variant.get('price') or 'price unavailable'))} {escape(str(variant.get('currency') or ''))}<br />"
        f"{escape(str(variant.get('inventory_label') or 'Inventory snapshot unavailable'))}"
        "</div>"
        for variant in product.get("variants", [])[:5]
    )
    return (
        "<article>"
        f"{image_html}"
        '<div class="body">'
        f"<h2><a href=\"{escape(str(product.get('public_url')))}\">{product_title}</a></h2>"
        f'<p class="meta">{vendor} | {product_type}</p>'
        f"<p>{escape(str(product.get('description') or 'No public description supplied.'))}</p>"
        f"{variants}"
        "</div></article>"
    )


def _variant_price_line(variant: Mapping[str, Any]) -> str:
    identity = variant.get("sku") or variant.get("variant_ref")
    price = variant.get("price")
    currency = variant.get("currency") or ""
    return f"{identity}: {price} {currency}".strip()


def _blocked_snapshot(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str | None,
    reason: str,
    message: str,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "message": message,
        "tenant_id": tenant_id,
        "merchant_id": merchant_id,
        "seller_agent_id": seller_agent_id,
        "products": [],
        "allowed_to_execute": False,
        "no_payment_execution": True,
        "non_authoritative_for_transaction": True,
    }


def _safe_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, item in value.items():
        clean_key = _safe_text(key)
        if isinstance(item, str | int | float | bool) or item is None:
            clean[clean_key] = item
        elif isinstance(item, Sequence) and not isinstance(item, str | bytes | bytearray):
            clean[clean_key] = [_safe_text(element) for element in item if _safe_text(element)]
        elif isinstance(item, Mapping):
            clean[clean_key] = _safe_mapping(item)
    if contains_private_or_executable_value(clean):
        raise OacpPublicPublishingError("public metadata contains private or executable values")
    return clean


def _clean_description(value: Any) -> str | None:
    text = _safe_optional(value)
    if text is None:
        return None
    return _TAG_RE.sub(" ", text).replace("&nbsp;", " ").strip()[:600] or None


def _safe_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text[:500] if text else fallback


def _safe_optional(value: Any) -> str | None:
    text = _safe_text(value)
    return text or None


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _public_ref(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:18]
    return f"{prefix}:public:{digest}"


def _slug(title: str, product_ref: str) -> str:
    base = _SLUG_RE.sub("-", title.lower()).strip("-") or "product"
    suffix = hashlib.sha256(product_ref.encode("utf-8")).hexdigest()[:8]
    return f"{base[:72].strip('-')}-{suffix}"


def _clean_base_url(value: str) -> str:
    text = value.strip().rstrip("/") or "https://agenticorg.ai"
    if not text.startswith(("https://", "http://")):
        text = f"https://{text}"
    return text


def _seller_query(*, tenant_id: str, seller_agent_id: str | None) -> str:
    params = {"tenant_id": tenant_id}
    if seller_agent_id:
        params["seller_agent_id"] = seller_agent_id
    return urlencode(params)


def _inventory_label(value: Any) -> str:
    quantity = _safe_int(value)
    if quantity is None:
        return "Inventory snapshot unavailable"
    if quantity <= 0:
        return "Inventory snapshot: out of stock"
    return f"Inventory snapshot: {quantity}"


def _schema_availability(value: Any) -> str:
    quantity = _safe_int(value)
    if quantity is None:
        return "https://schema.org/LimitedAvailability"
    return "https://schema.org/InStock" if quantity > 0 else "https://schema.org/OutOfStock"


def _assert_public_safe(value: Any, label: str) -> None:
    if _contains_public_unsafe_value(value):
        raise OacpPublicPublishingError(f"{label} contains private or executable values")


def _contains_public_unsafe_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).lower()
            if _text_has_marker(key_text, PRIVATE_KEY_MARKERS) or _text_has_marker(key_text, EXECUTION_MARKERS):
                return True
            if _contains_public_unsafe_value(item):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(_contains_public_unsafe_value(item) for item in value)
    if isinstance(value, str):
        text = value.strip()
        if _looks_like_public_url(text):
            return _public_url_has_unsafe_markers(text)
        return contains_private_or_executable_value(text)
    return False


def _looks_like_public_url(value: str) -> bool:
    return value.startswith(("https://", "http://"))


def _public_url_has_unsafe_markers(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return True
    text = value.lower()
    if _text_has_marker(text, PRIVATE_VALUE_MARKERS) or _text_has_marker(text, EXECUTION_MARKERS):
        return True
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        key_text = key.lower()
        item_text = item.lower()
        if _text_has_marker(key_text, PRIVATE_KEY_MARKERS) or _text_has_marker(key_text, EXECUTION_MARKERS):
            return True
        if _text_has_marker(item_text, PRIVATE_VALUE_MARKERS) or _text_has_marker(item_text, EXECUTION_MARKERS):
            return True
    return False


def _text_has_marker(value: str, markers: Sequence[str]) -> bool:
    return any(marker in value for marker in markers)
