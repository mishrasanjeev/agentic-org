# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Runtime counters for Insights: tool calls, guardrail outcomes and security events (migration v6z115).

Three writers, each of which can never fail or slow the call it records:

- ``schedule_tool_call``: the tool gateway hands over each call's outcome when
  it returns and one ``tool_calls`` row is written in the background, in the
  tenant's own session. The row holds the tool and connector names, the status
  (``success``, ``failed``, ``refused`` or ``replayed``), the error code, the
  latency, the attempt, a digest of the idempotency key and, when tracing knows
  it, the run. Never the parameters or the result.
- ``count_guardrail_event``: the guardrail engine counts each outcome it
  produces (not dry runs) by stage, detector, action and mode.
- ``count_security_event``: authentication and route enforcement count failed
  sign-ins, rate-limit and scope denials, session revocations, API-key use and
  webhook signature failures for the tenant they concern. An event with no
  known tenant (a sign-in for an unknown address, an invalid grant token) is
  not counted: nothing ties it to a tenant.

The two counters add up in process memory and are written every
``FLUSH_SECONDS`` as one upsert per tenant into ``guardrail_events`` and
``security_events_daily`` (UTC days), so a burst of denials costs one statement
per tenant rather than one per request. A process that stops loses at most the
last few seconds of counts; these are analytics, never a control.

Everything is off while Insights is off for the tenant (``core/insights/flags.py``).
A failure is logged with the error type only and dropped.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import structlog

from core.config import settings
from core.insights.flags import insights_enabled_for
from core.models.insights import SECURITY_EVENT_KINDS

logger = structlog.get_logger()

WRITE_TIMEOUT_SECONDS = 5.0
FLUSH_SECONDS = 5.0
MAX_PENDING_WRITES = 500
MAX_BUFFERED_KEYS = 10_000

TOOL_STATUSES = ("success", "failed", "refused", "replayed")
# Gateway error codes for a call the gateway refused before dispatch (policy, scope, limits, grants,
# idempotency conflicts, guardrails); every other code is a failure of the call itself.
REFUSAL_CODES = frozenset({"E1003", "E1007", "E1008", "E1009", "E1011", "E1016"})

# enterprise-gate: process-local-ok reason=strong-refs-to-in-flight-tool-call-writes-bounded
_PENDING: set[asyncio.Task[Any]] = set()
# enterprise-gate: process-local-ok reason=analytics-counts-buffered-seconds-before-the-upsert-never-a-control
_COUNTS: dict[tuple[Any, ...], int] = defaultdict(int)
_FLUSHER: asyncio.Task[Any] | None = None


def _uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value or "").strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _cut(value: Any, size: int) -> str:
    return str(value or "").strip()[:size]


# ------------------------------------------------------------------ tool calls


def tool_call_status(result: Any, *, replayed: bool = False) -> tuple[str, str | None]:
    """The status and error code of a gateway result: ``{"error": {"code": ...}}`` is a refusal or a failure."""
    if replayed:
        return "replayed", None
    error = result.get("error") if isinstance(result, dict) else None
    if not error:
        return "success", None
    code = _cut(error.get("code") if isinstance(error, dict) else error, 10) or None
    return ("refused" if code in REFUSAL_CODES else "failed"), code


def _run_id() -> uuid.UUID | None:
    try:
        from observability import tracing

        return _uuid(tracing.current_run_scope())
    # enterprise-gate: broad-except-ok reason=a-missing-run-scope-records-the-call-without-its-run
    except Exception:
        return None


