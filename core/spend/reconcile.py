# SPDX-License-Identifier: Apache-2.0
"""Reconciliation: a provider's billing month of metered usage against its invoices, in two figures.

**Scope.** The month is the provider's own billing month (``billing_date`` of
the records, ``core/spend/clock.py``), so a provider that closes its month in
Pacific time is compared over that month. Only tenant-billed usage is compared
(``billing_account`` ``tenant_key``; records whose account could not be
inferred are included and counted as ``unknown_account_records``).
Platform-key usage is summed beside it (``platform_billed_amount_inr``);
in-house serving, storage and GPU node hours have no provider invoice and are
never compared.

**Two figures per group** of the rollup (billing day, usage type, model, unit,
currency, card, price source, commitment), each in the invoice currency:

* *stored*: the amounts as written (in the invoice currency, or the records'
  INR amounts, or those INR amounts at the invoice currency's month-end
  rate); ``NULL`` for a group with unpriced or unconverted records;
* *re-priced*: the group priced again with ``pricing.price_with`` at the
  active cards in force on its billing day as known at run time, cards
  entered after metering included. A group the deployment fallback priced
  keeps its stored price (the fallback tables carry no dates); a group with
  no price at all is ``NULL``. Converted through INR at the month-end rates.

Both carry the month-level adjustments: the **tier true-up** of each contract
key ``(provider, usage_type, card model_sku, card unit, source)``, one volume
ladder over the month across card versions, and the **commitment overage**
at the commitment's overage price. Records are never re-priced here and never
change.

**Matching.** Invoice lines and groups meet on usage type, the canonical
model (the tenant's aliases applied to both) and the canonical unit
(``vocab.CANONICAL_UNIT``: cached input matches the cached-input line
whichever card priced it; a ``gb_month`` line matches ``gb_day`` usage, its
quantity times the month's days). Lines naming a model and a unit match
first, lines that leave either open take what remains, and whatever is left
on either side becomes an item of its own. A daily line gives a day item; a
monthly line gives a month item listing its days.

**Status.** An item is ``within_tolerance`` only when both figures are known
and each is within 1% of the invoice (exactly 1% passes); otherwise
``needs_review``. The provider level compares the sums the same way. A run is
``within_tolerance`` when the provider level is and no item needs review or
was accepted. Percentages are stored rounded away from zero, the verdicts use
the exact values. Non-usage lines (credits, tax, fees, commitment charges) are
reported as ``informational`` items and never enter the variance.

**Re-runs and acceptance.** A run supersedes the earlier runs of the month and
carries over an acceptance to an item whose key, invoice lines and both
figures are unchanged. Accepting needs a reason, an active tenant
administrator and someone other than the invoices' importer; every run, carry
over and acceptance is audited with the totals. A run goes stale when a card,
FX row, record or invoice it depends on changes after it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, ROUND_UP, Context, Decimal, localcontext
from typing import Any

import structlog
from sqlalchemy import and_, case, func, or_, select

from core.spend import audit, clock, invoices, locks, pricing, vocab
from core.spend.errors import SpendError, require_actor
from core.spend.pricing import Card, FxRate, Usage

logger = structlog.get_logger()

TOLERANCE_PCT = Decimal("1.00")
PCT_QUANT = Decimal("0.000001")
PCT_LIMIT = Decimal("99999999.999999")  # NUMERIC(14,6): a larger variance is stored at the bound
REASON_MIN = 10
REASON_MAX = 500
LIST_LIMIT = 200
FALLBACK_SOURCES = ("fallback_list", "fallback_override")
TENANT_BILLED = "tenant_key"
PLATFORM_BILLED = "platform_key"
_ZERO = Decimal("0")
_ONE = Decimal("1")
_CTX = Context(prec=38)

FxLookup = tuple[str, date, FxRate | None]


@dataclass(frozen=True)
class MeteredGroup:
    billing_date: date
    usage_type: str
    model: str
    unit: str  # a record unit
    currency: str | None
    rate_card_id: uuid.UUID | None
    price_source: str
    commitment_id: uuid.UUID | None
    quantity: Decimal
    amount: Decimal
    amount_inr: Decimal
    unconverted_amount: Decimal
    unpriced_quantity: Decimal
    overage_quantity: Decimal
    records: int
    unknown_account_records: int


@dataclass(frozen=True)
class PricedGroup:
    group: MeteredGroup
    canonical_unit: str
    quantity_canonical: Decimal
    stored_amount: Decimal | None  # in the invoice currency, before adjustments
    repriced_amount: Decimal | None  # in the invoice currency, before adjustments
    repriced_card: Card | None
    adjustments: tuple[dict[str, Any], ...]
    fx_converted: bool
    model: str = ""  # canonical, through the aliases known at run time
    repriced_currency: str | None = None
    factor: Decimal | None = None  # the re-priced currency -> the invoice currency
    card_quantity: Decimal | None = None  # the quantity in the re-pricing card's unit
    tierable: bool = False
    blend_card: Card | None = None
    stored_card: Card | None = None
    fx: tuple[FxLookup, ...] = ()

    @property
    def total_stored(self) -> Decimal | None:
        return _plus(self.stored_amount, self.adjustments)

    @property
    def total_repriced(self) -> Decimal | None:
        return _plus(self.repriced_amount, self.adjustments)


# ---------------------------------------------------------------- arithmetic


def _q(value: Decimal) -> Decimal:
    with localcontext(_CTX):
        return value.quantize(vocab.AMOUNT_QUANT, rounding=ROUND_HALF_EVEN)


def _qty(value: Decimal) -> Decimal:
    with localcontext(_CTX):
        return value.quantize(vocab.QTY_QUANT, rounding=ROUND_HALF_EVEN)


def _q_or_none(value: Decimal | None) -> Decimal | None:
    return None if value is None else _q(value)


def _dec(value: Any) -> Decimal:
    return Decimal(value) if value is not None else _ZERO


def _plus(base: Decimal | None, adjustments: Sequence[Mapping[str, Any]]) -> Decimal | None:
    if base is None:
        return None
    total = base
    for adjustment in adjustments:
        if adjustment.get("amount") is None:
            return None
        total += adjustment["amount"]
    return total


def _sum(values: Sequence[Decimal | None]) -> Decimal | None:
    """The sum, or ``None`` when any part is unknown (an empty sum is zero)."""
    total = _ZERO
    for value in values:
        if value is None:
            return None
        total += value
    return total


def _inr_rate(rates: Mapping[tuple[str, date], FxRate | None], currency: str, on: date) -> Decimal | None:
    if currency == vocab.REPORTING_CURRENCY:
        return _ONE
    rate = rates.get((currency, on))
    if rate is None or rate.rate_date > on:
        return None
    return Decimal(rate.rate_to_inr)


def _factor(
    rates: Mapping[tuple[str, date], FxRate | None], source: str, target: str, on: date
) -> tuple[Decimal | None, tuple[FxLookup, ...]]:
    """The multiplier from ``source`` to ``target`` through INR on ``on``, and the FX rows it read."""
    if source == target:
        return _ONE, ()
    used = tuple(
        (currency, on, rates.get((currency, on)))
        for currency in (source, target)
        if currency != vocab.REPORTING_CURRENCY
    )
    a, b = _inr_rate(rates, source, on), _inr_rate(rates, target, on)
    if a is None or b is None:
        return None, used
    with localcontext(_CTX):
        return a / b, used


def _variance(figure: Decimal | None, invoice: Decimal) -> tuple[Decimal | None, Decimal | None]:
    """``(figure - invoice, that as a percentage of the invoice)``; the percentage is ``None`` when undefined."""
    if figure is None:
        return None, None
    variance = figure - invoice
    if invoice > 0:
        with localcontext(_CTX):
            return variance, variance / invoice * 100
    if figure == 0 and invoice == 0:
        return variance, _ZERO
    return variance, None


def status_for(
    stored: Decimal | None, repriced: Decimal | None, invoice: Decimal, tolerance: Decimal = TOLERANCE_PCT
) -> tuple[str, Decimal | None, Decimal | None]:
    """``(status, stored pct, re-priced pct)``: within tolerance only when both figures are within it, exactly."""
    _sv, stored_pct = _variance(stored, invoice)
    _rv, repriced_pct = _variance(repriced, invoice)
    within = (
        stored_pct is not None
        and repriced_pct is not None
        and abs(stored_pct) <= tolerance
        and abs(repriced_pct) <= tolerance
    )
    return ("within_tolerance" if within else "needs_review"), stored_pct, repriced_pct


def pct_shown(pct: Decimal | None) -> Decimal | None:
    """A percentage for storage and display: six decimals away from zero (never better than the exact value)."""
    if pct is None:
        return None
    if abs(pct) > PCT_LIMIT:
        return PCT_LIMIT if pct > 0 else -PCT_LIMIT
    with localcontext(_CTX):
        return pct.quantize(PCT_QUANT, rounding=ROUND_UP)


def run_status(provider_status: str, needs_review: int, accepted: int) -> str:
    """A run's status from its provider-level status and the counts of its items."""
    if needs_review:
        return "needs_review"
    if accepted:
        return "accepted"  # every difference was accepted
    return "within_tolerance" if provider_status == "within_tolerance" else "needs_review"


