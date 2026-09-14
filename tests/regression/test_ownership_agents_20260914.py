# ruff: noqa: S106 — test files use fake tokens intentionally
"""Bug sheet 2026-09-14 rows 17/19/22/30/52 — per-user agent ownership on the agent routes.

Replays the tester's steps at TestClient level: a CFO or developer creating
agents without being a tenant admin, one CFO reaching another CFO's personal
agent by id, PATCH persisting a domain change, admins changing visibility,
personal connectors linked across owners, the SOP deploy route with no RBAC,
and the AI-model registry the non-admin create form needs.

The DB is a session double (``get_tenant_session`` patched), so SQL filters
are asserted on the compiled statement the route issued rather than on rows a
real database would drop.
"""

from __future__ import annotations

import inspect
import uuid
from contextlib import asynccontextmanager, contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from tests.company_scope import TEST_COMPANY_ID, TEST_TENANT_ID

TENANT = str(TEST_TENANT_ID)
COMPANY = str(TEST_COMPANY_ID)

CFO_A = uuid.uuid4()
CFO_B = uuid.uuid4()
DEV = uuid.uuid4()
ADMIN = uuid.uuid4()

DOMAIN_ROLE_SCOPES = [
    "agents:read",
    "agents:write",
    "workflows:read",
    "workflows:write",
    "approvals:read",
    "approvals:write",
    "audit:read",
    "connectors.read",
    "report_schedules.read",
    "report_schedules.write",
    "connectors.personal.write",
]
DEVELOPER_SCOPES = [
    "agents:read",
    "agents:write",
    "approvals:read",
    "approvals:write",
    "workflows:read",
    "connectors.read",
    "connectors.personal.write",
]
ANALYST_SCOPES = ["agents:read", "workflows:read", "approvals:read", "connectors.read", "report_schedules.read"]


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def _agent(domain: str = "finance", visibility: str = "tenant", owner: uuid.UUID | None = None, **overrides):
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TEST_TENANT_ID,
        "company_id": None,
        "name": f"{domain}-agent",
        "employee_name": f"{domain.upper()} Agent",
        "agent_type": "ap_processor",
        "domain": domain,
        "status": "shadow",
        "version": "1.0.0",
        "authorized_tools": [],
        "connector_ids": [],
        "system_prompt_text": "p",
        "system_prompt_ref": "",
        "parent_agent_id": None,
        "config": {},
        "prompt_amendments": [],
        "hitl_condition": "confidence < 0.88",
        "shadow_sample_count": 3,
        "shadow_scored_sample_count": 3,
        "shadow_min_samples": 10,
        "shadow_accuracy_current": None,
        "shadow_model_confidence_current": None,
        "shadow_human_confidence_current": None,
        "shadow_feedback_count": 0,
        "shadow_accuracy_floor": Decimal("0.80"),
        "shadow_comparison_agent_id": None,
        "description": None,
        "prompt_variables": {},
        "llm_model": "gemini-2.5-flash",
        "llm_provider": None,
        "llm_fallback": None,
        "llm_config": {},
        "confidence_floor": Decimal("0.88"),
        "max_retries": 3,
        "retry_backoff": "exponential",
        "output_schema": None,
        "cost_controls": {},
        "scaling": {},
        "tags": [],
        "ttl_hours": None,
        "expires_at": None,
        "created_at": None,
        "updated_at": None,
        "avatar_url": None,
        "designation": None,
        "specialization": None,
        "routing_filter": {},
        "is_builtin": False,
        "maturity": "beta",
        "cost_center_id": None,
        "reporting_to": None,
        "org_level": 0,
        "visibility": visibility,
        "owner_user_id": owner,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _connector(name: str, owner: uuid.UUID | None):
    return SimpleNamespace(id=uuid.uuid4(), name=name, owner_user_id=owner, status="active")


def _entity(stmt):
    try:
        return stmt.column_descriptions[0].get("entity")
    except (AttributeError, IndexError, KeyError, TypeError):
        return None


