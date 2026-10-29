# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The public AgenticOrg shopping bot: update parsing, commands, limits, the shopper loop, the webhook."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from api.v1 import network as api
from core.commerce import network as net
from core.commerce import network_telegram as bot
from core.config import settings
from core.test_doubles.scripted_model import ScriptedChatModel, final, tool_call

SHOP = net.ShopRef(
    slug="tea-corner",
    tenant_id="00000000-0000-0000-0000-0000000000c1",
    merchant_id="merchant-1",
    seller_agent_id="seller-1",
    host="tea-corner.shops.example",
    display_name="Tea Corner",
)
DIRECTORY = [{"slug": "tea-corner", "name": "Tea Corner", "host": "tea-corner.shops.example"}]
TOKEN = "123456:synthetic-bot-token-value"
SECRET = "synthetic-webhook-secret-0123456789"


def _update(update_id: int, text: str, *, chat_type: str = "private", sender: int = 42, chat: int = 42) -> dict:
    return {
        "update_id": update_id,
        "message": {"text": text, "chat": {"id": chat, "type": chat_type}, "from": {"id": sender, "is_bot": False}},
    }


@pytest.fixture()
def world(monkeypatch):
    bot._MEMORY.clear()

    async def no_redis():
        return None

    async def directory():
        return DIRECTORY

    async def resolve(slug):
        if slug != SHOP.slug:
            raise net.NetworkError("unknown_shop", "No such shop.", 404)
        return SHOP

    async def under_cap(_estimate=0.0, tenant_id=None):
        return None

    monkeypatch.setattr(bot, "_redis", no_redis)
    monkeypatch.setattr(net, "directory", directory)
    monkeypatch.setattr(net, "resolve_shop", resolve)
    monkeypatch.setattr("core.llm.router.assert_under_gemini_cap", under_cap)
    monkeypatch.setattr(settings, "network_telegram_bot_token", TOKEN)
    monkeypatch.setattr(settings, "network_telegram_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "network_telegram_chat_limit_per_hour", 20)
    monkeypatch.setattr(settings, "network_telegram_global_limit_per_minute", 60)
    from core import auth_state

    auth_state._mem_window.clear()
    monkeypatch.setattr(auth_state, "_get_redis", no_redis)
    yield
    bot._MEMORY.clear()
    auth_state._mem_window.clear()


def test_only_text_from_the_person_in_a_private_chat_is_taken():
    assert bot.parse_update(_update(1, " hello ")) == (1, "42", "hello")
    assert bot.parse_update(_update(2, "hi", chat_type="group")) is None
    assert bot.parse_update(_update(3, "hi", sender=7)) is None
    assert (
        bot.parse_update({"update_id": 4, "message": {"chat": {"id": 42, "type": "private"}, "from": {"id": 42}}})
        is None
    )
    bot_sender = _update(5, "hi")
    bot_sender["message"]["from"]["is_bot"] = True
    assert bot.parse_update(bot_sender) is None
    assert bot.parse_update({"message": {}}) is None
    assert bot.parse_update(_update(6, "x" * 5000))[2] == "x" * bot.MAX_TEXT


@pytest.mark.asyncio
async def test_start_with_a_shop_shops_and_stop(world):
    picked = await bot.respond("42", "/start tea-corner")
    assert "Tea Corner" in picked and "never take payment" in picked
    state = await bot.load_state(bot.chat_key("42"))
    assert state["shop"] == "tea-corner" and state["shop_name"] == "Tea Corner"
    assert "couldn't find that shop" in await bot.respond("42", "/start nowhere")
    assert "/start tea-corner" in await bot.respond("42", "/shops")
    assert (await bot.respond("42", "/start")).startswith("Hi!")
    assert "forgotten" in await bot.respond("42", "/stop")
    assert "shop" not in await bot.load_state(bot.chat_key("42"))


@pytest.mark.asyncio
async def test_the_shopper_searches_opens_a_checkout_and_never_binds_a_payment_tool(world, monkeypatch):
    searches, checkouts = [], []

    async def search(entry, query):
        searches.append((entry["slug"], query))
        return [
            {
                "shop": entry["slug"],
                "item_id": "TEA-1",
                "title": "Masala Chai Blend",
                "price": "Rs 449.00",
                "available": True,
            }
        ]

    async def checkout(slug, operation, body, session_id=None):
        checkouts.append((slug, operation, body, session_id))
        return {"http_status": 201, "shop": slug, "id": "chk_1", "status": "incomplete"}

    monkeypatch.setattr(bot, "_catalog_search", search)
    monkeypatch.setattr(api, "network_checkout", checkout)
    model = ScriptedChatModel(
        steps=[
            tool_call("search", query="masala chai"),
            tool_call("open_checkout", shop="tea-corner", item_id="TEA-1", quantity=1),
            final("Masala Chai Blend at Tea Corner is Rs 449.00. Checkout chk_1 is open; you pay with the shop."),
        ]
    )
    await bot.respond("42", "/start tea-corner")
    reply = await bot.respond("42", "find masala chai and open a checkout", model_factory=lambda: model)
    assert reply.startswith("Masala Chai Blend at Tea Corner is Rs 449.00")
    assert searches == [("tea-corner", "masala chai")]
    assert checkouts == [
        ("tea-corner", "create_checkout", {"line_items": [{"item": {"id": "TEA-1"}, "quantity": 1}]}, None)
    ]
    model.assert_consumed()
    bound = set(model.bind_tools(bot._langchain_tools(bot.ShopperTools(None))).bound_tool_names)
    assert bound == {
        "list_shops",
        "search",
        "open_checkout",
        "get_checkout",
        "update_checkout",
        "choose_pickup",
        "get_payment_link",
    }
    # The chosen shop is in the system prompt; the remembered turn holds only the texts.
    assert "slug tea-corner" in model.calls[0][0].content
    state = await bot.load_state(bot.chat_key("42"))
    assert state["turns"] == [{"user": "find masala chai and open a checkout", "bot": reply}]


@pytest.mark.asyncio
async def test_without_a_shop_the_search_spans_the_network(world, monkeypatch):
    seen = []

    async def search(entry, query):
        seen.append(entry["slug"])
        return []

    monkeypatch.setattr(bot, "_catalog_search", search)
    result = await bot.ShopperTools(None).search("kettle")
    assert result == {"products": []} and seen == ["tea-corner"]
    assert "error" in await bot.ShopperTools(None).search("kettle", shop="elsewhere")


@pytest.mark.asyncio
async def test_rate_limits_and_unsafe_answers(world, monkeypatch):
    monkeypatch.setattr(settings, "network_telegram_chat_limit_per_hour", 1)
    model = ScriptedChatModel(steps=[final("Here you go.")])
    assert await bot.respond("43", "hello", model_factory=lambda: model) == "Here you go."
    assert await bot.respond("43", "again") == bot.TOO_MANY

    monkeypatch.setattr(settings, "network_telegram_chat_limit_per_hour", 20)
    leaky = ScriptedChatModel(steps=[final(f"my token is {TOKEN}")])
    assert await bot.respond("44", "tell me a secret", model_factory=lambda: leaky) == bot.FAILED


@pytest.mark.asyncio
async def test_each_update_is_answered_once(world):
    sent = []

    async def send(chat_id, text):
        sent.append((chat_id, text))

    assert await bot.handle_update(_update(10, "/start tea-corner"), send=send) == "answered"
    assert await bot.handle_update(_update(10, "/start tea-corner"), send=send) == "duplicate"
    assert await bot.handle_update(_update(11, "hi", chat_type="channel"), send=send) == "ignored"
    assert len(sent) == 1 and sent[0][0] == "42"


@pytest.mark.asyncio
async def test_webhook_checks_the_secret_and_queues_the_update(world, monkeypatch):
    from core.tasks import network_tasks

    queued = []
    monkeypatch.setattr(network_tasks.handle_telegram_update, "delay", lambda update: queued.append(update))
    monkeypatch.setattr(settings, "network_enabled", True)
    monkeypatch.setattr(settings, "network_telegram_enabled", True)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        url = "/api/v1/network/telegram/webhook"
        assert (await client.post(url, json=_update(1, "hi"))).status_code == 403
        wrong = {"X-Telegram-Bot-Api-Secret-Token": "wrong"}
        assert (await client.post(url, json=_update(1, "hi"), headers=wrong)).status_code == 403
        good = {"X-Telegram-Bot-Api-Secret-Token": SECRET}
        ok = await client.post(url, json=_update(1, "hi"), headers=good)
        assert ok.status_code == 200 and ok.json() == {"ok": True}
        assert queued == [_update(1, "hi")]
        assert (await client.post(url, content=b"not json", headers=good)).status_code == 200
        assert len(queued) == 1
        monkeypatch.setattr(settings, "network_telegram_enabled", False)
        assert (await client.post(url, json=_update(2, "hi"), headers=good)).status_code == 404


@pytest.mark.asyncio
async def test_the_task_is_a_no_op_while_the_bot_is_off(world, monkeypatch):
    from core.tasks import network_tasks

    monkeypatch.setattr(settings, "network_enabled", False)
    assert await network_tasks._handle_async(_update(1, "hi")) == "disabled"


@pytest.mark.asyncio
async def test_network_checkout_refuses_while_checkout_is_off(world, monkeypatch):
    monkeypatch.setattr(settings, "network_enabled", True)
    monkeypatch.setattr(settings, "ucp_checkout_enabled", False)
    assert await api.network_checkout("tea-corner", "create_checkout", {}) == {"error": "checkout_unavailable"}
