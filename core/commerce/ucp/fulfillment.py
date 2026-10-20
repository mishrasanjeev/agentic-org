# SPDX-License-Identifier: Apache-2.0
"""The fulfillment extension (``dev.ucp.shopping.fulfillment``): methods, destinations, groups and options.

Request shape (both releases): ``fulfillment.methods[]`` with ``type``
(``shipping`` or ``pickup``), ``line_item_ids``, ``destinations`` (shipping
addresses the platform supplies), ``selected_destination_id`` and ``groups[]``
with ``selected_option_id``. Pickup destinations are the merchant's locations
(``business_location`` in 2026-08-25, ``retail_location`` in 2026-04-08) and
are response-only. Each method has one group holding its line items (the
default when the platform does not declare ``supports_multi_group``).

Options: shipping rates come from the merchant's Shopify delivery profiles
(flat-rate method definitions per zone, matched on the destination country
and the checkout currency); pickup is free at the selected location. The
business config decides multi-destination and method combinations
(``allows_multi_destination``/``allows_method_combinations`` in 2026-04-08,
``multi_destination[]``/``method_combinations`` in 2026-08-25).

The state kept here is version-neutral; ``core.commerce.ucp.renderers`` draws it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from core.commerce.ucp import messages as msg
from core.commerce.ucp.merchant import MerchantUcp
from core.commerce.ucp.shopify_reads import DeliveryRate

ADDRESS_FIELDS = (
    "extended_address",
    "street_address",
    "address_locality",
    "address_region",
    "address_country",
    "postal_code",
    "first_name",
    "last_name",
    "phone_number",
)
METHOD_TYPES = ("shipping", "pickup")

RatesSource = Callable[[MerchantUcp], Awaitable[Sequence[DeliveryRate]]]


class FulfillmentRequestError(ValueError):
    """A malformed fulfillment request (a protocol error, 400)."""


@dataclass
class FulfillmentResult:
    state: dict[str, Any] | None
    messages: list[dict[str, Any]] = field(default_factory=list)
    amount_minor_units: int = 0
    ready: bool = True


def _address(raw: Mapping[str, Any]) -> dict[str, str]:
    # 2026-04-08 request examples also use locality/region; accept them as aliases.
    aliases = {"locality": "address_locality", "region": "address_region", "country": "address_country"}
    out: dict[str, str] = {}
    for key, value in raw.items():
        name = aliases.get(key, key)
        if name in ADDRESS_FIELDS and isinstance(value, str | int) and str(value).strip():
            out[name] = str(value).strip()[:200]
    if "address_country" in out:
        out["address_country"] = out["address_country"].upper()
    return out


async def build_fulfillment(
    *,
    merchant: MerchantUcp,
    request: Any,
    previous: Mapping[str, Any] | None,
    line_item_ids: Sequence[str],
    currency: str | None,
    subtotal_minor_units: int,
    rates_source: RatesSource | None,
) -> FulfillmentResult:
    """Validate the requested methods and compute options, selections, messages and the fulfillment amount."""
    allowed = [
        m for m, on in (("shipping", merchant.fulfillment.shipping), ("pickup", merchant.fulfillment.pickup)) if on
    ]
    available = [{"type": m, "line_item_ids": list(line_item_ids), "fulfillable_on": "now"} for m in allowed]
    if request is None:
        result = FulfillmentResult(state={"methods": [], "available_methods": available}, ready=False)
        if line_item_ids:
            result.messages.append(msg.error("missing", "Choose how the order is fulfilled.", path="$.fulfillment"))
        return result
    if not isinstance(request, Mapping) or not isinstance(request.get("methods", []), list):
        raise FulfillmentRequestError("fulfillment.methods must be an array")
    previous_methods = list((previous or {}).get("methods") or [])
    messages: list[dict[str, Any]] = []
    methods: list[dict[str, Any]] = []
    rates: Sequence[DeliveryRate] | None = None
    total = 0
    ready = True
    covered: dict[str, int] = {}
    for index, raw in enumerate(request.get("methods") or []):
        if not isinstance(raw, Mapping):
            raise FulfillmentRequestError("each fulfillment method must be an object")
        prior = _match_previous(previous_methods, raw, index)
        method_type = str(raw.get("type") or (prior or {}).get("type") or "")
        path = f"$.fulfillment.methods[{index}]"
        if method_type not in METHOD_TYPES:
            raise FulfillmentRequestError(f"fulfillment method {index} needs a type of shipping or pickup")
        method_id = str((prior or {}).get("id") or raw.get("id") or f"fm_{index + 1}")
        if method_type not in allowed:
            messages.append(
                msg.error("unsupported_fulfillment_method", f"{method_type} is not offered.", path=f"{path}.type")
            )
            ready = False
        ids = raw.get("line_item_ids")
        if ids is None:
            ids = (prior or {}).get("line_item_ids") or list(line_item_ids)
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise FulfillmentRequestError("line_item_ids must be an array of strings")
        ids = [i for i in ids if i in line_item_ids] or list(line_item_ids)
        for item_id in ids:
            covered[item_id] = covered.get(item_id, 0) + 1
        if method_type == "pickup":
            destinations = [
                {"id": loc["id"], "kind": "business_location", "name": loc["name"], "address": dict(loc["address"])}
                for loc in merchant.pickup_locations
            ]
        else:
            destinations = _shipping_destinations(raw.get("destinations"), (prior or {}).get("destinations"))
        selected = raw.get("selected_destination_id", (prior or {}).get("selected_destination_id"))
        dest_ids = {d["id"] for d in destinations}
        if selected is not None and selected not in dest_ids:
            messages.append(
                msg.error(
                    "invalid_destination",
                    "The selected destination is not one of the destinations.",
                    path=f"{path}.selected_destination_id",
                )
            )
            selected = None
        group_raw = _first_group(raw.get("groups"))
        prior_group = ((prior or {}).get("groups") or [None])[0]
        group: dict[str, Any] = {
            "id": f"fg_{index + 1}",
            "line_item_ids": list(ids),
            "options": [],
            "selected_option_id": None,
        }
        if selected is None:
            messages.append(
                msg.error("missing", "Choose a fulfillment destination.", path=f"{path}.selected_destination_id")
            )
            ready = False
        else:
            destination = next(d for d in destinations if d["id"] == selected)
            if method_type == "pickup":
                group["options"] = [
                    {"id": f"pickup-{destination['id']}", "title": f"Pick up at {destination['name']}", "amount": 0}
                ]
            else:
                if rates is None:
                    rates = list(await rates_source(merchant)) if rates_source is not None else []
                group["options"] = _shipping_options(
                    rates,
                    destination,
                    currency,
                    subtotal_minor_units,
                    merchant.fulfillment.free_shipping_min_subtotal_minor_units,
                )
                if not group["options"]:
                    messages.append(
                        msg.error(
                            "address_undeliverable",
                            "No shipping option reaches this destination.",
                            path=f"{path}.selected_destination_id",
                        )
                    )
                    ready = False
            wanted_option = (group_raw or {}).get("selected_option_id", (prior_group or {}).get("selected_option_id"))
            option_ids = {o["id"] for o in group["options"]}
            if wanted_option in option_ids:
                group["selected_option_id"] = wanted_option
                total += next(o["amount"] for o in group["options"] if o["id"] == wanted_option)
            elif group["options"]:
                if wanted_option is not None:
                    messages.append(
                        msg.error(
                            "invalid_option",
                            "The selected fulfillment option is not available.",
                            path=f"{path}.groups[0].selected_option_id",
                        )
                    )
                else:
                    messages.append(
                        msg.error(
                            "missing", "Choose a fulfillment option.", path=f"{path}.groups[0].selected_option_id"
                        )
                    )
                ready = False
        methods.append(
            {
                "id": method_id,
                "type": method_type,
                "line_item_ids": list(ids),
                "destinations": destinations,
                "selected_destination_id": selected,
                "groups": [group],
            }
        )
    messages.extend(_combination_messages(merchant, methods))
    uncovered = [i for i in line_item_ids if covered.get(i, 0) == 0]
    if uncovered or not methods:
        messages.append(msg.error("missing", "Every line item needs a fulfillment method.", path="$.fulfillment"))
        ready = False
    if any(count > 1 for count in covered.values()):
        messages.append(
            msg.error("invalid_fulfillment", "A line item is in more than one method.", path="$.fulfillment")
        )
        ready = False
    if msg.has_errors(messages):
        ready = False
    return FulfillmentResult(
        state={"methods": methods, "available_methods": available},
        messages=messages,
        amount_minor_units=total,
        ready=ready,
    )


def _match_previous(previous: list[dict[str, Any]], raw: Mapping[str, Any], index: int) -> dict[str, Any] | None:
    if raw.get("id"):
        for method in previous:
            if method.get("id") == raw.get("id"):
                return method
    if index < len(previous) and (not raw.get("type") or previous[index].get("type") == raw.get("type")):
        return previous[index]
    return None


def _first_group(groups: Any) -> Mapping[str, Any] | None:
    if isinstance(groups, list) and groups and isinstance(groups[0], Mapping):
        return groups[0]
    return None


def _shipping_destinations(requested: Any, prior: Any) -> list[dict[str, Any]]:
    if requested is None:
        return [dict(d) for d in (prior or [])]
    if not isinstance(requested, list):
        raise FulfillmentRequestError("destinations must be an array")
    out: list[dict[str, Any]] = []
    for position, raw in enumerate(requested):
        if not isinstance(raw, Mapping):
            raise FulfillmentRequestError("each destination must be an object")
        address = _address(raw)
        if not address.get("address_country"):
            raise FulfillmentRequestError("a shipping destination needs address_country")
        out.append(
            {"id": str(raw.get("id") or f"dest_{position + 1}")[:80], "kind": "shipping_address", "address": address}
        )
    return out


def _shipping_options(
    rates: Sequence[DeliveryRate],
    destination: Mapping[str, Any],
    currency: str | None,
    subtotal: int,
    free_threshold: int | None,
) -> list[dict[str, Any]]:
    country = (destination.get("address") or {}).get("address_country")
    free = free_threshold is not None and subtotal >= free_threshold
    options = []
    for rate in rates:
        if not rate.serves(country) or (currency and rate.currency != currency):
            continue
        option: dict[str, Any] = {"id": rate.id, "title": rate.title, "amount": 0 if free else rate.amount_minor_units}
        if rate.description:
            option["description"] = rate.description
        if free:
            option["description"] = "Free shipping on this order."
        options.append(option)
    return options


def _combination_messages(merchant: MerchantUcp, methods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    by_type: dict[str, int] = {}
    for method in methods:
        by_type[method["type"]] = by_type.get(method["type"], 0) + 1
    for method_type, count in by_type.items():
        if count > 1 and method_type not in merchant.fulfillment.multi_destination:
            out.append(
                msg.error(
                    "multi_destination_unsupported",
                    f"Only one {method_type} destination is supported.",
                    path="$.fulfillment.methods",
                )
            )
    types = set(by_type)
    if len(types) > 1 and not any(set(combo) == types for combo in merchant.fulfillment.method_combinations):
        out.append(
            msg.error(
                "method_combination_unsupported",
                "These fulfillment methods cannot be combined.",
                path="$.fulfillment.methods",
            )
        )
    return out
