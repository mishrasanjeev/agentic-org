# SPDX-License-Identifier: Apache-2.0
"""The catalogue: registry entries filtered and searched, and the templates the packs offer."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from api.v1 import agent_registry as api
from core.agent_registry import lifecycle
from core.config import settings
from core.models.agent_registry import AgentRegistryEntry

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


def _agent(name, agent_type, domain, description="") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        agent_type=agent_type,
        domain=domain,
        description=description,
        status="active",
        owner_user_id=None,
        visibility="tenant",
    )


def _entry(agent, state="approved", **over) -> AgentRegistryEntry:
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


class TestFilters:
    def test_the_query_carries_the_stored_filters(self):
        session = _Session([])
        asyncio.run(
            lifecycle.list_entries(
                session, TENANT, state="approved", risk_tier="high", use_case="claims", channel="chat"
            )
        )
        statement = session.statements[0]
        for fragment in (
            "agent_registry.tenant_id",
            "agent_registry.state = ",
            "agent_registry.risk_tier = ",
            "agent_registry.use_case = ",
            "agent_registry.channels @> ",
        ):
            assert fragment in statement, fragment

    def test_a_search_term_is_matched_against_the_card_text(self):
        agent = _agent("Claims decider", "claims", "ops", "Decides simple motor claims.")
        entry = _entry(agent, purpose="Settle small claims fast", use_case="motor claims")
        for term in ("claims", "MOTOR", "settle", "decider", " "):
            assert lifecycle.matches_search(agent, entry, term)
        assert lifecycle.matches_search(agent, entry, None) and lifecycle.matches_search(agent, entry, "")
        assert not lifecycle.matches_search(agent, entry, "mortgage")


class TestCatalogueEndpoint:
    @pytest.fixture
    def store(self, monkeypatch):
        monkeypatch.setattr(settings, "agent_registry_enabled", True)
        holder: dict[str, _Session] = {}

        @asynccontextmanager
        async def _session(_tenant):
            yield holder["session"]

        monkeypatch.setattr(api, "get_tenant_session", _session)

        def install(*answers):
            holder["session"] = _Session(*answers)
            return holder["session"]

        return install

    def test_domain_and_search_are_applied_to_the_visible_agents(self, store):
        claims = _agent("Claims decider", "claims", "ops", "Decides simple motor claims.")
        loans = _agent("Loan analyst", "loan_underwriting_analyst", "finance")
        entries = [_entry(claims, use_case="motor claims"), _entry(loans)]
        store(entries, [claims, loans])
        listed = asyncio.run(
            api.list_agent_registry(domain="finance", tenant_id=str(TENANT), user_domains=None, caller=None)
        )
        assert [row["name"] for row in listed["entries"]] == ["Loan analyst"]
        assert listed["channels"] == list(lifecycle.CHANNELS)
        store(entries, [claims, loans])
        listed = asyncio.run(api.list_agent_registry(q="motor", tenant_id=str(TENANT), user_domains=None, caller=None))
        assert [row["name"] for row in listed["entries"]] == ["Claims decider"]
        assert listed["entries"][0]["environment"] == "staging"

    def test_the_templates_come_from_the_packs_in_the_cards_terms(self, store):
        result = asyncio.run(api.list_agent_templates(pack="banking", tenant_id=str(TENANT)))
        rows = result["templates"]
        assert len(rows) == 5 and {row["agent_type"] for row in rows} >= {"kyc_reviewer", "collections_agent"}
        kyc = next(row for row in rows if row["agent_type"] == "kyc_reviewer")
        assert kyc["pack"] == "banking" and kyc["installable"] is True and kyc["domain"] == "ops"
        assert kyc["tools"] == ["knowledge_base_search"] and kyc["confidence_floor"] == 0.92
        assert kyc["hitl_condition"] and "KYC_AML" in kyc["compliance"]
        everything = asyncio.run(api.list_agent_templates(tenant_id=str(TENANT)))["templates"]
        assert len(everything) > 5 and {row["pack"] for row in everything} >= {"banking", "healthcare"}

    def test_off_nothing_is_read(self, monkeypatch):
        assert settings.agent_registry_enabled is False
        with pytest.raises(HTTPException) as refused:
            asyncio.run(api.list_agent_templates(tenant_id=str(TENANT)))
        assert refused.value.status_code == 409
