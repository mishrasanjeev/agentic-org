# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Searching the network fairly: best match first, no fixed shop order, one product at several shops."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from core.commerce import network_telegram as tg
from core.commerce.catalog_upload import upload_products
from core.commerce.ucp.catalog import search_catalog


def _product(sku: str, title: str, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "sku": sku,
        "title": title,
        "description": extra.pop("description", ""),
        "price": extra.pop("price", "499.00"),
        "currency": "INR",
        "quantity": 5,
        **extra,
    }
    return upload_products(
        rows=[row],
        tenant_id="t",
        merchant_id="m",
        seller_agent_id="s",
        synced_at="2026-11-02T00:00:00Z",
        uploaded_at="2026-11-02T00:00:00Z",
    )[0]


def _titles(products: list[dict[str, Any]], query: str | None) -> list[str]:
    found = search_catalog(products, query=query, categories=None, price_min=None, price_max=None)
    return [item["title"] for item in found]


# --- best match first, inside one shop --------------------------------------------------------------


def test_a_word_in_the_title_ranks_above_a_word_only_in_the_description_or_tags() -> None:
    products = [
        _product("B-1", "Butter cookies", description="Baked with ghee"),
        _product("L-1", "Brass lamp", tags=["ghee lamp"]),
        _product("G-1", "Gheewala sweets"),
        _product("G-2", "Cow ghee 500 ml"),
    ]
    # Whole word in the title, then a whole word in the tags, then part of a word, then the description.
    assert _titles(products, "ghee") == ["Cow ghee 500 ml", "Brass lamp", "Gheewala sweets", "Butter cookies"]


def test_whole_words_count_in_any_script_and_with_hyphens() -> None:
    products = [
        _product("T-2", "T-Shirts pack"),
        _product("T-1", "T-Shirt blue"),
        _product("C-2", "\u091a\u093e\u092f\u0926\u093e\u0928\u0940"),
        _product("C-1", "\u092e\u0938\u093e\u0932\u093e \u091a\u093e\u092f 250 g"),
        _product("S-1", "Steak knife", description="Not for tea"),
        _product("K-1", "Kettle", tags=["tea"]),
    ]
    chai = "\u091a\u093e\u092f"
    assert _titles(products, "t-shirt") == ["T-Shirt blue", "T-Shirts pack"]
    assert _titles(products, chai) == [
        f"\u092e\u0938\u093e\u0932\u093e {chai} 250 g",
        f"{chai}\u0926\u093e\u0928\u0940",
    ]
    # "tea" inside "steak" is part of a word; a tag that is the whole word counts more.
    assert _titles(products, "tea") == ["Kettle", "Steak knife"]


def test_the_whole_query_in_the_title_ranks_first_and_ties_keep_title_order() -> None:
    products = [
        _product("G-3", "Ghee from cow milk"),
        _product("G-2", "Cow ghee 500 ml"),
        _product("G-1", "Cow ghee 1 litre"),
    ]
    assert _titles(products, "cow ghee") == ["Cow ghee 1 litre", "Cow ghee 500 ml", "Ghee from cow milk"]


def test_without_a_query_products_stay_in_title_order_and_every_word_must_still_match() -> None:
    products = [_product("Z-1", "Zesty pickle"), _product("A-1", "Amla candy"), _product("M-1", "Mango pickle")]
    assert _titles(products, None) == ["Amla candy", "Mango pickle", "Zesty pickle"]
    assert _titles(products, "mango pickle") == ["Mango pickle"]


def test_the_order_does_not_depend_on_the_order_products_were_stored() -> None:
    products = [_product("G-1", "Cow ghee"), _product("B-1", "Ghee biscuits", price="99.00"), _product("C-1", "Candle")]
    assert _titles(products, "ghee") == _titles(list(reversed(products)), "ghee")


# --- the Telegram shopper: no fixed shop order, every shop asked ------------------------------------


def _shops(count: int) -> list[dict[str, Any]]:
    return [
        {"slug": f"shop-{index:02d}", "name": f"Shop {index}", "host": f"shop-{index:02d}.example"}
        for index in range(count)
    ]


