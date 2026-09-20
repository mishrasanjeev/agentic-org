"""Offline Jev routing evaluation over synthetic, redacted cases."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any

from core.decisioning.contracts import DecisionProvider, DecisionProviderError
from core.decisioning.shadow import build_tool_routing_request

DEFAULT_ROUTING_CORPUS = Path(__file__).resolve().parents[2] / "evals" / "golden_datasets" / "jev_routing.json"


def load_routing_cases(path: Path = DEFAULT_ROUTING_CORPUS) -> tuple[RoutingEvaluationCase, ...]:
    """Load and validate the synthetic routing corpus without calling a provider."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Jev routing corpus must be a JSON list")
    return tuple(RoutingEvaluationCase.from_mapping(case) for case in raw if isinstance(case, Mapping))


def build_routing_evaluation_plan(
    cases: Sequence[RoutingEvaluationCase],
    *,
    sample_rate: float,
    max_calls: int,
    failure_threshold: int,
    cooldown_seconds: float,
) -> dict[str, Any]:
    """Return a redacted operator contract without calling Jev.

    This describes what an explicit operator-run evaluation would measure. It
    is not a scorecard and does not persist or execute anything by itself.
    """
    if not 0.0 <= sample_rate <= 1.0:
        raise ValueError("sample_rate must be between 0 and 1")
    if max_calls < 0 or failure_threshold < 1 or cooldown_seconds <= 0:
        raise ValueError("evaluation controls are out of range")
    return {
        "status": "ready",
        "execution_mode": "offline_provider_evaluation",
        "provider": "jev",
        "routing_authority": "existing_agent_runtime",
        "active_routing_enabled": False,
        "non_executing": True,
        "corpus": {
            "path": "evals/golden_datasets/jev_routing.json",
            "case_count": len(cases),
            "domains": sorted({case.domain for case in cases}),
            "content_policy": "synthetic_metadata_only",
        },
        "controls": {
            "sample_rate": sample_rate,
            "max_calls_per_run": max_calls,
            "failure_threshold": failure_threshold,
            "cooldown_seconds": cooldown_seconds,
        },
        "review_gates": {
            "minimum_agreement_rate": 0.95,
            "maximum_invalid_or_unavailable": 0,
            "maximum_p95_latency_ms": 800,
            "human_review_required": True,
            "active_mode_requires_separate_approval": True,
        },
        "reporting": {
            "status": "not_run",
            "run_policy": "explicit_operator_cli_only",
            "persistence": "operator_supplied_output_path",
            "redaction": "case_ids_and_aggregates_only",
        },
        "guardrails": {
            "tool_execution": False,
            "authorization": False,
            "approval": False,
            "tenant_policy_bypass": False,
            "task_content_forwarded": False,
        },
    }


