# SPDX-License-Identifier: Apache-2.0
"""Answer-mode context is text-only, bounded and isolated from execution state."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.channels import runtime
from tests.unit.test_channel_runtime_boundaries import Session, database


@pytest.fixture
def conversation():
    target = SimpleNamespace(id=uuid.uuid4(), company_id=None, authorized_tools=[])
    binding = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        provider="telegram",
        target_kind="agent",
        auto_reply=True,
        reply_mode="agent_answer",
    )
    receipt = SimpleNamespace(
        id=uuid.uuid4(),
        reply_mode="agent_answer",
        error_code=None,
        delegation_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        created_at=datetime.now(UTC),
    )
    scope = runtime._conversation_scope(binding, receipt, target)
    message = {
        "sender_hash": "a" * 64,
        "text": "मेरी दुकान का रंग बैंगनी है।",
        "answer_sent": True,
        "conversation_scope": scope,
    }
    result = {
        "status": "completed",
        "agent_id": str(target.id),
        "requested_agent_id": str(target.id),
        "output": {"answer": "आपकी दुकान का रंग बैंगनी है।"},
        "tool_calls": [],
    }
    return SimpleNamespace(target=target, binding=binding, receipt=receipt, scope=scope, message=message, result=result)


@pytest.mark.parametrize(
    "change",
    ["status", "workflow", "provider", "no_reply", "tools", "unknown_tools", "receipt_status", "traffic_split"],
)
def test_context_requires_explicit_answer_only_opt_in(conversation, change):
    s = conversation
    if change == "status":
        s.binding.reply_mode = "status"
    elif change == "workflow":
        s.binding.target_kind = "workflow"
    elif change == "provider":
        s.binding.provider = "email"
    elif change == "no_reply":
        s.binding.auto_reply = False
    elif change == "tools":
        s.target.authorized_tools = ["synthetic.read"]
    elif change == "unknown_tools":
        s.target.authorized_tools = None
    elif change == "traffic_split":
        s.target.config = {"traffic_split": {"to_agent_id": str(uuid.uuid4()), "percent": 100}}
    else:
        s.receipt.reply_mode = "status"
    assert runtime._conversation_scope(s.binding, s.receipt, s.target) is None


@pytest.mark.parametrize(
    "change",
    [
        "sender",
        "agent",
        "requested_agent",
        "company",
        "unconfirmed",
        "legacy",
        "error",
        "review",
        "tools",
        "private_input",
        "private_answer",
        "credential_input",
        "credential_answer",
    ],
)
def test_unsafe_or_differently_scoped_pairs_are_not_context(conversation, change):
    s = conversation
    if change == "sender":
        s.message["sender_hash"] = "b" * 64
    elif change in {"agent", "requested_agent"}:
        s.result["agent_id" if change == "agent" else "requested_agent_id"] = str(uuid.uuid4())
    elif change == "company":
        s.message["conversation_scope"] = {**s.scope, "company_id": str(uuid.uuid4())}
    elif change == "unconfirmed":
        s.message["answer_sent"] = False
    elif change == "legacy":
        del s.message["conversation_scope"]
    elif change == "error":
        s.receipt.error_code = "reply_delivery_unconfirmed"
    elif change == "review":
        s.result["hitl_required"] = True
    elif change == "tools":
        s.result["tool_calls"] = [{"tool": "synthetic.read"}]
    elif change == "private_input":
        s.message["text"] = "password=synthetic-secret"
    elif change == "private_answer":
        s.result["output"] = {"answer": "ok", "reasoning_trace": "private"}
    elif change == "credential_input":
        s.message["text"] = "synthetic-channel-secret"
    else:
        s.result["output"] = {"answer": "synthetic-channel-secret"}
    assert not runtime._conversation_pair(
        s.receipt, s.message, s.result, "a" * 64, s.scope, {"access_token": "synthetic-channel-secret"}
    )


@pytest.mark.asyncio
async def test_recent_context_has_scope_time_and_size_bounds(monkeypatch, conversation):
    s = conversation
    rows = [
        SimpleNamespace(message_ciphertext=f"message-{i}", result_ciphertext=f"result-{i}", error_code=None)
        for i in range(4)
    ]
    frame = Session(scalars=[rows])
    database(monkeypatch, runtime, s.binding.tenant_id, frame)

    async def unseal(value):
        return s.message if value.startswith("message") else s.result

    monkeypatch.setattr(runtime, "unseal", AsyncMock(side_effect=unseal))
    context = await runtime._recent_answer_context(s.binding, s.receipt, s.message, s.scope, {})
    assert len(context["conversation"]) == 8
    assert {turn["role"] for turn in context["conversation"]} == {"user", "assistant"}
    query = frame.queries[0]
    sql = str(query.compile(compile_kwargs={"literal_binds": True}))
    for field in ("tenant_id", "binding_id", "user_id", "delegation_id", "reply_mode", "status", "created_at"):
        assert field in sql
    assert "LIMIT 4" in sql and "DESC" in sql


@pytest.mark.asyncio
async def test_invalid_latest_pair_does_not_resurrect_older_history(monkeypatch, conversation):
    s = conversation
    rows = [SimpleNamespace(message_ciphertext="m", result_ciphertext="r", error_code="reply_delivery_unconfirmed")]
    database(monkeypatch, runtime, s.binding.tenant_id, Session(scalars=[rows]))
    monkeypatch.setattr(runtime, "unseal", AsyncMock(side_effect=[s.message, s.result]))
    assert await runtime._recent_answer_context(s.binding, s.receipt, s.message, s.scope, {}) == {}
