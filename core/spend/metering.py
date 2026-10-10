# SPDX-License-Identifier: Apache-2.0
"""Metering handlers behind ``core.spend.note``: usage from call sites outside ``record_model_call``.

Each site calls ``if spend.enabled(): spend.note(kind, tenant_id, **raw)``
and passes objects it already holds; the handler derives every quantity
here, inside ``note``'s guard, so a malformed object can never fail the call
site. Handlers read counts, lengths and ids only, never content.

* ``message``: a direct LangChain call (the explainer, the feedback analyser,
  the SOP parser). Provider from the model object (an in-house endpoint, else
  its class, Azure kept apart), tokens from the message's usage metadata.
* ``direct_response``: a direct provider SDK call (the workflow re-planner),
  tokens from the SDK response's usage.
* ``cancelled``: a router call cut off by its outer timeout before it could be
  recorded; counted as a gap, never estimated.
* ``embeddings``: chunks, rows or queries an in-house embedder embedded
  (knowledge ingestion, re-indexing, search, RPA ingestion). Tokens are
  estimated from characters (four per token) and capped at the model's input
  limit per item, because no embedding path returns token counts.
* ``ocr``: pages local Tesseract read (document processing, knowledge
  uploads, ingestion that extracted for itself).
* ``speech``: the audio minutes of a transcribed recording (local Whisper or
  Deepgram); a supplied transcript is not metered.
* ``tool_call``: a successful connector tool call, metered only when a rate
  card prices the tool (``tool_calls``, the connector as provider, the tool or
  ``''`` as model). A per-tenant set of priced tools, refreshed by the writer,
  lets an unpriced call be counted as a gap without being queued.

Direct calls get a fresh key per call (``provider_raw="direct"``) fixed when
the event is built, so a retry or a spill reuses it. Embeddings of one ingested
document, re-index batches, OCR of one stored document and a recording's
minutes have deterministic keys; searches, IDP analyses and tool calls get a
key fixed when the event is built.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Any

import structlog

from core.spend import vocab

logger = structlog.get_logger()

LLM = "llm_tokens"
EMBEDDINGS = "embedding_tokens"
OCR = "ocr_pages"
SPEECH = "speech_minutes"
TOOLS = "tool_calls"
CHARS_PER_TOKEN = 4  # core/rag/chunking.py:CHARS_PER_TOKEN
DEFAULT_EMBEDDING_MAX_TOKENS = 512  # the fastembed models' input limit
PRICED_TOOLS_TTL_SECONDS = 60.0
PRICED_TOOLS_MAX = 4096
# Engine -> (provider, model) of a metered transcription (core/speech/transcribe.py).
SPEECH_ENGINES = MappingProxyType({"faster_whisper": ("faster_whisper", "base"), "deepgram": ("deepgram", "nova-2")})
# Embedding purpose -> (default application, use case).
EMBEDDING_PURPOSES = MappingProxyType(
    {
        "ingest": ("knowledge", "knowledge.ingest"),
        "reindex": ("knowledge", "knowledge.reindex"),
        "search": ("knowledge", "knowledge.search"),
        "rpa": ("system", "rpa.ingest"),
    }
)
# OCR purpose -> (default application, use case).
OCR_PURPOSES = MappingProxyType(
    {
        "idp": ("documents", "documents.ocr"),
        "upload": ("knowledge", "knowledge.ocr"),
        "ingest": ("knowledge", "knowledge.ocr"),
    }
)

# enterprise-gate: process-local-ok reason=priced-tool-set-ttl-60s-refreshed-by-the-writer-keeps-no-cross-tenant-data
_PRICED_TOOLS_CACHE: dict[str, tuple[float, frozenset[tuple[str, str]]]] = {}


@dataclass(frozen=True)
class _DirectCall:
    """A model call nothing recorded, in the shape of a gateway record."""

    tenant_id: str | None
    correlation_id: str
    created_at: datetime
    provider: str
    model: str
    outcome: str
    tokens: int
    input_tokens: int | None
    output_tokens: int | None
    agent_id: str | None
    use_case: str


def _correlation_id() -> str:
    from core.governance.model_gateway import current_route, request_correlation_id

    route = current_route()
    decided = getattr(getattr(route, "decision", None), "correlation_id", None) if route is not None else None
    return str(decided or request_correlation_id() or uuid.uuid4().hex)


def _route_agent() -> str | None:
    from core.governance.model_gateway import current_route

    route = current_route()
    return getattr(route, "agent_id", None) if route is not None else None


def _tenant(tenant_id: object) -> str | None:
    from core.spend.context import current_scope

    if tenant_id:
        return str(tenant_id)
    scope = current_scope()
    return scope.tenant_id if scope is not None else None


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _direct_call(
    tenant_id: str | None, provider: str, model: str, input_tokens: int | None, output_tokens: int | None, total: int
) -> _DirectCall:
    from core.spend import clock

    return _DirectCall(
        tenant_id=tenant_id,
        correlation_id=_correlation_id(),
        created_at=clock.now_utc(),
        provider=provider,
        model=model,
        outcome="completed",
        tokens=total,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        agent_id=_route_agent(),
        use_case="",
    )


def _submit(call: _DirectCall, *, provider: str, details: Any, default_use_case: str) -> None:
    from core.spend import meter

    plan = meter.plan_model_call(
        call, details=details, provider=provider, provider_raw="direct", default_use_case=default_use_case
    )
    meter.submit_plan(plan, on=call.created_at)


def handle_message(tenant_id: object, raw: Mapping[str, Any]) -> None:
    """A LangChain AI message from a direct ``llm.ainvoke``."""
    from core.governance.model_gateway_records import message_tokens
    from core.spend import tokens

    llm = raw.get("llm")
    message = raw.get("message")
    model_object = tokens.unwrap_llm(llm)
    provider = tokens.serving_provider_of(llm) or vocab.CLASS_PROVIDERS.get(type(model_object).__name__, "")
    model = ""
    for attr in ("model", "model_name"):
        value = getattr(model_object, attr, None)
        if isinstance(value, str) and value:
            model = value
            break
    input_tokens, output_tokens, total = message_tokens(message)
    details = tokens.from_message(message, llm=llm)
    call = _direct_call(_tenant(tenant_id), provider or "unknown", model, input_tokens, output_tokens, total)
    _submit(
        call, provider=provider or "unknown", details=details, default_use_case=str(raw.get("default_use_case") or "")
    )


def _usage_counts(response: Any) -> tuple[int | None, int | None]:
    google = getattr(response, "usage_metadata", None)
    if google is not None:
        return (
            _int_or_none(getattr(google, "prompt_token_count", None)),
            _int_or_none(getattr(google, "candidates_token_count", None)),
        )
    usage = getattr(response, "usage", None)
    if usage is not None:
        return _int_or_none(getattr(usage, "prompt_tokens", None)), _int_or_none(
            getattr(usage, "completion_tokens", None)
        )
    return None, None


def handle_direct_response(tenant_id: object, raw: Mapping[str, Any]) -> None:
    """A provider SDK response (Google ``usage_metadata`` or OpenAI ``usage``) with a declared provider and model."""
    from core.spend.tokens import UsageDetails

    provider = vocab.label(raw.get("provider") or "") or "unknown"
    model = str(raw.get("model") or "")
    input_tokens, output_tokens = _usage_counts(raw.get("response"))
    total = (input_tokens or 0) + (output_tokens or 0)
    account = raw.get("billing_account")
    details = UsageDetails(billing_account=str(account)) if account in vocab.BILLING_ACCOUNTS else None
    call = _direct_call(_tenant(tenant_id), provider, model, input_tokens, output_tokens, total)
    _submit(call, provider=provider, details=details, default_use_case=str(raw.get("default_use_case") or ""))


def handle_cancelled(tenant_id: object, raw: Mapping[str, Any]) -> None:
    """A router call its outer timeout cancelled before it was recorded: counted, never estimated."""
    from core.governance.model_gateway import normalise_provider
    from core.spend import clock, writer
    from core.spend.meter import tenant_text
    from observability import metrics as m

    tenant = _tenant(tenant_id)
    m.spend_unmetered_calls_total.labels(usage_type=LLM, reason="cancelled").inc()
    if not tenant:
        return
    provider = vocab.label(normalise_provider(raw.get("provider")) or "") or "unknown"
    writer.add_gap(
        tenant_text(tenant), clock.event_date_of(clock.now_utc()), LLM, "failed_no_usage", f"cancelled:{provider}"
    )


# ---------------------------------------------------------------- non-token metering: shared


def _count(value: Any) -> int:
    """A non-negative count from an int-like value; anything else is 0."""
    if value is None or isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _correlation_ref() -> str:
    """The hashed id of the request or routed call this usage belongs to; ``""`` outside one."""
    from core.governance.model_gateway import current_route, request_correlation_id
    from core.spend.meter import correlation_ref

    route = current_route()
    decided = getattr(getattr(route, "decision", None), "correlation_id", None) if route is not None else None
    found = decided or request_correlation_id()
    return correlation_ref(str(found)) if found else ""


def _user_uuid(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value))) if value else None
    except ValueError:
        return None


def _event(
    tenant: str,
    *,
    usage_type: str,
    unit: str,
    quantity: Decimal,
    provider: str,
    model: str,
    key: str,
    source_ref: str,
    application: str,
    use_case: str,
    billing_account: str | None,
    calls: int = 1,
    estimated: bool = False,
    agent_id: str | None = None,
    user_id: Any = None,
    skip_if_unpriced: bool = False,
) -> Any:
    """One usage event stamped now, with hints from the bound scope and the caller's identity."""
    from core.spend import clock
    from core.spend.meter import UsageEvent, hints_from_context

    hints = hints_from_context(
        record_agent_id=agent_id or None,
        record_use_case="",
        default_application=application,
        default_use_case=use_case,
    )
    user = _user_uuid(user_id)
    if hints.initiating_user_id is None and user is not None:
        hints = replace(hints, initiating_user_id=user)
    return UsageEvent(
        tenant_id=tenant,
        usage_type=usage_type,
        unit=unit,
        quantity=quantity,
        provider=provider,
        model=model,
        event_time=clock.now_utc(),
        idempotency_key=key[:160],
        source_ref=source_ref[:64],
        correlation_ref=_correlation_ref(),
        hints=hints,
        calls=calls,
        quantity_estimated=estimated,
        billing_account=billing_account,
        skip_if_unpriced=skip_if_unpriced,
    )


