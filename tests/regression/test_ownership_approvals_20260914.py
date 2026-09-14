# ruff: noqa: S106 — test files use fake tokens intentionally
"""Bug sheet 2026-09-14 row 30 — per-user ownership on approvals and the
cross-module leak sites (chat routing, workflows, audit, KPIs, push).

Tester scenario: CFO A builds a personal agent; CFO B (same tenant, same
finance domain) must not see A's approval items, decide them, route chat to
A's agent, reference it from a workflow, read its audit rows, see its items
on the shared KPI dashboards, or receive its approval push.

Handler-level tests drive the real route functions with a request double
carrying per-caller ``agenticorg:user_id``; list/filter behaviour is asserted
on the compiled SQL because the ORM targets Postgres-only types. The chat
agent_id case replays the tester's step through TestClient.
"""

from __future__ import annotations

import inspect
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from tests.company_scope import TEST_COMPANY_ID, TEST_TENANT_ID, owned_company_validator

TENANT = str(TEST_TENANT_ID)
COMPANY = str(TEST_COMPANY_ID)
USER_A = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
USER_B = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
ADMIN = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
DEV = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")

DOMAIN_ROLE_SCOPES = [
    "agents:read",
    "agents:write",
    "workflows:read",
    "workflows:write",
    "approvals:read",
    "approvals:write",
    "audit:read",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(role: str, user_id: uuid.UUID | None, domains: list[str] | None, auth_mode: str = "legacy"):
    claims: dict = {"sub": f"{role}@x.io", "role": role, "agenticorg:domains": domains}
    if user_id is not None:
        claims["agenticorg:user_id"] = str(user_id)
    scopes = ["agenticorg:admin"] if role == "admin" else list(DOMAIN_ROLE_SCOPES)
    return SimpleNamespace(state=SimpleNamespace(claims=claims, scopes=scopes, auth_mode=auth_mode))


def _cfo_a():
    return _request("cfo", USER_A, ["finance"])


def _cfo_b():
    return _request("cfo", USER_B, ["finance"])


def _admin():
    return _request("admin", ADMIN, None)


def _agent(*, visibility: str = "tenant", owner: uuid.UUID | None = None, domain: str = "finance", **extra):
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TEST_TENANT_ID,
        "company_id": TEST_COMPANY_ID,
        "name": f"{domain}-agent",
        "employee_name": f"{domain} agent",
        "agent_type": "ap_processor",
        "domain": domain,
        "status": "active",
        "visibility": visibility,
        "owner_user_id": owner,
        "authorized_tools": [],
        "connector_ids": [],
        "system_prompt_text": "You are an agent.",
        "llm_provider": None,
        "llm_config": {},
    }
    base.update(extra)
    return SimpleNamespace(**base)


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


class _QueueSession:
    """Async session double: each execute pops the next value; records statements."""

    def __init__(self, *values):
        self.values = list(values)
        self.statements: list = []
        self.added: list = []

    async def execute(self, stmt, *_a, **_k):
        self.statements.append(stmt)
        value = self.values.pop(0) if self.values else None
        res = MagicMock()
        res.scalar_one_or_none.return_value = value
        res.scalar_one.return_value = value
        res.scalar.return_value = value if isinstance(value, int) else 0
        res.scalars.return_value.all.return_value = value if isinstance(value, list) else []
        res.all.return_value = value if isinstance(value, list) else []
        return res

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None


def _session_patch(target: str, session):
    @asynccontextmanager
    async def _fake(*_a, **_k):
        yield session

    return patch(target, _fake)


def _hitl(agent, **extra):
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TEST_TENANT_ID,
        "agent_id": agent.id,
        "workflow_run_id": None,
        "title": "Approve payment",
        "trigger_type": "policy_condition",
        "priority": "high",
        "status": "pending",
        "assignee_role": "cfo",
        "decision_options": {"options": ["approve", "reject"]},
        "context": {},
        "decision": None,
        "decision_by": None,
        "decision_at": None,
        "decision_notes": None,
        "requested_by_user_id": None,
        "expires_at": datetime.now(UTC) + timedelta(hours=4),
        "created_at": datetime.now(UTC),
    }
    base.update(extra)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# /approvals list
