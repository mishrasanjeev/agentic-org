# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Mapping

import pytest

from core.decisioning.contracts import DecisionAnswer, DecisionProviderError, DecisionResponse
from core.decisioning.evaluation import (
    RoutingEvaluationCase,
    RoutingEvaluationReport,
    RoutingEvaluationResult,
    assess_routing_evaluation_report,
    build_routing_evaluation_plan,
    evaluate_routing_cases,
    load_routing_cases,
)


class FakeEvaluationProvider:
    def __init__(self, routes: Mapping[str, str | Exception]) -> None:
        self.routes = routes
        self.calls = 0

    async def decide(self, request):
        self.calls += 1
        route = self.routes[request.state["action"]]
        if isinstance(route, Exception):
            raise route
        return DecisionResponse(
            provider="jev",
            model="jev-latest",
            answers={"route": DecisionAnswer(type="choice", value=route, confidence=0.9)},
            usage={"input_tokens": 10, "output_tokens": 2},
        )


def _case(case_id: str, action: str, *, with_tool: bool) -> RoutingEvaluationCase:
    names = ("catalog.search",) if with_tool else ()
    return RoutingEvaluationCase(case_id, "commerce_agent", "commerce", action, names, names)


@pytest.mark.asyncio
async def test_evaluator_reports_agreement_confidence_latency_and_usage() -> None:
    cases = (_case("one", "first", with_tool=True), _case("two", "second", with_tool=False))
    provider = FakeEvaluationProvider({"first": "tool_call", "second": "no_tool"})

    report = await evaluate_routing_cases(cases, provider)
    summary = report.to_dict()

    assert provider.calls == 2
    assert summary["outcomes"]["observed"] == 2
    assert summary["agreement_rate"] == 1.0
    assert summary["confidence_average"] == 0.9
    assert summary["usage"] == {
        "input_tokens": 20,
        "output_tokens": 4,
        "estimated_cost_usd": None,
    }
    assert summary["latency_ms"]["p95"] is not None

    priced = await evaluate_routing_cases(
        cases,
        provider,
        input_cost_per_million_usd=1.0,
        output_cost_per_million_usd=2.0,
    )
    assert priced.to_dict()["usage"]["estimated_cost_usd"] == 0.000028


def test_synthetic_routing_corpus_loads_without_provider_calls() -> None:
    cases = load_routing_cases()
    assert len(cases) == 6
    assert all(case.case_id.startswith("jev-route-") for case in cases)


def test_plan_is_redacted_and_keeps_active_routing_disabled() -> None:
    plan = build_routing_evaluation_plan(
        load_routing_cases(),
        sample_rate=1.0,
        max_calls=10,
        failure_threshold=2,
        cooldown_seconds=30.0,
    )

    assert plan["active_routing_enabled"] is False
    assert plan["non_executing"] is True
    assert plan["corpus"]["case_count"] == 6
    assert plan["corpus"]["content_policy"] == "synthetic_metadata_only"
    assert plan["reporting"]["status"] == "not_run"
    assert plan["guardrails"]["task_content_forwarded"] is False


def test_report_gates_fail_on_disagreement_and_provider_failures() -> None:
    report = RoutingEvaluationReport(
        results=(
            RoutingEvaluationResult("one", "observed", "tool_call", "no_tool", False, 0.9, 12.0),
            RoutingEvaluationResult("two", "unavailable", "no_tool"),
        ),
        max_calls=2,
    )

    gates = assess_routing_evaluation_report(report)

    assert gates["passed"] is False
    assert gates["blocking_reasons"] == ["agreement", "provider_failures"]


def test_report_gates_can_pass_without_cost_gate() -> None:
    report = RoutingEvaluationReport(
        results=(RoutingEvaluationResult("one", "observed", "tool_call", "tool_call", True, 0.9, 12.0),),
        max_calls=1,
        input_cost_per_million_usd=1.0,
        output_cost_per_million_usd=1.0,
    )

    gates = assess_routing_evaluation_report(report, maximum_estimated_cost_usd=1.0)

    assert gates["passed"] is True
    assert gates["checks"]["estimated_cost"]["actual"] == 0.0


@pytest.mark.asyncio
async def test_evaluator_circuit_breaker_stops_repeated_failures() -> None:
    cases = tuple(_case(str(index), f"action-{index}", with_tool=False) for index in range(4))
    provider = FakeEvaluationProvider({case.action: DecisionProviderError("unavailable") for case in cases})

    report = await evaluate_routing_cases(cases, provider, failure_threshold=2)

    assert provider.calls == 2
    assert [result.outcome for result in report.results] == [
        "unavailable",
        "unavailable",
        "circuit_open",
        "circuit_open",
    ]


@pytest.mark.asyncio
async def test_evaluator_call_budget_stops_additional_cases() -> None:
    cases = tuple(_case(str(index), f"action-{index}", with_tool=False) for index in range(3))
    provider = FakeEvaluationProvider({case.action: "no_tool" for case in cases})

    report = await evaluate_routing_cases(cases, provider, max_calls=1)

    assert provider.calls == 1
    assert [result.outcome for result in report.results] == [
        "observed",
        "budget_exhausted",
        "budget_exhausted",
    ]


@pytest.mark.asyncio
async def test_evaluator_sampling_prevents_provider_calls() -> None:
    cases = (_case("one", "first", with_tool=True), _case("two", "second", with_tool=False))
    provider = FakeEvaluationProvider({"first": "tool_call", "second": "no_tool"})

    report = await evaluate_routing_cases(cases, provider, sample_rate=0.0)

    assert provider.calls == 0
    assert report.to_dict()["provider_calls"] == 0
    assert report.to_dict()["outcomes"]["sampled_out"] == 2
