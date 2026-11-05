# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""A network shop's storefront and product feed on its own host (core.commerce.shop_site, shop_feed).

The products are built the way the UCP catalog builds them (catalog_upload.upload_products, then
ucp.catalog.ucp_product), so the pages and the feed see exactly what buyer agents see.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any

import pytest

from core.commerce import shop_feed, shop_site
from core.commerce.catalog_upload import upload_products
from core.commerce.shop_site import ShopSite, site_products
from core.commerce.ucp.catalog import ucp_product
from core.commerce.ucp.shopify_reads import DeliveryRate

HOST = "tea-corner.agenticorg.ai"
G = "{http://base.google.com/ns/1.0}"
XSS = '<script>alert("x")</script><img src=x onerror=alert(1)>'
GTIN = "4006381333931"


def _row(sku: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "sku": sku,
        "title": f"Masala chai {sku}",
        "description": "Strong Assam tea with cardamom & ginger.",
        "brand": "Tea Corner",
        "category": "Tea",
        "image_url": f"https://cdn.example/{sku}.jpg",
        "price": "449.00",
        "currency": "INR",
        "quantity": 12,
    }
    row.update(overrides)
    return row


def _products(*rows: dict[str, Any]) -> list[dict[str, Any]]:
    raw = upload_products(
        rows=list(rows),
        tenant_id="00000000-0000-0000-0000-0000000000b1",
        merchant_id="merchant-1",
        seller_agent_id="seller-1",
        synced_at="2026-11-04T00:00:00Z",
        uploaded_at="2026-11-04T00:00:00Z",
    )
    return [p for p in (ucp_product({**item, "_evidence_currency": "INR"}) for item in raw) if p is not None]


def _site(*rows: dict[str, Any], **overrides: Any) -> ShopSite:
    values: dict[str, Any] = {
        "slug": "tea-corner",
        "name": "Tea Corner",
        "host": HOST,
        "catalog_verified": True,
        "products": site_products(_products(*rows)),
        "rates": (
            DeliveryRate(
                id="flat_standard",
                title="Standard shipping",
                description=None,
                amount_minor_units=5000,
                currency="INR",
                countries=("IN",),
                rest_of_world=False,
            ),
        ),
        "returns_policy": "Returns within 7 days, unopened.",
        "shipping_policy": "Ships in 2 days.",
        "telegram_link": "https://t.me/agenticorg_shop_bot?start=tea-corner",
    }
    values.update(overrides)
    return ShopSite(**values)


def _jsonld(html: str) -> Any:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert match is not None
    return json.loads(match.group(1))


# --- products ------------------------------------------------------------------------------


def test_site_products_carry_price_stock_gtin_and_a_stable_handle() -> None:
    site = _site(_row("CHAI-1", gtin=GTIN, compare_at_price="499.00"))
    (product,) = site.products
    assert product.title == "Masala chai CHAI-1"
    assert product.handle.startswith("masala-chai-chai-1-") and len(product.handle.rsplit("-", 1)[1]) == 10
    (variant,) = product.variants
    assert (variant.amount, variant.currency, variant.list_amount, variant.available) == (44900, "INR", 49900, True)
    assert variant.gtin == GTIN and variant.sku == "CHAI-1"
    assert site_products(_products(_row("CHAI-1", gtin=GTIN)))[0].handle == product.handle
    assert shop_site.money(44900, "INR") == "449.00"
    assert shop_site.money(1500, "JPY") == "1500"
    assert shop_site.money(1500, "KWD") == "1.500"


def test_condition_comes_from_a_declared_detail_else_new() -> None:
    used = _site(_row("USED-1", attributes=[{"name": "condition", "label": "Condition", "value": "Used"}]))
    assert used.products[0].condition == "used"
    assert _site(_row("NEW-1")).products[0].condition == "new"


# --- pages -------------------------------------------------------------------------------------