class _Session:
    """Session double: Agent SELECTs resolve to ``agent``, Connector SELECTs
    to ``connectors``; every statement is recorded for SQL assertions."""

    def __init__(self, agent=None, connectors=(), agent_ids=None):
        self.agent = agent
        self.connectors = list(connectors)
        self.agent_ids = agent_ids
        self.statements: list = []
        self.added: list = []

    async def execute(self, stmt, *_a, **_k):
        from core.models.agent import Agent
        from core.models.connector import Connector

        self.statements.append(stmt)
        entity = _entity(stmt)
        res = MagicMock()
        res.scalar.return_value = 0
        res.fetchone.return_value = None
        if entity is Connector:
            res.scalars.return_value.all.return_value = list(self.connectors)
            res.scalar_one_or_none.return_value = None
            return res
        if entity is Agent:
            res.scalar_one_or_none.return_value = self.agent
            res.scalars.return_value.all.return_value = [self.agent] if self.agent else []
            res.all.return_value = [(a,) for a in (self.agent_ids or [])]
            return res
        res.scalar_one_or_none.return_value = None
        res.scalars.return_value.all.return_value = []
        return res

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    async def commit(self):
        return None

    def compiled(self, entity_name: str) -> list[tuple[str, dict]]:
        out = []
        for stmt in self.statements:
            entity = _entity(stmt)
            if getattr(entity, "__name__", "") != entity_name:
                continue
            compiled = stmt.compile(dialect=postgresql.dialect())
            out.append((str(compiled), dict(compiled.params)))
        return out


def _factory(session):
    def factory(*_a, **_k):
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    return factory


@pytest.fixture(scope="module")
def app():
    from api.main import app as _app

    @asynccontextmanager
    async def _test_lifespan(app):
        yield

    _app.router.lifespan_context = _test_lifespan
    return _app


@contextmanager
def _client(app, *, role: str, domains: list[str] | None, scopes: list[str], user_id: uuid.UUID):
    claims = {
        "sub": f"{role}@x.io",
        "role": role,
        "agenticorg:tenant_id": TENANT,
        "agenticorg:domains": domains,
        "agenticorg:user_id": str(user_id),
        "grantex:scopes": scopes,
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
        # Authorization tests use synthetic users; session revocation has its own
        # coverage. With a live DB (CI integration job) the lookup would 401.
        patch("auth.grantex_middleware.check_user_session_state", AsyncMock(return_value=None)),
        patch("auth.grantex_middleware.extract_tenant_id", return_value=TENANT),
        patch("auth.grantex_middleware.extract_scopes", return_value=scopes),
        patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)),
        patch("auth.grantex_registration.register_agent", return_value=None),
    ):
        with TestClient(app, raise_server_exceptions=False) as c:
            c.headers["Authorization"] = "Bearer fake-test-token"
            yield c


def _cfo_a(app, domains=("finance",)):
    return _client(app, role="cfo", domains=list(domains), scopes=DOMAIN_ROLE_SCOPES, user_id=CFO_A)


def _developer(app):
    return _client(app, role="developer", domains=["ops"], scopes=DEVELOPER_SCOPES, user_id=DEV)


def _analyst(app):
    return _client(app, role="analyst", domains=["finance"], scopes=ANALYST_SCOPES, user_id=uuid.uuid4())


def _admin(app):
    return _client(app, role="admin", domains=None, scopes=["agenticorg:admin"], user_id=ADMIN)


def _patch_session(session):
    return patch("api.v1.agents.get_tenant_session", side_effect=_factory(session))


def _created(session):
    from core.models.agent import Agent

    return [row for row in session.added if isinstance(row, Agent)]


# ---------------------------------------------------------------------------
# Row 19/22 — POST /agents: who may create what
# ---------------------------------------------------------------------------