# ---------------------------------------------------------------- the two figures (pure)


def _stored_figure(
    group: MeteredGroup, rates: Mapping[tuple[str, date], FxRate | None], invoice_currency: str, month_end: date
) -> tuple[Decimal | None, tuple[FxLookup, ...]]:
    if group.unpriced_quantity > 0 or group.unconverted_amount > 0:
        return None, ()  # incomplete: some records have no amount or no INR amount
    if group.currency is None or group.currency == invoice_currency:
        return group.amount, ()
    if invoice_currency == vocab.REPORTING_CURRENCY:
        return group.amount_inr, ()
    used = ((invoice_currency, month_end, rates.get((invoice_currency, month_end))),)
    rate = _inr_rate(rates, invoice_currency, month_end)
    if rate is None:
        return None, used
    with localcontext(_CTX):
        return _q(group.amount_inr / rate), used


def _overage(
    group: MeteredGroup,
    commitment: Any,
    canonical_unit: str,
    base: tuple[Decimal, str] | None,
    rates: Mapping[tuple[str, date], FxRate | None],
    invoice_currency: str,
    month_end: date,
) -> tuple[dict[str, Any], tuple[FxLookup, ...]]:
    """Overage at the commitment's price less the base price already in the figures, in the invoice currency."""
    unit = commitment.unit or canonical_unit
    divisor = Decimal(vocab.CANONICAL_DIVISOR.get(unit, 1))
    amount: Decimal | None = None
    used: tuple[FxLookup, ...] = ()
    if base is not None and group.quantity > 0:
        base_amount, base_currency = base
        to_base, used_a = _factor(rates, str(commitment.overage_currency).strip(), base_currency, month_end)
        to_invoice, used_b = _factor(rates, base_currency, invoice_currency, month_end)
        used = used_a + used_b
        if to_base is not None and to_invoice is not None:
            with localcontext(_CTX):
                base_price = base_amount / (group.quantity / divisor)
                overage_price = Decimal(commitment.overage_unit_price) * to_base
                native = (group.overage_quantity / divisor) * (overage_price - base_price)
                amount = _q(native * to_invoice)
    return {"kind": "commitment_overage", "commitment_id": str(commitment.id), "amount": amount}, used


def _price_group(
    group: MeteredGroup,
    *,
    provider: str,
    cards: Sequence[Card],
    stored_cards: Mapping[uuid.UUID, Card],
    commitments: Mapping[uuid.UUID, Any],
    rates: Mapping[tuple[str, date], FxRate | None],
    aliases: Mapping[tuple[str, str], str],
    invoice_currency: str,
    month_end: date,
) -> PricedGroup:
    model = pricing.canonical_model(provider, group.model, aliases)
    canonical_unit = vocab.CANONICAL_UNIT[group.unit]
    with localcontext(_CTX):
        quantity_canonical = group.quantity / Decimal(vocab.CANONICAL_DIVISOR[canonical_unit])
    stored, fx_used = _stored_figure(group, rates, invoice_currency, month_end)
    usage = Usage(
        provider=provider,
        usage_type=group.usage_type,
        unit=group.unit,
        quantity=group.quantity,
        model=model,
        on=group.billing_date,
        fx_on=month_end,
    )
    selection = pricing.select_price(usage, cards)
    native: Decimal | None = None
    currency: str | None = None
    repriced_card: Card | None = None
    blend_card: Card | None = None
    card_quantity: Decimal | None = None
    tierable = False
    keeps_stored = False
    if group.price_source == "in_house":
        native, currency = _ZERO, vocab.REPORTING_CURRENCY
    elif group.price_source in FALLBACK_SOURCES and selection is None:
        # The event-dated fallback price is the one stored: the fallback tables carry no dates.
        native, currency, keeps_stored = group.amount, group.currency, True
    else:
        # Records carry no batch flag in Phase 1 (nothing meters batch usage), so groups re-price as non-batch.
        priced = pricing.price_with(usage, cards, lambda ccy: rates.get((ccy, month_end)))
        if not priced.unpriced:
            native, currency = priced.amount, priced.currency
            if selection is not None:
                repriced_card, blend_card = selection.card, selection.blend_card
                price_field = vocab.PRICE_PATHS[group.unit][selection.path_index][1]
                if (
                    repriced_card is not None
                    and blend_card is None
                    and price_field == "unit_price"
                    and repriced_card.volume_tiers
                ):
                    tierable = True
                    with localcontext(_CTX):
                        card_quantity = group.quantity / Decimal(selection.divisor)
    repriced: Decimal | None = None
    factor: Decimal | None = None
    if native is not None and currency is not None:
        factor, used = _factor(rates, currency, invoice_currency, month_end)
        if keeps_stored and stored is not None:
            repriced = stored
        else:
            fx_used += used
            repriced = None if factor is None else _q(native * factor)
    adjustments: list[dict[str, Any]] = []
    commitment = commitments.get(group.commitment_id) if group.commitment_id else None
    if (
        commitment is not None
        and commitment.kind == "quantity"
        and commitment.overage_unit_price is not None
        and group.overage_quantity > 0
    ):
        base: tuple[Decimal, str] | None = None
        if native is not None and currency is not None:
            base = (native, currency)
        elif not group.unpriced_quantity and group.currency:
            base = (group.amount, group.currency)
        adjustment, used = _overage(group, commitment, canonical_unit, base, rates, invoice_currency, month_end)
        adjustments.append(adjustment)
        fx_used += used
    converted = (group.currency is not None and group.currency != invoice_currency) or (
        currency is not None and currency != invoice_currency
    )
    return PricedGroup(
        group=group,
        canonical_unit=canonical_unit,
        quantity_canonical=quantity_canonical,
        stored_amount=stored,
        repriced_amount=repriced,
        repriced_card=repriced_card,
        adjustments=tuple(adjustments),
        fx_converted=converted,
        model=model,
        repriced_currency=currency,
        factor=factor,
        card_quantity=card_quantity,
        tierable=tierable,
        blend_card=blend_card,
        stored_card=stored_cards.get(group.rate_card_id) if group.rate_card_id else None,
        fx=fx_used,
    )


