# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The public AgenticOrg shopping bot on Telegram: anyone can shop the network's listed shops.

Separate from tenant channels (``core.channels``), which answer only a linked owner: this bot is a
platform surface. It answers any private chat, reads only the public network directory and the
shops' public catalogs, and opens checkouts through the network checkout path
(``api.v1.network.network_checkout``) as the AgenticOrg network platform. It never takes payment:
its ``get_payment_link`` tool asks the shop for the payment hand-off (``core.commerce.payment_handoff``)
and passes the buyer the link to the shop's own payment page (its Shopify cart or its Plural
hosted checkout), where the buyer pays. It has no tool that cancels or pays.

* ``/start <slug>`` (the deep link ``t.me/<bot>?start=<slug>``) picks a shop for the chat;
  ``/shops`` lists the network; ``/stop`` forgets the chat; anything else goes to the shopper.
* Chat state (the chosen shop and the last few turns, never tool output) lives in Redis under a
  SHA-256 of the chat id for seven days; the turns expire after 30 minutes of quiet.
* Each chat may send ``network_telegram_chat_limit_per_hour`` messages an hour, and the bot as a
  whole ``network_telegram_global_limit_per_minute`` a minute; over either, a short "try later".
* Telegram update ids are claimed once, so a resent update is never answered twice.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

from core import auth_state
from core.channels.replies import final_answer
from core.commerce import network as net
from core.commerce import payment_handoff as ph
from core.config import settings

logger = structlog.get_logger()

STATE_TTL_SECONDS = 7 * 24 * 3600
TURN_TTL_SECONDS = 30 * 60
MAX_TURNS = 6
MAX_TOOL_ROUNDS = 8
UPDATE_TTL_SECONDS = 24 * 3600
SEARCH_TIMEOUT_SECONDS = 8.0
MAX_TEXT = 1000

WELCOME = (
    "Hi! I can find products at shops on the AgenticOrg network and open a checkout for you. "
    "I never take payment: when your checkout is ready I give you a link to the shop's own payment page.\n\n"
    'Try: "find masala chai". Send /shops to see the shops, or /stop to make me forget this chat.'
)
BUSY = "I'm getting a lot of messages right now. Please try again in a little while."
TOO_MANY = "You've sent a lot of messages this hour. Please try again a bit later."
FAILED = "Sorry, something went wrong on my side. Please try again."

SYSTEM_PROMPT = "\n".join(
    [
        "You are the AgenticOrg shopping assistant on Telegram. You help a buyer find products at shops on the",
        "AgenticOrg network and open a checkout.",
        "",
        "Rules:",
        "- Use only facts the tools return. Copy product names, prices and stock exactly; never invent products,",
        "  prices, stores or policies.",
        "- Prices are in minor units: amount 44900 with currency INR is Rs 449.00.",
        "- To buy, open a checkout with open_checkout and the item_id from search (never another id). For store",
        "  pickup call choose_pickup with the checkout id",
        "  (pass location only when the shop names several). For delivery, read the checkout and update it with",
        "  the buyer's address and the shipping option the shop offers.",
        "- Every checkout tool returns a summary. Quote a checkout's total only from summary.total, never from a",
        "  search price. If summary.problems is not empty the checkout is not ready: fix it (for example open a",
        "  new checkout with the right item_id) or tell the buyer the problem.",
        "- You cannot take payment and must never ask for card, UPI or bank details. When summary.ready is true,",
        "  call get_payment_link with the checkout id. Give the buyer summary.total and the link from",
        "  summary.continue_url exactly as returned, and say they pay on the shop's own payment page. If no",
        "  continue_url comes back, give the checkout id and total and say the buyer completes payment with the shop.",
        "- Never invent or change a payment link; only summary.continue_url is a real one.",
        "- Keep answers short and plain, for a phone screen. No markdown tables.",
        "- If the buyer has not picked a shop, search across all shops; say which shop each product is from.",
    ]
)


# --- chat state -------------------------------------------------------------------------------

# enterprise-gate: process-local-ok reason=relaxed-runtime-fallback-only; strict runtimes use redis via auth_state
_MEMORY: dict[str, tuple[float, str]] = {}


def chat_key(chat_id: str) -> str:
    return hashlib.sha256(f"network-telegram:{chat_id}".encode()).hexdigest()


async def _redis() -> Any:
    return await auth_state._get_redis()


async def _get(key: str) -> str | None:
    r = await _redis()
    if r is not None:
        return await r.get(key)
    hit = _MEMORY.get(key)
    return hit[1] if hit and hit[0] > time.time() else None


