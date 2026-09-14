# ruff: noqa: S106 — test files use fake tokens intentionally
"""Bug sheet 2026-09-14 — agents / chat items #24 #28 #32 #44 #45 #46 #50 #52 #53.

Each class replays the tester's steps at TestClient level where the bug is a
route behaviour (a domain-limited CFO session reaching HR agents through chat
and the agent sub-routes, a CFO deploying an HR agent through /agents/generate,
an analyst executing chat without agents:write) and at handler level where the
bug is a persistence detail (HITL row shape, cost ledger, PUT/PATCH field
handling, claim parsing).

Runtime reproduction on the local stack before the fix (cfo@agenticorg.local,
agent dc6991ee… domain=hr in company Gupta Traders): ``GET /agents/{hr}`` was
404 but ``POST /chat/query`` with that agent_id was 200 (answered as the HR
agent), a bogus agent_id silently fell back to keyword routing ("CHRO Agent
(Priya)", domain hr), and budget / amendments / feedback / prompt-history /
explanation / feedback/analyze on the HR agent all returned 200.
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
from sqlalchemy.exc import SQLAlchemyError

from tests.company_scope import TEST_COMPANY_ID, TEST_TENANT_ID, owned_company_validator

TENANT = str(TEST_TENANT_ID)
COMPANY = str(TEST_COMPANY_ID)

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
]
ANALYST_SCOPES = ["agents:read", "workflows:read", "approvals:read", "connectors.read", "report_schedules.read"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent(domain: str = "hr", **overrides) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TEST_TENANT_ID,
        "company_id": TEST_COMPANY_ID,
        "name": f"{domain}-agent",
        "employee_name": f"{domain.upper()} Agent",
        "agent_type": "onboarding" if domain == "hr" else "ap_processor",
        "domain": domain,
        "status": "shadow",
        "authorized_tools": [],
        "connector_ids": [],
        "system_prompt_text": "You are a domain agent.",
        "parent_agent_id": None,
        "config": {},
        "prompt_amendments": [],
        "hitl_condition": "amount > 500000",
        "shadow_accuracy_current": None,
        "shadow_accuracy_floor": Decimal("0.80"),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


_SAME = object()


class _FakeSession:
    """Async session double: the first SELECT resolves to ``agent``; later
    SELECTs resolve to ``later`` (default: ``agent`` again)."""

    def __init__(self, agent=None, later=_SAME):
        self.agent = agent
        self.later = agent if later is _SAME else later
        self.calls = 0
        self.added: list = []

    async def execute(self, *_a, **_k):
        value = self.agent if self.calls == 0 else self.later
        self.calls += 1
        res = MagicMock()
        res.scalar_one_or_none.return_value = value
        res.scalar_one.return_value = value
        res.scalars.return_value.all.return_value = []
        res.scalar.return_value = 0
        res.fetchone.return_value = None
        return res

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None

    async def commit(self):
        return None


def _session_factory(session):
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
def _client(app, *, role: str, domains: list[str] | None, scopes: list[str], sub: str = "user@x.io"):
    """Authenticated TestClient whose session carries ``agenticorg:domains``."""
    claims = {
        "sub": sub,
        "role": role,
        "agenticorg:tenant_id": TENANT,
        "agenticorg:domains": domains,
        "agenticorg:user_id": str(uuid.uuid4()),
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
        patch("api.v1.agents._require_company_for_tenant", side_effect=owned_company_validator()),
    ):
        with TestClient(app, raise_server_exceptions=False) as c:
            c.headers["Authorization"] = "Bearer fake-test-token"
            yield c


def _cfo(app):
    return _client(app, role="cfo", domains=["finance"], scopes=DOMAIN_ROLE_SCOPES)


def _admin(app):
    return _client(app, role="admin", domains=None, scopes=["agenticorg:admin"])


# ---------------------------------------------------------------------------
# #53 — chat authorization
# ---------------------------------------------------------------------------


class TestChatDomainAuthorization:
    def test_cfo_chat_with_hr_agent_id_is_404(self, app):
        """Tester step: cfo picks an HR agent id in the header bar and sends a
        query. Before: 200, answered as the HR agent. Expected: same 404 that
        ``GET /agents/{id}`` returns for that session."""
        hr = _agent("hr")
        with (
            _cfo(app) as c,
            patch("api.v1.chat.get_tenant_session", side_effect=_session_factory(_FakeSession(hr))),
            patch("core.langgraph.runner.run_agent", AsyncMock()) as lg,
        ):
            resp = c.post(
                "/api/v1/chat/query", json={"query": "hello", "company_id": COMPANY, "agent_id": str(hr.id)}
            )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Agent not found"
        lg.assert_not_called()

    def test_unknown_agent_id_is_404_not_keyword_fallback(self, app):
        """Before: an agent_id that matched nothing silently fell back to
        keyword routing (cfo got 'CHRO Agent (Priya)' / domain hr)."""
        with (
            _cfo(app) as c,
            patch("api.v1.chat.get_tenant_session", side_effect=_session_factory(_FakeSession(None))),
            patch("api.v1.chat._find_agent_for_domain", AsyncMock()) as finder,
        ):
            resp = c.post(
                "/api/v1/chat/query",
                json={"query": "what is the employee attrition", "company_id": COMPANY, "agent_id": str(uuid.uuid4())},
            )
        assert resp.status_code == 404
        finder.assert_not_called()

    def test_non_uuid_agent_id_is_404(self, app):
        with (
            _cfo(app) as c,
            patch("api.v1.chat._find_agent_for_domain", AsyncMock()) as finder,
        ):
            resp = c.post(
                "/api/v1/chat/query", json={"query": "hello", "company_id": COMPANY, "agent_id": "not-a-uuid"}
            )
        assert resp.status_code == 404
        finder.assert_not_called()

    def test_keyword_routing_cannot_hand_cfo_an_hr_agent(self, app):
        """No agent_id: the query classifies as hr and the router finds an HR
        agent. A finance-only session must be refused, not served."""
        finder = AsyncMock(return_value=("Priya", str(uuid.uuid4()), "onboarding", []))
        with (
            _cfo(app) as c,
            patch("api.v1.chat._find_agent_for_domain", finder),
            patch("core.langgraph.runner.run_agent", AsyncMock()) as lg,
        ):
            resp = c.post(
                "/api/v1/chat/query", json={"query": "what is the employee attrition", "company_id": COMPANY}
            )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "You do not have access to the 'hr' domain."
        lg.assert_not_called()
        # Refused before the lookup: the response must not depend on whether
        # an HR agent exists in the company.
        finder.assert_not_called()

    def test_keyword_routing_to_hr_is_403_even_when_no_hr_agent_exists(self, app):
        finder = AsyncMock(return_value=("CHRO Agent (Priya)", None, None, []))
        with _cfo(app) as c, patch("api.v1.chat._find_agent_for_domain", finder):
            resp = c.post(
                "/api/v1/chat/query", json={"query": "what is the employee attrition", "company_id": COMPANY}
            )
        assert resp.status_code == 403
        finder.assert_not_called()

    def test_unclassified_query_with_no_agent_stays_honest_no_answer(self, app):
        """No keyword hit ("general") and nothing picked: there is nothing to
        protect, so the honest no-answer response is unchanged."""
        finder = AsyncMock(return_value=("General Assistant", None, None, []))
        with (
            _cfo(app) as c,
            patch("api.v1.chat._find_agent_for_domain", finder),
            patch("api.v1.agents._resolve_agent_connector_ids_for_type", AsyncMock(return_value=[])),
        ):
            resp = c.post("/api/v1/chat/query", json={"query": "hello there", "company_id": COMPANY})
        assert resp.status_code == 200
        assert resp.json()["confidence"] == 0.0

    def test_unclassified_query_cannot_pick_out_of_domain_general_agent(self, app):
        finder = AsyncMock(return_value=("Gen", str(uuid.uuid4()), "custom", []))
        with (
            _cfo(app) as c,
            patch("api.v1.chat._find_agent_for_domain", finder),
            patch("core.langgraph.runner.run_agent", AsyncMock()) as lg,
        ):
            resp = c.post("/api/v1/chat/query", json={"query": "hello there", "company_id": COMPANY})
        assert resp.status_code == 403
        lg.assert_not_called()

    def test_admin_session_still_reaches_any_domain(self, app):
        hr = _agent("hr")
        lg = AsyncMock(
            return_value={
                "status": "completed",
                "output": {"answer": "done"},
                "confidence": 0.9,
                "performance": {"llm_tokens_used": 12, "llm_cost_usd": 0.001},
            }
        )
        with (
            _admin(app) as c,
            patch("api.v1.chat.get_tenant_session", side_effect=_session_factory(_FakeSession(hr))),
            patch("api.v1.agents._resolve_agent_connector_ids_for_type", AsyncMock(return_value=[])),
            patch("api.v1.chat._record_cost_ledger", AsyncMock(return_value=True)),
            patch("core.langgraph.runner.run_agent", lg),
        ):
            resp = c.post(
                "/api/v1/chat/query", json={"query": "hello", "company_id": COMPANY, "agent_id": str(hr.id)}
            )
        assert resp.status_code == 200
        assert resp.json()["domain"] == "hr"
        lg.assert_awaited_once()


class TestChatScopeFamily:
    def test_chat_family_maps_to_agents_scopes(self):
        from api.route_enforcement import SCOPE_FAMILIES, required_scopes_for

        assert SCOPE_FAMILIES["chat"] == ("agents:read", "agents:write")
        assert required_scopes_for("chat.agent_execution.external_tool_sensitive.write", "POST") == (
            "agents:write",
        )
        assert required_scopes_for("chat.history.sensitive.read", "GET") == ("agents:read",)

    def test_chat_is_no_longer_an_unmapped_family(self, app):
        from api.route_enforcement import unmapped_scope_families

        def walk(routes):
            for r in routes:
                if hasattr(r, "effective_candidates"):
                    cands = r.effective_candidates
                    yield from walk(cands() if callable(cands) else cands)
                else:
                    yield r

        declared = [
            getattr(getattr(r, "endpoint", None), "__enterprise_route_metadata__", {}).get("scope")
            for r in walk(app.routes)
        ]
        declared = [s for s in declared if s]
        assert "chat" not in unmapped_scope_families(declared)

    def test_analyst_without_agents_write_cannot_execute_chat(self, app):
        with (
            _client(app, role="analyst", domains=["finance"], scopes=ANALYST_SCOPES) as c,
            patch("api.v1.chat._find_agent_for_domain", AsyncMock()) as finder,
        ):
            resp = c.post("/api/v1/chat/query", json={"query": "cash runway", "company_id": COMPANY})
        assert resp.status_code == 403
        assert "agents:write" in resp.json()["detail"]
        finder.assert_not_called()

    def test_domain_role_with_agents_write_can_execute_chat(self, app):
        finder = AsyncMock(return_value=("CFO Agent (test)", None, None, []))
        with (
            _cfo(app) as c,
            patch("api.v1.chat._find_agent_for_domain", finder),
            patch("api.v1.agents._resolve_agent_connector_ids_for_type", AsyncMock(return_value=[])),
        ):
            resp = c.post("/api/v1/chat/query", json={"query": "cash runway", "company_id": COMPANY})
        assert resp.status_code == 200


class TestAgentSubRoutesHonourDomainRbac:
    """Sibling sweep: every non-admin route that loads an agent by id."""

    GET_ROUTES = ("budget", "amendments", "feedback", "prompt-history", "explanation/latest")

    @pytest.mark.parametrize("suffix", GET_ROUTES)
    def test_cfo_get_on_hr_agent_is_404(self, app, suffix):
        hr = _agent("hr")
        with (
            _cfo(app) as c,
            patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(_FakeSession(hr))),
            patch("core.feedback.collector.list_feedback", AsyncMock(return_value=[])),
        ):
            resp = c.get(f"/api/v1/agents/{hr.id}/{suffix}")
        assert resp.status_code == 404, suffix

    @pytest.mark.parametrize(
        "suffix,body",
        [
            ("feedback", {"run_id": "run-1", "feedback_type": "thumbs_down"}),
            ("feedback/analyze", None),
            ("run", {"inputs": {"query": "hello there"}}),
            ("delegate", {}),
        ],
    )
    def test_cfo_post_on_hr_agent_is_404(self, app, suffix, body):
        hr = _agent("hr")
        with (
            _cfo(app) as c,
            patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(_FakeSession(hr))),
            patch("core.feedback.collector.submit_feedback", AsyncMock()) as submit,
            patch("core.feedback.analyzer.analyze_feedback", AsyncMock()) as analyze,
            patch("core.langgraph.runner.run_agent", AsyncMock()) as lg,
        ):
            resp = c.post(f"/api/v1/agents/{hr.id}/{suffix}", json=body)
        assert resp.status_code == 404, suffix
        submit.assert_not_called()
        analyze.assert_not_called()
        lg.assert_not_called()

    @pytest.mark.parametrize("suffix", GET_ROUTES)
    def test_cfo_get_on_finance_agent_still_works(self, app, suffix):
        fin = _agent("finance", cost_controls={}, shadow_accuracy_current=None)
        # explanation/latest reads a task-result row after the agent; "no run
        # yet" keeps the double honest instead of handing back the agent.
        session = _FakeSession(fin, later=None) if suffix == "explanation/latest" else _FakeSession(fin)
        with (
            _cfo(app) as c,
            patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(session)),
            patch("core.feedback.collector.list_feedback", AsyncMock(return_value=[])),
        ):
            resp = c.get(f"/api/v1/agents/{fin.id}/{suffix}")
        assert resp.status_code == 200, (suffix, resp.text)

    def test_every_agent_by_id_route_declares_user_domains(self):
        from api.v1 import agents as agents_mod

        for name in (
            "run_agent",
            "delegate_to_agent",
            "get_agent_budget",
            "submit_agent_feedback",
            "list_agent_feedback",
            "get_latest_explanation",
            "analyze_agent_feedback",
            "list_agent_amendments",
            "get_prompt_history",
            "generate_agent",
            "create_agent",
        ):
            fn = getattr(agents_mod, name)
            assert "user_domains" in inspect.signature(fn).parameters, name
            if name not in ("generate_agent", "create_agent"):
                # bug sheet 2026-09-14 rows 19/22: _enforce_domain_access was replaced by the ownership-aware check.
                assert "require_agent_visible(" in inspect.getsource(fn), name


# ---------------------------------------------------------------------------
# #32 / #50 — generate / create respect the caller's domains
# ---------------------------------------------------------------------------


def _suggestion(domain: str, tools: list[str] | None = None) -> dict:
    return {
        "employee_name": "Generated",
        "agent_type": "onboarding",
        "domain": domain,
        "system_prompt": "You are generated.",
        "suggested_tools": tools or [],
        "confidence_floor": 0.88,
    }


class TestGenerateAgentDomainGate:
    def test_cfo_cannot_deploy_llm_chosen_hr_agent(self, app):
        gen = AsyncMock(return_value={"suggestions": [_suggestion("hr")]})
        session = _FakeSession(None)
        with (
            _cfo(app) as c,
            patch("core.agent_generator.generate_agent_config", gen),
            patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(session)),
        ):
            resp = c.post("/api/v1/agents/generate", json={"description": "onboard every new employee", "deploy": True})
        assert resp.status_code == 403
        assert resp.json()["detail"] == "You do not have access to the 'hr' domain."
        assert session.added == []

    def test_unconfigured_llm_is_structured_503_not_opaque_500(self, app):
        """Local docker replay: with no LLM credentials the generate route
        escaped LLMProviderConfigurationError as an E1001 500."""
        from core.llm.router import LLMProviderConfigurationError

        gen = AsyncMock(side_effect=LLMProviderConfigurationError("Gemini provider is not configured"))
        session = _FakeSession(None)
        with (
            _cfo(app) as c,
            patch("core.agent_generator.generate_agent_config", gen),
            patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(session)),
        ):
            resp = c.post("/api/v1/agents/generate", json={"description": "onboard every new employee", "deploy": True})
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "llm_provider_not_configured"
        assert session.added == []

    def test_provider_failure_is_structured_502_not_opaque_500(self, app):
        """Integration replay: with a key present but rejected by the provider
        (google.genai ClientError 400), generate escaped as an E1001 500."""
        gen = AsyncMock(side_effect=RuntimeError("400 INVALID_ARGUMENT. API key not valid"))
        session = _FakeSession(None)
        with (
            _cfo(app) as c,
            patch("core.agent_generator.generate_agent_config", gen),
            patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(session)),
        ):
            resp = c.post("/api/v1/agents/generate", json={"description": "onboard every new employee", "deploy": True})
        assert resp.status_code == 502
        body = resp.json()["detail"]
        assert body["error"] == "llm_generation_failed"
        assert "API key" not in body["message"]
        assert session.added == []

    def test_cfo_can_deploy_finance_agent_and_it_gets_no_static_tools(self, app):
        """#46 sibling: a generated agent has no connector, so it gets no
        tools — not the generator's statically-filled suggestion either."""
        gen = AsyncMock(return_value={"suggestions": [_suggestion("finance", ["fetch_bank_statement"])]})
        session = _FakeSession(None)
        with (
            _cfo(app) as c,
            patch("core.agent_generator.generate_agent_config", gen),
            patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(session)),
        ):
            resp = c.post(
                "/api/v1/agents/generate", json={"description": "reconcile the bank statements", "deploy": True}
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["deployed"]["domain"] == "finance"
        from core.models.agent import Agent

        created = [row for row in session.added if isinstance(row, Agent)]
        assert len(created) == 1
        assert created[0].authorized_tools == []
        # The suggestion is still visible in the preview.
        assert resp.json()["suggestions"][0]["suggested_tools"] == ["fetch_bank_statement"]

    def test_preview_without_deploy_is_not_domain_gated(self, app):
        gen = AsyncMock(return_value={"suggestions": [_suggestion("hr")]})
        with _cfo(app) as c, patch("core.agent_generator.generate_agent_config", gen):
            resp = c.post("/api/v1/agents/generate", json={"description": "onboard every new employee"})
        assert resp.status_code == 200
        assert resp.json()["deployed"] is None


class TestCreateAgentDomainGate:
    @pytest.mark.asyncio
    async def test_domain_limited_caller_cannot_create_outside_domain(self):
        from api.v1.agents import create_agent
        from core.schemas.api import AgentCreate

        body = AgentCreate(name="x", agent_type="onboarding", domain="hr")
        with patch("api.v1.agents.get_tenant_session") as gts:
            with pytest.raises(HTTPException) as exc:
                await create_agent(body, TENANT, user_domains=["finance"])
        assert exc.value.status_code == 403
        gts.assert_not_called()


# ---------------------------------------------------------------------------
# #50 — PUT must not reset hitl_condition; #52 / #45 — PATCH domain + lock
# ---------------------------------------------------------------------------


class TestReplaceAgentHitlPolicy:
    async def _put(self, body):
        from api.v1.agents import replace_agent

        agent = MagicMock()
        agent.domain = "finance"
        agent.status = "shadow"
        agent.system_prompt_text = "old"
        agent.hitl_condition = "amount > 500000"
        agent.employee_name = "E"
        session = _FakeSession(agent)
        with patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(session)):
            await replace_agent(agent_id=uuid.uuid4(), body=body, tenant_id=TENANT)
        return agent

    @pytest.mark.asyncio
    async def test_put_without_hitl_policy_keeps_condition(self):
        from core.schemas.api import AgentCreate

        agent = await self._put(AgentCreate(name="n", agent_type="ap_processor", domain="finance"))
        assert agent.hitl_condition == "amount > 500000"

    @pytest.mark.asyncio
    async def test_put_with_hitl_policy_replaces_condition(self):
        from core.schemas.api import AgentCreate

        body = AgentCreate(
            name="n", agent_type="ap_processor", domain="finance", hitl_policy={"condition": "confidence < 0.7"}
        )
        agent = await self._put(body)
        assert agent.hitl_condition == "confidence < 0.7"