def reprice(
    groups: Sequence[MeteredGroup],
    cards: Sequence[Card],
    stored_cards: Mapping[uuid.UUID, Card],
    commitments: Sequence[Any],
    rates: Mapping[tuple[str, date], FxRate | None],
    aliases: Mapping[tuple[str, str], str],
    *,
    provider: str,
    invoice_currency: str,
    b0: date,
    b1: date,
) -> list[PricedGroup]:
    """Both figures of every group, with the commitment overage (pure; tiers come after, ``tier_true_ups``)."""
    month_end = b1 - timedelta(days=1)
    by_id = {commitment.id: commitment for commitment in commitments}
    return [
        _price_group(
            group,
            provider=provider,
            cards=cards,
            stored_cards=stored_cards,
            commitments=by_id,
            rates=rates,
            aliases=aliases,
            invoice_currency=invoice_currency,
            month_end=month_end,
        )
        for group in groups
    ]


def _group_order(priced: PricedGroup) -> tuple[Any, ...]:
    g = priced.group
    return (
        g.billing_date,
        g.usage_type,
        priced.model,
        g.unit,
        str(g.rate_card_id),
        str(g.commitment_id),
        g.price_source,
    )


def contract_key(card: Card) -> tuple[str, str, str, str, str]:
    return (card.provider, card.usage_type, card.model_sku, card.unit, card.source)


def tier_true_ups(priced: Sequence[PricedGroup]) -> list[PricedGroup]:
    """Each contract key's volume ladder over the month, added to both figures of its groups (pure).

    Billing days are walked in order with the key's cumulative quantity in
    card units across card versions, so a card version change mid-month
    continues one ladder. Each day prices its quantity with that day's card:
    ``graduated`` from the cumulative position, ``all_units`` at the tier the
    month's total reaches. The adjustment is that price less the quantity at
    the card's base unit price, which the figures already hold. Records carry
    no batch flag in Phase 1 and nothing meters batch usage yet, so the base
    is the card's undiscounted ``unit_price``; a later phase that meters batch
    calls must discount the ladder the same way ``pricing.price_with`` does.
    """
    out = list(priced)
    keys: dict[tuple[str, str, str, str, str], list[int]] = {}
    for index, item in enumerate(out):
        if item.tierable and item.repriced_card is not None and item.card_quantity is not None:
            keys.setdefault(contract_key(item.repriced_card), []).append(index)
    for key, members in keys.items():
        members.sort(key=lambda i: _group_order(out[i]))
        month_total = sum((out[i].card_quantity or _ZERO for i in members), _ZERO)
        cumulative = _ZERO
        for index in members:
            item = out[index]
            card = item.repriced_card
            quantity = item.card_quantity
            if card is None or quantity is None:
                continue
            start = month_total - quantity if card.tier_mode == "all_units" else cumulative
            cumulative += quantity
            with localcontext(_CTX):
                tiered = pricing.tiered_amount(card.volume_tiers, card.tier_mode, start, quantity)
                delta = tiered - _q(quantity * card.unit_price)
            if delta == 0:
                continue
            amount = None if item.factor is None else _q(delta * item.factor)
            adjustment = {"kind": "tier_true_up", "contract_key": "|".join(key), "amount": amount}
            out[index] = replace(item, adjustments=(*item.adjustments, adjustment))
    return out


# ---------------------------------------------------------------- matching (pure)


def _line_key(
    line: Any, provider: str, aliases: Mapping[tuple[str, str], str], days_in_month: int
) -> tuple[tuple[str, str, str | None, date | None], Decimal | None]:
    model = pricing.canonical_model(provider, line.model_sku, aliases) if line.model_sku else ""
    unit = line.unit
    quantity = Decimal(line.quantity) if line.quantity is not None else None
    if unit == "gb_month":
        unit = "gb_day"
        quantity = quantity * days_in_month if quantity is not None else None
    return (line.usage_type, model, unit, line.usage_date), quantity


def _fits(item: PricedGroup, usage_type: str, model: str, unit: str | None, day: date | None) -> bool:
    return (
        item.group.usage_type == usage_type
        and (not model or item.model == model)
        and (unit is None or item.canonical_unit == unit)
        and (day is None or item.group.billing_date == day)
    )


def _card_json(card: Card, role: str) -> dict[str, Any]:
    return {
        "id": str(card.id),
        "role": role,
        "unit_price": vocab.dec_str(card.unit_price),
        "currency": card.currency,
        "source": card.source,
        "status": card.status,
        "created_at": card.created_at.isoformat() if card.created_at else None,
        "updated_at": card.updated_at.isoformat() if card.updated_at else None,
    }


