# SPDX-License-Identifier: Apache-2.0
"""Agent registry: the card, the card fields, the lifecycle transitions and the endpoints."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from api.v1 import agent_registry as api
from core.agent_registry import lifecycle
from core.config import settings
from core.evals import gates, runs
from core.models.agent_registry import AgentRegistryEntry, AgentRegistryEvent
from core.schemas.api import AgentCardIn, AgentLifecycleIn

ROOT = Path(__file__).resolve().parents[2]
TENANT = uuid.uuid4()
MAKER = uuid.uuid4()
CHECKER = uuid.uuid4()


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


def _agent(**over) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "name": "Claims decider",
        "agent_type": "claims",
        "domain": "ops",
        "description": "Decides simple claims.",
        "version": "1.2.0",
        "status": "shadow",
        "maturity": "beta",
        "visibility": "tenant",
        "owner_user_id": MAKER,
        "is_builtin": False,
        "tags": ["claims"],
        "llm_model": "gpt-4o",
        "llm_provider": "openai",
        "llm_fallback": "gpt-4o-mini",
        "llm_config": {"routing": {"policy": "cost"}},
        "authorized_tools": ["zoho_books:list_invoices"],
        "connector_ids": [uuid.uuid4()],
        "config": {
            "grantex": {"grantex_scopes": ["claims:read"], "route_scopes": []},
            "output_schema_json": {"type": "object"},
        },
        "output_schema": None,
        "system_prompt_ref": "claims/v3",
        "system_prompt_text": "Decide the claim.",
        "prompt_variables": {"org": "x"},
        "prompt_amendments": [{"text": "Be brief."}],
        "confidence_floor": Decimal("0.800"),
        "hitl_condition": "confidence < 0.8",
        "cost_controls": {"daily_usd": 5},
        "created_at": datetime(2026, 10, 7, tzinfo=UTC),
        "updated_at": datetime(2026, 10, 7, tzinfo=UTC),
    }
    base.update(over)
    return SimpleNamespace(**base)


def _entry(agent, state="draft", **over) -> AgentRegistryEntry:
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "agent_id": agent.id,
        "purpose": None,
        "risk_tier": None,
        "use_case": None,
        "channels": [],
        "state": state,
        "state_changed_at": datetime(2026, 10, 7, tzinfo=UTC),
        "state_changed_by": None,
        "submitted_by": None,
        "created_at": datetime(2026, 10, 7, tzinfo=UTC),
        "updated_at": datetime(2026, 10, 7, tzinfo=UTC),
    }
    base.update(over)
    return AgentRegistryEntry(**base)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(settings, "agent_registry_enabled", True)


class TestCardFields:
    def test_fields_are_checked_and_only_the_given_ones_change(self):
        assert lifecycle.parse_card_fields(
            {"purpose": " Decide simple claims. ", "channels": ["chat", "api", "chat"]}
        ) == {
            "purpose": "Decide simple claims.",
            "channels": ["api", "chat"],
        }
        assert lifecycle.parse_card_fields({"risk_tier": None, "use_case": ""}) == {"risk_tier": None, "use_case": None}
        assert lifecycle.parse_card_fields({}) == {}

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ({"owner": "x"}, "unknown card fields"),
            ({"purpose": "p" * 2001}, "at most 2000"),
            ({"risk_tier": "extreme"}, "risk_tier must be one of"),
            ({"channels": ["fax"]}, "channels is a list of"),
            ({"channels": "chat"}, "channels is a list of"),
        ],
    )
    def test_refused_shapes(self, raw, message):
        with pytest.raises(lifecycle.RegistryError, match=message) as refused:
            lifecycle.parse_card_fields(raw)
        assert refused.value.status == 422

    def test_setting_fields_creates_the_entry_as_draft_under_a_lock(self):
        agent = _agent()
        session = _Session(None)
        entry = asyncio.run(lifecycle.set_card_fields(session, TENANT, agent.id, {"purpose": "p", "risk_tier": "high"}))
        assert (
            session.added == [entry] and entry.state == "draft" and entry.purpose == "p" and entry.risk_tier == "high"
        )
        assert "FOR UPDATE" in session.statements[0] and "agent_registry.tenant_id" in session.statements[0]


class TestTransitions:
    def _move(self, agent, entry, to, actor=CHECKER, note=None):
        session = _Session(entry)
        result = asyncio.run(lifecycle.transition(session, TENANT, agent, to, actor=actor, note=note))
        return result, session

    def test_the_table_is_followed_and_every_move_is_recorded(self):
        assert lifecycle.TRANSITIONS == {
            "draft": ("review",),
            "review": ("draft", "approved"),
            "approved": ("draft", "review", "published"),
            "published": ("deprecated",),
            "deprecated": ("retired",),
            "retired": (),
        }
        agent = _agent()
        (entry, event), session = self._move(agent, _entry(agent), "review", actor=MAKER, note="ready")
        assert (entry.state, entry.submitted_by, entry.state_changed_by) == ("review", MAKER, MAKER)
        assert (event.from_state, event.to_state, event.actor_user_id, event.note) == (
            "draft",
            "review",
            MAKER,
            "ready",
        )
        assert session.added == [event]

    def test_a_move_the_table_does_not_allow_is_refused(self):
        agent = _agent()
        for state, to in (("draft", "published"), ("retired", "draft"), ("published", "review")):
            with pytest.raises(lifecycle.RegistryError, match="can move to") as refused:
                self._move(agent, _entry(agent, state), to)
            assert (refused.value.status, refused.value.code) == (409, "transition")
        with pytest.raises(lifecycle.RegistryError, match="state must be one of"):
            self._move(agent, _entry(agent), "live")

    def test_the_submitter_cannot_approve(self):
        agent = _agent()
        with pytest.raises(lifecycle.RegistryError, match="cannot approve") as refused:
            self._move(agent, _entry(agent, "review", submitted_by=MAKER), "approved", actor=MAKER)
        assert refused.value.code == "same_person"

    def test_a_transition_without_a_signed_in_user_is_refused(self):
        agent = _agent()
        with pytest.raises(lifecycle.RegistryError, match="signed-in user") as refused:
            self._move(agent, _entry(agent), "review", actor=None)
        assert (refused.value.status, refused.value.code) == (403, "no_actor")
        # The runtime may record a consequence of its own change without a person; approval never.
        active = _agent(status="active")
        entry, event = asyncio.run(
            lifecycle.transition(
                _Session(_entry(active, "approved")), TENANT, active, "published", actor=None, require_actor=False
            )
        )
        assert entry.state == "published" and event.actor_user_id is None
        with pytest.raises(lifecycle.RegistryError, match="cannot approve"):
            asyncio.run(
                lifecycle.transition(
                    _Session(_entry(agent, "review")), TENANT, agent, "approved", actor=None, require_actor=False
                )
            )
        (entry, _), _ = self._move(agent, _entry(agent, "review", submitted_by=MAKER), "approved", actor=CHECKER)
        assert entry.state == "approved"

    def test_only_an_active_agent_is_published(self):
        with pytest.raises(lifecycle.RegistryError, match="Only an active agent") as refused:
            self._move(_agent(status="shadow"), _entry(_agent(), "approved"), "published")
        assert refused.value.code == "not_active"
        (entry, _), _ = self._move(_agent(status="active"), _entry(_agent(), "approved"), "published")
        assert entry.state == "published"

    def test_a_note_is_bounded(self):
        with pytest.raises(lifecycle.RegistryError, match="note is text"):
            self._move(_agent(), _entry(_agent()), "review", note="n" * 501)


class TestCard:
    def test_the_card_summarises_the_agent_without_its_prompt_text(self, monkeypatch):
        async def _no_gate(_session, _tenant, _agent):
            return gates.Verdict(declared=False, enforced=False, ok=True, code="no_gate", message="No evaluation gate")

        monkeypatch.setattr(gates, "evaluate", _no_gate)
        from core.agent_registry import reliability

        async def _metrics(_session, _tenant, _agent, days=30):
            return {"window_days": days, "runs": 0}

        async def _summary(_session, _tenant, _agent_id):
            return {"count": 0, "average": None}

        async def _certification(_session, _tenant, _agent, *, registry_state, gate_verdict):
            return {"registry_state": registry_state, "trust_registry": {"attached": False}}

        monkeypatch.setattr(reliability, "metrics", _metrics)
        monkeypatch.setattr(reliability, "rating_summary", _summary)
        monkeypatch.setattr(reliability, "certification", _certification)
        agent = _agent()
        entry = _entry(
            agent, "review", purpose="Decide simple claims.", risk_tier="high", channels=["chat"], submitted_by=MAKER
        )
        card = asyncio.run(lifecycle.card(_Session(entry), TENANT, agent))
        assert card["name"] == "Claims decider" and card["status"] == "shadow" and card["owner_user_id"] == str(MAKER)
        assert card["registry"]["state"] == "review" and card["registry"]["next_states"] == ["draft", "approved"]
        assert card["registry"]["purpose"] == "Decide simple claims." and card["registry"]["risk_tier"] == "high"
        assert card["models"] == {
            "model": "gpt-4o",
            "provider": "openai",
            "fallback": "gpt-4o-mini",
            "routing": {"policy": "cost"},
        }
        assert card["tools"] == ["zoho_books:list_invoices"]
        assert (
            card["permissions"]["grantex_scopes"] == ["claims:read"] and len(card["permissions"]["connector_ids"]) == 1
        )
        assert card["schemas"] == {"output_schema": None, "own_schema": True}
        assert card["prompt"] == {
            "ref": "claims/v3",
            "hash": runs.prompt_hash("Decide the claim."),
            "variables": 1,
            "amendments": 1,
        }
        assert card["controls"]["confidence_floor"] == 0.8 and card["evaluation_gate"]["verdict"]["code"] == "no_gate"
        assert card["rating"] == {"count": 0, "average": None} and card["reliability"]["window_days"] == 30
        assert card["certification"]["registry_state"] == "review"
        assert "Decide the claim." not in str(card)

    def test_an_agent_without_an_entry_is_a_draft(self, monkeypatch):
        async def _no_gate(_session, _tenant, _agent):
            return gates.Verdict(declared=False, enforced=False, ok=True, code="no_gate")

        monkeypatch.setattr(gates, "evaluate", _no_gate)
        from core.agent_registry import reliability

        async def _metrics(_session, _tenant, _agent, days=30):
            return {"window_days": days, "runs": 0}

        async def _summary(_session, _tenant, _agent_id):
            return {"count": 0, "average": None}

        async def _certification(_session, _tenant, _agent, *, registry_state, gate_verdict):
            return {"registry_state": registry_state, "trust_registry": {"attached": False}}

        monkeypatch.setattr(reliability, "metrics", _metrics)
        monkeypatch.setattr(reliability, "rating_summary", _summary)
        monkeypatch.setattr(reliability, "certification", _certification)
        card = asyncio.run(lifecycle.card(_Session(None), TENANT, _agent(system_prompt_text=None)))
        assert card["registry"]["state"] == "draft" and card["registry"]["next_states"] == ["review"]
        assert card["prompt"]["hash"] is None


class TestEndpoints:
    @pytest.fixture
    def store(self, monkeypatch):
        holder: dict[str, _Session] = {}

        @asynccontextmanager
        async def _session(_tenant):
            yield holder["session"]

        async def _no_gate(_session, _tenant, _agent):
            return gates.Verdict(declared=False, enforced=False, ok=True, code="no_gate")

        monkeypatch.setattr(api, "get_tenant_session", _session)
        monkeypatch.setattr(api, "require_agent_mutable", lambda _agent, _caller: None)
        monkeypatch.setattr(api, "require_agent_visible", lambda _agent, _caller: None)
        monkeypatch.setattr(gates, "evaluate", _no_gate)
        from core.agent_registry import reliability

        async def _metrics(_session, _tenant, _agent, days=30):
            return {"window_days": days, "runs": 0}

        async def _summary(_session, _tenant, _agent_id):
            return {"count": 0, "average": None}

        async def _certification(_session, _tenant, _agent, *, registry_state, gate_verdict):
            return {"registry_state": registry_state, "trust_registry": {"attached": False}}

        monkeypatch.setattr(reliability, "metrics", _metrics)
        monkeypatch.setattr(reliability, "rating_summary", _summary)
        monkeypatch.setattr(reliability, "certification", _certification)

        def install(*answers):
            holder["session"] = _Session(*answers)
            return holder["session"]

        return install

    def test_off_by_default_nothing_is_read_or_written(self):
        assert settings.agent_registry_enabled is False
        agent_id = uuid.uuid4()
        for call in (
            api.get_agent_card(agent_id, tenant_id=str(TENANT), user_domains=None, caller=None),
            api.set_agent_card(
                agent_id, AgentCardIn(purpose="p"), tenant_id=str(TENANT), user_domains=None, caller=None
            ),
            api.transition_agent_lifecycle(
                agent_id, AgentLifecycleIn(to="review"), tenant_id=str(TENANT), user={}, user_domains=None, caller=None
            ),
            api.get_agent_lifecycle(agent_id, tenant_id=str(TENANT), user_domains=None, caller=None),
            api.list_agent_registry(tenant_id=str(TENANT), user_domains=None, caller=None),
        ):
            with pytest.raises(HTTPException) as refused:
                asyncio.run(call)
            assert refused.value.status_code == 409 and "off in this deployment" in refused.value.detail

    def test_unknown_card_fields_are_refused_at_the_boundary(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AgentCardIn(owner="someone")
        with pytest.raises(ValidationError):
            AgentLifecycleIn(to="review", force=True)

    def test_the_card_is_read_and_its_fields_written(self, on, store):
        agent = _agent()
        store(agent, None)
        card = asyncio.run(api.get_agent_card(agent.id, tenant_id=str(TENANT), user_domains=None, caller=None))
        assert card["id"] == str(agent.id) and card["registry"]["state"] == "draft"
        session = store(agent, None, None)
        card = asyncio.run(
            api.set_agent_card(
                agent.id,
                AgentCardIn(purpose="Decide simple claims.", risk_tier="high"),
                tenant_id=str(TENANT),
                user_domains=None,
                caller=None,
            )
        )
        [entry] = session.added
        assert entry.purpose == "Decide simple claims." and entry.risk_tier == "high"
        # The agent row is locked before the entry is created.
        assert "FOR UPDATE" in session.statements[0] and "agents.tenant_id" in session.statements[0]
        store(agent)
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                api.set_agent_card(
                    agent.id, AgentCardIn(risk_tier="extreme"), tenant_id=str(TENANT), user_domains=None, caller=None
                )
            )
        assert refused.value.status_code == 422 and refused.value.detail["error"] == "invalid"

    def test_a_transition_locks_the_agent_and_records_who_moved_it(self, on, store):
        agent = _agent()
        session = store(agent, _entry(agent))
        result = asyncio.run(
            api.transition_agent_lifecycle(
                agent.id,
                AgentLifecycleIn(to="review", note="ready"),
                tenant_id=str(TENANT),
                user={"agenticorg:user_id": str(MAKER)},
                user_domains=None,
                caller=None,
            )
        )
        assert "FOR UPDATE" in session.statements[0] and "agents.tenant_id" in session.statements[0]
        assert result["registry"]["state"] == "review" and result["event"]["actor_user_id"] == str(MAKER)
        [event] = session.added
        assert isinstance(event, AgentRegistryEvent) and event.note == "ready"
        store(agent, _entry(agent, "review", submitted_by=MAKER))
        with pytest.raises(HTTPException) as refused:
            asyncio.run(
                api.transition_agent_lifecycle(
                    agent.id,
                    AgentLifecycleIn(to="approved"),
                    tenant_id=str(TENANT),
                    user={"agenticorg:user_id": str(MAKER)},
                    user_domains=None,
                    caller=None,
                )
            )
        assert refused.value.status_code == 409 and refused.value.detail["error"] == "same_person"
        store(None)
        with pytest.raises(HTTPException) as missing:
            asyncio.run(
                api.transition_agent_lifecycle(
                    uuid.uuid4(),
                    AgentLifecycleIn(to="review"),
                    tenant_id=str(TENANT),
                    user={"agenticorg:user_id": str(MAKER)},
                    user_domains=None,
                    caller=None,
                )
            )
        assert missing.value.status_code == 404
        # An API key, a delegated credential or a malformed claim cannot move an agent.
        for claims in ({}, {"sub": "someone@example.com"}, {"agenticorg:user_id": "not-a-uuid"}):
            session = store(agent)
            with pytest.raises(HTTPException) as refused:
                asyncio.run(
                    api.transition_agent_lifecycle(
                        agent.id,
                        AgentLifecycleIn(to="review"),
                        tenant_id=str(TENANT),
                        user=claims,
                        user_domains=None,
                        caller=None,
                    )
                )
            assert refused.value.status_code == 403 and session.statements == []

    def test_the_lifecycle_and_the_registry_list_are_read(self, on, store):
        agent = _agent()
        entry = _entry(agent, "approved")
        event = AgentRegistryEvent(
            id=uuid.uuid4(),
            tenant_id=TENANT,
            agent_id=agent.id,
            from_state="review",
            to_state="approved",
            actor_user_id=CHECKER,
            note=None,
            created_at=datetime(2026, 10, 7, tzinfo=UTC),
        )
        store(agent, entry, [event])
        result = asyncio.run(api.get_agent_lifecycle(agent.id, tenant_id=str(TENANT), user_domains=None, caller=None))
        assert result["registry"]["state"] == "approved" and [item["to_state"] for item in result["events"]] == [
            "approved"
        ]
        assert result["transitions"]["approved"] == ["draft", "review", "published"]
        session = store([entry], [agent])
        listed = asyncio.run(
            api.list_agent_registry(state="approved", tenant_id=str(TENANT), user_domains=None, caller=None)
        )
        assert [row["agent_id"] for row in listed["entries"]] == [str(agent.id)]
        assert listed["entries"][0]["name"] == "Claims decider" and listed["entries"][0]["state"] == "approved"
        assert (
            "agent_registry.state = " in session.statements[0] and "agent_registry.tenant_id" in session.statements[0]
        )
        assert listed["risk_tiers"] == ["low", "medium", "high", "critical"]


class TestMigration:
    def test_both_tables_are_tenant_scoped_with_one_entry_per_agent(self):
        src = (ROOT / "migrations" / "versions" / "v6_z48_agent_registry.py").read_text(encoding="utf-8")
        assert 'down_revision = "v6z47_eval_run_tokens"' in src
        assert "FORCE ROW LEVEL SECURITY" in src and '_TABLES = ("agent_registry", "agent_registry_events")' in src
        assert "ux_agent_registry_agent ON agent_registry(agent_id)" in src
        assert "ON agent_registry_events(agent_id, created_at)" in src
        for state in lifecycle.STATES:
            assert f"'{state}'" in src
        # The checks are declared on the models and added outside the table creation too.
        from core.models.agent_registry import AgentRegistryEntry, AgentRegistryEvent

        names = {c.name for table in (AgentRegistryEntry, AgentRegistryEvent) for c in table.__table__.constraints}
        assert {"ck_agent_registry_state", "ck_agent_registry_risk_tier", "ck_agent_registry_events_to"} <= names
        for name in ("ck_agent_registry_state", "ck_agent_registry_risk_tier", "ck_agent_registry_events_to"):
            assert src.count(name) >= 2, name
        assert "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname" in src
