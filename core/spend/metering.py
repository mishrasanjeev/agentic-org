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

Direct calls get a fresh key per call (``provider_raw="direct"``) fixed when
the event is built, so a retry or a spill reuses it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

from core.spend import vocab

logger = structlog.get_logger()

LLM = "llm_tokens"


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


_HANDLERS = {
    "message": handle_message,
    "direct_response": handle_direct_response,
    "cancelled": handle_cancelled,
}


def handle(kind: str, tenant_id: object, raw: Mapping[str, Any]) -> None:
    """Dispatch ``spend.note``; an unknown kind is logged and ignored."""
    handler = _HANDLERS.get(kind)
    if handler is None:
        logger.debug("spend_note_unknown_kind", kind=str(kind)[:32])
        return
    handler(tenant_id, raw)
