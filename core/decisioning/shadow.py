# SPDX-License-Identifier: Apache-2.0

"""Shadow-mode Jev observation for bounded agent routing comparisons.

This module observes the existing route proposed by the LLM. It never changes
that route, executes a tool, or makes an approval decision. The state sent to
Jev is deliberately limited to non-content metadata so shadow mode cannot
leak task inputs, connector parameters, or commerce facts.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import structlog
from prometheus_client import Counter, Histogram

from core.config import settings
from core.decisioning.contracts import (
    DecisionProvider,
    DecisionProviderError,
    DecisionQuestion,
    DecisionRequest,
    DecisionResponse,
)
from core.decisioning.runtime import build_jev_provider

logger = structlog.get_logger(__name__)

ShadowOutcome = Literal[
    "disabled",
    "observed",
    "unavailable",
    "invalid",
    "sampled_out",
    "budget_exhausted",
    "circuit_open",
]

_shadow_total = Counter(
    "agenticorg_jev_shadow_decisions_total",
    "Jev shadow observations by outcome and agreement.",
    ["outcome", "agreement"],
)
_shadow_latency = Histogram(
    "agenticorg_jev_shadow_decision_duration_seconds",
    "Jev shadow decision latency.",
)

_shadow_calls = 0
_shadow_consecutive_failures = 0
_shadow_circuit_open_until = 0.0


@dataclass(frozen=True, slots=True)
class ShadowObservation:
    """Bounded result returned to the caller for tracing only."""

    outcome: ShadowOutcome
    agreement: bool | None = None
    confidence: float | None = None
    latency_ms: float | None = None


def _route_for(proposed_tool_names: Sequence[str]) -> str:
    return "tool_call" if proposed_tool_names else "no_tool"


def _sampled_in(tenant_id: str | None, action: str) -> bool:
    subject = f"{tenant_id or 'platform'}:{action[:120]}".encode()
    bucket = int.from_bytes(hashlib.sha256(subject).digest()[:4], "big") / 2**32
    return bucket < settings.jev_shadow_sample_rate


def _record_failure() -> None:
    global _shadow_consecutive_failures, _shadow_circuit_open_until
    _shadow_consecutive_failures += 1
    if _shadow_consecutive_failures >= settings.jev_shadow_failure_threshold:
        _shadow_circuit_open_until = time.monotonic() + settings.jev_shadow_cooldown_seconds


def _record_success() -> None:
    global _shadow_consecutive_failures
    _shadow_consecutive_failures = 0


def build_tool_routing_request(
    *,
    agent_type: str,
    domain: str,
    action: str,
    available_tools: Sequence[dict],
    requested_tools: object,
    tenant_id: str | None,
) -> tuple[DecisionRequest, str]:
    """Build the bounded routing request and deterministic baseline route."""
    proposed = _tool_names(requested_tools)
    available = _available_tool_names(available_tools)
    proposed_route = _route_for(proposed)
    return (
        DecisionRequest(
            state={
                "agent_type": agent_type[:80],
                "domain": domain[:80],
                "action": action[:120],
                "available_tool_names": available,
                "proposed_tool_names": proposed,
            },
            purpose="agent_tool_routing_shadow",
            questions={
                "route": DecisionQuestion(
                    type="choice",
                    instructions="Which bounded route best fits this agent action before any external tool call?",
                    criteria={
                        "no_tool": "Continue without an external tool call.",
                        "tool_call": "Use the already proposed authorized tool call.",
                        "human_review": "Pause for human review before continuing.",
                        "blocked": "Do not continue this action.",
                    },
                )
            },
            tenant_id=tenant_id,
        ),
        proposed_route,
    )


def _tool_names(requested_tools: object) -> list[str]:
    """Return connector/tool labels without copying request parameters."""
    if not isinstance(requested_tools, list):
        return []
    names: list[str] = []
    for item in requested_tools[:32]:
        if not isinstance(item, dict):
            continue
        connector = item.get("connector")
        tool = item.get("tool")
        if isinstance(connector, str) and isinstance(tool, str) and connector and tool:
            names.append(f"{connector[:80]}.{tool[:80]}")
    return names


def _available_tool_names(available_tools: Sequence[dict]) -> list[str]:
    names: list[str] = []
    for item in available_tools[:64]:
        if not isinstance(item, dict):
            continue
        connector = item.get("connector")
        tool = item.get("tool")
        if isinstance(connector, str) and isinstance(tool, str) and connector and tool:
            names.append(f"{connector[:80]}.{tool[:80]}")
    return names


async def observe_tool_routing(
    *,
    tenant_id: str | None,
    agent_type: str,
    domain: str,
    action: str,
    available_tools: Sequence[dict],
    requested_tools: object,
    provider: DecisionProvider | None = None,
) -> ShadowObservation:
    """Compare Jev's advisory route with the existing route in shadow mode.

    Any provider/configuration/response failure becomes an observation result;
    it never propagates into agent execution. ``active`` intentionally does
    not call Jev until a separately reviewed policy integration exists.
    """
    if settings.jev_mode != "shadow":
        agreement = "na" if settings.jev_mode == "off" else "inactive"
        _shadow_total.labels(outcome="disabled", agreement=agreement).inc()
        return ShadowObservation(outcome="disabled")

    if not _sampled_in(tenant_id, action):
        _shadow_total.labels(outcome="sampled_out", agreement="na").inc()
        return ShadowObservation(outcome="sampled_out")

    global _shadow_calls
    if _shadow_calls >= settings.jev_shadow_max_calls_per_process:
        _shadow_total.labels(outcome="budget_exhausted", agreement="na").inc()
        return ShadowObservation(outcome="budget_exhausted")
    if time.monotonic() < _shadow_circuit_open_until:
        _shadow_total.labels(outcome="circuit_open", agreement="na").inc()
        return ShadowObservation(outcome="circuit_open")
    _shadow_calls += 1

    request, proposed_route = build_tool_routing_request(
        agent_type=agent_type,
        domain=domain,
        action=action,
        available_tools=available_tools,
        requested_tools=requested_tools,
        tenant_id=tenant_id,
    )

    started = time.perf_counter()
    try:
        decision_provider = provider or await build_jev_provider(tenant_id)
        response: DecisionResponse = await decision_provider.decide(request)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        answer = response.answers.get("route")
        if answer is None or answer.type != "choice" or not isinstance(answer.value, str):
            raise DecisionProviderError("Jev shadow route answer was invalid")
        agreement = answer.value == proposed_route
        _record_success()
        _shadow_total.labels(outcome="observed", agreement=str(agreement).lower()).inc()
        _shadow_latency.observe(latency_ms / 1000)
        logger.info(
            "jev_shadow_decision",
            outcome="observed",
            agreement=agreement,
            confidence=answer.confidence,
            latency_ms=latency_ms,
        )
        return ShadowObservation(
            outcome="observed",
            agreement=agreement,
            confidence=answer.confidence,
            latency_ms=latency_ms,
        )
    except (DecisionProviderError, ValueError, TimeoutError, OSError) as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        _record_failure()
        _shadow_total.labels(outcome="unavailable", agreement="na").inc()
        logger.info("jev_shadow_unavailable", error_type=type(exc).__name__)
        return ShadowObservation(outcome="unavailable", latency_ms=latency_ms)
    except Exception as exc:  # enterprise-gate: broad-except-ok reason=shadow-observer-never-fails-agent-execution
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        _record_failure()
        _shadow_total.labels(outcome="invalid", agreement="na").inc()
        logger.warning("jev_shadow_invalid", error_type=type(exc).__name__)
        return ShadowObservation(outcome="invalid", latency_ms=latency_ms)
