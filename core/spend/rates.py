# SPDX-License-Identifier: Apache-2.0
"""Rate cards: effective-dated prices, refused overlaps, corrections instead of edits.

A card prices one key ``(provider, usage_type, model_sku, unit, source)``
over ``[effective_from, effective_to)`` in the provider's billing dates
(``model_sku`` ``''`` is the provider-wide default for the usage type). Two
**active** cards of one key may never overlap; the check runs under the
key's advisory lock on every create, every update of a date or the status,
every correction and every import row. A list card and a contract card of
one key may coexist; the contract card wins (``core/spend/pricing.py``).

A rate change is a new card: ``supersede`` closes the open predecessor at
the new card's start. Backdating it over priced records needs ``restate``.
Price fields of a card change only before the card starts and while no
record references it; otherwise a **correction** retires the card and
inserts its replacement with the same key and dates (``replaces_id``), and
the referencing records are restated. Retired means "replaced by a
correction"; a contract ends with ``effective_to``. A retired card prices
nothing.

In this part no usage record exists yet, so ``card_in_use`` answers ``None``
and no restatement job is enqueued (``restate_job_id`` is ``null``).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, NamedTuple

import structlog
from sqlalchemy import func, or_, select

from core.spend import audit, clock, imports, locks, vocab
from core.spend.errors import SpendError, require_actor
from core.spend.pricing import parse_tiers

logger = structlog.get_logger()

IMPORT_REQUIRED = ("provider", "usage_type", "unit", "unit_price", "currency", "effective_from", "source")
IMPORT_OPTIONAL = (
    "model_sku",
    "effective_to",
    "cached_unit_price",
    "batch_discount_pct",
    "volume_tiers",
    "tier_mode",
    "reference",
    "supersede",
    "restate",
)
# Fields that change only before the card starts and while nothing references it.
PRICE_FIELDS = (
    "unit_price",
    "cached_unit_price",
    "batch_discount_pct",
    "volume_tiers",
    "tier_mode",
    "currency",
    "effective_from",
)
AUDIT_FIELDS = (
    "unit_price",
    "cached_unit_price",
    "batch_discount_pct",
    "currency",
    "tier_mode",
    "volume_tiers",
    "effective_from",
    "effective_to",
    "status",
    "replaces_id",
    "reference",
)
CORRECTABLE_FIELDS = ("unit_price", "cached_unit_price", "batch_discount_pct", "volume_tiers", "tier_mode", "currency")
REASON_MIN = 10
REASON_MAX = 500


class CardKey(NamedTuple):
    provider: str
    usage_type: str
    model_sku: str
    unit: str
    source: str


def _on_change(tenant_id: uuid.UUID) -> None:
    """Called after every rate-card write; a later part drops the priced-tools cache here."""
    return None


# ---------------------------------------------------------------- pure checks


def overlaps(a_from: date, a_to: date | None, b_from: date, b_to: date | None) -> bool:
    """Whether two half-open date ranges intersect (``None`` = open-ended)."""
    return (b_to is None or a_from < b_to) and (a_to is None or b_from < a_to)


def validate_tiers(raw: Any, *, mode: str) -> list[dict[str, str]]:
    """Volume tiers in card units: ascending, the first at 0, at most 20, bounded prices; as decimal text."""
    vocab.choice(mode, vocab.TIER_MODES, field="tier_mode")
    if raw in (None, "", []):
        return []
    if isinstance(raw, str):
        try:
            # Numbers as Decimal: a binary float would change a price past about 15 significant digits.
            raw = json.loads(raw, parse_float=Decimal)
        except (ValueError, RecursionError):
            raise SpendError(422, "invalid_number", "volume_tiers is a JSON array of tiers") from None
    if not isinstance(raw, list):
        raise SpendError(422, "invalid_number", "volume_tiers is a list of tiers")
    if len(raw) > vocab.MAX_TIERS:
        raise SpendError(422, "invalid_number", f"a card has at most {vocab.MAX_TIERS} tiers")
    tiers: list[dict[str, str]] = []
    previous: Decimal | None = None
    for item in raw:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, dict):
            raise SpendError(422, "invalid_number", "a tier is an object with from_quantity and unit_price")
        start = vocab.parse_decimal(
            item.get("from_quantity"),
            field="from_quantity",
            minimum=0,
            maximum=vocab.MAX_TIER_QUANTITY,
            places=vocab.QUANTITY_PLACES,
        )
        price = vocab.parse_decimal(
            item.get("unit_price"),
            field="unit_price",
            minimum=0,
            maximum=vocab.MAX_UNIT_PRICE,
            places=vocab.PRICE_PLACES,
        )
        if previous is None and start != 0:
            raise SpendError(422, "invalid_number", "the first tier starts at from_quantity 0")
        if previous is not None and start <= previous:
            raise SpendError(422, "invalid_number", "tiers are in ascending from_quantity order")
        previous = start
        tiers.append({"from_quantity": format(start, "f"), "unit_price": format(price, "f")})
    return tiers


def _decimal_field(value: Any, field: str, *, maximum: Decimal, places: int) -> Decimal:
    return vocab.parse_decimal(value, field=field, minimum=0, maximum=maximum, places=places)


def _cached_price(value: Any, unit: str) -> Decimal | None:
    if value in (None, ""):
        return None
    if unit != "1m_input_tokens":
        raise SpendError(422, "invalid_unit", "only a 1m_input_tokens card carries a cached-input price")
    return _decimal_field(value, "cached_unit_price", maximum=vocab.MAX_UNIT_PRICE, places=vocab.PRICE_PLACES)


def check_card(body: dict[str, Any]) -> dict[str, Any]:
    """A card's fields, checked whole (the create body and an import row)."""
    usage_type = vocab.choice(body.get("usage_type"), vocab.USAGE_TYPES, field="usage_type", code="invalid_unit")
    unit = vocab.choice(body.get("unit"), vocab.CARD_UNITS[usage_type], field="unit", code="invalid_unit")
    tier_mode_raw = body.get("tier_mode")
    tier_mode = vocab.choice(
        tier_mode_raw if tier_mode_raw not in (None, "") else "graduated", vocab.TIER_MODES, field="tier_mode"
    )
    effective_from = vocab.parse_date(body.get("effective_from"), field="effective_from")
    effective_to = (
        vocab.parse_date(body["effective_to"], field="effective_to")
        if body.get("effective_to") not in (None, "")
        else None
    )
    if effective_to is not None and effective_to <= effective_from:
        raise SpendError(422, "invalid_period", "effective_to is after effective_from (it is exclusive)")
    batch = body.get("batch_discount_pct")
    return {
        "provider": vocab.norm_provider(body.get("provider")),
        "usage_type": usage_type,
        "model_sku": vocab.norm_sku(body.get("model_sku"), allow_empty=True),
        "unit": unit,
        "unit_price": _decimal_field(
            body.get("unit_price"), "unit_price", maximum=vocab.MAX_UNIT_PRICE, places=vocab.PRICE_PLACES
        ),
        "currency": vocab.norm_currency(body.get("currency")),
        "cached_unit_price": _cached_price(body.get("cached_unit_price"), unit),
        "batch_discount_pct": Decimal("0")
        if batch in (None, "")
        else _decimal_field(batch, "batch_discount_pct", maximum=Decimal("100"), places=vocab.PCT_PLACES),
        "volume_tiers": validate_tiers(body.get("volume_tiers"), mode=tier_mode),
        "tier_mode": tier_mode,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "source": vocab.choice(body.get("source"), vocab.CARD_SOURCES, field="source"),
        "reference": vocab.free_text(body.get("reference"), field="reference", max_len=200),
    }


