# SPDX-License-Identifier: Apache-2.0
"""Evaluation datasets: versioned reference cases, their rules, storage and endpoints."""

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
from sqlalchemy.exc import IntegrityError

from api.v1 import eval_datasets as api
from core.config import settings
from core.evals import datasets
from core.models.eval_dataset import EvalDataset, EvalDatasetVersion
from core.prompts import compare as prompt_compare
from core.schemas.api import EvalDatasetCreate, EvalDatasetRunIn, EvalDatasetVersionCreate

ROOT = Path(__file__).resolve().parents[2]
TENANT = uuid.uuid4()
ACTOR = uuid.uuid4()
CASES = [
    {"id": "refund", "input": "Can I get a refund after 40 days?", "contains": ["30 days"]},
    {"input": "What is the claim limit?", "matches": r"\d+", "not_contains": ["guarantee"]},
]


class _Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value

    def scalars(self) -> Any:
        return SimpleNamespace(all=lambda: list(self.value))


class _Session:
    """Answers each read from a script, in order, and records what was written."""

    def __init__(self, *answers: Any, fail_flush: bool = False) -> None:
        self.answers = list(answers)
        self.statements: list[str] = []
        self.added: list[Any] = []
        self.flushes = 0
        self.fail_flush = fail_flush

    def _next(self, statement: Any) -> Any:
        self.statements.append(str(statement))
        assert self.answers, f"unexpected read: {statement}"
        return self.answers.pop(0)

    async def execute(self, statement: Any) -> _Result:
        return _Result(self._next(statement))

    async def scalar(self, statement: Any) -> Any:
        return self._next(statement)

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        self.flushes += 1
        if self.fail_flush:
            raise IntegrityError("insert", {}, Exception("duplicate key"))


def _dataset(**over: Any) -> EvalDataset:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "name": "Claims answers",
        "description": None,
        "latest_version": 1,
        "case_count": 2,
        "created_by_user": ACTOR,
        "created_at": datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
        "archived_at": None,
    }
    base.update(over)
    return EvalDataset(**base)


def _version(dataset: EvalDataset, number: int = 1, cases: list | None = None) -> EvalDatasetVersion:
    stored, content_hash = datasets.normalise_cases(cases or CASES)
    return EvalDatasetVersion(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        dataset_id=dataset.id,
        version=number,
        cases=stored,
        case_count=len(stored),
        content_hash=content_hash,
        note=None,
        created_by_user=ACTOR,
        created_at=datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
    )


def _refused(coroutine: Any) -> datasets.DatasetError:
    with pytest.raises(datasets.DatasetError) as refused:
        asyncio.run(coroutine)
    return refused.value


class TestCases:
    def test_cases_are_stored_in_a_canonical_form_with_a_hash(self):
        stored, content_hash = datasets.normalise_cases(CASES)
        assert stored == [
            {"id": "refund", "input": "Can I get a refund after 40 days?", "contains": ["30 days"]},
            {"id": "2", "input": "What is the claim limit?", "not_contains": ["guarantee"], "matches": r"\d+"},
        ]
        assert len(content_hash) == 64
        # The same cases written with different key order hash the same; a changed case does not.
        reordered = [dict(reversed(list(case.items()))) for case in CASES]
        assert datasets.normalise_cases(reordered)[1] == content_hash
        changed = [{**CASES[0], "contains": ["31 days"]}, CASES[1]]
        assert datasets.normalise_cases(changed)[1] != content_hash
        # What is stored can be parsed again by the scorer, unchanged.
        assert datasets.normalise_cases(stored) == (stored, content_hash)

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ([], "non-empty list"),
            ("cases", "non-empty list"),
            ([{"input": "no expectation"}], "needs at least one of"),
            ([{"input": "x", "contains": ["a"], "weight": 2}], "unknown keys: weight"),
            ([{"id": "a", "input": "x", "equals": "y"}, {"id": "a", "input": "z", "equals": "y"}], "distinct"),
            ([{"input": "x", "matches": "(a+)+$"}], "."),
            ([{"input": "x", "equals": "y"}] * (datasets.MAX_CASES + 1), f"at most {datasets.MAX_CASES} cases"),
        ],
    )
    def test_cases_that_could_not_be_run_are_refused(self, raw, message):
        with pytest.raises(datasets.DatasetError, match=message) as refused:
            datasets.normalise_cases(raw)
        assert (refused.value.status, refused.value.code) == (422, "invalid_cases")

    def test_a_dataset_holds_more_cases_than_one_run_and_a_bounded_amount_of_text(self, monkeypatch):
        many = [{"input": f"question {index}", "equals": "yes"} for index in range(prompt_compare.MAX_CASES + 5)]
        assert len(datasets.normalise_cases(many)[0]) == prompt_compare.MAX_CASES + 5
        # The run limit is unchanged for a direct evaluation.
        with pytest.raises(ValueError, match=f"at most {prompt_compare.MAX_CASES} cases"):
            prompt_compare.parse_cases(many)
        monkeypatch.setattr(datasets, "MAX_VERSION_BYTES", 200)
        with pytest.raises(datasets.DatasetError, match="at most 200 bytes"):
            datasets.normalise_cases(many)


