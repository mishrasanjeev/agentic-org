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
from typing import Any

from core.commerce.ucp import messages as msg
from core.commerce.ucp.items import CatalogItem, ItemCatalog
from core.commerce.ucp.merchant import MerchantUcp

# Members of the stored state that the rebuilt update cannot carry exactly (kept as they were).
KEPT_MEMBERS = ("consent", "attribution", "signals", "payment", "loyalty")


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


def merged_state(fresh: Mapping[str, Any], stored: Mapping[str, Any]) -> dict[str, Any]:
    """The re-priced state plus what only the stored state holds (platform, hand-offs, consent, payment...)."""
    out = dict(fresh)
    for key, value in stored.items():
        if key not in out or key in KEPT_MEMBERS:
            out[key] = value
    return out
