# SPDX-License-Identifier: Apache-2.0
"""Usage events: built from a finished model call on the call path, written by the writer off it.

**On the call path** (``meter_model_call``, from ``record_model_call``): the
call's tokens become up to three events (uncached input, cached input,
output), or one estimated ``token`` event when the split is unknown. The
first event of a call carries ``calls = 1``. Nothing here does I/O or
awaits: the events go on the writer's bounded queue (``core/spend/writer.py``).
A call with no tokens is counted as a gap, never guessed, except a router
timeout, whose prompt length gives one estimated input event.

**Keys.** A model call's idempotency key is ``llm:{call_hash}:{unit}``, the
hash taken over fields a ``model_gateway_records`` row reproduces, so the
backfill writes the same keys; a retry or a spill reuses the event and its
key, and ``ON CONFLICT DO NOTHING`` drops repeats. The correlation id (which
can be a client's request id) is stored only as a hash.

**Off the call path** (``write_events``, in one tenant transaction): events
for another tenant are refused; the rollup days are locked shared (a
non-blocking try in the writer); each distinct attribution hint is resolved
once; billing accounts are inferred where the call site had none; events are
priced at their billing date with the tenant's model aliases; records are
inserted in chunks with ``ON CONFLICT DO NOTHING RETURNING``; only the rows
returned add to the rollup, so nothing is counted twice; late events mark
the provider's commitments for a full recompute.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog

from core.spend import vocab
from core.spend.resolver import Hints, Resolved, application_of

logger = structlog.get_logger()

APPEND_GRACE = timedelta(hours=2)
INSERT_CHUNK = 400  # 49 bound columns per row, under asyncpg's 32 767 parameter limit
LLM = "llm_tokens"
_AGENT_USE_CASES = ("agent_run", "agent_resume")


@dataclass(frozen=True)
class WriteResult:
    written: int
    duplicates: int
    skipped: int
    refused: int
    busy: bool
    unpriced: int = 0  # written records that no card or fallback priced


@dataclass(frozen=True)
class UsageEvent:
    tenant_id: str
    usage_type: str
    unit: str
    quantity: Decimal
    provider: str
    model: str
    event_time: datetime
    idempotency_key: str
    source_ref: str
    correlation_ref: str
    hints: Hints
    calls: int = 0
    quantity_estimated: bool = False
    allocated: bool = False
    allocated_from: str | None = None
    billing_account: str | None = None
    batch: bool = False
    resolved: Resolved | None = None  # set by GPU allocation; never travels on the wire
    priced: Any = None  # a pricing.Priced, set by GPU allocation; never travels on the wire
    skip_if_unpriced: bool = False  # priced tool calls

    def to_wire(self) -> dict[str, Any]:
        """Strings, numbers and booleans only: ids, counts, labels and decimal text (no content)."""
        return {
            "tenant_id": self.tenant_id,
            "usage_type": self.usage_type,
            "unit": self.unit,
            "quantity": format(self.quantity, "f"),
            "provider": self.provider,
            "model": self.model,
            "event_time": self.event_time.isoformat(),
            "idempotency_key": self.idempotency_key,
            "source_ref": self.source_ref,
            "correlation_ref": self.correlation_ref,
            "hints": {
                "agent_id": self.hints.agent_id,
                "agent_version": self.hints.agent_version,
                "application": self.hints.application,
                "default_use_case": self.hints.default_use_case,
                "workflow_id": self.hints.workflow_id,
                "workflow_run_id": self.hints.workflow_run_id,
                "run_id": self.hints.run_id,
                "initiating_user_id": self.hints.initiating_user_id,
                "origin": self.hints.origin,
            },
            "calls": self.calls,
            "quantity_estimated": self.quantity_estimated,
            "allocated": self.allocated,
            "allocated_from": self.allocated_from,
            "billing_account": self.billing_account,
            "batch": self.batch,
            "skip_if_unpriced": self.skip_if_unpriced,
        }

    @classmethod
    def from_wire(cls, data: Mapping[str, Any]) -> UsageEvent:
        hints = dict(data.get("hints") or {})
        event_time = datetime.fromisoformat(str(data["event_time"]))
        return cls(
            tenant_id=str(data["tenant_id"]),
            usage_type=str(data["usage_type"]),
            unit=str(data["unit"]),
            quantity=Decimal(str(data["quantity"])),
            provider=str(data["provider"]),
            model=str(data.get("model") or ""),
            event_time=event_time if event_time.tzinfo else event_time.replace(tzinfo=UTC),
            idempotency_key=str(data["idempotency_key"]),
            source_ref=str(data.get("source_ref") or ""),
            correlation_ref=str(data.get("correlation_ref") or ""),
            hints=Hints(
                agent_id=hints.get("agent_id"),
                agent_version=hints.get("agent_version"),
                application=str(hints.get("application") or "system"),
                default_use_case=str(hints.get("default_use_case") or ""),
                workflow_id=hints.get("workflow_id"),
                workflow_run_id=hints.get("workflow_run_id"),
                run_id=hints.get("run_id"),
                initiating_user_id=hints.get("initiating_user_id"),
                origin=str(hints.get("origin") or "hook"),
            ),
            calls=int(data.get("calls") or 0),
            quantity_estimated=bool(data.get("quantity_estimated")),
            allocated=bool(data.get("allocated")),
            allocated_from=data.get("allocated_from"),
            billing_account=data.get("billing_account"),
            batch=bool(data.get("batch")),
            skip_if_unpriced=bool(data.get("skip_if_unpriced")),
        )


@dataclass(frozen=True)
class MeterPlan:
    """What one finished call meters: its events, or the gap it leaves."""

    tenant_id: str | None
    events: list[UsageEvent] = field(default_factory=list)
    gap: tuple[str, str] | None = None  # (reason, detail)
    unmetered: str | None = None  # spend_unmetered_calls_total reason


# ---------------------------------------------------------------- keys and hints


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tenant_text(value: Any) -> str:
    """A tenant id as canonical UUID text when it is one (so the hook and a gateway row hash alike)."""
    text = str(value or "").strip()
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return text


def call_hash(
    tenant_id: str,
    correlation_id: str,
    created_at: datetime,
    provider_raw: str,
    model: str,
    outcome: str,
    tokens: int,
    input_tokens: int | None,
    output_tokens: int | None,
) -> str:
    """sha256 over fields a ``model_gateway_records`` row carries, hex, first 40 characters."""
    from core.governance.model_gateway_records import _canonical

    parts = (
        tenant_text(tenant_id),
        str(correlation_id or ""),
        str(_canonical(created_at)),
        str(provider_raw or ""),
        str(model or ""),
        str(outcome or ""),
        str(int(tokens or 0)),
        "" if input_tokens is None else str(int(input_tokens)),
        "" if output_tokens is None else str(int(output_tokens)),
    )
    return _sha("|".join(parts))[:40]


def correlation_ref(correlation_id: str) -> str:
    """The stored reference of a correlation id: sha256 hex, first 32 characters (never the raw id)."""
    return _sha(str(correlation_id or ""))[:32]


def _identity_application() -> str:
    from core.governance.caller_identity import current_identity

    identity = current_identity()
    if identity is None:
        return "system"
    if identity.auth_mode == "api_key":
        return "api"
    if identity.auth_mode == "legacy" or str(identity.principal or "").startswith("user:"):
        return "console"
    return "system"


def _identity_user() -> str | None:
    from core.governance.caller_identity import current_identity

    identity = current_identity()
    principal = str(getattr(identity, "principal", "") or "")
    if principal.startswith("user:"):
        return principal[len("user:") :] or None
    return None


def hints_from_context(
    *,
    record_agent_id: str | None,
    record_use_case: str,
    default_application: str,
    default_use_case: str,
    origin: str = "hook",
) -> Hints:
    """Attribution hints from the bound scope, the routing context and the caller's identity (server-owned)."""
    from core.spend.context import current_scope

    scope = current_scope()
    application = None
    if scope is not None and scope.application in vocab.APPLICATIONS:
        application = scope.application
    elif record_use_case in _AGENT_USE_CASES:
        application = "agents"
    elif default_application in vocab.APPLICATIONS:
        application = default_application
    else:
        application = _identity_application()
    return Hints(
        agent_id=(scope.agent_id if scope and scope.agent_id else None) or (record_agent_id or None),
        agent_version=scope.agent_version if scope else None,
        application=application_of(application),
        default_use_case=(default_use_case or (scope.default_use_case if scope else None) or "completion"),
        workflow_id=scope.workflow_id if scope else None,
        workflow_run_id=scope.workflow_run_id if scope else None,
        run_id=scope.run_id if scope else None,
        initiating_user_id=(scope.initiating_user_id if scope and scope.initiating_user_id else None)
        or _identity_user(),
        origin=origin,
    )