class TestUpdateAgentDomainAndPromptLock:
    def _agent(self, status: str):
        agent = MagicMock()
        agent.domain = "finance"
        agent.status = status
        agent.system_prompt_text = "p"
        agent.prompt_amendments = []
        return agent

    @pytest.mark.asyncio
    async def test_patch_domain_is_assigned(self):
        """Route-level replay: a domain in the validated update payload must
        be written, not just validated (the pre-fix code validated then
        dropped it)."""
        from api.v1.agents import update_agent

        agent = self._agent("shadow")
        body = MagicMock()
        body.model_dump.return_value = {"domain": "hr"}
        with patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(_FakeSession(agent))):
            result = await update_agent(agent_id=uuid.uuid4(), body=body, tenant_id=TENANT, user_domains=None, user={})
        assert result["updated"] is True
        assert agent.domain == "hr"

    @pytest.mark.asyncio
    async def test_patch_domain_outside_caller_domains_is_403_and_not_written(self):
        from api.v1.agents import update_agent

        agent = self._agent("shadow")
        body = MagicMock()
        body.model_dump.return_value = {"domain": "hr"}
        with patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(_FakeSession(agent))):
            with pytest.raises(HTTPException) as exc:
                await update_agent(
                    agent_id=uuid.uuid4(), body=body, tenant_id=TENANT, user_domains=["finance"], user={}
                )
        assert exc.value.status_code == 403
        assert agent.domain == "finance"

    @pytest.mark.asyncio
    async def test_prompt_amendments_are_locked_on_active_agents(self):
        from api.v1.agents import update_agent
        from core.schemas.api import AgentUpdate

        agent = self._agent("active")
        with patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(_FakeSession(agent))):
            with pytest.raises(HTTPException) as exc:
                await update_agent(
                    agent_id=uuid.uuid4(),
                    body=AgentUpdate(prompt_amendments=["Always cite the ledger"]),
                    tenant_id=TENANT,
                    user_domains=None,
                    user={},
                )
        assert exc.value.status_code == 409
        assert agent.prompt_amendments == []

    @pytest.mark.asyncio
    async def test_prompt_amendments_still_apply_on_shadow_agents(self):
        from api.v1.agents import update_agent
        from core.schemas.api import AgentUpdate

        agent = self._agent("shadow")
        with patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(_FakeSession(agent))):
            await update_agent(
                agent_id=uuid.uuid4(),
                body=AgentUpdate(prompt_amendments=["Always cite the ledger"]),
                tenant_id=TENANT,
                user_domains=None,
                user={},
            )
        assert agent.prompt_amendments == ["Always cite the ledger"]