# ---------------------------------------------------------------------------


class TestApprovalList:
    async def _list_sql(self, request) -> str:
        from api.v1.approvals import list_approvals

        session = _QueueSession(0, [])
        with _session_patch("api.v1.approvals.get_tenant_session", session):
            await list_approvals(request=request, tenant_id=TENANT)
        return _sql(session.statements[0])

    @pytest.mark.asyncio
    async def test_cfo_b_list_excludes_other_users_personal_items(self):
        sql = await self._list_sql(_cfo_b())
        assert "agents.visibility = 'tenant'" in sql
        assert "agents.domain IN ('finance')" in sql
        # Personal rows only when B owns them — never A's.
        assert f"agents.owner_user_id = '{USER_B}'" in sql
        assert str(USER_A) not in sql

    @pytest.mark.asyncio
    async def test_admin_list_is_unfiltered(self):
        sql = await self._list_sql(_admin())
        assert "agents" not in sql

    @pytest.mark.asyncio
    async def test_developer_list_is_own_personal_items_only(self):
        sql = await self._list_sql(_request("developer", DEV, None))
        assert f"agents.owner_user_id = '{DEV}'" in sql
        assert "agents.visibility = 'tenant'" not in sql

    def test_dict_carries_requested_by_user_id(self):
        from api.v1.approvals import _hitl_to_dict

        item = _hitl(_agent(), requested_by_user_id=USER_A)
        assert _hitl_to_dict(item)["requested_by_user_id"] == str(USER_A)
        assert _hitl_to_dict(_hitl(_agent()))["requested_by_user_id"] is None


# ---------------------------------------------------------------------------
# /approvals/{id}/decide
# ---------------------------------------------------------------------------


class TestApprovalDecide:
    async def _decide(self, request, item, agent):
        from api.v1.approvals import decide
        from core.schemas.api import HITLDecision

        session = _QueueSession(item, agent)
        claims = request.state.claims
        with (
            _session_patch("api.v1.approvals.get_tenant_session", session),
            patch("core.approvals.resolve_policy", AsyncMock(return_value=None)),
            patch("core.feedback.shadow_learning.capture_hitl_feedback", AsyncMock(return_value={})),
        ):
            return await decide(
                hitl_id=item.id,
                body=HITLDecision(decision="approve"),
                background_tasks=BackgroundTasks(),
                request=request,
                tenant_id=TENANT,
                user_claims=claims,
                user_role=claims["role"],
                user_domains=claims["agenticorg:domains"],
            )

    @pytest.mark.asyncio
    async def test_cfo_b_deciding_a_personal_item_is_404(self):
        agent = _agent(visibility="personal", owner=USER_A)
        with pytest.raises(HTTPException) as exc:
            await self._decide(_cfo_b(), _hitl(agent), agent)
        assert exc.value.status_code == 404
        assert exc.value.detail == "HITL item not found"

    @pytest.mark.asyncio
    async def test_cfo_b_probe_on_decided_personal_item_is_still_404(self):
        """No 409 status leak for an item B cannot see."""
        agent = _agent(visibility="personal", owner=USER_A)
        with pytest.raises(HTTPException) as exc:
            await self._decide(_cfo_b(), _hitl(agent, status="decided"), agent)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cfo_a_decides_own_personal_item_despite_higher_assignee_role(self):
        agent = _agent(visibility="personal", owner=USER_A)
        item = _hitl(agent, assignee_role="ceo")  # cfo (30) < ceo (50) under the hierarchy
        resp = await self._decide(_cfo_a(), item, agent)
        assert resp["status"] == "decided"
        assert item.decision_by == USER_A

    @pytest.mark.asyncio
    async def test_cfo_a_on_shared_ceo_item_still_follows_hierarchy(self):
        agent = _agent()
        with pytest.raises(HTTPException) as exc:
            await self._decide(_cfo_a(), _hitl(agent, assignee_role="ceo"), agent)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_developer_deciding_shared_finance_item_is_denied(self):
        agent = _agent()
        with pytest.raises(HTTPException) as exc:
            await self._decide(_request("developer", DEV, None), _hitl(agent, assignee_role="staff"), agent)
        assert exc.value.status_code == 403
        assert "own personal agents" in exc.value.detail

    @pytest.mark.asyncio
    async def test_developer_decides_own_personal_item(self):
        agent = _agent(visibility="personal", owner=DEV, domain="hr")
        resp = await self._decide(_request("developer", DEV, None), _hitl(agent), agent)
        assert resp["status"] == "decided"

    @pytest.mark.asyncio
    async def test_admin_decides_personal_item(self):
        agent = _agent(visibility="personal", owner=USER_A)
        resp = await self._decide(_admin(), _hitl(agent), agent)
        assert resp["status"] == "decided"