class TestCreateAgentOwnership:
    BODY = {"name": "Recon helper", "agent_type": "recon_agent", "domain": "finance", "connector_ids": []}

    def test_cfo_creates_personal_agent_owned_by_cfo(self, app):
        session = _Session()
        with _cfo_a(app) as c, _patch_session(session):
            resp = c.post("/api/v1/agents", json=self.BODY)
        assert resp.status_code == 201, resp.text
        assert resp.json()["visibility"] == "personal"
        assert resp.json()["owner_user_id"] == str(CFO_A)
        (row,) = _created(session)
        assert row.visibility == "personal"
        assert row.owner_user_id == CFO_A

    def test_cfo_audit_row_names_the_user_not_the_tenant(self, app):
        from core.models.audit import AuditLog

        session = _Session()
        with _cfo_a(app) as c, _patch_session(session):
            resp = c.post("/api/v1/agents", json=self.BODY)
        assert resp.status_code == 201, resp.text
        (audit,) = [row for row in session.added if isinstance(row, AuditLog)]
        assert audit.actor_id == str(CFO_A)

    def test_cfo_cannot_create_shared_agent(self, app):
        session = _Session()
        with _cfo_a(app) as c, _patch_session(session):
            resp = c.post("/api/v1/agents", json={**self.BODY, "visibility": "tenant"})
        assert resp.status_code == 403
        assert session.added == []

    def test_cfo_cannot_create_in_hr(self, app):
        session = _Session()
        with _cfo_a(app) as c, _patch_session(session):
            resp = c.post("/api/v1/agents", json={**self.BODY, "agent_type": "onboarding", "domain": "hr"})
        assert resp.status_code == 403
        assert resp.json()["detail"] == "You do not have access to the 'hr' domain."
        assert session.added == []

    def test_developer_creates_personal_agent_in_any_domain(self, app):
        session = _Session()
        with _developer(app) as c, _patch_session(session):
            resp = c.post("/api/v1/agents", json=self.BODY)
        assert resp.status_code == 201, resp.text
        (row,) = _created(session)
        assert (row.visibility, row.owner_user_id, row.domain) == ("personal", DEV, "finance")

    def test_analyst_cannot_create(self, app):
        session = _Session()
        with _analyst(app) as c, _patch_session(session):
            resp = c.post("/api/v1/agents", json=self.BODY)
        assert resp.status_code == 403
        assert session.added == []

    def test_admin_still_creates_shared_agent(self, app):
        session = _Session()
        with _admin(app) as c, _patch_session(session):
            resp = c.post("/api/v1/agents", json=self.BODY)
        assert resp.status_code == 201, resp.text
        (row,) = _created(session)
        assert (row.visibility, row.owner_user_id) == ("tenant", None)

    @pytest.mark.asyncio
    async def test_direct_call_without_caller_never_creates_personal_agent(self):
        """Direct Python calls (sop.py before this fix, tests) cannot mint an owner."""
        from api.v1.agents import create_agent
        from core.schemas.api import AgentCreate

        body = AgentCreate(name="x", agent_type="recon_agent", domain="finance", visibility="personal")
        with patch("api.v1.agents.get_tenant_session") as gts:
            with pytest.raises(HTTPException) as exc:
                await create_agent(body=body, tenant_id=TENANT)
        assert exc.value.status_code == 403
        gts.assert_not_called()


# ---------------------------------------------------------------------------
# Row 22 — by-id routes: another user's personal agent is invisible
# ---------------------------------------------------------------------------