# ---------------------------------------------------------------------------
# #44 — HITL rows carry the output once, in context
# ---------------------------------------------------------------------------


class TestHitlRowShape:
    @pytest.mark.asyncio
    async def test_chat_hitl_row_does_not_duplicate_context(self):
        from api.v1.chat import _record_chat_hitl

        session = _FakeSession(None)
        with (
            patch("api.v1.chat.get_tenant_session", side_effect=_session_factory(session)),
            patch("core.push.sender.notify_approval_created", AsyncMock()),
        ):
            ok = await _record_chat_hitl(
                tenant_id=TENANT,
                agent_id=str(uuid.uuid4()),
                agent_type="tax_compliance",
                agent_name="TDS",
                domain="finance",
                query="pay 10 lakh under 194J",
                hitl_trigger="high_value",
                confidence=0.9,
                hitl_context={"output": {"tds_amount": 100000}, "tool_calls": []},
            )
        assert ok is True
        (row,) = session.added
        assert row.decision_options == {"options": ["approve", "reject", "override"]}
        assert row.context["output"] == {"tds_amount": 100000}

    def test_run_agent_hitl_row_keeps_output_in_context_only(self):
        from api.v1.agents import run_agent

        src = inspect.getsource(run_agent)
        assert '"context": task_output' not in src
        assert '"output": task_output' in src

    @pytest.mark.asyncio
    async def test_shadow_learning_reads_output_from_context(self):
        from core.feedback.shadow_learning import capture_hitl_feedback

        item = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            workflow_run_id=None,
            trigger_type="policy_condition",
            context={"agent_status": "shadow", "run_id": "r", "output": {"invoice_total": 100}},
            decision_options={"options": ["approve", "reject", "override"]},
        )
        captured = await self._capture(capture_hitl_feedback, item)
        assert captured.original_output == {"invoice_total": 100}

    @pytest.mark.asyncio
    async def test_shadow_learning_falls_back_to_legacy_decision_options(self):
        from core.feedback.shadow_learning import capture_hitl_feedback

        item = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            workflow_run_id=None,
            trigger_type="policy_condition",
            context={"agent_status": "shadow", "run_id": "r"},
            decision_options={"options": ["approve"], "context": {"legacy": True}},
        )
        captured = await self._capture(capture_hitl_feedback, item)
        assert captured.original_output == {"legacy": True}

    async def _capture(self, capture_hitl_feedback, item):
        agent = SimpleNamespace(
            status="shadow",
            confidence_floor=Decimal("0.880"),
            hitl_condition="confidence < 0.88",
            shadow_min_samples=10,
            shadow_accuracy_floor=Decimal("0.800"),
            shadow_sample_count=4,
            shadow_scored_sample_count=4,
            shadow_accuracy_current=Decimal("0.700"),
            shadow_model_confidence_current=Decimal("0.700"),
            shadow_feedback_count=0,
            shadow_human_confidence_current=None,
        )
        first = MagicMock()
        first.scalar_one_or_none.return_value = None
        second = MagicMock()
        second.scalar_one.return_value = agent
        session = MagicMock()
        session.execute = AsyncMock(side_effect=[first, second])
        session.flush = AsyncMock()
        rows: list = []

        def add(row):
            row.id = uuid.uuid4()
            rows.append(row)

        session.add.side_effect = add
        await capture_hitl_feedback(
            session,
            item=item,
            decision="approve",
            notes="ok",
            actor_id="reviewer",
            actor_role="admin",
            actor_name="Reviewer",
            policy_action="advance",
            policy_state=None,
            delegated_from=None,
        )
        return rows[0]