# ---------------------------------------------------------------------------
# Chat routing and explicit agent_id
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app():
    from api.main import app as _app

    @asynccontextmanager
    async def _test_lifespan(app):
        yield

    _app.router.lifespan_context = _test_lifespan
    return _app


@contextmanager
def _client(app, *, role: str, domains: list[str] | None, user_id: uuid.UUID):
    claims = {
        "sub": f"{role}@x.io",
        "role": role,
        "agenticorg:tenant_id": TENANT,
        "agenticorg:domains": domains,
        "agenticorg:user_id": str(user_id),
        "grantex:scopes": DOMAIN_ROLE_SCOPES,
    }

    async def _fake_validate(token):
        return claims

    async def _noop(*_a, **_k):
        return None

    with (
        patch("auth.grantex_middleware.is_ip_blocked", return_value=False),
        patch("auth.grantex_middleware.record_auth_failure", side_effect=_noop),
        patch("auth.grantex_middleware.clear_auth_failures", side_effect=_noop),
        patch("auth.grantex_middleware.validate_token", side_effect=_fake_validate),
        patch("auth.grantex_middleware.extract_tenant_id", return_value=TENANT),
        patch("auth.grantex_middleware.extract_scopes", return_value=DOMAIN_ROLE_SCOPES),
        patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)),
        patch("api.v1.agents._require_company_for_tenant", side_effect=owned_company_validator()),
    ):
        with TestClient(app, raise_server_exceptions=False) as c:
            c.headers["Authorization"] = "Bearer fake-test-token"
            yield c


def _chat_session_factory(agent):
    def factory(*_a, **_k):
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=_QueueSession(*([agent] * 10)))
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    return factory


