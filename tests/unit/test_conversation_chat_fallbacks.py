# SPDX-License-Identifier: Apache-2.0
"""Conversational services: POST /chat/query holds back weak answers and carries the fallback streak across queries."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.v1 import chat
from core.config import settings
from core.conversation import fallbacks

TENANT = str(uuid.uuid4())
COMPANY = uuid.uuid4()
AGENT = str(uuid.uuid4())


def _request(user: str = "user-1") -> MagicMock:
    request = MagicMock()
    request.state = SimpleNamespace(claims={"sub": user}, grant_token=None, agent_id="")
    return request


def _ask(store: dict, query: str, *, agent_id: str | None = None, run_result: dict | None = None, user: str = "user-1"):
    """POST /chat/query routed by keyword, with the agent run and the session store replaced."""
    body = chat.ChatQueryRequest(query=query, company_id=str(COMPANY))

    async def _load(key):
        return list(store.get(key, []))

    async def _save(key, entries):
        store[key] = list(entries)

    runner = AsyncMock(return_value=run_result or {})
    with (
        patch("api.v1.agents._require_company_for_tenant", AsyncMock(return_value=COMPANY)),
        patch("api.v1.agents._resolve_agent_connector_ids_for_type", AsyncMock(return_value=[])),
        patch("api.v1.agents._record_cost_ledger", AsyncMock(return_value=True)),
        patch("api.v1._tds_routing.try_tds_deterministic_route", AsyncMock(return_value=None)),
        patch("core.langgraph.runner.run_agent", runner),
        patch.object(chat, "_record_cost_ledger", AsyncMock(return_value=True)),
        patch.object(chat, "caller_from_request", return_value=SimpleNamespace(user_id=None, is_admin=True)),
        patch.object(chat, "_find_agent_for_domain", AsyncMock(return_value=("General Assistant", agent_id, None, []))),
        patch.object(chat, "_load_routed_agent", AsyncMock(return_value=None)),
        patch.object(chat, "resolve_run_grant", AsyncMock(return_value=None)),
        patch.object(chat, "check_operator_override", AsyncMock(return_value=SimpleNamespace(blocked=False))),
        patch.object(chat.conversation_runtime, "chat_turn", AsyncMock(return_value=None)),
        patch.object(chat.conversation_runtime, "agent_bindings", AsyncMock(return_value={})),
        patch.object(chat, "_load_session", _load),
        patch.object(chat, "_save_session", _save),
    ):
        response = asyncio.run(chat.chat_query(body, _request(user), tenant_id=TENANT, user_domains=None))
    return response, runner


@pytest.fixture
def conversation_on(monkeypatch):
    monkeypatch.setattr(settings, "conversation_v2_enabled", True)


class TestFallbackStreak:
    def test_the_second_failed_query_in_a_row_offers_a_person(self, conversation_on):
        store: dict = {}
        first, _ = _ask(store, "what is the weather today")
        assert first.confidence == 0.0 and "connect you to a person" not in first.answer
        second, _ = _ask(store, "and tomorrow")
        assert "connect you to a person" in second.answer
        (entries,) = store.values()
        assert [e.get(fallbacks.FALLBACK_KEY) for e in entries if e["role"] == "agent"] == [
            fallbacks.KIND_NO_ANSWER,
            fallbacks.KIND_NO_ANSWER,
        ]

    def test_an_answer_in_between_resets_the_streak(self, conversation_on):
        store: dict = {}
        _ask(store, "what is the weather today")
        good = {"status": "completed", "output": {"answer": "The balance is 500."}, "confidence": 0.9}
        answered, _ = _ask(store, "what is my balance", agent_id=AGENT, run_result=good)
        assert answered.answer == "The balance is 500." and answered.confidence == 0.9
        after, _ = _ask(store, "and the weather again")
        assert "connect you to a person" not in after.answer

    def test_the_streak_is_kept_per_user(self, conversation_on):
        store: dict = {}
        _ask(store, "what is the weather today", user="user-1")
        other, _ = _ask(store, "and tomorrow", user="user-2")
        assert "connect you to a person" not in other.answer
        again, _ = _ask(store, "and tomorrow", user="user-1")
        assert "connect you to a person" in again.answer


class TestLowConfidence:
    def test_a_low_confidence_answer_is_held_back(self, conversation_on):
        weak = {"status": "completed", "output": {"answer": "Probably 42."}, "confidence": 0.2}
        response, runner = _ask({}, "what is my balance", agent_id=AGENT, run_result=weak)
        runner.assert_awaited_once()
        assert "Probably 42." not in response.answer
        assert "not confident enough" in response.answer and response.confidence == 0.0

    def test_a_confident_answer_is_returned(self, conversation_on):
        strong = {"status": "completed", "output": {"answer": "The balance is 500."}, "confidence": 0.8}
        response, _ = _ask({}, "what is my balance", agent_id=AGENT, run_result=strong)
        assert response.answer == "The balance is 500." and response.confidence == 0.8

    def test_with_the_flag_off_the_answer_is_returned_as_before(self, monkeypatch):
        monkeypatch.setattr(settings, "conversation_v2_enabled", False)
        weak = {"status": "completed", "output": {"answer": "Probably 42."}, "confidence": 0.2}
        response, _ = _ask({}, "what is my balance", agent_id=AGENT, run_result=weak)
        assert response.answer == "Probably 42."
