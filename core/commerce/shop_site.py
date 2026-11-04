# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""A network shop's own small storefront, served on its web address (pure rendering, no I/O).

When a request reaches a listed network shop's serving host (its platform subdomain or its own
custom domain, ``api/middleware/merchant_host.py``), these pages are served there:

* ``/`` - the shop's name and its products;
* ``/products/<handle>`` - one product: price, stock, images, description, the shop's shipping
  and returns summary, how to buy, and schema.org ``Product`` JSON-LD (with ``gtin`` and
  ``shippingDetails`` where known). These pages are the landing pages the product feeds link to;
* ``/sitemap.xml``, ``/robots.txt`` and ``/llms.txt``;
* ``/feeds/google.xml`` - the product feed (``core.commerce.shop_feed``).

The products are the shop's catalog as buyer agents see it at ``/ucp/catalog/search``: the newest
snapshot of every catalog source (a Shopify read or an upload), with the shop's source precedence
applied, served only while Grantex's catalog, price and stock artifacts verify. Every catalog and
shop text is escaped for its context here (HTML text and attributes, JSON inside a script element,
XML, and plain text with markdown-significant characters escaped in ``llms.txt``).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from html import escape
from typing import Any
from urllib.parse import quote

from core.commerce.catalog_upload import currency_exponent
from core.commerce.ucp.shopify_reads import DeliveryRate

