"""core/ownership.py — the single source of truth for per-user ownership.

Bug sheet 2026-09-14 rows 17/18/19/22/29/30/52. Every route that shows,
changes, links, or approves an agent or connector delegates to these rules,
so the rule table is pinned here exhaustively (tenant A vs B, owner vs
non-owner, admin vs domain role vs developer vs machine credential).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core import ownership as own

ALICE = uuid.uuid4()
BOB = uuid.uuid4()


def _caller(role: str, user_id=ALICE, domains=None, admin=False, machine=False) -> own.Caller:
    return own.Caller(user_id=user_id, role=role, domains=domains, is_admin=admin, is_machine=machine)


ADMIN = _caller("admin", user_id=uuid.uuid4(), admin=True)
CFO_ALICE = _caller("cfo", domains=["finance"])
CFO_BOB = _caller("cfo", user_id=BOB, domains=["finance"])
CHRO_BOB = _caller("chro", user_id=BOB, domains=["hr"])
DEV_ALICE = _caller("developer", domains=["ops"])
ANALYST = _caller("analyst", domains=["finance"])
API_KEY = own.Caller(user_id=None, role="", domains=None, is_admin=False, is_machine=True)


def _agent(domain="finance", visibility="tenant", owner=None):
    return SimpleNamespace(domain=domain, visibility=visibility, owner_user_id=owner)


def _connector(owner=None):
    return SimpleNamespace(owner_user_id=owner)


class TestAgentVisibility:
    def test_tenant_agent_follows_domain_rules(self):
        agent = _agent("finance")
        assert own.can_view_agent(agent, CFO_ALICE)
        assert own.can_view_agent(agent, ADMIN)
        assert own.can_view_agent(agent, API_KEY)
        assert not own.can_view_agent(agent, CHRO_BOB)

    def test_personal_agent_is_owner_and_admin_only(self):
        agent = _agent("finance", "personal", ALICE)
        assert own.can_view_agent(agent, CFO_ALICE)
        assert own.can_view_agent(agent, ADMIN)
        # Same role, same domain, different user: invisible (row 22/30 leak).
        assert not own.can_view_agent(agent, CFO_BOB)
        assert not own.can_view_agent(agent, API_KEY)

    def test_ownerless_personal_agent_is_admin_only(self):
        agent = _agent("finance", "personal", None)
        assert own.can_view_agent(agent, ADMIN)
        assert not own.can_view_agent(agent, CFO_ALICE)

    def test_developer_sees_own_personal_agent_outside_domain(self):
        agent = _agent("finance", "personal", ALICE)
        assert own.can_view_agent(agent, DEV_ALICE)

    def test_hidden_agent_is_404_not_403(self):
        with pytest.raises(HTTPException) as exc:
            own.require_agent_visible(_agent("finance", "personal", ALICE), CFO_BOB)
        assert exc.value.status_code == 404


class TestAgentMutation:
    def test_tenant_agent_mutation_stays_admin_only(self):
        agent = _agent("finance")
        assert own.can_mutate_agent(agent, ADMIN)
        assert not own.can_mutate_agent(agent, CFO_ALICE)
        with pytest.raises(HTTPException) as exc:
            own.require_agent_mutable(agent, CFO_ALICE)
        assert exc.value.status_code == 403

    def test_owner_mutates_own_personal_agent(self):
        agent = _agent("finance", "personal", ALICE)
        assert own.can_mutate_agent(agent, CFO_ALICE)
        assert not own.can_mutate_agent(agent, CFO_BOB)

    def test_only_admin_changes_visibility(self):
        agent = _agent("finance", "personal", ALICE)
        own.check_agent_visibility_change(agent, "tenant", ADMIN)
        own.check_agent_visibility_change(agent, "personal", CFO_ALICE)  # unchanged value
        with pytest.raises(HTTPException) as exc:
            own.check_agent_visibility_change(agent, "tenant", CFO_ALICE)
        assert exc.value.status_code == 403

    def test_domain_change_rules(self):
        own.check_agent_domain_change(_agent(), "hr", ADMIN)
        own.check_agent_domain_change(_agent("finance", "personal", ALICE), "hr", DEV_ALICE)
        with pytest.raises(HTTPException):
            own.check_agent_domain_change(_agent("finance", "personal", ALICE), "hr", CFO_ALICE)


class TestNewAgentOwnership:
    def test_admin_defaults_to_tenant_agent(self):
        assert own.resolve_new_agent_ownership(ADMIN, None, "hr") == ("tenant", None)

    def test_admin_may_create_personal_agent_for_self(self):
        assert own.resolve_new_agent_ownership(ADMIN, "personal", "hr") == ("personal", ADMIN.user_id)

    def test_domain_role_gets_personal_agent_in_own_domain(self):
        assert own.resolve_new_agent_ownership(CFO_ALICE, None, "finance") == ("personal", ALICE)

    def test_domain_role_cannot_create_tenant_agent(self):
        with pytest.raises(HTTPException) as exc:
            own.resolve_new_agent_ownership(CFO_ALICE, "tenant", "finance")
        assert exc.value.status_code == 403

    def test_domain_role_cannot_leave_domain(self):
        with pytest.raises(HTTPException) as exc:
            own.resolve_new_agent_ownership(CFO_ALICE, None, "hr")
        assert exc.value.status_code == 403

    def test_developer_may_pick_any_domain(self):
        assert own.resolve_new_agent_ownership(DEV_ALICE, None, "finance") == ("personal", ALICE)

    def test_analyst_and_machine_cannot_create(self):
        for caller in (ANALYST, API_KEY):
            with pytest.raises(HTTPException) as exc:
                own.resolve_new_agent_ownership(caller, None, "finance")
            assert exc.value.status_code == 403


class TestConnectors:
    def test_shared_connector_visible_to_all_mutable_by_admin(self):
        conn = _connector()
        assert own.can_view_connector(conn, CFO_ALICE)
        assert own.can_view_connector(conn, API_KEY)
        assert own.can_mutate_connector(conn, ADMIN)
        assert not own.can_mutate_connector(conn, CFO_ALICE)

    def test_personal_connector_owner_and_admin_only(self):
        conn = _connector(ALICE)
        assert own.can_view_connector(conn, CFO_ALICE)
        assert own.can_view_connector(conn, ADMIN)
        assert not own.can_view_connector(conn, CFO_BOB)
        assert not own.can_view_connector(conn, API_KEY)
        assert own.can_mutate_connector(conn, CFO_ALICE)
        assert not own.can_mutate_connector(conn, CFO_BOB)

    def test_new_connector_owner(self):
        assert own.resolve_new_connector_owner(ADMIN) is None
        assert own.resolve_new_connector_owner(CFO_ALICE) == ALICE
        for caller in (ANALYST, API_KEY):
            with pytest.raises(HTTPException):
                own.resolve_new_connector_owner(caller)

    def test_personal_connector_links_only_to_owners_personal_agent(self):
        conn = _connector(ALICE)
        assert own.connector_link_allowed(conn, "personal", ALICE)
        assert not own.connector_link_allowed(conn, "personal", BOB)
        # A shared agent would hand Alice's credentials to every user.
        assert not own.connector_link_allowed(conn, "tenant", None)
        assert own.connector_link_allowed(_connector(), "tenant", None)


class TestApprovals:
    def test_personal_agent_items_owner_or_admin(self):
        agent = _agent("finance", "personal", ALICE)
        assert own.personal_approval_decision(agent, CFO_ALICE) is True
        assert own.personal_approval_decision(agent, ADMIN) is True
        assert own.personal_approval_decision(agent, CFO_BOB) is False

    def test_tenant_items_use_normal_hierarchy(self):
        assert own.personal_approval_decision(_agent("finance"), CFO_ALICE) is None

    def test_developer_decides_only_own_personal_items(self):
        assert own.personal_approval_decision(_agent("finance"), DEV_ALICE) is False
        assert own.personal_approval_decision(_agent("finance", "personal", ALICE), DEV_ALICE) is True


class TestSqlClauses:
    """The SQL clauses must compile and reference the ownership columns."""

    def _compile(self, clause) -> str:
        from sqlalchemy.dialects import postgresql

        return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    def test_agent_list_clause_for_domain_role(self):
        from core.models.agent import Agent

        sql = self._compile(own.agent_visibility_clause(Agent, CFO_ALICE))
        assert "agents.visibility = 'tenant'" in sql
        assert "agents.domain IN ('finance')" in sql
        assert "agents.visibility = 'personal'" in sql
        assert "agents.owner_user_id" in sql

    def test_agent_list_clause_for_machine_is_tenant_only(self):
        from core.models.agent import Agent

        sql = self._compile(own.agent_visibility_clause(Agent, API_KEY))
        assert "personal" not in sql

    def test_connector_clause(self):
        from core.models.connector import Connector

        assert "owner_user_id IS NULL" in self._compile(own.connector_visibility_clause(Connector, CFO_ALICE))
        assert self._compile(own.connector_visibility_clause(Connector, ADMIN)) == "true"

    def test_approval_clause_developer_is_own_only(self):
        from core.models.agent import Agent

        sql = self._compile(own.approval_visibility_clause(Agent, DEV_ALICE))
        assert "'tenant'" not in sql and "owner_user_id" in sql
