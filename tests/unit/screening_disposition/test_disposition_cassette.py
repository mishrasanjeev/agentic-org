# SPDX-License-Identifier: Apache-2.0
"""§8.2 record and replay: the disposition rationale replays from a reviewed cassette with no live model."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import pytest

from connectors.framework.verification_provider import Deadline, PersonSubject, ScreenOptions
from connectors.providers.mock import MockConfig, MockProvider
from core import model_replay
from core.agents.screening_disposition import DispositionConfig, DispositionDependencies, run_screening_disposition
from core.domain_schemas import validate
from core.langgraph import llm_factory
from core.model_replay import CassetteMissError
from core.test_doubles.grant_authorizer import ALLOW_PROVIDER_CALLS

FROZEN = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
MODEL = "gemini-2.5-flash"


@pytest.fixture(autouse=True)
def _test_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_replay, "_runtime_env", lambda: "test")
    monkeypatch.setenv("AGENTICORG_LLM_MODE", "cloud")
    if os.getenv("AGENTICORG_MODEL_MODE") != "record":
        monkeypatch.setenv("AGENTICORG_MODEL_MODE", "replay")

        def no_live_model(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("replay must not build a live model")

        monkeypatch.setattr(llm_factory, "_create_live_chat_model", no_live_model)


async def _dispose(name: str, date_of_birth: str) -> Any:
    provider = MockProvider(MockConfig(clock=lambda: FROZEN))
    result = await provider.screen_person(
        PersonSubject(full_name=name, date_of_birth=date_of_birth),
        ScreenOptions(idempotency_key="cassette.person"),
        deadline=Deadline.after(5),
    )
    return await run_screening_disposition(
        tenant_id="",
        case_id="case-cassette-2",
        screening_result=result,
        hit_id=result.hits[0].hit_id,
        subject={"kind": "person", "name": name, "date_of_birth": date_of_birth, "nationalities": ["US"]},
        associated_entities=["Oakhollow Bakery Cooperative"],
        config=DispositionConfig(llm_model=MODEL),
        deps=DispositionDependencies(provider=provider, clock=lambda: FROZEN, authorizer=ALLOW_PROVIDER_CALLS),
        run_id="run-cassette-2",
    )


async def test_false_positive_rationale_replays_from_its_cassette(model_cassette: Any) -> None:
    outcome = await _dispose("Jorund Halvessen", "1990-03")
    assert outcome.status == "completed", outcome.failure_reason
    validate("screening_disposition", outcome.disposition)
    assert outcome.rationale_source == "model", outcome.rationale_rejected
    assert outcome.disposition["proposed_outcome"] == "false_positive"


async def test_a_changed_comparison_misses_the_cassette(model_cassette: Any) -> None:
    if os.getenv("AGENTICORG_MODEL_MODE") == "record":
        pytest.skip("replay-only check")
    with pytest.raises(CassetteMissError):
        await _dispose("Jorund Halvessen", "1948-07")