def _summary(
    shop: str,
    title: str,
    *,
    barcode: str | None = None,
    amount: int = 50000,
    currency: str = "INR",
    item: str | None = None,
    cheaper_variant: int | None = None,
    description: str = "",
) -> dict[str, Any]:
    variant: dict[str, Any] = {"id": item or f"{shop}-v", "price": {"amount": amount, "currency": currency}}
    if barcode:
        variant["barcodes"] = [{"type": "GTIN", "value": barcode}]
    variants = [variant]
    if cheaper_variant is not None:
        variants.append({"id": f"{shop}-small", "price": {"amount": cheaper_variant, "currency": currency}})
    low = min([amount] + ([cheaper_variant] if cheaper_variant is not None else []))
    product = {
        "id": f"{shop}-p",
        "title": title,
        "description": {"plain": description} if description else None,
        "price_range": {"min": {"amount": low, "currency": currency}},
        "variants": variants,
    }
    return {**tg._product_summary(shop, product), **tg._offer_price(product)}


@pytest.mark.asyncio
async def test_a_search_asks_shops_in_a_fresh_order_up_to_the_limit_a_few_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shops = _shops(60)
    asked: list[str] = []
    running = {"now": 0, "most": 0}

    async def directory() -> list[dict[str, Any]]:
        return shops

    async def catalog_search(entry: dict[str, Any], _query: str) -> list[dict[str, Any]]:
        running["now"] += 1
        running["most"] = max(running["most"], running["now"])
        await asyncio.sleep(0)
        asked.append(entry["slug"])
        running["now"] -= 1
        if entry["slug"] == "shop-58":
            raise RuntimeError("shop down")
        return [_summary(entry["slug"], f"Cow ghee {n}", description="x" * 300) for n in range(5)]

    monkeypatch.setattr(tg.net, "directory", directory)
    monkeypatch.setattr(tg, "_catalog_search", catalog_search)
    monkeypatch.setattr(tg, "_shuffle", lambda items: items.reverse())
    body = await tg.ShopperTools(None).search("ghee")

    # Reversed by the stubbed shuffle: the last-joined shops are asked, the first ones are not.
    assert sorted(asked) == sorted(f"shop-{index:02d}" for index in range(60 - tg.MAX_SEARCH_SHOPS, 60))
    assert 1 < running["most"] <= tg.SEARCH_CONCURRENCY
    assert body["shops_searched"] == tg.MAX_SEARCH_SHOPS and body["shops_in_network"] == 60
    assert body["shops_in_random_order"] is True
    shown = [item["shop"] for item in body["products"]]
    assert "shop-58" not in shown and shown[0] == "shop-59"
    # A share each (one product from each of 19 answering shops), with short descriptions, so all of them fit.
    assert len(shown) == len(set(shown)) == tg.MAX_SEARCH_SHOPS - 1 and "more_results_left_out" not in body
    assert all(len(item["description"]) <= tg.NETWORK_DESCRIPTION for item in body["products"])
    assert all("_price" not in item for item in body["products"])


@pytest.mark.asyncio
async def test_a_shop_that_does_not_answer_in_time_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    async def directory() -> list[dict[str, Any]]:
        return _shops(2)

    async def catalog_search(entry: dict[str, Any], _query: str) -> list[dict[str, Any]]:
        if entry["slug"] == "shop-00":
            await asyncio.sleep(5)
        return [_summary(entry["slug"], "Cow ghee")]

    monkeypatch.setattr(tg.net, "directory", directory)
    monkeypatch.setattr(tg, "_catalog_search", catalog_search)
    monkeypatch.setattr(tg, "SEARCH_TIMEOUT_SECONDS", -1.9)
    body = await tg.ShopperTools(None).search("ghee")
    assert [item["shop"] for item in body["products"]] == ["shop-01"]


@pytest.mark.asyncio
async def test_no_shop_is_always_first(monkeypatch: pytest.MonkeyPatch) -> None:
    async def directory() -> list[dict[str, Any]]:
        return _shops(3)

    async def catalog_search(entry: dict[str, Any], _query: str) -> list[dict[str, Any]]:
        return [_summary(entry["slug"], "Cow ghee")]

    monkeypatch.setattr(tg.net, "directory", directory)
    monkeypatch.setattr(tg, "_catalog_search", catalog_search)
    firsts = {(await tg.ShopperTools(None).search("ghee"))["products"][0]["shop"] for _ in range(40)}
    assert firsts == {"shop-00", "shop-01", "shop-02"}