def test_home_lists_the_products_with_links_to_their_pages() -> None:
    site = _site(_row("CHAI-1"), _row("CHAI-2", price="99.50", quantity=0))
    html = shop_site.render_home(site)
    for product in site.products:
        assert f'href="{shop_site.product_url(site, product)}"' in html
    assert "INR 449.00" in html and "INR 99.50" in html and "Out of stock" in html
    assert f'<link rel="canonical" href="https://{HOST}/">' in html
    assert "Returns within 7 days" in html and "Standard shipping: INR 50.00 to IN" in html
    assert _jsonld(html)["@type"] == "OnlineStore"


def test_product_page_has_price_stock_policies_and_product_jsonld() -> None:
    site = _site(_row("CHAI-1", gtin=GTIN, compare_at_price="499.00", url="https://shop.example/chai"))
    product = site.products[0]
    html = shop_site.render_product(site, product)
    assert "INR 449.00" in html and 'class="was">INR 499.00' in html and "In stock" in html
    assert "Strong Assam tea with cardamom &amp; ginger." in html
    assert 'href="https://shop.example/chai"' in html and "mcp.agenticorg.ai/mcp" in html
    assert "t.me/agenticorg_shop_bot?start=tea-corner" in html
    document = _jsonld(html)
    assert document["@type"] == "Product" and document["gtin"] == GTIN and document["sku"] == "CHAI-1"
    assert document["brand"] == {"@type": "Brand", "name": "Tea Corner"}
    offer = document["offers"]
    assert (offer["price"], offer["priceCurrency"]) == ("449.00", "INR")
    assert offer["availability"] == "https://schema.org/InStock"
    assert offer["url"] == shop_site.product_url(site, product)
    assert offer["shippingDetails"][0]["shippingRate"] == {
        "@type": "MonetaryAmount",
        "value": "50.00",
        "currency": "INR",
    }
    assert offer["shippingDetails"][0]["shippingDestination"] == [{"@type": "DefinedRegion", "addressCountry": "IN"}]


def _variant_product() -> dict[str, Any]:
    return {
        "id": "shopify_product:abc",
        "title": "Teapot",
        "description": {"plain": "Glass teapot."},
        "media": [{"type": "image", "url": "https://cdn.example/pot.jpg"}],
        "variants": [
            {"id": "v-small", "title": "Small", "price": {"amount": 90000, "currency": "INR"},
             "availability": {"available": True}},
            {"id": "v-large", "title": "Large", "price": {"amount": 120000, "currency": "INR"},
             "availability": {"available": False}},
        ],
    }  # fmt: skip


def test_a_product_with_options_is_a_product_group_and_a_variant_link_selects_one() -> None:
    site = _site(products=site_products([_variant_product()]))
    product = site.products[0]
    document = _jsonld(shop_site.render_product(site, product))
    assert document["@type"] == "ProductGroup" and len(document["hasVariant"]) == 2
    assert {v["offers"]["price"] for v in document["hasVariant"]} == {"900.00", "1200.00"}
    large = product.variants[1]
    url = shop_site.product_url(site, product, large)
    assert url.endswith(f"?variant={shop_site.variant_key_of(large)}")
    html = shop_site.render_product(site, product, shop_site.variant_key_of(large))
    assert '<tr class="selected">' in html and "INR 1200.00" in html
    assert f'<link rel="canonical" href="{url}">' in html


def _raw(title: str, description: str, **extra: Any) -> dict[str, Any]:
    """A catalog product as another source might hand it over, tags and all (no upstream stripping)."""
    return {
        "id": "p-raw",
        "title": title,
        "description": {"plain": description},
        "media": [{"type": "image", "url": 'https://cdn.example/a.jpg" onerror="alert(6)', "alt_text": XSS}],
        "variants": [
            {"id": "v-raw", "title": XSS, "sku": '"><script>', "price": {"amount": 100, "currency": "INR"},
             "availability": {"available": True}},
        ],
        **extra,
    }  # fmt: skip


def test_upstream_catalog_text_arrives_without_tags() -> None:
    (product,) = _site(_row("XSS-0", title=XSS)).products
    assert product.title == 'alert("x")'


