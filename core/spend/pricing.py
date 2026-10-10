# SPDX-License-Identifier: Apache-2.0
"""The pricing engine: a usage priced at its date, in the card's currency and in INR.

**Effective dating.** A card prices a usage when ``effective_from <= on <
effective_to`` (``NULL`` = open), ``on`` being the provider's billing date of
the usage. Only active cards price; a retired card (replaced by a
correction) prices nothing.

**Precedence.** Candidates from every pricing path of the record unit
(``vocab.PRICE_PATHS``) are ranked together: a model-specific card beats a
provider default whatever its path; within one specificity a card that
prices cached tokens beats the "input price, no discount" path, then a
contract card beats a list card, then path order, then the latest
``effective_from``. Unsplit ``token`` usage may be priced by a blend of an
input and an output card (three to one), marked estimated.

**Fallback.** With no card, LLM tokens fall back to the deployment's list
prices and overrides (``core/governance/model_pricing.py``), computed in
``Decimal`` from the floats' text: the SKU's price, else (when an alias
replaced the called name) the called name's, so an alias never leaves
unpriced a call the list prices. In-house providers price at zero per
token, page or minute. Anything else is unpriced: no amount, never zero.

**Money.** ``amount = quantity / divisor * unit_price`` rounded half-even to
ten places; ``amount_inr = amount * fx_rate`` per record on the reporting
date: an exact-date rate, else the latest earlier rate (``fx_estimated``),
else no INR amount (``unconverted``). INR converts at one; zero converts
to zero without a lookup.

Everything above is pure (``price_with``); the loaders read active cards
once per batch, one FX rate per (currency, date), and the tenant's model
aliases (cached for 60 seconds per process, dropped locally on a change).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any

from core.spend import clock, vocab
from core.spend.errors import SpendError

INPUT_SHARE = Decimal("0.75")  # core.governance.model_pricing.INPUT_SHARE, in Decimal
OUTPUT_SHARE = Decimal("1") - INPUT_SHARE
ALIAS_TTL_SECONDS = 60.0
_ALIAS_CACHE_MAX = 10_000
_FALLBACK_SOURCES = {"override": "fallback_override", "list": "fallback_list", "local": "in_house"}

# enterprise-gate: process-local-ok reason=alias-cache-ttl-60s-per-tenant-key-bounded-dropped-locally-on-alias-writes
_ALIAS_CACHE: dict[str, tuple[float, dict[tuple[str, str], str]]] = {}


@dataclass(frozen=True)
class Tier:
    from_quantity: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class Card:
    id: uuid.UUID
    provider: str
    usage_type: str
    model_sku: str
    unit: str
    unit_price: Decimal
    currency: str
    cached_unit_price: Decimal | None
    batch_discount_pct: Decimal
    volume_tiers: tuple[Tier, ...]
    tier_mode: str
    effective_from: date
    effective_to: date | None
    source: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    status: str = "active"


@dataclass(frozen=True)
class FxRate:
    currency: str
    rate_date: date
    rate_to_inr: Decimal
    updated_at: datetime | None = None


@dataclass(frozen=True)
class Usage:
    provider: str
    usage_type: str
    unit: str  # a record unit
    quantity: Decimal
    model: str  # canonical (price/price_many apply the tenant's aliases)
    on: date  # billing date: selects cards
    fx_on: date  # event (reporting) date: selects the FX rate
    batch: bool = False
    # The model name as called, when an alias replaced it in ``model``; the list fallback
    # tries it after the SKU, so an alias never leaves unpriced a call the list prices.
    called_model: str = ""


@dataclass(frozen=True)
class Selection:
    card: Card | None
    blend_card: Card | None
    unit_price: Decimal
    divisor: Decimal | int
    source: str
    path_index: int
    card_unit: str
    estimated: bool
    currency: str = field(default="USD")


@dataclass(frozen=True)
class Priced:
    amount: Decimal | None
    currency: str | None
    unit_price: Decimal | None
    amount_inr: Decimal | None
    fx_rate: Decimal | None
    fx_rate_date: date | None
    rate_card_id: uuid.UUID | None
    blend_card_id: uuid.UUID | None
    card_unit: str | None
    price_source: str
    unpriced: bool
    fx_estimated: bool
    unconverted: bool
    price_estimated: bool

    @property
    def flags(self) -> list[str]:
        names = ("unpriced", "fx_estimated", "unconverted", "price_estimated")
        return [name for name in names if getattr(self, name)]


# ---------------------------------------------------------------- pure core


def in_force(card: Card, on: date) -> bool:
    """``effective_from <= on < effective_to`` (an open card has no end)."""
    return card.effective_from <= on and (card.effective_to is None or on < card.effective_to)


def rank_key(specific: bool, path_index: int, unit: str, source: str, effective_from: date) -> tuple[int, ...]:
    """Lowest wins: specificity, then cached-capable before "no discount", contract, path, latest start."""
    no_discount = path_index in vocab.NO_DISCOUNT_PATHS.get(unit, frozenset())
    return (
        0 if specific else 1,
        1 if no_discount else 0,
        0 if source == "contract" else 1,
        path_index,
        -effective_from.toordinal(),
    )


def _divisor(divisor: int | str, on: date) -> int:
    return clock.days_in_month(on) if divisor == "month" else int(divisor)


def _candidates_for(usage: Usage, cards: Sequence[Card], card_unit: str) -> list[tuple[bool, Card]]:
    found: list[tuple[bool, Card]] = []
    for card in cards:
        if card.status != "active" or card.unit != card_unit or not in_force(card, usage.on):
            continue
        if card.provider != usage.provider or card.usage_type != usage.usage_type:
            continue
        if card.model_sku and card.model_sku == usage.model:
            found.append((True, card))
        elif not card.model_sku:
            found.append((False, card))
    return found


def select_price(usage: Usage, cards: Sequence[Card]) -> Selection | None:
    """The best card candidate for ``usage`` across every pricing path, or ``None``."""
    paths = vocab.PRICE_PATHS.get(usage.unit)
    if not paths:
        return None
    ranked: list[tuple[tuple[Any, ...], Selection]] = []
    for index, (card_unit, price_field, divisor) in enumerate(paths):
        if card_unit == "blend":
            inputs = _candidates_for(usage, cards, "1m_input_tokens")
            outputs = _candidates_for(usage, cards, "1m_output_tokens")
            for in_specific, in_card in inputs:
                for out_specific, out_card in outputs:
                    if in_specific != out_specific or in_card.currency != out_card.currency:
                        continue
                    source = "contract" if in_card.source == out_card.source == "contract" else "list"
                    started = max(in_card.effective_from, out_card.effective_from)
                    price = in_card.unit_price * INPUT_SHARE + out_card.unit_price * OUTPUT_SHARE
                    key = (
                        *rank_key(in_specific, index, usage.unit, source, started),
                        str(in_card.id),
                        str(out_card.id),
                    )
                    ranked.append(
                        (
                            key,
                            Selection(
                                card=in_card,
                                blend_card=out_card,
                                unit_price=price,
                                divisor=_divisor(divisor, usage.on),
                                source=source,
                                path_index=index,
                                card_unit="blend",
                                estimated=True,
                                currency=in_card.currency,
                            ),
                        )
                    )
            continue
        for specific, card in _candidates_for(usage, cards, card_unit):
            price = card.cached_unit_price if price_field == "cached_unit_price" else card.unit_price
            if price is None:
                continue
            key = (*rank_key(specific, index, usage.unit, card.source, card.effective_from), str(card.id), "")
            ranked.append(
                (
                    key,
                    Selection(
                        card=card,
                        blend_card=None,
                        unit_price=price,
                        divisor=_divisor(divisor, usage.on),
                        source=card.source,
                        path_index=index,
                        card_unit=card_unit,
                        estimated=False,
                        currency=card.currency,
                    ),
                )
            )
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def _float_decimal(value: float) -> Decimal:
    return Decimal(str(value))


def fallback_price(usage: Usage) -> Selection | None:
    """LLM tokens with no card: the deployment's override or list price (``model_pricing.price_for``), in Decimal.

    The SKU is tried first; when an alias named an SKU the deployment has no
    price for, the model as called keeps the price it had before the alias.
    """
    if usage.usage_type != "llm_tokens":
        return None
    from core.governance.model_pricing import price_for

    price = price_for(usage.provider, usage.model)
    if price is None and usage.called_model and usage.called_model != usage.model:
        price = price_for(usage.provider, usage.called_model)
    if price is None:
        return None
    source = _FALLBACK_SOURCES.get(price.source)
    if source is None:
        return None
    if source == "in_house":
        return _in_house_selection(usage)
    in_rate = _float_decimal(price.input_per_million)
    out_rate = _float_decimal(price.output_per_million)
    if usage.unit in ("input_token", "cached_input_token"):
        unit_price, estimated = in_rate, False
    elif usage.unit == "output_token":
        unit_price, estimated = out_rate, False
    elif usage.unit == "token":
        unit_price, estimated = in_rate * INPUT_SHARE + out_rate * OUTPUT_SHARE, True
    else:
        return None
    return Selection(
        card=None,
        blend_card=None,
        unit_price=unit_price,
        divisor=1_000_000,
        source=source,
        path_index=0,
        card_unit=vocab.CANONICAL_UNIT[usage.unit],
        estimated=estimated,
        currency="USD",
    )


def _in_house_selection(usage: Usage) -> Selection:
    return Selection(
        card=None,
        blend_card=None,
        unit_price=Decimal("0"),
        divisor=1,
        source="in_house",
        path_index=0,
        card_unit=vocab.CANONICAL_UNIT.get(usage.unit, usage.unit),
        estimated=False,
        currency=vocab.REPORTING_CURRENCY,
    )


def in_house_price(usage: Usage) -> Selection | None:
    """Zero for an in-house provider's tokens, pages and minutes; GPU hours and storage need a card."""
    if usage.provider in vocab.IN_HOUSE_PROVIDERS and usage.usage_type in vocab.ZERO_PRICED_IN_HOUSE:
        return _in_house_selection(usage)
    return None


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(vocab.AMOUNT_QUANT, rounding=ROUND_HALF_EVEN)


