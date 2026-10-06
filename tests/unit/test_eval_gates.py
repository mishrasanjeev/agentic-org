# SPDX-License-Identifier: Apache-2.0
"""Promotion gates and the model comparison: a prompt is promoted only when its evaluation says so."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from api.v1 import agents as agents_api
from api.v1 import eval_datasets as api
from core.config import settings
from core.evals import datasets, gates, runs
from core.models.eval_run import EvalRun
from core.schemas.api import AgentEvalGateIn, EvalDatasetRunIn

ROOT = Path(__file__).resolve().parents[2]
TENANT = uuid.uuid4()
DATASET = uuid.uuid4()
PROMPT = "Decide the claim in one sentence."
T0 = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)


class _Session:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.statements: list[str] = []
        self.added: list[Any] = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        value = self.answers.pop(0)
        return SimpleNamespace(
            scalar_one_or_none=lambda: value, scalars=lambda: SimpleNamespace(all=lambda: list(value))
        )

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None


def _version(number: int = 2) -> SimpleNamespace:
    return SimpleNamespace(version=number, content_hash="h" * 64, dataset_id=DATASET)


def _agent(gate: dict | None = None, text: str = PROMPT, **over) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "name": "Claims decider",
        "status": "shadow",
        "system_prompt_text": text,
        "config": {"eval_gate": gate} if gate else {},
    }
    base.update(over)
    return SimpleNamespace(**base)


def _run(pass_rate, *, prompt=PROMPT, model="gpt-4o", at=T0, tokens=1200, cost=0.02, cases=10, latency=400, **over):
    base = {
        "id": uuid.uuid4(),
        "dataset_id": DATASET,
        "version": 2,
        "prompt_hash": runs.prompt_hash(prompt),
        "prompt_label": None,
        "model": model,
        "pass_rate": pass_rate,
        "cases_run": cases,
        "cases_total": cases,
        "offset": 0,
        "avg_latency_ms": latency,
        "tokens": tokens,
        "cost_usd": cost,
        "metrics": {"classification": {"accuracy": pass_rate}},
        "scores": {"relevance": {"cases": cases, "mean": 0.8, "errors": 0}},
        "created_at": at,
    }
    base.update(over)
    return SimpleNamespace(**base)


GATE = {"dataset_id": str(DATASET), "version": None, "min_pass_rate": 80, "max_regression": 5}


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(settings, "evals_v2_enabled", True)
    monkeypatch.setattr(settings, "eval_promotion_gate_enabled", True)


class TestGateShape:
    def test_a_gate_takes_its_defaults(self):
        assert gates.parse_gate({"dataset_id": str(DATASET)}) == {
            "dataset_id": str(DATASET),
            "version": None,
            "min_pass_rate": 100,
            "max_regression": 0,
        }
        assert gates.parse_gate(GATE) == GATE

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ("gate", "must be an object"),
            ({"dataset_id": "nope"}, "dataset_id must be"),
            ({"dataset_id": str(DATASET), "version": 0}, "version is a whole number"),
            ({"dataset_id": str(DATASET), "min_pass_rate": 101}, "between 0 and 100"),
            ({"dataset_id": str(DATASET), "max_regression": True}, "between 0 and 100"),
            ({"dataset_id": str(DATASET), "threshold": 1}, "unknown gate keys"),
        ],
    )
    def test_refused_shapes(self, raw, message):
        with pytest.raises(datasets.DatasetError, match=message):
            gates.parse_gate(raw)

    def test_off_by_default_a_gate_is_not_enforced(self):
        assert settings.eval_promotion_gate_enabled is False and gates.enabled() is False
        assert gates.declared(_agent()) is None and gates.declared(_agent(GATE)) == GATE


class TestEvaluate:
    def _verdict(self, agent, *answers, monkeypatch=None):
        session = _Session(*answers)
        return asyncio.run(gates.evaluate(session, TENANT, agent)), session

    def test_an_agent_without_a_gate_passes(self, enforced):
        verdict, session = self._verdict(_agent())
        assert verdict.to_dict() == {
            "declared": False,
            "enforced": True,
            "ok": True,
            "code": "no_gate",
            "message": "No evaluation gate",
            "detail": {},
        }
        assert session.statements == []

    def test_a_prompt_that_was_never_run_is_not_promoted(self, enforced, monkeypatch):
        monkeypatch.setattr(datasets, "get_version", _fake_version())
        verdict, session = self._verdict(_agent(GATE), None)
        assert (verdict.ok, verdict.code) == (False, "not_evaluated")
        assert verdict.detail["prompt_hash"] == runs.prompt_hash(PROMPT) and verdict.detail["version"] == 2
        # The newest run of this version with this prompt hash.
        statement = session.statements[0]
        assert "eval_runs.prompt_hash = " in statement and "eval_runs.version = " in statement and "DESC" in statement

    def test_the_newest_run_of_the_prompt_is_held_to_the_minimum(self, enforced, monkeypatch):
        monkeypatch.setattr(datasets, "get_version", _fake_version())
        verdict, _ = self._verdict(_agent(GATE), _run(0.7))
        assert (verdict.ok, verdict.code) == (False, "below_minimum") and "70%" in verdict.message
        verdict, _ = self._verdict(_agent(GATE), _run(None))
        assert (verdict.ok, verdict.code) == (False, "not_scored")

    def test_a_regression_against_the_prompt_it_replaces_is_refused_within_the_allowance(self, enforced, monkeypatch):
        monkeypatch.setattr(datasets, "get_version", _fake_version())
        previous = _run(0.95, prompt="The old prompt.")
        verdict, session = self._verdict(_agent(GATE), _run(0.85), previous)
        assert (verdict.ok, verdict.code) == (False, "regressed")
        assert verdict.detail["previous_pass_rate"] == 0.95 and verdict.detail["previous_run_id"] == str(previous.id)
        assert "eval_runs.prompt_hash != " in session.statements[1]
        # Within five points, or with no earlier prompt, the gate is passed.
        verdict, _ = self._verdict(_agent(GATE), _run(0.9), previous)
        assert verdict.ok and verdict.code == "passed"
        verdict, _ = self._verdict(_agent(GATE), _run(0.9), None)
        assert verdict.ok

    def test_a_gate_that_cannot_be_read_or_an_agent_without_text_is_not_passed(self, enforced, monkeypatch):
        async def _missing(_session, _tenant, _dataset, _version):
            raise datasets.DatasetError(404, "not_found", "Evaluation dataset not found")

        monkeypatch.setattr(datasets, "get_version", _missing)
        verdict, _ = self._verdict(_agent(GATE))
        assert (verdict.ok, verdict.code) == (False, "gate_unusable")
        monkeypatch.setattr(datasets, "get_version", _fake_version())
        verdict, _ = self._verdict(_agent(GATE, text=""))
        assert (verdict.ok, verdict.code) == (False, "no_prompt_text")

    def test_promotion_raises_only_while_the_gate_is_enforced(self, monkeypatch):
        monkeypatch.setattr(settings, "evals_v2_enabled", True)
        monkeypatch.setattr(datasets, "get_version", _fake_version())
        # Off: the verdict says the gate is not passed, promotion is not held.
        verdict = asyncio.run(gates.check_promotion(_Session(_run(0.5)), TENANT, _agent(GATE)))
        assert verdict.ok is False and verdict.enforced is False
        monkeypatch.setattr(settings, "eval_promotion_gate_enabled", True)
        with pytest.raises(gates.GateError) as refused:
            asyncio.run(gates.check_promotion(_Session(_run(0.5)), TENANT, _agent(GATE)))
        assert refused.value.code == "below_minimum" and refused.value.detail["pass_rate"] == 0.5
        passed = asyncio.run(gates.check_promotion(_Session(_run(0.9), None), TENANT, _agent(GATE)))
        assert passed.ok


def _fake_version(number: int = 2):
    async def _get(_session, _tenant, _dataset, version):
        return _version(version or number)

    return _get


class TestPromotionPaths:
    def test_promote_and_resume_to_active_pass_the_gate_after_maker_checker(self):
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        assert src.count("await eval_gates.check_promotion(session, tid, agent)") == 2
        promote = src[src.index("async def promote_agent(") : src.index('"/agents/{agent_id}/retire"')]
        assert promote.index("prompt_activation.check_activation(") < promote.index("eval_gates.check_promotion(")
        assert promote.index("eval_gates.check_promotion(") < promote.index("agent.status = new_status")
        resume = src[src.index("resume_to = pause_event.from_status") : src.index("async def promote_agent(")]
        assert resume.index("prompt_activation.check_activation(") < resume.index("eval_gates.check_promotion(")
        assert resume.index("eval_gates.check_promotion(") < resume.index("agent.status = resume_to")

    def test_a_refused_promotion_names_the_gate_and_the_numbers(self):
        exc = gates.GateError("below_minimum", "Pass rate 50% is below the gate's 80%", {"pass_rate": 0.5})
        refused = agents_api._gate_refused(exc)
        assert refused.status_code == 409
        assert refused.detail == {
            "error": "evaluation_gate",
            "code": "below_minimum",
            "message": "Pass rate 50% is below the gate's 80%",
            "pass_rate": 0.5,
        }


class TestGateEndpoints:
    @pytest.fixture
    def store(self, monkeypatch):
        holder: dict[str, _Session] = {}

        @asynccontextmanager
        async def _session(_tenant):
            yield holder["session"]

        monkeypatch.setattr(agents_api, "get_tenant_session", _session)
        monkeypatch.setattr(agents_api, "require_agent_mutable", lambda _agent, _caller: None)
        monkeypatch.setattr(agents_api, "require_agent_visible", lambda _agent, _caller: None)
        monkeypatch.setattr(datasets, "get_version", _fake_version())

        def install(*answers):
            holder["session"] = _Session(*answers)
            return holder["session"]

        return install

    def test_a_gate_is_stored_under_a_row_lock_and_the_verdict_is_returned(self, enforced, store):
        agent = _agent()
        session = store(agent, _run(0.9), None)
        result = asyncio.run(
            agents_api.set_agent_eval_gate(
                agent.id, AgentEvalGateIn(gate=GATE), tenant_id=str(TENANT), user_domains=None, caller=None
            )
        )
        assert agent.config["eval_gate"] == GATE and result["gate"] == GATE
        assert result["verdict"]["ok"] is True and result["verdict"]["enforced"] is True
        assert "FOR UPDATE" in session.statements[0]

    def test_null_removes_the_gate(self, enforced, store):
        agent = _agent(GATE)
        store(agent)
        result = asyncio.run(
            agents_api.set_agent_eval_gate(
                agent.id, AgentEvalGateIn(gate=None), tenant_id=str(TENANT), user_domains=None, caller=None
            )
        )
        assert "eval_gate" not in agent.config and result["gate"] is None and result["verdict"]["code"] == "no_gate"

    def test_a_gate_on_a_dataset_that_does_not_exist_is_refused(self, enforced, store, monkeypatch):
        async def _missing(_session, _tenant, _dataset, _version):
            raise datasets.DatasetError(404, "not_found", "Evaluation dataset not found")

        monkeypatch.setattr(datasets, "get_version", _missing)
        agent = _agent()
        store(agent)
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                agents_api.set_agent_eval_gate(
                    agent.id, AgentEvalGateIn(gate=GATE), tenant_id=str(TENANT), user_domains=None, caller=None
                )
            )
        assert refused.value.status_code == 404 and agent.config == {}
        with pytest.raises(HTTPException) as bad:
            asyncio.run(
                agents_api.set_agent_eval_gate(
                    agent.id,
                    AgentEvalGateIn(gate={"dataset_id": "x"}),
                    tenant_id=str(TENANT),
                    user_domains=None,
                    caller=None,
                )
            )
        assert bad.value.status_code == 422

    def test_the_gate_is_read_with_its_verdict(self, enforced, store):
        agent = _agent(GATE)
        store(agent, _run(0.7))
        result = asyncio.run(
            agents_api.get_agent_eval_gate(agent.id, tenant_id=str(TENANT), user_domains=None, caller=None)
        )
        assert result["gate"] == GATE and result["verdict"]["code"] == "below_minimum"
        store(None)
        with pytest.raises(HTTPException) as missing:
            asyncio.run(
                agents_api.get_agent_eval_gate(uuid.uuid4(), tenant_id=str(TENANT), user_domains=None, caller=None)
            )
        assert missing.value.status_code == 404


class TestRunWithAgentPrompt:
    @pytest.fixture
    def ready(self, monkeypatch):
        monkeypatch.setattr(settings, "evals_v2_enabled", True)
        monkeypatch.setattr(settings, "prompt_compare_enabled", True)
        stored, content_hash = datasets.normalise_cases([{"id": "a", "input": "q", "equals": "yes"}])
        version = SimpleNamespace(
            id=uuid.uuid4(),
            dataset_id=DATASET,
            version=2,
            cases=stored,
            case_count=1,
            content_hash=content_hash,
            note=None,
            created_by_user=None,
            created_at=None,
        )
        holder: dict[str, Any] = {}

        @asynccontextmanager
        async def _session(_tenant):
            yield holder["session"]

        asked: dict[str, Any] = {}

        async def _run_version(tenant_id, **kwargs):
            asked.update(kwargs)
            return {
                "dataset_id": str(DATASET),
                "version": 2,
                "content_hash": content_hash,
                "cases_total": 1,
                "offset": 0,
                "cases_run": 1,
                "cases": 1,
                "complete": True,
                "model": "gpt-4o",
                "judge_model": None,
                "judges": [],
                "prompt_hash": runs.prompt_hash(kwargs["system_text"]),
                "max_tokens": 512,
                "passed": 1,
                "failed": 0,
                "errors": 0,
                "pass_rate": 1.0,
                "avg_latency_ms": 10,
                "cost_usd": 0.001,
                "tokens": 30,
                "metrics": {},
                "scores": {},
                "results": [],
                "reasons": {},
            }

        monkeypatch.setattr(api, "get_tenant_session", _session)
        monkeypatch.setattr(datasets, "get_version", _fake_version_with(version))
        monkeypatch.setattr(runs, "run_version", _run_version)
        return SimpleNamespace(holder=holder, asked=asked)

    def test_an_agents_prompt_text_is_run_and_labelled_so_the_gate_can_match_it(self, ready):
        agent = _agent()
        session = ready.holder["session"] = _Session(agent)
        report = asyncio.run(
            api.run_eval_dataset(
                DATASET, EvalDatasetRunIn(agent_id=agent.id, model="gpt-4o"), tenant_id=str(TENANT), user={}
            )
        )
        assert ready.asked["system_text"] == PROMPT and report["prompt_hash"] == runs.prompt_hash(PROMPT)
        [stored] = session.added
        assert isinstance(stored, EvalRun) and stored.prompt_label == "agent:Claims decider" and stored.tokens == 30

    def test_the_prompt_comes_from_the_text_or_the_agent_not_both_or_neither(self, ready):
        for body in (
            EvalDatasetRunIn(model="gpt-4o"),
            EvalDatasetRunIn(system="s", agent_id=uuid.uuid4(), model="gpt-4o"),
        ):
            ready.holder["session"] = _Session()
            with pytest.raises(HTTPException) as refused:
                asyncio.run(api.run_eval_dataset(DATASET, body, tenant_id=str(TENANT), user={}))
            assert refused.value.status_code == 422 and ready.asked == {}
        ready.holder["session"] = _Session(None)
        with pytest.raises(HTTPException) as missing:
            asyncio.run(
                api.run_eval_dataset(
                    DATASET, EvalDatasetRunIn(agent_id=uuid.uuid4(), model="gpt-4o"), tenant_id=str(TENANT), user={}
                )
            )
        assert missing.value.status_code == 404
        ready.holder["session"] = _Session(_agent(text=" "))
        with pytest.raises(HTTPException) as empty:
            asyncio.run(
                api.run_eval_dataset(
                    DATASET, EvalDatasetRunIn(agent_id=uuid.uuid4(), model="gpt-4o"), tenant_id=str(TENANT), user={}
                )
            )
        assert empty.value.status_code == 422 and "no prompt text" in empty.value.detail


def _fake_version_with(version):
    async def _get(_session, _tenant, _dataset, _number):
        return version

    return _get


class TestComparison:
    def test_models_are_ranked_from_their_newest_runs(self):
        older_fast = _run(0.9, model="gpt-4o-mini", at=T0 - timedelta(hours=2), latency=200, tokens=800, cost=0.004)
        newest_fast = _run(0.8, model="gpt-4o-mini", at=T0, latency=250, tokens=900, cost=0.005)
        strong = _run(0.9, model="gpt-4o", at=T0 - timedelta(hours=1), latency=600, tokens=1500, cost=0.03)
        other = _run(0.9, model="claude-sonnet", at=T0 - timedelta(hours=3), latency=600, tokens=1400, cost=0.02)
        unscored = _run(None, model="broken", at=T0, latency=0, tokens=0, cost=0.0)
        rows = runs.rank_models([newest_fast, older_fast, strong, other, unscored])
        assert [(row["rank"], row["model"]) for row in rows] == [
            (1, "claude-sonnet"),
            (2, "gpt-4o"),
            (3, "gpt-4o-mini"),
            (4, "broken"),
        ]
        mini = rows[2]
        # The newest run of a model is the one compared, not its best.
        assert mini["run_id"] == str(newest_fast.id) and mini["pass_rate"] == 0.8
        assert mini["answers_per_minute"] == 240.0 and mini["tokens_per_case"] == 90.0
        assert mini["cost_per_case_usd"] == 0.0005 and mini["accuracy"] == 0.8 and mini["scores"] == {"relevance": 0.8}
        assert rows[3]["answers_per_minute"] is None and rows[3]["tokens_per_case"] == 0.0

    def test_the_comparison_endpoint_reads_the_version_and_its_stored_runs(self, monkeypatch):
        monkeypatch.setattr(settings, "evals_v2_enabled", True)
        monkeypatch.setattr(datasets, "get_version", _fake_version())
        session = _Session([_run(0.9), _run(0.7, model="gpt-4o-mini")])

        @asynccontextmanager
        async def _session(_tenant):
            yield session

        monkeypatch.setattr(api, "get_tenant_session", _session)
        result = asyncio.run(api.compare_eval_models(DATASET, version=None, tenant_id=str(TENANT)))
        assert result["version"] == 2 and [row["model"] for row in result["models"]] == ["gpt-4o", "gpt-4o-mini"]
        statement = session.statements[0]
        assert "eval_runs.tenant_id" in statement and "eval_runs.version = " in statement

    def test_off_nothing_is_read(self):
        assert settings.evals_v2_enabled is False
        with pytest.raises(HTTPException) as refused:
            asyncio.run(api.compare_eval_models(DATASET, version=None, tenant_id=str(TENANT)))
        assert refused.value.status_code == 409


class TestMigration:
    def test_tokens_are_added_to_stored_runs(self):
        src = (ROOT / "migrations" / "versions" / "v6_z47_eval_run_tokens.py").read_text(encoding="utf-8")
        assert 'down_revision = "v6z46_synthetic_check_kinds"' in src
        assert "ADD COLUMN IF NOT EXISTS tokens INTEGER NOT NULL DEFAULT 0" in src