def _queue(events: Sequence[Any]) -> None:
    from core.spend import writer

    writer.submit(events)


def _tenant_or_count(tenant_id: object, usage_type: str) -> str | None:
    """The tenant (the argument, else the bound scope) as canonical text; counted ``no_tenant`` when none."""
    from core.spend.meter import tenant_text

    tenant = _tenant(tenant_id)
    if not tenant:
        from observability import metrics as m

        m.spend_usage_write_failures_total.labels(usage_type=usage_type, reason="no_tenant").inc()
        return None
    return tenant_text(tenant)


# ---------------------------------------------------------------- embeddings


def embedding_tokens(char_counts: Sequence[int], *, max_tokens: int) -> int:
    """Estimated tokens of embedded items: ``ceil(chars / 4)`` each, capped at the model's input limit."""
    cap = max(1, int(max_tokens))
    return sum(min(math.ceil(max(0, int(chars)) / CHARS_PER_TOKEN), cap) for chars in char_counts)


def embedding_max_tokens(model: str) -> int:
    """The input limit of an in-house embedding model (the catalogue's ``max_input_tokens``; 512 when unknown)."""
    from core.ai_providers.catalog import EMBEDDING_CATALOG

    for entry in EMBEDDING_CATALOG:
        if entry.provider == "local" and entry.model.lower() == str(model or "").lower():
            return int(entry.max_input_tokens)
    return DEFAULT_EMBEDDING_MAX_TOKENS