def convert(
    amount: Decimal, currency: str, on: date, rate: FxRate | None
) -> tuple[Decimal | None, Decimal | None, date | None, bool, bool]:
    """``(amount_inr, fx_rate, fx_rate_date, fx_estimated, unconverted)`` for ``amount`` on reporting date ``on``."""
    if currency == vocab.REPORTING_CURRENCY:
        return amount, Decimal("1"), on, False, False
    if amount == 0:
        return _quantize(Decimal("0")), None, None, False, False
    if rate is None or rate.rate_date > on:
        return None, None, None, False, True
    with localcontext(Context(prec=38)):
        inr = _quantize(amount * rate.rate_to_inr)
    return inr, rate.rate_to_inr, rate.rate_date, rate.rate_date != on, False


def unpriced() -> Priced:
    return Priced(
        amount=None,
        currency=None,
        unit_price=None,
        amount_inr=None,
        fx_rate=None,
        fx_rate_date=None,
        rate_card_id=None,
        blend_card_id=None,
        card_unit=None,
        price_source="none",
        unpriced=True,
        fx_estimated=False,
        unconverted=False,
        price_estimated=False,
    )


def _selection(usage: Usage, cards: Sequence[Card]) -> Selection | None:
    return select_price(usage, cards) or fallback_price(usage) or in_house_price(usage)


