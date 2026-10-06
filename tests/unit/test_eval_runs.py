# SPDX-License-Identifier: Apache-2.0
"""Evaluation runs: answering, scoring and judging one slice, what is stored, and the run endpoints."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from api.v1 import eval_datasets as api
from core.config import settings
from core.evals import datasets, runs, scoring
from core.models.eval_dataset import EvalDataset, EvalDatasetVersion
from core.models.eval_run import EvalRun
from core.prompts import compare as prompt_compare
from core.schemas.api import EvalDatasetRunIn

ROOT = Path(__file__).resolve().parents[2]
TENANT = uuid.uuid4()
ACTOR = uuid.uuid4()
RAW_CASES = [
    {"id": "a", "input": "a", "label": "approve", "reference": "Approve it."},
    {"id": "b", "input": "b", "label": "decline", "context": "Policy: decline b."},
    {"id": "c", "input": "c", "equals": "yes"},
    {"id": "d", "input": "d", "equals": "yes"},
]
ANSWERS = {"a": "We approve.", "b": "We approve too.", "c": "yes", "d": "no"}


def _cases():
    return prompt_compare.parse_cases(RAW_CASES)


class _Session:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.statements: list[str] = []
        self.added: list[Any] = []
        self.flushes = 0

    async def execute(self, statement):
        self.statements.append(str(statement))
        value = self.answers.pop(0)
        return SimpleNamespace(
            scalar_one_or_none=lambda: value, scalars=lambda: SimpleNamespace(all=lambda: list(value))
        )

    async def scalar(self, statement):
        self.statements.append(str(statement))
        return self.answers.pop(0)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushes += 1


@pytest.fixture
def engine(monkeypatch):
    """Scripted answers and judges in place of the model calls; records what was asked."""
    calls: dict[str, list] = {"answers": [], "judges": []}

    async def _run_one(_tenant, model, system, user_input, _limit, _gate, _session):
        calls["answers"].append((model, system, user_input))
        if user_input == "d" and "flaky" in system:
            return prompt_compare.ModelResult(model=model, ok=False, error_type="TimeoutError", latency_ms=5)
        return prompt_compare.ModelResult(
            model=model, ok=True, output=ANSWERS[user_input], latency_ms=10, cost_usd=0.001
        )

    async def _judge_one(_tenant, judge_model, kind, case, system, output, _gate, _session):
        calls["judges"].append((judge_model, kind, case.id))
        if case.id == "c":
            return scoring.Verdict(kind=kind, error_type="unrated", cost_usd=0.0001)
        return scoring.Verdict(kind=kind, score=0.75, reason=f"{kind} of {case.id}", cost_usd=0.0001)

    async def _no_pseudonymiser(_tenant):
        return None

    monkeypatch.setattr(prompt_compare, "run_one", _run_one)
    monkeypatch.setattr(prompt_compare, "open_pseudonymiser", _no_pseudonymiser)
    monkeypatch.setattr(scoring, "judge_one", _judge_one)
    return calls


def _run(**over):
    kwargs: dict[str, Any] = {
        "dataset_id": uuid.uuid4(),
        "version": 2,
        "content_hash": "h" * 64,
        "cases_total": 4,
        "cases": _cases(),
        "offset": 0,
        "system_text": "Decide the claim.",
        "model": "gpt-4o",
    }
    kwargs.update(over)
    return asyncio.run(runs.run_version(TENANT, **kwargs))


class TestRunVersion:
    def test_every_case_is_answered_scored_and_summarised(self, engine):
        report = _run()
        assert [call[2] for call in engine["answers"]] == ["a", "b", "c", "d"]
        assert all(call[0] == "gpt-4o" and call[1] == "Decide the claim." for call in engine["answers"])
        assert (report["passed"], report["failed"], report["errors"], report["pass_rate"]) == (2, 2, 0, 0.5)
        assert report["complete"] is True and report["judges"] == [] and report["judge_model"] is None
        assert report["prompt_hash"] == runs.prompt_hash("Decide the claim.") and len(report["prompt_hash"]) == 64
        by_id = {entry["id"]: entry for entry in report["results"]}
        assert by_id["a"] == {"id": "a", "result": "passed", "label": {"expected": "approve", "predicted": "approve"}}
        assert by_id["b"]["failed_checks"] == ["label:decline"] and by_id["b"]["label"]["predicted"] == "approve"
        assert by_id["d"] == {"id": "d", "has_equals": True, "result": "failed", "failed_checks": ["equals"]}
        assert report["metrics"]["exact_match"] == {"cases": 2, "matched": 1, "rate": 0.5}
        assert report["metrics"]["classification"]["accuracy"] == 0.5
        assert report["avg_latency_ms"] == 10 and report["cost_usd"] == 0.004
        assert engine["judges"] == [] and report["scores"] == {} and report["reasons"] == {}
        # Never an answer or an input.
        text = str(report)
        assert "We approve" not in text and "Decide the claim." not in text

    def test_judges_rate_the_cases_they_apply_to_and_errors_are_kept_apart(self, engine):
        report = _run(judges=("faithfulness", "relevance"), judge_model="gpt-4o-mini")
        # faithfulness needs context: only b has it; relevance applies to all four.
        assert sorted(engine["judges"]) == sorted(
            [("gpt-4o-mini", "faithfulness", "b")] + [("gpt-4o-mini", "relevance", case) for case in "abcd"]
        )
        assert report["scores"] == {
            "faithfulness": {"cases": 1, "mean": 0.75, "errors": 0},
            "relevance": {"cases": 3, "mean": 0.75, "errors": 1},
        }
        by_id = {entry["id"]: entry for entry in report["results"]}
        assert by_id["b"]["scores"] == {"faithfulness": 0.75, "relevance": 0.75}
        assert by_id["c"]["judge_errors"] == {"relevance": "unrated"} and "scores" not in by_id["c"]
        assert report["reasons"]["b"] == {"faithfulness": "faithfulness of b", "relevance": "relevance of b"}
        assert report["cost_usd"] == round(0.004 + 5 * 0.0001, 6)

    def test_a_failed_answer_is_an_error_and_is_not_judged(self, engine):
        report = _run(system_text="a flaky prompt", judges=("relevance",), judge_model="gpt-4o-mini")
        assert (report["passed"], report["failed"], report["errors"]) == (2, 1, 1)
        by_id = {entry["id"]: entry for entry in report["results"]}
        assert by_id["d"]["result"] == "error" and by_id["d"]["error_type"] == "TimeoutError"
        assert ("gpt-4o-mini", "relevance", "d") not in engine["judges"]
        assert report["pass_rate"] == round(2 / 3, 4)

    def test_a_slice_is_reported_as_partial(self, engine):
        report = _run(cases=_cases()[2:], offset=2)
        assert (report["cases_run"], report["offset"], report["complete"]) == (2, 2, False)

    def test_an_unusable_model_or_judge_model_is_refused_before_any_call(self, engine):
        with pytest.raises(ValueError):
            _run(model="no-such-model")
        with pytest.raises(ValueError):
            _run(judges=("relevance",), judge_model="")
        assert engine["answers"] == [] and engine["judges"] == []


def _report(**over):
    base = {
        "dataset_id": str(uuid.uuid4()),
        "version": 2,
        "content_hash": "h" * 64,
        "cases_total": 4,
        "offset": 0,
        "cases_run": 4,
        "complete": True,
        "model": "gpt-4o",
        "judge_model": "gpt-4o-mini",
        "judges": ["relevance"],
        "prompt_hash": "p" * 64,
        "max_tokens": 512,
        "passed": 2,
        "failed": 2,
        "errors": 0,
        "pass_rate": 0.5,
        "avg_latency_ms": 10,
        "cost_usd": 0.0045,
        "metrics": {"pass_rate": 0.5},
        "scores": {"relevance": {"cases": 4, "mean": 0.75, "errors": 0}},
        "results": [{"id": "a", "result": "passed", "scores": {"relevance": 0.75}}],
        "reasons": {"a": {"relevance": "fine"}},
    }
    base.update(over)
    return base


class TestStore:
    def test_what_is_kept_and_what_is_not(self):
        session = _Session()
        report = _report()
        run = runs.store(session, TENANT, report, prompt_label="claims v3", actor=ACTOR)
        assert session.added == [run] and run.tenant_id == TENANT and str(run.dataset_id) == report["dataset_id"]
        assert (run.model, run.judge_model, run.judges, run.prompt_label) == (
            "gpt-4o",
            "gpt-4o-mini",
            ["relevance"],
            "claims v3",
        )
        assert run.results == report["results"] and run.scores == report["scores"] and run.pass_rate == 0.5
        assert not hasattr(run, "reasons") and "fine" not in str(vars(run))
        described = runs.run_dict(run)
        assert described["complete"] is True and "results" not in described
        assert runs.run_dict(run, with_results=True)["results"] == report["results"]

    def test_lists_are_tenant_scoped_newest_first_and_bounded(self):
        session = _Session([])
        dataset_id = uuid.uuid4()
        assert asyncio.run(runs.list_runs(session, TENANT, dataset_id)) == []
        statement = session.statements[0]
        assert "eval_runs.tenant_id" in statement and "eval_runs.dataset_id" in statement
        assert "DESC" in statement and "LIMIT" in statement.upper()
        missing = _Session(None)
        with pytest.raises(datasets.DatasetError) as refused:
            asyncio.run(runs.get_run(missing, TENANT, uuid.uuid4()))
        assert (refused.value.status, refused.value.code) == (404, "run_not_found")


def _dataset(**over):
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "name": "Claims",
        "description": None,
        "latest_version": 2,
        "case_count": 4,
        "created_by_user": ACTOR,
        "created_at": datetime(2026, 10, 6, tzinfo=UTC),
        "updated_at": datetime(2026, 10, 6, tzinfo=UTC),
        "archived_at": None,
    }
    base.update(over)
    return EvalDataset(**base)


class TestEndpoints:
    @pytest.fixture
    def on(self, monkeypatch):
        monkeypatch.setattr(settings, "evals_v2_enabled", True)
        monkeypatch.setattr(settings, "prompt_compare_enabled", True)

    @pytest.fixture
    def store(self, monkeypatch):
        holder: dict[str, _Session] = {}

        @asynccontextmanager
        async def _session(_tenant):
            yield holder["session"]

        monkeypatch.setattr(api, "get_tenant_session", _session)

        def install(*answers):
            holder["session"] = _Session(*answers)
            return holder["session"]

        return install

    def test_a_run_is_stored_with_its_label_and_the_response_carries_its_id(self, on, store, engine):
        dataset = _dataset()
        stored, _ = datasets.normalise_cases(RAW_CASES)
        version = EvalDatasetVersion(
            id=uuid.uuid4(),
            tenant_id=TENANT,
            dataset_id=dataset.id,
            version=2,
            cases=stored,
            case_count=4,
            content_hash="h" * 64,
            note=None,
            created_by_user=ACTOR,
            created_at=None,
        )
        session = store(dataset, version)
        report = asyncio.run(
            api.run_eval_dataset(
                dataset.id,
                EvalDatasetRunIn(
                    system="Decide.",
                    model="gpt-4o",
                    judges=["relevance"],
                    judge_model="gpt-4o-mini",
                    prompt_label="claims v3",
                ),
                tenant_id=str(TENANT),
                user={"agenticorg:user_id": str(ACTOR)},
            )
        )
        [run] = session.added
        assert isinstance(run, EvalRun) and report["id"] == str(run.id) and session.flushes == 1
        assert run.prompt_label == "claims v3" and run.created_by_user == ACTOR and run.judges == ["relevance"]
        assert report["scores"]["relevance"]["cases"] == 3 and report["reasons"]["a"] == {"relevance": "relevance of a"}

    def test_a_run_can_be_kept_out_of_the_history(self, on, store, engine):
        dataset = _dataset()
        stored, _ = datasets.normalise_cases(RAW_CASES)
        version = EvalDatasetVersion(
            id=uuid.uuid4(),
            tenant_id=TENANT,
            dataset_id=dataset.id,
            version=2,
            cases=stored,
            case_count=4,
            content_hash="h" * 64,
            note=None,
            created_by_user=ACTOR,
            created_at=None,
        )
        session = store(dataset, version)
        report = asyncio.run(
            api.run_eval_dataset(
                dataset.id,
                EvalDatasetRunIn(system="Decide.", model="gpt-4o", store=False),
                tenant_id=str(TENANT),
                user={},
            )
        )
        assert session.added == [] and "id" not in report

    def test_judges_need_a_judge_model_and_a_known_name(self, on, store, engine):
        for body in (
            EvalDatasetRunIn(system="s", model="gpt-4o", judges=["relevance"]),
            EvalDatasetRunIn(system="s", model="gpt-4o", judges=["vibes"], judge_model="gpt-4o-mini"),
        ):
            store()
            with pytest.raises(HTTPException) as refused:
                asyncio.run(api.run_eval_dataset(uuid.uuid4(), body, tenant_id=str(TENANT), user={}))
            assert refused.value.status_code == 422 and engine["answers"] == []

    def test_the_history_and_one_run_are_read_back(self, on, store):
        dataset = _dataset()
        run = runs.store(_Session(), TENANT, _report(dataset_id=str(dataset.id)), prompt_label=None, actor=None)
        store(dataset, [run])
        listed = asyncio.run(api.list_eval_runs(dataset.id, tenant_id=str(TENANT)))
        assert [item["id"] for item in listed["runs"]] == [str(run.id)] and "results" not in listed["runs"][0]
        assert listed["judges"] == list(scoring.JUDGES)
        store(run)
        one = asyncio.run(api.get_eval_run(run.id, tenant_id=str(TENANT)))
        assert one["results"] == run.results
        store(None)
        with pytest.raises(HTTPException) as refused:
            asyncio.run(api.get_eval_run(uuid.uuid4(), tenant_id=str(TENANT)))
        assert refused.value.status_code == 404 and refused.value.detail["error"] == "run_not_found"

    def test_off_nothing_is_read(self):
        assert settings.evals_v2_enabled is False
        for call in (
            api.list_eval_runs(uuid.uuid4(), tenant_id=str(TENANT)),
            api.get_eval_run(uuid.uuid4(), tenant_id=str(TENANT)),
        ):
            with pytest.raises(HTTPException) as refused:
                asyncio.run(call)
            assert refused.value.status_code == 409


class TestMigration:
    def test_runs_are_tenant_scoped_and_indexed_by_dataset(self):
        src = (ROOT / "migrations" / "versions" / "v6_z45_eval_runs.py").read_text(encoding="utf-8")
        assert 'down_revision = "v6z44_eval_datasets"' in src
        assert "FORCE ROW LEVEL SECURITY" in src and "ON eval_runs(dataset_id, created_at);" in src
        assert "REFERENCES eval_datasets(id)" in src