def pricing_provider(record: Any, decision: Any = None, details: Any = None) -> str:
    """The provider a call is priced with: the in-house endpoint, a local prefix, a local decision, else the
    record's provider as the catalogue spells it (``claude`` and ``gpt`` are ``anthropic`` and ``openai``)."""
    from core.governance.model_gateway import normalise_provider
    from core.governance.model_pricing import LOCAL_PROVIDERS

    serving = getattr(details, "serving_provider", None) if details is not None else None
    if serving:
        return str(serving)
    model = str(getattr(record, "model", "") or "").strip().lower()
    for local in LOCAL_PROVIDERS:
        if model.startswith(f"{local}:"):
            return local
    decided = normalise_provider(getattr(decision, "provider", None)) if decision is not None else None
    if decided in LOCAL_PROVIDERS:
        return str(decided)
    raw = str(getattr(record, "provider", "") or "")
    return vocab.label(normalise_provider(raw) or "") or "unknown"


def _scope_tenant() -> str | None:
    from core.spend.context import current_scope

    scope = current_scope()
    return scope.tenant_id if scope is not None else None


def _billing_account(provider: str, details: Any) -> str | None:
    from core.spend.context import account_for, current_credential

    explicit = getattr(details, "billing_account", None) if details is not None else None
    if explicit in vocab.BILLING_ACCOUNTS:
        return str(explicit)
    noted = account_for(provider, current_credential())
    if noted is not None:
        return noted
    if provider in vocab.IN_HOUSE_PROVIDERS:
        return "in_house"
    return None


