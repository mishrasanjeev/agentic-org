# SPDX-License-Identifier: Apache-2.0
"""Shared set-up for the Business Onboarding Underwriter tests: mock provider, policies, a scripted narrative."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from connectors.providers.mock import MockConfig, MockProvider
from core.agents.business_underwriter import (
    UnderwriterConfig,
    UnderwriterDependencies,
    UnderwritingOutcome,
    run_underwriter,
)
from core.policy import EXAMPLES_DIR, Policy, load_policy
from core.test_doubles.scripted_model import final

ALL_FIXTURES = (
    "gb-adversarial-northgate",
    "gb-clean-brightwater",
    "gb-dissolved-ashcombe",
    "gb-missing-owner-marlpit",
    "gb-true-match-corvane",
    "us-clean-hollowbrook",
    "us-clean-quillfeather",
    "us-false-positive-oakhollow",
    "us-hostile-web-glintmoor",
    "us-missing-owner-cinderpath",
    "us-thin-file-brambleway",
    "us-undeclared-owner-larkspur",
)

FROZEN = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def frozen() -> datetime:
    return FROZEN


def policy_for(key: str) -> Policy:
    name = "business_onboarding_uk.yaml" if key.startswith("gb-") else "business_onboarding_us.yaml"
    return load_policy(EXAMPLES_DIR / name)


def narrative_step(messages: list[BaseMessage]) -> AIMessage:
    """A well-behaved narrative: one summary per section with content, citing its first evidence entry."""
    import json

    context = json.loads(str(messages[-1].content))
    summaries = [
        {
            "section_id": s["section_id"],
            "summary": f"The {s['section_id']} section records {len(s['finding_codes'])} finding(s).",
            "citations": [0],
        }
        for s in context["sections"]
        if s["status"] in ("complete", "partial") and s["evidence_count"] > 0
    ]
    return final({"summaries": summaries, "confidence": 0.61})


async def underwrite(
    key: str,
    *,
    provider: Any = None,
    config: MockConfig | None = None,
    tenant_id: str = "",
    case_id: str | None = None,
    **dependency_overrides: Any,
) -> UnderwritingOutcome:
    backend = MockProvider(config or MockConfig(clock=frozen))
    fixture = backend.fixture(key)

    async def no_wait(seconds: float) -> None:
        return None

    deps = UnderwriterDependencies(provider=provider or backend, clock=frozen, sleep=no_wait, **dependency_overrides)
    return await run_underwriter(
        tenant_id=tenant_id,
        case_id=case_id or f"case-{key}",
        run_id=f"run-{key}",
        application=fixture.application,
        config=UnderwriterConfig(policy=policy_for(key), require_os_isolation=False, llm_model="scripted"),
        deps=deps,
    )


@pytest.fixture
def narrative(scripted_model: Callable[[list[Any]], Any]) -> Callable[..., Any]:
    """Install ``n`` well-behaved narrative turns (one per underwriter run)."""

    def install(runs: int = 1) -> Any:
        return scripted_model([narrative_step] * runs)

    return install


@pytest.fixture
def run_case() -> Callable[..., Any]:
    """``await run_case(fixture_key, **options)`` runs the underwriter against the mock provider."""
    return underwrite