@pytest.mark.asyncio
async def test_a_chosen_shop_is_searched_alone_without_the_network_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    async def directory() -> list[dict[str, Any]]:
        return _shops(3)

    async def catalog_search(entry: dict[str, Any], _query: str) -> list[dict[str, Any]]:
        return [_summary(entry["slug"], "Cow ghee")]

    monkeypatch.setattr(tg.net, "directory", directory)
    monkeypatch.setattr(tg, "_catalog_search", catalog_search)
    body = await tg.ShopperTools("shop-01").search("ghee")
    assert [item["shop"] for item in body["products"]] == ["shop-01"]
    assert not {"shops_in_random_order", "shops_searched", "same_product_at_several_shops"} & set(body)


# --- one product at several shops --------------------------------------------------------------------


def test_a_group_offer_is_the_variant_with_that_barcode_and_each_shop_appears_once() -> None:
    results = [
        # A cheap second variant of shop-a (another size) must not stand in for the 1 kg item.
        _summary("shop-a", "Ghee 1 kg", barcode="8901234567890", amount=90000, item="a-1kg", cheaper_variant=100),
        _summary("shop-b", "Ghee 1 kg", barcode="8901234567890", amount=85000),
        _summary("shop-b", "Ghee 1 kg (old stock)", barcode="8901234567890", amount=80000, item="b-old"),
        _summary("shop-c", "Ghee 1 kg", barcode="8901234567890", amount=1, currency="AED"),
        _summary("shop-d", "Ghee", barcode="1"),
        _summary("shop-e", "Ghee", barcode="1"),
    ]
    [group] = tg._fit_search_results(results)["same_product_at_several_shops"]
    assert [(o["shop"], o["item_id"], o["price"]) for o in group["offers"]] == [
        ("shop-b", "b-old", "Rs 800.00"),
        ("shop-a", "a-1kg", "Rs 900.00"),
        ("shop-c", "shop-c-v", "AED 0.01"),
    ]


def test_the_same_barcode_at_several_shops_lists_each_offer_lowest_price_first() -> None:
    results = [
        _summary("shop-a", "Cow ghee 500 ml", barcode="8901234567890", amount=89900),
        _summary("shop-b", "A2 cow ghee (500 ml)", barcode="8901234567890", amount=84900),
        _summary("shop-c", "Cow ghee 500 ml", barcode="8901234567890"),
        _summary("shop-c", "Paneer", barcode="4006381333931"),
        _summary("shop-a", "Butter"),
    ]
    results[2].pop("_price")  # a shop whose price could not be read sorts last
    body = tg._fit_search_results(results)
    [group] = body["same_product_at_several_shops"]
    assert group["barcode"] == "8901234567890"
    assert [offer["shop"] for offer in group["offers"]] == ["shop-b", "shop-a", "shop-c"]
    assert group["offers"][0] == {"shop": "shop-b", "item_id": "shop-b-v", "price": "Rs 849.00", "available": None}
    assert all("_price" not in item for item in body["products"])
    assert body["products"][0]["barcode"] == "8901234567890"


def test_trimming_to_the_budget_keeps_the_groups_in_step_with_the_products(monkeypatch: pytest.MonkeyPatch) -> None:
    results = [
        _summary(f"shop-{index}", "Cow ghee", barcode="8901234567890", amount=1000 + index) for index in range(3)
    ]
    results += [_summary("shop-0", f"Other product {index} " + "x" * 200) for index in range(6)]
    full = len(json.dumps(tg._fit_search_results([dict(item) for item in results]), default=str))
    monkeypatch.setattr(tg, "SEARCH_RESULT_BUDGET", full - 1)
    body = tg._fit_search_results([dict(item) for item in results])
    assert body["more_results_left_out"] is True
    shown = {(item["shop"], item["item_id"]) for item in body["products"]}
    for group in body.get("same_product_at_several_shops", []):
        assert all((offer["shop"], offer["item_id"]) in shown for offer in group["offers"])
        assert len({offer["shop"] for offer in group["offers"]}) >= 2
    monkeypatch.setattr(tg, "SEARCH_RESULT_BUDGET", 400)
    small = tg._fit_search_results([dict(item) for item in results])
    assert "same_product_at_several_shops" not in small or all(
        len({o["shop"] for o in g["offers"]}) >= 2 for g in small["same_product_at_several_shops"]
    )


def test_the_shopper_is_told_the_order_means_nothing() -> None:
    assert "random order on every search" in tg.SYSTEM_PROMPT
    assert "never favour a" in tg.SYSTEM_PROMPT and "same_product_at_several_shops" in tg.SYSTEM_PROMPT