async def _set(key: str, value: str, ttl: int, *, only_new: bool = False) -> bool:
    r = await _redis()
    if r is not None:
        return bool(await r.set(key, value, ex=ttl, nx=only_new))
    hit = _MEMORY.get(key)
    if only_new and hit and hit[0] > time.time():
        return False
    _MEMORY[key] = (time.time() + ttl, value)
    return True


async def _delete(key: str) -> None:
    r = await _redis()
    if r is not None:
        await r.delete(key)
    _MEMORY.pop(key, None)


async def load_state(chat: str) -> dict[str, Any]:
    raw = await _get(f"network-telegram:state:{chat}")
    state = json.loads(raw) if raw else {}
    if not isinstance(state, dict):
        state = {}
    stored = state.get("turns")
    turns: list[Any] = stored if isinstance(stored, list) else []
    if turns and time.time() - float(state.get("updated_at") or 0) > TURN_TTL_SECONDS:
        turns = []
    state["turns"] = turns[-MAX_TURNS:]
    return state


async def save_state(chat: str, state: dict[str, Any]) -> None:
    state["updated_at"] = time.time()
    state["turns"] = list(state.get("turns") or [])[-MAX_TURNS:]
    await _set(f"network-telegram:state:{chat}", json.dumps(state), STATE_TTL_SECONDS)


async def claim_update(update_id: int) -> bool:
    """True the first time an update id is seen (Telegram resends unacknowledged updates)."""
    return await _set(f"network-telegram:update:{update_id}", "1", UPDATE_TTL_SECONDS, only_new=True)


def parse_update(update: Any) -> tuple[int, str, str] | None:
    """``(update_id, chat_id, text)`` for a text message in a private chat; None for anything else."""
    if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
        return None
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    raw_chat, raw_sender = message.get("chat"), message.get("from")
    chat: dict[str, Any] = raw_chat if isinstance(raw_chat, dict) else {}
    sender: dict[str, Any] = raw_sender if isinstance(raw_sender, dict) else {}
    text = message.get("text")
    if chat.get("type") != "private" or not isinstance(text, str) or not text.strip():
        return None
    if sender.get("is_bot") or sender.get("id") != chat.get("id") or not isinstance(chat.get("id"), int):
        return None
    return update["update_id"], str(chat["id"]), text.strip()[:MAX_TEXT]


# --- tools ------------------------------------------------------------------------------------


def _money(price: Any) -> str | None:
    if isinstance(price, dict) and isinstance(price.get("amount"), int):
        currency = price.get("currency") or ""
        symbol = "Rs " if currency == "INR" else f"{currency} "
        return f"{symbol}{price['amount'] / 100:.2f}"
    return None


def _product_summary(shop: str, product: dict[str, Any]) -> dict[str, Any]:
    variants = product.get("variants") if isinstance(product.get("variants"), list) else []
    first = variants[0] if variants and isinstance(variants[0], dict) else {}
    price = (product.get("price_range") or {}).get("min") if isinstance(product.get("price_range"), dict) else None
    available = first.get("availability", {}).get("available") if isinstance(first.get("availability"), dict) else None
    return {
        "shop": shop,
        # Checkouts take the variant, so the model sees only the id it should buy with.
        "item_id": first.get("id") or product.get("id"),
        "title": product.get("title"),
        "price": _money(price or first.get("price")),
        "available": available,
        "description": (product.get("description") or {}).get("plain")
        if isinstance(product.get("description"), dict)
        else product.get("description"),
    }


async def _catalog_search(entry: dict[str, Any], query: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_SECONDS, follow_redirects=False) as client:
        response = await client.post(
            f"https://{entry['host']}/ucp/catalog/search",
            json={"query": query, "context": {"currency": "INR"}},
            headers={"UCP-Agent": f'profile="{settings.network_agent_profile}"', "Request-Id": str(uuid.uuid4())},
        )
    if response.status_code != 200:
        return []
    body = response.json()
    products = body.get("products") if isinstance(body, dict) else None
    return [_product_summary(entry["slug"], p) for p in (products or [])[:5] if isinstance(p, dict)]