MCP_SERVER_URL = "https://mcp.agenticorg.ai/mcp"
NETWORK_SITE = "https://agenticorg.ai"
MAX_HOME_PRODUCTS = 200
MAX_SITEMAP_PRODUCTS = 5000
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CONDITIONS = frozenset({"new", "used", "refurbished"})
# A page has no script and loads nothing but https images; the JSON-LD block is data, not script.
PAGE_CSP = (
    "default-src 'none'; img-src https:; style-src 'unsafe-inline'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)


@dataclass(frozen=True)
class SiteVariant:
    id: str
    title: str
    sku: str | None
    amount: int
    currency: str
    list_amount: int | None
    available: bool | None
    gtin: str | None
    options: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SiteProduct:
    id: str
    handle: str
    title: str
    description: str
    brand: str | None
    category: str | None
    condition: str
    images: tuple[tuple[str, str | None], ...]
    shop_url: str | None
    details: tuple[tuple[str, str], ...]
    variants: tuple[SiteVariant, ...]

    def variant(self, variant_key: str | None) -> SiteVariant | None:
        if not variant_key:
            return None
        return next((v for v in self.variants if variant_key_of(v) == variant_key), None)


@dataclass(frozen=True)
class ShopSite:
    slug: str
    name: str
    host: str
    catalog_verified: bool
    products: tuple[SiteProduct, ...] = ()
    rates: tuple[DeliveryRate, ...] = ()
    returns_policy: str | None = None
    shipping_policy: str | None = None
    pickup_policy: str | None = None
    shop_details: tuple[tuple[str, str], ...] = ()
    telegram_link: str | None = None
    categories: tuple[str, ...] = field(default_factory=tuple)

    @property
    def base_url(self) -> str:
        return f"https://{self.host}"

    def product(self, handle: str) -> SiteProduct | None:
        return next((p for p in self.products if p.handle == handle), None)


# --- text helpers ---------------------------------------------------------------------------


def clean(value: Any, limit: int = 5000) -> str:
    """Plain single-spaced text without control characters (catalog text was tag-stripped upstream)."""
    if not isinstance(value, str):
        return ""
    text = re.sub(r"\s+", " ", _CONTROL_RE.sub("", value)).strip()
    return text[:limit]


def h(value: Any) -> str:
    """HTML text or a double-quoted attribute value."""
    return escape(clean(value) if isinstance(value, str) else str(value), quote=True)


def script_json(document: Any) -> str:
    """JSON safe inside ``<script type="application/ld+json">``: no ``<``, ``>`` or ``&`` survive."""
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    return (
        text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def md(value: Any, limit: int = 300) -> str:
    """One line of llms.txt text with markdown link and emphasis characters escaped."""
    return re.sub(r"([\\`*_\[\]()<>#|])", r"\\\1", clean(value, limit))


def money(amount: int, currency: str) -> str:
    """Integer minor units as decimal text in major units, for example ``44900, INR`` -> ``449.00``."""
    exponent = currency_exponent(currency)
    value = Decimal(amount).scaleb(-exponent)
    return f"{value:.{exponent}f}"


def price_label(amount: int, currency: str) -> str:
    return f"{currency} {money(amount, currency)}"


def handle_for(product_id: str, title: str) -> str:
    """A stable path segment: the title's words and a short digest of the product id."""
    ascii_title = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:60].strip("-") or "product"
    return f"{words}-{hashlib.sha256(product_id.encode()).hexdigest()[:10]}"


def variant_key_of(variant: SiteVariant) -> str:
    """A short, stable key for a variant (the ``?variant=`` query value of a landing URL)."""
    return hashlib.sha256(variant.id.encode()).hexdigest()[:12]


def product_url(site: ShopSite, product: SiteProduct, variant: SiteVariant | None = None) -> str:
    url = f"{site.base_url}/products/{quote(product.handle, safe='-')}"
    if variant is not None and len(product.variants) > 1:
        url += f"?variant={variant_key_of(variant)}"
    return url


def feed_url(site: ShopSite) -> str:
    return f"{site.base_url}/feeds/google.xml"


# --- building the site's products from UCP catalog products ---------------------------------


def _condition(details: Sequence[tuple[str, str]]) -> str:
    for name, value in details:
        if name.strip().lower() == "condition" and value.strip().lower() in _CONDITIONS:
            return value.strip().lower()
    return "new"


def site_product(product: Mapping[str, Any]) -> SiteProduct | None:
    """One UCP catalog product (``core.commerce.ucp.catalog.ucp_product``) as a storefront product."""
    product_id = product.get("id")
    if not isinstance(product_id, str) or not product_id:
        return None
    variants: list[SiteVariant] = []
    for item in product.get("variants") or ():
        if not isinstance(item, Mapping) or not isinstance(item.get("price"), Mapping):
            continue
        amount = item["price"].get("amount")
        currency = item["price"].get("currency")
        if isinstance(amount, bool) or not isinstance(amount, int) or not isinstance(currency, str):
            continue
        list_price = item.get("list_price") if isinstance(item.get("list_price"), Mapping) else {}
        list_amount = list_price.get("amount") if isinstance(list_price.get("amount"), int) else None
        availability = item.get("availability") if isinstance(item.get("availability"), Mapping) else {}
        available = availability.get("available") if isinstance(availability.get("available"), bool) else None
        gtin = next(
            (
                str(code.get("value"))
                for code in item.get("barcodes") or ()
                if isinstance(code, Mapping) and code.get("type") == "GTIN" and code.get("value")
            ),
            None,
        )
        options = tuple(
            (clean(option.get("name"), 120), clean(option.get("label"), 120))
            for option in item.get("options") or ()
            if isinstance(option, Mapping)
        )
        variants.append(
            SiteVariant(
                id=str(item.get("id") or ""),
                title=clean(item.get("title"), 255) or "Default",
                sku=clean(item.get("sku"), 255) or None,
                amount=amount,
                currency=currency,
                list_amount=list_amount if list_amount is not None and list_amount > amount else None,
                available=available,
                gtin=gtin,
                options=options,
            )
        )
    if not variants:
        return None
    title = clean(product.get("title"), 255) or "Untitled product"
    metadata = product.get("metadata") if isinstance(product.get("metadata"), Mapping) else {}
    details = tuple(
        (clean(item.get("name"), 60), clean(item.get("value"), 1000))
        for item in metadata.get("attributes") or ()
        if isinstance(item, Mapping) and clean(item.get("name")) and clean(item.get("value"))
    )
    description = product.get("description") if isinstance(product.get("description"), Mapping) else {}
    categories = product.get("categories") or ()
    category = next((clean(c.get("value"), 255) for c in categories if isinstance(c, Mapping)), None)
    images = tuple(
        (str(media["url"]), clean(media.get("alt_text"), 255) or None)
        for media in product.get("media") or ()
        if isinstance(media, Mapping)
        and media.get("type") == "image"
        and str(media.get("url", "")).startswith("https://")
    )
    shop_url = product.get("url")
    return SiteProduct(
        id=product_id,
        handle=handle_for(product_id, title),
        title=title,
        description=clean(description.get("plain"), 5000) or title,
        brand=clean(metadata.get("brand"), 70) or None,
        category=category or None,
        condition=_condition(details),
        images=images,
        shop_url=shop_url if isinstance(shop_url, str) and shop_url.startswith("https://") else None,
        details=details,
        variants=tuple(variants),
    )


def site_products(products: Sequence[Mapping[str, Any]]) -> tuple[SiteProduct, ...]:
    out: list[SiteProduct] = []
    seen: set[str] = set()
    for product in products:
        item = site_product(product)
        if item is not None and item.handle not in seen:
            seen.add(item.handle)
            out.append(item)
    return tuple(out)


# --- schema.org JSON-LD ---------------------------------------------------------------------

_AVAILABILITY = {True: "https://schema.org/InStock", False: "https://schema.org/OutOfStock"}
_CONDITION_URL = {
    "new": "https://schema.org/NewCondition",
    "used": "https://schema.org/UsedCondition",
    "refurbished": "https://schema.org/RefurbishedCondition",
}


def shipping_details(site: ShopSite, currency: str) -> list[dict[str, Any]]:
    """schema.org ``OfferShippingDetails`` for the shop's own flat rates in the product's currency."""
    out: list[dict[str, Any]] = []
    for rate in site.rates:
        if rate.currency != currency or not rate.countries:
            continue
        out.append(
            {
                "@type": "OfferShippingDetails",
                "shippingRate": {
                    "@type": "MonetaryAmount",
                    "value": money(rate.amount_minor_units, rate.currency),
                    "currency": rate.currency,
                },
                "shippingDestination": [
                    {"@type": "DefinedRegion", "addressCountry": country} for country in rate.countries
                ],
            }
        )
    return out


def _offer(site: ShopSite, product: SiteProduct, variant: SiteVariant) -> dict[str, Any]:
    offer: dict[str, Any] = {
        "@type": "Offer",
        "url": product_url(site, product, variant),
        "price": money(variant.amount, variant.currency),
        "priceCurrency": variant.currency,
        "itemCondition": _CONDITION_URL[product.condition],
        "seller": {"@type": "Organization", "name": site.name},
    }
    if variant.available is not None:
        offer["availability"] = _AVAILABILITY[variant.available]
    shipping = shipping_details(site, variant.currency)
    if shipping:
        offer["shippingDetails"] = shipping
    return offer


def _product_node(site: ShopSite, product: SiteProduct, variant: SiteVariant, *, name: str) -> dict[str, Any]:
    node: dict[str, Any] = {
        "@type": "Product",
        "name": name,
        "description": product.description,
        "url": product_url(site, product, variant),
        "offers": _offer(site, product, variant),
    }
    if product.images:
        node["image"] = [url for url, _alt in product.images[:10]]
    if variant.sku:
        node["sku"] = variant.sku
    if variant.gtin:
        node["gtin"] = variant.gtin
    if product.brand:
        node["brand"] = {"@type": "Brand", "name": product.brand}
    if product.category:
        node["category"] = product.category
    return node


def product_jsonld(site: ShopSite, product: SiteProduct) -> dict[str, Any]:
    """schema.org ``Product`` (one variant) or ``ProductGroup`` with ``hasVariant`` (several)."""
    if len(product.variants) == 1:
        node = _product_node(site, product, product.variants[0], name=product.title)
        return {"@context": "https://schema.org", **node}
    group: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "ProductGroup",
        "name": product.title,
        "description": product.description,
        "url": product_url(site, product),
        "productGroupID": product.handle,
        "hasVariant": [
            _product_node(site, product, variant, name=f"{product.title} - {variant.title}")
            for variant in product.variants
        ],
    }
    if product.brand:
        group["brand"] = {"@type": "Brand", "name": product.brand}
    if product.images:
        group["image"] = [url for url, _alt in product.images[:10]]
    return group