def _field(item: Any, index: int) -> Any:
    """``item[index]`` of a tuple, a list or a database row; ``None`` for anything else (a string included)."""
    if isinstance(item, (str, bytes)):
        return None
    try:
        return item[index]
    except (TypeError, IndexError, KeyError):
        return None


def _chunk_chars(item: Any) -> int:
    text = _field(item, 0)
    return len(text) if isinstance(text, str) else 0


def _row_chars(item: Any) -> int:
    return len(str(_field(item, 1) or ""))


def _text_chars(item: Any) -> int:
    return len(item) if isinstance(item, str) else 0


# Embedding purpose -> how one item's characters are measured: ingest chunks are (text, span),
# re-index rows are (id, content, ...), searches and RPA items are the strings embedded.
_EMBEDDING_MEASURES: Mapping[str, Callable[[Any], int]] = MappingProxyType(
    {"ingest": _chunk_chars, "reindex": _row_chars, "search": _text_chars, "rpa": _text_chars}
)


def _embedding_key(purpose: str, raw: Mapping[str, Any]) -> tuple[str, str]:
    """``(idempotency key, source_ref)`` of an embedding event."""
    if purpose == "ingest":
        ref = str(raw.get("ref") or uuid.uuid4())
        return f"emb:ingest:{ref}", ref
    if purpose == "reindex":
        run_ref = str(raw.get("run_ref") or uuid.uuid4().hex)[:64]
        return f"emb:reindex:{run_ref}:{_count(raw.get('start'))}", run_ref
    if purpose == "rpa":
        script = str(raw.get("script_key") or "")[:64]
        return f"emb:rpa:{script}:{uuid.uuid4().hex}", script
    return f"emb:search:{uuid.uuid4().hex}", ""