class ShopperTools:
    """The bot's tools; ``shop`` defaults to the chat's chosen shop."""

    def __init__(self, chosen_shop: str | None) -> None:
        self.chosen_shop = chosen_shop

    async def list_shops(self) -> dict[str, Any]:
        shops = await net.directory()
        return {"shops": [{"shop": s["slug"], "name": s["name"]} for s in shops]}

    async def search(self, query: str, shop: str | None = None) -> dict[str, Any]:
        slug = shop or self.chosen_shop
        shops = await net.directory()
        targets = [s for s in shops if s["slug"] == slug] if slug else shops[:10]
        if slug and not targets:
            return {"error": f"No shop {slug!r} in the network."}
        results: list[dict[str, Any]] = []
        for entry in targets:
            try:
                results.extend(await _catalog_search(entry, query))
            # enterprise-gate: broad-except-ok reason=one-shop-failing-never-fails-the-search
            except Exception:
                logger.warning("network_telegram_search_failed", shop=entry["slug"])
        return {"products": results}

    async def open_checkout(self, shop: str, item_id: str, quantity: int = 1) -> dict[str, Any]:
        from api.v1.network import network_checkout  # noqa: PLC0415 - the API module wires the UCP runtime

        quantity = max(1, min(int(quantity), 20))
        body = {"line_items": [{"item": {"id": item_id}, "quantity": quantity}]}
        return _for_model(await network_checkout(shop or self.chosen_shop or "", "create_checkout", body))

    async def get_checkout(self, shop: str, checkout_id: str) -> dict[str, Any]:
        from api.v1.network import network_checkout  # noqa: PLC0415

        return _for_model(await network_checkout(shop or self.chosen_shop or "", "get_checkout", None, checkout_id))

    async def update_checkout(self, shop: str, checkout_id: str, checkout: dict[str, Any]) -> dict[str, Any]:
        from api.v1.network import network_checkout  # noqa: PLC0415

        slug = shop or self.chosen_shop or ""
        body = dict(checkout)
        if not body.get("line_items"):
            # An update replaces the checkout, and the model often sends only the part it changes.
            current = await network_checkout(slug, "get_checkout", None, checkout_id)
            body["line_items"] = _line_items(current)
        return _for_model(await network_checkout(slug, "update_checkout", body, checkout_id))

    async def get_payment_link(self, shop: str, checkout_id: str) -> dict[str, Any]:
        """The shop's payment page for a ready checkout (a hand-off: the buyer pays there, never here)."""
        from api.v1.network import network_checkout  # noqa: PLC0415

        slug = shop or self.chosen_shop or ""
        return _for_model(await network_checkout(slug, "complete_checkout", None, checkout_id))

    async def choose_pickup(self, shop: str, checkout_id: str, location: str | None = None) -> dict[str, Any]:
        """Pickup takes three choices (method, location, option); make them from what the shop returns."""
        from api.v1.network import network_checkout  # noqa: PLC0415

        slug = shop or self.chosen_shop or ""
        current = await network_checkout(slug, "get_checkout", None, checkout_id)
        items = _line_items(current)
        if not items:
            return _for_model(current)
        method: dict[str, Any] = {"type": "pickup"}
        answer = await network_checkout(
            slug, "update_checkout", {"line_items": items, "fulfillment": {"methods": [method]}}, checkout_id
        )
        locations = [d for d in _pickup_method(answer).get("destinations") or [] if isinstance(d, dict)]
        wanted = (location or "").strip().lower()
        matches = [d for d in locations if wanted and wanted in f"{d.get('id', '')} {d.get('name', '')}".lower()]
        if len(matches) != 1:
            matches = locations if len(locations) == 1 and not wanted else []
        if not matches:
            if not locations:
                return _for_model(answer)
            return {"choose_location": [{"id": d.get("id"), "name": d.get("name")} for d in locations]}
        method["selected_destination_id"] = matches[0].get("id")
        answer = await network_checkout(
            slug, "update_checkout", {"line_items": items, "fulfillment": {"methods": [method]}}, checkout_id
        )
        group = (_pickup_method(answer).get("groups") or [{}])[0]
        options = [o for o in group.get("options") or [] if isinstance(o, dict)]
        if group.get("selected_option_id") or not options:
            return _for_model(answer)
        method["groups"] = [{"selected_option_id": options[0].get("id")}]
        return _for_model(
            await network_checkout(
                slug, "update_checkout", {"line_items": items, "fulfillment": {"methods": [method]}}, checkout_id
            )
        )