# --- HTML -----------------------------------------------------------------------------------

_STYLE = """
body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#0f172a;background:#fff;
line-height:1.5}
header,main,footer{max-width:960px;margin:0 auto;padding:16px}
header{border-bottom:1px solid #e2e8f0}
a{color:#1d4ed8}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:16px;padding:0;list-style:none}
.card{border:1px solid #e2e8f0;border-radius:8px;padding:12px}
.card img,.gallery img{max-width:100%;height:auto;border-radius:6px}
.gallery{display:flex;flex-wrap:wrap;gap:8px}
.gallery img{max-height:320px}
.price{font-weight:600}
.was{text-decoration:line-through;color:#64748b;margin-left:6px}
.muted{color:#475569;font-size:.9rem}
table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid #e2e8f0;padding:6px;text-align:left;vertical-align:top}
tr.selected{background:#eff6ff}
footer{border-top:1px solid #e2e8f0;color:#475569;font-size:.9rem}
""".strip()


def _page(site: ShopSite, *, title: str, description: str, canonical: str, body: str, jsonld: Any = None) -> str:
    ld = f'\n<script type="application/ld+json">{script_json(jsonld)}</script>' if jsonld is not None else ""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{h(title)}</title>\n"
        f'<meta name="description" content="{h(clean(description, 300))}">\n'
        f'<link rel="canonical" href="{h(canonical)}">\n'
        f"<style>{_STYLE}</style>{ld}\n</head>\n<body>\n"
        f'<header><a href="{h(site.base_url)}/"><strong>{h(site.name)}</strong></a></header>\n'
        f"<main>\n{body}\n</main>\n{_footer(site)}\n</body>\n</html>\n"
    )