class TestCreate:
    def test_a_dataset_is_created_with_version_one(self):
        session = _Session(0, None)
        dataset, version = asyncio.run(
            datasets.create(
                session, TENANT, name="  Claims answers ", description="", cases=CASES, note="first", actor=ACTOR
            )
        )
        assert (dataset.name, dataset.description, dataset.latest_version, dataset.case_count) == (
            "Claims answers",
            None,
            1,
            2,
        )
        assert (version.version, version.case_count, version.note, version.created_by_user) == (1, 2, "first", ACTOR)
        assert version.dataset_id == dataset.id and version.tenant_id == dataset.tenant_id == TENANT
        assert version.content_hash == datasets.normalise_cases(CASES)[1]
        # The dataset row is written before the version that refers to it.
        assert session.added == [dataset, version] and session.flushes == 2
        count, taken = session.statements
        assert "count(" in count and "eval_datasets.tenant_id" in count
        assert "lower(eval_datasets.name)" in taken and "archived_at IS NULL" in taken

    @pytest.mark.parametrize(
        ("kwargs", "answers", "status", "code"),
        [
            ({"name": " "}, (), 422, "invalid"),
            ({"name": "x" * 121}, (), 422, "invalid"),
            ({"name": "ok", "description": "d" * 501}, (), 422, "invalid"),
            ({"name": "ok", "note": 7}, (), 422, "invalid"),
            ({"name": "ok", "cases": []}, (), 422, "invalid_cases"),
            ({"name": "ok"}, (datasets.MAX_DATASETS,), 409, "too_many"),
            ({"name": "ok"}, (3, uuid.uuid4()), 409, "name_taken"),
        ],
    )
    def test_refusals_write_nothing(self, kwargs, answers, status, code):
        session = _Session(*answers)
        error = _refused(datasets.create(session, TENANT, **{"cases": CASES, **kwargs}))
        assert (error.status, error.code) == (status, code) and session.added == []

    def test_two_creations_of_one_name_at_once_are_decided_by_the_index(self):
        error = _refused(datasets.create(_Session(0, None, fail_flush=True), TENANT, name="ok", cases=CASES))
        assert (error.status, error.code) == (409, "name_taken")