def _for_model(answer: dict[str, Any]) -> dict[str, Any]:
    """A shop's checkout answer plus the few facts the model must quote from it, not guess."""
    if "http_status" not in answer:
        return answer
    raw_totals = answer.get("totals")
    totals = (
        {t.get("type"): t.get("amount") for t in raw_totals if isinstance(t, dict)}
        if isinstance(raw_totals, list)
        else {}
    )
    raw_messages = answer.get("messages")
    problems = [
        str(m.get("content"))
        for m in (raw_messages if isinstance(raw_messages, list) else [])
        if isinstance(m, dict) and m.get("type") == "error"
    ]
    if answer.get("error"):
        problems.append(str(answer.get("message") or answer["error"]))
    total = totals.get("total")
    continue_url = answer.get("continue_url")
    if answer.get("status") == "requires_escalation" and isinstance(continue_url, str):
        # The payment hand-off message is the buyer's next step, not a problem with the checkout.
        problems = [p for p in problems if p not in _HANDOFF_CONTENTS]
    summary = {
        "checkout_id": answer.get("id"),
        "status": answer.get("status"),
        "ready": answer.get("status") == "ready_for_complete" and not problems,
        "continue_url": continue_url if isinstance(continue_url, str) and continue_url.startswith("https://") else None,
        "total": _money({"amount": total, "currency": answer.get("currency") or "INR"})
        if isinstance(total, int)
        else None,
        "problems": problems,
    }
    return {"summary": summary, **answer}


_HANDOFF_CONTENTS = frozenset({ph.SHOPIFY_MESSAGE, ph.PLURAL_MESSAGE})


def _line_items(checkout: dict[str, Any]) -> list[dict[str, Any]]:
    """A checkout answer's line items, in the shape an update sends back."""
    items: list[dict[str, Any]] = []
    for line in checkout.get("line_items") or []:
        if isinstance(line, dict) and isinstance(line.get("item"), dict):
            items.append(
                {"id": line.get("id"), "item": {"id": line["item"].get("id")}, "quantity": line.get("quantity", 1)}
            )
    return items


def _pickup_method(checkout: dict[str, Any]) -> dict[str, Any]:
    methods = (
        (checkout.get("fulfillment") or {}).get("methods") if isinstance(checkout.get("fulfillment"), dict) else None
    )
    return next((m for m in methods or [] if isinstance(m, dict) and m.get("type") == "pickup"), {})


def _langchain_tools(tools: ShopperTools) -> list[Any]:
    from langchain_core.tools import StructuredTool  # noqa: PLC0415

    return [
        StructuredTool.from_function(
            coroutine=tools.list_shops, name="list_shops", description="List the shops on the AgenticOrg network."
        ),
        StructuredTool.from_function(
            coroutine=tools.search,
            name="search",
            description="Search products by text in one shop (shop slug) or, without shop, across the network.",
        ),
        StructuredTool.from_function(
            coroutine=tools.open_checkout,
            name="open_checkout",
            description="Open a checkout at a shop for one item_id from search and a quantity. Never takes payment.",
        ),
        StructuredTool.from_function(
            coroutine=tools.get_checkout,
            name="get_checkout",
            description="Read a checkout, including the fulfilment options the shop offers.",
        ),
        StructuredTool.from_function(
            coroutine=tools.update_checkout,
            name="update_checkout",
            description="Change a checkout with UCP JSON (line_items, fulfillment), for example a delivery address. "
            "Line items are kept when left out. No payment.",
        ),
        StructuredTool.from_function(
            coroutine=tools.get_payment_link,
            name="get_payment_link",
            description="When a checkout is ready (summary.ready), get the link to the shop's own payment page "
            "(summary.continue_url). The buyer pays there; this tool never takes payment.",
        ),
        StructuredTool.from_function(
            coroutine=tools.choose_pickup,
            name="choose_pickup",
            description="Choose store pickup for a checkout; pass location only when the shop has several. No payment.",
        ),
    ]


ModelFactory = Callable[[], Any]


def _default_model() -> Any:
    from core.langgraph.llm_factory import create_chat_model  # noqa: PLC0415

    return create_chat_model(temperature=0.1, max_tokens=1024)