class TestChatOwnership:
    async def _routing_sql(self, caller) -> str:
        from api.v1.chat import _find_agent_for_domain

        session = _QueueSession(None)
        with _session_patch("api.v1.chat.get_tenant_session", session):
            await _find_agent_for_domain("finance", TENANT, TEST_COMPANY_ID, caller=caller)
        return _sql(session.statements[0])

    @pytest.mark.asyncio
    async def test_keyword_routing_for_b_only_reaches_shared_or_own_agents(self):
        from core.ownership import caller_from_request

        sql = await self._routing_sql(caller_from_request(_cfo_b()))
        assert "agents.visibility = 'tenant'" in sql
        assert "agents.visibility = 'personal'" in sql
        assert f"agents.owner_user_id = '{USER_B}'" in sql
        assert str(USER_A) not in sql

    @pytest.mark.asyncio
    async def test_keyword_routing_for_admin_never_lands_on_someone_elses_personal_agent(self):
        from core.ownership import caller_from_request

        sql = await self._routing_sql(caller_from_request(_admin()))
        assert f"agents.owner_user_id = '{ADMIN}'" in sql

    @pytest.mark.asyncio
    async def test_keyword_routing_without_caller_is_shared_only(self):
        sql = await self._routing_sql(None)
        assert "agents.visibility = 'tenant'" in sql
        assert "personal" not in sql

    def test_explicit_agent_id_of_a_personal_agent_from_b_is_404(self, app):
        personal = _agent(visibility="personal", owner=USER_A)
        with (
            _client(app, role="cfo", domains=["finance"], user_id=USER_B) as c,
            patch("api.v1.chat.get_tenant_session", side_effect=_chat_session_factory(personal)),
            patch("core.langgraph.runner.run_agent", AsyncMock()) as lg,
        ):
            resp = c.post(
                "/api/v1/chat/query", json={"query": "hello", "company_id": COMPANY, "agent_id": str(personal.id)}
            )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Agent not found"
        lg.assert_not_called()

    def test_owner_can_chat_with_own_personal_agent(self, app):
        personal = _agent(visibility="personal", owner=USER_A)
        lg = AsyncMock(
            return_value={"status": "completed", "output": {"answer": "ok"}, "confidence": 0.9, "performance": {}}
        )
        with (
            _client(app, role="cfo", domains=["finance"], user_id=USER_A) as c,
            patch("api.v1.chat.get_tenant_session", side_effect=_chat_session_factory(personal)),
            patch("api.v1.agents._resolve_agent_connector_ids_for_type", AsyncMock(return_value=[])),
            patch("api.v1.chat._record_cost_ledger", AsyncMock(return_value=True)),
            patch("core.langgraph.runner.run_agent", lg),
        ):
            resp = c.post(
                "/api/v1/chat/query", json={"query": "hello", "company_id": COMPANY, "agent_id": str(personal.id)}
            )
        assert resp.status_code == 200, resp.text
        lg.assert_awaited_once()

    # -- personal connector dispatch guard (rows 19/30) -------------------------

    def _chat_with_connector(self, app, *, agent, user_id, finder=None):
        personal_connector = SimpleNamespace(name="gmail-a", owner_user_id=USER_A)
        owned_lookup = AsyncMock(return_value=[personal_connector])
        lg = AsyncMock(
            return_value={"status": "completed", "output": {"answer": "ok"}, "confidence": 0.9, "performance": {}}
        )
        body = {"query": "cash runway", "company_id": COMPANY}
        if finder is None:
            body["agent_id"] = str(agent.id)
        patches = [
            patch("api.v1.chat.get_tenant_session", side_effect=_chat_session_factory(agent)),
            patch("api.v1.agents._personal_connectors_for_ids", owned_lookup),
            patch("api.v1.agents._assert_connectors_ready_for_activation", AsyncMock()),
            patch("api.v1.agents._resolve_connector_configs", AsyncMock(return_value=({}, ["gmail-a"]))),
            patch("api.v1.chat._record_cost_ledger", AsyncMock(return_value=True)),
            patch("core.langgraph.runner.run_agent", lg),
        ]
        if finder is not None:
            patches.append(patch("api.v1.chat._find_agent_for_domain", finder))
        with _client(app, role="cfo", domains=["finance"], user_id=user_id) as c:
            for p in patches:
                p.start()
            try:
                resp = c.post("/api/v1/chat/query", json=body)
            finally:
                for p in reversed(patches):
                    p.stop()
        return resp, lg, owned_lookup

    def test_owner_chat_with_personal_agent_passes_personal_connector_guard(self, app):
        agent = _agent(visibility="personal", owner=USER_A, connector_ids=["registry-gmail-a"])
        resp, lg, owned_lookup = self._chat_with_connector(app, agent=agent, user_id=USER_A)
        assert resp.status_code == 200, resp.text
        owned_lookup.assert_awaited()  # the real guard ran and allowed the owner's link
        lg.assert_awaited_once()

    def test_keyword_routed_owner_chat_passes_personal_connector_guard(self, app):
        agent = _agent(visibility="personal", owner=USER_A, connector_ids=["registry-gmail-a"])
        finder = AsyncMock(return_value=("Mine", str(agent.id), "ap_processor", []))
        with patch("api.v1.agents._resolve_agent_connector_ids_for_type", AsyncMock(return_value=["registry-gmail-a"])):
            resp, lg, owned_lookup = self._chat_with_connector(app, agent=agent, user_id=USER_A, finder=finder)
        assert resp.status_code == 200, resp.text
        assert owned_lookup.await_args.args[2] == ["registry-gmail-a", "registry-gmail-a"]
        lg.assert_awaited_once()

    def test_shared_agent_linking_a_personal_connector_is_refused(self, app):
        agent = _agent(connector_ids=["registry-gmail-a"])
        resp, lg, _ = self._chat_with_connector(app, agent=agent, user_id=USER_A)
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "connector_not_available_to_agent"
        lg.assert_not_called()

    def test_other_user_never_reaches_the_personal_agent_or_its_connector(self, app):
        agent = _agent(visibility="personal", owner=USER_A, connector_ids=["registry-gmail-a"])
        resp, lg, owned_lookup = self._chat_with_connector(app, agent=agent, user_id=USER_B)
        assert resp.status_code == 404
        owned_lookup.assert_not_called()
        lg.assert_not_called()

    @pytest.mark.asyncio
    async def test_chat_hitl_row_records_requester_and_pushes_owner_only(self):
        from api.v1.chat import _record_chat_hitl

        personal = _agent(visibility="personal", owner=USER_A)
        session = _QueueSession(personal)
        push = AsyncMock()
        with (
            _session_patch("api.v1.chat.get_tenant_session", session),
            patch("core.push.sender.notify_approval_created", push),
        ):
            ok = await _record_chat_hitl(
                tenant_id=TENANT,
                agent_id=str(personal.id),
                agent_type="ap_processor",
                agent_name="AP",
                domain="finance",
                query="pay",
                hitl_trigger="high_value",
                confidence=0.9,
                requested_by_user_id=USER_A,
            )
        assert ok is True
        (row,) = session.added
        assert row.requested_by_user_id == USER_A
        assert push.await_args.kwargs["agent_visibility"] == "personal"
        assert push.await_args.kwargs["agent_owner_user_id"] == str(USER_A)


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


