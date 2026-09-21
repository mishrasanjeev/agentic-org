# SPDX-License-Identifier: Apache-2.0
"""§8.2 record and replay: the underwriter's narrative call replays from a reviewed cassette.

The cassette under ``tests/cassettes/test_underwriter_cassette/`` is the recorded model exchange
for the missing-owner case. Replay needs no model credentials and fails on any change to the
prompt or the case facts the model is shown. Re-record deliberately with
``AGENTICORG_MODEL_MODE=record`` against a live model and review the diff
(``docs/testing/record-replay.md``).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import pytest

from connectors.providers.mock import MockConfig, MockProvider
from core import model_replay
from core.agents.business_underwriter import UnderwriterConfig, UnderwriterDependencies, run_underwriter
from core.domain_schemas import validate
from core.langgraph import llm_factory
from core.model_replay import CassetteMissError
from core.policy import EXAMPLES_DIR, load_policy

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


async def _underwrite(application_key: str) -> Any:
    provider = MockProvider(MockConfig(clock=lambda: FROZEN))

    async def no_wait(seconds: float) -> None:
        return None

    return await run_underwriter(
        tenant_id="",
        case_id="case-cassette-1",
        run_id="run-cassette-1",
        application=provider.fixture(application_key).application,
        config=UnderwriterConfig(
            policy=load_policy(EXAMPLES_DIR / "business_onboarding_uk.yaml"),
            require_os_isolation=False,
            llm_model=MODEL,
        ),
        deps=UnderwriterDependencies(provider=provider, clock=lambda: FROZEN, sleep=no_wait),
    )


async def test_missing_owner_case_replays_its_recorded_narrative(model_cassette: Any) -> None:
    assert list(model_cassette.glob("*.json")), f"no cassette recorded in {model_cassette}"
    outcome = await _underwrite("gb-missing-owner-marlpit")

    assert outcome.status == "completed", outcome.failure_reason
    validate("underwriting_memo", outcome.memo)
    assert outcome.narrative["rejected"] == []
    assert set(outcome.narrative["accepted"]) >= {"ownership", "screening"}
    assert outcome.memo["provenance"]["model_id"] == MODEL
    ownership = next(s for s in outcome.memo["sections"] if s["section_id"] == "ownership")
    assert [f["code"] for f in ownership["findings"]] == ["missing_owner", "narrative_summary"]

    # The replayed narrative must describe the case the request actually
    # carried: a stale recording would summarise a different activity finding.
    activity = next(s for s in outcome.memo["sections"] if s["section_id"] == "activity")
    assert [f["code"] for f in activity["findings"]] == ["activity_consistent", "narrative_summary"]
    summary = next(f for f in activity["findings"] if f["code"] == "narrative_summary")
    assert summary["statement"] == "Activity observed on the website is consistent with the declared activity."


async def test_a_different_case_misses_the_cassette_instead_of_replaying_stale_text(model_cassette: Any) -> None:
    if os.getenv("AGENTICORG_MODEL_MODE") == "record":
        pytest.skip("replay-only check")
    with pytest.raises(CassetteMissError):
        await _underwrite("gb-clean-brightwater")