def _footer(site: ShopSite) -> str:
    return (
        "<footer>"
        f"<p>{h(site.name)} is the seller of every product on this site and takes payment itself. "
        f'The shop is listed on the <a href="{NETWORK_SITE}/">AgenticOrg</a> shopping network.</p>'
        f'<p><a href="{h(site.base_url)}/sitemap.xml">Sitemap</a> · '
        f'<a href="{h(site.base_url)}/llms.txt">llms.txt</a> · '
        f'<a href="{h(site.base_url)}/.well-known/ucp">Shop profile for AI agents</a> · '
        f'<a href="{NETWORK_SITE}/privacy">Privacy</a> · '
        f'<a href="{NETWORK_SITE}/network-terms">Network terms</a></p>'
        "</footer>"
    )


def _price_html(variants: Sequence[SiteVariant]) -> str:
    amounts = sorted({v.amount for v in variants})
    currency = variants[0].currency
    if len(amounts) == 1:
        variant = variants[0]
        was = (
            f'<span class="was">{h(price_label(variant.list_amount, currency))}</span>'
            if variant.list_amount is not None and len(variants) == 1
            else ""
        )
        return f'<span class="price">{h(price_label(amounts[0], currency))}</span>{was}'
    return (
        f'<span class="price">{h(price_label(amounts[0], currency))} - {h(price_label(amounts[-1], currency))}</span>'
    )


def _stock_label(variants: Sequence[SiteVariant]) -> str:
    states = {v.available for v in variants}
    if True in states:
        return "In stock"
    if states == {False}:
        return "Out of stock"
    return "Ask the shop about stock"


def shipping_lines(site: ShopSite) -> list[str]:
    lines = []
    for rate in site.rates:
        where = ", ".join(rate.countries) if rate.countries else "any country the shop ships to"
        fee = "free" if rate.amount_minor_units == 0 else price_label(rate.amount_minor_units, rate.currency)
        lines.append(f"{clean(rate.title, 120)}: {fee} to {where}")
    return lines