def check_reason(reason: Any) -> str:
    """A correction's reason: 10 to 500 characters of plain text."""
    text = str(reason or "").strip()
    if len(text) < REASON_MIN or len(text) > REASON_MAX:
        raise SpendError(
            422, "reason_required", f"a correction gives its reason in {REASON_MIN} to {REASON_MAX} characters"
        )
    return vocab.free_text(text, field="reason", max_len=REASON_MAX, required=True)


def _key_of(row: Any) -> CardKey:
    return CardKey(row.provider, row.usage_type, row.model_sku or "", row.unit, row.source)


def _key_text(row: Any) -> str:
    key = _key_of(row)
    return f"{key.provider}:{key.usage_type}:{key.model_sku}:{key.unit}:{key.source}:{row.effective_from.isoformat()}"


def _audit_fields(row: Any) -> dict[str, Any]:
    return {name: getattr(row, name) for name in AUDIT_FIELDS}


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def card_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "provider": row.provider,
        "usage_type": row.usage_type,
        "model_sku": row.model_sku or "",
        "unit": row.unit,
        "unit_price": vocab.dec_str(row.unit_price),
        "currency": str(row.currency).strip(),
        "cached_unit_price": vocab.dec_str(row.cached_unit_price),
        "batch_discount_pct": vocab.dec_str(row.batch_discount_pct),
        "volume_tiers": list(row.volume_tiers or []),
        "tier_mode": row.tier_mode,
        "effective_from": _iso(row.effective_from),
        "effective_to": _iso(row.effective_to),
        "source": row.source,
        "status": row.status,
        "replaces_id": str(row.replaces_id) if row.replaces_id else None,
        "retired_at": _iso(row.retired_at),
        "reference": row.reference or "",
        "updated_by": row.updated_by or "",
        "updated_at": _iso(getattr(row, "updated_at", None)),
    }


