# SPDX-License-Identifier: Apache-2.0
"""Registry approval workflow, environments and the traffic split."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.agent_registry import approval, lifecycle, traffic
from core.config import settings
from core.models.agent_registry import AgentRegistryEntry

ROOT = Path(__file__).resolve().parents[2]
TENANT = uuid.uuid4()
ACTOR = uuid.uuid4()


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


def _agent(status="shadow", **over):
    base = {"id": uuid.uuid4(), "status": status, "config": {}}
    base.update(over)
    return SimpleNamespace(**base)


def _entry(agent, state) -> AgentRegistryEntry:
    return AgentRegistryEntry(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        agent_id=agent.id,
        purpose=None,
        risk_tier=None,
        use_case=None,
        channels=[],
        state=state,
        state_changed_at=datetime(2026, 10, 7, tzinfo=UTC),
        state_changed_by=None,
        submitted_by=None,
        created_at=datetime(2026, 10, 7, tzinfo=UTC),
        updated_at=datetime(2026, 10, 7, tzinfo=UTC),
    )


@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setattr(settings, "agent_registry_enabled", True)
    monkeypatch.setattr(settings, "agent_registry_gates_promotion", True)


class TestApproval:
    def test_off_by_default_the_registry_neither_gates_nor_follows(self):
        assert settings.agent_registry_gates_promotion is False and approval.gates_promotion() is False
        session = _Session()
        agent = _agent()
        assert asyncio.run(approval.check_promotion(session, TENANT, agent)) is None
        asyncio.run(approval.follow_promotion(session, TENANT, agent, actor=ACTOR))
        asyncio.run(approval.follow_retirement(session, TENANT, agent, actor=ACTOR))
        assert session.statements == [] and session.added == []

    def test_the_registry_switch_alone_does_not_gate(self, monkeypatch):
        monkeypatch.setattr(settings, "agent_registry_enabled", True)
        assert approval.gates_promotion() is False
        monkeypatch.setattr(settings, "agent_registry_gates_promotion", True)
        monkeypatch.setattr(settings, "agent_registry_enabled", False)
        assert approval.gates_promotion() is False

    @pytest.mark.parametrize("state", ["draft", "review", "deprecated", "retired"])
    def test_an_agent_that_is_not_approved_is_not_promoted(self, gated, state):
        agent = _agent()
        with pytest.raises(approval.ApprovalError) as refused:
            asyncio.run(approval.check_promotion(_Session(_entry(agent, state)), TENANT, agent))
        assert refused.value.code == "not_approved" and refused.value.state == state and state in refused.value.message

    def test_an_agent_without_an_entry_is_a_draft_and_is_not_promoted(self, gated):
        with pytest.raises(approval.ApprovalError) as refused:
            asyncio.run(approval.check_promotion(_Session(None), TENANT, _agent()))
        assert refused.value.state == "draft"

    @pytest.mark.parametrize("state", approval.PROMOTABLE_STATES)
    def test_an_approved_or_published_agent_is_promoted(self, gated, state):
        agent = _agent()
        assert asyncio.run(approval.check_promotion(_Session(_entry(agent, state)), TENANT, agent)) == state

    def test_promotion_publishes_an_approved_entry(self, gated):
        agent = _agent(status="active")
        entry = _entry(agent, "approved")
        session = _Session(entry, entry)
        asyncio.run(approval.follow_promotion(session, TENANT, agent, actor=ACTOR))
        assert entry.state == "published" and entry.state_changed_by == ACTOR
        [event] = session.added
        assert (event.from_state, event.to_state, event.note) == ("approved", "published", "Promoted to active")
        # Already published: nothing to record.
        session = _Session(_entry(agent, "published"))
        asyncio.run(approval.follow_promotion(session, TENANT, agent, actor=ACTOR))
        assert session.added == []

    def test_retirement_retires_a_published_entry_through_deprecated(self, gated):
        agent = _agent(status="retired")
        entry = _entry(agent, "published")
        session = _Session(entry, entry, entry)
        asyncio.run(approval.follow_retirement(session, TENANT, agent, actor=ACTOR))
        assert entry.state == "retired"
        assert [(event.from_state, event.to_state) for event in session.added] == [
            ("published", "deprecated"),
            ("deprecated", "retired"),
        ]
        # A draft that is retired at runtime keeps its registry state: it was never in production.
        session = _Session(_entry(agent, "draft"))
        asyncio.run(approval.follow_retirement(session, TENANT, agent, actor=ACTOR))
        assert session.added == []

    def test_environments_are_read_from_the_state(self):
        assert [approval.environment_of(state) for state in lifecycle.STATES] == [
            "development",
            "development",
            "staging",
            "production",
            "production",
            None,
        ]
        assert lifecycle.entry_dict(_entry(_agent(), "approved"))["environment"] == "staging"
        assert lifecycle.entry_dict(None)["environment"] == "development"


class TestPromotionPaths:
    def test_promote_resume_and_retire_consult_the_registry(self):
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        assert src.count("await registry_approval.check_promotion(session, tid, agent)") == 2
        assert src.count("await registry_approval.follow_promotion(session, tid, agent, actor=") == 2
        assert src.count("registry_approval.follow_retirement(") == 1
        promote = src[src.index("async def promote_agent(") : src.index('"/agents/{agent_id}/retire"')]
        # After the evaluation gate, before the status changes; the registry follows after the change.
        assert promote.index("eval_gates.check_promotion(") < promote.index("registry_approval.check_promotion(")
        assert promote.index("registry_approval.check_promotion(") < promote.index("agent.status = new_status")
        assert promote.index("agent.status = new_status") < promote.index("registry_approval.follow_promotion(")
        resume = src[src.index("resume_to = pause_event.from_status") : src.index("async def promote_agent(")]
        assert resume.index("registry_approval.check_promotion(") < resume.index("agent.status = resume_to")
        assert resume.index("agent.status = resume_to") < resume.index("registry_approval.follow_promotion(")
        retire = src[src.index("async def retire_agent(") :][:2500]
        assert retire.index('agent.status = "retired"') < retire.index("registry_approval.follow_retirement(")


class TestTrafficSplit:
    def test_a_split_names_another_agent_and_a_share(self):
        own = uuid.uuid4()
        other = uuid.uuid4()
        assert traffic.parse_split({"to_agent_id": str(other), "percent": 10}, own_id=own) == {
            "to_agent_id": str(other),
            "percent": 10,
        }
        for raw, message in (
            ("x", "must be an object"),
            ({"to_agent_id": "nope", "percent": 10}, "must be an agent id"),
            ({"to_agent_id": str(own), "percent": 10}, "to itself"),
            ({"to_agent_id": str(other), "percent": 0}, "between 1 and 100"),
            ({"to_agent_id": str(other), "percent": True}, "between 1 and 100"),
            ({"to_agent_id": str(other), "percent": 10, "sticky": True}, "unknown split keys"),
        ):
            with pytest.raises(traffic.TrafficError, match=message):
                traffic.parse_split(raw, own_id=own)

    def test_the_share_is_reproducible_from_the_correlation_id(self):
        assert traffic.bucket("run_abc") == traffic.bucket("run_abc") and 0 <= traffic.bucket("run_abc") < 100
        split = {"to_agent_id": str(uuid.uuid4()), "percent": 100}
        assert traffic.chooses_target(split, "anything") is True
        assert traffic.chooses_target({**split, "percent": 1}, None) in (True, False)
        # Over many ids the share is close to the percentage.
        hits = sum(traffic.chooses_target({**split, "percent": 30}, f"run_{index}") for index in range(2000))
        assert 500 < hits < 700

    def test_off_by_default_no_run_is_redirected(self):
        assert settings.agent_traffic_split_enabled is False
        target = _agent(status="active")
        agent = _agent(status="active", config={"traffic_split": {"to_agent_id": str(target.id), "percent": 100}})
        assert traffic.choose(agent, "run_1", lambda _id: target) == (agent, None)

    def test_on_the_target_serves_its_share_unless_it_is_not_active(self, monkeypatch):
        monkeypatch.setattr(settings, "agent_traffic_split_enabled", True)
        target = _agent(status="active")
        agent = _agent(status="active", config={"traffic_split": {"to_agent_id": str(target.id), "percent": 100}})
        assert traffic.choose(agent, "run_1", lambda _id: target) == (target, "traffic_split:100")
        asked: list = []

        def _load(target_id):
            asked.append(target_id)
            return target

        traffic.choose(agent, "run_1", _load)
        assert asked == [target.id]
        paused = _agent(status="paused")
        assert traffic.choose(agent, "run_1", lambda _id: paused) == (agent, None)
        assert traffic.choose(agent, "run_1", lambda _id: None) == (agent, None)
        none_declared = _agent(status="active")
        assert traffic.choose(none_declared, "run_1", lambda _id: target) == (none_declared, None)
        zero_share = _agent(status="active", config={"traffic_split": {"to_agent_id": str(target.id), "percent": 1}})
        # A bucket of 99 and a share of 1 stays on the agent asked for.
        monkeypatch.setattr(traffic, "bucket", lambda _cid: 99)
        assert traffic.choose(zero_share, "run_1", lambda _id: target) == (zero_share, None)