def _policies_html(site: ShopSite) -> str:
    items = [f"<li>{h(line)}</li>" for line in shipping_lines(site)]
    if site.shipping_policy:
        items.append(f"<li>Shipping: {h(site.shipping_policy)}</li>")
    if site.pickup_policy:
        items.append(f"<li>Store pickup: {h(site.pickup_policy)}</li>")
    if site.returns_policy:
        items.append(f"<li>Returns: {h(site.returns_policy)}</li>")
    if not items:
        items.append("<li>Ask the shop about shipping and returns before you buy.</li>")
    return (
        '<section aria-label="Shipping and returns"><h2>Shipping and returns</h2><ul>'
        + "".join(items)
        + "</ul></section>"
    )


def _how_to_buy_html(site: ShopSite, product: SiteProduct | None = None) -> str:
    items = []
    if product is not None and product.shop_url:
        items.append(
            f'<li><a href="{h(product.shop_url)}" rel="nofollow noopener">Buy on {h(site.name)}\'s own store</a></li>'
        )
    if site.telegram_link:
        items.append(
            f'<li><a href="{h(site.telegram_link)}" rel="nofollow noopener">Shop {h(site.name)} on Telegram</a>: '
            "the AgenticOrg shopping bot finds the product and opens a checkout.</li>"
        )
    items.append(
        "<li>In ChatGPT, Claude or another AI app that takes MCP servers, add "
        f"<code>{MCP_SERVER_URL}</code> and ask for {h(product.title if product else site.name)}.</li>"
    )
    return (
        '<section aria-label="How to buy"><h2>How to buy</h2><ul>'
        + "".join(items)
        + f'</ul><p class="muted">You pay on {h(site.name)}\'s own payment page; AgenticOrg never takes payment.</p>'
        "</section>"
    )


def _details_html(site: ShopSite, product: SiteProduct) -> str:
    rows = list(product.details) + list(site.shop_details)
    if not rows:
        return ""
    body = "".join(f'<tr><th scope="row">{h(name)}</th><td>{h(value)}</td></tr>' for name, value in rows)
    return f'<section aria-label="Product details"><h2>Details</h2><table>{body}</table></section>'


def render_home(site: ShopSite) -> str:
    cards = []
    for product in site.products[:MAX_HOME_PRODUCTS]:
        image = (
            f'<img src="{h(product.images[0][0])}" alt="{h(product.images[0][1] or product.title)}" loading="lazy">'
            if product.images
            else ""
        )
        cards.append(
            f'<li class="card">{image}<h2><a href="{h(product_url(site, product))}">{h(product.title)}</a></h2>'
            f'<p>{_price_html(product.variants)}</p><p class="muted">{h(_stock_label(product.variants))}</p></li>'
        )
    listing = f'<ul class="grid">{"".join(cards)}</ul>' if cards else "<p>This shop has no products to show yet.</p>"
    intro = f"Products from {site.name}, a shop on the AgenticOrg shopping network."
    body = f"<h1>{h(site.name)}</h1>\n<p>{h(intro)}</p>\n{listing}\n{_policies_html(site)}\n{_how_to_buy_html(site)}"
    jsonld = {
        "@context": "https://schema.org",
        "@type": "OnlineStore",
        "name": site.name,
        "url": f"{site.base_url}/",
    }
    return _page(site, title=site.name, description=intro, canonical=f"{site.base_url}/", body=body, jsonld=jsonld)