def price_with(usage: Usage, cards: Sequence[Card], rate_for: Callable[[str], FxRate | None]) -> Priced:
    """Price ``usage`` against ``cards`` (pure); ``rate_for(currency)`` is asked only when a conversion needs it."""
    selection = _selection(usage, cards)
    if selection is None:
        return unpriced()
    with localcontext(Context(prec=38)):
        raw = usage.quantity / Decimal(selection.divisor) * selection.unit_price
        if usage.batch and selection.card is not None and selection.card.batch_discount_pct:
            raw = raw * (Decimal("1") - selection.card.batch_discount_pct / Decimal("100"))
        amount = _quantize(raw)
    needs_rate = selection.currency != vocab.REPORTING_CURRENCY and amount != 0
    rate = rate_for(selection.currency) if needs_rate else None
    amount_inr, fx_rate, fx_rate_date, fx_estimated, unconverted = convert(
        amount, selection.currency, usage.fx_on, rate
    )
    return Priced(
        amount=amount,
        currency=selection.currency,
        unit_price=selection.unit_price,
        amount_inr=amount_inr,
        fx_rate=fx_rate,
        fx_rate_date=fx_rate_date,
        rate_card_id=selection.card.id if selection.card else None,
        blend_card_id=selection.blend_card.id if selection.blend_card else None,
        card_unit=selection.card_unit,
        price_source=selection.source,
        unpriced=False,
        fx_estimated=fx_estimated,
        unconverted=unconverted,
        price_estimated=selection.estimated,
    )


def tiered_amount(tiers: Sequence[Tier], mode: str, start_quantity: Decimal, quantity: Decimal) -> Decimal:
    """The price of ``quantity`` card units starting at cumulative position ``start_quantity``.

    ``graduated``: each unit at the tier its position falls in. ``all_units``:
    every unit at the tier the month's total (``start_quantity + quantity``)
    reaches, a tier being reached once the total passes its ``from_quantity``.
    """
    if not tiers or quantity <= 0:
        return _quantize(Decimal("0"))
    ordered = sorted(tiers, key=lambda tier: tier.from_quantity)
    end = start_quantity + quantity
    with localcontext(Context(prec=38)):
        if mode == "all_units":
            reached = ordered[0]
            for tier in ordered:
                if tier.from_quantity < end:
                    reached = tier
            return _quantize(quantity * reached.unit_price)
        total = Decimal("0")
        for index, tier in enumerate(ordered):
            upper = ordered[index + 1].from_quantity if index + 1 < len(ordered) else None
            low = max(start_quantity, tier.from_quantity)
            high = end if upper is None else min(end, upper)
            if high > low:
                total += (high - low) * tier.unit_price
        return _quantize(total)