def test_catalog_text_is_escaped_in_html_attributes_and_jsonld() -> None:
    hostile = _raw(
        XSS,
        'Tea</script><script>alert(2)</script> " onmouseover="alert(3)',
        metadata={"brand": 'Brand"><script>alert(4)</script>', "attributes": [{"name": "<i>x</i>", "value": XSS}]},
    )
    site = _site(
        products=site_products([hostile]),
        name='Shop <b onmouseover="x">',
        returns_policy="<img src=x onerror=alert(5)>",
    )
    for html in (shop_site.render_home(site), shop_site.render_product(site, site.products[0])):
        lowered = html.lower()
        # The only script element is the JSON-LD block, and nothing breaks out of it.
        assert lowered.count("<script") == 1 and lowered.count("</script>") == 1
        assert "<img src=x" not in lowered and 'onmouseover="' not in lowered and "<b " not in lowered
        assert 'onerror="' not in lowered and "<i>" not in lowered
        assert "&lt;script&gt;" in html
        block = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
        assert block is not None and "<" not in block.group(1) and ">" not in block.group(1)
        json.loads(block.group(1))


def test_unavailable_page_names_the_shop_escaped() -> None:
    assert "&lt;script&gt;" in shop_site.render_unavailable("<script>")


# --- sitemap, robots, llms.txt -------------------------------------------------------------------


