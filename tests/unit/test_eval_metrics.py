# SPDX-License-Identifier: Apache-2.0
"""Labelled cases and the deterministic metrics over a run's outcomes."""

from __future__ import annotations

import pytest

from core.evals import metrics
from core.prompts import compare as prompt_compare


class TestLabels:
    def test_a_label_is_an_expectation_and_the_labels_are_read_from_the_dataset(self):
        cases = prompt_compare.parse_cases(
            [
                {"id": "a", "input": "Loud noise from the engine", "label": "mechanical"},
                {"id": "b", "input": "Dashboard warning light", "label": "electrical"},
                {"id": "c", "input": "Scratched door", "label": "mechanical", "contains": ["body shop"]},
            ]
        )
        assert prompt_compare.labels_of(cases) == ("mechanical", "electrical")
        assert cases[0].label == "mechanical" and cases[0].contains == ()

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ([{"input": "x", "label": ""}], "label is non-empty text"),
            ([{"input": "x", "label": 3}], "label is non-empty text"),
            ([{"input": "x", "label": "l" * 81}], "at most 80"),
            ([{"input": "x", "equals": "y", "reference": ""}], "reference is non-empty text"),
            ([{"input": "x", "equals": "y", "context": ["c"]}], "context is non-empty text"),
            ([{"input": "x", "reference": "only material, no expectation"}], "needs at least one of"),
        ],
    )
    def test_refused_shapes(self, raw, message):
        with pytest.raises(ValueError, match=message):
            prompt_compare.parse_cases(raw)

    def test_reference_material_is_kept_and_is_not_a_check(self):
        [case] = prompt_compare.parse_cases(
            [{"input": "q", "equals": "yes", "reference": " The answer is yes. ", "context": "Policy: yes."}]
        )
        assert (case.reference, case.context) == ("The answer is yes.", "Policy: yes.")
        assert prompt_compare.score(case, "yes") == []

    def test_the_predicted_label_is_the_first_whole_word_match(self):
        labels = ("approve", "approve with conditions", "decline")
        assert prompt_compare.predict_label("Decision: approve with conditions.", labels) == "approve with conditions"
        assert prompt_compare.predict_label("We decline; do not approve.", labels) == "decline"
        assert prompt_compare.predict_label("I cannot say.", labels) is None
        # A label inside another word is not a mention.
        assert prompt_compare.predict_label("The approvement process", ("approve",)) is None
        assert prompt_compare.predict_label("APPROVE", ("approve",)) == "approve"

    def test_a_labelled_case_fails_when_the_answer_names_another_or_no_label(self):
        [case] = prompt_compare.parse_cases([{"input": "q", "label": "decline"}])
        labels = ("approve", "decline")
        assert prompt_compare.score(case, "We decline the claim.", labels) == []
        assert prompt_compare.score(case, "We approve the claim.", labels) == ["label:decline"]
        assert prompt_compare.score(case, "Unsure.", labels) == ["label:decline"]
        # Without the dataset's labels the case's own label is the only one looked for.
        assert prompt_compare.score(case, "We approve the claim.") == ["label:decline"]

    def test_evaluate_scores_labels_with_the_datasets_labels(self):
        import asyncio
        import uuid

        answers = {"a": "approve", "b": "approve"}

        async def _one(_tenant, _model, _system, user_input, _limit, _gate, _session):
            return prompt_compare.ModelResult(model="m", ok=True, output=answers[user_input])

        cases = prompt_compare.parse_cases(
            [{"id": "a", "input": "a", "label": "approve"}, {"id": "b", "input": "b", "label": "decline"}]
        )
        original = prompt_compare.run_one
        prompt_compare.run_one = _one  # type: ignore[assignment]
        try:
            report = asyncio.run(
                prompt_compare.evaluate(uuid.uuid4(), variants=[("v", "s")], cases=cases, model="gpt-4o")
            )
        finally:
            prompt_compare.run_one = original  # type: ignore[assignment]
        [variant] = report["variants"]
        assert (variant["passed"], variant["failed"]) == (1, 1)
        assert variant["results"][1]["failed_checks"] == ["label:decline"]


RESULTS = [
    {"id": "1", "result": "passed", "has_equals": True, "label": {"expected": "approve", "predicted": "approve"}},
    {"id": "2", "result": "failed", "has_equals": True, "failed_checks": ["equals"]},
    {
        "id": "3",
        "result": "failed",
        "failed_checks": ["label:decline"],
        "label": {"expected": "decline", "predicted": "approve"},
    },
    {
        "id": "4",
        "result": "failed",
        "failed_checks": ["label:decline"],
        "label": {"expected": "decline", "predicted": None},
    },
    {"id": "5", "result": "passed", "label": {"expected": "approve", "predicted": "approve"}},
    {"id": "6", "result": "error", "error_type": "TimeoutError", "has_equals": True, "label": {"expected": "decline"}},
]


class TestMetrics:
    def test_pass_rate_counts_scored_cases_only(self):
        assert metrics.summarise(RESULTS, ("approve", "decline"))["pass_rate"] == 0.4
        assert metrics.summarise([], ())["pass_rate"] is None

    def test_exact_match_covers_the_cases_that_carry_equals(self):
        assert metrics.exact_match(RESULTS) == {"cases": 2, "matched": 1, "rate": 0.5}
        assert metrics.exact_match([{"id": "x", "result": "passed"}]) == {"cases": 0, "matched": 0, "rate": None}

    def test_classification_is_macro_averaged_over_the_datasets_labels(self):
        report = metrics.classification(RESULTS, ("approve", "decline"))
        assert report is not None
        # approve: expected 2, predicted 3 (one wrong), correct 2 -> precision 2/3, recall 1.
        # decline: expected 2, predicted 0, correct 0 -> precision 0, recall 0.
        assert report["cases"] == 4 and report["accuracy"] == 0.5
        assert report["labels"]["approve"] == {
            "expected": 2,
            "predicted": 3,
            "correct": 2,
            "precision": 0.6667,
            "recall": 1.0,
            "f1": 0.8,
        }
        assert report["labels"]["decline"]["predicted"] == 0 and report["labels"]["decline"]["f1"] == 0.0
        assert (report["precision"], report["recall"], report["f1"]) == (0.3333, 0.5, 0.4)

    def test_no_labelled_cases_means_no_classification(self):
        assert metrics.classification([{"id": "1", "result": "passed"}], ()) is None
        assert metrics.summarise([{"id": "1", "result": "passed"}], ())["classification"] is None