class TestVersions:
    def test_new_cases_become_the_next_version_under_a_row_lock(self):
        dataset = _dataset()
        session = _Session(dataset, _version(dataset).content_hash)
        new_cases = [*CASES, {"input": "Is flood covered?", "equals": "No"}]
        updated, version = asyncio.run(
            datasets.add_version(
                session, TENANT, dataset.id, cases=new_cases, note="adds flood", actor=ACTOR, expected_latest=1
            )
        )
        assert updated is dataset and (dataset.latest_version, dataset.case_count) == (2, 3)
        assert (version.version, version.case_count, version.note) == (2, 3, "adds flood")
        assert session.added == [version]
        assert "FOR UPDATE" in session.statements[0] and "eval_datasets.tenant_id" in session.statements[0]

    def test_identical_cases_are_not_stored_again(self):
        dataset = _dataset()
        session = _Session(dataset, _version(dataset).content_hash)
        error = _refused(datasets.add_version(session, TENANT, dataset.id, cases=CASES))
        assert (error.status, error.code) == (409, "unchanged")
        assert session.added == [] and dataset.latest_version == 1

    def test_a_writer_who_edited_an_older_version_is_told(self):
        dataset = _dataset(latest_version=3)
        error = _refused(datasets.add_version(_Session(dataset), TENANT, dataset.id, cases=CASES, expected_latest=2))
        assert (error.status, error.code) == (409, "stale") and "version 3, not 2" in error.message

    def test_an_archived_or_unknown_dataset_takes_no_version(self):
        archived = _dataset(archived_at=datetime(2026, 10, 5, tzinfo=UTC))
        error = _refused(datasets.add_version(_Session(archived), TENANT, archived.id, cases=CASES))
        assert (error.status, error.code) == (409, "archived")
        error = _refused(datasets.add_version(_Session(None), TENANT, uuid.uuid4(), cases=CASES))
        assert (error.status, error.code) == (404, "not_found")

    def test_reading_a_version_defaults_to_the_latest(self):
        dataset = _dataset(latest_version=2)
        second = _version(dataset, 2)
        session = _Session(dataset, second)
        assert asyncio.run(datasets.get_version(session, TENANT, dataset.id)) is second
        assert "eval_dataset_versions.version = " in session.statements[1]
        error = _refused(datasets.get_version(_Session(dataset, None), TENANT, dataset.id, 9))
        assert (error.status, error.code) == (404, "version_not_found")

    def test_lists_are_tenant_scoped_and_hide_archived_datasets_unless_asked(self):
        dataset = _dataset()
        session = _Session([dataset])
        assert asyncio.run(datasets.list_datasets(session, TENANT)) == [dataset]
        assert "eval_datasets.tenant_id" in session.statements[0] and "archived_at IS NULL" in session.statements[0]
        session = _Session([dataset])
        asyncio.run(datasets.list_datasets(session, TENANT, include_archived=True))
        assert "archived_at IS NULL" not in session.statements[0]
        versions = _Session(dataset, [_version(dataset)])
        assert len(asyncio.run(datasets.list_versions(versions, TENANT, dataset.id))) == 1
        assert "eval_dataset_versions.tenant_id" in versions.statements[1] and "DESC" in versions.statements[1]
        assert asyncio.run(datasets.get_dataset(_Session(dataset), TENANT, dataset.id)) is dataset

    def test_archiving_is_repeatable_and_keeps_the_first_time(self):
        dataset = _dataset()
        asyncio.run(datasets.archive(_Session(dataset), TENANT, dataset.id))
        first = dataset.archived_at
        assert first is not None
        asyncio.run(datasets.archive(_Session(dataset), TENANT, dataset.id))
        assert dataset.archived_at == first

    def test_what_is_returned(self):
        dataset = _dataset()
        version = _version(dataset)
        described = datasets.dataset_dict(dataset)
        assert described["name"] == "Claims answers" and described["latest_version"] == 1
        assert described["archived_at"] is None and described["created_by_user"] == str(ACTOR)
        assert "cases" not in datasets.version_dict(version)
        assert datasets.version_dict(version, with_cases=True)["cases"] == version.cases