def handle_embeddings(tenant_id: object, raw: Mapping[str, Any]) -> None:
    """Embedding tokens of an in-house embed call (nothing under the hermetic fake embedder)."""
    from core.embeddings import serving_identity

    purpose = str(raw.get("purpose") or "")
    spec = EMBEDDING_PURPOSES.get(purpose)
    if spec is None:
        logger.debug("spend_embeddings_unknown_purpose", purpose=purpose[:32])
        return
    identity = serving_identity()
    if identity is None:
        return
    provider, model = identity
    measure = _EMBEDDING_MEASURES[purpose]
    quantity = embedding_tokens(
        [measure(item) for item in raw.get("items") or ()], max_tokens=embedding_max_tokens(model)
    )
    if quantity <= 0:
        return
    tenant = _tenant_or_count(tenant_id, EMBEDDINGS)
    if tenant is None:
        return
    key, source_ref = _embedding_key(purpose, raw)
    application, use_case = spec
    _queue(
        [
            _event(
                tenant,
                usage_type=EMBEDDINGS,
                unit="embedding_token",
                quantity=Decimal(quantity),
                provider=provider,
                model=model,
                key=key,
                source_ref=source_ref,
                application=application,
                use_case=use_case,
                billing_account="in_house",
                estimated=True,
            )
        ],
    )


# ---------------------------------------------------------------- OCR pages


def ocr_page_count(extracted: Any) -> int:
    """Pages Tesseract read for an extraction: every frame of an image, the low-text pages of a PDF, else 0."""
    method = str(getattr(extracted, "extraction_method", "") or "")
    extra = getattr(extracted, "extra", None)
    extra = extra if isinstance(extra, Mapping) else {}
    if method == "tesseract-ocr":
        return _count(extra.get("page_count"))
    if "+ocr" in method:
        pages = extra.get("ocr_pages")
        return len(pages) if isinstance(pages, (list, tuple)) else 0
    return 0


