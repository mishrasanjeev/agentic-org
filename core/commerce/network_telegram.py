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
* ``/avoid <shop>`` leaves a shop out of the chat's searches and checkouts (``/avoid`` lists the
  avoided shops, ``/avoid clear`` empties the list, ``/allow <shop>`` takes one off it); the buyer
  may also just say so, and the shopper's ``avoid_shop`` tool records it. Every other shop is
  still searched in a fresh random order.
* Chat state (the chosen shop, the avoided shops and the last few turns, never tool output) lives
  in Redis under a SHA-256 of the chat id for seven days; the turns expire after 30 minutes of quiet.
* Each chat may send ``network_telegram_chat_limit_per_hour`` messages an hour, and the bot as a
  whole ``network_telegram_global_limit_per_minute`` a minute; over either, a short "try later".
* Telegram update ids are claimed once, so a resent update is never answered twice.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
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
from core.commerce.c6z_runtime_vertical import contains_private_or_executable_value
from core.commerce.catalog_upload import gtin_or_none, reads_private_or_reserved
from core.config import settings

logger = structlog.get_logger()

STATE_TTL_SECONDS = 7 * 24 * 3600
TURN_TTL_SECONDS = 30 * 60
MAX_TURNS = 6
MAX_TOOL_ROUNDS = 8
UPDATE_TTL_SECONDS = 24 * 3600
SEARCH_TIMEOUT_SECONDS = 8.0
# A search across the network asks this many shops at once, and at most this many in all, chosen
# afresh in random order on every search so no shop is always first or always left out. The answer
# keeps about NETWORK_PRODUCTS products in all (fewer per shop when more shops answer) with shorter
# descriptions, so it fits the tool-result cap instead of being cut down to the first few shops.
SEARCH_CONCURRENCY = 10
MAX_SEARCH_SHOPS = 20
NETWORK_PRODUCTS = 20
NETWORK_DESCRIPTION = 120
SHOP_PRODUCTS = 5
MAX_TEXT = 1000
# At most this many shops on a chat's avoid list.
MAX_AVOIDED_SHOPS = 50