# ---------------------------------------------------------------------------
# #28 — chat turns reach the cost ledger
# ---------------------------------------------------------------------------


class TestCostLedger:
    @pytest.mark.asyncio
    async def test_zero_usage_writes_nothing_by_default(self):
        from api.v1.agents import _record_cost_ledger

        with patch("api.v1.agents.get_tenant_session") as gts:
            assert await _record_cost_ledger(TEST_TENANT_ID, uuid.uuid4(), {}) is True
        gts.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_usage_chat_turn_still_counts_as_a_task(self):
        from api.v1.agents import _record_cost_ledger
        from core.models.agent import AgentCostLedger

        session = _FakeSession(None)
        aid = uuid.uuid4()
        with patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(session)):
            ok = await _record_cost_ledger(TEST_TENANT_ID, aid, {}, count_zero_usage_task=True)
        assert ok is True
        (row,) = session.added
        assert isinstance(row, AgentCostLedger)
        assert (row.agent_id, row.tenant_id) == (aid, TEST_TENANT_ID)
        assert (row.task_count, row.token_count, row.cost_usd) == (1, 0, 0.0)

    @pytest.mark.asyncio
    async def test_existing_row_is_incremented(self):
        from api.v1.agents import _record_cost_ledger

        ledger = SimpleNamespace(token_count=10, cost_usd=Decimal("0.5"), task_count=2)
        session = _FakeSession(ledger)
        with patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(session)):
            ok = await _record_cost_ledger(
                TEST_TENANT_ID, uuid.uuid4(), {"llm_tokens_used": 5, "llm_cost_usd": 0.25}, count_zero_usage_task=True
            )
        assert ok is True
        assert (ledger.token_count, ledger.cost_usd, ledger.task_count) == (15, 0.75, 3)
        assert session.added == []

    @pytest.mark.asyncio
    async def test_malformed_usage_still_counts_the_task(self):
        from api.v1.agents import _record_cost_ledger

        session = _FakeSession(None)
        with patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(session)):
            ok = await _record_cost_ledger(
                TEST_TENANT_ID, uuid.uuid4(), {"llm_tokens_used": "n/a"}, count_zero_usage_task=True
            )
        assert ok is True
        (row,) = session.added
        assert (row.task_count, row.token_count) == (1, 0)

    @pytest.mark.asyncio
    async def test_db_failure_returns_false_and_does_not_raise(self):
        from api.v1.agents import _record_cost_ledger

        session = _FakeSession(None)
        session.execute = AsyncMock(side_effect=SQLAlchemyError("boom"))
        with patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(session)):
            ok = await _record_cost_ledger(TEST_TENANT_ID, uuid.uuid4(), {"llm_tokens_used": 5})
        assert ok is False

    def test_run_agent_uses_the_shared_helper_and_keeps_budget_tracking_failed(self):
        from api.v1.agents import run_agent

        src = inspect.getsource(run_agent)
        assert "await _record_cost_ledger(tid, agent_id, perf)" in src
        assert 'hitl_trigger = hitl_trigger or "budget_tracking_failed"' in src

    def _chat(self, app, ledger_mock, perf):
        fin = _agent("finance")
        lg = AsyncMock(
            return_value={"status": "completed", "output": {"answer": "42"}, "confidence": 0.9, "performance": perf}
        )
        with (
            _admin(app) as c,
            patch("api.v1.chat.get_tenant_session", side_effect=_session_factory(_FakeSession(fin))),
            patch("api.v1.agents._resolve_agent_connector_ids_for_type", AsyncMock(return_value=[])),
            patch("api.v1.chat._record_cost_ledger", ledger_mock),
            patch("core.langgraph.runner.run_agent", lg),
        ):
            resp = c.post(
                "/api/v1/chat/query", json={"query": "hello", "company_id": COMPANY, "agent_id": str(fin.id)}
            )
        return resp, fin

    def test_chat_turn_records_ledger_even_with_zero_tokens(self, app):
        ledger = AsyncMock(return_value=True)
        resp, fin = self._chat(app, ledger, {"llm_tokens_used": 0, "llm_cost_usd": 0})
        assert resp.status_code == 200
        ledger.assert_awaited_once_with(
            TEST_TENANT_ID, fin.id, {"llm_tokens_used": 0, "llm_cost_usd": 0}, count_zero_usage_task=True
        )

    def test_chat_ledger_failure_does_not_fail_the_chat(self, app):
        ledger = AsyncMock(return_value=False)
        resp, _ = self._chat(app, ledger, {"llm_tokens_used": 7, "llm_cost_usd": 0.01})
        assert resp.status_code == 200
        assert resp.json()["answer"]
        ledger.assert_awaited_once()