def test_sitemap_lists_home_and_every_product_page() -> None:
    site = _site(_row("CHAI-1"), _row("CHAI-2"))
    root = ET.fromstring(shop_site.render_sitemap(site))  # noqa: S314  # nosec B314
    locs = [el.text for el in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    assert locs == [f"https://{HOST}/"] + [shop_site.product_url(site, p) for p in site.products]


def test_robots_points_at_the_sitemap_and_keeps_agent_apis_out() -> None:
    robots = shop_site.render_robots(HOST)
    assert "Allow: /\n" in robots and "Disallow: /ucp/\n" in robots and "Disallow: /acp/\n" in robots
    assert robots.endswith(f"Sitemap: https://{HOST}/sitemap.xml\n")


def test_llms_txt_lists_products_and_escapes_markdown() -> None:
    site = _site(_row("CHAI-1", title="Chai [best](https://evil.example)\n# heading"))
    text = shop_site.render_llms(site)
    assert "](https://evil.example)" not in text and "\\[best\\]\\(https://evil.example\\)" in text
    assert "\n# heading" not in text
    assert f"({shop_site.product_url(site, site.products[0])}): INR 449.00" in text
    assert f"Product feed: https://{HOST}/feeds/google.xml" in text
    assert f"Shop profile (UCP): https://{HOST}/.well-known/ucp" in text


# --- the product feed ----------------------------------------------------------------------------

REQUIRED = ("g:id", "title", "description", "link", "g:image_link", "g:availability", "g:price", "g:condition")


def _feed_items(site: ShopSite) -> list[ET.Element]:
    root = ET.fromstring(shop_feed.render_google_feed(site))  # noqa: S314  # nosec B314
    assert root.tag == "rss" and root.attrib["version"] == "2.0"
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("title") == site.name and channel.findtext("link") == f"https://{HOST}/"
    return channel.findall("item")


def _find(item: ET.Element, name: str) -> str | None:
    tag = G + name[2:] if name.startswith("g:") else name
    return item.findtext(tag)


def test_feed_items_have_every_required_field() -> None:
    site = _site(_row("CHAI-1", gtin=GTIN), _row("CHAI-2", brand=None, quantity=0))
    items = _feed_items(site)
    assert len(items) == 2
    for item in items:
        for name in REQUIRED:
            assert _find(item, name), name
        assert (_find(item, "link") or "").startswith(f"https://{HOST}/products/")
        assert _find(item, "g:gtin") or _find(item, "g:identifier_exists") == "no"
        shipping = item.find(G + "shipping")
        assert shipping is not None
        assert (shipping.findtext(G + "country"), shipping.findtext(G + "price")) == ("IN", "50.00 INR")
    first, second = items
    assert (_find(first, "g:id"), _find(first, "g:price"), _find(first, "g:availability")) == (
        "CHAI-1",
        "449.00 INR",
        "in_stock",
    )
    assert _find(first, "g:gtin") == GTIN and _find(first, "g:brand") == "Tea Corner"
    assert _find(first, "g:identifier_exists") is None
    assert _find(second, "g:availability") == "out_of_stock" and _find(second, "g:identifier_exists") == "no"
    assert _find(second, "g:brand") is None


def test_feed_links_are_the_product_pages() -> None:
    site = _site(products=site_products([_variant_product()]))
    items = _feed_items(site)
    product = site.products[0]
    assert [_find(i, "link") for i in items] == [shop_site.product_url(site, product, v) for v in product.variants]
    assert {_find(i, "g:item_group_id") for i in items} == {product.handle[-10:]}
    assert [_find(i, "title") for i in items] == ["Teapot - Small", "Teapot - Large"]


def test_a_compare_at_price_becomes_price_and_sale_price() -> None:
    (item,) = _feed_items(_site(_row("SALE-1", compare_at_price="499.00")))
    assert (_find(item, "g:price"), _find(item, "g:sale_price")) == ("499.00 INR", "449.00 INR")


def test_unsellable_variants_are_left_out_and_counted() -> None:
    products = [
        {
            "id": f"p-{reason}",
            "title": reason,
            "description": {"plain": reason},
            "media": [] if reason == "no_image" else [{"type": "image", "url": "https://cdn.example/x.jpg"}],
            "variants": [
                {
                    "id": f"v-{reason}",
                    "title": "Default",
                    "price": {"amount": 0 if reason == "no_price" else 100, "currency": "INR"},
                    **({} if reason == "no_stock_figure" else {"availability": {"available": True}}),
                }
            ],
        }
        for reason in ("no_price", "no_stock_figure", "no_image", "ok")
    ]
    build = shop_feed.build_feed(_site(products=site_products(products)))
    assert len(build.items) == 1
    assert build.excluded == {"no_price": 1, "no_stock_figure": 1, "no_image": 1}
    # A variant whose price is not a number never reaches the storefront at all (ucp_product drops it).
    assert _products(_row("NOPRICE", price="call us")) == []


def test_feed_text_is_escaped_and_long_values_are_cut() -> None:
    site = _site(
        products=site_products([_raw("Tea & <Biscuits> " + "x" * 300, "Fish & chips </description><x>")]),
        name="Tea & Co <shop>",
    )
    raw = shop_feed.render_google_feed(site)
    assert "<Biscuits>" not in raw and "</description><x>" not in raw and "Tea &amp; Co &lt;shop&gt;" in raw
    (item,) = _feed_items(site)
    title = _find(item, "title") or ""
    assert title.startswith("Tea & <Biscuits>") and len(title) == shop_feed.MAX_TITLE
    assert _find(item, "description") == "Fish & chips </description><x>"


def test_item_ids_use_a_plain_sku_else_a_digest() -> None:
    long_sku = shop_site.SiteVariant(
        id="shopify_variant:abc", title="Default", sku="has spaces", amount=1, currency="INR",
        list_amount=None, available=True, gtin=None,
    )  # fmt: skip
    assert re.fullmatch(r"[0-9a-f]{24}", shop_feed.item_id(long_sku))
    assert shop_feed.item_id(shop_site.SiteVariant(**{**long_sku.__dict__, "sku": "SKU-1"})) == "SKU-1"


def test_shipping_rates_in_another_currency_or_without_countries_are_not_in_the_feed() -> None:
    rates = (
        DeliveryRate("flat_usd", "Air", None, 900, "USD", ("US",), False),
        DeliveryRate("flat_world", "World", None, 0, "INR", (), True),
    )
    (item,) = _feed_items(_site(_row("CHAI-1"), rates=rates))
    assert item.find(G + "shipping") is None


@pytest.mark.parametrize("value", ["\x00", "\x0b", "￾"])
def test_illegal_xml_characters_are_dropped(value: str) -> None:
    assert shop_feed.xml_text(f"a{value}b") == "ab"