def _quantities(record: Any, details: Any) -> list[tuple[str, int, bool]]:
    """``[(unit, quantity, estimated)]`` for one call (empty when it carries no tokens)."""
    inp = record.input_tokens
    out = record.output_tokens
    total = int(record.tokens or 0)
    if (inp is None) != (out is None) and total > 0:
        if inp is None:
            inp = max(0, total - int(out or 0))
        else:
            out = max(0, total - int(inp))
    extra = int(getattr(details, "extra_output_tokens", None) or 0) if details is not None else 0
    if inp is None and out is None:
        if total > 0:
            return [("token", total + extra, True)]
        return []
    inp_n, out_n = int(inp or 0), int(out or 0) + extra
    cached_raw = int(getattr(details, "cached_input_tokens", None) or 0) if details is not None else 0
    if details is not None and getattr(details, "cached_outside_input", False):
        uncached, cached = inp_n, cached_raw
    else:
        cached = min(cached_raw, inp_n)
        uncached = inp_n - cached
    out_units: list[tuple[str, int, bool]] = []
    if uncached > 0:
        out_units.append(("input_token", uncached, False))
    if cached > 0:
        out_units.append(("cached_input_token", cached, False))
    if out_n > 0:
        out_units.append(("output_token", out_n, False))
    return out_units


def plan_model_call(
    record: Any,
    *,
    decision: Any = None,
    details: Any = None,
    hints: Hints | None = None,
    provider: str | None = None,
    provider_raw: str | None = None,
    default_use_case: str = "",
) -> MeterPlan:
    """The events (or the gap) of one finished model call; pure apart from reading context variables."""
    tenant = record.tenant_id or (getattr(details, "tenant_id", None) if details is not None else None)
    tenant = tenant or _scope_tenant()
    if not tenant:
        return MeterPlan(tenant_id=None)
    tenant = tenant_text(tenant)
    spend_provider = provider or pricing_provider(record, decision, details)
    units = _quantities(record, details)
    gap: tuple[str, str] | None = None
    unmetered: str | None = None
    if not units:
        estimate = int(getattr(details, "estimated_input_tokens", None) or 0) if details is not None else 0
        if estimate > 0:
            units = [("input_token", estimate, True)]
            gap, unmetered = ("timeout_estimated", spend_provider), "timeout_estimated"
        else:
            return MeterPlan(
                tenant_id=tenant, gap=("failed_no_usage", spend_provider[:160]), unmetered="failed_no_usage"
            )
    digest = call_hash(
        record.tenant_id or tenant,
        record.correlation_id,
        record.created_at,
        provider_raw if provider_raw is not None else record.provider,
        record.model,
        record.outcome,
        record.tokens,
        record.input_tokens,
        record.output_tokens,
    )
    call_hints = hints or hints_from_context(
        record_agent_id=record.agent_id,
        record_use_case=str(record.use_case or ""),
        default_application="",
        default_use_case=default_use_case,
    )
    account = _billing_account(spend_provider, details)
    created = record.created_at if record.created_at.tzinfo else record.created_at.replace(tzinfo=UTC)
    events = [
        UsageEvent(
            tenant_id=tenant,
            usage_type=LLM,
            unit=unit,
            quantity=Decimal(quantity),
            provider=spend_provider,
            model=str(record.model or "").strip(),
            event_time=created,
            idempotency_key=f"llm:{digest}:{unit}",
            source_ref=digest,
            correlation_ref=correlation_ref(record.correlation_id),
            hints=call_hints,
            calls=1 if index == 0 else 0,
            quantity_estimated=estimated,
            billing_account=account,
        )
        for index, (unit, quantity, estimated) in enumerate(units)
    ]
    return MeterPlan(tenant_id=tenant, events=events, gap=gap, unmetered=unmetered)


