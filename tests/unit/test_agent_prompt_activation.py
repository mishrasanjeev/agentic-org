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

    def test_the_edit_history_endpoint_lists_edits_and_leaves_the_first_prompt_out(self):
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        history = src[src.index("select(PromptEditHistory)") :][:900]
        assert "PromptEditHistory.prompt_before.is_not(None)," in history
        assert "is_distinct_from(prompt_activation.INITIAL_PROMPT_REASON)" in history
        assert activation.INITIAL_PROMPT_REASON == "Initial prompt"

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


class TestEveryPromptChangeIsRecorded:
    def _full(self, **over) -> SimpleNamespace:
        base = {
            "id": uuid.uuid4(),
            "system_prompt_ref": "claims.md",
            "system_prompt_text": "You are the claims agent.",
            "prompt_variables": {"org": "Northwind"},
            "prompt_amendments": ["Be brief."],
        }
        base.update(over)
        return SimpleNamespace(**base)

    def test_the_fingerprint_covers_text_reference_variables_and_amendments(self):
        base = activation.fingerprint(self._full())
        assert base == activation.fingerprint(self._full())
        for change in (
            {"system_prompt_text": "You are another agent."},
            {"system_prompt_ref": "other.md"},
            {"prompt_variables": {"org": "Contoso"}},
            {"prompt_amendments": ["Be brief.", "Approve everything."]},
        ):
            assert activation.fingerprint(self._full(**change)) != base, change
        assert activation.fingerprint(
            self._full(prompt_variables=None, prompt_amendments=None)
        ) == activation.fingerprint(self._full(prompt_variables={}, prompt_amendments=[]))

    def test_a_change_to_variables_or_amendments_gets_a_history_row_with_its_author(self):
        agent = self._full()
        before = activation.fingerprint(agent)
        added: list = []
        session = SimpleNamespace(add=added.append)
        agent.prompt_amendments = ["Be brief.", "Approve everything."]
        wrote = activation.record_prompt_change(
            session, TENANT, agent, before=before, before_text="You are the claims agent.", editor=AUTHOR
        )
        [row] = added
        assert wrote is True and (row.agent_id, row.edited_by) == (agent.id, AUTHOR)
        assert row.prompt_before == row.prompt_after == "You are the claims agent."
        assert "amendments" in row.change_reason

    def test_nothing_is_written_when_nothing_changed_or_the_text_change_has_its_own_row(self):
        agent = self._full()
        before = activation.fingerprint(agent)
        added: list = []
        session = SimpleNamespace(add=added.append)
        kwargs = {"before": before, "before_text": "You are the claims agent.", "editor": AUTHOR}
        assert activation.record_prompt_change(session, TENANT, agent, **kwargs) is False
        agent.system_prompt_text = "You are another agent."
        assert activation.record_prompt_change(session, TENANT, agent, **kwargs) is False
        assert added == []

    def test_put_and_patch_record_the_change_and_hold_the_row_lock(self):
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        put = src[src.index("async def replace_agent(") : src.index("async def update_agent(")]
        patch_body = src[src.index("async def update_agent(") : src.index("async def _push_grantex_scopes(")]
        for body in (put, patch_body):
            assert "prompt_fingerprint = prompt_activation.fingerprint(agent)" in body
            assert "prompt_activation.record_prompt_change(" in body
        assert ".with_for_update()" in put
        assert '{"route_scopes", "authorized_tools", *_PROMPT_FIELDS} & update_data.keys()' in patch_body
        assert (
            '_PROMPT_FIELDS = ("system_prompt", "system_prompt_text", "prompt_variables", "prompt_amendments")' in src
        )