def _idp_ocr_pages(result: Any) -> int:
    """Pages of an IDP result whose OCR ran (``ocr.pages`` lists the pages with ``ocr == "done"``)."""
    ocr = result.get("ocr") if isinstance(result, Mapping) else None
    pages = ocr.get("pages") if isinstance(ocr, Mapping) else None
    return len(pages) if isinstance(pages, (list, tuple)) else 0


def handle_ocr(tenant_id: object, raw: Mapping[str, Any]) -> None:
    """OCR pages local Tesseract read (zero-priced in-house unless a card exists)."""
    purpose = str(raw.get("purpose") or "")
    spec = OCR_PURPOSES.get(purpose)
    if spec is None:
        logger.debug("spend_ocr_unknown_purpose", purpose=purpose[:32])
        return
    pages = _idp_ocr_pages(raw.get("result")) if purpose == "idp" else ocr_page_count(raw.get("extracted"))
    if pages <= 0:
        return
    tenant = _tenant_or_count(tenant_id, OCR)
    if tenant is None:
        return
    if purpose == "idp":
        key, source_ref = f"ocr:idp:{uuid.uuid4().hex}", ""
    else:
        ref = str(raw.get("ref") or uuid.uuid4())
        key, source_ref = f"ocr:{purpose}:{ref}", ref
    application, use_case = spec
    _queue(
        [
            _event(
                tenant,
                usage_type=OCR,
                unit="ocr_page",
                quantity=Decimal(pages),
                provider="tesseract",
                model="",
                key=key,
                source_ref=source_ref,
                application=application,
                use_case=use_case,
                billing_account="in_house",
            )
        ],
    )


# ---------------------------------------------------------------- speech minutes


def speech_minutes(duration_seconds: Any) -> Decimal:
    """Audio minutes of a duration in seconds, to six places."""
    return (Decimal(str(duration_seconds)) / 60).quantize(vocab.QTY_QUANT)


def handle_speech(tenant_id: object, raw: Mapping[str, Any]) -> None:
    """The audio minutes of a recording a metered engine transcribed (``speech:{recording_id}``)."""
    from core.spend.context import account_for, current_credential

    row = raw.get("row")
    if getattr(row, "status", None) != "transcribed":
        return
    spec = SPEECH_ENGINES.get(str(raw.get("engine") or ""))
    if spec is None:
        return
    quantity = speech_minutes(getattr(row, "duration_seconds", 0) or 0)
    if quantity <= 0:
        return
    tenant = _tenant_or_count(tenant_id, SPEECH)
    if tenant is None:
        return
    provider, model = spec
    account = "in_house" if provider in vocab.IN_HOUSE_PROVIDERS else account_for(provider, current_credential())
    recording = str(getattr(row, "id", None) or uuid.uuid4())
    _queue(
        [
            _event(
                tenant,
                usage_type=SPEECH,
                unit="audio_minute",
                quantity=quantity,
                provider=provider,
                model=model,
                key=f"speech:{recording}",
                source_ref=recording,
                application="speech",
                use_case="speech.transcription",
                billing_account=account,
                user_id=raw.get("user_id"),
            )
        ],
    )


# ---------------------------------------------------------------- priced tool calls


def invalidate_priced_tools(tenant_id: uuid.UUID | str) -> None:
    """Drop the tenant's priced-tool set in this process (a rate-card write; other processes within 60 s)."""
    _PRICED_TOOLS_CACHE.pop(str(tenant_id), None)


def cached_priced_tools(tenant_id: uuid.UUID | str) -> frozenset[tuple[str, str]] | None:
    """The tenant's cached ``(provider, model_sku)`` pairs a tool card prices, or ``None`` when not fresh."""
    hit = _PRICED_TOOLS_CACHE.get(str(tenant_id))
    if hit is None or time.monotonic() - hit[0] >= PRICED_TOOLS_TTL_SECONDS:
        return None
    return hit[1]