def _cards_of(members: Sequence[PricedGroup]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for item in members:
        for card, role in (
            (item.stored_card, "stored"),
            (item.repriced_card, "repriced"),
            (item.blend_card, "repriced"),
        ):
            if card is not None:
                seen.setdefault((str(card.id), role), _card_json(card, role))
    return [seen[key] for key in sorted(seen)]


def fx_json(lookups: Sequence[FxLookup]) -> list[dict[str, Any]]:
    """The FX rows a set of lookups read (``rate_date`` ``None`` where none existed), one per lookup."""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for currency, on, rate in lookups:
        seen.setdefault(
            (currency, on.isoformat()),
            {
                "currency": currency,
                "on": on.isoformat(),
                "rate_date": rate.rate_date.isoformat() if rate is not None else None,
                "rate_to_inr": vocab.dec_str(Decimal(rate.rate_to_inr)) if rate is not None else None,
                "updated_at": rate.updated_at.isoformat() if rate is not None and rate.updated_at else None,
            },
        )
    return [seen[key] for key in sorted(seen)]


def _days(members: Sequence[PricedGroup]) -> list[dict[str, Any]]:
    by_day: dict[date, list[PricedGroup]] = {}
    for item in members:
        by_day.setdefault(item.group.billing_date, []).append(item)
    return [
        {
            "date": day.isoformat(),
            "metered_quantity": _qty(sum((m.quantity_canonical for m in by_day[day]), _ZERO)),
            "stored_amount": _q_or_none(_sum([m.total_stored for m in by_day[day]])),
            "repriced_amount": _q_or_none(_sum([m.total_repriced for m in by_day[day]])),
        }
        for day in sorted(by_day)
    ]


def _usage_item(
    key: tuple[str, str, str | None, date | None], entry: dict[str, Any] | None, members: Sequence[PricedGroup]
) -> dict[str, Any]:
    usage_type, model, unit, day = key
    stored = _sum([m.total_stored for m in members])
    repriced = _sum([m.total_repriced for m in members])
    invoice_amount = entry["amount"] if entry else _ZERO
    quantities = entry["quantities"] if entry else []
    invoice_quantity = (
        _qty(sum(quantities, _ZERO)) if quantities and all(q is not None for q in quantities) and unit else None
    )
    status, stored_pct, repriced_pct = status_for(stored, repriced, invoice_amount)
    unpriced = _ZERO
    unconverted = _ZERO
    for member in members:
        with localcontext(_CTX):
            unpriced += member.group.unpriced_quantity / Decimal(vocab.CANONICAL_DIVISOR[member.canonical_unit])
        unconverted += member.group.unconverted_amount
    return {
        "item_kind": "usage",
        "line_kind": "usage",
        "usage_type": usage_type,
        "model_sku": model,
        "unit": unit,
        "usage_date": day,
        "invoice_quantity": invoice_quantity,
        "metered_quantity": _qty(sum((m.quantity_canonical for m in members), _ZERO)),
        "invoice_amount": invoice_amount,
        "stored_amount": stored,
        "repriced_amount": repriced,
        "adjustments": [adjustment for member in members for adjustment in member.adjustments],
        "stored_variance_amount": None if stored is None else stored - invoice_amount,
        "stored_variance_pct": stored_pct,
        "repriced_variance_amount": None if repriced is None else repriced - invoice_amount,
        "repriced_variance_pct": repriced_pct,
        "status": status,
        "fx_converted": any(m.fx_converted for m in members),
        "unpriced_quantity": _qty(unpriced),
        "unconverted_amount": unconverted,
        "cards": _cards_of(members),
        "fx": fx_json([lookup for m in members for lookup in m.fx]),
        "invoice_line_ids": sorted(str(line.id) for line in entry["lines"]) if entry else [],
        "days": _days(members) if day is None and members else [],
        "members": list(members),
    }


def _non_usage_item(line: Any) -> dict[str, Any]:
    return {
        "item_kind": "non_usage_line",
        "line_kind": line.line_kind,
        "usage_type": line.usage_type,
        "model_sku": line.model_sku or "",
        "unit": line.unit,
        "usage_date": line.usage_date,
        "invoice_quantity": Decimal(line.quantity) if line.quantity is not None else None,
        "metered_quantity": _qty(_ZERO),
        "invoice_amount": Decimal(line.amount),
        "stored_amount": None,
        "repriced_amount": None,
        "adjustments": [],
        "stored_variance_amount": None,
        "stored_variance_pct": None,
        "repriced_variance_amount": None,
        "repriced_variance_pct": None,
        "status": "informational",
        "fx_converted": False,
        "unpriced_quantity": _qty(_ZERO),
        "unconverted_amount": _q(_ZERO),
        "cards": [],
        "fx": [],
        "invoice_line_ids": [str(line.id)],
        "days": [],
        "members": [],
    }


def _line_order(line: Any) -> tuple[str, int]:
    return (str(line.invoice_id), int(line.line_no))


def match(
    lines: Sequence[Any],
    priced: Sequence[PricedGroup],
    aliases: Mapping[tuple[str, str], str],
    *,
    provider: str,
    days_in_month: int,
) -> list[dict[str, Any]]:
    """Items from invoice lines and priced groups (pure).

    Lines with the same key (two accounts, a repeated line) are one item.
    Pass 1: lines naming a model and a unit take the groups of their key (and
    day). Pass 2: lines leaving the model or the unit open take the remaining
    groups that fit what they name (a model before a unit before neither, a
    day before a month). Pass 3: groups left over become items with no
    invoice amount; lines that took nothing are items with nothing metered.
    Non-usage lines are informational items.
    """
    merged: dict[tuple[str, str, str | None, date | None], dict[str, Any]] = {}
    for line in sorted((ln for ln in lines if ln.line_kind == "usage"), key=_line_order):
        key, quantity = _line_key(line, provider, aliases, days_in_month)
        entry = merged.setdefault(key, {"lines": [], "amount": _ZERO, "quantities": []})
        entry["lines"].append(line)
        entry["amount"] += Decimal(line.amount)
        entry["quantities"].append(quantity)
    remaining = list(range(len(priced)))

    def first_line(key: tuple[str, str, str | None, date | None]) -> tuple[str, int]:
        return _line_order(merged[key]["lines"][0])

    exact = sorted((k for k in merged if k[1] and k[2] is not None), key=lambda k: (k[3] is None, first_line(k)))
    loose = sorted(
        (k for k in merged if not (k[1] and k[2] is not None)),
        key=lambda k: (not k[1], k[2] is None, k[3] is None, first_line(k)),
    )
    items: list[dict[str, Any]] = []
    for key in (*exact, *loose):
        usage_type, model, unit, day = key
        chosen = [i for i in remaining if _fits(priced[i], usage_type, model, unit, day)]
        remaining = [i for i in remaining if i not in chosen]
        items.append(_usage_item(key, merged[key], [priced[i] for i in chosen]))
    leftovers: dict[tuple[str, str, str], list[int]] = {}
    for index in remaining:
        item = priced[index]
        leftovers.setdefault((item.group.usage_type, item.model, item.canonical_unit), []).append(index)
    for (usage_type, model, unit), indexes in sorted(leftovers.items()):
        items.append(_usage_item((usage_type, model, unit, None), None, [priced[i] for i in indexes]))
    for line in sorted((ln for ln in lines if ln.line_kind != "usage"), key=_line_order):
        items.append(_non_usage_item(line))
    return items


def carry_over(items: Sequence[dict[str, Any]], previous: Sequence[Any]) -> list[tuple[dict[str, Any], Any]]:
    """Accepted items of the previous run handed to unchanged items of this one (pure); the pairs carried.

    An item is unchanged when its usage type, model, unit and day, its invoice
    lines and both its figures are the same.
    """

    def key_of(usage_type: Any, model: Any, unit: Any, day: Any, line_ids: Any, stored: Any, repriced: Any) -> Any:
        return (
            usage_type,
            model or "",
            unit,
            day,
            tuple(sorted(str(i) for i in line_ids or [])),
            None if stored is None else Decimal(stored),
            None if repriced is None else Decimal(repriced),
        )

    accepted: dict[Any, Any] = {}
    for old in previous:
        if old.item_kind == "usage" and old.status == "accepted":
            accepted.setdefault(
                key_of(
                    old.usage_type,
                    old.model_sku,
                    old.unit,
                    old.usage_date,
                    old.invoice_line_ids,
                    old.stored_amount,
                    old.repriced_amount,
                ),
                old,
            )
    carried: list[tuple[dict[str, Any], Any]] = []
    for item in items:
        if item["item_kind"] != "usage" or item["status"] != "needs_review":
            continue
        key = key_of(
            item["usage_type"],
            item["model_sku"],
            item["unit"],
            item["usage_date"],
            item["invoice_line_ids"],
            None if item["stored_amount"] is None else _q(item["stored_amount"]),
            None if item["repriced_amount"] is None else _q(item["repriced_amount"]),
        )
        old = accepted.pop(key, None)
        if old is not None:
            item.update(
                status="accepted",
                accepted_by=old.accepted_by,
                accepted_at=old.accepted_at,
                accept_reason=old.accept_reason,
                carried_from=old.id,
            )
            carried.append((item, old))
    return carried


def _late(stamps: Sequence[datetime | None], period_end: datetime, imported_at: datetime | None) -> list[str]:
    reasons: list[str] = []
    if any(stamp is not None and stamp > period_end for stamp in stamps):
        reasons.append("after_period_end")
    if imported_at is not None and any(stamp is not None and stamp > imported_at for stamp in stamps):
        reasons.append("after_invoice_import")
    return reasons


def retroactive_refs(
    cards: Sequence[Card], lookups: Sequence[FxLookup], *, period_end: datetime, imported_at: datetime | None
) -> list[dict[str, Any]]:
    """Cards and FX rows the run used that were entered or changed after the month or the newest invoice import."""
    out: list[dict[str, Any]] = []
    seen_cards: set[uuid.UUID] = set()
    for card in sorted(cards, key=lambda c: str(c.id)):
        if card.id in seen_cards:
            continue
        seen_cards.add(card.id)
        reasons = _late((card.created_at, card.updated_at), period_end, imported_at)
        if reasons:
            out.append(
                {
                    "kind": "card",
                    "id": str(card.id),
                    "reasons": reasons,
                    "created_at": card.created_at.isoformat() if card.created_at else None,
                    "updated_at": card.updated_at.isoformat() if card.updated_at else None,
                }
            )
    seen_rates: set[tuple[str, date]] = set()
    for _currency, _on, rate in lookups:
        if rate is None or (rate.currency, rate.rate_date) in seen_rates:
            continue
        seen_rates.add((rate.currency, rate.rate_date))
        reasons = _late((rate.updated_at,), period_end, imported_at)
        if reasons:
            out.append(
                {
                    "kind": "fx",
                    "currency": rate.currency,
                    "rate_date": rate.rate_date.isoformat(),
                    "reasons": reasons,
                    "updated_at": rate.updated_at.isoformat() if rate.updated_at else None,
                }
            )
    return out


# ---------------------------------------------------------------- loaders


async def load_groups(
    session: Any, tenant_id: uuid.UUID, provider: str, b0: date, b1: date
) -> tuple[list[MeteredGroup], Decimal]:
    """The provider's tenant-billed rollup groups of the billing month, and its platform-billed INR amount."""
    from core.models.spend_usage import SpendUsageRollup as U

    dims = (U.billing_date, U.usage_type, U.model, U.unit, U.currency, U.rate_card_id, U.price_source, U.commitment_id)
    scope = (U.tenant_id == tenant_id, U.provider == provider, U.billing_date >= b0, U.billing_date < b1)
    statement = (
        select(
            *dims,
            func.sum(U.quantity),
            func.sum(U.amount),
            func.sum(U.amount_inr),
            func.sum(U.unconverted_amount),
            func.sum(U.unpriced_quantity),
            func.sum(U.overage_quantity),
            func.sum(U.record_count),
            func.sum(case((U.billing_account.is_(None), U.record_count), else_=0)),
        )
        .where(*scope, or_(U.billing_account.is_(None), U.billing_account == TENANT_BILLED))
        .group_by(*dims)
    )
    rows = (await session.execute(statement)).all()
    groups = [
        MeteredGroup(
            billing_date=row[0],
            usage_type=row[1],
            model=row[2] or "",
            unit=row[3],
            currency=str(row[4]).strip() if row[4] else None,
            rate_card_id=row[5],
            price_source=row[6],
            commitment_id=row[7],
            quantity=_dec(row[8]),
            amount=_dec(row[9]),
            amount_inr=_dec(row[10]),
            unconverted_amount=_dec(row[11]),
            unpriced_quantity=_dec(row[12]),
            overage_quantity=_dec(row[13]),
            records=int(row[14] or 0),
            unknown_account_records=int(row[15] or 0),
        )
        for row in rows
    ]
    groups.sort(
        key=lambda g: (
            g.billing_date,
            g.usage_type,
            g.model,
            g.unit,
            g.currency or "",
            str(g.rate_card_id),
            g.price_source,
            str(g.commitment_id),
        )
    )
    platform = (
        await session.execute(select(func.sum(U.amount_inr)).where(*scope, U.billing_account == PLATFORM_BILLED))
    ).scalar()
    return groups, _dec(platform)


async def _cards_by_id(session: Any, tenant_id: uuid.UUID, ids: set[uuid.UUID]) -> dict[uuid.UUID, Card]:
    from core.models.spend import SpendRateCard

    if not ids:
        return {}
    rows = (
        (
            await session.execute(
                select(SpendRateCard).where(SpendRateCard.tenant_id == tenant_id, SpendRateCard.id.in_(sorted(ids)))
            )
        )
        .scalars()
        .all()
    )
    return {row.id: pricing.card_from_row(row) for row in rows}


async def _commitments(session: Any, tenant_id: uuid.UUID, ids: set[uuid.UUID]) -> list[Any]:
    from core.models.spend import SpendCommitment

    if not ids:
        return []
    statement = select(SpendCommitment).where(
        SpendCommitment.tenant_id == tenant_id, SpendCommitment.id.in_(sorted(ids))
    )
    return list((await session.execute(statement)).scalars().all())


async def _rates(
    session: Any, tenant_id: uuid.UUID, currencies: set[str], on: date
) -> dict[tuple[str, date], FxRate | None]:
    from core.spend import fx

    out: dict[tuple[str, date], FxRate | None] = {}
    for currency in sorted(currencies - {vocab.REPORTING_CURRENCY}):
        out[(currency, on)] = await fx.rate_on(session, tenant_id, currency, on)
    return out


async def _runs(session: Any, tenant_id: uuid.UUID, provider: str, period_start: date) -> list[Any]:
    """The month's runs not yet superseded, newest first."""
    from core.models.spend_invoice import SpendReconciliation as R

    statement = (
        select(R)
        .where(
            R.tenant_id == tenant_id, R.provider == provider, R.period_start == period_start, R.superseded.is_(False)
        )
        .order_by(R.created_at.desc())
        .with_for_update()
    )
    return list((await session.execute(statement)).scalars().all())


async def _items(session: Any, tenant_id: uuid.UUID, reconciliation_id: uuid.UUID) -> list[Any]:
    from core.models.spend_invoice import SpendReconciliationItem as I

    statement = select(I).where(I.tenant_id == tenant_id, I.reconciliation_id == reconciliation_id)
    rows = list((await session.execute(statement)).scalars().all())
    return sorted(rows, key=_item_order)


def _item_order(row: Any) -> tuple[Any, ...]:
    return (
        row.item_kind != "usage",
        row.usage_type or "",
        row.model_sku or "",
        row.unit or "",
        row.usage_date.isoformat() if row.usage_date else "",
        row.line_kind or "",
        str(row.id),
    )


# ---------------------------------------------------------------- JSON


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def item_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "reconciliation_id": str(row.reconciliation_id),
        "item_kind": row.item_kind,
        "line_kind": row.line_kind,
        "usage_type": row.usage_type,
        "model_sku": row.model_sku or "",
        "unit": row.unit,
        "usage_date": _iso(row.usage_date),
        "invoice_quantity": vocab.dec_str(row.invoice_quantity),
        "metered_quantity": vocab.dec_str(row.metered_quantity),
        "invoice_amount": vocab.dec_str(row.invoice_amount),
        "stored_amount": vocab.dec_str(row.stored_amount),
        "repriced_amount": vocab.dec_str(row.repriced_amount),
        "adjustments": list(row.adjustments or []),
        "stored_variance_amount": vocab.dec_str(row.stored_variance_amount),
        "stored_variance_pct": vocab.dec_str(row.stored_variance_pct),
        "repriced_variance_amount": vocab.dec_str(row.repriced_variance_amount),
        "repriced_variance_pct": vocab.dec_str(row.repriced_variance_pct),
        "status": row.status,
        "fx_converted": bool(row.fx_converted),
        "unpriced_quantity": vocab.dec_str(row.unpriced_quantity),
        "unconverted_amount": vocab.dec_str(row.unconverted_amount),
        "cards": list(row.cards or []),
        "fx": list(row.fx or []),
        "invoice_line_ids": list(row.invoice_line_ids or []),
        "days": list(row.days or []),
        "accepted_by": row.accepted_by,
        "accepted_at": _iso(row.accepted_at),
        "accept_reason": row.accept_reason,
        "carried_from": str(row.carried_from) if row.carried_from else None,
    }


