# SPDX-License-Identifier: Apache-2.0
"""Sample governed cases seed (scripts/seed_governed_cases.py), without a database."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest

from scripts import seed_governed_cases as seed
from scripts.seed_dev import SeedError


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row


class _Session:
    """Enough session for the flag upsert: no row exists, so one is added."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flushes = 0

    async def execute(self, *_args: Any, **_kwargs: Any) -> _Result:
        return _Result(None)

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        self.flushes += 1


class _Runtime:
    def __init__(self) -> None:
        self.session = _Session()
        self.llm_model = ""

    def session_factory(self, _tenant_id: uuid.UUID) -> Any:
        @asynccontextmanager
        async def _open() -> Any:
            yield self.session

        return _open()

    def clock(self) -> Any:
        from datetime import UTC, datetime

        return datetime.now(UTC)


def test_fixture_list_is_parsed_and_an_empty_list_is_refused() -> None:
    assert seed.parse_fixtures(" gb-clean-brightwater , us-thin-file-brambleway ") == (
        "gb-clean-brightwater",
        "us-thin-file-brambleway",
    )
    with pytest.raises(SeedError):
        seed.parse_fixtures(" , ")


def test_unknown_fixture_is_refused_before_anything_is_written() -> None:
    with pytest.raises(SeedError, match="unknown mock fixture"):
        seed.load_applications(["gb-clean-brightwater", "not-a-fixture"])


def test_default_fixtures_cover_the_scenarios_the_console_screens_show() -> None:
    applications = seed.load_applications(seed.DEFAULT_FIXTURES)
    assert set(applications) == set(seed.DEFAULT_FIXTURES)
    for application in applications.values():
        assert application["legal_name"]
        assert application["jurisdiction"][:2] in {"GB", "US"}


async def test_seed_enables_the_flag_then_investigates_and_disposes_each_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.cases import runtime as case_runtime
    from core.cases import store as case_store

    created: list[str] = []
    investigated: list[str] = []
    disposed: list[str] = []

    async def fake_create_case(_session: Any, **kwargs: Any) -> Any:
        ref = f"case_{len(created):024x}"
        created.append(kwargs["application"]["legal_name"])
        return type("Case", (), {"case_ref": ref})()

    async def fake_investigate(_tenant: Any, case_ref: str, **_kwargs: Any) -> dict[str, Any]:
        investigated.append(case_ref)
        return {"state": "awaiting_decision", "tier": "medium", "screening_hits": 1}

    async def fake_dispose(_tenant: Any, case_ref: str, **_kwargs: Any) -> dict[str, Any]:
        disposed.append(case_ref)
        return {"proposed": 1, "outcomes": ["false_positive"], "failed": []}

    monkeypatch.setattr(case_store, "create_case", fake_create_case)
    monkeypatch.setattr(case_runtime, "investigate_case", fake_investigate)
    monkeypatch.setattr(case_runtime, "dispose_screening_hits", fake_dispose)

    runtime = _Runtime()
    summary = await seed.seed_cases(["gb-clean-brightwater", "us-false-positive-oakhollow"], runtime=runtime)

    flag = runtime.session.added[0]
    assert (flag.flag_key, flag.enabled, flag.rollout_percentage) == (seed.FLAG_KEY, True, 100)
    assert len(created) == 2
    assert investigated == disposed
    assert summary["cases"]["gb-clean-brightwater"]["dispositions_proposed"] == 1
    assert summary["tenant_id"] == str(seed.seed_id("tenant"))


async def test_a_refused_disposition_is_reported_and_does_not_stop_the_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.cases import runtime as case_runtime
    from core.cases import store as case_store
    from core.cases.states import CaseError

    async def fake_create_case(_session: Any, **_kwargs: Any) -> Any:
        return type("Case", (), {"case_ref": "case_000000000000000000000001"})()

    async def fake_investigate(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"state": "awaiting_decision", "screening_hits": 2}

    async def fake_dispose(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise CaseError("transition_not_allowed")

    monkeypatch.setattr(case_store, "create_case", fake_create_case)
    monkeypatch.setattr(case_runtime, "investigate_case", fake_investigate)
    monkeypatch.setattr(case_runtime, "dispose_screening_hits", fake_dispose)

    summary = await seed.seed_cases(["gb-clean-brightwater"], runtime=_Runtime())
    assert summary["cases"]["gb-clean-brightwater"]["dispositions_refused"] == "transition_not_allowed"


@pytest.mark.parametrize("runtime_env", ["", "production", "staging"])
def test_the_seed_refuses_a_production_like_runtime(runtime_env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_run(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("must not run against a production-like runtime")

    monkeypatch.setattr(seed.asyncio, "run", no_run)
    assert seed.main([], {"AGENTICORG_ENV": runtime_env}) == 2