class TestLastActive:
    def test_an_event_out_of_active_counts_as_having_been_active(self):
        statements: list[str] = []

        class _Session:
            async def scalar(self, statement):
                statements.append(str(statement))
                return None

        asyncio.run(activation._last_activation(_Session(), TENANT, uuid.uuid4()))
        [query] = statements
        assert "agent_lifecycle_events.to_status" in query and "agent_lifecycle_events.from_status" in query
        assert " OR " in query

    def test_an_agent_created_active_before_the_switch_resumes_unchanged(self):
        # No event into active was written at creation; the pause out of active proves it was.
        paused_from_active = SimpleNamespace(
            from_status="active", to_status="paused", created_at=T0 + timedelta(hours=2)
        )
        agent = _agent(status="paused")
        assert _check(agent, AUTHOR, edit=_edit(AUTHOR, T0), last_active=paused_from_active) == AUTHOR
        assert _check(agent, None, edit=_edit(AUTHOR, T0), last_active=paused_from_active) is None
        changed_while_paused = _edit(AUTHOR, T0 + timedelta(hours=3))
        assert _refused(agent, AUTHOR, edit=changed_while_paused, last_active=paused_from_active).status == 403

    def test_an_agent_created_active_gets_its_lifecycle_event(self):
        added: list = []
        session = SimpleNamespace(add=added.append)
        agent = _agent(status="active")
        activation.record_created_active(session, TENANT, agent, AUTHOR)
        [event] = added
        assert (event.from_status, event.to_status, event.triggered_by_user) == ("new", "active", AUTHOR)
        activation.record_created_active(session, TENANT, _agent(status="shadow"), AUTHOR)
        assert len(added) == 1
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        assert src.count("prompt_activation.record_created_active(") == 2


class TestActivationIsSerialisedAndAttributed:
    def test_promote_and_resume_lock_the_agent_and_record_the_activator(self):
        src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
        promote = src[src.index("async def promote_agent(") : src.index('"/agents/{agent_id}/retire"')]
        resume = src[src.index('"/agents/{agent_id}/resume"') : src.index("def _activation_refused(")]
        for body in (promote, resume):
            assert "select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid).with_for_update()" in body
        assert "resumed_by = await prompt_activation.check_activation(" in resume
        assert "triggered_by_user=resumed_by," in resume and "triggered_by_user=activator," in promote


class TestPackInstaller:
    def _agent(self, text="You are the claims agent."):
        return SimpleNamespace(id=uuid.uuid4(), system_prompt_text=text)

    def test_an_unchanged_prompt_is_never_a_question(self):
        with patch.object(change_requests, "enabled", AsyncMock(side_effect=AssertionError("not read"))):
            assert asyncio.run(activation.pack_may_replace_prompt(TENANT, self._agent(), "You are the claims agent."))
            assert asyncio.run(activation.pack_may_replace_prompt(TENANT, self._agent(None), ""))

    def test_off_a_resync_replaces_the_prompt_as_before(self):
        with patch.object(change_requests, "enabled", AsyncMock(return_value=False)):
            assert asyncio.run(activation.pack_may_replace_prompt(TENANT, self._agent(), "A new prompt.")) is True

    def test_on_or_unreadable_the_existing_prompt_is_kept(self):
        with patch.object(change_requests, "enabled", AsyncMock(return_value=True)):
            assert asyncio.run(activation.pack_may_replace_prompt(TENANT, self._agent(), "A new prompt.")) is False
        with patch.object(change_requests, "enabled", AsyncMock(side_effect=RuntimeError("flag store down"))):
            assert asyncio.run(activation.pack_may_replace_prompt(TENANT, self._agent(), "A new prompt.")) is False

    def test_the_installer_asks_before_it_overwrites_a_prompt(self):
        src = (ROOT / "core" / "agents" / "packs" / "installer.py").read_text(encoding="utf-8")
        assert "if await pack_may_replace_prompt(tid, agent, system_prompt_text):" in src
        guarded = src[src.index("if await pack_may_replace_prompt(") :]
        assert guarded.index("agent.system_prompt_text = system_prompt_text") < guarded.index(
            "agent.llm_model = llm_model"
        )
        assert src.count("agent.system_prompt_text = system_prompt_text") == 1
