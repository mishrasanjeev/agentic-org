# SPDX-License-Identifier: Apache-2.0
"""Model-graded scorers: the judges, what they are sent, how a verdict is read and how a failure is kept apart."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from core.evals import scoring
from core.prompts import compare as prompt_compare

TENANT = uuid.uuid4()
CASE = prompt_compare.parse_cases(
    [
        {
            "id": "c1",
            "input": "Is water damage covered?",
            "equals": "yes",
            "context": "Policy 4.2: water damage from burst pipes is covered.",
            "reference": "Yes, burst-pipe water damage is covered under 4.2.",
        }
    ]
)[0]
BARE = prompt_compare.parse_cases([{"id": "c2", "input": "q", "equals": "y"}])[0]


class TestJudges:
    def test_the_judges_are_named_and_bounded(self):
        assert scoring.validate_judges(None) == ()
        assert scoring.validate_judges(["relevance", "faithfulness", "relevance"]) == ("relevance", "faithfulness")
        with pytest.raises(scoring.JudgeError, match="unknown judge"):
            scoring.validate_judges(["vibes"])
        with pytest.raises(scoring.JudgeError, match="at most 4"):
            scoring.validate_judges(list(scoring.JUDGES) + ["relevance"])

    def test_a_judge_that_needs_material_the_case_lacks_does_not_run(self):
        assert scoring.applicable("faithfulness", CASE) and not scoring.applicable("faithfulness", BARE)
        assert scoring.applicable("context_recall", CASE) and not scoring.applicable("context_recall", BARE)
        assert scoring.applicable("relevance", BARE) and scoring.applicable("instruction_adherence", BARE)

    def test_each_judge_is_sent_its_rubric_and_the_material_it_rates(self):
        system = "Answer claims questions in one sentence."
        output = "Yes, burst pipes are covered."
        for kind in scoring.JUDGES:
            messages = scoring.messages_for(kind, CASE, system, output)
            assert [message["role"] for message in messages] == ["system", "user"]
            user = messages[1]["content"]
            assert "ANSWER:\n" + output in user and "QUESTION:\n" + CASE.input in user
            assert '{"score": <1 to 5>' in user
        assert "CONTEXT:\nPolicy 4.2" in scoring.messages_for("faithfulness", CASE, system, output)[1]["content"]
        assert (
            "INSTRUCTIONS:\n" + system
            in scoring.messages_for("instruction_adherence", CASE, system, output)[1]["content"]
        )
        assert (
            "REFERENCE:\nYes, burst-pipe" in scoring.messages_for("context_recall", CASE, system, output)[1]["content"]
        )
        assert "CONTEXT:" not in scoring.messages_for("relevance", CASE, system, output)[1]["content"]

    def test_long_material_is_cut_before_it_is_sent(self):
        long_output = "x" * (scoring.MAX_JUDGED_CHARS + 500)
        user = scoring.messages_for("relevance", CASE, "s", long_output)[1]["content"]
        assert "[cut]" in user and len(user) < scoring.MAX_JUDGED_CHARS + 600


class TestVerdict:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('{"score": 5, "reason": "Fully supported."}', (1.0, "Fully supported.")),
            ('Here you go: {"score": 1, "reason": "No."} thanks', (0.0, "No.")),
            ('{"reason": "r", "score": 3.0}', (0.5, "r")),
            ('{"score": 4}', (0.75, "")),
            ("I would say 4 out of 5.", None),
            ('{"score": 7, "reason": "off the scale"}', None),
            ('{"score": true}', None),
            ('{"score": "4"}', None),
            ("", None),
        ],
    )
    def test_a_rating_is_read_from_the_json_the_judge_returns(self, text, expected):
        assert scoring.parse_verdict(text) == expected

    def test_the_reason_is_bounded(self):
        score, reason = scoring.parse_verdict('{"score": 2, "reason": "' + "r" * 1000 + '"}')
        assert score == 0.25 and len(reason) == scoring.MAX_REASON_CHARS


class TestJudgeOne:
    def _judge(self, monkeypatch, complete):
        monkeypatch.setattr(prompt_compare, "_complete", complete)
        gate = asyncio.Semaphore(1)
        return asyncio.run(scoring.judge_one(TENANT, "gpt-4o-mini", "relevance", CASE, "s", "Yes.", gate))

    def test_a_verdict_carries_the_score_the_reason_and_the_cost(self, monkeypatch):
        seen = {}

        async def _complete(tenant, model, messages, max_tokens, pseudonymiser=None):
            seen.update(tenant=tenant, model=model, max_tokens=max_tokens, user=messages[1]["content"])
            return SimpleNamespace(model="gpt-4o-mini", content='{"score": 4, "reason": "Mostly."}', cost_usd=0.0002)

        verdict = self._judge(monkeypatch, _complete)
        assert (verdict.score, verdict.reason, verdict.error_type, verdict.cost_usd) == (0.75, "Mostly.", None, 0.0002)
        assert seen["tenant"] == TENANT and seen["model"] == "gpt-4o-mini"
        assert seen["max_tokens"] == scoring.JUDGE_MAX_TOKENS and "ANSWER:\nYes." in seen["user"]

    def test_a_failed_call_an_unrated_reply_and_another_model_are_errors_not_scores(self, monkeypatch):
        async def _boom(*_args, **_kwargs):
            raise TimeoutError("slow")

        assert self._judge(monkeypatch, _boom).error_type == "TimeoutError"

        async def _prose(*_args, **_kwargs):
            return SimpleNamespace(model="gpt-4o-mini", content="Looks fine to me.", cost_usd=0.0001)

        unrated = self._judge(monkeypatch, _prose)
        assert (unrated.score, unrated.error_type, unrated.cost_usd) == (None, "unrated", 0.0001)

        async def _other(*_args, **_kwargs):
            return SimpleNamespace(model="gpt-4o", content='{"score": 5}', cost_usd=0.001)

        assert self._judge(monkeypatch, _other).error_type == "served_by_other_model"

    def test_with_pseudonymisation_the_judge_call_carries_the_session_and_the_reason_is_restored(self, monkeypatch):
        seen = {}

        async def _complete(tenant, model, messages, max_tokens, pseudonymiser=None):
            seen["pseudonymiser"] = pseudonymiser
            return SimpleNamespace(model="gpt-4o-mini", content='{"score": 3, "reason": "[P1] is named."}', cost_usd=0)

        class _Session:
            def restore_text(self, text):
                return text.replace("[P1]", "Asha")

        monkeypatch.setattr(prompt_compare, "_complete", _complete)
        session = _Session()
        verdict = asyncio.run(
            scoring.judge_one(TENANT, "gpt-4o-mini", "relevance", CASE, "s", "Yes.", asyncio.Semaphore(1), session)
        )
        assert seen["pseudonymiser"] is session and verdict.reason == "Asha is named."