async def run_shopper(state: dict[str, Any], text: str, model_factory: ModelFactory | None = None) -> str:
    """One buyer turn through the tool-calling model; returns the reply text."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: PLC0415

    from core.llm.router import assert_under_gemini_cap, assert_under_gemini_platform_cap  # noqa: PLC0415

    # The shopper belongs to no tenant. With recorded spend, the per-tenant cap would be measured
    # against every tenant's spend combined and stop the bot for the rest of the UTC day.
    if settings.recorded_spend_controls_enabled:
        await assert_under_gemini_platform_cap(0.0)
    else:
        await assert_under_gemini_cap(0.0)
    tools = ShopperTools(state.get("shop"))
    bound = _langchain_tools(tools)
    by_name = {t.name: t for t in bound}
    base = (model_factory or _default_model)()
    model = base.bind_tools(bound)
    system = SYSTEM_PROMPT
    if state.get("shop"):
        system += f"\n\nThe buyer chose the shop {state.get('shop_name') or state['shop']} (slug {state['shop']})."
    messages: list[Any] = [SystemMessage(content=system)]
    for turn in state.get("turns") or []:
        messages.append(HumanMessage(content=str(turn.get("user", ""))))
        messages.append(AIMessage(content=str(turn.get("bot", ""))))
    messages.append(HumanMessage(content=text))
    for _round in range(MAX_TOOL_ROUNDS):
        ai = await model.ainvoke(messages)
        messages.append(ai)
        if not getattr(ai, "tool_calls", None):
            content = ai.content if isinstance(ai.content, str) else json.dumps(ai.content)
            return content.strip()
        for call in ai.tool_calls:
            tool = by_name.get(call["name"])
            if tool is None:
                result: Any = {"error": f"Unknown tool {call['name']}"}
            else:
                try:
                    result = await tool.ainvoke(call["args"])
                # enterprise-gate: broad-except-ok reason=tool-failure-is-reported-to-the-model-not-the-buyer
                except Exception as exc:
                    result = {"error": type(exc).__name__}
            messages.append(ToolMessage(content=json.dumps(result, default=str)[:8000], tool_call_id=call["id"]))
    # Out of tool rounds: the work so far (often a checkout already opened) still deserves an answer.
    # The tool results go in as plain text, so the closing call needs no tools at all.
    results = "\n".join(str(m.content) for m in messages if isinstance(m, ToolMessage))[-12000:]
    closing = await base.ainvoke(
        [
            SystemMessage(content=system),
            HumanMessage(content=text),
            HumanMessage(
                content="Here is everything the shop tools returned for this request:\n"
                + results
                + "\n\nReply to the buyer now in a few short lines: what you found and did, with any checkout id "
                "and total. Do not call tools."
            ),
        ]
    )
    answer = closing.content if isinstance(closing.content, str) else json.dumps(closing.content)
    return answer.strip() or "I couldn't finish that. Please ask again more simply."


# --- one update -------------------------------------------------------------------------------

Sender = Callable[[str, str], Awaitable[None]]


async def respond(chat_id: str, text: str, *, model_factory: ModelFactory | None = None) -> str | None:
    """The reply to one buyer message (None: nothing to send)."""
    chat = chat_key(chat_id)
    state = await load_state(chat)
    command, _, argument = text.partition(" ")
    command = command.split("@", 1)[0].lower()
    if command == "/stop":
        await _delete(f"network-telegram:state:{chat}")
        return "Done. I've forgotten this chat. Send /start to begin again."
    if command == "/shops":
        shops = await net.directory()
        if not shops:
            return "No shops are open on the network right now."
        lines = [f"- {s['name']}: /start {s['slug']}" for s in shops[:20]]
        return "Shops on the AgenticOrg network:\n" + "\n".join(lines)
    if command in ("/start", "/help"):
        slug = argument.strip().lower()
        if slug:
            try:
                shop = await net.resolve_shop(slug)
            except net.NetworkError:
                return "I couldn't find that shop. Send /shops to see the shops on the network."
            state.update(shop=shop.slug, shop_name=shop.display_name, turns=[])
            await save_state(chat, state)
            return (
                f'You\'re shopping at {shop.display_name}. Ask me for a product, for example "show me your teas". '
                "I never take payment: you pay on the shop's own payment page."
            )
        return WELCOME
    if await auth_state.check_window_rate(
        "network_tg_chat", chat, int(settings.network_telegram_chat_limit_per_hour), 3600
    ):
        return TOO_MANY
    if await auth_state.check_window_rate(
        "network_tg_global", "all", int(settings.network_telegram_global_limit_per_minute), 60
    ):
        return BUSY
    try:
        answer = await run_shopper(state, text, model_factory)
    # enterprise-gate: broad-except-ok reason=the-buyer-gets-a-generic-reply-and-the-error-is-logged
    except Exception as exc:
        logger.warning("network_telegram_turn_failed", error_type=type(exc).__name__)
        return FAILED
    credentials = {
        "access_token": settings.network_telegram_bot_token,
        "webhook_secret": settings.network_telegram_webhook_secret,
    }
    safe = final_answer({"status": "completed", "output": {"answer": answer}}, credentials)
    if not safe:
        return FAILED
    state.setdefault("turns", []).append({"user": text, "bot": safe})
    await save_state(chat, state)
    return safe


async def handle_update(update: Any, *, send: Sender, model_factory: ModelFactory | None = None) -> str:
    """Answer one Telegram update once; returns what happened (for the task log)."""
    parsed = parse_update(update)
    if parsed is None:
        return "ignored"
    update_id, chat_id, text = parsed
    if not await claim_update(update_id):
        return "duplicate"
    reply = await respond(chat_id, text, model_factory=model_factory)
    if not reply:
        return "no_reply"
    await send(chat_id, reply)
    return "answered"
