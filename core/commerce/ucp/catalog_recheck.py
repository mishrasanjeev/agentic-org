# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The catalog check at Complete: the cart still matches the shop's current catalog.

A checkout is priced when it is built or updated (``core.commerce.ucp.checkout``).
A buyer's agent may complete it hours later, after the shop changed a price or
sold out, so Complete compares the stored line items with the shop's catalog as
it is now (the latest synced or uploaded products) before any payment link is
issued or any executor runs:

* an item the shop no longer sells: ``item_unavailable``;
* a new price or currency: ``price_changed``;
* fewer in stock than the cart holds: ``out_of_stock``.

When something changed, ``rebuild_body`` gives the full update body of the stored
checkout (the same items, quantities, buyer, context and fulfillment choices), so
the engine re-prices it at the current catalog; the Complete is refused with the
change messages and the buyer's agent confirms the new total with the buyer
before it completes again. Nothing changes for a checkout whose items match.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from core.commerce.ucp import messages as msg
from core.commerce.ucp.items import CatalogItem, ItemCatalog
from core.commerce.ucp.merchant import MerchantUcp

# Members of the stored state that the rebuilt update cannot carry exactly (kept as they were).
# Loyalty is recomputed from the new total when the Shopify reads are available (``merged_state``).
KEPT_MEMBERS = ("consent", "attribution", "signals", "payment")


def _money(amount: int, currency: str) -> str:
    code = str(currency or "").upper()
    return f"{'Rs ' if code == 'INR' else code + ' '}{amount / 100:,.2f}"


def _title(line: Mapping[str, Any]) -> str:
    item = line.get("item") if isinstance(line.get("item"), Mapping) else {}
    return str(item.get("title") or item.get("id") or "An item")