class TestWorkflowOwnership:
    @pytest.mark.asyncio
    async def test_create_referencing_a_personal_agent_by_b_is_403(self):
        """Handler-level: the route also sits behind require_tenant_admin, so
        this proves the ownership check itself (defence in depth)."""
        from api.v1.workflows import create_workflow
        from core.schemas.api import WorkflowCreate

        personal = _agent(visibility="personal", owner=USER_A)
        session = _QueueSession([personal])
        body = WorkflowCreate(name="wf", definition={"steps": [{"id": "s1", "agent_id": str(personal.id)}]})
        with _session_patch("api.v1.workflows.get_tenant_session", session), pytest.raises(HTTPException) as exc:
            await create_workflow(body=body, request=_cfo_b(), tenant_id=TENANT)
        assert exc.value.status_code == 403
        assert session.added == []

    def test_create_referencing_nested_personal_agent_is_also_checked(self):
        from api.v1.workflows import _definition_agent_ids

        aid = uuid.uuid4()
        definition = {"steps": [{"id": "p", "type": "parallel", "steps": [{"id": "c", "agent_id": str(aid)}]}]}
        assert _definition_agent_ids(definition) == {aid}

    @pytest.mark.asyncio
    async def test_admin_create_referencing_a_personal_agent_is_allowed(self):
        from api.v1.workflows import create_workflow
        from core.schemas.api import WorkflowCreate

        personal = _agent(visibility="personal", owner=USER_A)
        session = _QueueSession([personal])
        body = WorkflowCreate(name="wf", definition={"steps": [{"id": "s1", "agent_id": str(personal.id)}]})
        with _session_patch("api.v1.workflows.get_tenant_session", session):
            resp = await create_workflow(body=body, request=_admin(), tenant_id=TENANT)
        assert resp["name"] == "wf"

    @pytest.mark.asyncio
    async def test_run_records_server_side_initiator_not_payload_user(self):
        from api.v1.workflows import run_workflow
        from core.schemas.api import WorkflowRunTrigger

        wf = SimpleNamespace(
            id=uuid.uuid4(), company_id=None, is_active=True, definition={"steps": [{"id": "s1"}]}
        )
        session = _QueueSession(wf)
        with (
            _session_patch("api.v1.workflows.get_tenant_session", session),
            patch("core.workflow_ab.pick_variant", AsyncMock(return_value=None)),
        ):
            await run_workflow(
                wf_id=wf.id,
                background_tasks=BackgroundTasks(),
                request=_cfo_a(),
                body=WorkflowRunTrigger(payload={"user_id": str(USER_B)}),
                tenant_id=TENANT,
            )
        (run,) = session.added
        assert run.context["initiated_by_user_id"] == str(USER_A)

    @pytest.mark.asyncio
    async def test_machine_run_records_no_initiator(self):
        from api.v1.workflows import run_workflow

        wf = SimpleNamespace(id=uuid.uuid4(), company_id=None, is_active=True, definition={"steps": [{"id": "s"}]})
        session = _QueueSession(wf)
        with (
            _session_patch("api.v1.workflows.get_tenant_session", session),
            patch("core.workflow_ab.pick_variant", AsyncMock(return_value=None)),
        ):
            await run_workflow(
                wf_id=wf.id,
                background_tasks=BackgroundTasks(),
                request=_request("", None, None, auth_mode="api_key"),
                tenant_id=TENANT,
            )
        (run,) = session.added
        assert "initiated_by_user_id" not in run.context

    @pytest.mark.parametrize(
        ("initiator", "expected"),
        [(str(USER_A), True), (str(USER_B), False), (None, False)],
    )
    @pytest.mark.asyncio
    async def test_personal_agent_step_requires_owner_initiated_run(self, initiator, expected):
        from workflows.step_types import _workflow_run_may_use_agent

        db_run = SimpleNamespace(context={"initiated_by_user_id": initiator} if initiator else {})
        session = _QueueSession(db_run)
        state = {"tenant_id": TENANT, "workflow_run_id": str(uuid.uuid4())}
        config = {"visibility": "personal", "owner_user_id": str(USER_A)}
        with _session_patch("core.database.get_tenant_session", session):
            assert await _workflow_run_may_use_agent(config, state) is expected

    @pytest.mark.asyncio
    async def test_sub_workflow_without_run_id_cannot_use_personal_agent(self):
        from workflows.step_types import _workflow_run_may_use_agent

        config = {"visibility": "personal", "owner_user_id": str(USER_A)}
        assert await _workflow_run_may_use_agent(config, {"tenant_id": TENANT}) is False
        assert await _workflow_run_may_use_agent({"visibility": "tenant"}, {"tenant_id": TENANT}) is True

    @pytest.mark.asyncio
    async def test_agent_step_on_someone_elses_personal_agent_fails_closed(self):
        from workflows import step_types

        config = {"id": str(uuid.uuid4()), "agent_type": "ap_processor", "visibility": "personal",
                  "owner_user_id": str(USER_A)}
        with (
            patch.object(step_types, "_load_workflow_agent_config", AsyncMock(return_value=config)),
            patch.object(step_types, "_workflow_run_may_use_agent", AsyncMock(return_value=False)),
            patch.object(step_types, "_llm_available_for_workflow") as llm,
        ):
            result = await step_types._execute_agent(
                {"id": "s1", "agent_id": config["id"]}, {"tenant_id": TENANT, "workflow_run_id": str(uuid.uuid4())}
            )
        assert result["status"] == "failed"
        assert "personal agent" in result["error"]["details"]["cause"]
        llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_sync_hitl_fallback_is_shared_only_and_stamps_initiator(self):
        from workflows.run_sync import sync_engine_state_to_workflow_run

        db_run = SimpleNamespace(
            status="running", steps_completed=0, steps_total=1, result=None, error=None, completed_at=None,
            context={"initiated_by_user_id": str(USER_A)},
        )
        fallback_agent_id = uuid.uuid4()
        session = _QueueSession(db_run, fallback_agent_id)
        step_row = SimpleNamespace(status="waiting_hitl", agent_id=None)
        push = AsyncMock()
        state = {
            "status": "waiting_hitl",
            "step_results": {"approve": {"status": "waiting_hitl", "output": {}}},
            "definition": {"steps": [{"id": "approve", "type": "human_in_loop"}]},
        }
        with (
            _session_patch("core.database.get_tenant_session", session),
            patch("api.v1.workflows._upsert_step_execution", AsyncMock(return_value=(step_row, True))),
            patch("workflows.run_sync.schedule_hitl_timeout", return_value=True),
            patch("core.push.sender.notify_approval_created", push),
            patch("workflows.run_sync.record_ab_outcome_if_terminal", AsyncMock()),
        ):
            await sync_engine_state_to_workflow_run(
                tenant_id=TEST_TENANT_ID, workflow_run_id=uuid.uuid4(), engine_run_id="eng", state=state
            )
        fallback_sql = _sql(session.statements[1])
        assert "agents.visibility = 'tenant'" in fallback_sql
        (hitl,) = session.added
        assert hitl.agent_id == fallback_agent_id
        assert hitl.requested_by_user_id == USER_A
        assert push.await_args.kwargs["agent_visibility"] == "tenant"

    @pytest.mark.asyncio
    async def test_run_sync_hitl_on_personal_step_agent_pushes_owner_only(self):
        from workflows.run_sync import sync_engine_state_to_workflow_run

        db_run = SimpleNamespace(
            status="running", steps_completed=0, steps_total=1, result=None, error=None, completed_at=None, context={}
        )
        personal = _agent(visibility="personal", owner=USER_A)
        session = _QueueSession(db_run, personal)
        step_row = SimpleNamespace(status="waiting_hitl", agent_id=personal.id)
        push = AsyncMock()
        state = {
            "status": "waiting_hitl",
            "step_results": {"approve": {"status": "waiting_hitl", "output": {}}},
            "definition": {"steps": [{"id": "approve", "type": "human_in_loop"}]},
        }
        with (
            _session_patch("core.database.get_tenant_session", session),
            patch("api.v1.workflows._upsert_step_execution", AsyncMock(return_value=(step_row, True))),
            patch("workflows.run_sync.schedule_hitl_timeout", return_value=True),
            patch("core.push.sender.notify_approval_created", push),
            patch("workflows.run_sync.record_ab_outcome_if_terminal", AsyncMock()),
        ):
            await sync_engine_state_to_workflow_run(
                tenant_id=TEST_TENANT_ID, workflow_run_id=uuid.uuid4(), engine_run_id="eng", state=state
            )
        (hitl,) = session.added
        assert hitl.requested_by_user_id is None
        assert push.await_args.kwargs["agent_visibility"] == "personal"
        assert push.await_args.kwargs["agent_owner_user_id"] == str(USER_A)

    def test_background_executor_fallback_is_shared_only(self):
        from api.v1.workflows import _execute_workflow_bg

        src = inspect.getsource(_execute_workflow_bg)
        assert ".where(Agent.tenant_id == tenant_id, shared_agents_only_clause(Agent))" in src
        assert "requested_by_user_id=workflow_run_initiator(db_run)" in src
        assert "**push_scope" in src


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestAuditOwnership:
    async def _audit_sql(self, request, role: str, *, enforce: bool = False) -> str:
        from api.v1 import audit

        session = _QueueSession(0, [])
        with _session_patch("api.v1.audit.get_tenant_session", session):
            if enforce:
                await audit.query_enforce_audit(request=request, tenant_id=TENANT, user_role=role)
            else:
                await audit.query_audit(request=request, tenant_id=TENANT, user_role=role)
        return _sql(session.statements[0])

    @pytest.mark.parametrize("enforce", [False, True])
    @pytest.mark.asyncio
    async def test_cfo_audit_hides_other_users_personal_rows(self, enforce):
        sql = await self._audit_sql(_cfo_b(), "cfo", enforce=enforce)
        assert "agents.visibility = 'tenant'" in sql
        assert f"agents.owner_user_id = '{USER_B}'" in sql
        assert "audit_log.agent_id IS NULL" in sql
        assert str(USER_A) not in sql

    @pytest.mark.parametrize("enforce", [False, True])
    @pytest.mark.asyncio
    async def test_auditor_sees_every_row(self, enforce):
        sql = await self._audit_sql(_request("auditor", uuid.uuid4(), ["finance"]), "auditor", enforce=enforce)
        assert "agents" not in sql

    @pytest.mark.asyncio
    async def test_machine_credential_claiming_auditor_role_is_still_filtered(self):
        sql = await self._audit_sql(_request("auditor", None, None, auth_mode="grantex"), "auditor")
        assert "agents.visibility = 'tenant'" in sql

    @pytest.mark.asyncio
    async def test_admin_sees_every_row(self):
        sql = await self._audit_sql(_admin(), "admin")
        assert "agents" not in sql


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------