def model_call_events(record: Any, *, decision: Any = None, details: Any = None) -> list[UsageEvent]:
    """The usage events of one finished model call (pure; empty without a tenant or tokens)."""
    return plan_model_call(record, decision=decision, details=details).events


def _count_failure(usage_type: str, reason: str, count: int = 1) -> None:
    from observability import metrics as m

    m.spend_usage_write_failures_total.labels(usage_type=usage_type, reason=reason).inc(count)


def submit_plan(plan: MeterPlan, *, usage_type: str = LLM, on: datetime | None = None) -> None:
    """Hand a plan to the writer: its events to the queue, its gap to the aggregator, its metrics."""
    from core.spend import clock, writer
    from observability import metrics as m

    if plan.tenant_id is None:
        _count_failure(usage_type, "no_tenant")
        return
    if plan.unmetered:
        m.spend_unmetered_calls_total.labels(usage_type=usage_type, reason=plan.unmetered).inc()
    if plan.gap is not None:
        moment = on or (plan.events[0].event_time if plan.events else clock.now_utc())
        writer.add_gap(plan.tenant_id, clock.event_date_of(moment), usage_type, plan.gap[0], plan.gap[1])
    if plan.events:
        writer.submit(plan.events)


def meter_model_call(record: Any, *, decision: Any = None, usage: Any = None) -> None:
    """The model-call hook: build the call's events and queue them. Synchronous; no I/O; never raises."""
    from observability import metrics as m

    started = time.perf_counter()
    try:
        from core.spend import tokens

        details = tokens.details_of(usage)
        plan = plan_model_call(record, decision=decision, details=details)
        submit_plan(plan, on=getattr(record, "created_at", None))
    # enterprise-gate: broad-except-ok reason=spend-hook-failure-is-logged-and-counted-the-call-proceeds
    except Exception as exc:
        logger.warning("spend_usage_hook_failed", error_type=type(exc).__name__)
        _count_failure(LLM, "hook_error")
    finally:
        m.spend_hook_seconds.labels(usage_type=LLM).observe(time.perf_counter() - started)


# ---------------------------------------------------------------- the write path