class TestPersonalAgentIsolation:
    def test_cfo_a_cannot_get_patch_or_delete_cfo_b_personal_agent(self, app):
        b_agent = _agent("finance", "personal", CFO_B)
        with _cfo_a(app) as c, _patch_session(_Session(b_agent)):
            get = c.get(f"/api/v1/agents/{b_agent.id}")
            patch_resp = c.patch(f"/api/v1/agents/{b_agent.id}", json={"name": "mine now"})
            delete = c.delete(f"/api/v1/agents/{b_agent.id}")
        assert (get.status_code, patch_resp.status_code, delete.status_code) == (404, 404, 404)
        assert b_agent.name == "finance-agent"
        assert b_agent.status == "shadow"

    @pytest.mark.parametrize(
        "method,suffix",
        [
            ("post", "pause"),
            ("post", "retest"),
            ("post", "promote"),
            ("post", "rollback"),
            ("post", "retire"),
            ("get", "budget"),
            ("get", "prompt-history"),
            ("get", "amendments"),
            ("post", "feedback/analyze"),
        ],
    )
    def test_cfo_a_sub_routes_on_cfo_b_personal_agent_are_404(self, app, method, suffix):
        b_agent = _agent("finance", "personal", CFO_B)
        with (
            _cfo_a(app) as c,
            _patch_session(_Session(b_agent)),
            patch("core.feedback.analyzer.analyze_feedback", AsyncMock()) as analyze,
        ):
            resp = getattr(c, method)(f"/api/v1/agents/{b_agent.id}/{suffix}")
        assert resp.status_code == 404, (suffix, resp.text)
        analyze.assert_not_called()
        assert b_agent.shadow_sample_count == 3

    def test_cfo_run_on_cfo_b_personal_agent_is_404(self, app):
        b_agent = _agent("finance", "personal", CFO_B)
        with (
            _cfo_a(app) as c,
            _patch_session(_Session(b_agent)),
            patch("core.langgraph.runner.run_agent", AsyncMock()) as lg,
        ):
            resp = c.post(f"/api/v1/agents/{b_agent.id}/run", json={"inputs": {"query": "hello there"}})
        assert resp.status_code == 404
        lg.assert_not_called()

    def test_cfo_cannot_retest_a_shared_agent_it_can_see(self, app):
        """retest had no gate at all before this fix."""
        shared = _agent("finance")
        with _cfo_a(app) as c, _patch_session(_Session(shared)):
            resp = c.post(f"/api/v1/agents/{shared.id}/retest")
        assert resp.status_code == 403
        assert shared.shadow_sample_count == 3

    def test_owner_can_get_and_retest_own_personal_agent(self, app):
        mine = _agent("finance", "personal", CFO_A)
        with _cfo_a(app) as c, _patch_session(_Session(mine)):
            get = c.get(f"/api/v1/agents/{mine.id}")
            retest = c.post(f"/api/v1/agents/{mine.id}/retest")
        assert get.status_code == 200, get.text
        assert get.json()["visibility"] == "personal"
        assert get.json()["owner_user_id"] == str(CFO_A)
        assert retest.status_code == 200, retest.text
        assert mine.shadow_sample_count == 0

    def test_delegate_checks_the_parent_agent_too(self, app):
        parent = _agent("finance", "personal", CFO_B)
        child = _agent("finance", "personal", CFO_A, parent_agent_id=parent.id)

        class _TwoAgents(_Session):
            async def execute(self, stmt, *a, **k):
                res = await super().execute(stmt, *a, **k)
                res.scalar_one_or_none.return_value = child if len(self.statements) == 1 else parent
                return res

        with _cfo_a(app) as c, _patch_session(_TwoAgents()):
            resp = c.post(f"/api/v1/agents/{child.id}/delegate", json={})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Parent agent not found"


# ---------------------------------------------------------------------------
# Row 52 + 19/22 — PATCH / PUT: domain persisted, visibility admin-only
# ---------------------------------------------------------------------------


class TestPatchOwnership:
    def test_owner_patches_domain_within_own_domains_and_it_is_persisted(self, app):
        mine = _agent("finance", "personal", CFO_A)
        with _cfo_a(app, domains=("finance", "ops")) as c, _patch_session(_Session(mine)):
            resp = c.patch(f"/api/v1/agents/{mine.id}", json={"domain": "ops", "name": "Ops helper"})
        assert resp.status_code == 200, resp.text
        # Before the schema fix AgentUpdate dropped ``domain`` (extra="ignore").
        assert mine.domain == "ops"
        assert mine.name == "Ops helper"

    def test_owner_cannot_patch_domain_outside_own_domains(self, app):
        mine = _agent("finance", "personal", CFO_A)
        with _cfo_a(app) as c, _patch_session(_Session(mine)):
            resp = c.patch(f"/api/v1/agents/{mine.id}", json={"domain": "hr"})
        assert resp.status_code == 403
        assert mine.domain == "finance"

    def test_cfo_cannot_patch_shared_agent(self, app):
        shared = _agent("finance")
        with _cfo_a(app) as c, _patch_session(_Session(shared)):
            resp = c.patch(f"/api/v1/agents/{shared.id}", json={"name": "hijack"})
        assert resp.status_code == 403
        assert shared.name == "finance-agent"

    def test_owner_cannot_change_visibility(self, app):
        mine = _agent("finance", "personal", CFO_A)
        with _cfo_a(app) as c, _patch_session(_Session(mine)):
            resp = c.patch(f"/api/v1/agents/{mine.id}", json={"visibility": "tenant"})
        assert resp.status_code == 403
        assert (mine.visibility, mine.owner_user_id) == ("personal", CFO_A)

    def test_owner_cannot_publish_via_put_body(self, app):
        mine = _agent("finance", "personal", CFO_A)
        body = {"name": "n", "agent_type": "ap_processor", "domain": "finance", "visibility": "tenant"}
        with _cfo_a(app) as c, _patch_session(_Session(mine)):
            resp = c.put(f"/api/v1/agents/{mine.id}", json=body)
        assert resp.status_code == 403
        assert (mine.visibility, mine.owner_user_id) == ("personal", CFO_A)

    def test_admin_sets_visibility_tenant_and_owner_is_cleared(self, app):
        mine = _agent("finance", "personal", CFO_A)
        with _admin(app) as c, _patch_session(_Session(mine)):
            resp = c.patch(f"/api/v1/agents/{mine.id}", json={"visibility": "tenant"})
        assert resp.status_code == 200, resp.text
        assert (mine.visibility, mine.owner_user_id) == ("tenant", None)

    def test_admin_cannot_make_an_ownerless_agent_personal(self, app):
        shared = _agent("finance")
        with _admin(app) as c, _patch_session(_Session(shared)):
            resp = c.patch(f"/api/v1/agents/{shared.id}", json={"visibility": "personal"})
        assert resp.status_code == 422
        assert shared.visibility == "tenant"

    def test_developer_moves_own_personal_agent_to_any_domain(self, app):
        mine = _agent("finance", "personal", DEV)
        with _developer(app) as c, _patch_session(_Session(mine)):
            resp = c.patch(f"/api/v1/agents/{mine.id}", json={"domain": "hr"})
        assert resp.status_code == 200, resp.text
        assert mine.domain == "hr"