# ---------------------------------------------------------------- store helpers


async def card_in_use(session: Any, tenant_id: uuid.UUID, card_id: uuid.UUID) -> date | None:
    """The latest billing date of a record the card priced; ``None`` while no usage records exist (this part)."""
    return None


async def _lock(session: Any, tenant_id: uuid.UUID, key: CardKey) -> None:
    await locks.xact_lock(
        session, locks.rate_card(tenant_id, key.provider, key.usage_type, key.model_sku, key.unit, key.source)
    )


async def _active_cards(session: Any, tenant_id: uuid.UUID, key: CardKey) -> list[Any]:
    from core.models.spend import SpendRateCard

    statement = select(SpendRateCard).where(
        SpendRateCard.tenant_id == tenant_id,
        SpendRateCard.status == "active",
        SpendRateCard.provider == key.provider,
        SpendRateCard.usage_type == key.usage_type,
        SpendRateCard.model_sku == key.model_sku,
        SpendRateCard.unit == key.unit,
        SpendRateCard.source == key.source,
    )
    return list((await session.execute(statement)).scalars().all())


async def check_overlap(
    session: Any,
    tenant_id: uuid.UUID,
    key: CardKey,
    eff_from: date,
    eff_to: date | None,
    *,
    exclude_id: uuid.UUID | None,
) -> None:
    """409 ``rate_card_overlap`` naming the active card of ``key`` whose range meets ``[eff_from, eff_to)``."""
    for other in await _active_cards(session, tenant_id, key):
        if other.id == exclude_id:
            continue
        if overlaps(eff_from, eff_to, other.effective_from, other.effective_to):
            raise SpendError(
                409,
                "rate_card_overlap",
                f"card {other.id} covers {other.effective_from.isoformat()} to "
                f"{other.effective_to.isoformat() if other.effective_to else 'open'} for the same key",
            )