class TestEndpoints:
    @pytest.fixture
    def on(self, monkeypatch):
        monkeypatch.setattr(settings, "evals_v2_enabled", True)

    @pytest.fixture
    def store(self, monkeypatch):
        """Route the endpoints' session to a scripted one; ``store(*answers)`` returns it."""
        holder: dict[str, _Session] = {}

        @asynccontextmanager
        async def _session(_tenant):
            yield holder["session"]

        monkeypatch.setattr(api, "get_tenant_session", _session)

        def install(*answers: Any) -> _Session:
            holder["session"] = _Session(*answers)
            return holder["session"]

        return install

    def test_off_by_default_nothing_is_read_or_written(self):
        assert settings.evals_v2_enabled is False
        listed = asyncio.run(api.list_eval_datasets(tenant_id=str(TENANT)))
        assert listed["enabled"] is False and listed["datasets"] == []
        calls = [
            api.create_eval_dataset(EvalDatasetCreate(name="x", cases=CASES), tenant_id=str(TENANT), user={}),
            api.get_eval_dataset(uuid.uuid4(), tenant_id=str(TENANT)),
            api.get_eval_dataset_version(uuid.uuid4(), 1, tenant_id=str(TENANT)),
            api.add_eval_dataset_version(
                uuid.uuid4(), EvalDatasetVersionCreate(cases=CASES), tenant_id=str(TENANT), user={}
            ),
            api.archive_eval_dataset(uuid.uuid4(), tenant_id=str(TENANT)),
            api.run_eval_dataset(uuid.uuid4(), EvalDatasetRunIn(system="s", model="m"), tenant_id=str(TENANT)),
        ]
        for call in calls:
            with pytest.raises(HTTPException) as refused:
                asyncio.run(call)
            assert refused.value.status_code == 409 and "off in this deployment" in refused.value.detail

    def test_every_route_is_for_tenant_administrators(self):
        src = (ROOT / "api" / "v1" / "eval_datasets.py").read_text(encoding="utf-8")
        assert src.count("@router.") == 7 == src.count("dependencies=[require_tenant_admin])")

    def test_create_list_read_and_archive(self, on, store):
        session = store(0, None)
        created = asyncio.run(
            api.create_eval_dataset(
                EvalDatasetCreate(name="Claims answers", cases=CASES),
                tenant_id=str(TENANT),
                user={"agenticorg:user_id": str(ACTOR), "sub": "someone@example.com"},
            )
        )
        assert created["latest_version"] == 1 and created["version"]["version"] == 1
        assert created["created_by_user"] == str(ACTOR) and "cases" not in created["version"]
        dataset = session.added[0]
        version = session.added[1]

        store([dataset])
        listed = asyncio.run(api.list_eval_datasets(tenant_id=str(TENANT)))
        assert listed["enabled"] is True and [row["name"] for row in listed["datasets"]] == ["Claims answers"]
        assert listed["limits"] == {"datasets": 200, "cases": 200, "cases_per_run": 25}

        store(dataset, dataset, [version])
        read = asyncio.run(api.get_eval_dataset(dataset.id, tenant_id=str(TENANT)))
        assert [item["version"] for item in read["versions"]] == [1] and "cases" not in read["versions"][0]

        store(dataset, version)
        with_cases = asyncio.run(api.get_eval_dataset_version(dataset.id, 1, tenant_id=str(TENANT)))
        assert with_cases["cases"] == version.cases

        store(dataset, version.content_hash)
        added = asyncio.run(
            api.add_eval_dataset_version(
                dataset.id,
                EvalDatasetVersionCreate(cases=[*CASES, {"input": "Is flood covered?", "equals": "No"}]),
                tenant_id=str(TENANT),
                user={"sub": str(uuid.uuid4())},
            )
        )
        # ``sub`` is not a local user id: no author is recorded from it.
        assert added["version"]["version"] == 2 and added["version"]["created_by_user"] is None

        store(dataset)
        assert asyncio.run(api.archive_eval_dataset(dataset.id, tenant_id=str(TENANT)))["archived_at"]

    def test_refusals_carry_a_code(self, on, store):
        store(None)
        with pytest.raises(HTTPException) as missing:
            asyncio.run(api.get_eval_dataset(uuid.uuid4(), tenant_id=str(TENANT)))
        assert missing.value.status_code == 404 and missing.value.detail["error"] == "not_found"
        store(3, uuid.uuid4())
        with pytest.raises(HTTPException) as taken:
            asyncio.run(
                api.create_eval_dataset(EvalDatasetCreate(name="x", cases=CASES), tenant_id=str(TENANT), user={})
            )
        assert taken.value.status_code == 409 and taken.value.detail["error"] == "name_taken"
        for call in (
            api.get_eval_dataset_version(uuid.uuid4(), 1, tenant_id=str(TENANT)),
            api.add_eval_dataset_version(
                uuid.uuid4(), EvalDatasetVersionCreate(cases=CASES), tenant_id=str(TENANT), user={}
            ),
            api.archive_eval_dataset(uuid.uuid4(), tenant_id=str(TENANT)),
        ):
            store(None)
            with pytest.raises(HTTPException) as refused:
                asyncio.run(call)
            assert refused.value.status_code == 404

    def test_the_author_is_the_local_user_id_only(self):
        assert api._actor({"user_id": str(ACTOR)}) == ACTOR
        assert api._actor({"agenticorg:user_id": "not-a-uuid", "user_id": str(ACTOR)}) == ACTOR
        assert api._actor({"sub": str(ACTOR)}) is None and api._actor(None) is None


