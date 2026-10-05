# SPDX-License-Identifier: Apache-2.0
"""Maker-checker for agent prompts: the author of a prompt change does not activate the agent."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.prompts import activation, change_requests

TENANT = uuid.uuid4()
ROOT = Path(__file__).resolve().parents[2]
AUTHOR = uuid.uuid4()
OTHER = uuid.uuid4()
T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def _agent(**over) -> SimpleNamespace:
    base = {"id": uuid.uuid4(), "status": "shadow", "system_prompt_text": "You are the claims agent."}
    base.update(over)
    return SimpleNamespace(**base)


def _edit(by, at=T0) -> SimpleNamespace:
    return SimpleNamespace(edited_by=by, created_at=at)


def _activated(at) -> SimpleNamespace:
    return SimpleNamespace(to_status="active", created_at=at)


def _check(agent, activator, *, edit=None, last_active=None, on=True):
    with (
        patch.object(change_requests, "enabled", AsyncMock(return_value=on)),
        patch.object(activation, "_last_prompt_edit", AsyncMock(return_value=edit)),
        patch.object(activation, "_last_activation", AsyncMock(return_value=last_active)),
    ):
        return asyncio.run(activation.check_activation(object(), TENANT, agent, activator))


def _refused(agent, activator, **kwargs) -> activation.ActivationError:
    with pytest.raises(activation.ActivationError) as caught:
        _check(agent, activator, **kwargs)
    return caught.value


class TestOff:
    def test_off_activation_is_as_it_was_and_nothing_is_read(self):
        reads = AsyncMock(side_effect=AssertionError("not read while off"))
        with (
            patch.object(change_requests, "enabled", AsyncMock(return_value=False)),
            patch.object(activation, "_last_prompt_edit", reads),
            patch.object(activation, "_last_activation", reads),
        ):
            assert asyncio.run(activation.check_activation(object(), TENANT, _agent(), AUTHOR)) == AUTHOR
            assert asyncio.run(activation.check_activation(object(), TENANT, _agent(), None)) is None
            asyncio.run(activation.check_new_agent_status(TENANT, "active"))


class TestFirstActivation:
    def test_the_author_of_the_prompt_cannot_activate_the_agent(self):
        refusal = _refused(_agent(), AUTHOR, edit=_edit(AUTHOR))
        assert refusal.status == 403 and "cannot activate" in refusal.message

    def test_another_signed_in_user_can(self):
        assert _check(_agent(), OTHER, edit=_edit(AUTHOR)) == OTHER

    def test_the_last_editor_is_the_author(self):
        # OTHER created the agent, AUTHOR then rewrote the prompt: AUTHOR is who cannot activate it.
        assert _refused(_agent(), AUTHOR, edit=_edit(AUTHOR, T0 + timedelta(hours=1))).status == 403
        assert _check(_agent(), OTHER, edit=_edit(AUTHOR, T0 + timedelta(hours=1))) == OTHER

    def test_an_api_key_cannot_activate_a_changed_prompt(self):
        refusal = _refused(_agent(), None, edit=_edit(AUTHOR))
        assert refusal.status == 403 and "not an API key" in refusal.message

    def test_a_prompt_with_no_recorded_author_is_not_activated(self):
        for kwargs in ({}, {"edit": _edit(None)}):
            refusal = _refused(_agent(), OTHER, **kwargs)
            assert refusal.status == 409 and "no recorded author" in refusal.message


class TestInitialPrompt:
    def test_creating_an_agent_records_its_prompt_and_who_set_it(self):
        added: list = []
        agent = _agent()
        activation.record_initial_prompt(SimpleNamespace(add=added.append), TENANT, agent, AUTHOR)
        [row] = added
        assert (row.agent_id, row.tenant_id, row.edited_by) == (agent.id, TENANT, AUTHOR)
        assert row.prompt_before is None and row.prompt_after == "You are the claims agent."
        assert row.change_reason == "Initial prompt"

    def test_a_caller_with_no_user_id_records_nothing(self):
        added: list = []
        activation.record_initial_prompt(SimpleNamespace(add=added.append), TENANT, _agent(), None)
        assert added == []


class TestReactivation:
    def test_a_pause_and_resume_with_no_prompt_change_needs_no_second_person(self):
        agent = _agent(status="paused")
        last_active = _activated(T0 + timedelta(hours=1))
        assert _check(agent, AUTHOR, edit=_edit(AUTHOR, T0), last_active=last_active) == AUTHOR
        assert _check(agent, AUTHOR, edit=None, last_active=last_active) == AUTHOR
        # An operator's key can resume it too: nothing new reaches production.
        assert _check(agent, None, edit=_edit(AUTHOR, T0), last_active=last_active) is None

    def test_a_prompt_changed_since_the_last_activation_needs_one(self):
        agent = _agent(status="paused")
        last_active = _activated(T0)
        changed = _edit(AUTHOR, T0 + timedelta(hours=1))
        assert _refused(agent, AUTHOR, edit=changed, last_active=last_active).status == 403
        assert _refused(agent, None, edit=changed, last_active=last_active).status == 403
        assert _check(agent, OTHER, edit=changed, last_active=last_active) == OTHER


class TestNewAgents:
    def test_an_agent_does_not_start_active_under_maker_checker(self):
        with patch.object(change_requests, "enabled", AsyncMock(return_value=True)):
            asyncio.run(activation.check_new_agent_status(TENANT, "shadow"))
            with pytest.raises(activation.ActivationError) as caught:
                asyncio.run(activation.check_new_agent_status(TENANT, "active"))
        assert caught.value.status == 409 and "created in shadow" in caught.value.message

    def test_the_switch_is_not_read_for_a_shadow_agent(self):
        with patch.object(change_requests, "enabled", AsyncMock(side_effect=AssertionError("not read"))):
            asyncio.run(activation.check_new_agent_status(TENANT, "shadow"))


class TestFailClosed:
    def test_an_unreadable_switch_refuses_the_activation(self):
        with patch.object(change_requests, "enabled", AsyncMock(side_effect=RuntimeError("flag store down"))):
            with pytest.raises(activation.ActivationError) as caught:
                asyncio.run(activation.check_activation(object(), TENANT, _agent(), OTHER))
            assert caught.value.status == 503
            with pytest.raises(activation.ActivationError):
                asyncio.run(activation.check_new_agent_status(TENANT, "active"))


class TestQueries:
    def test_the_lookups_read_the_newest_prompt_edit_and_the_newest_activation(self):
        statements: list[str] = []

        class _Session:
            async def scalar(self, statement):
                statements.append(str(statement))
                return None

        agent_id = uuid.uuid4()
        asyncio.run(activation._last_prompt_edit(_Session(), TENANT, agent_id))
        asyncio.run(activation._last_activation(_Session(), TENANT, agent_id))
        edit, activated = statements
        assert "FROM prompt_edit_history" in edit and "ORDER BY prompt_edit_history.created_at DESC" in edit
        assert "FROM agent_lifecycle_events" in activated and "to_status" in activated
        assert "ORDER BY agent_lifecycle_events.created_at DESC" in activated


class TestCallSites:
    """The four ways an agent becomes active all pass the check before the status changes."""

    def test_every_path_to_active_is_guarded(self):
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        assert src.count("prompt_activation.check_new_agent_status(") == 2
        assert src.count("prompt_activation.check_activation(") == 2
        promote = src[src.index("async def promote_agent(") : src.index('"/agents/{agent_id}/retire"')]
        assert promote.index("prompt_activation.check_activation(") < promote.index("agent.status = new_status")
        assert 'if new_status == "active":' in promote and "triggered_by_user=activator," in promote
        resume = src[src.index("resume_to = pause_event.from_status") : src.index("async def promote_agent(")]
        assert resume.index("prompt_activation.check_activation(") < resume.index("agent.status = resume_to")

    def test_create_and_clone_record_the_first_author(self):
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        assert src.count("prompt_activation.record_initial_prompt(session, tid, agent, effective_caller.user_id)") == 1
        assert src.count("prompt_activation.record_initial_prompt(session, tid, clone, effective_caller.user_id)") == 1

    def test_no_other_assignment_makes_an_agent_active(self):
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        assert 'agent.status = "active"' not in src
        # Computed assignments: promotion (guarded), rollback (to shadow or the unchanged status) and resume (guarded).
        assert src.count("agent.status = new_status") == 2 and src.count("agent.status = resume_to") == 1