async def _card_row(session: Any, tenant_id: uuid.UUID, card_id: uuid.UUID, *, for_update: bool = False) -> Any:
    from core.models.spend import SpendRateCard

    statement = select(SpendRateCard).where(SpendRateCard.tenant_id == tenant_id, SpendRateCard.id == card_id)
    if for_update:
        # Re-read under the key's lock: the row's current values, not the ones the first read cached.
        statement = statement.with_for_update().execution_options(populate_existing=True)
    rows = (await session.execute(statement)).scalars().all()
    if not rows:
        raise SpendError(404, "not_found", "no such rate card")
    return rows[0]


async def _locked_card(session: Any, tenant_id: uuid.UUID, card_id: uuid.UUID) -> Any:
    """The card, read again under its key's advisory lock and then row-locked.

    Every writer of a card takes the key's advisory lock before it touches a
    card row (a superseding create and an import update the predecessor row
    while holding it), so the row lock is taken second here too. Taking the
    row lock first would deadlock against them. The key fields never change,
    so the plain first read names the right lock.
    """
    row = await _card_row(session, tenant_id, card_id)
    await _lock(session, tenant_id, _key_of(row))
    return await _card_row(session, tenant_id, card_id, for_update=True)


def _today(provider: str, now: datetime) -> date:
    return clock.today_in(clock.billing_zone(provider), now)


async def _create_in(
    session: Any,
    tenant_id: uuid.UUID,
    fields: dict[str, Any],
    *,
    supersede: bool,
    restate: bool,
    who: str,
    now: datetime,
) -> tuple[Any, list[audit.Change], uuid.UUID | None]:
    """Insert a card (closing its open predecessor when ``supersede``); the caller holds the key's lock."""
    from core.models.spend import SpendRateCard

    key = CardKey(fields["provider"], fields["usage_type"], fields["model_sku"], fields["unit"], fields["source"])
    changes: list[audit.Change] = []
    superseded: uuid.UUID | None = None
    if supersede:
        start = fields["effective_from"]
        for previous in await _active_cards(session, tenant_id, key):
            if previous.effective_to is not None or previous.effective_from >= start:
                continue
            used_until = await card_in_use(session, tenant_id, previous.id)
            if used_until is not None and used_until >= start and not restate:
                raise SpendError(
                    409,
                    "restate_required",
                    f"card {previous.id} priced records from {start.isoformat()} to "
                    f"{_today(key.provider, now).isoformat()}; send restate=true to restate them",
                )
            before = _audit_fields(previous)
            previous.effective_to = start
            previous.updated_by = who
            previous.updated_at = now
            changes.append(audit.Change(_key_text(previous), before, _audit_fields(previous)))
            superseded = previous.id
        await session.flush()
    await check_overlap(session, tenant_id, key, fields["effective_from"], fields["effective_to"], exclude_id=None)
    row = SpendRateCard(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        status="active",
        created_by=who,
        updated_by=who,
        created_at=now,
        updated_at=now,
        **fields,
    )
    session.add(row)
    await session.flush()
    changes.append(audit.Change(_key_text(row), None, _audit_fields(row)))
    return row, changes, superseded