def tool_call_row(
    *,
    tenant_id: Any,
    agent_id: Any,
    tool_name: Any,
    connector_name: Any,
    status: str,
    error_code: str | None,
    latency_ms: Any,
    idempotency_key: Any = None,
    attempt: Any = 1,
    run_id: Any = None,
) -> dict[str, Any] | None:
    """The ``tool_calls`` row for a call, or None without a valid tenant. Strings are cut to their columns."""
    tid = _uuid(tenant_id)
    if tid is None:
        return None
    key = str(idempotency_key or "")
    try:
        latency = max(0, int(latency_ms))
    except (TypeError, ValueError):
        latency = None
    try:
        tries = min(max(1, int(attempt)), 32767)
    except (TypeError, ValueError):
        tries = 1
    return {
        "id": uuid.uuid4(),
        "tenant_id": tid,
        "agent_id": _uuid(agent_id),
        "tool_name": _cut(tool_name, 100) or "unknown",
        "connector_name": _cut(connector_name, 100) or None,
        "status": status if status in TOOL_STATUSES else "failed",
        "error_code": _cut(error_code, 10) or None,
        "latency_ms": latency,
        # A digest: the key itself may carry a business reference.
        "idempotency_key": hashlib.sha256(key.encode()).hexdigest() if key else None,
        "attempt": tries,
        "replayed": status == "replayed",
        "run_id": _uuid(run_id),
    }


async def _insert_tool_call(row: dict[str, Any]) -> None:
    from sqlalchemy import insert
    from sqlalchemy.exc import IntegrityError

    from core.database import get_tenant_session
    from core.models.tool_call import ToolCall

    try:
        async with get_tenant_session(row["tenant_id"]) as session:
            await session.execute(insert(ToolCall).values(**row))
    except IntegrityError:
        if row["agent_id"] is None:
            raise
        # The agent id is not a stored agent (a customer or system agent): keep the call without it.
        async with get_tenant_session(row["tenant_id"]) as session:
            await session.execute(insert(ToolCall).values(**{**row, "agent_id": None}))


async def record_tool_call(**fields: Any) -> bool:
    """Write one tool call. Never raises; False while Insights is off for the tenant, without a tenant, or on error."""
    try:
        row = tool_call_row(**fields)
        if row is None or not await insights_enabled_for(row["tenant_id"]):
            return False
        await asyncio.wait_for(_insert_tool_call(row), timeout=WRITE_TIMEOUT_SECONDS)
        return True
    # enterprise-gate: broad-except-ok reason=tool-call-record-never-fails-the-tool-call
    except Exception as exc:
        logger.warning("tool_call_write_failed", error_type=type(exc).__name__)
        return False


def schedule_tool_call(
    *,
    tenant_id: Any,
    agent_id: Any,
    tool_name: Any,
    connector_name: Any,
    result: Any,
    started: float,
    idempotency_key: Any = None,
    replayed: bool = False,
) -> None:
    """Record a gateway call in the background. Never raises; a no-op while Insights is off."""
    if not settings.insights_enabled:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if len(_PENDING) >= MAX_PENDING_WRITES:
        logger.warning("tool_call_write_failed", error_type="backlog_full")
        return
    try:
        status, code = tool_call_status(result, replayed=replayed)
        task = loop.create_task(
            record_tool_call(
                tenant_id=tenant_id,
                agent_id=agent_id,
                tool_name=tool_name,
                connector_name=connector_name,
                status=status,
                error_code=code,
                latency_ms=(time.monotonic() - started) * 1000,
                idempotency_key=idempotency_key,
                run_id=_run_id(),
            )
        )
    # enterprise-gate: broad-except-ok reason=tool-call-record-never-fails-the-tool-call
    except Exception as exc:
        logger.warning("tool_call_write_failed", error_type=type(exc).__name__)
        return
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


# ------------------------------------------------------------------ counters


def _today() -> Any:
    return datetime.now(UTC).date()


def _add(key: tuple[Any, ...]) -> None:
    if key not in _COUNTS and len(_COUNTS) >= MAX_BUFFERED_KEYS:
        logger.warning("insights_counter_dropped", error_type="buffer_full")
        return
    _COUNTS[key] += 1
    _ensure_flusher()