# ---------------------------------------------------------------------------
# #46 — no connectors, no default tools
# ---------------------------------------------------------------------------


class TestNoStaticDefaultTools:
    def test_derive_default_tools_without_connectors_is_empty(self):
        from api.v1.agents import _derive_default_tools

        assert _derive_default_tools("ap_processor", "finance", None) == []
        assert _derive_default_tools("ap_processor", "finance", []) == []

    def test_chat_runs_agent_with_its_own_empty_tool_list(self, app):
        fin = _agent("finance", authorized_tools=[])
        lg = AsyncMock(
            return_value={"status": "completed", "output": {"answer": "42"}, "confidence": 0.9, "performance": {}}
        )
        with (
            _admin(app) as c,
            patch("api.v1.chat.get_tenant_session", side_effect=_session_factory(_FakeSession(fin))),
            patch("api.v1.agents._resolve_agent_connector_ids_for_type", AsyncMock(return_value=[])),
            patch("api.v1.chat._record_cost_ledger", AsyncMock(return_value=True)),
            patch("core.langgraph.runner.run_agent", lg),
        ):
            resp = c.post(
                "/api/v1/chat/query", json={"query": "hello", "company_id": COMPANY, "agent_id": str(fin.id)}
            )
        assert resp.status_code == 200
        assert lg.await_args.kwargs["authorized_tools"] == []

    def test_chat_module_no_longer_imports_the_static_maps(self):
        from api.v1 import chat

        src = inspect.getsource(chat)
        assert "_AGENT_TYPE_DEFAULT_TOOLS" not in src
        assert "_DOMAIN_DEFAULT_TOOLS" not in src