def _patch_values(row: Any, body: dict[str, Any]) -> dict[str, Any]:
    """The checked values of ``body`` that differ from ``row``."""
    new: dict[str, Any] = {}
    tier_mode = row.tier_mode
    if body.get("tier_mode") not in (None, ""):
        tier_mode = vocab.choice(body["tier_mode"], vocab.TIER_MODES, field="tier_mode")
        if tier_mode != row.tier_mode:
            new["tier_mode"] = tier_mode
    if "unit_price" in body and body["unit_price"] is not None:
        value = _decimal_field(
            body["unit_price"], "unit_price", maximum=vocab.MAX_UNIT_PRICE, places=vocab.PRICE_PLACES
        )
        if value != row.unit_price:
            new["unit_price"] = value
    if "cached_unit_price" in body:
        cached = _cached_price(body["cached_unit_price"], row.unit)
        if cached != row.cached_unit_price:
            new["cached_unit_price"] = cached
    if "batch_discount_pct" in body and body["batch_discount_pct"] is not None:
        value = _decimal_field(body["batch_discount_pct"], "batch_discount_pct", maximum=Decimal("100"), places=2)
        if value != row.batch_discount_pct:
            new["batch_discount_pct"] = value
    if "volume_tiers" in body and body["volume_tiers"] is not None:
        tiers = validate_tiers(body["volume_tiers"], mode=tier_mode)
        if parse_tiers(tiers) != parse_tiers(row.volume_tiers):
            new["volume_tiers"] = tiers
    if body.get("currency") not in (None, ""):
        currency = vocab.norm_currency(body["currency"])
        if currency != str(row.currency).strip():
            new["currency"] = currency
    if body.get("effective_from") not in (None, ""):
        start = vocab.parse_date(body["effective_from"], field="effective_from")
        if start != row.effective_from:
            new["effective_from"] = start
    if "effective_to" in body:
        end = vocab.parse_date(body["effective_to"], field="effective_to") if body["effective_to"] else None
        if end != row.effective_to:
            new["effective_to"] = end
    if body.get("status") not in (None, ""):
        status = vocab.choice(body["status"], ("retired",), field="status")
        if status != row.status:
            new["status"] = status
    if "reference" in body:
        reference = vocab.free_text(body.get("reference"), field="reference", max_len=200)
        if reference != (row.reference or ""):
            new["reference"] = reference
    start = new.get("effective_from", row.effective_from)
    end = new.get("effective_to", row.effective_to)
    if end is not None and end <= start:
        raise SpendError(422, "invalid_period", "effective_to is after effective_from (it is exclusive)")
    return new


async def _update_in(
    session: Any,
    tenant_id: uuid.UUID,
    row: Any,
    body: dict[str, Any],
    *,
    restate: bool,
    who: str,
    now: datetime,
) -> list[audit.Change]:
    """Apply a change to a card under the rules of the lifecycle; the caller holds the key's lock."""
    if row.status != "active":
        raise SpendError(409, "card_in_use", "a retired card is kept as history; change its replacement")
    new = _patch_values(row, body)
    if not new:
        return []
    today = _today(row.provider, now)
    used_until = await card_in_use(session, tenant_id, row.id)
    if any(name in new for name in PRICE_FIELDS) and (used_until is not None or row.effective_from <= today):
        raise SpendError(
            409,
            "card_in_use",
            "the card has started or priced records: correct it with POST /spend/rate-cards/{card_id}/correct",
        )
    if new.get("status") == "retired" and used_until is not None:
        raise SpendError(409, "card_in_use", "a card that priced records is retired only by a correction (/correct)")
    if "effective_to" in new:
        end = new["effective_to"]
        if end is not None and end <= today and used_until is not None and used_until >= end and not restate:
            raise SpendError(
                409,
                "restate_required",
                f"records from {end.isoformat()} to {today.isoformat()} used this card; send restate=true",
            )
    if new.get("status", row.status) == "active" and ("effective_to" in new or "effective_from" in new):
        await check_overlap(
            session,
            tenant_id,
            _key_of(row),
            new.get("effective_from", row.effective_from),
            new.get("effective_to", row.effective_to),
            exclude_id=row.id,
        )
    before = _audit_fields(row)
    key_text = _key_text(row)
    for name, value in new.items():
        setattr(row, name, value)
    if new.get("status") == "retired":
        row.retired_at = now
    row.updated_by = who
    row.updated_at = now
    await session.flush()
    return [audit.Change(key_text, before, _audit_fields(row))]


# ---------------------------------------------------------------- services


