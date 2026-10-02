# SPDX-License-Identifier: Apache-2.0
"""Model-level metrics and routing records for the model gateway.

Every model call the platform makes, on the agent path (the graph's reasoning
node) or the direct router, is recorded once it ends: provider and model,
outcome, latency, tokens, cost, the error type when it failed, the model it
fell back from when failover took it, and how long admission under the
per-model limits took. Each record is metered by provider and model
(``agenticorg_model_call_*``), and while the gateway is on for the tenant the
record is also written, signed, to ``model_gateway_records`` with the routing
decision that produced it: the correlation id, the use case, the agent, the
routing and access policies evaluated, what was requested and what was chosen.

The runner binds the run's routing decision for the span of the run
(``core.governance.model_gateway.bind_route``), so the reasoning node can
attribute its calls without the decision being threaded through the graph. A
call outside a routed run (no tenant, gateway off) is metered but writes no
record.

Time to first token needs streaming, which the platform's model calls do not
use yet; latency, tokens per second and admission wait are recorded instead.
Recording is best effort: a failure to meter or to write the row is logged and
never changes the call's outcome.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from core.config import settings
from core.governance.model_gateway import current_route

logger = structlog.get_logger()

OUTCOMES: tuple[str, ...] = ("completed", "failed")
SIGNED_FIELDS: tuple[str, ...] = (
    "tenant_id",
    "correlation_id",
    "use_case",
    "agent_id",
    "policy_id",
    "access_policy_id",
    "requested_provider",
    "requested_model",
    "provider",
    "model",
    "fallback_from",
    "restricted",
    "outcome",
    "error_type",
    "latency_ms",
    "admission_wait_ms",
    "tokens",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "created_at",
)


def _usage_value(usage: Any, *names: str) -> int | None:
    for name in names:
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        if value:
            return int(value)
    return None


def message_tokens(message: Any) -> tuple[int | None, int | None, int]:
    """``(input, output, total)`` tokens of one AI message from LangChain or Google metadata."""
    usage = getattr(message, "usage_metadata", None)
    if not usage:
        meta = getattr(message, "response_metadata", None) or {}
        usage = (meta.get("usage_metadata") or meta.get("token_usage") or {}) if isinstance(meta, dict) else {}
    if not usage:
        return None, None, 0
    input_tokens = _usage_value(usage, "input_tokens", "prompt_token_count", "prompt_tokens")
    output_tokens = _usage_value(usage, "output_tokens", "candidates_token_count", "completion_tokens")
    total = _usage_value(usage, "total_tokens", "total_token_count") or ((input_tokens or 0) + (output_tokens or 0))
    return input_tokens, output_tokens, int(total or 0)


def estimate_cost_usd(
    provider: str | None, model: str, *, input_tokens: int | None, output_tokens: int | None, tokens: int
) -> float:
    """The call's cost: the provider's list price when it is known, otherwise the platform's blended estimate."""
    name = (model or "").strip()
    if (provider == "gemini" or name.startswith("gemini")) and input_tokens is not None and output_tokens is not None:
        from core.llm.router import gemini_cost_usd

        return round(gemini_cost_usd(name, input_tokens, output_tokens), 6)
    from core.langgraph.runner import _BLENDED_COST_PER_1K_TOKENS_USD

    return round(tokens * _BLENDED_COST_PER_1K_TOKENS_USD / 1000, 6) if tokens else 0.0


@dataclass(frozen=True)
class ModelCallRecord:
    tenant_id: str | None
    correlation_id: str
    use_case: str
    agent_id: str | None
    policy_id: str | None
    access_policy_id: str | None
    requested_provider: str | None
    requested_model: str | None
    provider: str
    model: str
    fallback_from: str | None
    restricted: bool
    outcome: str
    error_type: str | None
    latency_ms: int
    admission_wait_ms: int | None
    tokens: int
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float
    tokens_per_second: float | None
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        return data


def _canonical(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    if isinstance(value, bool | int | float):
        return value
    return str(value)


def canonical_record_payload(record: Any) -> str:
    """The signed fields of a record (dataclass, dict or row), serialised the same way wherever they come from."""
    get = record.get if isinstance(record, dict) else lambda k, d=None: getattr(record, k, d)
    canonical = {name: _canonical(get(name)) for name in SIGNED_FIELDS}
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)


def sign_record(record: Any, secret: bytes) -> str:
    return hmac.new(secret, canonical_record_payload(record).encode(), hashlib.sha256).hexdigest()


def verify_record(row: Any, secret: bytes | None = None) -> bool:
    """Whether a stored row's signature still matches its fields."""
    key = secret if secret is not None else settings.secret_key.encode()
    raw = row.get("signature") if isinstance(row, dict) else getattr(row, "signature", None)
    signature = str(raw or "")
    return bool(signature) and hmac.compare_digest(signature, sign_record(row, key))