def _record_row(
    tenant_id: uuid.UUID,
    event: UsageEvent,
    model: str,
    resolved: Resolved,
    priced: Any,
    billing_account: str | None,
) -> dict[str, Any]:
    from core.spend import clock, ids

    return {
        "id": ids.time_uuid(),
        "tenant_id": tenant_id,
        "idempotency_key": event.idempotency_key[:160],
        "source_ref": event.source_ref[:64],
        "correlation_ref": event.correlation_ref[:32],
        "event_time": event.event_time,
        "event_date": clock.event_date_of(event.event_time),
        "billing_date": clock.billing_date_of(event.provider, event.event_time),
        "usage_type": event.usage_type,
        "unit": event.unit,
        "quantity": event.quantity.quantize(vocab.QTY_QUANT),
        "calls": 1 if event.calls else 0,
        "provider": event.provider[:64],
        "model": model[:128],
        "rate_card_id": priced.rate_card_id,
        "blend_card_id": priced.blend_card_id,
        "price_source": priced.price_source,
        "unit_price": priced.unit_price,
        "amount": priced.amount,
        "currency": priced.currency,
        "fx_rate": priced.fx_rate,
        "fx_rate_date": priced.fx_rate_date,
        "amount_inr": priced.amount_inr,
        "unpriced": priced.unpriced,
        "fx_estimated": priced.fx_estimated,
        "unconverted": priced.unconverted,
        "overage": False,
        "overage_quantity": Decimal("0"),
        "allocated": event.allocated,
        "quantity_estimated": event.quantity_estimated,
        "price_estimated": priced.price_estimated,
        "commitment_id": None,
        "agent_id": resolved.agent_id,
        "agent_version": resolved.agent_version,
        "org_node_id": resolved.org_node_id,
        "business_unit_node_id": resolved.business_unit_node_id,
        "attribution_path": resolved.attribution_path,
        "unattributed_reason": resolved.unattributed_reason,
        "product_line": resolved.product_line,
        "use_case": resolved.use_case,
        "application": resolved.application,
        "region": resolved.region,
        "workflow_id": resolved.workflow_id,
        "run_id": resolved.run_id,
        "initiating_user_id": resolved.initiating_user_id,
        "environment": resolved.environment,
        "risk_tier": resolved.risk_tier,
        "billing_account": billing_account,
        "allocated_from": event.allocated_from[:64] if event.allocated_from else None,
    }


async def _insert_records(session: Any, rows: Sequence[dict[str, Any]]) -> set[tuple[str, datetime]]:
    """Insert in chunks with ``ON CONFLICT DO NOTHING RETURNING``; the ``(key, event_time)`` of written rows."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from core.models.spend_usage import SpendUsageRecord

    table = SpendUsageRecord.__table__
    written: set[tuple[str, datetime]] = set()
    for start in range(0, len(rows), INSERT_CHUNK):
        chunk = list(rows[start : start + INSERT_CHUNK])
        statement = (
            pg_insert(table)
            .values(chunk)
            .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key", "event_time"])
            .returning(table.c.idempotency_key, table.c.event_time)
        )
        for row in (await session.execute(statement)).all():
            written.add((str(row[0]), row[1]))
    return written


async def upsert_gaps(session: Any, tenant_id: uuid.UUID, gaps: Mapping[tuple[date, str, str, str], int]) -> None:
    """Add gap counts: one additive upsert per ``(day, usage_type, reason, detail)``."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from core.models.spend_usage import SpendMeterGap

    if not gaps:
        return
    table = SpendMeterGap.__table__
    values = [
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "day": day,
            "usage_type": usage_type,
            "reason": reason,
            "detail": detail[:160],
            "count": int(count),
        }
        for (day, usage_type, reason, detail), count in sorted(gaps.items(), key=lambda item: str(item[0]))
        if count > 0
    ]
    if not values:
        return
    from sqlalchemy import func

    statement = pg_insert(table).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=["tenant_id", "day", "usage_type", "reason", "detail"],
        set_={"count": table.c.count + statement.excluded.count, "updated_at": func.now()},
    )
    await session.execute(statement)


async def mark_commitments_for_replay(
    session: Any, tenant_id: uuid.UUID, providers: Sequence[str], *, after: datetime | None = None, kind: str = ""
) -> None:
    """Set ``needs_full_recompute`` on the providers' active commitments (watermark past ``after`` when given)."""
    from sqlalchemy import update

    from core.models.spend import SpendCommitment as C

    if not providers:
        return
    conditions = [C.tenant_id == tenant_id, C.status == "active", C.provider.in_(sorted(set(providers)))]
    if after is not None:
        conditions.append(C.recomputed_through > after)
    if kind:
        conditions.append(C.kind == kind)
    await session.execute(update(C).where(*conditions).values(needs_full_recompute=True))


def _gap_key(day: date, usage_type: str, reason: str, detail: str) -> tuple[date, str, str, str]:
    return day, usage_type, reason, detail


