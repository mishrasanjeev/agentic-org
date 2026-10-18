# SPDX-License-Identifier: Apache-2.0
"""Replay opt-in answers and refusal paths without contacting a provider."""

import math
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.v1 import agents, channels
from core.channels import runtime
from core.channels.replies import final_answer
from tests.unit.test_channel_runtime_boundaries import Session, database
from tests.unit.test_channel_runtime_boundaries import state as state


@pytest.mark.parametrize(
    "output,expected",
    [
        ({"answer": 18, "confidence": 1}, "18"),
        ("18 items remain", "18 items remain"),
        (0, "0"),
        (False, "false"),
        ({"items": [7, 11]}, '{"items": [7, 11]}'),
        ("नमस्ते 世界", "नमस्ते 世界"),
    ],
)
def test_final_answer_contains_only_the_completed_output(output, expected):
    result = {"status": "completed", "output": output, "reasoning_trace": ["not exported"], "tool_calls": []}
    assert final_answer(result, {}) == expected


@pytest.mark.parametrize(
    "output",
    [
        None,
        "",
        "   ",
        {"api_key": "synthetic"},
        {"nested": {"tool_calls": []}},
        "Bearer synthetic-private-value",
        "password=synthetic-private-value",
        "-----BEGIN PRIVATE KEY-----",
        "AIza" + "x" * 35,
        "123456789:" + "x" * 35,
        "eyJ123456789.abcdefgh.ijklmnop",
        "sk-" + "x" * 30,
        "x" * 16001,
        "unsafe\x00control",
        "invalid\ud800unicode",
        math.nan,
        object(),
        [[[[[[["deep"]]]]]]],
        list(range(101)),
        {str(i): i for i in range(101)},
    ],
)
def test_private_malformed_and_unbounded_outputs_are_withheld(output):
    assert final_answer({"status": "completed", "output": output}, {}) is None


def test_own_credentials_cannot_be_echoed_and_unicode_is_bounded():
    assert (
        final_answer(
            {"status": "completed", "output": "do not echo synthetic-channel-secret"},
            {"webhook_secret": "synthetic-channel-secret"},
        )
        is None
    )
    text = final_answer({"status": "completed", "output": "🙂" * 2000}, {})
    assert text is not None and len(text.encode("utf-16-le")) <= 3800
    assert text.endswith("[Answer shortened. Open AgenticOrg for the full result.]")
    for changes in [{"status": "waiting_hitl"}, {"error": "failed"}, {"hitl_trigger": "review"}]:
        assert final_answer({"status": "completed", "output": "private", **changes}, {}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "answer",
        "status_default",
        "old_intake",
        "mode_disabled",
        "reply_disabled",
        "target_changed",
        "binding_owner_changed",
        "provider_changed",
        "approval",
        "private_output",
        "missing_user",
        "missing_link",
        "missing_target",
        "paused_target",
        "foreign_target",
        "viewer",
        "session_revoked",
        "link_revoked",
        "provider_failure",
    ],
)
async def test_dispatch_rechecks_consent_authority_and_sends_answer_once(monkeypatch, state, mode):
    b, receipt, user = state.binding, state.receipt, state.user
    b.auto_reply, b.reply_mode, receipt.reply_mode = True, "agent_answer", "agent_answer"
    live = SimpleNamespace(**vars(b))
    if mode == "status_default":
        b.reply_mode = live.reply_mode = "status"
    if mode == "old_intake":
        receipt.reply_mode = "status"
    if mode == "mode_disabled":
        live.reply_mode = "status"
    if mode == "reply_disabled":
        live.auto_reply = False
    if mode == "target_changed":
        live.target_id = uuid.uuid4()
    if mode == "binding_owner_changed":
        live.owner_user_id = uuid.uuid4()
    if mode == "provider_changed":
        live.provider = "viber"
    initial = Session(scalar=[b, None, receipt, user, state.delegation])
    target = Session(scalar=[state.target])
    saved, current = Session(gets=[receipt]), Session(gets=[live])
    reply_user = SimpleNamespace(**vars(user))
    reply_target = SimpleNamespace(**vars(state.target))
    if mode == "viewer":
        reply_user.role = "viewer"
    if mode == "session_revoked":
        reply_user.sessions_invalid_before = datetime.now(UTC) + timedelta(seconds=1)
    if mode == "link_revoked":
        reply_user.sessions_invalid_before = state.delegation.created_at + timedelta(microseconds=1)
        receipt.updated_at = datetime.now(UTC) + timedelta(seconds=2)
    if mode == "paused_target":
        reply_target.status = "paused"
    if mode == "foreign_target":
        reply_user.role = "developer"
        reply_target.owner_user_id = uuid.uuid4()
    auth = Session(
        scalar=[
            None if mode == "missing_user" else reply_user,
            None if mode == "missing_link" else state.delegation,
            None if mode == "missing_target" else reply_target,
        ]
    )
    database(monkeypatch, runtime, state.tid, initial, target, saved, current, auth, Session(gets=[receipt]))
    result = {
        "status": "waiting_hitl" if mode == "approval" else "completed",
        "output": {"api_key": "synthetic"} if mode == "private_output" else {"answer": 18},
        "reasoning_trace": ["never exported"],
        "tool_calls": [],
    }
    monkeypatch.setattr(agents, "run_agent", AsyncMock(return_value=result))
    monkeypatch.setattr(
        runtime,
        "unseal",
        AsyncMock(
            side_effect=[
                {"sender": "10001", "text": "There are 7, 11 and 13 items. Remove 11 and 2. How many remain?"},
                {"access_token": "123:synthetic-token", "webhook_secret": "synthetic-secret"},
            ]
        ),
    )
    reply = AsyncMock(
        side_effect=runtime.ChannelError("provider_reply_unconfirmed") if mode == "provider_failure" else None
    )
    monkeypatch.setattr(runtime, "send_reply", reply)
    await runtime.dispatch_one(state.tid, b.id)
    if mode == "reply_disabled":
        reply.assert_not_awaited()
    else:
        reply.assert_awaited_once()
        text = reply.call_args.args[3]
        assert text == "18" if mode in {"answer", "provider_failure"} else "18" not in text
        assert "never exported" not in text and "api_key" not in text
    if mode == "provider_failure":
        assert receipt.error_code == "reply_delivery_unconfirmed"
    if auth.queries:
        assert all(state.tid in query.compile().params.values() for query in auth.queries)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["answer_mode", "route", "foreign_agent", "busy", "partial_route", "workflow_answer", "other_provider"]
)
async def test_setting_and_route_changes_are_owner_scoped_and_validate_targets(monkeypatch, state, mode):
    b = state.binding
    b.reply_mode = "status"
    target_id = uuid.uuid4()
    body = {"reply_mode": "agent_answer"}
    if mode in {"route", "foreign_agent", "busy", "workflow_answer"}:
        body.update(target_kind="workflow" if mode == "workflow_answer" else "agent", target_id=target_id)
    if mode == "other_provider":
        b.provider = "viber"
    if mode == "partial_route":
        body["target_id"] = target_id
    lookup = Session(scalar=[b])
    update = Session(
        scalar=[b, None if mode == "foreign_agent" else state.target, uuid.uuid4() if mode == "busy" else None]
    )
    database(monkeypatch, channels, state.tid, lookup, update)
    monkeypatch.setattr(channels, "_actor", AsyncMock(return_value=state.user))
    if mode in {"answer_mode", "route"}:
        result = await channels.update_binding(
            b.id, channels.BindingUpdate(**body), runtime._request_for_user(state.user), str(state.tid)
        )
        assert result["reply_mode"] == "agent_answer"
        assert b.credentials_ciphertext == "encrypted-credentials"
        if mode == "route":
            assert b.target_id == target_id
    else:
        with pytest.raises(HTTPException) as error:
            await channels.update_binding(
                b.id, channels.BindingUpdate(**body), runtime._request_for_user(state.user), str(state.tid)
            )
        assert error.value.status_code == {"foreign_agent": 404, "busy": 409}.get(mode, 422)
    assert {state.tid, state.user.id, b.id}.issubset(set(lookup.queries[0].compile().params.values()))