# ---------------------------------------------------------------------------
# #24 — edited_by / created_by come from agenticorg:user_id, never sub
# ---------------------------------------------------------------------------


class TestUserUuidFromClaims:
    @pytest.mark.parametrize("module", ["api.v1.agents", "api.v1.prompt_templates"])
    def test_sub_is_never_used_even_when_uuid_shaped(self, module):
        import importlib

        fn = importlib.import_module(module)._user_uuid_from_claims
        oidc_subject = str(uuid.uuid4())
        assert fn({"sub": oidc_subject}) is None
        assert fn({"sub": "cfo@agenticorg.local"}) is None

    @pytest.mark.parametrize("module", ["api.v1.agents", "api.v1.prompt_templates"])
    def test_agenticorg_user_id_is_used(self, module):
        import importlib

        fn = importlib.import_module(module)._user_uuid_from_claims
        uid = uuid.uuid4()
        assert fn({"sub": "cfo@agenticorg.local", "agenticorg:user_id": str(uid)}) == uid
        assert fn({"user_id": str(uid)}) == uid
        assert fn({"agenticorg:user_id": "garbage"}) is None
        assert fn(None) is None


# ---------------------------------------------------------------------------
# #31 sibling — chat and /run pass the agent's pinned LLM provider
# ---------------------------------------------------------------------------