class TestRun:
    @pytest.fixture
    def ready(self, monkeypatch):
        monkeypatch.setattr(settings, "evals_v2_enabled", True)
        monkeypatch.setattr(settings, "prompt_compare_enabled", True)
        dataset = _dataset(latest_version=2, case_count=30)
        many = [{"id": f"c{index}", "input": f"question {index}", "equals": "yes"} for index in range(30)]
        version = _version(dataset, 2, many)
        seen: dict[str, Any] = {}

        @asynccontextmanager
        async def _session(_tenant):
            yield _Session(dataset, version)

        async def _evaluate(tenant_id, *, variants, cases, model, max_tokens):
            seen.update(tenant=tenant_id, variants=variants, cases=cases, model=model, max_tokens=max_tokens)
            return {
                "model": model,
                "max_tokens": max_tokens or 512,
                "variants": [
                    {
                        "name": "prompt",
                        "cases": len(cases),
                        "passed": len(cases) - 1,
                        "failed": 1,
                        "errors": 0,
                        "pass_rate": round((len(cases) - 1) / len(cases), 4),
                        "avg_latency_ms": 12,
                        "cost_usd": 0.001,
                        "results": [{"id": case.id, "result": "passed"} for case in cases],
                    }
                ],
            }

        monkeypatch.setattr(api, "get_tenant_session", _session)
        monkeypatch.setattr(prompt_compare, "evaluate", _evaluate)
        return SimpleNamespace(dataset=dataset, version=version, seen=seen)

    def test_a_run_scores_one_slice_and_names_the_version_it_measured(self, ready):
        report = asyncio.run(
            api.run_eval_dataset(
                ready.dataset.id,
                EvalDatasetRunIn(system="You answer claims questions.", model="gpt-4o", offset=25),
                tenant_id=str(TENANT),
            )
        )
        assert [case.id for case in ready.seen["cases"]] == [f"c{index}" for index in range(25, 30)]
        assert ready.seen["variants"] == [("prompt", "You answer claims questions.")]
        assert ready.seen["tenant"] == TENANT and ready.seen["model"] == "gpt-4o"
        assert (report["version"], report["content_hash"]) == (2, ready.version.content_hash)
        assert (report["cases_total"], report["offset"], report["cases_run"], report["complete"]) == (30, 25, 5, False)
        assert report["passed"] == 4 and report["pass_rate"] == 0.8 and "name" not in report
        # Never an answer or an input: ids and outcomes only.
        assert all(set(result) == {"id", "result"} for result in report["results"])

    def test_a_version_that_fits_one_request_is_reported_complete(self, ready):
        ready.version.cases = ready.version.cases[:3]
        ready.version.case_count = 3
        report = asyncio.run(
            api.run_eval_dataset(ready.dataset.id, EvalDatasetRunIn(system="s", model="gpt-4o"), tenant_id=str(TENANT))
        )
        assert (report["cases_run"], report["complete"]) == (3, True)

    def test_a_slice_past_the_end_makes_no_model_call(self, ready):
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                api.run_eval_dataset(
                    ready.dataset.id, EvalDatasetRunIn(system="s", model="gpt-4o", offset=30), tenant_id=str(TENANT)
                )
            )
        assert refused.value.status_code == 422 and ready.seen == {}

    def test_a_run_needs_prompt_evaluation_to_be_on(self, ready, monkeypatch):
        monkeypatch.setattr(settings, "prompt_compare_enabled", False)
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                api.run_eval_dataset(
                    ready.dataset.id, EvalDatasetRunIn(system="s", model="gpt-4o"), tenant_id=str(TENANT)
                )
            )
        assert refused.value.status_code == 409 and ready.seen == {}

    def test_a_refused_model_or_failed_pseudonymisation_is_reported_not_raised(self, ready, monkeypatch):
        from core.pii.pseudonymiser import PseudonymisationError

        async def _unknown_model(*_args, **_kwargs):
            raise ValueError("unknown model")

        monkeypatch.setattr(prompt_compare, "evaluate", _unknown_model)
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                api.run_eval_dataset(
                    ready.dataset.id, EvalDatasetRunIn(system="s", model="nope"), tenant_id=str(TENANT)
                )
            )
        assert refused.value.status_code == 422

        async def _no_pseudonymiser(*_args, **_kwargs):
            raise PseudonymisationError("vault unavailable")

        monkeypatch.setattr(prompt_compare, "evaluate", _no_pseudonymiser)
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                api.run_eval_dataset(
                    ready.dataset.id, EvalDatasetRunIn(system="s", model="gpt-4o"), tenant_id=str(TENANT)
                )
            )
        assert refused.value.status_code == 503 and "no model was called" in refused.value.detail


class TestMigration:
    def test_both_tables_are_tenant_scoped_and_versions_cannot_be_changed(self):
        src = (ROOT / "migrations" / "versions" / "v6_z44_eval_datasets.py").read_text(encoding="utf-8")
        assert 'down_revision = "v6z43_prompt_change_requests"' in src
        assert '_TABLES = ("eval_datasets", "eval_dataset_versions")' in src
        assert "FORCE ROW LEVEL SECURITY" in src and "WITH CHECK (tenant_id::text = current_setting(" in src
        # The foreign key has an index that leads with it.
        assert "ON eval_dataset_versions(dataset_id, version);" in src
        assert "BEFORE UPDATE ON eval_dataset_versions" in src
        assert "ON eval_datasets(tenant_id, lower(name)) WHERE archived_at IS NULL;" in src