async def write_events(
    session: Any,
    tenant_id: uuid.UUID,
    events: Sequence[UsageEvent],
    *,
    lock: str = "try",
    now: datetime | None = None,
    gaps: Mapping[tuple[date, str, str, str], int] | None = None,
) -> WriteResult:
    """Write a tenant's events and their rollup contributions in the caller's transaction.

    ``lock="try"`` (the writer) never waits: a rollup day held exclusively by
    a rebuild answers ``busy`` with nothing written. ``lock="wait"`` (Celery
    callers) waits up to 30 seconds.
    """
    from core.spend import billing, clock, locks, pricing, resolver, rollups
    from observability import metrics as m

    stamp = now or clock.now_utc()
    tid_text = str(tenant_id)
    own: list[UsageEvent] = []
    gap_counts: dict[tuple[date, str, str, str], int] = dict(gaps or {})
    refused = 0
    for event in events:
        if tenant_text(event.tenant_id) != tid_text:
            refused += 1
            _count_failure(event.usage_type, "tenant_mismatch")
            key = _gap_key(clock.event_date_of(event.event_time), event.usage_type, "tenant_mismatch", "")
            gap_counts[key] = gap_counts.get(key, 0) + 1
            continue
        own.append(event)
    if lock == "wait":
        await locks.set_lock_timeout(session, 30_000)
    for day in sorted({clock.event_date_of(e.event_time) for e in own}):
        key = locks.rollup_day(tenant_id, day)
        if lock == "try":
            if not await locks.try_xact_lock_shared(session, key):
                return WriteResult(written=0, duplicates=0, skipped=0, refused=refused, busy=True)
        else:
            await locks.xact_lock_shared(session, key)
    if not own:
        await upsert_gaps(session, tenant_id, gap_counts)
        return WriteResult(written=0, duplicates=0, skipped=0, refused=refused, busy=False)

    resolutions: dict[Hints, Resolved] = {}
    for event in own:
        if event.resolved is None and event.hints not in resolutions:
            resolutions[event.hints] = await resolver.resolve(session, tenant_id, event.hints)
    missing_accounts = {e.provider for e in own if e.billing_account is None}
    inferred = await billing.infer(session, tenant_id, missing_accounts) if missing_accounts else {}
    aliases = await pricing.cached_aliases(session, tenant_id)
    to_price = [e for e in own if e.priced is None]
    priced_list = await pricing.price_many(
        session,
        tenant_id,
        [
            pricing.Usage(
                provider=e.provider,
                usage_type=e.usage_type,
                unit=e.unit,
                quantity=e.quantity,
                model=e.model,
                on=clock.billing_date_of(e.provider, e.event_time),
                fx_on=clock.event_date_of(e.event_time),
                batch=e.batch,
            )
            for e in to_price
        ],
    )
    priced_by_key = {id(e): p for e, p in zip(to_price, priced_list, strict=True)}
    rows: list[dict[str, Any]] = []
    skipped = 0
    for event in own:
        priced = event.priced if event.priced is not None else priced_by_key[id(event)]
        if event.skip_if_unpriced and priced.unpriced:
            skipped += 1
            m.spend_usage_records_total.labels(usage_type=event.usage_type, outcome="skipped").inc()
            key = _gap_key(
                clock.event_date_of(event.event_time),
                event.usage_type,
                "unpriced_tool",
                f"{event.provider}:{event.model}"[:160],
            )
            gap_counts[key] = gap_counts.get(key, 0) + 1
            continue
        resolved = event.resolved or resolutions[event.hints]
        model = pricing.canonical_model(event.provider, event.model, aliases)
        account = event.billing_account or inferred.get(event.provider)
        rows.append(_record_row(tenant_id, event, model, resolved, priced, account))
    written_keys = await _insert_records(session, rows) if rows else set()
    written_rows = [r for r in rows if (r["idempotency_key"], r["event_time"]) in written_keys]
    if written_rows:
        await rollups.apply_deltas(session, tenant_id, rollups.aggregate(written_rows))
        late = [r for r in written_rows if r["event_time"] < stamp - APPEND_GRACE]
        if late:
            await mark_commitments_for_replay(
                session,
                tenant_id,
                sorted({r["provider"] for r in late}),
                after=min(r["event_time"] for r in late),
            )
    await upsert_gaps(session, tenant_id, gap_counts)
    duplicates = len(rows) - len(written_rows)
    for row in written_rows:
        m.spend_usage_records_total.labels(usage_type=row["usage_type"], outcome="written").inc()
    for row in rows:
        if (row["idempotency_key"], row["event_time"]) not in written_keys:
            m.spend_usage_records_total.labels(usage_type=row["usage_type"], outcome="duplicate").inc()
    return WriteResult(
        written=len(written_rows),
        duplicates=duplicates,
        skipped=skipped,
        refused=refused,
        busy=False,
        unpriced=sum(1 for r in written_rows if r["unpriced"]),
    )