class TestLlmProviderPassThrough:
    def test_pinned_provider_prefers_column_then_config(self):
        from api.v1.agents import _pinned_llm_provider

        assert _pinned_llm_provider("anthropic", {"provider": "gemini"}) == "anthropic"
        assert _pinned_llm_provider(None, {"provider": "gemini"}) == "gemini"
        assert _pinned_llm_provider("", None) is None
        assert _pinned_llm_provider(MagicMock(), MagicMock()) is None

    def _chat(self, app, agent, *, body_agent_id: bool, finder=None):
        lg = AsyncMock(
            return_value={"status": "completed", "output": {"answer": "42"}, "confidence": 0.9, "performance": {}}
        )
        json_body = {"query": "cash runway", "company_id": COMPANY}
        if body_agent_id:
            json_body["agent_id"] = str(agent.id)
        patches = [
            patch("api.v1.chat.get_tenant_session", side_effect=_session_factory(_FakeSession(agent))),
            patch("api.v1.agents._resolve_agent_connector_ids_for_type", AsyncMock(return_value=[])),
            patch("api.v1.chat._record_cost_ledger", AsyncMock(return_value=True)),
            patch("core.langgraph.runner.run_agent", lg),
        ]
        if finder is not None:
            patches.append(patch("api.v1.chat._find_agent_for_domain", finder))
        with _admin(app) as c:
            for p in patches:
                p.start()
            try:
                resp = c.post("/api/v1/chat/query", json=json_body)
            finally:
                for p in reversed(patches):
                    p.stop()
        return resp, lg

    def test_chat_by_agent_id_passes_provider(self, app):
        fin = _agent("finance", llm_provider="anthropic", llm_config={})
        resp, lg = self._chat(app, fin, body_agent_id=True)
        assert resp.status_code == 200, resp.text
        assert lg.await_args.kwargs["llm_provider"] == "anthropic"

    def test_chat_by_domain_routing_passes_provider(self, app):
        fin = _agent("finance", llm_provider=None, llm_config={"provider": "openai"})
        finder = AsyncMock(return_value=("CFO", str(fin.id), "ap_processor", []))
        resp, lg = self._chat(app, fin, body_agent_id=False, finder=finder)
        assert resp.status_code == 200, resp.text
        assert lg.await_args.kwargs["llm_provider"] == "openai"

    def test_chat_domain_routing_provider_read_failure_is_503_not_unpinned_run(self, app):
        fin = _agent("finance")
        session = _FakeSession(fin)
        session.execute = AsyncMock(side_effect=SQLAlchemyError("down"))
        lg = AsyncMock()
        finder = AsyncMock(return_value=("CFO", str(fin.id), "ap_processor", []))
        with (
            _admin(app) as c,
            patch("api.v1.chat._find_agent_for_domain", finder),
            patch("api.v1.chat.get_tenant_session", side_effect=_session_factory(session)),
            patch("core.langgraph.runner.run_agent", lg),
        ):
            resp = c.post("/api/v1/chat/query", json={"query": "cash runway", "company_id": COMPANY})
        assert resp.status_code == 503
        lg.assert_not_called()

    def test_run_agent_passes_pinned_provider(self):
        from api.v1.agents import run_agent

        src = inspect.getsource(run_agent)
        assert (
            'llm_provider=_pinned_llm_provider(agent_config.get("llm_provider"), agent_config.get("llm_config"))'
            in src
        )