def test_invalid_modes_and_nontelegram_workflow_cannot_opt_in():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        channels.BindingUpdate(reply_mode="everything")
    assert channels.BindingIn.model_fields["reply_mode"].default == "status"
    for provider, kind in [("viber", "agent"), ("telegram", "workflow")]:
        with pytest.raises(HTTPException):
            channels._validate_reply_mode(provider, kind, "agent_answer")


@pytest.mark.asyncio
@pytest.mark.parametrize("send_replies,expected", [(False, "status"), (True, "agent_answer")])
async def test_answer_consent_is_snapshotted_only_when_replies_were_enabled(monkeypatch, state, send_replies, expected):
    from core.channels.providers import Message

    state.binding.reply_mode, state.binding.auto_reply = "agent_answer", send_replies
    session = Session(scalar=[state.binding, None, state.delegation])
    database(monkeypatch, runtime, state.tid, session)
    assert (
        await runtime.receive(state.binding, Message(event_id="snapshot", sender="10001", text="Synthetic task"))
        == "pending"
    )
    assert session.added[0].reply_mode == expected


def test_local_stack_starts_the_channel_scheduler_with_the_same_runtime():
    from pathlib import Path

    import yaml

    services = yaml.safe_load((Path(__file__).resolve().parents[2] / "docker-compose.dev.yml").read_text())["services"]
    beat = services["beat"]
    assert beat["image"] == services["worker"]["image"] == services["api"]["image"]
    assert "celery -A core.tasks.celery_app beat" in beat["command"]
    assert beat["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert beat["environment"] == services["worker"]["environment"]
    assert "--pidfile=/tmp/celerybeat.pid" in beat["command"]
    assert beat["healthcheck"].get("disable") is not True
    assert beat["healthcheck"]["test"][:2] == ["CMD", "python"]
    assert "celerybeat.pid" in beat["healthcheck"]["test"][3]
    assert "os.kill" in beat["healthcheck"]["test"][3]