async def priced_tool_set(session: Any, tenant_id: uuid.UUID, *, now: datetime) -> frozenset[tuple[str, str]]:
    """``(provider, model_sku)`` of the tenant's active tool cards in force around ``now``.

    A day either side covers every provider's billing zone, so the set never
    misses a priced tool; the writer still prices each call at its own date.
    """
    from sqlalchemy import or_, select

    from core.models.spend import SpendRateCard as C

    today = now.astimezone(UTC).date()
    start, end = today - timedelta(days=1), today + timedelta(days=1)
    rows = (
        await session.execute(
            select(C.provider, C.model_sku).where(
                C.tenant_id == tenant_id,
                C.usage_type == TOOLS,
                C.status == "active",
                C.effective_from <= end,
                or_(C.effective_to.is_(None), C.effective_to > start),
            )
        )
    ).all()
    return frozenset((str(row[0]), str(row[1] or "")) for row in rows)


async def refresh_priced_tools(session: Any, tenant_id: uuid.UUID, *, now: datetime) -> None:
    """Load the tenant's priced-tool set into this process's cache unless a fresh one is there (the writer)."""
    if cached_priced_tools(tenant_id) is not None:
        return
    found = await priced_tool_set(session, tenant_id, now=now)
    if len(_PRICED_TOOLS_CACHE) >= PRICED_TOOLS_MAX:
        _PRICED_TOOLS_CACHE.clear()
    _PRICED_TOOLS_CACHE[str(tenant_id)] = (time.monotonic(), found)


def handle_tool_call(tenant_id: object, raw: Mapping[str, Any]) -> None:
    """One successful connector tool call, queued only when a card may price it."""
    from core.langgraph.tool_adapter import _tool_outcome
    from core.spend import clock, writer

    if _tool_outcome(raw.get("result")) != "ok":
        return
    tenant = _tenant_or_count(tenant_id, TOOLS)
    if tenant is None:
        return
    provider = vocab.norm_provider(raw.get("connector"))
    sku = vocab.norm_sku(raw.get("tool"))
    priced = cached_priced_tools(tenant)
    if priced is not None and (provider, sku) not in priced and (provider, "") not in priced:
        writer.add_gap(tenant, clock.event_date_of(clock.now_utc()), TOOLS, "unpriced_tool", f"{provider}:{sku}")
        writer.start_for_gaps()
        return
    agent = raw.get("agent_id")
    _queue(
        [
            _event(
                tenant,
                usage_type=TOOLS,
                unit="call",
                quantity=Decimal(1),
                provider=provider,
                model=sku,
                key=f"tool:{uuid.uuid4().hex}",
                source_ref="",
                application="agents",
                use_case="tool.call",
                billing_account="tenant_key",
                agent_id=str(agent) if agent else None,
                skip_if_unpriced=True,
            )
        ],
    )


# ---------------------------------------------------------------- dispatch

_HANDLERS: Mapping[str, Callable[[object, Mapping[str, Any]], None]] = MappingProxyType(
    {
        "message": handle_message,
        "direct_response": handle_direct_response,
        "cancelled": handle_cancelled,
        "embeddings": handle_embeddings,
        "ocr": handle_ocr,
        "speech": handle_speech,
        "tool_call": handle_tool_call,
    }
)
# Note kind -> the usage type its hook time and failures are counted under.
KIND_USAGE_TYPES = MappingProxyType(
    {
        "message": LLM,
        "direct_response": LLM,
        "cancelled": LLM,
        "embeddings": EMBEDDINGS,
        "ocr": OCR,
        "speech": SPEECH,
        "tool_call": TOOLS,
    }
)


def handle(kind: str, tenant_id: object, raw: Mapping[str, Any]) -> None:
    """Dispatch ``spend.note``; an unknown kind is logged and ignored. The time it adds is observed."""
    handler = _HANDLERS.get(kind)
    if handler is None:
        logger.debug("spend_note_unknown_kind", kind=str(kind)[:32])
        return
    started = time.perf_counter()
    try:
        handler(tenant_id, raw)
    finally:
        from observability import metrics as m

        m.spend_hook_seconds.labels(usage_type=KIND_USAGE_TYPES[kind]).observe(time.perf_counter() - started)