def run_dict(row: Any, *, items: Sequence[Any] | None = None, stale: bool | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(row.id),
        "provider": row.provider,
        "period": invoices.period_text(row.period_start),
        "period_start": row.period_start.isoformat(),
        "billing_timezone": row.billing_timezone,
        "currency": str(row.currency).strip(),
        "invoice_amount": vocab.dec_str(row.invoice_amount),
        "non_usage_amount": vocab.dec_str(row.non_usage_amount),
        "stored_amount": vocab.dec_str(row.stored_amount),
        "repriced_amount": vocab.dec_str(row.repriced_amount),
        "adjustments_amount": vocab.dec_str(row.adjustments_amount),
        "stored_variance_amount": vocab.dec_str(row.stored_variance_amount),
        "stored_variance_pct": vocab.dec_str(row.stored_variance_pct),
        "repriced_variance_amount": vocab.dec_str(row.repriced_variance_amount),
        "repriced_variance_pct": vocab.dec_str(row.repriced_variance_pct),
        "tolerance_pct": vocab.dec_str(row.tolerance_pct),
        "status": row.status,
        "item_count": int(row.item_count or 0),
        "needs_review_count": int(row.needs_review_count or 0),
        "unpriced_quantity_items": int(row.unpriced_quantity_items or 0),
        "unknown_account_records": int(row.unknown_account_records or 0),
        "platform_billed_amount_inr": vocab.dec_str(row.platform_billed_amount_inr),
        "invoice_ids": list(row.invoice_ids or []),
        "card_ids": [str(card_id) for card_id in row.card_ids or []],
        "fx_rows": list(row.fx_rows or []),
        "retroactive": list(row.retroactive or []),
        "superseded": bool(row.superseded),
        "accepted_by": row.accepted_by,
        "accepted_at": _iso(row.accepted_at),
        "accept_reason": row.accept_reason,
        "run_by": row.run_by,
        "created_at": _iso(getattr(row, "created_at", None)),
    }
    if stale is not None:
        out["stale"] = stale
    if items is not None:
        out["items"] = [item_dict(item) for item in items]
    return out