def parse_tiers(raw: Any) -> tuple[Tier, ...]:
    """Stored tiers (``[{"from_quantity": "0", "unit_price": "2.5"}, ...]``) as ``Tier`` values."""
    out: list[Tier] = []
    for item in raw or []:
        if isinstance(item, dict):
            out.append(Tier(Decimal(str(item["from_quantity"])), Decimal(str(item["unit_price"]))))
    return tuple(out)


def card_from_row(row: Any) -> Card:
    """A ``Card`` from a ``spend_rate_cards`` row."""
    return Card(
        id=row.id,
        provider=row.provider,
        usage_type=row.usage_type,
        model_sku=row.model_sku or "",
        unit=row.unit,
        unit_price=Decimal(row.unit_price),
        currency=str(row.currency).strip(),
        cached_unit_price=Decimal(row.cached_unit_price) if row.cached_unit_price is not None else None,
        batch_discount_pct=Decimal(row.batch_discount_pct or 0),
        volume_tiers=parse_tiers(row.volume_tiers),
        tier_mode=row.tier_mode,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        source=row.source,
        created_at=getattr(row, "created_at", None),
        updated_at=getattr(row, "updated_at", None),
        status=row.status,
    )


def priced_json(priced: Priced) -> dict[str, Any]:
    """A ``Priced`` for JSON: amounts, prices and rates as decimal text."""
    return {
        "amount": vocab.dec_str(priced.amount),
        "currency": priced.currency,
        "unit_price": vocab.dec_str(priced.unit_price),
        "amount_inr": vocab.dec_str(priced.amount_inr),
        "fx_rate": vocab.dec_str(priced.fx_rate),
        "fx_rate_date": priced.fx_rate_date.isoformat() if priced.fx_rate_date else None,
        "rate_card_id": str(priced.rate_card_id) if priced.rate_card_id else None,
        "blend_card_id": str(priced.blend_card_id) if priced.blend_card_id else None,
        "card_unit": priced.card_unit,
        "price_source": priced.price_source,
        "flags": priced.flags,
    }


# ---------------------------------------------------------------- aliases


def canonical_model(provider: str, model: str, aliases: Mapping[tuple[str, str], str]) -> str:
    """The SKU a called model prices and reconciles as: lower-cased, checked, then the tenant's alias.

    A name that is not a valid SKU is kept lower-cased and bounded (no card
    can name it, so it falls back or is unpriced); it is never refused here,
    because this runs on metering paths.
    """
    name = str(model or "").strip()
    if not name:
        return ""
    try:
        sku = vocab.norm_sku(name)
    except SpendError:
        return name.lower()[:128]
    return aliases.get((provider, sku), sku)


async def alias_map(session: Any, tenant_id: uuid.UUID) -> dict[tuple[str, str], str]:
    """The tenant's aliases, read now: ``(provider, alias) -> model_sku``."""
    from sqlalchemy import select

    from core.models.spend import SpendModelAlias

    rows = (
        await session.execute(
            select(SpendModelAlias.provider, SpendModelAlias.alias, SpendModelAlias.model_sku).where(
                SpendModelAlias.tenant_id == tenant_id
            )
        )
    ).all()
    return {(row[0], row[1]): row[2] for row in rows}


async def cached_aliases(session: Any, tenant_id: uuid.UUID) -> dict[tuple[str, str], str]:
    """The tenant's aliases, cached for ``ALIAS_TTL_SECONDS`` in this process."""
    key = str(tenant_id)
    now = time.monotonic()
    hit = _ALIAS_CACHE.get(key)
    if hit is not None and now - hit[0] < ALIAS_TTL_SECONDS:
        return hit[1]
    found = await alias_map(session, tenant_id)
    if len(_ALIAS_CACHE) >= _ALIAS_CACHE_MAX:
        _ALIAS_CACHE.clear()
    _ALIAS_CACHE[key] = (now, found)
    return found


