# SPDX-License-Identifier: Apache-2.0
"""The traffic split endpoints and the run path that applies a split."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from api.v1 import agents as agents_api
from core.agent_registry import approval, traffic
from core.config import settings
from core.schemas.api import AgentCardIn, AgentCloneRequest, AgentCreate, AgentLifecycleIn, AgentTrafficSplitIn

ROOT = Path(__file__).resolve().parents[2]
TENANT = uuid.uuid4()


class _Session:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        value = self.answers.pop(0)
        return SimpleNamespace(scalar_one_or_none=lambda: value)


def _agent(status="active", **over):
    base = {"id": uuid.uuid4(), "status": status, "config": {"temperature": 0.1}}
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def store(monkeypatch):
    holder: dict[str, _Session] = {}

    @asynccontextmanager
    async def _session(_tenant):
        yield holder["session"]

    monkeypatch.setattr(agents_api, "get_tenant_session", _session)
    monkeypatch.setattr(agents_api, "require_agent_mutable", lambda _agent, _caller: None)
    monkeypatch.setattr(agents_api, "require_agent_visible", lambda _agent, _caller: None)

    def install(*answers):
        holder["session"] = _Session(*answers)
        return holder["session"]

    return install


class TestEndpoints:
    def test_shadow_source_cannot_be_configured_to_run_active_target(self, store):
        source, target = _agent("shadow"), _agent()
        session = store(source, target)
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                agents_api.set_agent_traffic_split(
                    source.id,
                    AgentTrafficSplitIn(split={"to_agent_id": str(target.id), "percent": 100}),
                    tenant_id=str(TENANT),
                    user_domains=None,
                    caller=None,
                )
            )
        assert refused.value.status_code == 409
        assert "source" in refused.value.detail
        assert len(session.statements) == 1
        assert "traffic_split" not in source.config

    def test_target_visibility_is_required_at_configuration(self, store, monkeypatch):
        source, target = _agent(), _agent()
        store(source, target)
        check = Mock(side_effect=HTTPException(404, "Agent not found"))
        monkeypatch.setattr(agents_api, "require_agent_visible", check)
        with pytest.raises(HTTPException):
            asyncio.run(
                agents_api.set_agent_traffic_split(
                    source.id,
                    AgentTrafficSplitIn(split={"to_agent_id": str(target.id), "percent": 100}),
                    tenant_id=str(TENANT),
                    user_domains=None,
                    caller=None,
                )
            )
        assert check.call_args.args[0] is target
        assert "traffic_split" not in source.config

    def test_a_split_is_stored_under_a_row_lock_when_the_target_is_active(self, store):
        agent = _agent()
        target = _agent()
        session = store(agent, target)
        result = asyncio.run(
            agents_api.set_agent_traffic_split(
                agent.id,
                AgentTrafficSplitIn(split={"to_agent_id": str(target.id), "percent": 10}),
                tenant_id=str(TENANT),
                user_domains=None,
                caller=None,
            )
        )
        assert agent.config == {"temperature": 0.1, "traffic_split": {"to_agent_id": str(target.id), "percent": 10}}
        assert result["split"]["percent"] == 10 and result["enforced"] is False
        assert "FOR UPDATE" in session.statements[0] and "agents.tenant_id" in session.statements[1]

    def test_refusals_leave_the_agent_unchanged(self, store):
        agent = _agent()
        before = dict(agent.config)
        cases = [
            (AgentTrafficSplitIn(split={"to_agent_id": "x", "percent": 10}), (), 422),
            (AgentTrafficSplitIn(split={"to_agent_id": str(agent.id), "percent": 10}), (), 422),
            (AgentTrafficSplitIn(split={"to_agent_id": str(uuid.uuid4()), "percent": 10}), (agent, None), 404),
            (
                AgentTrafficSplitIn(split={"to_agent_id": str(uuid.uuid4()), "percent": 10}),
                (agent, _agent(status="shadow")),
                409,
            ),
        ]
        for body, answers, status in cases:
            store(*answers)
            with pytest.raises(HTTPException) as refused:
                asyncio.run(
                    agents_api.set_agent_traffic_split(
                        agent.id, body, tenant_id=str(TENANT), user_domains=None, caller=None
                    )
                )
            assert refused.value.status_code == status
        assert agent.config == before
        store(None)
        with pytest.raises(HTTPException) as missing:
            asyncio.run(
                agents_api.set_agent_traffic_split(
                    uuid.uuid4(), AgentTrafficSplitIn(split=None), tenant_id=str(TENANT), user_domains=None, caller=None
                )
            )
        assert missing.value.status_code == 404

    def test_null_removes_the_split_and_the_split_is_read_back(self, store):
        agent = _agent(config={"temperature": 0.1, "traffic_split": {"to_agent_id": str(uuid.uuid4()), "percent": 5}})
        store(agent)
        result = asyncio.run(
            agents_api.set_agent_traffic_split(
                agent.id, AgentTrafficSplitIn(split=None), tenant_id=str(TENANT), user_domains=None, caller=None
            )
        )
        assert agent.config == {"temperature": 0.1} and result["split"] is None
        split = {"to_agent_id": str(uuid.uuid4()), "percent": 25}
        store(_agent(config={"traffic_split": split}))
        read = asyncio.run(
            agents_api.get_agent_traffic_split(uuid.uuid4(), tenant_id=str(TENANT), user_domains=None, caller=None)
        )
        assert read["split"] == split and read["enforced"] is False


class TestRunPath:
    @pytest.mark.parametrize("source_state", ["active", "shadow"])
    def test_single_random_draw_and_no_shadow_to_live_reroute(self, store, monkeypatch, source_state):
        monkeypatch.setattr(settings, "agent_traffic_split_enabled", True)
        monkeypatch.setattr(agents_api, "_active_agent_below_production_floor", lambda _agent: False)
        override = AsyncMock(return_value=SimpleNamespace(blocked=False))
        monkeypatch.setattr(agents_api, "check_operator_override", override)
        monkeypatch.setattr(agents_api, "can_view_agent", lambda _agent, _caller: True)
        draw = Mock(side_effect=[0, 99])
        monkeypatch.setattr(traffic, "bucket", draw)
        target = _agent()
        source = _agent(source_state, config={"traffic_split": {"to_agent_id": str(target.id), "percent": 50}})
        session = store(source, target)
        seen = []

        class StopBeforeExecutionError(Exception):
            pass

        def record(agent):
            seen.append(agent)
            raise StopBeforeExecutionError

        monkeypatch.setattr(agents_api, "_agent_to_dict", record)
        with pytest.raises(StopBeforeExecutionError):
            asyncio.run(
                agents_api.run_agent(
                    source.id,
                    Request({"type": "http"}),
                    {"inputs": {"text": "test"}},
                    tenant_id=str(TENANT),
                    user_domains=None,
                    caller=None,
                )
            )
        assert seen == [target if source_state == "active" else source]
        assert draw.call_count == (1 if source_state == "active" else 0)
        assert len(session.statements) == (2 if source_state == "active" else 1)
        assert [call.kwargs["agent_id"] for call in override.await_args_list] == (
            [str(source.id), str(target.id)] if source_state == "active" else [str(source.id)]
        )

    @pytest.mark.parametrize("blocked_on", ["source", "target"])
    def test_shadow_accuracy_floor_cannot_be_bypassed(self, store, monkeypatch, blocked_on):
        monkeypatch.setattr(settings, "agent_traffic_split_enabled", True)
        monkeypatch.setattr(
            agents_api, "check_operator_override", AsyncMock(return_value=SimpleNamespace(blocked=False))
        )
        monkeypatch.setattr(agents_api, "can_view_agent", lambda _agent, _caller: True)
        target = _agent(shadow_accuracy_current=0.5, shadow_accuracy_floor=0.8)
        source = _agent(
            shadow_accuracy_current=0.5,
            shadow_accuracy_floor=0.8,
            config={"traffic_split": {"to_agent_id": str(target.id), "percent": 100}},
        )
        monkeypatch.setattr(
            agents_api,
            "_active_agent_below_production_floor",
            lambda agent: agent is (source if blocked_on == "source" else target),
        )
        session = store(source, target)
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                agents_api.run_agent(
                    source.id,
                    Request({"type": "http"}),
                    {"inputs": {"text": "test"}},
                    tenant_id=str(TENANT),
                    user_domains=None,
                    caller=None,
                )
            )
        assert refused.value.status_code == 409
        assert "shadow-accuracy" in refused.value.detail
        assert len(session.statements) == (1 if blocked_on == "source" else 2)

    @pytest.mark.parametrize("split", [{"percent": 5}, {"to_agent_id": "broken", "percent": 5}, "bad"])
    def test_malformed_stored_split_does_not_reroute(self, monkeypatch, split):
        monkeypatch.setattr(settings, "agent_traffic_split_enabled", True)
        source = _agent(config={"traffic_split": split})
        target = Mock()
        assert traffic.choose(source, None, target) == (source, None)
        target.assert_not_called()

    @pytest.mark.parametrize("state", ["paused", "retired"])
    def test_halted_source_is_refused_before_loading_target(self, store, monkeypatch, state):
        monkeypatch.setattr(settings, "agent_traffic_split_enabled", True)
        monkeypatch.setattr(settings, "paused_agents_refused", True)
        source = _agent(status=state, config={"traffic_split": {"to_agent_id": str(uuid.uuid4()), "percent": 100}})
        session = store(source)
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                agents_api.run_agent(
                    source.id,
                    Request({"type": "http"}),
                    {"inputs": {"text": "test"}},
                    tenant_id=str(TENANT),
                    user_domains=None,
                    caller=None,
                )
            )
        assert refused.value.status_code == 409
        assert len(session.statements) == 1

    def test_operator_override_on_source_blocks_before_target_lookup(self, store, monkeypatch):
        monkeypatch.setattr(settings, "agent_traffic_split_enabled", True)
        monkeypatch.setattr(agents_api, "_active_agent_below_production_floor", lambda _agent: False)
        override = AsyncMock(return_value=SimpleNamespace(blocked=True, reason="operator hold", override=None))
        monkeypatch.setattr(agents_api, "check_operator_override", override)
        source = _agent(config={"traffic_split": {"to_agent_id": str(uuid.uuid4()), "percent": 100}})
        session = store(source)
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                agents_api.run_agent(
                    source.id,
                    Request({"type": "http"}),
                    {"inputs": {"text": "test"}},
                    tenant_id=str(TENANT),
                    user_domains=None,
                    caller=None,
                )
            )
        assert refused.value.status_code == 423
        assert len(session.statements) == 1
        override.assert_awaited_once_with(str(TENANT), agent_id=str(source.id))

    @pytest.mark.parametrize(
        "model,payload",
        [
            (AgentCardIn, {"owner": "ignored"}),
            (AgentLifecycleIn, {"to": "review", "actor": "ignored"}),
            (AgentTrafficSplitIn, {"splti": None}),
            (AgentTrafficSplitIn, {}),
        ],
    )
    def test_unknown_fields_are_not_silently_ignored(self, model, payload):
        with pytest.raises(ValidationError):
            model.model_validate(payload)

    def test_the_run_checks_source_and_target_and_reports_the_served_agent(self):
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        run = src[src.index('@router.post("/agents/{agent_id}/run")') :]
        run = run[: run.index('"runtime": "langgraph",', run.index("response = {"))]
        assert run.index("require_agent_visible(agent_row, effective_caller)") < run.index(
            "agent_traffic.declared(agent_row)"
        )
        assert run.index("await _require_agent_runnable(agent_row, tenant_id)") < run.index("agent_traffic.choose(")
        assert run.index("await _require_agent_runnable(chosen, tenant_id)") < run.index("agent_row = chosen")
        # The target is loaded tenant-scoped and must be visible to the caller.
        assert (
            "Agent.tenant_id == tid"
            in run[run.index("agent_traffic.chooses_target(") : run.index("agent_traffic.choose(")]
        )
        assert "can_view_agent(target_row, effective_caller)" in run
        assert '"requested_agent_id": str(requested_agent_id),' in run and '"served_by": served_by_split,' in run
        # The served agent is the one the rest of the run uses.
        assert "agent_id = chosen.id" in run

    def test_a_refused_promotion_names_the_registry_state(self):
        refused = agents_api._approval_refused(approval.ApprovalError("not_approved", "not yet", "review"))
        assert refused.status_code == 409
        assert refused.detail == {
            "error": "agent_registry",
            "code": "not_approved",
            "message": "not yet",
            "state": "review",
        }

    def test_off_by_default_no_run_is_redirected(self):
        assert settings.agent_traffic_split_enabled is False
        assert traffic.enabled() is False


@pytest.mark.parametrize("path", ["create", "clone"])
def test_active_creation_cannot_bypass_registry_approval(path, store, monkeypatch):
    monkeypatch.setattr(settings, "agent_registry_enabled", True)
    monkeypatch.setattr(settings, "agent_registry_gates_promotion", True)
    monkeypatch.setattr(agents_api.prompt_activation, "check_new_agent_status", AsyncMock())
    monkeypatch.setattr(agents_api, "resolve_new_agent_ownership", lambda *_args: ("tenant", None))
    store(_agent())
    if path == "create":
        call = agents_api.create_agent(
            AgentCreate(name="Synthetic", agent_type="custom", domain="ops", initial_status="active"),
            tenant_id=str(TENANT),
            user_domains=None,
            caller=None,
        )
    else:
        call = agents_api.clone_agent(
            uuid.uuid4(),
            AgentCloneRequest(name="Synthetic", agent_type="custom", initial_status="active"),
            tenant_id=str(TENANT),
            caller=None,
        )
    with pytest.raises(HTTPException) as refused:
        asyncio.run(call)
    assert refused.value.status_code == 409
    assert refused.value.detail["error"] == "agent_registry"


def test_new_agent_gate_is_flagged_and_allows_non_active_states(monkeypatch):
    approval.check_new_agent_status("active")
    monkeypatch.setattr(settings, "agent_registry_enabled", True)
    monkeypatch.setattr(settings, "agent_registry_gates_promotion", True)
    for state in ("shadow", "paused"):
        approval.check_new_agent_status(state)
    with pytest.raises(approval.ApprovalError):
        approval.check_new_agent_status("active")
