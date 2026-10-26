# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Public shopping bot: Redis chat store, catalog parsing, tool paths, task wrapper and setup script."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from api.v1 import network as api
from core.commerce import network as net
from core.commerce import network_telegram as bot
from core.config import settings
from core.test_doubles.scripted_model import ScriptedChatModel, final, tool_call

SHOP = net.ShopRef(
    slug="tea-corner",
    tenant_id="00000000-0000-0000-0000-0000000000d1",
    merchant_id="merchant-1",
    seller_agent_id="seller-1",
    host="tea-corner.shops.example",
    display_name="Tea Corner",
)


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return None
        self.data[key] = value
        self.ttls[key] = ex
        return True

    async def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture()
def redis(monkeypatch):
    fake = FakeRedis()

    async def get_redis():
        return fake

    monkeypatch.setattr(bot, "_redis", get_redis)
    bot._MEMORY.clear()
    return fake


@pytest.mark.asyncio
async def test_the_chat_store_uses_redis_with_ttls_and_claims_updates_once(redis):
    chat = bot.chat_key("42")
    assert chat != "42" and len(chat) == 64
    await bot.save_state(chat, {"shop": "tea-corner", "turns": [{"user": str(i), "bot": "x"} for i in range(9)]})
    key = f"network-telegram:state:{chat}"
    assert redis.ttls[key] == bot.STATE_TTL_SECONDS
    state = await bot.load_state(chat)
    assert state["shop"] == "tea-corner" and len(state["turns"]) == bot.MAX_TURNS
    assert await bot.claim_update(7) is True and await bot.claim_update(7) is False
    await bot._delete(key)
    assert await bot.load_state(chat) == {"turns": []}


@pytest.mark.asyncio
async def test_old_turns_expire_and_bad_state_is_reset(redis):
    chat = bot.chat_key("43")
    key = f"network-telegram:state:{chat}"
    redis.data[key] = json.dumps({"shop": "tea-corner", "turns": [{"user": "a", "bot": "b"}], "updated_at": 1})
    assert (await bot.load_state(chat))["turns"] == []
    redis.data[key] = json.dumps(["not", "a", "dict"])
    assert await bot.load_state(chat) == {"turns": []}


@pytest.mark.asyncio
async def test_the_memory_fallback_expires_and_respects_only_new(monkeypatch):
    async def none():
        return None

    monkeypatch.setattr(bot, "_redis", none)
    bot._MEMORY.clear()
    assert await bot._set("k", "v", 60, only_new=True) is True
    assert await bot._set("k", "w", 60, only_new=True) is False
    assert await bot._get("k") == "v"
    bot._MEMORY["k"] = (0.0, "stale")
    assert await bot._get("k") is None
    await bot._delete("k")
    assert "k" not in bot._MEMORY


def test_products_are_summarised_with_rupee_prices():
    product = {
        "id": "TEA-1",
        "title": "Masala Chai Blend",
        "description": {"plain": "Black tea with spices."},
        "price_range": {"min": {"amount": 44900, "currency": "INR"}},
        "variants": [
            {"id": "TEA-1-250", "availability": {"available": True}, "price": {"amount": 44900, "currency": "INR"}}
        ],
    }
    assert bot._product_summary("tea-corner", product) == {
        "shop": "tea-corner",
        "id": "TEA-1",
        "variant_id": "TEA-1-250",
        "title": "Masala Chai Blend",
        "price": "Rs 449.00",
        "available": True,
        "description": "Black tea with spices.",
    }
    bare = bot._product_summary(
        "tea-corner",
        {
            "id": "X",
            "title": "X",
            "description": "plain text",
            "variants": [{"price": {"amount": 1000, "currency": "USD"}}],
        },
    )
    assert bare["price"] == "USD 10.00" and bare["description"] == "plain text" and bare["available"] is None
    assert bot._money(None) is None