# ---------------------------------------------------------------- run


def _needed_currencies(
    invoice_currency: str, groups: Sequence[MeteredGroup], cards: Sequence[Card], commitments: Sequence[Any]
) -> set[str]:
    needed = {invoice_currency, "USD"}  # USD: the deployment fallback prices
    needed |= {g.currency for g in groups if g.currency}
    needed |= {card.currency for card in cards}
    needed |= {str(c.overage_currency).strip() for c in commitments if c.overage_currency}
    return needed


def _provider_level(items: Sequence[dict[str, Any]], invoice_amount: Decimal) -> dict[str, Any]:
    usage = [item for item in items if item["item_kind"] == "usage"]
    stored = _sum([item["stored_amount"] for item in usage])
    repriced = _sum([item["repriced_amount"] for item in usage])
    status, stored_pct, repriced_pct = status_for(stored, repriced, invoice_amount)
    adjustments = sum(
        (a["amount"] for item in usage for a in item["adjustments"] if a.get("amount") is not None), _ZERO
    )
    return {
        "stored": stored,
        "repriced": repriced,
        "status": status,
        "stored_pct": stored_pct,
        "repriced_pct": repriced_pct,
        "adjustments": adjustments,
    }


def _item_row(tenant_id: uuid.UUID, run_id: uuid.UUID, item: dict[str, Any], stamp: datetime) -> Any:
    from core.models.spend_invoice import SpendReconciliationItem

    def money(value: Decimal | None) -> Decimal | None:
        return None if value is None else _q(value)

    return SpendReconciliationItem(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        reconciliation_id=run_id,
        item_kind=item["item_kind"],
        line_kind=item["line_kind"],
        usage_type=item["usage_type"],
        model_sku=item["model_sku"] or "",
        unit=item["unit"],
        usage_date=item["usage_date"],
        invoice_quantity=item["invoice_quantity"],
        metered_quantity=item["metered_quantity"],
        invoice_amount=_q(item["invoice_amount"]),
        stored_amount=money(item["stored_amount"]),
        repriced_amount=money(item["repriced_amount"]),
        adjustments=audit.jsonable(item["adjustments"]),
        stored_variance_amount=money(item["stored_variance_amount"]),
        stored_variance_pct=pct_shown(item["stored_variance_pct"]),
        repriced_variance_amount=money(item["repriced_variance_amount"]),
        repriced_variance_pct=pct_shown(item["repriced_variance_pct"]),
        status=item["status"],
        fx_converted=bool(item["fx_converted"]),
        unpriced_quantity=item["unpriced_quantity"],
        unconverted_amount=_q(item["unconverted_amount"]),
        cards=item["cards"],
        fx=item["fx"],
        invoice_line_ids=item["invoice_line_ids"],
        days=audit.jsonable(item["days"]),
        accepted_by=item.get("accepted_by"),
        accepted_at=item.get("accepted_at"),
        accept_reason=item.get("accept_reason"),
        carried_from=item.get("carried_from"),
        created_at=stamp,
    )


async def run(tenant_id: uuid.UUID, *, provider: str, period: str, actor: str, now: datetime) -> dict[str, Any]:
    """Reconcile a provider's billing month against its current invoices (one transaction); the run with items."""
    from core.database import get_tenant_session
    from core.models.spend_invoice import SpendReconciliation

    who = require_actor(actor)
    provider = invoices.check_provider(provider)
    b0, b1 = invoices.parse_period(period)
    zone = clock.billing_zone(provider)
    month_end = b1 - timedelta(days=1)
    async with get_tenant_session(tenant_id) as session:
        await locks.xact_lock(session, locks.reconciliation(tenant_id, provider, b0))
        lines, current = await invoices.current_lines(session, tenant_id, provider=provider, period_start=b0)
        if not current:
            raise SpendError(409, "invoice_missing", f"no current {provider} invoice for {invoices.period_text(b0)}")
        currencies = sorted({str(inv.currency).strip() for inv in current})
        if len(currencies) != 1:
            raise SpendError(
                422, "currency_mismatch", f"the month's current invoices are in {', '.join(currencies)}; use one"
            )
        currency = currencies[0]
        groups, platform_inr = await load_groups(session, tenant_id, provider, b0, b1)
        aliases = await pricing.alias_map(session, tenant_id)
        cards = await pricing.load_cards(
            session, tenant_id, providers={provider}, usage_types=vocab.USAGE_TYPES, start=b0, end=month_end
        )
        stored_cards = await _cards_by_id(session, tenant_id, {g.rate_card_id for g in groups if g.rate_card_id})
        commitments = await _commitments(session, tenant_id, {g.commitment_id for g in groups if g.commitment_id})
        rates = await _rates(session, tenant_id, _needed_currencies(currency, groups, cards, commitments), month_end)
        priced = tier_true_ups(
            reprice(
                groups,
                cards,
                stored_cards,
                commitments,
                rates,
                aliases,
                provider=provider,
                invoice_currency=currency,
                b0=b0,
                b1=b1,
            )
        )
        items = match(lines, priced, aliases, provider=provider, days_in_month=clock.days_in_month(b0))
        earlier = await _runs(session, tenant_id, provider, b0)
        carried = carry_over(items, await _items(session, tenant_id, earlier[0].id) if earlier else [])
        for row in earlier:
            row.superseded = True
        await session.flush()

        usage_lines = [line for line in lines if line.line_kind == "usage"]
        invoice_amount = sum((Decimal(line.amount) for line in usage_lines), _ZERO)
        non_usage_amount = sum((Decimal(line.amount) for line in lines if line.line_kind != "usage"), _ZERO)
        level = _provider_level(items, invoice_amount)
        needs_review = sum(1 for item in items if item["status"] == "needs_review")
        accepted_items = [item for item in items if item["status"] == "accepted"]
        status = run_status(level["status"], needs_review, len(accepted_items))
        acceptance: dict[str, Any] = {"accepted_by": None, "accepted_at": None, "accept_reason": None}
        if status == "accepted":
            latest = max(accepted_items, key=lambda item: item["accepted_at"])
            acceptance = {key: latest[key] for key in acceptance}
        all_cards = [card for p in priced for card in (p.stored_card, p.repriced_card, p.blend_card) if card]
        lookups = [lookup for p in priced for lookup in p.fx]
        imported_at = max((inv.created_at for inv in current if inv.created_at is not None), default=None)
        reconciliation = SpendReconciliation(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            provider=provider,
            period_start=b0,
            billing_timezone=zone.key,
            currency=currency,
            invoice_amount=_q(invoice_amount),
            non_usage_amount=_q(non_usage_amount),
            stored_amount=None if level["stored"] is None else _q(level["stored"]),
            repriced_amount=None if level["repriced"] is None else _q(level["repriced"]),
            adjustments_amount=_q(level["adjustments"]),
            stored_variance_amount=None if level["stored"] is None else _q(level["stored"] - invoice_amount),
            stored_variance_pct=pct_shown(level["stored_pct"]),
            repriced_variance_amount=None if level["repriced"] is None else _q(level["repriced"] - invoice_amount),
            repriced_variance_pct=pct_shown(level["repriced_pct"]),
            tolerance_pct=TOLERANCE_PCT,
            status=status,
            item_count=len(items),
            needs_review_count=needs_review,
            unpriced_quantity_items=sum(1 for item in items if item["unpriced_quantity"] > 0),
            unknown_account_records=sum(g.unknown_account_records for g in groups),
            platform_billed_amount_inr=_q(platform_inr),
            invoice_ids=sorted(str(inv.id) for inv in current),
            card_ids=sorted({card.id for card in all_cards}, key=str),
            fx_rows=fx_json(lookups),
            retroactive=retroactive_refs(
                all_cards, lookups, period_end=clock.day_bounds(b1, zone)[0], imported_at=imported_at
            ),
            superseded=False,
            run_by=who,
            created_at=now,
            **acceptance,
        )
        session.add(reconciliation)
        await session.flush()
        rows = [_item_row(tenant_id, reconciliation.id, item, now) for item in items]
        for row in rows:
            session.add(row)
        await session.flush()
        session.add(
            audit.audit_entry(
                tenant_id,
                actor_id=who,
                action="reconciliations.run",
                resource_type="spend_reconciliation",
                resource_id=str(reconciliation.id),
                details={
                    "provider": provider,
                    "period": invoices.period_text(b0),
                    "currency": currency,
                    "invoice_amount": reconciliation.invoice_amount,
                    "non_usage_amount": reconciliation.non_usage_amount,
                    "stored_amount": reconciliation.stored_amount,
                    "repriced_amount": reconciliation.repriced_amount,
                    "stored_variance_pct": reconciliation.stored_variance_pct,
                    "repriced_variance_pct": reconciliation.repriced_variance_pct,
                    "status": status,
                    "item_count": len(items),
                    "needs_review_count": needs_review,
                    "carried_over": len(carried),
                    "invoice_ids": reconciliation.invoice_ids,
                    "superseded_ids": [str(row.id) for row in earlier],
                },
                now=now,
            )
        )
        if carried:
            by_item = {id(item): row for item, row in zip(items, rows, strict=True)}
            session.add(
                audit.audit_entry(
                    tenant_id,
                    actor_id=who,
                    action="reconciliations.carry_over",
                    resource_type="spend_reconciliation",
                    resource_id=str(reconciliation.id),
                    details={
                        "items": [
                            {"item_id": str(by_item[id(item)].id), "carried_from": str(old.id)} for item, old in carried
                        ]
                    },
                    now=now,
                )
            )
        out = run_dict(reconciliation, items=sorted(rows, key=_item_order), stale=False)
    logger.info("spend_reconciliation_run", status=status, items=len(items), needs_review=needs_review)
    return out