WELCOME = (
    "Hi! I can find products at shops on the AgenticOrg network and open a checkout for you. "
    "I never take payment: when your checkout is ready I give you a link to the shop's own payment page.\n\n"
    'Try: "find masala chai". Send /shops to see the shops, /avoid <shop> to leave a shop out, '
    "or /stop to make me forget this chat."
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
        "- Before a payment link, the shop checks the cart against its current prices and stock. If",
        "  get_payment_link says a price changed, tell the buyer the new summary.total and call get_payment_link",
        "  again only after they agree. If it says an item is short or no longer sold, tell the buyer and offer",
        "  to change the quantity or the item.",
        "- Keep answers short and plain, for a phone screen. No markdown tables.",
        "- If the buyer has not picked a shop, search across all shops; say which shop each product is from.",
        "- Shops come back in a random order on every search, and AgenticOrg does not rank them: never favour a",
        "  shop or product for being listed first. Compare what the buyer cares about (price, stock, details).",
        "  When same_product_at_several_shops lists one product at several shops, show the buyer the choice.",
        "- The buyer chooses the shop. When products come from several shops, ask which shop they want before",
        "  opening a checkout, and tell them they can leave a shop out (/avoid <shop>).",
        "- If the buyer says they do not want a shop, call avoid_shop with it. Never search, open a checkout or",
        "  get a payment link at a shop the buyer avoids; searches already leave those shops out. If the buyer",
        "  wants an avoided shop again, call allow_shop first.",
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


MAX_DETAILS = 4
MAX_SHOP_DETAILS = 3
MAX_DETAIL_CHARS = 100
MAX_SUMMARY_DESCRIPTION = 300
# Tool results are cut at 8000 characters; search answers are built to fit well inside that.
SEARCH_RESULT_BUDGET = 7000


def _product_summary(shop: str, product: dict[str, Any]) -> dict[str, Any]:
    variants = product.get("variants") if isinstance(product.get("variants"), list) else []
    first = variants[0] if variants and isinstance(variants[0], dict) else {}
    price = (product.get("price_range") or {}).get("min") if isinstance(product.get("price_range"), dict) else None
    available = first.get("availability", {}).get("available") if isinstance(first.get("availability"), dict) else None
    summary = {
        "shop": shop,
        # Checkouts take the variant, so the model sees only the id it should buy with.
        "item_id": first.get("id") or product.get("id"),
        "title": product.get("title"),
        "price": _money(price or first.get("price")),
        "available": available,
        "description": str(
            (product.get("description") or {}).get("plain")
            if isinstance(product.get("description"), dict)
            else product.get("description") or ""
        )[:MAX_SUMMARY_DESCRIPTION]
        or None,
    }
    barcode = _barcode(first)
    if barcode:
        summary["barcode"] = barcode
    metadata: dict[str, Any] = product["metadata"] if isinstance(product.get("metadata"), dict) else {}
    details = _detail_lines(metadata.get("attributes"), MAX_DETAILS)
    shop_details = _detail_lines(metadata.get("shop_details"), MAX_SHOP_DETAILS)
    if metadata.get("brand"):
        summary["brand"] = metadata["brand"]
    if details:
        # The shop's own declarations (ingredients, licence numbers and so on), kept short for the tool result cap.
        summary["details_declared_by_shop"] = details
    if shop_details:
        # Declared once for the whole shop (a food-safety licence, a certification).
        summary["shop_details_declared_by_shop"] = shop_details
    return summary


def _offer_price(product: dict[str, Any]) -> dict[str, Any]:
    """``{"_price": (currency, minor units)}`` of the variant the summary offers (its ``item_id`` and
    ``barcode``), for grouping one product across shops; ``_fit_search_results`` removes it before the
    model sees the result. The product's lowest price may belong to another variant, so it is not used."""
    variants = product.get("variants") if isinstance(product.get("variants"), list) else []
    first = variants[0] if variants and isinstance(variants[0], dict) else {}
    money = first.get("price")
    if isinstance(money, dict) and isinstance(money.get("amount"), int) and isinstance(money.get("currency"), str):
        return {"_price": (money["currency"], money["amount"])}
    return {}


def _barcode(variant: dict[str, Any]) -> str | None:
    """The variant's GTIN (8, 12, 13 or 14 digits with a valid check digit), as the UCP catalog serves it."""
    for item in variant.get("barcodes") or []:
        if isinstance(item, dict) and item.get("type") == "GTIN" and (gtin := gtin_or_none(item.get("value"))):
            return gtin
    return None


def _same_product_groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Products with the same barcode at two or more shops, each shop's offer listed once.

    Prices are compared only in the group's main currency (the one most of its offers use): those offers
    come first, lowest price first, and each shop's cheapest one stands for it. Offers in another currency,
    or without a readable price, follow in the order the shops answered (shuffled on every search).
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if item.get("barcode"):
            groups.setdefault(str(item["barcode"]), []).append(item)
    same: list[dict[str, Any]] = []
    for barcode, offers in groups.items():
        if len({str(offer.get("shop")) for offer in offers}) < 2:
            continue
        priced = [offer["_price"][0] for offer in offers if isinstance(offer.get("_price"), tuple)]
        main = max(priced, key=lambda currency: (priced.count(currency), -priced.index(currency))) if priced else None
        compared = sorted(
            (offer for offer in offers if isinstance(offer.get("_price"), tuple) and offer["_price"][0] == main),
            key=lambda offer: offer["_price"][1],
        )
        rest = [offer for offer in offers if all(offer is not chosen for chosen in compared)]
        ranked: list[dict[str, Any]] = []
        for offer in compared + rest:
            if offer.get("shop") not in {kept.get("shop") for kept in ranked}:
                ranked.append(offer)
        same.append(
            {
                "barcode": barcode,
                "title": ranked[0].get("title"),
                "offers": [
                    {
                        "shop": o.get("shop"),
                        "item_id": o.get("item_id"),
                        "price": _money({"amount": o["_price"][1], "currency": o["_price"][0]})
                        if isinstance(o.get("_price"), tuple)
                        else o.get("price"),
                        "available": o.get("available"),
                    }
                    for o in ranked
                ],
            }
        )
    return same


def _detail_lines(items: Any, limit: int) -> list[str]:
    return [
        f"{item.get('name')}: {str(item.get('value'))[:MAX_DETAIL_CHARS]}"
        for item in items or []
        if isinstance(item, dict) and item.get("name") and item.get("value")
        and not reads_private_or_reserved(str(item["name"]))
        and not contains_private_or_executable_value(f"{item['name']} {item['value']}")
    ][:limit]


def _fit_search_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Products from every shop in turn, trimmed to fit the tool-result cap as whole products.

    Shops take turns in the order their results arrive (the search shuffles that order every time),
    and the same product at several shops (by barcode) is listed with each shop's offer.
    """
    by_shop: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        by_shop.setdefault(str(item.get("shop")), []).append(item)
    ordered = [
        shop_items[index]
        for index in range(max((len(items) for items in by_shop.values()), default=0))
        for shop_items in by_shop.values()
        if index < len(shop_items)
    ]
    shops_with_details: set[str] = set()
    for item in ordered:
        # The shop's details are the same on each of its products; the first one carries them.
        if "shop_details_declared_by_shop" in item:
            if item.get("shop") in shops_with_details:
                item.pop("shop_details_declared_by_shop")
            shops_with_details.add(str(item.get("shop")))
    same = _same_product_groups(ordered)
    for item in ordered:
        item.pop("_price", None)
    body: dict[str, Any] = {"products": ordered}
    if len(by_shop) > 1:
        body["shops_in_random_order"] = True
    if same:
        body["same_product_at_several_shops"] = same
    if len(json.dumps(body, default=str)) <= SEARCH_RESULT_BUDGET:
        return body
    for item in ordered:
        item.pop("details_declared_by_shop", None)
        item.pop("shop_details_declared_by_shop", None)
    while ordered and len(json.dumps(body, default=str)) > SEARCH_RESULT_BUDGET:
        # Drop whole products from the end (the shops' later turns), keeping each group's offers in step.
        dropped = ordered.pop()
        _drop_offer(body, dropped)
    body["more_results_left_out"] = True
    return body


def _drop_offer(body: dict[str, Any], product: dict[str, Any]) -> None:
    groups = body.get("same_product_at_several_shops") or []
    for group in groups:
        group["offers"] = [
            offer
            for offer in group["offers"]
            if (offer.get("shop"), offer.get("item_id")) != (product.get("shop"), product.get("item_id"))
        ]
    kept = [group for group in groups if len({offer.get("shop") for offer in group["offers"]}) >= 2]
    if kept:
        body["same_product_at_several_shops"] = kept
    else:
        body.pop("same_product_at_several_shops", None)


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
    return [
        {**_product_summary(entry["slug"], p), **_offer_price(p)}
        for p in (products or [])[:SHOP_PRODUCTS]
        if isinstance(p, dict)
    ]


def _shuffle(items: list[Any]) -> None:
    """Shuffle in place (a seam for tests); fairness only, so the default generator is enough."""
    random.shuffle(items)  # noqa: S311  # nosec B311 - not security-sensitive: the order of shops in one answer


def _avoided_refusal(slug: str) -> dict[str, Any]:
    return {
        "error": f"The buyer asked to avoid the shop {slug!r}. Do not use it; offer another shop, "
        "or call allow_shop only if the buyer asks for this shop again."
    }


async def _find_shop(text: str) -> dict[str, Any] | None:
    """The directory entry a buyer named, by slug or by shop name (case does not matter)."""
    wanted = text.strip().lower()
    if not wanted:
        return None
    shops = await net.directory()
    return next((s for s in shops if s["slug"] == wanted), None) or next(
        (s for s in shops if str(s.get("name") or "").strip().lower() == wanted), None
    )


class ShopperTools:
    """The bot's tools; ``shop`` defaults to the chat's chosen shop.

    ``avoided`` is the chat's avoid list (the same list object as the chat state's, so what the
    ``avoid_shop`` and ``allow_shop`` tools change is saved with the turn). ``state`` is the chat
    state: avoiding the chosen shop also unpicks it there. No checkout tool acts at an avoided shop.
    """

    def __init__(
        self, chosen_shop: str | None, avoided: list[str] | None = None, state: dict[str, Any] | None = None
    ) -> None:
        self.chosen_shop = chosen_shop
        self.avoided: list[str] = avoided if avoided is not None else []
        self._state = state

    def _refused(self, slug: str | None) -> dict[str, Any] | None:
        return _avoided_refusal(slug) if slug and slug in self.avoided else None

    async def avoid_shop(self, shop: str) -> dict[str, Any]:
        """Leave a shop out of this chat's searches and checkouts."""
        found = await _find_shop(shop)
        if found is None:
            return {"error": f"No shop {shop!r} in the network."}
        if found["slug"] not in self.avoided:
            if len(self.avoided) >= MAX_AVOIDED_SHOPS:
                return {"error": f"The buyer already avoids {MAX_AVOIDED_SHOPS} shops."}
            self.avoided.append(found["slug"])
        if self.chosen_shop == found["slug"]:
            # The chosen shop was the one avoided: the chat shops the rest of the network again.
            self.chosen_shop = None
            if self._state is not None:
                self._state.pop("shop", None)
                self._state.pop("shop_name", None)
        return {"avoided_shops": list(self.avoided)}

    async def allow_shop(self, shop: str) -> dict[str, Any]:
        """Take a shop off this chat's avoid list."""
        found = await _find_shop(shop)
        slug = found["slug"] if found else shop.strip().lower()
        if slug in self.avoided:
            self.avoided.remove(slug)
        return {"avoided_shops": list(self.avoided)}

    async def list_shops(self) -> dict[str, Any]:
        shops = await net.directory()
        return {"shops": [{"shop": s["slug"], "name": s["name"]} for s in shops]}

    async def search(self, query: str, shop: str | None = None) -> dict[str, Any]:
        slug = shop or self.chosen_shop
        if refused := self._refused(slug):
            return refused
        shops = await net.directory()
        if slug:
            targets = [s for s in shops if s["slug"] == slug]
            if not targets:
                return {"error": f"No shop {slug!r} in the network."}
        else:
            # The buyer's avoided shops are left out first; then a fresh random order each search,
            # so no other shop is always first, or always past the limit.
            targets = [s for s in shops if s["slug"] not in self.avoided]
            _shuffle(targets)
            targets = targets[:MAX_SEARCH_SHOPS]
        gate = asyncio.Semaphore(SEARCH_CONCURRENCY)
        # One shop's best matches when it is the only one asked; across shops, a share of the answer each.
        per_shop = SHOP_PRODUCTS if slug else max(1, min(SHOP_PRODUCTS, NETWORK_PRODUCTS // max(1, len(targets))))

        async def one(entry: dict[str, Any]) -> list[dict[str, Any]]:
            async with gate:
                try:
                    # The client's timeout is per phase; this bounds the whole answer from one shop.
                    found = await asyncio.wait_for(_catalog_search(entry, query), SEARCH_TIMEOUT_SECONDS + 2)
                # enterprise-gate: broad-except-ok reason=one-shop-failing-never-fails-the-search
                except Exception:
                    logger.warning("network_telegram_search_failed", shop=entry["slug"])
                    return []
            items = found[:per_shop]
            if not slug:
                for item in items:
                    if isinstance(item.get("description"), str):
                        item["description"] = item["description"][:NETWORK_DESCRIPTION]
            return items

        answers = await asyncio.gather(*(one(entry) for entry in targets))
        body = _fit_search_results([item for answer in answers for item in answer])
        if not slug and len(shops) > len(targets):
            body["shops_searched"] = len(targets)
            body["shops_in_network"] = len(shops)
        if not slug and self.avoided:
            body["shops_avoided_by_buyer"] = list(self.avoided)
        return body

    async def open_checkout(self, shop: str, item_id: str, quantity: int = 1) -> dict[str, Any]:
        from api.v1.network import network_checkout  # noqa: PLC0415 - the API module wires the UCP runtime

        slug = shop or self.chosen_shop or ""
        if refused := self._refused(slug):
            return refused
        quantity = max(1, min(int(quantity), 20))
        body = {"line_items": [{"item": {"id": item_id}, "quantity": quantity}]}
        return _for_model(await network_checkout(slug, "create_checkout", body))

    async def get_checkout(self, shop: str, checkout_id: str) -> dict[str, Any]:
        from api.v1.network import network_checkout  # noqa: PLC0415

        slug = shop or self.chosen_shop or ""
        if refused := self._refused(slug):
            # Not even a read: a checkout there could still carry the shop's payment link.
            return refused
        return _for_model(await network_checkout(slug, "get_checkout", None, checkout_id))

    async def update_checkout(self, shop: str, checkout_id: str, checkout: dict[str, Any]) -> dict[str, Any]:
        from api.v1.network import network_checkout  # noqa: PLC0415

        slug = shop or self.chosen_shop or ""
        if refused := self._refused(slug):
            return refused
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
        if refused := self._refused(slug):
            return refused
        return _for_model(await network_checkout(slug, "complete_checkout", None, checkout_id))

    async def choose_pickup(self, shop: str, checkout_id: str, location: str | None = None) -> dict[str, Any]:
        """Pickup takes three choices (method, location, option); make them from what the shop returns."""
        from api.v1.network import network_checkout  # noqa: PLC0415

        slug = shop or self.chosen_shop or ""
        if refused := self._refused(slug):
            return refused
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
        StructuredTool.from_function(
            coroutine=tools.avoid_shop,
            name="avoid_shop",
            description="The buyer does not want a shop (slug or name): leave it out of searches and checkouts.",
        ),
        StructuredTool.from_function(
            coroutine=tools.allow_shop,
            name="allow_shop",
            description="The buyer wants a shop they avoided again: take it off the avoid list.",
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
    tools = ShopperTools(state.get("shop"), _avoid_list(state), state)
    bound = _langchain_tools(tools)
    by_name = {t.name: t for t in bound}
    base = (model_factory or _default_model)()
    model = base.bind_tools(bound)
    system = SYSTEM_PROMPT
    if state.get("shop"):
        system += f"\n\nThe buyer chose the shop {state.get('shop_name') or state['shop']} (slug {state['shop']})."
    if tools.avoided:
        system += "\n\nThe buyer avoids these shops (slugs): " + ", ".join(tools.avoided) + "."
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


def _avoid_list(state: dict[str, Any]) -> list[str]:
    """The chat state's avoid list (created when missing)."""
    stored = state.get("avoid")
    avoided = [slug for slug in stored if isinstance(slug, str)] if isinstance(stored, list) else []
    state["avoid"] = avoided[:MAX_AVOIDED_SHOPS]
    return state["avoid"]


async def _avoid_command(state: dict[str, Any], command: str, argument: str) -> str:
    """``/avoid``, ``/avoid <shop>``, ``/avoid clear`` and ``/allow <shop>`` (no model call)."""
    avoided = _avoid_list(state)
    if command == "/avoid" and not argument:
        if not avoided:
            return "You're not avoiding any shop. Send /avoid <shop> to leave a shop out of your searches."
        return (
            "I leave these shops out of your searches and checkouts: "
            + ", ".join(avoided)
            + ". Send /allow <shop> to shop at one again, or /avoid clear to clear the list."
        )
    if command == "/avoid" and argument.lower() == "clear":
        avoided.clear()
        return "Done. I'll search every shop again."
    tools = ShopperTools(state.get("shop"), avoided, state)
    if command == "/allow":
        if not argument:
            return "Send /allow <shop> with the shop to shop at again."
        before = len(avoided)
        await tools.allow_shop(argument)
        if len(avoided) == before:
            return "That shop isn't on your avoid list. Send /avoid to see the list."
        return "Done. I'll include that shop in your searches again."
    if len(avoided) >= MAX_AVOIDED_SHOPS:
        return f"You're already avoiding {MAX_AVOIDED_SHOPS} shops. Send /avoid clear to start again."
    found = await _find_shop(argument)
    if found is None:
        return "I couldn't find that shop. Send /shops to see the shops on the network."
    await tools.avoid_shop(found["slug"])
    return (
        f"OK. I'll leave {found['name']} out of your searches and never open a checkout there. "
        f"Send /allow {found['slug']} to undo, or /avoid to see the list."
    )


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
    if command in ("/avoid", "/allow"):
        reply = await _avoid_command(state, command, argument.strip())
        await save_state(chat, state)
        return reply
    if command in ("/start", "/help"):
        slug = argument.strip().lower()
        if slug:
            try:
                shop = await net.resolve_shop(slug)
            except net.NetworkError:
                return "I couldn't find that shop. Send /shops to see the shops on the network."
            state.update(shop=shop.slug, shop_name=shop.display_name, turns=[])
            # Picking a shop is the buyer's choice: it comes off the avoid list.
            avoided = _avoid_list(state)
            if shop.slug in avoided:
                avoided.remove(shop.slug)
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
