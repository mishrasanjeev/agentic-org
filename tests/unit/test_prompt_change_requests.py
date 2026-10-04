# SPDX-License-Identifier: Apache-2.0
"""Maker-checker for prompt templates: proposing, deciding, staleness and the endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from api.deps import get_current_tenant, get_current_user, get_user_domains
from api.route_enforcement import enforce_route_metadata
from api.v1 import prompt_templates as api
from core.config import settings
from core.prompts import change_requests as cr

TENANT = uuid.uuid4()
ROOT = Path(__file__).resolve().parents[2]
MAKER = {"agenticorg:user_id": str(uuid.uuid4()), "sub": "maker@example.test"}
CHECKER = {"agenticorg:user_id": str(uuid.uuid4()), "sub": "checker@example.test"}
STAMP = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)


def _template(**over) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "name": "claims agent",
        "agent_type": "claims_agent",
        "domain": "ops",
        "template_text": "You are the {{role}} agent for the claims team.",
        "variables": [{"name": "role"}],
        "description": "The claims agent prompt.",
        "is_builtin": False,
        "is_active": True,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _request(template=None, **over) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "template_id": template.id if template is not None else None,
        "kind": "update",
        "domain": "ops",
        "proposed": {"template_text": "You are the {{role}} agent. Be brief."},
        "base_updated_at": STAMP,
        "reason": None,
        "status": "pending",
        "requested_by": cr.actor_of(MAKER),
        "requested_by_user": uuid.UUID(MAKER["agenticorg:user_id"]),
        "requested_at": STAMP,
        "decided_by": None,
        "decided_at": None,
        "decision_note": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


class _Session:
    """Answers ``scalar`` and ``execute`` from a queue and records what was added."""

    def __init__(self, answers: list | None = None) -> None:
        self.answers = list(answers or [])
        self.added: list = []

    def _next(self):
        return self.answers.pop(0) if self.answers else None

    async def scalar(self, _statement):
        return self._next()

    async def scalars(self, _statement):
        value = self._next()
        return SimpleNamespace(all=lambda: list(value or []))

    async def execute(self, _statement):
        value = self._next()
        return SimpleNamespace(scalar_one_or_none=lambda: value)

    def add(self, row):
        if getattr(row, "id", None) is None:
            row.id = uuid.uuid4()
        self.added.append(row)

    async def flush(self):
        return None

    async def rollback(self):
        return None


def _run(coroutine):
    return asyncio.run(coroutine)


class TestSwitch:
    def test_off_by_default_and_on_by_setting_or_tenant_flag(self, monkeypatch):
        from core.feature_flags import FlagRows

        assert settings.prompts_maker_checker is False
        rows = AsyncMock(return_value=FlagRows(None, None))
        with patch("core.feature_flags.load_flag_rows_strict", rows):
            assert _run(cr.enabled(TENANT)) is False
        rows.assert_awaited_once_with("prompts.maker_checker", tenant_id=TENANT)
        on = FlagRows(None, {"enabled": True, "rollout_percentage": 100})
        with patch("core.feature_flags.load_flag_rows_strict", AsyncMock(return_value=on)):
            assert _run(cr.enabled(TENANT)) is True
        monkeypatch.setattr(settings, "prompts_maker_checker", True)
        with patch("core.feature_flags.load_flag_rows_strict", AsyncMock(side_effect=AssertionError("not read"))):
            assert _run(cr.enabled(TENANT)) is True

    def test_an_unreadable_flag_raises(self):
        with (
            patch("core.feature_flags.load_flag_rows_strict", AsyncMock(side_effect=RuntimeError("flag store down"))),
            pytest.raises(RuntimeError),
        ):
            _run(cr.enabled(TENANT))


class TestIdentity:
    def test_the_local_user_id_then_the_subject_and_never_nobody(self):
        assert cr.actor_of(MAKER) == f"user:{MAKER['agenticorg:user_id']}"
        assert cr.actor_of({"sub": "apikey:key_01"}) == "sub:apikey:key_01"
        assert cr.actor_of({"agenticorg:user_id": "not-a-uuid", "sub": "s"}) == "sub:s"
        for nobody in ({}, {"sub": " "}, None):
            with pytest.raises(cr.ChangeRequestError) as caught:
                cr.actor_of(nobody)
            assert caught.value.status == 403


class TestPropose:
    def test_a_proposal_is_stored_with_the_template_it_was_made_against(self):
        template = _template()
        session = _Session([None])
        row = _run(
            cr.open_request(
                session,
                TENANT,
                kind="update",
                template=template,
                proposed={"template_text": "new text", "ignored": 1},
                domain="ops",
                reason="  tighten the wording  ",
                user=MAKER,
            )
        )
        assert session.added == [row]
        assert (row.kind, row.status, row.template_id, row.base_updated_at) == ("update", "pending", template.id, STAMP)
        assert row.proposed == {"template_text": "new text"} and row.reason == "tighten the wording"
        assert row.requested_by == cr.actor_of(MAKER) and str(row.requested_by_user) == MAKER["agenticorg:user_id"]

    def test_one_pending_change_per_template(self):
        with pytest.raises(cr.ChangeRequestError) as caught:
            _run(
                cr.open_request(
                    _Session([uuid.uuid4()]),
                    TENANT,
                    kind="update",
                    template=_template(),
                    proposed={},
                    domain="ops",
                    reason=None,
                    user=MAKER,
                )
            )
        assert caught.value.status == 409

    def test_an_unknown_kind_or_an_unattributable_caller_is_refused(self):
        for kind, user, status in (("rename", MAKER, 422), ("update", {}, 403)):
            with pytest.raises(cr.ChangeRequestError) as caught:
                _run(
                    cr.open_request(
                        _Session([None]),
                        TENANT,
                        kind=kind,
                        template=_template(),
                        proposed={},
                        domain="ops",
                        reason=None,
                        user=user,
                    )
                )
            assert caught.value.status == status


class TestDecide:
    def test_an_approval_applies_the_change_and_records_both_people(self):
        template = _template()
        request = _request(template, reason="tighten the wording")
        session = _Session([request, template])
        row = _run(cr.approve(session, TENANT, request.id, user=CHECKER, note="ok", domains=None))
        assert row.status == "approved" and row.decided_by == cr.actor_of(CHECKER) and row.decision_note == "ok"
        assert template.template_text == "You are the {{role}} agent. Be brief."
        [history] = session.added
        assert history.template_text_before == "You are the {{role}} agent for the claims team."
        assert history.template_text_after == template.template_text
        assert str(history.edited_by) == MAKER["agenticorg:user_id"]
        assert history.approved_by == cr.actor_of(CHECKER) and history.change_request_id == request.id
        assert history.change_reason == "tighten the wording"

    def test_the_proposer_cannot_decide_their_own_change(self):
        template = _template()
        for decide in (
            lambda s, r: cr.approve(s, TENANT, r.id, user=MAKER, note=None, domains=None),
            lambda s, r: cr.reject(s, TENANT, r.id, user=MAKER, note="no", domains=None),
        ):
            request = _request(template)
            with pytest.raises(cr.ChangeRequestError) as caught:
                _run(decide(_Session([request, template]), request))
            assert caught.value.status == 403 and request.status == "pending"
        assert template.template_text == "You are the {{role}} agent for the claims team."

    def test_a_change_proposed_against_an_older_template_is_stale_and_not_applied(self):
        moved_on = _template(updated_at=STAMP + timedelta(minutes=5))
        request = _request(moved_on)
        session = _Session([request, moved_on])
        row = _run(cr.approve(session, TENANT, request.id, user=CHECKER, note=None, domains=None))
        assert row.status == "stale" and session.added == []
        assert moved_on.template_text == "You are the {{role}} agent for the claims team."
        for gone in (None, _template(is_active=False), _template(is_builtin=True)):
            request = _request(_template())
            assert (
                _run(
                    cr.approve(_Session([request, gone]), TENANT, request.id, user=CHECKER, note=None, domains=None)
                ).status
                == "stale"
            )

    def test_an_approved_delete_deactivates_and_an_approved_create_inserts(self):
        template = _template()
        delete = _request(template, kind="delete", proposed={})
        _run(cr.approve(_Session([delete, template]), TENANT, delete.id, user=CHECKER, note=None, domains=None))
        assert template.is_active is False and delete.status == "approved"
        proposed = {
            "name": "new agent",
            "agent_type": "new_agent",
            "domain": "ops",
            "template_text": "You are a new agent for the operations team.",
            "variables": [],
            "description": None,
        }
        create = _request(None, kind="create", proposed=proposed, base_updated_at=None)
        session = _Session([create])
        row = _run(cr.approve(session, TENANT, create.id, user=CHECKER, note=None, domains=None))
        [inserted] = session.added
        assert (inserted.name, inserted.agent_type, inserted.tenant_id) == ("new agent", "new_agent", TENANT)
        assert str(inserted.created_by) == MAKER["agenticorg:user_id"]
        assert row.status == "approved" and row.template_id == inserted.id

    def test_a_rejection_needs_a_note_and_changes_nothing(self):
        template = _template()
        request = _request(template)
        with pytest.raises(cr.ChangeRequestError) as caught:
            _run(cr.reject(_Session([request]), TENANT, request.id, user=CHECKER, note=" ", domains=None))
        assert caught.value.status == 422
        row = _run(cr.reject(_Session([request]), TENANT, request.id, user=CHECKER, note="wrong tone", domains=None))
        assert (row.status, row.decision_note) == ("rejected", "wrong tone")
        assert template.template_text == "You are the {{role}} agent for the claims team."

    def test_only_the_proposer_withdraws_and_only_while_pending(self):
        request = _request(_template())
        with pytest.raises(cr.ChangeRequestError) as caught:
            _run(cr.withdraw(_Session([request]), TENANT, request.id, user=CHECKER, domains=None))
        assert caught.value.status == 403
        row = _run(cr.withdraw(_Session([request]), TENANT, request.id, user=MAKER, domains=None))
        assert row.status == "withdrawn"
        for decide in (
            lambda r: cr.withdraw(_Session([r]), TENANT, r.id, user=MAKER, domains=None),
            lambda r: cr.approve(_Session([r]), TENANT, r.id, user=CHECKER, note=None, domains=None),
            lambda r: cr.reject(_Session([r]), TENANT, r.id, user=CHECKER, note="n", domains=None),
        ):
            with pytest.raises(cr.ChangeRequestError) as caught:
                _run(decide(request))
            assert caught.value.status == 409

    def test_a_request_in_another_domain_or_missing_is_not_found(self):
        request = _request(_template(), domain="finance")
        for session in (_Session([request]), _Session([None])):
            with pytest.raises(cr.ChangeRequestError) as caught:
                _run(cr.approve(session, TENANT, request.id, user=CHECKER, note=None, domains=["ops"]))
            assert caught.value.status == 404


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _client(session: _Session, user: dict):
    app = FastAPI(dependencies=[Depends(enforce_route_metadata)])

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.auth_mode = "api_key"
        request.state.claims = {"sub": "apikey:key_01"}
        request.state.scopes = ["agenticorg:admin"]
        request.state.tenant_id = str(TENANT)
        return await call_next(request)

    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[get_current_tenant] = lambda: str(TENANT)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_user_domains] = lambda: None

    @contextlib.asynccontextmanager
    async def _ctx(_tid):
        yield session

    return TestClient(app), patch.object(api, "get_tenant_session", _ctx)


@pytest.fixture(autouse=True)
def _no_rate_limit_redis():
    with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)):
        yield


def _switch(on: bool):
    return patch.object(cr, "enabled", AsyncMock(return_value=on))


class TestWritesWaitForApproval:
    def test_off_an_update_is_applied_at_once_as_before(self):
        template = _template()
        session = _Session([template])
        client, sessions = _client(session, MAKER)
        with sessions, _switch(False):
            resp = client.put(f"/api/v1/prompt-templates/{template.id}", json={"description": "A changed description."})
        assert resp.status_code == 200 and resp.json() == {"id": str(template.id), "updated": True}
        assert template.description == "A changed description."

    def test_on_an_update_becomes_a_pending_request_and_the_template_is_untouched(self):
        template = _template()
        session = _Session([template, None])
        client, sessions = _client(session, MAKER)
        with sessions, _switch(True):
            resp = client.put(f"/api/v1/prompt-templates/{template.id}", json={"description": "A changed description."})
        assert resp.status_code == 202
        body = resp.json()
        assert body["pending_approval"] is True and body["status"] == "pending" and body["id"] == str(template.id)
        assert template.description == "The claims agent prompt."
        [request] = session.added
        assert request.kind == "update" and request.proposed == {"description": "A changed description."}

    def test_on_a_delete_and_a_create_wait_too(self):
        template = _template()
        session = _Session([template, None])
        client, sessions = _client(session, MAKER)
        with sessions, _switch(True):
            deleted = client.delete(f"/api/v1/prompt-templates/{template.id}")
        assert deleted.status_code == 202 and template.is_active is True and session.added[0].kind == "delete"
        session = _Session([None])
        client, sessions = _client(session, MAKER)
        payload = {
            "name": "new agent",
            "agent_type": "new_agent",
            "domain": "ops",
            "template_text": "You are a new agent for the operations team.",
        }
        with sessions, _switch(True):
            created = client.post("/api/v1/prompt-templates", json=payload)
        assert created.status_code == 202 and created.json()["id"] is None
        [request] = session.added
        assert request.kind == "create" and request.proposed["agent_type"] == "new_agent"

    def test_an_unreadable_switch_refuses_the_change(self):
        template = _template()
        session = _Session([template])
        client, sessions = _client(session, MAKER)
        with sessions, patch.object(cr, "enabled", AsyncMock(side_effect=RuntimeError("flag store down"))):
            resp = client.put(f"/api/v1/prompt-templates/{template.id}", json={"description": "A changed description."})
        assert resp.status_code == 503 and template.description == "The claims agent prompt."

    def test_every_write_path_checks_the_switch_before_it_changes_anything(self):
        src = (ROOT / "api" / "v1" / "prompt_templates.py").read_text(encoding="utf-8")
        assert src.count("if await _maker_checker(tid):") == 4
        update = src[
            src.index("async def update_prompt_template(") : src.index("async def get_prompt_template_history(")
        ]
        assert update.index("if await _maker_checker(tid):") < update.index("template.name = update_data")
        rollback = src[
            src.index("async def rollback_prompt_template(") : src.index("async def delete_prompt_template(")
        ]
        assert rollback.index("if await _maker_checker(tid):") < rollback.index("template.name = hist.name_before")
        delete = src[src.index("async def delete_prompt_template(") :]
        assert delete.index("if await _maker_checker(tid):") < delete.index("template.is_active = False")


class TestDecisionEndpoints:
    def test_the_list_says_whether_maker_checker_is_on(self):
        request = _request(_template())
        client, sessions = _client(_Session([[request]]), CHECKER)
        with sessions, _switch(True):
            resp = client.get("/api/v1/prompt-templates/changes")
            bad = client.get("/api/v1/prompt-templates/changes", params={"status": "unknown"})
        assert resp.status_code == 200 and resp.json()["maker_checker"] is True
        assert resp.json()["changes"][0]["id"] == str(request.id) and bad.status_code == 422

    def test_a_request_is_shown_beside_the_template_as_it_is_now(self):
        template = _template()
        request = _request(template)
        client, sessions = _client(_Session([request, template]), CHECKER)
        with sessions:
            resp = client.get(f"/api/v1/prompt-templates/changes/{request.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["proposed"] == request.proposed and body["current"]["template_text"] == template.template_text

    def test_approve_reject_and_withdraw(self):
        template = _template()
        request = _request(template)
        client, sessions = _client(_Session([request, template]), CHECKER)
        with sessions:
            approved = client.post(f"/api/v1/prompt-templates/changes/{request.id}/approve", json={"note": "ok"})
        assert approved.status_code == 200 and approved.json()["status"] == "approved"
        own = _request(template)
        client, sessions = _client(_Session([own, own]), MAKER)
        with sessions:
            refused = client.post(f"/api/v1/prompt-templates/changes/{own.id}/approve", json={})
            withdrawn = client.post(f"/api/v1/prompt-templates/changes/{own.id}/withdraw")
        assert refused.status_code == 403
        assert withdrawn.status_code == 200 and withdrawn.json()["status"] == "withdrawn"
        other = _request(template)
        client, sessions = _client(_Session([other, other]), CHECKER)
        with sessions:
            no_note = client.post(f"/api/v1/prompt-templates/changes/{other.id}/reject", json={})
            rejected = client.post(f"/api/v1/prompt-templates/changes/{other.id}/reject", json={"note": "wrong tone"})
        assert no_note.status_code == 422 and rejected.status_code == 200 and rejected.json()["status"] == "rejected"

    def test_a_stale_approval_is_a_409_that_says_nothing_was_applied(self):
        moved_on = _template(updated_at=STAMP + timedelta(minutes=5))
        request = _request(moved_on)
        client, sessions = _client(_Session([request, moved_on]), CHECKER)
        with sessions:
            resp = client.post(f"/api/v1/prompt-templates/changes/{request.id}/approve", json={})
        assert resp.status_code == 409 and resp.json()["detail"]["error"] == "stale_change_request"
        assert request.status == "stale"


class TestStorage:
    def test_the_table_is_tenant_scoped_and_the_checker_differs_from_the_maker(self):
        migration = (ROOT / "migrations" / "versions" / "v6_z43_prompt_change_requests.py").read_text(encoding="utf-8")
        assert 'down_revision = "v6z42_synthetic_checks"' in migration
        assert "ALTER TABLE prompt_change_requests FORCE ROW LEVEL SECURITY;" in migration
        assert "decided_by <> requested_by" in migration
        assert "ADD COLUMN IF NOT EXISTS approved_by" in migration
        assert "ix_prompt_change_requests_template" in migration and "(template_id, status)" in migration