class TestKpiOwnership:
    @pytest.mark.parametrize("company_id", [None, COMPANY])
    @pytest.mark.asyncio
    async def test_recent_escalations_exclude_personal_agents(self, company_id):
        from api.v1.kpis import _get_recent_escalations

        session = _QueueSession([])
        with _session_patch("api.v1.kpis.get_tenant_session", session):
            assert await _get_recent_escalations(TENANT, limit=5, company_id=company_id) == []
        sql = _sql(session.statements[0])
        assert "JOIN agents ON agents.id = hitl_queue.agent_id" in sql
        assert "agents.visibility = 'tenant'" in sql
        # hitl_queue has no company_id column; the company scope is the agent's.
        assert "hitl_queue.company_id" not in sql
        if company_id:
            assert f"agents.company_id = '{COMPANY}'" in sql

    @pytest.mark.asyncio
    async def test_recent_escalations_company_query_compiles_against_real_columns(self):
        from api.v1.kpis import _get_recent_escalations
        from core.models.hitl import HITLQueue

        assert "company_id" not in HITLQueue.__table__.columns
        session = _QueueSession([])
        with _session_patch("api.v1.kpis.get_tenant_session", session):
            await _get_recent_escalations(TENANT, company_id=COMPANY)
        referenced = {c.table.name + "." + c.name for c in session.statements[0].selected_columns}
        assert referenced <= {f"hitl_queue.{c}" for c in ("id", "title", "priority", "status", "created_at")}

    @pytest.mark.asyncio
    async def test_cmo_approval_timeout_risk_excludes_personal_agents(self):
        from api.v1.kpis import _load_cmo_approval_timeout_risk

        session = _QueueSession([])
        with _session_patch("api.v1.kpis.get_tenant_session", session):
            await _load_cmo_approval_timeout_risk(TENANT, COMPANY)
        assert "agents.visibility = 'tenant'" in _sql(session.statements[0])


# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------


class TestApprovalPush:
    async def _notify(self, **kwargs):
        from core.push import sender

        per_user = AsyncMock(return_value={"sent": 1, "failed": 0, "stale_removed": 0})
        with (
            patch.object(sender, "send_push_notification_for_user", per_user),
            patch.object(sender, "_subscribed_user_ids", AsyncMock(return_value=[str(USER_A), str(USER_B)])),
        ):
            await sender.notify_approval_created(TENANT, item_id=str(uuid.uuid4()), **kwargs)
        return [c.args[1] for c in per_user.await_args_list]

    @pytest.mark.asyncio
    async def test_personal_agent_item_is_pushed_to_owner_only(self):
        targets = await self._notify(agent_visibility="personal", agent_owner_user_id=str(USER_A))
        assert targets == [str(USER_A)]

    @pytest.mark.asyncio
    async def test_ownerless_personal_agent_item_is_pushed_to_nobody(self):
        assert await self._notify(agent_visibility="personal", agent_owner_user_id=None) == []

    @pytest.mark.asyncio
    async def test_shared_agent_item_keeps_fan_out(self):
        assert await self._notify(agent_visibility="tenant") == [str(USER_A), str(USER_B)]

    @pytest.mark.asyncio
    async def test_legacy_caller_is_scoped_by_looking_up_the_item_agent(self):
        from core.push import sender

        with patch.object(sender, "_approval_agent_scope", AsyncMock(return_value=("personal", str(USER_A)))):
            assert await self._notify() == [str(USER_A)]
        with patch.object(sender, "_approval_agent_scope", AsyncMock(return_value=None)):
            assert await self._notify() == []