@dataclass(frozen=True, slots=True)
class RoutingEvaluationCase:
    """Non-sensitive metadata for one baseline-vs-Jev comparison."""

    case_id: str
    agent_type: str
    domain: str
    action: str
    available_tool_names: tuple[str, ...]
    proposed_tool_names: tuple[str, ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> RoutingEvaluationCase:
        def text(name: str, limit: int) -> str:
            value = raw.get(name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"evaluation case {name} must be non-empty text")
            return value.strip()[:limit]

        def names(name: str, limit: int) -> tuple[str, ...]:
            value = raw.get(name, [])
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                raise ValueError(f"evaluation case {name} must be a list")
            result = tuple(item.strip()[:160] for item in value if isinstance(item, str) and item.strip())
            return result[:limit]

        return cls(
            case_id=text("case_id", 80),
            agent_type=text("agent_type", 80),
            domain=text("domain", 80),
            action=text("action", 120),
            available_tool_names=names("available_tool_names", 64),
            proposed_tool_names=names("proposed_tool_names", 32),
        )

    @property
    def baseline_route(self) -> str:
        return "tool_call" if self.proposed_tool_names else "no_tool"


@dataclass(frozen=True, slots=True)
class RoutingEvaluationResult:
    case_id: str
    outcome: str
    baseline_route: str
    jev_route: str | None = None
    agreement: bool | None = None
    confidence: float | None = None
    latency_ms: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class RoutingEvaluationReport:
    results: tuple[RoutingEvaluationResult, ...]
    max_calls: int
    input_cost_per_million_usd: float | None = None
    output_cost_per_million_usd: float | None = None
    sample_rate: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        observed = [result for result in self.results if result.outcome == "observed"]
        agreements = [result for result in observed if result.agreement is not None]
        latencies = sorted(result.latency_ms for result in observed if result.latency_ms is not None)
        confidences = [result.confidence for result in observed if result.confidence is not None]
        input_tokens = sum(result.input_tokens for result in self.results)
        output_tokens = sum(result.output_tokens for result in self.results)
        estimated_cost = None
        if self.input_cost_per_million_usd is not None and self.output_cost_per_million_usd is not None:
            estimated_cost = round(
                input_tokens * self.input_cost_per_million_usd / 1_000_000
                + output_tokens * self.output_cost_per_million_usd / 1_000_000,
                8,
            )
        return {
            "execution_mode": "offline_provider_evaluation",
            "total_cases": len(self.results),
            "sample_rate": self.sample_rate,
            "provider_calls": sum(
                result.outcome not in {"sampled_out", "budget_exhausted", "circuit_open"}
                for result in self.results
            ),
            "max_calls": self.max_calls,
            "outcomes": {
                outcome: sum(result.outcome == outcome for result in self.results)
                for outcome in ("observed", "unavailable", "invalid", "sampled_out", "budget_exhausted", "circuit_open")
            },
            "agreement_rate": (
                round(sum(result.agreement is True for result in agreements) / len(agreements), 4)
                if agreements
                else None
            ),
            "confidence_average": round(mean(confidences), 4) if confidences else None,
            "latency_ms": {
                "p50": round(median(latencies), 2) if latencies else None,
                "p95": round(latencies[max(0, int(len(latencies) * 0.95) - 1)], 2) if latencies else None,
            },
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost_usd": estimated_cost,
            },
            "cases": [
                {
                    "case_id": result.case_id,
                    "outcome": result.outcome,
                    "baseline_route": result.baseline_route,
                    "jev_route": result.jev_route,
                    "agreement": result.agreement,
                    "confidence": result.confidence,
                    "latency_ms": result.latency_ms,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                }
                for result in self.results
            ],
        }


def assess_routing_evaluation_report(
    report: RoutingEvaluationReport,
    *,
    minimum_agreement_rate: float = 0.95,
    maximum_failures: int = 0,
    maximum_p95_latency_ms: float = 800.0,
    maximum_estimated_cost_usd: float | None = None,
) -> dict[str, Any]:
    """Apply explicit operator gates to a measured report."""
    if not 0.0 <= minimum_agreement_rate <= 1.0:
        raise ValueError("minimum_agreement_rate must be between 0 and 1")
    if maximum_failures < 0 or maximum_p95_latency_ms <= 0:
        raise ValueError("evaluation gates are out of range")
    if maximum_estimated_cost_usd is not None and maximum_estimated_cost_usd < 0:
        raise ValueError("maximum_estimated_cost_usd must not be negative")

    summary = report.to_dict()
    observed = summary["outcomes"]["observed"]
    failures = summary["outcomes"]["unavailable"] + summary["outcomes"]["invalid"]
    stopped = summary["outcomes"]["budget_exhausted"] + summary["outcomes"]["circuit_open"]
    agreement = summary["agreement_rate"]
    p95_latency = summary["latency_ms"]["p95"]
    estimated_cost = summary["usage"]["estimated_cost_usd"]

    checks: dict[str, dict[str, Any]] = {
        "agreement": {
            "passed": observed > 0 and agreement is not None and agreement >= minimum_agreement_rate,
            "actual": agreement,
            "threshold": minimum_agreement_rate,
        },
        "provider_failures": {
            "passed": failures <= maximum_failures and stopped == 0,
            "actual": failures,
            "stopped_early": stopped,
            "threshold": maximum_failures,
        },
        "p95_latency": {
            "passed": p95_latency is not None and p95_latency <= maximum_p95_latency_ms,
            "actual": p95_latency,
            "threshold": maximum_p95_latency_ms,
        },
    }
    if maximum_estimated_cost_usd is not None:
        checks["estimated_cost"] = {
            "passed": estimated_cost is not None and estimated_cost <= maximum_estimated_cost_usd,
            "actual": estimated_cost,
            "threshold": maximum_estimated_cost_usd,
        }
    else:
        checks["estimated_cost"] = {
            "passed": True,
            "actual": estimated_cost,
            "threshold": None,
            "status": "not_gated",
        }

    failures_list = [name for name, check in checks.items() if not check["passed"]]
    return {
        "passed": not failures_list,
        "checks": checks,
        "blocking_reasons": failures_list,
        "observed_cases": observed,
        "total_cases": summary["total_cases"],
    }