def changes(
    line_items: Sequence[Mapping[str, Any]], found: Mapping[str, CatalogItem], currency: str | None
) -> list[dict[str, Any]]:
    """One error message per line item that no longer matches the catalog (empty when all match)."""
    out: list[dict[str, Any]] = []
    for index, line in enumerate(line_items):
        path = f"$.line_items[{index}]"
        stored = line.get("item") if isinstance(line.get("item"), Mapping) else {}
        item = found.get(str(stored.get("id") or ""))
        title = _title(line)
        if item is None:
            out.append(msg.error("item_unavailable", f"{title} is no longer sold by this shop.", path=path))
            continue
        if item.price_minor_units != stored.get("price") or (currency and item.currency != currency):
            out.append(
                msg.error(
                    "price_changed",
                    f"The price of {title} is now {_money(item.price_minor_units, item.currency)}. "
                    "The checkout shows the new total; confirm it with the buyer, then complete again.",
                    path=path,
                )
            )
        quantity = line.get("quantity")
        if isinstance(quantity, int) and item.inventory is not None:
            whole_units = -(-quantity // (10**item.quantity_unit.scale))
            if whole_units > item.inventory:
                out.append(
                    msg.error(
                        "out_of_stock",
                        f"Only {max(item.inventory, 0)} of {title} in stock now; change the quantity or the item.",
                        path=path,
                    )
                )
    return out


async def catalog_changes(
    catalog: ItemCatalog, merchant: MerchantUcp, state: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """The change messages for the stored checkout ``state`` against the catalog as it is now."""
    line_items = [li for li in state.get("line_items") or [] if isinstance(li, Mapping)]
    if not line_items:
        return []
    ids = [str((li.get("item") or {}).get("id") or "") for li in line_items]
    found = await catalog.lookup(merchant, [i for i in ids if i])
    return changes(line_items, found, state.get("currency"))


def not_repeated(found: Sequence[Mapping[str, Any]], kept: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The change messages the re-priced checkout does not already state for the same line.

    The engine names a short or unknown item itself (``out_of_stock``, ``not_found``); a new
    price it never names. When nothing is left, one info message still says why Complete stopped.
    """
    stated = {m.get("path") for m in kept if isinstance(m, Mapping) and m.get("type") == "error"}
    out = [dict(m) for m in found if m.get("code") == "price_changed" or m.get("path") not in stated]
    if out:
        return out
    return [
        msg.info("The shop's catalog changed since this checkout was built; see its messages.", code="catalog_changed")
    ]


def rebuild_body(state: Mapping[str, Any]) -> dict[str, Any]:
    """The full-replacement update body of the stored checkout: same items, buyer, context and choices.

    Fulfillment methods are sent by id and type only; the engine takes their
    destinations and selected options from the stored state (``build_fulfillment``
    matches each method to its previous one).
    """
    lines = []
    for line in state.get("line_items") or []:
        if not isinstance(line, Mapping):
            continue
        out: dict[str, Any] = {
            "id": line.get("id"),
            "item": {"id": (line.get("item") or {}).get("id")},
            "quantity": line.get("quantity"),
        }
        if line.get("parent_id"):
            out["parent_id"] = line["parent_id"]
        lines.append(out)
    body: dict[str, Any] = {"line_items": lines}
    if isinstance(state.get("buyer"), Mapping):
        body["buyer"] = dict(state["buyer"])
    if isinstance(state.get("context"), Mapping):
        body["context"] = dict(state["context"])
    fulfillment = state.get("fulfillment") if isinstance(state.get("fulfillment"), Mapping) else None
    methods = [m for m in (fulfillment or {}).get("methods") or [] if isinstance(m, Mapping)]
    if methods:
        body["fulfillment"] = {
            "methods": [
                {"id": m.get("id"), "type": m.get("type"), "line_item_ids": list(m.get("line_item_ids") or [])}
                for m in methods
            ]
        }
    return body


def merged_state(
    fresh: Mapping[str, Any], stored: Mapping[str, Any], *, loyalty_recomputed: bool = False
) -> dict[str, Any]:
    """The re-priced state plus what only the stored state holds (platform, hand-offs, consent, payment...).

    ``loyalty_recomputed``: the re-pricing read the buyer's membership again (Shopify reads were
    available), so its loyalty forecast is for the new total. Otherwise, or when the lookup gave no
    membership (it failed), the stored loyalty is kept rather than erased.
    """
    out = dict(fresh)
    for key, value in stored.items():
        if key not in out or key in KEPT_MEMBERS:
            out[key] = value
    if stored.get("loyalty") is not None and (not loyalty_recomputed or out.get("loyalty") is None):
        # Without the reads, or when the membership lookup failed, the stored loyalty is kept, not erased.
        out["loyalty"] = stored["loyalty"]
    return out


def _whole_units(quantity: int, item: CatalogItem) -> int:
    return -(-quantity // (10**item.quantity_unit.scale))


def _clock(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%H:%M UTC")


def held_stock_changes(
    line_items: Sequence[Mapping[str, Any]],
    found: Mapping[str, CatalogItem],
    held: Mapping[str, Sequence[tuple[int, datetime]]],
    *,
    sold: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """``out_of_stock`` for each line whose stock, less what is sold and what others hold, is short.

    ``held`` maps an item id to ``(quantity, until)`` per checkout holding it; ``sold`` maps it to the
    units of orders made since the catalog was last read (whole units). The message says how many can
    be bought now and, when other buyers' pending payments hold the rest, when enough frees up if
    those payments do not go through.
    """
    out: list[dict[str, Any]] = []
    for index, line in enumerate(line_items):
        stored = line.get("item") if isinstance(line.get("item"), Mapping) else {}
        item_id = str(stored.get("id") or "")
        item = found.get(item_id)
        quantity = line.get("quantity")
        holds = sorted(held.get(item_id) or (), key=lambda hold: hold[1])
        sold_units = int((sold or {}).get(item_id) or 0)
        if item is None or item.inventory is None or not isinstance(quantity, int) or not (holds or sold_units):
            continue
        need = _whole_units(quantity, item)
        stock = item.inventory - sold_units
        units = [_whole_units(q, item) for q, _until in holds]
        held_total = sum(units)
        if stock - held_total >= need:
            continue
        path = f"$.line_items[{index}]"
        if stock < need:
            out.append(
                msg.error(
                    "out_of_stock",
                    f"Only {max(stock, 0)} of {_title(line)} left: the rest has been sold since the shop's "
                    "catalog was last read. Change the quantity or the item.",
                    path=path,
                )
            )
            continue
        free_at: datetime | None = None
        remaining = held_total
        for count, (_q, until) in zip(units, holds, strict=True):
            remaining -= count
            if stock - remaining >= need:
                free_at = until
                break
        available = max(stock - held_total, 0)
        when = f" until about {_clock(free_at)}" if free_at is not None else ""
        out.append(
            msg.error(
                "out_of_stock",
                f"Only {available} of {_title(line)} can be bought now: {held_total} are held by another "
                f"buyer's pending payment{when}. If that payment does not go through, they free up then; "
                "change the quantity or try again later.",
                path=path,
            )
        )
    return out


def stock_unconfirmed(line_items: Sequence[Mapping[str, Any]], tracked: set[str]) -> list[dict[str, Any]]:
    """``out_of_stock`` for every tracked line when the held or sold stock could not be counted in full."""
    return [
        msg.error(
            "out_of_stock",
            f"The stock of {_title(line)} cannot be confirmed right now because many payments are pending; "
            "try again in a few minutes.",
            path=f"$.line_items[{index}]",
        )
        for index, line in enumerate(line_items)
        if str(((line.get("item") if isinstance(line.get("item"), Mapping) else {}) or {}).get("id") or "") in tracked
    ]


async def stock_short_lines(
    catalog: ItemCatalog, merchant: MerchantUcp, state: Mapping[str, Any], *, sold: Mapping[str, int] | None = None
) -> list[dict[str, Any]]:
    """The lines whose quantity the catalog's stock no longer covers (``out_of_stock`` of ``catalog_changes``).

    For a payment that already arrived: one entry per short line (``line_item_id``, ``item_id``,
    ``title``, ``quantity`` in whole units, ``in_stock``). ``sold`` (units of orders made since the
    catalog was last read) is taken off the catalog's stock first. An item the catalog no longer
    lists, or one whose stock is not tracked, is not counted as short.
    """
    import dataclasses

    line_items = [li for li in state.get("line_items") or [] if isinstance(li, Mapping)]
    if not line_items:
        return []
    ids = [str((li.get("item") or {}).get("id") or "") for li in line_items]
    found = dict(await catalog.lookup(merchant, [i for i in ids if i]))
    for item_id, units in (sold or {}).items():
        item = found.get(item_id)
        if item is not None and item.inventory is not None and units:
            found[item_id] = dataclasses.replace(item, inventory=item.inventory - int(units))
    short: list[dict[str, Any]] = []
    for message in changes(line_items, found, state.get("currency")):
        path = str(message.get("path") or "")
        if message.get("code") != "out_of_stock" or not path.startswith("$.line_items["):
            continue
        line = line_items[int(path[len("$.line_items[") : -1])]
        item = found[str((line.get("item") or {}).get("id") or "")]
        short.append(
            {
                "line_item_id": line.get("id"),
                "item_id": item.id,
                "title": _title(line),
                "quantity": _whole_units(int(line["quantity"]), item),
                "in_stock": max(int(item.inventory or 0), 0),
            }
        )
    return short