# ---------------------------------------------------------------------------
# Containerized full-stack replay (2026-09-14): POST /agents edge cases that
# escaped as opaque E1001 500s on a fresh docker database.
# ---------------------------------------------------------------------------


class _IntegrityOnFlushSession(_FakeSession):
    async def flush(self):
        from sqlalchemy.exc import IntegrityError

        raise IntegrityError(
            "INSERT INTO agents",
            {},
            Exception("duplicate key value violates unique constraint agents_tenant_id_agent_type_key"),
        )


class TestAgentCreateBoundary:
    def test_unknown_initial_status_is_422_not_stored(self, app):
        session = _FakeSession(None)
        with (
            _admin(app) as c,
            patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(session)),
        ):
            resp = c.post(
                "/api/v1/agents",
                json={"name": "E2E HR", "agent_type": "onboarding_agent", "domain": "hr", "initial_status": "draft"},
            )
        assert resp.status_code == 422, resp.text
        assert session.added == []

    def test_duplicate_agent_is_409_not_500(self, app):
        session = _IntegrityOnFlushSession(None)
        with (
            _admin(app) as c,
            patch("api.v1.agents.get_tenant_session", side_effect=_session_factory(session)),
        ):
            resp = c.post(
                "/api/v1/agents",
                json={"name": "E2E HR", "agent_type": "onboarding_agent", "domain": "hr", "initial_status": "paused"},
            )
        assert resp.status_code == 409, resp.text
        assert "already exists" in resp.json()["detail"]

    def test_clone_duplicate_maps_to_409(self):
        import api.v1.agents as agents_mod

        src = inspect.getsource(agents_mod.clone_agent)
        assert "except IntegrityError" in src and "409" in src
