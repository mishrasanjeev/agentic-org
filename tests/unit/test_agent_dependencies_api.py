# SPDX-License-Identifier: Apache-2.0
"""The dependency graph endpoint: visible agents only, related agents named only when the caller may see them."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from api.v1 import agent_registry as api
from core.agent_registry import dependencies
from core.config import settings

TENANT = uuid.uuid4()


class _Session:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        value = self.answers.pop(0)
        return SimpleNamespace(
            scalar_one_or_none=lambda: value, scalars=lambda: SimpleNamespace(all=lambda: list(value))
        )


def _agent(**over):
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "name": "Claims decider",
        "agent_type": "claims",
        "domain": "ops",
        "status": "active",
        "llm_model": "gpt-4o",
        "llm_provider": None,
        "llm_fallback": None,
        "llm_config": {},
        "system_prompt_ref": "claims/v3",
        "system_prompt_text": None,
        "authorized_tools": [],
        "connector_ids": [],
        "hitl_condition": None,
        "output_schema": None,
        "config": {},
        "parent_agent_id": None,
        "owner_user_id": None,
        "visibility": "tenant",
    }
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(settings, "agent_registry_enabled", True)

    async def _no_rules(_tenant):
        return []

    import core.governance.guardrails.engine as engine

    monkeypatch.setattr(engine, "active_rules", _no_rules)
    monkeypatch.setattr(api, "require_agent_visible", lambda _agent, _caller: None)


def _install(monkeypatch, *answers):
    session = _Session(*answers)

    @asynccontextmanager
    async def _session(_tenant):
        yield session

    monkeypatch.setattr(api, "get_tenant_session", _session)
    return session


def test_the_graph_is_returned_with_its_kinds(on, monkeypatch):
    agent = _agent()
    _install(monkeypatch, agent, None, [])
    result = asyncio.run(api.get_agent_dependencies(agent.id, tenant_id=str(TENANT), user_domains=None, caller=None))
    assert result["id"] == str(agent.id) and result["kinds"] == list(dependencies.KINDS)
    assert any(node["id"] == f"agent:{agent.id}" for node in result["nodes"])
    assert any(edge["relation"] == "calls" for edge in result["edges"])


def test_a_related_agent_the_caller_may_not_see_is_named_by_its_id_only(on, monkeypatch):
    parent = _agent(name="Secret parent", visibility="personal", owner_user_id=uuid.uuid4())
    agent = _agent(parent_agent_id=parent.id)
    monkeypatch.setattr(api, "can_view_agent", lambda row, _caller: row is not parent)
    _install(monkeypatch, agent, None, parent, [])
    result = asyncio.run(api.get_agent_dependencies(agent.id, tenant_id=str(TENANT), user_domains=None, caller=None))
    labels = {node["id"]: node["label"] for node in result["nodes"]}
    assert labels[f"agent:{parent.id}"] == str(parent.id) and "Secret parent" not in str(result)


def test_missing_and_off(on, monkeypatch):
    _install(monkeypatch, None)
    with pytest.raises(HTTPException) as missing:
        asyncio.run(api.get_agent_dependencies(uuid.uuid4(), tenant_id=str(TENANT), user_domains=None, caller=None))
    assert missing.value.status_code == 404
    monkeypatch.setattr(settings, "agent_registry_enabled", False)
    with pytest.raises(HTTPException) as off:
        asyncio.run(api.get_agent_dependencies(uuid.uuid4(), tenant_id=str(TENANT), user_domains=None, caller=None))
    assert off.value.status_code == 409