# ---------------------------------------------------------------------------
# Row 22 — list + org tree filters
# ---------------------------------------------------------------------------


class TestListFilters:
    @pytest.mark.parametrize("path", ["/api/v1/agents", "/api/v1/agents/org-tree"])
    def test_cfo_list_sql_is_own_personal_or_shared_in_domain(self, app, path):
        session = _Session()
        with _cfo_a(app) as c, _patch_session(session):
            resp = c.get(path)
        assert resp.status_code == 200, resp.text
        queries = session.compiled("Agent")
        assert queries, "route issued no agent query"
        for sql, params in queries:
            values = list(params.values())
            where = sql.split("WHERE", 1)[1]
            assert "agents.visibility" in where and "agents.owner_user_id" in where, sql
            assert CFO_A in values
            assert CFO_B not in values
            assert "personal" in values and "tenant" in values
            assert ["finance"] in values

    def test_admin_list_is_not_ownership_filtered(self, app):
        session = _Session()
        with _admin(app) as c, _patch_session(session):
            resp = c.get("/api/v1/agents")
        assert resp.status_code == 200, resp.text
        queries = session.compiled("Agent")
        assert queries
        for sql, _params in queries:
            assert "agents.owner_user_id" not in sql.split("WHERE", 1)[1]

    def test_clause_semantics_match_the_rule_table(self):
        from core.models.agent import Agent
        from core.ownership import Caller, agent_visibility_clause, can_view_agent

        cfo_a = Caller(user_id=CFO_A, role="cfo", domains=["finance"], is_admin=False, is_machine=False)
        mine = _agent("finance", "personal", CFO_A)
        theirs = _agent("finance", "personal", CFO_B)
        assert can_view_agent(mine, cfo_a) and not can_view_agent(theirs, cfo_a)
        compiled = agent_visibility_clause(Agent, cfo_a).compile(dialect=postgresql.dialect())
        assert CFO_A in compiled.params.values()


# ---------------------------------------------------------------------------
# Rows 17/19/22 — connector links + runtime guard
# ---------------------------------------------------------------------------