# ---------------------------------------------------------------- acceptance


def check_reason(reason: Any) -> str:
    """An acceptance reason: 10 to 500 characters of plain text."""
    text = str(reason if reason is not None else "").strip()
    if not REASON_MIN <= len(text) <= REASON_MAX:
        raise SpendError(422, "reason_required", f"a reason of {REASON_MIN} to {REASON_MAX} characters is required")
    return vocab.free_text(text, field="reason", max_len=REASON_MAX, required=True)


async def _locked_run(session: Any, tenant_id: uuid.UUID, reconciliation_id: uuid.UUID) -> Any:
    """The run, locked: the month's advisory lock first (the order ``run`` takes them), then the row."""
    from core.models.spend_invoice import SpendReconciliation as R

    found = (
        await session.execute(
            select(R.provider, R.period_start).where(R.tenant_id == tenant_id, R.id == reconciliation_id)
        )
    ).all()
    if not found:
        raise SpendError(404, "not_found", "no such reconciliation")
    await locks.xact_lock(session, locks.reconciliation(tenant_id, found[0][0], found[0][1]))
    rows = (
        (await session.execute(select(R).where(R.tenant_id == tenant_id, R.id == reconciliation_id).with_for_update()))
        .scalars()
        .all()
    )
    return rows[0]


async def _check_checker(session: Any, tenant_id: uuid.UUID, run_row: Any, who: str) -> None:
    """Maker-checker: whoever imported one of the run's invoices may not accept its differences."""
    from core.models.spend_invoice import SpendInvoice

    ids = [uuid.UUID(str(invoice_id)) for invoice_id in run_row.invoice_ids or []]
    importers: set[str] = set()
    if ids:
        rows = (
            await session.execute(
                select(SpendInvoice.imported_by).where(SpendInvoice.tenant_id == tenant_id, SpendInvoice.id.in_(ids))
            )
        ).all()
        importers = {str(row[0]) for row in rows}
    if who in importers:
        raise SpendError(409, "same_actor", "the administrator who imported the invoice cannot accept its differences")


def _audit_figures(row: Any) -> dict[str, Any]:
    return {
        "invoice_amount": row.invoice_amount,
        "stored_amount": row.stored_amount,
        "repriced_amount": row.repriced_amount,
        "stored_variance_pct": row.stored_variance_pct,
        "repriced_variance_pct": row.repriced_variance_pct,
    }