@pytest.mark.asyncio
async def test_catalog_search_calls_the_shop_public_route(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["agent"] = request.headers.get("ucp-agent")
        seen["body"] = json.loads(request.content)
        if "down" in str(request.url):
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"products": [{"id": "TEA-1", "title": "Chai", "variants": []}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        bot.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)
    )
    monkeypatch.setattr(settings, "network_agent_profile", "https://platform.example/network.json")
    products = await bot._catalog_search({"slug": "tea-corner", "host": "tea-corner.shops.example"}, "chai")
    assert seen["url"] == "https://tea-corner.shops.example/ucp/catalog/search"
    assert seen["agent"] == 'profile="https://platform.example/network.json"'
    assert seen["body"] == {"query": "chai", "context": {"currency": "INR"}}
    assert products[0]["shop"] == "tea-corner" and products[0]["title"] == "Chai"
    assert await bot._catalog_search({"slug": "x", "host": "down.shops.example"}, "chai") == []


@pytest.mark.asyncio
async def test_checkout_tools_default_to_the_chosen_shop_and_failures_reach_the_model(monkeypatch):
    calls = []

    async def checkout(slug, operation, body, session_id=None):
        calls.append((slug, operation, body, session_id))
        return {"http_status": 200, "shop": slug}

    async def directory():
        return [{"slug": "tea-corner", "name": "Tea Corner", "host": "tea-corner.shops.example"}]

    async def failing_search(entry, query):
        raise RuntimeError("boom")

    monkeypatch.setattr(api, "network_checkout", checkout)
    monkeypatch.setattr(net, "directory", directory)
    monkeypatch.setattr(bot, "_catalog_search", failing_search)
    tools = bot.ShopperTools("tea-corner")
    await tools.open_checkout("", "TEA-1", 99)
    await tools.get_checkout("", "chk_1")
    await tools.update_checkout("", "chk_1", {"id": "chk_1"})
    assert calls == [
        ("tea-corner", "create_checkout", {"line_items": [{"item": {"id": "TEA-1"}, "quantity": 20}]}, None),
        ("tea-corner", "get_checkout", None, "chk_1"),
        ("tea-corner", "update_checkout", {"id": "chk_1"}, "chk_1"),
    ]
    assert await tools.list_shops() == {"shops": [{"shop": "tea-corner", "name": "Tea Corner"}]}
    assert await tools.search("chai") == {"products": []}


@pytest.mark.asyncio
async def test_the_shopper_reports_unknown_tools_and_stops_after_five_rounds(monkeypatch):
    async def under_cap(_estimate=0.0, tenant_id=None):
        return None

    async def directory():
        raise RuntimeError("directory down")

    monkeypatch.setattr("core.llm.router.assert_under_gemini_cap", under_cap)
    monkeypatch.setattr(net, "directory", directory)
    # An unknown tool and a failing tool are reported back to the model, which then answers.
    recovering = ScriptedChatModel(steps=[tool_call("list_shops"), final("Sorry, the shop list is unavailable.")])
    state = {"shop": "tea-corner", "shop_name": "Tea Corner", "turns": [{"user": "hi", "bot": "hello"}]}
    assert (
        await bot.run_shopper(state, "list shops", model_factory=lambda: recovering)
        == "Sorry, the shop list is unavailable."
    )
    tool_messages = [m for m in recovering.calls[1] if m.__class__.__name__ == "ToolMessage"]
    assert json.loads(tool_messages[0].content) == {"error": "RuntimeError"}

    # Out of tool rounds, one closing call without tools still answers from what the tools returned.
    looping = ScriptedChatModel(
        steps=[tool_call("list_shops", call_id=f"c{i}") for i in range(bot.MAX_TOOL_ROUNDS)]
        + [final("Checkout chk_1 is open for Rs 449.00; you pay with the shop.")]
    )
    reply = await bot.run_shopper({}, "loop", model_factory=lambda: looping)
    assert reply == "Checkout chk_1 is open for Rs 449.00; you pay with the shop."
    closing = looping.calls[-1]
    assert len(closing) == 3 and "Do not call tools" in closing[-1].content
    assert not [m for m in closing if m.__class__.__name__ == "ToolMessage"]
    looping.assert_consumed()
    silent = ScriptedChatModel(
        steps=[tool_call("list_shops", call_id=f"s{i}") for i in range(bot.MAX_TOOL_ROUNDS)] + [final("")]
    )
    assert "couldn't finish" in await bot.run_shopper({}, "loop", model_factory=lambda: silent)