async def create_card(
    tenant_id: uuid.UUID, body: dict[str, Any], *, actor: str, now: datetime | None = None
) -> dict[str, Any]:
    """A new card; ``supersede`` closes the open predecessor of the key, ``restate`` allows a backdated one."""
    from core.database import get_tenant_session

    who = require_actor(actor)
    fields = check_card(body)
    stamp = now or clock.now_utc()
    supersede = bool(body.get("supersede"))
    restate = bool(body.get("restate"))
    key = CardKey(fields["provider"], fields["usage_type"], fields["model_sku"], fields["unit"], fields["source"])
    async with get_tenant_session(tenant_id) as session:
        await _lock(session, tenant_id, key)
        row, changes, superseded = await _create_in(
            session, tenant_id, fields, supersede=supersede, restate=restate, who=who, now=stamp
        )
        session.add(
            audit.audit_change(
                tenant_id,
                actor_id=who,
                action="rate_cards.create",
                resource_type="spend_rate_card",
                resource_id=str(row.id),
                changes=changes,
                now=stamp,
            )
        )
        out = card_dict(row)
    _on_change(tenant_id)
    logger.info("spend_rate_card_created", usage_type=key.usage_type, source=key.source)
    return {**out, "superseded_id": str(superseded) if superseded else None, "restate_job_id": None}


async def update_card(
    tenant_id: uuid.UUID, card_id: uuid.UUID, body: dict[str, Any], *, actor: str, now: datetime | None = None
) -> dict[str, Any]:
    """Change a card's reference, end, status or (before it starts, while unused) its price fields."""
    from core.database import get_tenant_session

    who = require_actor(actor)
    stamp = now or clock.now_utc()
    async with get_tenant_session(tenant_id) as session:
        row = await _locked_card(session, tenant_id, card_id)
        changes = await _update_in(session, tenant_id, row, body, restate=bool(body.get("restate")), who=who, now=stamp)
        if changes:
            session.add(
                audit.audit_change(
                    tenant_id,
                    actor_id=who,
                    action="rate_cards.update",
                    resource_type="spend_rate_card",
                    resource_id=str(row.id),
                    changes=changes,
                    now=stamp,
                )
            )
        out = card_dict(row)
    if changes:
        _on_change(tenant_id)
    return {**out, "restate_job_id": None}


