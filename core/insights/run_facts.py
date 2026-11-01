# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The run-fact writer: one ``run_facts`` row per agent run, written so it can never fail the run.

``record_run_fact(**fields)`` validates and normalises what a call site hands
it (unknown statuses and entry points are folded into the allowed sets,
strings are cut to their column sizes, a non-UUID agent id is kept as
``agent_ref``, confidence is clamped to 0..1 and negative numbers become 0)
and inserts the row in the tenant's own session with ``ON CONFLICT DO
NOTHING`` on ``ux_run_facts_tenant_run_key``, so writing the same fact twice
is a no-op. It never raises: a failure is logged as ``run_fact_write_failed``
with the error type only, and the call returns False, as it does while
Insights is off for the tenant (``core/insights/flags.py``), when nothing is
written at all.

Call sites:

- ``RunRecorder``: the LangGraph runner collects a run's outcome while it runs
  and records it once from its ``finally`` block;
- ``record_task_result_fact``: ``BaseAgent.execute`` (workflow steps and the
  sales pipeline) awaits the write of its result's fact before it returns the
  result unchanged;
- ``record_run_facts``: the demo seeder writes its ``seeded`` facts in one
  statement.

A fact holds ids, codes, counts and timings only: no message text, no error
message (``error_class`` is an exception class name or an error code) and no
subject identifier (no user id, e-mail or customer reference).
"""

from __future__ import annotations

import asyncio
import math
import sys
import time
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from core.config import settings
from core.insights.context import current_entrypoint, get_entrypoint
from core.insights.flags import insights_enabled_for

logger = structlog.get_logger()

# The allowed values (CHECK constraints of v6z106, core/models/insights.py).
STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "hitl_triggered", "limit_exceeded", "refused", "paused", "rejected"}
)
ENTRYPOINTS: frozenset[str] = frozenset(
    {
        "agents",
        "channel",
        "chat",
        "a2a",
        "mcp",
        "voice",
        "hitl_resume",
        "debug",
        "workflow",
        "sales",
        "customer_agent",
        "rpa",
    }
)
ENVIRONMENTS: frozenset[str] = frozenset({"production", "shadow", "sandbox"})
DATA_QUALITY: frozenset[str] = frozenset({"measured", "seeded"})

# Run results whose status names the refusal rather than an outcome.
_STATUS_ALIASES: dict[str, str] = {
    "model_gateway_refused": "refused",
    "operator_override": "refused",
    "guardrail_blocked": "refused",
    "success": "completed",
    "succeeded": "completed",
    "error": "failed",
}

# Column sizes of run_facts.
_STRING_LIMITS: dict[str, int] = {
    "run_key": 160,
    "agent_ref": 128,
    "agent_type": 100,
    "agent_version": 64,
    "domain": 50,
    "correlation_id": 160,
    "trace_id": 64,
    "step_id": 128,
    "task_type": 100,
    "use_case": 100,
    "provider": 32,
    "model": 128,
    "error_class": 100,
}
_TOKEN_FIELDS = ("tokens_in", "tokens_out", "tokens_cached", "tokens_total")
_INT_MAX = 2_147_483_647  # INTEGER columns
_COST_MAX = Decimal("99999999.999999")  # NUMERIC(14,6)
_COST_QUANTUM = Decimal("0.000001")

# A fact write waits at most this long, so a slow database cannot hold a run's response
# (the runner awaits it in its ``finally``, and ``BaseAgent.execute`` before returning).
WRITE_TIMEOUT_SECONDS = 2.0
# Background writes (``schedule_run_fact``, for synchronous callers) in flight at once; beyond it a fact is
# dropped and logged.
MAX_PENDING_WRITES = 500

# enterprise-gate: process-local-ok reason=strong-refs-to-in-flight-run-fact-writes-bounded
_PENDING: set[asyncio.Task[bool]] = set()


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def _code(value: Any) -> str:
    return str(value or "").strip().lower()


def _non_negative_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    if not math.isfinite(number) or number <= 0:
        return 0
    return min(int(number), _INT_MAX)


def _unit_interval(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return min(1.0, max(0.0, number))


def _cost(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        return Decimal("0")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")
    if not amount.is_finite() or amount <= 0:
        return Decimal("0")
    return min(amount, _COST_MAX).quantize(_COST_QUANTUM)


def _started_at(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            value = None
    if not isinstance(value, datetime):
        return datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def normalise_fact(fields: Mapping[str, Any]) -> dict[str, Any] | None:
    """The ``run_facts`` row for ``fields``, or ``None`` when it has no valid tenant id. Pure; never raises on values.

    ``status`` outside the allowed set becomes ``failed`` (a refusal status such
    as ``operator_override`` becomes ``refused``), keeping the given
    ``error_class`` or, without one, the original status as the error code.
    ``entrypoint``, ``environment`` and ``data_quality`` outside their sets
    become ``agents``, ``production`` and ``measured``. A ``hitl_resume``
    fact (an approval resuming a paused run) is always ``hitl_required``.
    """
    tenant_id = _uuid_or_none(fields.get("tenant_id"))
    if tenant_id is None:
        return None

    error_class = _text(fields.get("error_class"), _STRING_LIMITS["error_class"])
    raw_status = _code(fields.get("status"))
    status = _STATUS_ALIASES.get(raw_status, raw_status)
    if status not in STATUSES:
        status = "failed"
    if status != raw_status and raw_status and error_class is None:
        error_class = raw_status[: _STRING_LIMITS["error_class"]]

    entrypoint = _code(fields.get("entrypoint"))
    if entrypoint not in ENTRYPOINTS:
        entrypoint = "agents"
    environment = _code(fields.get("environment")) or "production"
    if environment not in ENVIRONMENTS:
        environment = "production"
    data_quality = _code(fields.get("data_quality")) or "measured"
    if data_quality not in DATA_QUALITY:
        data_quality = "measured"

    raw_agent = fields.get("agent_id")
    agent_id = _uuid_or_none(raw_agent)
    agent_ref = _text(fields.get("agent_ref"), _STRING_LIMITS["agent_ref"])
    if agent_id is None and agent_ref is None:
        agent_ref = _text(raw_agent, _STRING_LIMITS["agent_ref"])

    run_key = _text(fields.get("run_key"), _STRING_LIMITS["run_key"]) or f"{entrypoint}:{uuid.uuid4().hex}"

    tokens = {name: _non_negative_int(fields.get(name)) for name in _TOKEN_FIELDS}
    if not tokens["tokens_total"]:
        tokens["tokens_total"] = min(tokens["tokens_in"] + tokens["tokens_out"], _INT_MAX)
    duration_ms = _non_negative_int(fields["duration_ms"]) if fields.get("duration_ms") is not None else None

    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "company_id": _uuid_or_none(fields.get("company_id")),
        "run_key": run_key,
        "entrypoint": entrypoint,
        "agent_id": agent_id,
        "agent_ref": agent_ref,
        "workflow_run_id": _uuid_or_none(fields.get("workflow_run_id")),
        "environment": environment,
        "status": status,
        "confidence": _unit_interval(fields.get("confidence")),
        "confidence_floor": _unit_interval(fields.get("confidence_floor")),
        "duration_ms": duration_ms,
        # A resume exists only because a person decided, so it always involved one.
        "hitl_required": bool(fields.get("hitl_required")) or status == "hitl_triggered" or entrypoint == "hitl_resume",
        "tokens_in": tokens["tokens_in"],
        "tokens_out": tokens["tokens_out"],
        "tokens_cached": tokens["tokens_cached"],
        "tokens_total": tokens["tokens_total"],
        "cost_usd": _cost(fields.get("cost_usd")),
        "error_class": error_class,
        "outcome_validated": bool(fields.get("outcome_validated")),
        "data_quality": data_quality,
        "started_at": _started_at(fields.get("started_at")),
    }
    for name in ("agent_type", "agent_version", "domain", "correlation_id", "trace_id", "step_id"):
        row[name] = _text(fields.get(name), _STRING_LIMITS[name])
    for name in ("task_type", "use_case", "provider", "model"):
        row[name] = _text(fields.get(name), _STRING_LIMITS[name])
    return row


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def _insert(tenant_id: uuid.UUID, rows: list[dict[str, Any]]) -> None:
    from sqlalchemy.dialects.postgresql import insert

    from core.database import get_tenant_session
    from core.models.insights import RunFact

    statement = (
        insert(RunFact)
        .values(rows)
        .on_conflict_do_nothing(index_elements=[RunFact.tenant_id, RunFact.run_key, RunFact.started_at])
    )
    async with get_tenant_session(tenant_id) as session:
        await session.execute(statement)


async def _write(rows: list[dict[str, Any]]) -> bool:
    tenant_id = rows[0]["tenant_id"]
    if not await insights_enabled_for(tenant_id):
        return False
    await _insert(tenant_id, rows)
    return True


async def record_run_fact(**fields: Any) -> bool:
    """Write one run fact (idempotent on its run key and start). Never raises.

    True when the row was written (or was already there); False while Insights
    is off for the tenant, for a fact without a valid tenant id, or on error.
    """
    try:
        row = normalise_fact(fields)
        if row is None:
            return False
        return await asyncio.wait_for(_write([row]), timeout=WRITE_TIMEOUT_SECONDS)
    # enterprise-gate: broad-except-ok reason=run-fact-write-never-fails-the-run
    except Exception as exc:
        logger.warning("run_fact_write_failed", error_type=type(exc).__name__)
        return False


async def record_run_facts(tenant_id: Any, facts: Iterable[Mapping[str, Any]]) -> int:
    """Write many facts of one tenant in one statement; the number written (0 when off or on error). Never raises."""
    try:
        tid = _uuid_or_none(tenant_id)
        if tid is None:
            return 0
        rows = [row for row in (normalise_fact({**fact, "tenant_id": tid}) for fact in facts) if row is not None]
        if not rows:
            return 0
        return len(rows) if await _write(rows) else 0
    # enterprise-gate: broad-except-ok reason=run-fact-write-never-fails-the-caller
    except Exception as exc:
        logger.warning("run_fact_write_failed", error_type=type(exc).__name__)
        return 0


def schedule_run_fact(**fields: Any) -> None:
    """Write a fact from synchronous code on the running loop, in the background. Never raises; a no-op while off."""
    if not settings.insights_enabled:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if len(_PENDING) >= MAX_PENDING_WRITES:
        logger.warning("run_fact_write_failed", error_type="backlog_full")
        return
    try:
        task = loop.create_task(record_run_fact(**fields))
        _PENDING.add(task)
        task.add_done_callback(_PENDING.discard)
    # enterprise-gate: broad-except-ok reason=run-fact-scheduling-never-fails-the-caller
    except Exception as exc:
        logger.warning("run_fact_write_failed", error_type=type(exc).__name__)


# ---------------------------------------------------------------------------
# Token split
# ---------------------------------------------------------------------------


def _count(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return number if number > 0 else 0


def usage_split(messages: Any) -> tuple[int, int, int]:
    """``(input, output, cached input)`` tokens summed over a run's AI messages, from their usage metadata.

    Reads the LangChain ``usage_metadata`` (input/output counts, cache reads)
    and, without it, the Gemini or OpenAI counts in ``response_metadata``.
    Counts only, never content.
    """
    from core.spend.tokens import from_message

    tokens_in = tokens_out = cached = 0
    for message in messages or []:
        if getattr(message, "type", "") != "ai":
            continue
        usage = getattr(message, "usage_metadata", None)
        if isinstance(usage, Mapping) and usage:
            tokens_in += _count(usage.get("input_tokens"))
            tokens_out += _count(usage.get("output_tokens"))
        else:
            meta = getattr(message, "response_metadata", None)
            meta = meta if isinstance(meta, Mapping) else {}
            counts = meta.get("usage_metadata") or meta.get("token_usage") or {}
            if isinstance(counts, Mapping):
                tokens_in += _count(counts.get("prompt_token_count") or counts.get("prompt_tokens"))
                tokens_out += _count(counts.get("candidates_token_count") or counts.get("completion_tokens"))
        details = from_message(message)
        if details is not None:
            cached += _count(details.cached_input_tokens)
    return tokens_in, tokens_out, cached


# ---------------------------------------------------------------------------
# The runner's recorder
# ---------------------------------------------------------------------------


def _performance(result: Mapping[str, Any]) -> Mapping[str, Any]:
    performance = result.get("performance")
    return performance if isinstance(performance, Mapping) else {}


def _result_error_class(result: Mapping[str, Any]) -> str | None:
    for key in ("error_code", "reason"):
        if result.get(key):
            return str(result[key])
    limit = result.get("limit")
    if isinstance(limit, Mapping) and limit.get("reason"):
        return str(limit["reason"])
    return None


def _span_trace_id(span: Any) -> str | None:
    context = span.span.get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


class RunRecorder:
    """Collects one run's outcome while it runs and records it once as a run fact.

    Nothing it does can fail or change the run: ``capture`` stores and returns
    the result unchanged, and ``finish`` swallows every error. A run that ends
    with no captured result (an exception escaping the runner, a cancellation)
    is recorded as ``failed`` with the exception's class name.

    ``record_usage=False`` records tokens and cost as 0: a resumed run reports
    the whole thread's usage, already counted by the run that paused.
    """

    def __init__(
        self,
        *,
        tenant_id: Any,
        agent_id: Any,
        entrypoint: str,
        agent_type: str | None = None,
        domain: str | None = None,
        company_id: Any = None,
        confidence_floor: Any = None,
        provider: str | None = None,
        model: str | None = None,
        task_input: Any = None,
        thread_id: str | None = None,
        use_case: str | None = None,
        record_usage: bool = True,
    ) -> None:
        self.started_at = datetime.now(UTC)
        self._t0 = time.perf_counter()
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self.entrypoint = entrypoint
        self.agent_type = agent_type
        self.domain = domain
        self.company_id = company_id
        self.confidence_floor = confidence_floor
        self.provider = provider
        self.model = model
        self.task_input = task_input
        self.thread_id = thread_id
        self.use_case = use_case
        self.record_usage = record_usage
        self.correlation_id: str | None = None
        self.result: Mapping[str, Any] | None = None
        self.error_class: str | None = None
        self.tokens: tuple[int, int, int] | None = None
        self._recorded = False

    def routed(self, route: Any, *, provider: Any = None, model: Any = None, thread_id: Any = None) -> None:
        """Note the routing decision's correlation id, the provider and model the run uses, and its thread."""
        try:
            self.correlation_id = str(getattr(route, "correlation_id", "") or "") or None
            self.provider = provider or self.provider
            self.model = model or self.model
            self.thread_id = thread_id or self.thread_id
        # enterprise-gate: broad-except-ok reason=run-fact-bookkeeping-never-fails-the-run
        except Exception:
            logger.debug("run_fact_route_unreadable")

    def capture(self, result: Any, *, error_class: str | None = None) -> Any:
        """Keep ``result`` as the run's outcome and hand it back unchanged."""
        self.result = result if isinstance(result, Mapping) else None
        self.error_class = error_class
        return result

    def usage(self, messages: Any) -> None:
        """Split the run's tokens into input, output and cached from its messages' usage metadata."""
        try:
            self.tokens = usage_split(messages)
        # enterprise-gate: broad-except-ok reason=run-fact-bookkeeping-never-fails-the-run
        except Exception:
            self.tokens = None

    def _task_type(self) -> str | None:
        task = self.task_input
        if not isinstance(task, Mapping):
            return None
        return str(task.get("action") or "") or None

    def _environment(self) -> str:
        task = self.task_input
        return "shadow" if isinstance(task, Mapping) and task.get("action") == "shadow_sample" else "production"

    def _run_key(self) -> str:
        from core.governance.model_gateway import request_correlation_id

        anchor = self.correlation_id or self.thread_id or request_correlation_id() or uuid.uuid4().hex
        return f"{self.entrypoint}:{anchor}"

    def fields(self, span: Any = None) -> dict[str, Any]:
        """The fact's fields from what was captured (the writer normalises them)."""
        result: Mapping[str, Any] = self.result or {}
        error_class = self.error_class
        if self.result is None:
            escaping = sys.exc_info()[1]
            error_class = error_class or (type(escaping).__name__ if escaping is not None else "no_result")
            status = "failed"
        else:
            status = str(result.get("status") or "completed")
            error_class = error_class or _result_error_class(result)
        performance = _performance(result)
        duration = _non_negative_int(performance.get("total_latency_ms")) or int(
            (time.perf_counter() - self._t0) * 1000
        )
        fields: dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "run_key": self._run_key(),
            "entrypoint": self.entrypoint,
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "domain": self.domain,
            "company_id": self.company_id,
            "correlation_id": self.correlation_id,
            "trace_id": _span_trace_id(span) if span is not None else None,
            "task_type": self._task_type(),
            "use_case": self.use_case,
            "environment": self._environment(),
            "status": status,
            "confidence": result.get("confidence"),
            "confidence_floor": self.confidence_floor,
            "duration_ms": duration,
            "error_class": error_class,
            "provider": self.provider,
            "model": self.model,
            "started_at": self.started_at,
        }
        if self.record_usage:
            fields["tokens_total"] = performance.get("llm_tokens_used")
            fields["cost_usd"] = performance.get("llm_cost_usd")
            if self.tokens is not None:
                fields["tokens_in"], fields["tokens_out"], fields["tokens_cached"] = self.tokens
        return fields

    async def fail(self, exc: BaseException) -> None:
        """Record the run as ``failed`` with ``exc``'s class name, unless it was already recorded. Never raises."""
        if not self._recorded and self.result is None:
            self.error_class = type(exc).__name__
        await self.finish()

    async def finish(self, result: Any = None, *, error_class: str | None = None, span: Any = None) -> Any:
        """Record the run once (``result`` when given, else what was captured) and return ``result``. Never raises."""
        if result is not None:
            self.capture(result, error_class=error_class)
        try:
            if not self._recorded:
                self._recorded = True
                await record_run_fact(**self.fields(span))
        # enterprise-gate: broad-except-ok reason=run-fact-write-never-fails-the-run
        except Exception as exc:
            logger.warning("run_fact_write_failed", error_type=type(exc).__name__)
        return result


