# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The Telegram shopper's shop choice: /avoid, /allow, and an avoided shop is never searched or ordered from."""

from __future__ import annotations

import pytest

from api.v1 import network as api
from core.commerce import network as net
from core.commerce import network_telegram as bot
from core.config import settings
from core.test_doubles.scripted_model import ScriptedChatModel, final, tool_call

TEA = net.ShopRef(
    slug="tea-corner",
    tenant_id="00000000-0000-0000-0000-0000000000c1",
    merchant_id="merchant-1",
    seller_agent_id="seller-1",
    host="tea-corner.shops.example",
    display_name="Tea Corner",
)
DIRECTORY = [
    {"slug": "tea-corner", "name": "Tea Corner", "host": "tea-corner.shops.example"},
    {"slug": "kettle-works", "name": "Kettle Works", "host": "kettle-works.shops.example"},
    {"slug": "spice-hub", "name": "Spice Hub", "host": "spice-hub.shops.example"},
]


@pytest.fixture()
def world(monkeypatch):
    bot._MEMORY.clear()

    async def no_redis():
        return None

    async def directory():
        return DIRECTORY

    async def resolve(slug):
        if slug != TEA.slug:
            raise net.NetworkError("unknown_shop", "No such shop.", 404)
        return TEA

    async def under_cap(_estimate=0.0, tenant_id=None):
        return None

    monkeypatch.setattr(bot, "_redis", no_redis)
    monkeypatch.setattr(net, "directory", directory)
    monkeypatch.setattr(net, "resolve_shop", resolve)
    monkeypatch.setattr("core.llm.router.assert_under_gemini_cap", under_cap)
    monkeypatch.setattr(settings, "recorded_spend_controls_enabled", False)
    monkeypatch.setattr(settings, "network_telegram_bot_token", "123456:synthetic-bot-token-value")
    monkeypatch.setattr(settings, "network_telegram_webhook_secret", "synthetic-webhook-secret-0123456789")
    monkeypatch.setattr(settings, "network_telegram_chat_limit_per_hour", 20)
    monkeypatch.setattr(settings, "network_telegram_global_limit_per_minute", 60)
    from core import auth_state

    auth_state._mem_window.clear()
    monkeypatch.setattr(auth_state, "_get_redis", no_redis)
    yield
    bot._MEMORY.clear()
    auth_state._mem_window.clear()


async def _avoided(chat: str = "42") -> list[str]:
    return (await bot.load_state(bot.chat_key(chat))).get("avoid") or []


@pytest.mark.asyncio
async def test_avoid_lists_adds_by_slug_or_name_allows_and_clears(world):
    assert "not avoiding any shop" in await bot.respond("42", "/avoid")
    added = await bot.respond("42", "/avoid kettle-works")
    assert "leave Kettle Works out" in added and "/allow kettle-works" in added
    assert "Spice Hub" in await bot.respond("42", "/avoid spice hub")
    assert await _avoided() == ["kettle-works", "spice-hub"]
    listed = await bot.respond("42", "/avoid")
    assert "kettle-works, spice-hub" in listed and "/avoid clear" in listed
    assert "couldn't find that shop" in await bot.respond("42", "/avoid nowhere")
    assert "Done" in await bot.respond("42", "/allow kettle-works")
    assert await _avoided() == ["spice-hub"]
    assert "isn't on your avoid list" in await bot.respond("42", "/allow kettle-works")
    assert "every shop again" in await bot.respond("42", "/avoid clear")
    assert await _avoided() == []


@pytest.mark.asyncio
async def test_avoiding_the_chosen_shop_unpicks_it_and_start_takes_it_off_the_list(world):
    await bot.respond("42", "/start tea-corner")
    await bot.respond("42", "/avoid tea-corner")
    state = await bot.load_state(bot.chat_key("42"))
    assert "shop" not in state and state["avoid"] == ["tea-corner"]
    await bot.respond("42", "/start tea-corner")
    state = await bot.load_state(bot.chat_key("42"))
    assert state["shop"] == "tea-corner" and state["avoid"] == []


@pytest.mark.asyncio
async def test_the_list_has_a_cap(world, monkeypatch):
    monkeypatch.setattr(bot, "MAX_AVOIDED_SHOPS", 1)
    await bot.respond("42", "/avoid kettle-works")
    assert "already avoiding 1 shops" in await bot.respond("42", "/avoid spice-hub")
    assert await _avoided() == ["kettle-works"]


@pytest.mark.asyncio
async def test_a_network_search_leaves_avoided_shops_out_and_keeps_the_random_order(world, monkeypatch):
    asked, shuffled = [], []

    async def search(entry, query):
        asked.append(entry["slug"])
        return []

    def shuffle(items):
        shuffled.append([s["slug"] for s in items])
        items.reverse()

    monkeypatch.setattr(bot, "_catalog_search", search)
    monkeypatch.setattr(bot, "_shuffle", shuffle)
    result = await bot.ShopperTools(None, ["kettle-works"]).search("chai")
    # The avoided shop is left out before the shuffle; the others are still shuffled.
    assert shuffled == [["tea-corner", "spice-hub"]]
    assert sorted(asked) == ["spice-hub", "tea-corner"]
    assert result["shops_avoided_by_buyer"] == ["kettle-works"]
    assert result["shops_searched"] == 2 and result["shops_in_network"] == 3
    refused = await bot.ShopperTools(None, ["kettle-works"]).search("chai", shop="kettle-works")
    assert "avoid" in refused["error"] and asked.count("kettle-works") == 0


@pytest.mark.asyncio
async def test_no_checkout_or_payment_link_at_an_avoided_shop(world, monkeypatch):
    calls = []

    async def checkout(slug, operation, body, session_id=None):
        calls.append((slug, operation))
        return {"http_status": 201, "id": "chk_1", "status": "incomplete"}

    monkeypatch.setattr(api, "network_checkout", checkout)
    tools = bot.ShopperTools(None, ["kettle-works"])
    assert "error" in await tools.open_checkout("kettle-works", "KET-1")
    assert "error" in await tools.get_payment_link("kettle-works", "chk_1")
    assert calls == []
    await tools.open_checkout("tea-corner", "TEA-1")
    assert calls == [("tea-corner", "create_checkout")]


@pytest.mark.asyncio
async def test_the_buyer_can_ask_in_words_and_the_prompt_names_the_avoided_shops(world, monkeypatch):
    async def search(entry, query):
        return []

    monkeypatch.setattr(bot, "_catalog_search", search)
    model = ScriptedChatModel(
        steps=[
            tool_call("avoid_shop", shop="Kettle Works"),
            final("Done, I'll leave Kettle Works out."),
            final("Here is what I found."),
        ]
    )
    reply = await bot.respond("42", "please don't use Kettle Works", model_factory=lambda: model)
    assert reply == "Done, I'll leave Kettle Works out."
    assert await _avoided() == ["kettle-works"]
    await bot.respond("42", "find chai", model_factory=lambda: model)
    system = model.calls[-1][0].content
    assert "The buyer avoids these shops (slugs): kettle-works." in system
    assert "Never search, open a checkout or" in bot.SYSTEM_PROMPT
    assert "The buyer chooses the shop." in bot.SYSTEM_PROMPT
    bound = set(model.bind_tools(bot._langchain_tools(bot.ShopperTools(None))).bound_tool_names)
    assert {"avoid_shop", "allow_shop"} <= bound