async def accept_item(
    tenant_id: uuid.UUID,
    reconciliation_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    reason: str,
    actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Accept one item that needs review, with a reason; the run is accepted once nothing needs review."""
    from core.database import get_tenant_session

    who = require_actor(actor)
    why = check_reason(reason)
    stamp = now or clock.now_utc()
    async with get_tenant_session(tenant_id) as session:
        run_row = await _locked_run(session, tenant_id, reconciliation_id)
        items = await _items(session, tenant_id, run_row.id)
        item = next((row for row in items if row.id == item_id), None)
        if item is None:
            raise SpendError(404, "not_found", "no such item in this reconciliation")
        if run_row.superseded or item.status != "needs_review":
            raise SpendError(409, "not_reviewable", "only an item that needs review in a current run can be accepted")
        await _check_checker(session, tenant_id, run_row, who)
        item.status = "accepted"
        item.accepted_by = who
        item.accepted_at = stamp
        item.accept_reason = why
        run_row.needs_review_count = sum(1 for row in items if row.status == "needs_review")
        if run_row.needs_review_count == 0 and run_row.status == "needs_review":
            run_row.status = "accepted"
            run_row.accepted_by = who
            run_row.accepted_at = stamp
            run_row.accept_reason = why
        await session.flush()
        session.add(
            audit.audit_entry(
                tenant_id,
                actor_id=who,
                action="reconciliations.accept_item",
                resource_type="spend_reconciliation_item",
                resource_id=str(item.id),
                details={
                    "reconciliation_id": str(run_row.id),
                    "provider": run_row.provider,
                    "period": invoices.period_text(run_row.period_start),
                    "reason": why,
                    "item": {"usage_type": item.usage_type, "model_sku": item.model_sku, "unit": item.unit}
                    | _audit_figures(item),
                    "run": _audit_figures(run_row) | {"status": run_row.status},
                },
                now=stamp,
            )
        )
        out = {
            **item_dict(item),
            "run": {
                "id": str(run_row.id),
                "status": run_row.status,
                "needs_review_count": run_row.needs_review_count,
            },
        }
    logger.info("spend_reconciliation_item_accepted", run_status=out["run"]["status"])
    return out


async def accept_run(
    tenant_id: uuid.UUID, reconciliation_id: uuid.UUID, *, reason: str, actor: str, now: datetime | None = None
) -> dict[str, Any]:
    """Accept a run whose provider-level variance is out of tolerance while no item needs review."""
    from core.database import get_tenant_session

    who = require_actor(actor)
    why = check_reason(reason)
    stamp = now or clock.now_utc()
    async with get_tenant_session(tenant_id) as session:
        run_row = await _locked_run(session, tenant_id, reconciliation_id)
        items = await _items(session, tenant_id, run_row.id)
        if run_row.superseded or run_row.status != "needs_review" or any(r.status == "needs_review" for r in items):
            raise SpendError(
                409,
                "not_reviewable",
                "a run is accepted whole only when it is current, out of tolerance and no item needs review",
            )
        await _check_checker(session, tenant_id, run_row, who)
        run_row.status = "accepted"
        run_row.accepted_by = who
        run_row.accepted_at = stamp
        run_row.accept_reason = why
        await session.flush()
        session.add(
            audit.audit_entry(
                tenant_id,
                actor_id=who,
                action="reconciliations.accept_run",
                resource_type="spend_reconciliation",
                resource_id=str(run_row.id),
                details={
                    "provider": run_row.provider,
                    "period": invoices.period_text(run_row.period_start),
                    "reason": why,
                }
                | _audit_figures(run_row),
                now=stamp,
            )
        )
        out = run_dict(run_row, items=items, stale=await is_stale(session, tenant_id, run_row))
    logger.info("spend_reconciliation_accepted")
    return out


# ---------------------------------------------------------------- reads and staleness


def _after(stamp: datetime | None, moment: datetime) -> bool:
    return stamp is not None and stamp > moment


async def is_stale(session: Any, tenant_id: uuid.UUID, run: Any) -> bool:
    """Whether a card, FX row, record or invoice the run depends on changed after it.

    Stale when a card it used, or any card of the provider in force in the
    month, was created or changed after the run; when an FX lookup it made
    would now find another row, rate or change; when the provider's rollup
    rows of the month changed after it (late records, restatement,
    settlement, re-attribution); or when the month's current invoices are no
    longer the ones it compared.
    """
    from core.models.spend import SpendRateCard as C
    from core.models.spend_invoice import SpendInvoice
    from core.models.spend_usage import SpendUsageRollup as U
    from core.spend import fx

    created = run.created_at
    if created is None:
        return True
    b0 = run.period_start
    b1 = clock.next_month(b0)
    in_month = and_(
        C.provider == run.provider, C.effective_from < b1, or_(C.effective_to.is_(None), C.effective_to > b0)
    )
    card_ids = [uuid.UUID(str(card_id)) for card_id in run.card_ids or []]
    scope = or_(C.id.in_(card_ids), in_month) if card_ids else in_month
    cards = (await session.execute(select(C.created_at, C.updated_at).where(C.tenant_id == tenant_id, scope))).all()
    if any(_after(stamp, created) for row in cards for stamp in row):
        return True
    for entry in run.fx_rows or []:
        rate = await fx.rate_on(session, tenant_id, entry["currency"], date.fromisoformat(entry["on"]))
        if rate is None:
            if entry.get("rate_date") is not None:
                return True
            continue
        if (
            entry.get("rate_date") != rate.rate_date.isoformat()
            or entry.get("rate_to_inr") is None
            or Decimal(entry["rate_to_inr"]) != Decimal(rate.rate_to_inr)
            or _after(rate.updated_at, created)
        ):
            return True
    latest = (
        await session.execute(
            select(func.max(U.updated_at)).where(
                U.tenant_id == tenant_id, U.provider == run.provider, U.billing_date >= b0, U.billing_date < b1
            )
        )
    ).scalar()
    if _after(latest, created):
        return True
    current = (
        await session.execute(
            select(SpendInvoice.id).where(
                SpendInvoice.tenant_id == tenant_id,
                SpendInvoice.provider == run.provider,
                SpendInvoice.period_start == b0,
                SpendInvoice.status == "current",
            )
        )
    ).all()
    return {str(row[0]) for row in current} != {str(i) for i in run.invoice_ids or []}


async def list_runs(
    tenant_id: uuid.UUID,
    *,
    period: str | None = None,
    provider: str | None = None,
    include_superseded: bool = False,
) -> dict[str, Any]:
    """Runs, newest first (at most 200), each with ``stale``."""
    from core.database import get_tenant_session
    from core.models.spend_invoice import SpendReconciliation as R

    conditions = [R.tenant_id == tenant_id]
    if period:
        conditions.append(R.period_start == invoices.parse_period(period)[0])
    if provider:
        conditions.append(R.provider == vocab.norm_provider(provider))
    if not include_superseded:
        conditions.append(R.superseded.is_(False))
    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(R).where(*conditions).order_by(R.period_start.desc(), R.provider, R.created_at.desc())
                )
            )
            .scalars()
            .all()
        )[:LIST_LIMIT]
        out = [run_dict(row, stale=await is_stale(session, tenant_id, row)) for row in rows]
    return {"items": out}


async def get_run(tenant_id: uuid.UUID, reconciliation_id: uuid.UUID) -> dict[str, Any]:
    """A run with its items and ``stale``."""
    from core.database import get_tenant_session
    from core.models.spend_invoice import SpendReconciliation as R

    async with get_tenant_session(tenant_id) as session:
        rows = (
            (await session.execute(select(R).where(R.tenant_id == tenant_id, R.id == reconciliation_id)))
            .scalars()
            .all()
        )
        if not rows:
            raise SpendError(404, "not_found", "no such reconciliation")
        items = await _items(session, tenant_id, rows[0].id)
        return run_dict(rows[0], items=items, stale=await is_stale(session, tenant_id, rows[0]))


async def latest_run(session: Any, tenant_id: uuid.UUID, provider: str, period_start: date) -> Any:
    """The month's newest run that is not superseded, or ``None``."""
    from core.models.spend_invoice import SpendReconciliation as R

    rows = (
        (
            await session.execute(
                select(R)
                .where(
                    R.tenant_id == tenant_id,
                    R.provider == provider,
                    R.period_start == period_start,
                    R.superseded.is_(False),
                )
                .order_by(R.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .all()
    )
    return rows[0] if rows else None


# ---------------------------------------------------------------- references kept by runs


async def card_used_until(session: Any, tenant_id: uuid.UUID, card_id: uuid.UUID) -> date | None:
    """The last day of the newest month a current run compared with the card (``rates.card_in_use``)."""
    from core.models.spend_invoice import SpendReconciliation as R

    rows = (
        await session.execute(
            select(R.period_start, R.card_ids).where(R.tenant_id == tenant_id, R.superseded.is_(False))
        )
    ).all()
    ends = [
        clock.next_month(period_start) - timedelta(days=1)
        for period_start, ids in rows
        if any(str(found) == str(card_id) for found in ids or [])
    ]
    return max(ends) if ends else None


async def fx_row_used(session: Any, tenant_id: uuid.UUID, currency: str, rate_date: date) -> bool:
    """Whether a current run used the FX row of ``(currency, rate_date)`` (``fx.fx_in_use``)."""
    from core.models.spend_invoice import SpendReconciliation as R

    rows = (await session.execute(select(R.fx_rows).where(R.tenant_id == tenant_id, R.superseded.is_(False)))).all()
    day = rate_date.isoformat()
    return any(
        entry.get("currency") == currency and entry.get("rate_date") == day
        for (rows_used,) in rows
        for entry in rows_used or []
    )