def invalidate_aliases(tenant_id: uuid.UUID | str) -> None:
    """Drop the tenant's cached aliases in this process (other processes refresh within the TTL)."""
    _ALIAS_CACHE.pop(str(tenant_id), None)


# ---------------------------------------------------------------- loaders


async def load_cards(
    session: Any,
    tenant_id: uuid.UUID,
    *,
    providers: Collection[str],
    usage_types: Collection[str],
    start: date,
    end: date,
) -> list[Card]:
    """Active cards of the providers and usage types in force at some date of ``[start, end]``."""
    from sqlalchemy import or_, select

    from core.models.spend import SpendRateCard

    if not providers or not usage_types:
        return []
    statement = select(SpendRateCard).where(
        SpendRateCard.tenant_id == tenant_id,
        SpendRateCard.status == "active",
        SpendRateCard.provider.in_(sorted(set(providers))),
        SpendRateCard.usage_type.in_(sorted(set(usage_types))),
        SpendRateCard.effective_from <= end,
        or_(SpendRateCard.effective_to.is_(None), SpendRateCard.effective_to > start),
    )
    rows = (await session.execute(statement)).scalars().all()
    return [card_from_row(row) for row in rows]


async def rate_on(session: Any, tenant_id: uuid.UUID, currency: str, on: date) -> FxRate | None:
    """The rate of ``currency`` on ``on``, else the latest earlier one (``core.spend.fx.rate_on``)."""
    from core.spend import fx

    return await fx.rate_on(session, tenant_id, currency, on)


def _through_aliases(usage: Usage, aliases: Mapping[tuple[str, str], str]) -> Usage:
    """``usage`` with its model canonical, keeping the name as called when an alias replaced it.

    A caller that applied the aliases itself passes the called name in
    ``called_model``; it is kept.
    """
    called = canonical_model(usage.provider, usage.model, {})
    model = canonical_model(usage.provider, usage.model, aliases)
    return replace(usage, model=model, called_model=usage.called_model or (called if called != model else ""))


async def price_many(session: Any, tenant_id: uuid.UUID, usages: Sequence[Usage]) -> list[Priced]:
    """Price a batch: cards loaded once, one FX lookup per (currency, date), models through the aliases."""
    if not usages:
        return []
    aliases = await cached_aliases(session, tenant_id)
    usages = [_through_aliases(u, aliases) for u in usages]
    cards = await load_cards(
        session,
        tenant_id,
        providers={u.provider for u in usages},
        usage_types={u.usage_type for u in usages},
        start=min(u.on for u in usages),
        end=max(u.on for u in usages),
    )
    needed: set[tuple[str, date]] = set()
    for usage in usages:
        selection = _selection(usage, cards)
        if (
            selection is not None
            and selection.currency != vocab.REPORTING_CURRENCY
            and selection.unit_price != 0
            and usage.quantity != 0
        ):
            needed.add((selection.currency, usage.fx_on))
    rates: dict[tuple[str, date], FxRate | None] = {}
    for currency, on in sorted(needed):
        rates[(currency, on)] = await rate_on(session, tenant_id, currency, on)
    return [price_with(u, cards, lambda currency, on=u.fx_on: rates.get((currency, on))) for u in usages]


async def price(session: Any, tenant_id: uuid.UUID, usage: Usage) -> Priced:
    """Price one usage (``price_many`` of one)."""
    return (await price_many(session, tenant_id, [usage]))[0]


async def quote(
    tenant_id: uuid.UUID,
    *,
    provider: str,
    usage_type: str,
    unit: str,
    quantity: Any,
    model: str,
    on: date,
    fx_on: date | None,
) -> dict[str, Any]:
    """The price of a hypothetical usage at a billing date, for an administrator checking the cards."""
    from core.database import get_tenant_session

    usage_type = vocab.choice(usage_type, vocab.USAGE_TYPES, field="usage_type", code="invalid_unit")
    unit_name = str(unit or "").strip().lower()
    if unit_name not in vocab.RECORD_UNITS[usage_type]:
        raise SpendError(422, "invalid_unit", f"unit is one of {', '.join(vocab.RECORD_UNITS[usage_type])}")
    usage = Usage(
        provider=vocab.norm_provider(provider),
        usage_type=usage_type,
        unit=unit_name,
        quantity=vocab.parse_decimal(
            quantity, field="quantity", minimum=0, maximum=vocab.MAX_QUOTE_QUANTITY, places=vocab.QUANTITY_PLACES
        ),
        model=str(model or "").strip(),
        on=on,
        fx_on=fx_on or on,
    )
    async with get_tenant_session(tenant_id) as session:
        priced = await price(session, tenant_id, usage)
    return priced_json(priced)