def _meter(record: ModelCallRecord) -> None:
    try:
        from observability import metrics as m

        labels = {"provider": record.provider, "model": record.model}
        m.model_calls_total.labels(**labels, outcome=record.outcome).inc()
        m.model_call_latency_seconds.labels(**labels).observe(record.latency_ms / 1000.0)
        if record.tokens:
            m.model_call_tokens_total.labels(**labels, direction="total").inc(record.tokens)
            m.llm_tokens_total.labels(model=record.model).inc(record.tokens)
        if record.input_tokens:
            m.model_call_tokens_total.labels(**labels, direction="input").inc(record.input_tokens)
        if record.output_tokens:
            m.model_call_tokens_total.labels(**labels, direction="output").inc(record.output_tokens)
        if record.cost_usd:
            m.model_call_cost_usd_total.labels(**labels).inc(record.cost_usd)
            m.llm_cost_total.labels(model=record.model).inc(record.cost_usd)
        if record.tokens_per_second is not None:
            m.model_call_output_tokens_per_second.labels(**labels).observe(record.tokens_per_second)
        if record.error_type:
            m.model_call_errors_total.labels(**labels, error_type=record.error_type).inc()
        if record.fallback_from:
            m.model_fallbacks_total.labels(
                provider=record.provider, from_model=record.fallback_from, to_model=record.model
            ).inc()
        if record.admission_wait_ms is not None:
            m.model_admission_wait_seconds.labels(**labels).observe(record.admission_wait_ms / 1000.0)
    # enterprise-gate: broad-except-ok reason=metrics-outage-degrades-to-an-unmetered-call-never-changes-it
    except Exception:
        logger.debug("model_call_metrics_skipped", provider=record.provider, model=record.model)


async def _write(record: ModelCallRecord) -> bool:
    """Write the signed row while the gateway is on for the tenant; a failure is logged, never raised."""
    tid = record.tenant_id
    if not tid or not settings.model_gateway_records_enabled:
        return False
    try:
        from core.database import get_tenant_session
        from core.models.model_gateway_record import ModelGatewayRecord

        signature = sign_record(record, settings.secret_key.encode())
        row = ModelGatewayRecord(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(str(tid)),
            signature=signature,
            **{name: getattr(record, name) for name in SIGNED_FIELDS if name != "tenant_id"},
        )
        async with get_tenant_session(uuid.UUID(str(tid))) as session:
            session.add(row)
        return True
    # enterprise-gate: broad-except-ok reason=record-write-failure-degrades-to-a-logged-call-never-changes-it
    except Exception as exc:
        logger.warning(
            "model_gateway_record_write_failed",
            error_type=type(exc).__name__,
            correlation_id=record.correlation_id,
        )
        return False


async def record_model_call(
    route: Any = None,
    *,
    provider: str | None,
    model: str,
    outcome: str,
    latency_ms: int,
    tokens: int = 0,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    error_type: str | None = None,
    fallback_from: str | None = None,
    admission_wait_ms: int | None = None,
    use_case: str | None = None,
    agent_id: str | None = None,
) -> ModelCallRecord:
    """Meter one finished model call and, for a routed call, write its signed record.

    ``route`` is the decision the call was made under; when omitted, the
    decision bound for the current run (``model_gateway.bind_route``) is used.
    """
    context = current_route() if route is None else None
    decision = route if route is not None else (context.decision if context else None)
    if context is not None:
        use_case = use_case or context.use_case
        agent_id = agent_id or context.agent_id
    provider_name = (provider or getattr(decision, "provider", None) or "unknown").strip().lower()
    if cost_usd is None:
        cost_usd = estimate_cost_usd(
            provider_name, model, input_tokens=input_tokens, output_tokens=output_tokens, tokens=tokens
        )
    produced = output_tokens if output_tokens is not None else (tokens or None)
    tokens_per_second = round(produced / (latency_ms / 1000.0), 2) if produced and latency_ms > 0 else None
    record = ModelCallRecord(
        tenant_id=str(getattr(decision, "tenant_id", None) or "") or None,
        correlation_id=str(getattr(decision, "correlation_id", None) or uuid.uuid4().hex),
        use_case=use_case or str(getattr(decision, "use_case", "") or ""),
        agent_id=agent_id,
        policy_id=getattr(decision, "policy_id", None),
        access_policy_id=getattr(decision, "access_policy_id", None),
        requested_provider=getattr(decision, "requested_provider", None),
        requested_model=getattr(decision, "requested_model", None),
        provider=provider_name,
        model=(model or getattr(decision, "model", "") or "unknown").strip(),
        fallback_from=fallback_from,
        restricted=bool(getattr(decision, "restricted", False)),
        outcome=outcome if outcome in OUTCOMES else "failed",
        error_type=error_type,
        latency_ms=int(latency_ms),
        admission_wait_ms=admission_wait_ms,
        tokens=int(tokens or 0),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=float(cost_usd or 0.0),
        tokens_per_second=tokens_per_second,
        created_at=datetime.now(UTC),
    )
    _meter(record)
    logger.info(
        "model_call_recorded",
        correlation_id=record.correlation_id,
        use_case=record.use_case,
        provider=record.provider,
        model=record.model,
        outcome=record.outcome,
        latency_ms=record.latency_ms,
        tokens=record.tokens,
        cost_usd=record.cost_usd,
        fallback_from=record.fallback_from,
        error_type=record.error_type,
    )
    if decision is not None and getattr(decision, "gated", False):
        await _write(record)
    return record