class TestConnectorLinks:
    def test_linking_another_users_personal_connector_is_403(self, app):
        mine = _agent("finance", "personal", CFO_A)
        b_conn = _connector("b_gmail", CFO_B)
        with _cfo_a(app) as c, _patch_session(_Session(mine, connectors=[b_conn])):
            resp = c.patch(f"/api/v1/agents/{mine.id}", json={"connector_ids": ["registry-b_gmail"]})
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "connector_not_available_to_agent"
        assert mine.connector_ids == []

    def test_admin_linking_personal_connector_to_shared_agent_is_403(self, app):
        shared = _agent("finance")
        a_conn = _connector("a_gmail", CFO_A)
        with _admin(app) as c, _patch_session(_Session(shared, connectors=[a_conn])):
            resp = c.patch(f"/api/v1/agents/{shared.id}", json={"connector_ids": ["a_gmail"]})
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "connector_not_available_to_agent"
        assert shared.connector_ids == []

    def test_create_with_another_users_personal_connector_is_403(self, app):
        session = _Session(connectors=[_connector("b_gmail", CFO_B)])
        with _cfo_a(app) as c, _patch_session(session):
            resp = c.post(
                "/api/v1/agents",
                json={"name": "x", "agent_type": "recon_agent", "domain": "finance", "connector_ids": ["b_gmail"]},
            )
        assert resp.status_code == 403
        assert _created(session) == []

    def test_owner_links_own_personal_connector(self, app):
        mine = _agent("finance", "personal", CFO_A)
        with _cfo_a(app) as c, _patch_session(_Session(mine, connectors=[_connector("a_gmail", CFO_A)])):
            resp = c.patch(f"/api/v1/agents/{mine.id}", json={"connector_ids": ["registry-a_gmail"]})
        assert resp.status_code == 200, resp.text
        assert mine.connector_ids == ["registry-a_gmail"]

    def test_admin_publishing_agent_with_personal_connector_is_403(self, app):
        mine = _agent("finance", "personal", CFO_A, connector_ids=["a_gmail"])
        with _admin(app) as c, _patch_session(_Session(mine, connectors=[_connector("a_gmail", CFO_A)])):
            resp = c.patch(f"/api/v1/agents/{mine.id}", json={"visibility": "tenant"})
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_dispatch_refuses_personal_connector_for_shared_agent(self):
        from api.v1.agents import _assert_connectors_ready_for_dispatch

        session = _Session(connectors=[_connector("a_gmail", CFO_A)])
        with patch("api.v1.agents._assert_connectors_ready_for_activation", AsyncMock()) as ready:
            with pytest.raises(HTTPException) as exc:
                await _assert_connectors_ready_for_dispatch(session, TEST_TENANT_ID, ["registry-a_gmail"])
        assert exc.value.status_code == 403
        assert exc.value.detail["error"] == "connector_not_available_to_agent"
        ready.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_refuses_other_owners_connector_and_allows_own(self):
        from api.v1.agents import _assert_connectors_ready_for_dispatch

        session = _Session(connectors=[_connector("a_gmail", CFO_A)])
        with patch("api.v1.agents._assert_connectors_ready_for_activation", AsyncMock()) as ready:
            with pytest.raises(HTTPException) as exc:
                await _assert_connectors_ready_for_dispatch(
                    session,
                    TEST_TENANT_ID,
                    [],
                    agent_visibility="personal",
                    agent_owner_user_id=CFO_B,
                    linked_connector_ids=["a_gmail"],
                )
            assert exc.value.status_code == 403
            await _assert_connectors_ready_for_dispatch(
                session, TEST_TENANT_ID, ["a_gmail"], agent_visibility="personal", agent_owner_user_id=CFO_A
            )
        ready.assert_awaited_once()

    def test_run_agent_passes_owner_and_full_connector_list_to_dispatch_guard(self):
        from api.v1.agents import run_agent

        src = inspect.getsource(run_agent)
        assert "agent_visibility=run_agent_visibility" in src
        assert "agent_owner_user_id=run_agent_owner_user_id" in src
        assert "linked_connector_ids=list(raw_connector_ids)" in src
        # Row 30: the HITL row records who triggered the run.
        assert "requested_by_user_id=effective_caller.user_id" in src


# ---------------------------------------------------------------------------
# Rows 19/22 — automatic selection never lands on a personal agent
# ---------------------------------------------------------------------------


class TestAutomaticSelectionIsSharedOnly:
    @pytest.mark.asyncio
    async def test_resolve_agent_connector_ids_for_type_excludes_personal_agents(self):
        from api.v1.agents import _resolve_agent_connector_ids_for_type

        session = _Session()
        with patch("core.database.get_tenant_session", side_effect=_factory(session)):
            await _resolve_agent_connector_ids_for_type(TENANT, "ap_processor")
        ((sql, params),) = session.compiled("Agent")
        assert "agents.visibility" in sql.split("WHERE", 1)[1]
        assert "tenant" in params.values()

    def test_sales_and_pack_installer_filter_shared_agents(self):
        from api.v1 import sales
        from core.agents.packs import installer

        assert "shared_agents_only_clause(Agent)" in inspect.getsource(sales._run_sales_agent_on_lead)
        src = inspect.getsource(installer)
        dup = src.split("existing = await session.execute(", 1)[1].split("_mark_duplicate_pack_agents_deleted", 1)[0]
        assert "shared_agents_only_clause(Agent)" in dup