async def correct_card(
    tenant_id: uuid.UUID,
    card_id: uuid.UUID,
    body: dict[str, Any],
    *,
    reason: str,
    actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Retire a card and insert its replacement with the same key and start, the corrected fields and a reason."""
    from core.database import get_tenant_session
    from core.models.spend import SpendRateCard

    who = require_actor(actor)
    why = check_reason(reason)
    stamp = now or clock.now_utc()
    async with get_tenant_session(tenant_id) as session:
        old = await _locked_card(session, tenant_id, card_id)
        if old.status != "active":
            raise SpendError(409, "card_in_use", "the card is already retired; correct its replacement")
        key = _key_of(old)
        merged = {name: getattr(old, name) for name in CORRECTABLE_FIELDS}
        merged["currency"] = str(old.currency).strip()
        for name in CORRECTABLE_FIELDS:
            if name in body and (body[name] is not None or name == "cached_unit_price"):
                merged[name] = body[name]
        tier_mode = vocab.choice(merged["tier_mode"], vocab.TIER_MODES, field="tier_mode")
        effective_to = old.effective_to
        if "effective_to" in body:
            effective_to = (
                vocab.parse_date(body["effective_to"], field="effective_to") if body["effective_to"] else None
            )
        if effective_to is not None and effective_to <= old.effective_from:
            raise SpendError(422, "invalid_period", "effective_to is after effective_from (it is exclusive)")
        fields = {
            "provider": old.provider,
            "usage_type": old.usage_type,
            "model_sku": old.model_sku or "",
            "unit": old.unit,
            "source": old.source,
            "unit_price": _decimal_field(
                merged["unit_price"], "unit_price", maximum=vocab.MAX_UNIT_PRICE, places=vocab.PRICE_PLACES
            ),
            "cached_unit_price": _cached_price(merged["cached_unit_price"], old.unit),
            "batch_discount_pct": _decimal_field(
                merged["batch_discount_pct"] or 0, "batch_discount_pct", maximum=Decimal("100"), places=2
            ),
            "volume_tiers": validate_tiers(merged["volume_tiers"], mode=tier_mode),
            "tier_mode": tier_mode,
            "currency": vocab.norm_currency(merged["currency"]),
            "effective_from": old.effective_from,
            "effective_to": effective_to,
            "reference": old.reference or "",
        }
        before_old = _audit_fields(old)
        old.status = "retired"
        old.retired_at = stamp
        old.updated_by = who
        old.updated_at = stamp
        await session.flush()
        await check_overlap(session, tenant_id, key, fields["effective_from"], effective_to, exclude_id=old.id)
        new = SpendRateCard(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            status="active",
            replaces_id=old.id,
            created_by=who,
            updated_by=who,
            created_at=stamp,
            updated_at=stamp,
            **fields,
        )
        session.add(new)
        await session.flush()
        changes = [
            audit.Change(_key_text(old), before_old, _audit_fields(old)),
            audit.Change(_key_text(new), None, _audit_fields(new)),
        ]
        for entry in audit.audit_changes(
            tenant_id,
            actor_id=who,
            action="rate_cards.correct",
            resource_type="spend_rate_card",
            changes=changes,
            summary={"reason": why, "retired_id": str(old.id), "card_id": str(new.id)},
            file_sha256=None,
            resource_id=str(new.id),
            now=stamp,
        ):
            session.add(entry)
        out = card_dict(new)
        retired_id = str(old.id)
    _on_change(tenant_id)
    logger.info("spend_rate_card_corrected", usage_type=key.usage_type)
    return {"card": out, "retired_id": retired_id, "restate_job_id": None}


def _import_row(raw: dict[str, str]) -> tuple[dict[str, Any], bool, bool]:
    fields = check_card(raw)
    supersede = bool(vocab.parse_bool(raw.get("supersede"), field="supersede"))
    restate = bool(vocab.parse_bool(raw.get("restate"), field="restate"))
    return fields, supersede, restate


async def import_cards(
    tenant_id: uuid.UUID,
    rows: list[dict[str, str]],
    *,
    actor: str,
    dry_run: bool,
    file_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Upsert cards by ``(provider, usage_type, model_sku, unit, source, effective_from)`` among active cards.

    An identical card is unchanged; a different one goes through the update
    rules, a new one through the create rules (overlap checked against the
    database and the file's earlier rows). Each row runs in its own savepoint.
    """
    from core.database import get_tenant_session

    who = require_actor(actor)
    stamp = now or clock.now_utc()
    report = imports.new_report(dry_run=dry_run, received=len(rows))
    checked: list[tuple[int, dict[str, Any], set[str], bool, bool]] = []
    seen: set[tuple[CardKey, date]] = set()
    for index, raw in enumerate(rows, start=2):
        label = ":".join(str(raw.get(k, "")) for k in ("provider", "usage_type", "model_sku", "unit", "source"))
        try:
            fields, supersede, restate = _import_row(raw)
        except SpendError as exc:
            imports.reject(report, row=index, key=label, reason=exc.code)
            continue
        key = CardKey(fields["provider"], fields["usage_type"], fields["model_sku"], fields["unit"], fields["source"])
        if (key, fields["effective_from"]) in seen:
            imports.reject(report, row=index, key=label, reason="duplicate_in_file")
            continue
        seen.add((key, fields["effective_from"]))
        checked.append((index, fields, set(raw), supersede, restate))
    changes: list[audit.Change] = []
    async with get_tenant_session(tenant_id) as session:
        keys = sorted({k for k, _start in seen})
        for key in keys:
            await _lock(session, tenant_id, key)
        outer = await session.begin_nested() if dry_run else None
        for index, fields, present, supersede, restate in checked:
            key = CardKey(
                fields["provider"], fields["usage_type"], fields["model_sku"], fields["unit"], fields["source"]
            )
            label = f"{key.provider}:{key.usage_type}:{key.model_sku}:{key.unit}:{key.source}"
            try:
                async with session.begin_nested():
                    same = [
                        c
                        for c in await _active_cards(session, tenant_id, key)
                        if c.effective_from == fields["effective_from"]
                    ]
                    if same:
                        body = {
                            name: fields[name]
                            for name in (*PRICE_FIELDS, "effective_to", "reference")
                            if name in IMPORT_REQUIRED or name in present
                        }
                        row_changes = await _update_in(
                            session, tenant_id, same[0], body, restate=restate, who=who, now=stamp
                        )
                        report["updated" if row_changes else "unchanged"] += 1
                    else:
                        _row, row_changes, _superseded = await _create_in(
                            session, tenant_id, fields, supersede=supersede, restate=restate, who=who, now=stamp
                        )
                        report["created"] += 1
            except SpendError as exc:
                imports.reject(report, row=index, key=label, reason=exc.code)
                continue
            changes.extend(row_changes)
        report["rejected"].sort(key=lambda item: item["row"])
        if outer is not None:
            await outer.rollback()
        elif changes:
            for entry in audit.audit_changes(
                tenant_id,
                actor_id=who,
                action="rate_cards.import",
                resource_type="spend_rate_card",
                changes=changes,
                summary=_summary(report),
                file_sha256=file_sha256,
                now=stamp,
            ):
                session.add(entry)
    if changes and not dry_run:
        _on_change(tenant_id)
    return report


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    return {key: report[key] for key in ("received", "created", "updated", "unchanged")} | {
        "rejected": len(report["rejected"])
    }


async def list_cards(
    tenant_id: uuid.UUID,
    *,
    provider: str | None = None,
    usage_type: str | None = None,
    as_of: date | None = None,
    status: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """Cards by key and start date; ``as_of`` keeps the cards in force on that billing date.

    With ``as_of`` and no ``status``, only active cards are listed: a retired card
    (corrected, or retired while unused) keeps its dates as history but prices
    nothing. ``status="retired"`` lists them.
    """
    from core.database import get_tenant_session
    from core.models.spend import SpendRateCard

    conditions = [SpendRateCard.tenant_id == tenant_id]
    if provider:
        conditions.append(SpendRateCard.provider == vocab.norm_provider(provider))
    if usage_type:
        conditions.append(
            SpendRateCard.usage_type
            == vocab.choice(usage_type, vocab.USAGE_TYPES, field="usage_type", code="invalid_unit")
        )
    if status:
        conditions.append(SpendRateCard.status == vocab.choice(status, vocab.CARD_STATUSES, field="status"))
    if as_of is not None:
        conditions.append(SpendRateCard.effective_from <= as_of)
        conditions.append(or_(SpendRateCard.effective_to.is_(None), SpendRateCard.effective_to > as_of))
        if not status:
            # In force means active: a corrected card keeps its dates as history but prices nothing.
            conditions.append(SpendRateCard.status == "active")
    async with get_tenant_session(tenant_id) as session:
        total = (await session.execute(select(func.count()).select_from(SpendRateCard).where(*conditions))).scalar()
        rows = (
            (
                await session.execute(
                    select(SpendRateCard)
                    .where(*conditions)
                    .order_by(
                        SpendRateCard.provider,
                        SpendRateCard.usage_type,
                        SpendRateCard.model_sku,
                        SpendRateCard.unit,
                        SpendRateCard.source,
                        SpendRateCard.effective_from.desc(),
                    )
                    .limit(max(1, min(limit, 500)))
                    .offset(max(0, offset))
                )
            )
            .scalars()
            .all()
        )
    return {"items": [card_dict(row) for row in rows], "total": int(total or 0)}