def _ensure_flusher() -> None:
    global _FLUSHER
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    current = _FLUSHER
    # A flusher from a loop that has since closed (a finished worker task) never runs: start one here.
    if current is not None and not current.done() and current.get_loop() is loop:
        return
    _FLUSHER = loop.create_task(_flush_later())


async def _flush_later() -> None:
    await asyncio.sleep(FLUSH_SECONDS)
    await flush()


def count_security_event(tenant_id: Any, kind: str) -> None:
    """Count one security event for the tenant. Never raises; a no-op while Insights is off or without a tenant."""
    try:
        if not settings.insights_enabled or kind not in SECURITY_EVENT_KINDS:
            return
        tid = _uuid(tenant_id)
        if tid is None:
            return
        _add(("security", tid, _today(), kind))
    # enterprise-gate: broad-except-ok reason=security-counter-failure-is-logged-and-never-changes-the-request
    except Exception as exc:
        logger.warning("insights_counter_dropped", error_type=type(exc).__name__)


def count_guardrail_event(tenant_id: Any, stage: str, detector: str, action: str, mode: str) -> None:
    """Count one guardrail outcome. Never raises; a no-op while Insights is off or without a tenant."""
    try:
        if not settings.insights_enabled:
            return
        tid = _uuid(tenant_id)
        if tid is None:
            return
        _add(("guardrail", tid, _today(), _cut(stage, 16), _cut(detector, 64), _cut(action, 16), _cut(mode, 16)))
    # enterprise-gate: broad-except-ok reason=guardrail-counter-failure-is-logged-and-never-changes-the-outcome
    except Exception as exc:
        logger.warning("insights_counter_dropped", error_type=type(exc).__name__)


def _rows(counts: dict[tuple[Any, ...], int]) -> dict[uuid.UUID, dict[str, list[dict[str, Any]]]]:
    by_tenant: dict[uuid.UUID, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"security": [], "guardrail": []})
    for key, n in counts.items():
        kind, tid, day = key[0], key[1], key[2]
        if kind == "security":
            by_tenant[tid]["security"].append(
                {"id": uuid.uuid4(), "tenant_id": tid, "day": day, "event_kind": key[3], "count": n}
            )
        else:
            stage, detector, action, mode = key[3:]
            by_tenant[tid]["guardrail"].append(
                {"id": uuid.uuid4(), "tenant_id": tid, "day": day, "stage": stage, "detector": detector,
                 "action": action, "mode": mode, "count": n}
            )
    return by_tenant


async def _upsert(tid: uuid.UUID, rows: dict[str, list[dict[str, Any]]]) -> None:
    from sqlalchemy import func
    from sqlalchemy.dialects.postgresql import insert

    from core.database import get_tenant_session
    from core.models.insights import GuardrailEvent, SecurityEventDaily

    async with get_tenant_session(tid) as session:
        for model, constraint, key in (
            (SecurityEventDaily, "uq_security_events_daily_day", "security"),
            (GuardrailEvent, "uq_guardrail_events_day", "guardrail"),
        ):
            if not rows[key]:
                continue
            statement = insert(model).values(rows[key])
            await session.execute(
                statement.on_conflict_do_update(
                    constraint=constraint,
                    set_={"count": model.count + statement.excluded.count, "updated_at": func.now()},
                )
            )


async def flush() -> int:
    """Write the buffered counts (one upsert per tenant); the number of counter rows written. Never raises."""
    if not _COUNTS:
        return 0
    counts = dict(_COUNTS)
    _COUNTS.clear()
    written = 0
    for tid, rows in _rows(counts).items():
        try:
            if not await insights_enabled_for(tid):
                continue
            await asyncio.wait_for(_upsert(tid, rows), timeout=WRITE_TIMEOUT_SECONDS)
            written += len(rows["security"]) + len(rows["guardrail"])
        # enterprise-gate: broad-except-ok reason=a-counter-flush-failure-drops-that-tenants-counts-and-is-logged
        except Exception as exc:
            logger.warning("insights_counter_flush_failed", error_type=type(exc).__name__)
    return written