async def evaluate_routing_cases(
    cases: Sequence[RoutingEvaluationCase],
    provider: DecisionProvider,
    *,
    max_calls: int = 100,
    failure_threshold: int = 3,
    sample_rate: float = 1.0,
    input_cost_per_million_usd: float | None = None,
    output_cost_per_million_usd: float | None = None,
) -> RoutingEvaluationReport:
    """Evaluate Jev against deterministic routes without executing anything."""
    if max_calls < 0:
        raise ValueError("max_calls must not be negative")
    if failure_threshold < 1:
        raise ValueError("failure_threshold must be positive")
    if not 0.0 <= sample_rate <= 1.0:
        raise ValueError("sample_rate must be between 0 and 1")
    if (input_cost_per_million_usd is None) != (output_cost_per_million_usd is None):
        raise ValueError("input and output cost rates must be supplied together")
    if any(rate is not None and rate < 0 for rate in (input_cost_per_million_usd, output_cost_per_million_usd)):
        raise ValueError("cost rates must not be negative")

    results: list[RoutingEvaluationResult] = []
    calls = 0
    consecutive_failures = 0
    for case in cases:
        bucket = int.from_bytes(hashlib.sha256(case.case_id.encode()).digest()[:4], "big") / 2**32
        if bucket >= sample_rate:
            results.append(RoutingEvaluationResult(case.case_id, "sampled_out", case.baseline_route))
            continue
        if calls >= max_calls:
            results.append(RoutingEvaluationResult(case.case_id, "budget_exhausted", case.baseline_route))
            continue
        if consecutive_failures >= failure_threshold:
            results.append(RoutingEvaluationResult(case.case_id, "circuit_open", case.baseline_route))
            continue

        request, _ = build_tool_routing_request(
            agent_type=case.agent_type,
            domain=case.domain,
            action=case.action,
            available_tools=[
                {"connector": name.split(".", 1)[0], "tool": name.split(".", 1)[-1]}
                for name in case.available_tool_names
            ],
            requested_tools=[
                {"connector": name.split(".", 1)[0], "tool": name.split(".", 1)[-1]}
                for name in case.proposed_tool_names
            ],
            tenant_id=None,
        )
        started = time.perf_counter()
        calls += 1
        try:
            response = await provider.decide(request)
            answer = response.answers.get("route")
            if answer is None or answer.type != "choice" or not isinstance(answer.value, str):
                raise DecisionProviderError("Jev evaluation route answer was invalid")
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            results.append(
                RoutingEvaluationResult(
                    case.case_id,
                    "observed",
                    case.baseline_route,
                    answer.value,
                    answer.value == case.baseline_route,
                    answer.confidence,
                    latency_ms,
                    response.usage.get("input_tokens", 0),
                    response.usage.get("output_tokens", 0),
                )
            )
            consecutive_failures = 0
        except (DecisionProviderError, ValueError, TimeoutError, OSError):
            consecutive_failures += 1
            results.append(RoutingEvaluationResult(case.case_id, "unavailable", case.baseline_route))

    return RoutingEvaluationReport(
        tuple(results),
        max_calls,
        input_cost_per_million_usd,
        output_cost_per_million_usd,
        sample_rate,
    )