def render_product(site: ShopSite, product: SiteProduct, variant_key: str | None = None) -> str:
    selected = product.variant(variant_key)
    shown = [selected] if selected is not None else list(product.variants)
    gallery = "".join(f'<img src="{h(url)}" alt="{h(alt or product.title)}">' for url, alt in product.images[:10])
    facts = []
    if product.brand:
        facts.append(f"Brand: {h(product.brand)}")
    if product.category:
        facts.append(f"Category: {h(product.category)}")
    facts.append(f"Condition: {h(product.condition)}")
    gtin = next((v.gtin for v in shown if v.gtin), None)
    if gtin and len(shown) == 1:
        facts.append(f"GTIN: {h(gtin)}")
    variants_html = ""
    if len(product.variants) > 1:
        rows = []
        for variant in product.variants:
            stock = {True: "In stock", False: "Out of stock", None: "Ask the shop"}[variant.available]
            css = ' class="selected"' if selected is not None and variant is selected else ""
            was = (
                f'<span class="was">{h(price_label(variant.list_amount, variant.currency))}</span>'
                if variant.list_amount is not None
                else ""
            )
            rows.append(
                f'<tr{css}><td><a href="{h(product_url(site, product, variant))}">{h(variant.title)}</a></td>'
                f'<td><span class="price">{h(price_label(variant.amount, variant.currency))}</span>{was}</td>'
                f"<td>{h(stock)}</td><td>{h(variant.sku or '')}</td></tr>"
            )
        variants_html = (
            '<section aria-label="Options"><h2>Options</h2><table><tr><th>Option</th><th>Price</th>'
            f"<th>Stock</th><th>SKU</th></tr>{''.join(rows)}</table></section>"
        )
    body = (
        f'<p><a href="{h(site.base_url)}/">{h(site.name)}</a></p>\n'
        f"<h1>{h(product.title)}</h1>\n"
        + (f'<div class="gallery">{gallery}</div>\n' if gallery else "")
        + f"<p>{_price_html(shown)}</p>\n<p>{h(_stock_label(shown))}</p>\n"
        + f'<p class="muted">{" · ".join(facts)}</p>\n'
        + f"<p>{h(product.description)}</p>\n"
        + variants_html
        + _details_html(site, product)
        + _policies_html(site)
        + _how_to_buy_html(site, product)
    )
    title = product.title if selected is None else f"{product.title} - {selected.title}"
    return _page(
        site,
        title=f"{title} | {site.name}",
        description=product.description,
        canonical=product_url(site, product, selected),
        body=body,
        jsonld=product_jsonld(site, product),
    )


def render_unavailable(site_name: str) -> str:
    """The page while the shop's verified catalog is being refreshed (sent with 503 and Retry-After)."""
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>'
        f"{h(site_name)}</title></head><body><h1>{h(site_name)}</h1>"
        "<p>This shop's catalog is being refreshed. Please try again in a few minutes.</p></body></html>\n"
    )


# --- sitemap, robots, llms.txt --------------------------------------------------------------


def _xml(value: str) -> str:
    return escape(_CONTROL_RE.sub("", value), quote=True)


def render_sitemap(site: ShopSite) -> str:
    urls = [f"{site.base_url}/"] + [product_url(site, p) for p in site.products[:MAX_SITEMAP_PRODUCTS]]
    body = "".join(f"<url><loc>{_xml(url)}</loc></url>" for url in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>\n'
    )


def render_robots(host: str) -> str:
    return f"User-agent: *\nAllow: /\nDisallow: /ucp/\nDisallow: /acp/\nSitemap: https://{host}/sitemap.xml\n"


def render_llms(site: ShopSite) -> str:
    lines = [
        f"# {md(site.name, 200)}",
        "",
        f"> {md(site.name, 200)} sells on the AgenticOrg shopping network. The shop is the seller and takes "
        "payment on its own payment page.",
        "",
        "## Products",
        "",
    ]
    for product in site.products[:MAX_HOME_PRODUCTS]:
        prices = sorted({v.amount for v in product.variants})
        currency = product.variants[0].currency
        price = price_label(prices[0], currency) if len(prices) == 1 else f"from {price_label(prices[0], currency)}"
        lines.append(f"- [{md(product.title, 150)}]({product_url(site, product)}): {md(price, 60)}")
    if not site.products:
        lines.append("- No products to show yet.")
    lines += [
        "",
        "## Shipping and returns",
        "",
        *(f"- {md(line, 300)}" for line in shipping_lines(site)),
        *([f"- Shipping: {md(site.shipping_policy, 500)}"] if site.shipping_policy else []),
        *([f"- Returns: {md(site.returns_policy, 500)}"] if site.returns_policy else []),
        "",
        "## For AI agents",
        "",
        f"- Shop profile (UCP): {site.base_url}/.well-known/ucp",
        f"- Product feed: {feed_url(site)}",
        f"- Sitemap: {site.base_url}/sitemap.xml",
        f"- Shop every network shop over MCP: {MCP_SERVER_URL}",
        "",
    ]
    return "\n".join(lines)