# ---------------------------------------------------------------------------
# Sibling routes: SOP deploy, agent teams, AI registry
# ---------------------------------------------------------------------------


class TestSopDeploy:
    CONFIG = {"config": {"agent_name": "SOP Recon", "agent_type": "recon_agent", "domain": "finance"}}

    def test_analyst_cannot_deploy_sop_agent(self, app):
        with _analyst(app) as c, patch("api.v1.agents.create_agent", AsyncMock()) as create:
            resp = c.post("/api/v1/sop/deploy", json=self.CONFIG)
        assert resp.status_code == 403
        create.assert_not_called()

    def test_cfo_sop_deploy_is_personal_and_domain_gated(self, app):
        session = _Session()
        with _cfo_a(app) as c, _patch_session(session):
            ok = c.post("/api/v1/sop/deploy", json=self.CONFIG)
            hr = c.post("/api/v1/sop/deploy", json={"config": {**self.CONFIG["config"], "domain": "hr"}})
        assert ok.status_code == 201, ok.text
        assert ok.json()["visibility"] == "personal"
        assert hr.status_code == 403
        (row,) = _created(session)
        assert (row.visibility, row.owner_user_id) == ("personal", CFO_A)


class TestAgentTeamMembers:
    def test_member_agents_must_be_visible_to_the_caller(self, app):
        session = _Session(agent_ids=[])
        b_agent_id = uuid.uuid4()
        with _cfo_a(app) as c, patch("api.v1.agent_teams.get_tenant_session", side_effect=_factory(session)):
            resp = c.post(
                "/api/v1/agent-teams",
                json={"name": "t", "members": [{"agent_id": str(b_agent_id), "role": "primary"}]},
            )
        assert resp.status_code == 404
        ((sql, params),) = session.compiled("Agent")
        assert "agents.owner_user_id" in sql.split("WHERE", 1)[1]
        assert CFO_A in params.values()
        assert session.added == []


class TestAiModelRegistry:
    def test_cfo_reads_registry_but_not_tenant_ai_settings(self, app):
        with _cfo_a(app) as c:
            registry = c.get("/api/v1/tenant-ai-settings/registry")
            settings_get = c.get("/api/v1/tenant-ai-settings")
            settings_put = c.put("/api/v1/tenant-ai-settings", json={"chunk_size": 512})
        assert registry.status_code == 200, registry.text
        assert set(registry.json()) == {"llm", "embedding"}
        assert settings_get.status_code == 403
        assert settings_put.status_code == 403

    def test_analyst_cannot_read_registry(self, app):
        with _analyst(app) as c:
            resp = c.get("/api/v1/tenant-ai-settings/registry")
        assert resp.status_code == 403

    def test_registry_carries_no_credential_material(self, app):
        with _cfo_a(app) as c:
            body = c.get("/api/v1/tenant-ai-settings/registry").json()
        allowed = {"model", "context_window", "max_output_tokens", "supports_tools", "supports_vision", "notes"}
        allowed_embedding = {"model", "dimensions", "max_input_tokens", "notes"}
        for entries in body["llm"].values():
            for entry in entries:
                assert set(entry) <= allowed
        for entries in body["embedding"].values():
            for entry in entries:
                assert set(entry) <= allowed_embedding


def test_agent_teams_family_requires_agent_scopes() -> None:
    """The agent_teams scope family was unmapped: any authenticated user
    (auditor, merchant, analyst) could create routing teams."""
    from api.route_enforcement import SCOPE_FAMILIES, required_scopes_for

    assert SCOPE_FAMILIES["agent_teams"] == ("agents:read", "agents:write")
    assert required_scopes_for("agent_teams.routing_control.sensitive.write", "POST") == ("agents:write",)
    assert required_scopes_for("agent_teams.sensitive.read", "GET") == ("agents:read",)