# ---------------------------------------------------------------------------
# BaseAgent results (workflow steps, the sales pipeline)
# ---------------------------------------------------------------------------


def _error_code(error: Any) -> str | None:
    if isinstance(error, Mapping) and error.get("code"):
        return str(error["code"])
    return None


def task_result_fields(
    agent: Any, task: Any, result: Any, *, latency_ms: int = 0, error_class: str | None = None
) -> dict[str, Any]:
    """The fact fields of a ``BaseAgent`` result (``result`` is ``None`` when ``execute`` raised). Reads only.

    The entry point is ``sales`` inside the sales pipeline (which also sets a
    synthetic ``workflow_run_id``), else ``workflow`` for a result with a
    workflow run, else the bound entry point, else ``agents``. Tokens are 0:
    the base agent does not accumulate them.
    """
    workflow_run_id = str(getattr(result, "workflow_run_id", "") or getattr(task, "workflow_run_id", "") or "")
    bound = current_entrypoint.get()
    entrypoint = bound if bound == "sales" else ("workflow" if workflow_run_id else get_entrypoint())
    status = "failed" if result is None else str(getattr(result, "status", "") or "failed")
    latency = _non_negative_int(latency_ms)
    message_id = str(getattr(result, "message_id", "") or uuid.uuid4().hex)
    task_input = getattr(task, "task", None)
    return {
        "tenant_id": getattr(agent, "tenant_id", None),
        "company_id": getattr(agent, "company_id", None),
        "run_key": f"{entrypoint}:{message_id}",
        "entrypoint": entrypoint,
        "agent_id": getattr(result, "agent_id", None) or getattr(agent, "agent_id", None),
        "agent_type": getattr(agent, "agent_type", None),
        "domain": getattr(agent, "domain", None),
        "correlation_id": getattr(result, "correlation_id", None) or getattr(task, "correlation_id", None),
        "workflow_run_id": workflow_run_id or None,
        "step_id": getattr(result, "step_id", None) or getattr(task, "step_id", None),
        "task_type": getattr(task_input, "action", None),
        "status": status,
        "confidence": getattr(result, "confidence", None),
        "confidence_floor": getattr(agent, "confidence_floor", None),
        "duration_ms": latency,
        "hitl_required": status == "hitl_triggered",
        "error_class": error_class or _error_code(getattr(result, "error", None)),
        "model": getattr(agent, "llm_model", None),
        "started_at": datetime.now(UTC) - timedelta(milliseconds=latency),
    }


def _result_latency(result: Any) -> int | None:
    performance = getattr(result, "performance", None)
    latency = getattr(performance, "total_latency_ms", None)
    return _non_negative_int(latency) if latency else None


async def record_task_result_fact(
    agent: Any, task: Any, result: Any, *, latency_ms: int = 0, error_class: str | None = None
) -> None:
    """Record a ``BaseAgent`` result's fact, awaited. Never raises; never reads or changes ``result``'s value.

    The result's own ``total_latency_ms`` is the duration when it has one,
    else ``latency_ms`` (what the caller measured). ``result`` is ``None``
    for an ``execute`` that raised: the fact is ``failed`` with
    ``error_class``.
    """
    if not settings.insights_enabled:
        return
    try:
        latency = _result_latency(result) or latency_ms
        await record_run_fact(**task_result_fields(agent, task, result, latency_ms=latency, error_class=error_class))
    # enterprise-gate: broad-except-ok reason=run-fact-write-never-fails-the-agent-result
    except Exception as exc:
        logger.warning("run_fact_write_failed", error_type=type(exc).__name__)