@pytest.mark.asyncio
async def test_network_checkout_runs_for_a_listed_shop(monkeypatch):
    monkeypatch.setattr(settings, "network_enabled", True)
    monkeypatch.setattr(settings, "ucp_checkout_enabled", True)
    seen = {}

    async def resolve(slug):
        if slug != SHOP.slug:
            raise net.NetworkError("unknown_shop", "No such shop.", 404)
        return SHOP

    async def run(operation, shop, raw, **kwargs):
        seen.update(operation=operation, shop=shop.slug, raw=raw, **kwargs)
        return SimpleNamespace(status=201, body={"id": "chk_9", "status": "incomplete"}, headers={})

    real_run = api._run_for_network
    monkeypatch.setattr(net, "resolve_shop", resolve)
    monkeypatch.setattr(api, "_run_for_network", run)
    result = await api.network_checkout("tea-corner", "create_checkout", {"line_items": []})
    assert result == {"http_status": 201, "shop": "tea-corner", "id": "chk_9", "status": "incomplete"}
    assert seen["raw"] == b'{"line_items": []}' and seen["idempotency_key"] and seen["session_id"] is None
    await api.network_checkout("tea-corner", "get_checkout", None, "chk_9")
    assert seen["raw"] == b"" and seen["idempotency_key"] is None and seen["session_id"] == "chk_9"
    assert (await api.network_checkout("nobody", "get_checkout", None, "x"))["error"] == "unknown_shop"
    with pytest.raises(ValueError):
        await real_run("complete_checkout", SHOP, b"", idempotency_key=None, request_id=None, session_id=None)


@pytest.mark.asyncio
async def test_the_task_sends_with_the_bot_token_and_survives_failures(monkeypatch):
    from core.channels import providers
    from core.tasks import network_tasks

    sent = []

    async def send_reply(provider, credentials, sender, text):
        sent.append((provider, credentials, sender, text))

    monkeypatch.setattr(providers, "send_reply", send_reply)
    monkeypatch.setattr(settings, "network_telegram_bot_token", "123:token")
    await network_tasks._send("42", "hello")
    assert sent == [("telegram", {"access_token": "123:token"}, "42", "hello")]

    monkeypatch.setattr(settings, "network_enabled", True)
    monkeypatch.setattr(settings, "network_telegram_enabled", True)

    async def handled(update, *, send):
        return "answered"

    async def broken(update, *, send):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot, "handle_update", handled)
    assert await network_tasks._handle_async({}) == "answered"
    monkeypatch.setattr(bot, "handle_update", broken)
    assert await network_tasks._handle_async({}) == "failed"
    monkeypatch.setattr(network_tasks, "run_async", lambda coro: (coro.close(), "ran")[1])
    assert network_tasks.handle_telegram_update.run({}) == "ran"


@pytest.mark.asyncio
async def test_the_setup_script_validates_and_registers_the_webhook(monkeypatch, capsys):
    import scripts.connect_network_telegram_bot as script
    from core.channels import providers

    registered = []

    async def connect(credentials, callback):
        registered.append(callback)
        return "agenticorg_bot"

    monkeypatch.setattr(providers, "connect_telegram", connect)
    monkeypatch.setenv("AGENTICORG_NETWORK_TELEGRAM_BOT_TOKEN", "123456:synthetic-token")
    monkeypatch.setenv("AGENTICORG_NETWORK_TELEGRAM_WEBHOOK_SECRET", "s" * 32)
    assert await script._main("https://app.example/") == 0
    assert registered == ["https://app.example/api/v1/network/telegram/webhook"]
    out = capsys.readouterr().out
    assert "@agenticorg_bot" in out and "synthetic-token" not in out and "s" * 32 not in out

    async def refuse(credentials, callback):
        raise providers.ChannelError("telegram_bot_connected_elsewhere")

    monkeypatch.setattr(providers, "connect_telegram", refuse)
    assert await script._main("https://app.example") == 1
    monkeypatch.setenv("AGENTICORG_NETWORK_TELEGRAM_BOT_TOKEN", "not a token")
    assert await script._main("https://app.example") == 2


def test_the_setup_script_entry_point_reads_the_base_url(monkeypatch):
    import scripts.connect_network_telegram_bot as script

    seen = []

    async def fake_main(base: str) -> int:
        seen.append(base)
        return 0

    monkeypatch.setattr(script.sys, "argv", ["x", "--base-url", "https://app.example"])
    monkeypatch.setattr(script, "_main", fake_main)
    assert script.main() == 0
    assert seen == ["https://app.example"]
