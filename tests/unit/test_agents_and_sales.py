"""Unit tests for api.v1.agents and api.v1.sales modules."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# ═══════════════════════════════════════════════════════════════════════════════
# Shared fixtures & helpers
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def tenant_id():
    return "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def tenant_uuid():
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock()
    return session


def _make_tenant_session_ctx(mock_session):
    """Return a context-manager mock for get_tenant_session."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def make_mock_agent(**overrides):
    agent = MagicMock()
    agent.id = overrides.get("id", uuid.uuid4())
    agent.tenant_id = overrides.get(
        "tenant_id", uuid.UUID("00000000-0000-0000-0000-000000000001")
    )
    agent.name = overrides.get("name", "Test Agent")
    agent.employee_name = overrides.get("employee_name", "Test Employee")
    agent.agent_type = overrides.get("agent_type", "ap_processor")
    agent.domain = overrides.get("domain", "finance")
    agent.status = overrides.get("status", "shadow")
    agent.version = overrides.get("version", "1.0.0")
    agent.confidence_floor = overrides.get("confidence_floor", Decimal("0.880"))
    agent.shadow_sample_count = overrides.get("shadow_sample_count", 0)
    agent.shadow_min_samples = overrides.get("shadow_min_samples", 10)
    agent.shadow_accuracy_current = overrides.get("shadow_accuracy_current", None)
    agent.shadow_accuracy_floor = overrides.get(
        "shadow_accuracy_floor", Decimal("0.950")
    )
    agent.shadow_comparison_agent_id = overrides.get(
        "shadow_comparison_agent_id", None
    )
    agent.parent_agent_id = overrides.get("parent_agent_id", None)
    agent.reporting_to = overrides.get("reporting_to", None)
    agent.org_level = overrides.get("org_level", 0)
    agent.routing_filter = overrides.get("routing_filter", {})
    agent.specialization = overrides.get("specialization", None)
    agent.designation = overrides.get("designation", None)
    agent.avatar_url = overrides.get("avatar_url", None)
    agent.is_builtin = overrides.get("is_builtin", False)
    agent.system_prompt_text = overrides.get(
        "system_prompt_text", "You are a test agent."
    )
    agent.system_prompt_ref = overrides.get("system_prompt_ref", "")
    agent.llm_model = overrides.get("llm_model", "gemini-2.5-flash")
    agent.llm_fallback = overrides.get("llm_fallback", None)
    agent.llm_config = overrides.get("llm_config", {})
    agent.hitl_condition = overrides.get("hitl_condition", "")
    agent.max_retries = overrides.get("max_retries", 3)
    agent.retry_backoff = overrides.get("retry_backoff", "exponential")
    agent.authorized_tools = overrides.get("authorized_tools", [])
    agent.output_schema = overrides.get("output_schema", None)
    agent.prompt_variables = overrides.get("prompt_variables", {})
    agent.cost_controls = overrides.get("cost_controls", {})
    agent.scaling = overrides.get("scaling", {})
    agent.tags = overrides.get("tags", [])
    agent.ttl_hours = overrides.get("ttl_hours", None)
    agent.expires_at = overrides.get("expires_at", None)
    agent.config = overrides.get("config", {})
    agent.description = overrides.get("description", None)
    agent.created_at = overrides.get("created_at", datetime(2026, 1, 1, tzinfo=UTC))
    agent.updated_at = overrides.get("updated_at", datetime(2026, 1, 1, tzinfo=UTC))
    return agent


def make_mock_lead(**overrides):
    lead = MagicMock()
    lead.id = overrides.get("id", uuid.uuid4())
    lead.tenant_id = overrides.get(
        "tenant_id", uuid.UUID("00000000-0000-0000-0000-000000000001")
    )
    lead.name = overrides.get("name", "Amit Sharma")
    lead.email = overrides.get("email", "amit@example.com")
    lead.company = overrides.get("company", "TestCorp")
    lead.role = overrides.get("role", "VP Finance")
    lead.phone = overrides.get("phone", "+91-9876543210")
    lead.source = overrides.get("source", "website")
    lead.stage = overrides.get("stage", "new")
    lead.score = overrides.get("score", 0)
    lead.score_factors = overrides.get("score_factors", {})
    lead.assigned_agent_id = overrides.get("assigned_agent_id", None)
    lead.assigned_human = overrides.get("assigned_human", None)
    lead.budget = overrides.get("budget", None)
    lead.authority = overrides.get("authority", None)
    lead.need = overrides.get("need", None)
    lead.timeline = overrides.get("timeline", None)
    lead.last_contacted_at = overrides.get("last_contacted_at", None)
    lead.next_followup_at = overrides.get("next_followup_at", None)
    lead.followup_count = overrides.get("followup_count", 0)
    lead.demo_scheduled_at = overrides.get("demo_scheduled_at", None)
    lead.trial_started_at = overrides.get("trial_started_at", None)
    lead.deal_value_usd = overrides.get("deal_value_usd", None)
    lead.lost_reason = overrides.get("lost_reason", None)
    lead.notes = overrides.get("notes", None)
    lead.created_at = overrides.get("created_at", datetime(2026, 1, 1, tzinfo=UTC))
    lead.updated_at = overrides.get("updated_at", datetime(2026, 1, 1, tzinfo=UTC))
    return lead


# ═══════════════════════════════════════════════════════════════════════════════
# agents.py — _agent_to_dict
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentToDict:
    """Tests for _agent_to_dict helper."""

    def test_basic_fields(self):
        from api.v1.agents import _agent_to_dict

        agent = make_mock_agent(name="MyAgent", domain="finance", status="active")
        result = _agent_to_dict(agent)
        assert result["name"] == "MyAgent"
        assert result["domain"] == "finance"
        assert result["status"] == "active"

    def test_id_is_string(self):
        from api.v1.agents import _agent_to_dict

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid)
        result = _agent_to_dict(agent)
        assert result["id"] == str(aid)

    def test_all_expected_keys_present(self):
        from api.v1.agents import _agent_to_dict

        agent = make_mock_agent()
        result = _agent_to_dict(agent)
        expected_keys = {
            "id", "name", "agent_type", "domain", "status", "version",
            "description", "system_prompt_ref", "prompt_variables",
            "llm_model", "llm_fallback", "llm_config",
            "confidence_floor", "hitl_condition", "max_retries",
            "retry_backoff", "authorized_tools", "output_schema",
            "parent_agent_id", "shadow_comparison_agent_id",
            "shadow_min_samples", "shadow_accuracy_floor",
            "shadow_sample_count", "shadow_accuracy_current",
            "cost_controls", "scaling", "tags", "ttl_hours",
            "expires_at", "created_at", "updated_at",
            "employee_name", "avatar_url", "designation",
            "specialization", "routing_filter", "is_builtin",
            "system_prompt_text", "reporting_to", "org_level",
            "prompt_amendments",
        }
        assert set(result.keys()) == expected_keys

    def test_parent_agent_id_none(self):
        from api.v1.agents import _agent_to_dict

        agent = make_mock_agent(parent_agent_id=None)
        result = _agent_to_dict(agent)
        assert result["parent_agent_id"] is None

    def test_parent_agent_id_set(self):
        from api.v1.agents import _agent_to_dict

        pid = uuid.uuid4()
        agent = make_mock_agent(parent_agent_id=pid)
        result = _agent_to_dict(agent)
        assert result["parent_agent_id"] == str(pid)

    def test_confidence_floor_is_float(self):
        from api.v1.agents import _agent_to_dict

        agent = make_mock_agent(confidence_floor=Decimal("0.950"))
        result = _agent_to_dict(agent)
        assert isinstance(result["confidence_floor"], float)
        assert result["confidence_floor"] == 0.95

    def test_shadow_accuracy_current_none(self):
        from api.v1.agents import _agent_to_dict

        agent = make_mock_agent(shadow_accuracy_current=None)
        result = _agent_to_dict(agent)
        assert result["shadow_accuracy_current"] is None

    def test_shadow_accuracy_current_set(self):
        from api.v1.agents import _agent_to_dict

        agent = make_mock_agent(shadow_accuracy_current=Decimal("0.972"))
        result = _agent_to_dict(agent)
        assert isinstance(result["shadow_accuracy_current"], float)
        assert result["shadow_accuracy_current"] == pytest.approx(0.972)

    def test_expires_at_none(self):
        from api.v1.agents import _agent_to_dict

        agent = make_mock_agent(expires_at=None)
        result = _agent_to_dict(agent)
        assert result["expires_at"] is None

    def test_expires_at_isoformat(self):
        from api.v1.agents import _agent_to_dict

        dt = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
        agent = make_mock_agent(expires_at=dt)
        result = _agent_to_dict(agent)
        assert result["expires_at"] == dt.isoformat()

    def test_virtual_employee_persona_fields(self):
        from api.v1.agents import _agent_to_dict

        agent = make_mock_agent(
            employee_name="Priya",
            designation="Senior Analyst",
            specialization="Invoice Processing",
            routing_filter={"vendor_tier": "gold"},
            org_level=2,
            reporting_to="Head of Finance",
        )
        result = _agent_to_dict(agent)
        assert result["employee_name"] == "Priya"
        assert result["designation"] == "Senior Analyst"
        assert result["specialization"] == "Invoice Processing"
        assert result["routing_filter"] == {"vendor_tier": "gold"}
        assert result["org_level"] == 2
        assert result["reporting_to"] == "Head of Finance"


# ═══════════════════════════════════════════════════════════════════════════════
# agents.py — create_agent
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateAgent:
    @pytest.mark.asyncio
    async def test_create_agent_returns_id_and_status(self, mock_session, tenant_id):
        from api.v1.agents import create_agent

        # Build a body mock that mimics AgentCreate
        body = MagicMock()
        body.name = "NewAgent"
        body.agent_type = "ap_processor"
        body.domain = "finance"
        body.system_prompt = "prompt_ref"
        body.system_prompt_text = "You are a test."
        body.prompt_variables = {}
        body.llm = MagicMock()
        body.llm.model = "gemini-2.5-flash"
        body.llm.fallback_model = None
        body.llm.model_dump.return_value = {"model": "gemini-2.5-flash"}
        body.confidence_floor = 0.88
        body.hitl_policy = MagicMock()
        body.hitl_policy.condition = "confidence < 0.88"
        body.hitl_policy.model_dump.return_value = {"condition": "confidence < 0.88"}
        body.max_retries = 3
        body.authorized_tools = []
        body.output_schema = None
        body.initial_status = "shadow"
        body.shadow_comparison_agent = None
        body.shadow_min_samples = 10
        body.shadow_accuracy_floor = 0.95
        body.cost_controls = MagicMock()
        body.cost_controls.model_dump.return_value = {}
        body.scaling = MagicMock()
        body.scaling.model_dump.return_value = {}
        body.ttl_hours = None
        body.employee_name = None
        body.avatar_url = None
        body.designation = None
        body.specialization = None
        body.routing_filter = {}
        body.reporting_to = None
        body.org_level = 0
        body.parent_agent_id = None

        # Mock execute to handle shadow limit queries (Tenant + count)
        mock_tenant = MagicMock()
        mock_tenant.settings = {}
        mock_tenant_result = MagicMock()
        mock_tenant_result.scalar_one_or_none.return_value = mock_tenant

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0

        mock_session.execute = AsyncMock(
            side_effect=[mock_tenant_result, mock_count_result, MagicMock(), MagicMock()]
        )

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await create_agent(body=body, tenant_id=tenant_id)

        assert "agent_id" in result
        assert result["status"] == "shadow"
        assert result["version"] == "1.0.0"
        assert result["token_issued"] is True

    @pytest.mark.asyncio
    async def test_create_agent_with_parent_id(self, mock_session, tenant_id):
        from api.v1.agents import create_agent

        parent_id = str(uuid.uuid4())
        body = MagicMock()
        body.name = "ChildAgent"
        body.agent_type = "ap_processor"
        body.domain = "finance"
        body.system_prompt = ""
        body.system_prompt_text = ""
        body.prompt_variables = {}
        body.llm = MagicMock()
        body.llm.model = "gemini-2.5-flash"
        body.llm.fallback_model = None
        body.llm.model_dump.return_value = {}
        body.confidence_floor = 0.88
        body.hitl_policy = MagicMock()
        body.hitl_policy.condition = ""
        body.hitl_policy.model_dump.return_value = {"condition": ""}
        body.max_retries = 3
        body.authorized_tools = []
        body.output_schema = None
        body.initial_status = None
        body.shadow_comparison_agent = None
        body.shadow_min_samples = 10
        body.shadow_accuracy_floor = 0.95
        body.cost_controls = MagicMock()
        body.cost_controls.model_dump.return_value = {}
        body.scaling = MagicMock()
        body.scaling.model_dump.return_value = {}
        body.ttl_hours = None
        body.employee_name = "Child Employee"
        body.avatar_url = None
        body.designation = None
        body.specialization = None
        body.routing_filter = {}
        body.reporting_to = None
        body.org_level = 1
        body.parent_agent_id = parent_id

        # Mock shadow limit queries (Tenant + count)
        mock_tenant = MagicMock()
        mock_tenant.settings = {}
        mock_tenant_result = MagicMock()
        mock_tenant_result.scalar_one_or_none.return_value = mock_tenant
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0
        mock_session.execute = AsyncMock(
            side_effect=[mock_tenant_result, mock_count_result, MagicMock(), MagicMock()]
        )

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await create_agent(body=body, tenant_id=tenant_id)

        assert result["status"] == "shadow"


# ═══════════════════════════════════════════════════════════════════════════════
# agents.py — list_agents
# ═══════════════════════════════════════════════════════════════════════════════


class TestListAgents:
    @pytest.mark.asyncio
    async def test_list_agents_no_filters(self, mock_session, tenant_id):
        from api.v1.agents import list_agents

        agent1 = make_mock_agent(name="Agent1")
        agent2 = make_mock_agent(name="Agent2")

        # First call: count query, second: agent query
        count_result = MagicMock()
        count_result.scalar.return_value = 2
        agents_result = MagicMock()
        agents_result.scalars.return_value.all.return_value = [agent1, agent2]
        mock_session.execute = AsyncMock(
            side_effect=[count_result, agents_result]
        )

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await list_agents(tenant_id=tenant_id, user_domains=None)

        assert result.total == 2
        assert len(result.items) == 2
        assert result.page == 1

    @pytest.mark.asyncio
    async def test_list_agents_with_domain_filter(self, mock_session, tenant_id):
        from api.v1.agents import list_agents

        agent1 = make_mock_agent(name="Agent1", domain="hr")

        count_result = MagicMock()
        count_result.scalar.return_value = 1
        agents_result = MagicMock()
        agents_result.scalars.return_value.all.return_value = [agent1]
        mock_session.execute = AsyncMock(
            side_effect=[count_result, agents_result]
        )

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await list_agents(
                domain="hr", tenant_id=tenant_id, user_domains=None
            )

        assert result.total == 1
        assert result.items[0]["domain"] == "hr"

    @pytest.mark.asyncio
    async def test_list_agents_with_status_filter(self, mock_session, tenant_id):
        from api.v1.agents import list_agents

        agent1 = make_mock_agent(name="Active1", status="active")

        count_result = MagicMock()
        count_result.scalar.return_value = 1
        agents_result = MagicMock()
        agents_result.scalars.return_value.all.return_value = [agent1]
        mock_session.execute = AsyncMock(
            side_effect=[count_result, agents_result]
        )

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await list_agents(
                status="active", tenant_id=tenant_id, user_domains=None
            )

        assert result.total == 1
        assert result.items[0]["status"] == "active"

    @pytest.mark.asyncio
    async def test_list_agents_pagination(self, mock_session, tenant_id):
        from api.v1.agents import list_agents

        count_result = MagicMock()
        count_result.scalar.return_value = 50
        agents_result = MagicMock()
        agents_result.scalars.return_value.all.return_value = [
            make_mock_agent(name=f"Agent{i}") for i in range(20)
        ]
        mock_session.execute = AsyncMock(
            side_effect=[count_result, agents_result]
        )

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await list_agents(
                page=2, per_page=20, tenant_id=tenant_id, user_domains=None
            )

        assert result.total == 50
        assert result.pages == 3
        assert result.page == 2

    @pytest.mark.asyncio
    async def test_list_agents_empty(self, mock_session, tenant_id):
        from api.v1.agents import list_agents

        count_result = MagicMock()
        count_result.scalar.return_value = 0
        agents_result = MagicMock()
        agents_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(
            side_effect=[count_result, agents_result]
        )

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await list_agents(tenant_id=tenant_id, user_domains=None)

        assert result.total == 0
        assert result.items == []
        assert result.pages == 1


# ═══════════════════════════════════════════════════════════════════════════════
# agents.py — get_agent
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetAgent:
    @pytest.mark.asyncio
    async def test_get_agent_found(self, mock_session, tenant_id):
        from api.v1.agents import get_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, name="FoundAgent")
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_agent(agent_id=aid, tenant_id=tenant_id)

        assert result["id"] == str(aid)
        assert result["name"] == "FoundAgent"

    @pytest.mark.asyncio
    async def test_get_agent_not_found(self, mock_session, tenant_id):
        from api.v1.agents import get_agent

        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await get_agent(agent_id=uuid.uuid4(), tenant_id=tenant_id)

        assert exc_info.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# agents.py — update_agent (PATCH)
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdateAgent:
    @pytest.mark.asyncio
    async def test_update_agent_name(self, mock_session, tenant_id):
        from api.v1.agents import update_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, status="shadow")
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = MagicMock()
        body.model_dump.return_value = {"name": "NewName"}

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await update_agent(
                agent_id=aid, body=body, tenant_id=tenant_id
            )

        assert result["updated"] is True
        assert agent.name == "NewName"

    @pytest.mark.asyncio
    async def test_update_agent_prompt_lock_on_active(self, mock_session, tenant_id):
        from api.v1.agents import update_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, status="active")
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = MagicMock()
        body.model_dump.return_value = {"system_prompt_text": "new prompt"}

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await update_agent(
                    agent_id=aid, body=body, tenant_id=tenant_id
                )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_update_agent_not_found(self, mock_session, tenant_id):
        from api.v1.agents import update_agent

        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = MagicMock()
        body.model_dump.return_value = {"name": "X"}

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await update_agent(
                    agent_id=uuid.uuid4(), body=body, tenant_id=tenant_id
                )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_agent_persona_fields(self, mock_session, tenant_id):
        from api.v1.agents import update_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, status="shadow")
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = MagicMock()
        body.model_dump.return_value = {
            "employee_name": "Updated Employee",
            "designation": "Lead",
            "org_level": 3,
        }

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await update_agent(
                agent_id=aid, body=body, tenant_id=tenant_id
            )

        assert result["updated"] is True
        assert agent.employee_name == "Updated Employee"
        assert agent.designation == "Lead"
        assert agent.org_level == 3

    @pytest.mark.asyncio
    async def test_update_agent_prompt_edit_on_shadow_creates_history(
        self, mock_session, tenant_id
    ):
        from api.v1.agents import update_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(
            id=aid, status="shadow", system_prompt_text="old prompt"
        )
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = MagicMock()
        body.model_dump.return_value = {
            "system_prompt_text": "new prompt",
            "change_reason": "improvement",
        }

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await update_agent(
                agent_id=aid, body=body, tenant_id=tenant_id
            )

        assert result["updated"] is True
        # PromptEditHistory should have been added to session
        assert mock_session.add.called

    @pytest.mark.asyncio
    async def test_update_agent_confidence_floor(self, mock_session, tenant_id):
        from api.v1.agents import update_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, status="shadow")
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = MagicMock()
        body.model_dump.return_value = {"confidence_floor": 0.92}

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            await update_agent(agent_id=aid, body=body, tenant_id=tenant_id)

        assert agent.confidence_floor == Decimal("0.92")


# ═══════════════════════════════════════════════════════════════════════════════
# agents.py — promote_agent
# ═══════════════════════════════════════════════════════════════════════════════


class TestPromoteAgent:
    @pytest.mark.asyncio
    async def test_promote_shadow_to_active(self, mock_session, tenant_id):
        from api.v1.agents import promote_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(
            id=aid,
            status="shadow",
            shadow_sample_count=15,
            shadow_min_samples=10,
            shadow_accuracy_current=Decimal("0.97"),
            shadow_accuracy_floor=Decimal("0.95"),
        )
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await promote_agent(agent_id=aid, tenant_id=tenant_id)

        assert result["promoted"] is True
        assert result["from"] == "shadow"
        assert result["to"] == "active"

    @pytest.mark.asyncio
    async def test_promote_insufficient_samples(self, mock_session, tenant_id):
        from api.v1.agents import promote_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(
            id=aid,
            status="shadow",
            shadow_sample_count=3,
            shadow_min_samples=10,
        )
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await promote_agent(agent_id=aid, tenant_id=tenant_id)

        assert exc_info.value.status_code == 409
        assert "samples" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_promote_below_accuracy_floor(self, mock_session, tenant_id):
        from api.v1.agents import promote_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(
            id=aid,
            status="shadow",
            shadow_sample_count=15,
            shadow_min_samples=10,
            shadow_accuracy_current=Decimal("0.80"),
            shadow_accuracy_floor=Decimal("0.95"),
        )
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await promote_agent(agent_id=aid, tenant_id=tenant_id)

        assert exc_info.value.status_code == 409
        assert "accuracy" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_promote_agent_not_found(self, mock_session, tenant_id):
        from api.v1.agents import promote_agent

        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await promote_agent(agent_id=uuid.uuid4(), tenant_id=tenant_id)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_promote_active_agent_rejected(self, mock_session, tenant_id):
        from api.v1.agents import promote_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, status="active")
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await promote_agent(agent_id=aid, tenant_id=tenant_id)

        assert exc_info.value.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# agents.py — pause_agent / resume_agent
# ═══════════════════════════════════════════════════════════════════════════════


class TestPauseAgent:
    @pytest.mark.asyncio
    async def test_pause_active_agent(self, mock_session, tenant_id):
        from api.v1.agents import pause_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, status="active")
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await pause_agent(agent_id=aid, tenant_id=tenant_id)

        assert result["status"] == "paused"
        assert result["previous_status"] == "active"
        assert result["token_revoked"] is True

    @pytest.mark.asyncio
    async def test_pause_already_paused(self, mock_session, tenant_id):
        from api.v1.agents import pause_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, status="paused")
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await pause_agent(agent_id=aid, tenant_id=tenant_id)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_pause_retired_agent(self, mock_session, tenant_id):
        from api.v1.agents import pause_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, status="retired")
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await pause_agent(agent_id=aid, tenant_id=tenant_id)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_pause_agent_not_found(self, mock_session, tenant_id):
        from api.v1.agents import pause_agent

        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await pause_agent(agent_id=uuid.uuid4(), tenant_id=tenant_id)

        assert exc_info.value.status_code == 404


class TestResumeAgent:
    @pytest.mark.asyncio
    async def test_resume_paused_agent_from_active(self, mock_session, tenant_id):
        """Agent paused from active resumes back to active."""
        from api.v1.agents import resume_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, status="paused")

        # First execute returns the agent, second returns the lifecycle event
        agent_result = MagicMock()
        agent_result.scalar_one_or_none.return_value = agent
        pause_event = MagicMock()
        pause_event.from_status = "active"
        event_result = MagicMock()
        event_result.scalar_one_or_none.return_value = pause_event
        mock_session.execute = AsyncMock(side_effect=[agent_result, event_result])

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await resume_agent(agent_id=aid, tenant_id=tenant_id)

        assert result["status"] == "active"

    @pytest.mark.asyncio
    async def test_resume_paused_agent_from_shadow(self, mock_session, tenant_id):
        """TC_AGENT-007: Agent paused from shadow resumes back to shadow, not active."""
        from api.v1.agents import resume_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, status="paused")

        agent_result = MagicMock()
        agent_result.scalar_one_or_none.return_value = agent
        pause_event = MagicMock()
        pause_event.from_status = "shadow"
        event_result = MagicMock()
        event_result.scalar_one_or_none.return_value = pause_event
        mock_session.execute = AsyncMock(side_effect=[agent_result, event_result])

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await resume_agent(agent_id=aid, tenant_id=tenant_id)

        assert result["status"] == "shadow"

    @pytest.mark.asyncio
    async def test_resume_shadow_agent_blocked_if_accuracy_below_floor(
        self, mock_session, tenant_id
    ):
        """TC_AGENT-007: Resume from shadow is blocked when accuracy is below floor."""
        from api.v1.agents import resume_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(
            id=aid,
            status="paused",
            shadow_min_samples=10,
            shadow_accuracy_current=Decimal("0.800"),
            shadow_accuracy_floor=Decimal("0.950"),
        )

        agent_result = MagicMock()
        agent_result.scalar_one_or_none.return_value = agent
        pause_event = MagicMock()
        pause_event.from_status = "shadow"
        event_result = MagicMock()
        event_result.scalar_one_or_none.return_value = pause_event
        mock_session.execute = AsyncMock(side_effect=[agent_result, event_result])

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await resume_agent(agent_id=aid, tenant_id=tenant_id)

        assert exc_info.value.status_code == 409
        assert "below floor" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_resume_active_agent_rejected(self, mock_session, tenant_id):
        from api.v1.agents import resume_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, status="active")
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await resume_agent(agent_id=aid, tenant_id=tenant_id)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_resume_agent_not_found(self, mock_session, tenant_id):
        from api.v1.agents import resume_agent

        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await resume_agent(agent_id=uuid.uuid4(), tenant_id=tenant_id)

        assert exc_info.value.status_code == 404


class TestRetestAgent:
    @pytest.mark.asyncio
    async def test_retest_shadow_agent(self, mock_session, tenant_id):
        """TC_AGENT-008: Retest resets shadow counters."""
        from api.v1.agents import retest_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(
            id=aid,
            status="shadow",
            shadow_sample_count=15,
            shadow_accuracy_current=Decimal("0.920"),
        )
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await retest_agent(agent_id=aid, tenant_id=tenant_id)

        assert result["retest"] is True
        assert result["shadow_sample_count"] == 0
        assert result["shadow_accuracy_current"] is None
        assert result["previous_sample_count"] == 15
        assert result["previous_accuracy"] == 0.920

    @pytest.mark.asyncio
    async def test_retest_non_shadow_agent_rejected(self, mock_session, tenant_id):
        """TC_AGENT-008: Retest is only allowed for shadow agents."""
        from api.v1.agents import retest_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, status="active")
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await retest_agent(agent_id=aid, tenant_id=tenant_id)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_retest_agent_not_found(self, mock_session, tenant_id):
        from api.v1.agents import retest_agent

        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await retest_agent(agent_id=uuid.uuid4(), tenant_id=tenant_id)

        assert exc_info.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# agents.py — clone_agent
# ═══════════════════════════════════════════════════════════════════════════════


class TestCloneAgent:
    @pytest.mark.asyncio
    async def test_clone_agent_success(self, mock_session, tenant_id):
        from api.v1.agents import clone_agent

        parent_id = uuid.uuid4()
        parent = make_mock_agent(
            id=parent_id,
            name="ParentAgent",
            authorized_tools=["tool_a", "tool_b"],
        )
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = parent
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = MagicMock()
        body.name = "ClonedAgent"
        body.agent_type = "ap_processor"
        body.overrides = {}
        body.initial_status = "shadow"
        body.shadow_comparison_agent = None

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await clone_agent(
                agent_id=parent_id, body=body, tenant_id=tenant_id
            )

        assert "clone_id" in result
        assert result["parent_id"] == str(parent_id)
        assert result["status"] == "shadow"
        # Both clone Agent and AgentVersion added
        assert mock_session.add.call_count == 2

    @pytest.mark.asyncio
    async def test_clone_agent_scope_ceiling_violation(self, mock_session, tenant_id):
        from api.v1.agents import clone_agent

        parent_id = uuid.uuid4()
        parent = make_mock_agent(
            id=parent_id, authorized_tools=["tool_a"]
        )
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = parent
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = MagicMock()
        body.name = "BadClone"
        body.agent_type = "ap_processor"
        body.overrides = {"authorized_tools": ["tool_a", "tool_z"]}
        body.initial_status = "shadow"
        body.shadow_comparison_agent = None

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await clone_agent(
                    agent_id=parent_id, body=body, tenant_id=tenant_id
                )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_clone_agent_parent_not_found(self, mock_session, tenant_id):
        from api.v1.agents import clone_agent

        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = MagicMock()
        body.name = "Orphan"
        body.agent_type = "ap_processor"
        body.overrides = {}
        body.initial_status = None
        body.shadow_comparison_agent = None

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await clone_agent(
                    agent_id=uuid.uuid4(), body=body, tenant_id=tenant_id
                )

        assert exc_info.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# agents.py — get_org_tree
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetOrgTree:
    @pytest.mark.asyncio
    async def test_org_tree_with_parent_child(self, mock_session, tenant_id):
        from api.v1.agents import get_org_tree

        parent_id = uuid.uuid4()
        child_id = uuid.uuid4()

        parent = make_mock_agent(
            id=parent_id, name="Boss", parent_agent_id=None, org_level=0
        )
        child = make_mock_agent(
            id=child_id, name="Worker", parent_agent_id=parent_id, org_level=1
        )

        agents_result = MagicMock()
        agents_result.scalars.return_value.all.return_value = [parent, child]
        mock_session.execute = AsyncMock(return_value=agents_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_org_tree(tenant_id=tenant_id)

        assert result["flat_count"] == 2
        assert len(result["tree"]) == 1  # only root
        root = result["tree"][0]
        assert root["name"] == "Boss"
        assert len(root["children"]) == 1
        assert root["children"][0]["name"] == "Worker"

    @pytest.mark.asyncio
    async def test_org_tree_empty(self, mock_session, tenant_id):
        from api.v1.agents import get_org_tree

        agents_result = MagicMock()
        agents_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=agents_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_org_tree(tenant_id=tenant_id)

        assert result["flat_count"] == 0
        assert result["tree"] == []

    @pytest.mark.asyncio
    async def test_org_tree_multiple_roots(self, mock_session, tenant_id):
        from api.v1.agents import get_org_tree

        root1 = make_mock_agent(id=uuid.uuid4(), name="Root1", parent_agent_id=None)
        root2 = make_mock_agent(id=uuid.uuid4(), name="Root2", parent_agent_id=None)

        agents_result = MagicMock()
        agents_result.scalars.return_value.all.return_value = [root1, root2]
        mock_session.execute = AsyncMock(return_value=agents_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_org_tree(tenant_id=tenant_id)

        assert result["flat_count"] == 2
        assert len(result["tree"]) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# agents.py — import_agents_csv
# ═══════════════════════════════════════════════════════════════════════════════


class TestImportAgentsCsv:
    @pytest.mark.asyncio
    async def test_import_csv_basic(self, mock_session, tenant_id):
        from api.v1.agents import import_agents_csv

        csv_content = (
            "name,agent_type,domain,designation,org_level\n"
            "Agent1,ap_processor,finance,Analyst,1\n"
            "Agent2,hr_assistant,hr,Manager,0\n"
        )
        file = MagicMock()
        file.read = AsyncMock(return_value=csv_content.encode("utf-8"))

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await import_agents_csv(file=file, tenant_id=tenant_id)

        assert result["imported"] == 2
        assert result["skipped"] == 0

    @pytest.mark.asyncio
    async def test_import_csv_missing_required_field(self, mock_session, tenant_id):
        from api.v1.agents import import_agents_csv

        csv_content = "name,agent_type,domain\n,ap_processor,finance\n"
        file = MagicMock()
        file.read = AsyncMock(return_value=csv_content.encode("utf-8"))

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await import_agents_csv(file=file, tenant_id=tenant_id)

        assert result["imported"] == 0
        assert result["skipped"] == 1
        assert "missing required field" in result["skip_details"][0]["reason"]

    @pytest.mark.asyncio
    async def test_import_csv_self_reference_parent(self, mock_session, tenant_id):
        from api.v1.agents import import_agents_csv

        csv_content = (
            "name,agent_type,domain,reporting_to_name\n"
            "Agent1,ap_processor,finance,Agent1\n"
        )
        file = MagicMock()
        file.read = AsyncMock(return_value=csv_content.encode("utf-8"))

        # For the second pass (parent linking), mock all_agents
        all_agents_result = MagicMock()
        agent_mock = MagicMock()
        agent_mock.employee_name = "Agent1"
        agent_mock.name = "Agent1"
        agent_mock.domain = "finance"
        agent_mock.id = uuid.uuid4()
        all_agents_result.scalars.return_value.all.return_value = [agent_mock]

        call_count = 0
        original_session = mock_session

        async def side_effect_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # The second pass execute returns all agents
            return all_agents_result

        original_session.execute = AsyncMock(side_effect=side_effect_execute)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await import_agents_csv(file=file, tenant_id=tenant_id)

        assert result["imported"] == 1
        # Self-reference should be in skipped
        found_self_ref = any(
            "self-reference" in s.get("reason", "") for s in result["skip_details"]
        )
        assert found_self_ref

    @pytest.mark.asyncio
    async def test_import_csv_with_parent_linking(self, mock_session, tenant_id):
        from api.v1.agents import import_agents_csv

        csv_content = (
            "name,agent_type,domain,reporting_to_name\n"
            "Boss,manager,finance,\n"
            "Worker,ap_processor,finance,Boss\n"
        )
        file = MagicMock()
        file.read = AsyncMock(return_value=csv_content.encode("utf-8"))

        boss_id = uuid.uuid4()
        worker_id = uuid.uuid4()
        id_sequence = iter([boss_id, worker_id])

        # Capture agents added during first pass and assign valid UUIDs
        def capture_add(obj):
            # Only assign id to Agent objects (not AgentVersion etc.)
            if hasattr(obj, "agent_type") and hasattr(obj, "tenant_id"):
                obj.id = next(id_sequence)

        mock_session.add = MagicMock(side_effect=capture_add)

        # Mock for second pass
        boss_agent = MagicMock()
        boss_agent.employee_name = "Boss"
        boss_agent.name = "Boss"
        boss_agent.domain = "finance"
        boss_agent.id = boss_id

        worker_agent = MagicMock()
        worker_agent.employee_name = "Worker"
        worker_agent.name = "Worker"
        worker_agent.domain = "finance"
        worker_agent.id = worker_id

        all_result = MagicMock()
        all_result.scalars.return_value.all.return_value = [boss_agent, worker_agent]

        worker_row_result = MagicMock()
        worker_row_result.scalar_one_or_none.return_value = worker_agent

        # Second session: first call returns all_agents, second returns the agent to update
        second_session = AsyncMock()
        second_session.execute = AsyncMock(
            side_effect=[all_result, worker_row_result]
        )
        second_session.add = MagicMock()

        call_idx = [0]

        def make_ctx(*args, **kwargs):
            ctx = AsyncMock()
            if call_idx[0] == 0:
                ctx.__aenter__ = AsyncMock(return_value=mock_session)
            else:
                ctx.__aenter__ = AsyncMock(return_value=second_session)
            ctx.__aexit__ = AsyncMock(return_value=False)
            call_idx[0] += 1
            return ctx

        with patch("api.v1.agents.get_tenant_session", side_effect=make_ctx):
            result = await import_agents_csv(file=file, tenant_id=tenant_id)

        assert result["imported"] == 2
        assert result["parent_links_set"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# agents.py — get_prompt_history
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetPromptHistory:
    @pytest.mark.asyncio
    async def test_get_prompt_history_returns_entries(self, mock_session, tenant_id):
        from api.v1.agents import get_prompt_history

        aid = uuid.uuid4()
        entry = MagicMock()
        entry.id = uuid.uuid4()
        entry.agent_id = aid
        entry.edited_by = None
        entry.prompt_before = "old"
        entry.prompt_after = "new"
        entry.change_reason = "test"
        entry.created_at = datetime(2026, 3, 1, tzinfo=UTC)

        exec_result = MagicMock()
        exec_result.scalars.return_value.all.return_value = [entry]
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_prompt_history(
                agent_id=aid, tenant_id=tenant_id
            )

        assert len(result) == 1
        assert result[0]["prompt_before"] == "old"
        assert result[0]["prompt_after"] == "new"
        assert result[0]["change_reason"] == "test"

    @pytest.mark.asyncio
    async def test_get_prompt_history_empty(self, mock_session, tenant_id):
        from api.v1.agents import get_prompt_history

        exec_result = MagicMock()
        exec_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_prompt_history(
                agent_id=uuid.uuid4(), tenant_id=tenant_id
            )

        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# agents.py — get_agent_budget
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetAgentBudget:
    @pytest.mark.asyncio
    async def test_get_budget_under_cap(self, mock_session, tenant_id):
        from api.v1.agents import get_agent_budget

        aid = uuid.uuid4()
        agent = make_mock_agent(
            id=aid,
            cost_controls={"monthly_cost_cap_usd": 100, "daily_token_budget": 5000},
        )

        agent_result = MagicMock()
        agent_result.scalar_one_or_none.return_value = agent

        spend_result = MagicMock()
        spend_result.fetchone.return_value = (Decimal("45.00"), 2000, 50)

        mock_session.execute = AsyncMock(
            side_effect=[agent_result, spend_result]
        )

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_agent_budget(agent_id=aid, tenant_id=tenant_id)

        assert result["agent_id"] == str(aid)
        assert result["monthly_cap_usd"] == 100
        assert result["monthly_spent_usd"] == 45.0
        assert result["monthly_pct_used"] == 45.0
        assert result["warnings"] == []

    @pytest.mark.asyncio
    async def test_get_budget_exceeded(self, mock_session, tenant_id):
        from api.v1.agents import get_agent_budget

        aid = uuid.uuid4()
        agent = make_mock_agent(
            id=aid,
            cost_controls={"monthly_cost_cap_usd": 50, "daily_token_budget": 5000},
        )

        agent_result = MagicMock()
        agent_result.scalar_one_or_none.return_value = agent

        spend_result = MagicMock()
        spend_result.fetchone.return_value = (Decimal("55.00"), 3000, 80)

        mock_session.execute = AsyncMock(
            side_effect=[agent_result, spend_result]
        )

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_agent_budget(agent_id=aid, tenant_id=tenant_id)

        assert result["monthly_pct_used"] == 110.0
        assert any("exceeded" in w.lower() for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_get_budget_at_80_pct(self, mock_session, tenant_id):
        from api.v1.agents import get_agent_budget

        aid = uuid.uuid4()
        agent = make_mock_agent(
            id=aid,
            cost_controls={"monthly_cost_cap_usd": 100, "daily_token_budget": 5000},
        )

        agent_result = MagicMock()
        agent_result.scalar_one_or_none.return_value = agent

        spend_result = MagicMock()
        spend_result.fetchone.return_value = (Decimal("85.00"), 4000, 90)

        mock_session.execute = AsyncMock(
            side_effect=[agent_result, spend_result]
        )

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_agent_budget(agent_id=aid, tenant_id=tenant_id)

        assert result["monthly_pct_used"] == 85.0
        assert any("80%" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_get_budget_agent_not_found(self, mock_session, tenant_id):
        from api.v1.agents import get_agent_budget

        agent_result = MagicMock()
        agent_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=agent_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await get_agent_budget(
                    agent_id=uuid.uuid4(), tenant_id=tenant_id
                )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_budget_zero_cap(self, mock_session, tenant_id):
        from api.v1.agents import get_agent_budget

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, cost_controls={})

        agent_result = MagicMock()
        agent_result.scalar_one_or_none.return_value = agent

        spend_result = MagicMock()
        spend_result.fetchone.return_value = (Decimal("0"), 0, 0)

        mock_session.execute = AsyncMock(
            side_effect=[agent_result, spend_result]
        )

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_agent_budget(agent_id=aid, tenant_id=tenant_id)

        assert result["monthly_pct_used"] == 0
        assert result["warnings"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# sales.py — _lead_to_dict
# ═══════════════════════════════════════════════════════════════════════════════


class TestLeadToDict:
    def test_basic_fields(self):
        from api.v1.sales import _lead_to_dict

        lead = make_mock_lead(name="Amit", email="amit@x.com", company="TestCo")
        result = _lead_to_dict(lead)
        assert result["name"] == "Amit"
        assert result["email"] == "amit@x.com"
        assert result["company"] == "TestCo"

    def test_id_is_string(self):
        from api.v1.sales import _lead_to_dict

        lid = uuid.uuid4()
        lead = make_mock_lead(id=lid)
        result = _lead_to_dict(lead)
        assert result["id"] == str(lid)

    def test_all_expected_keys(self):
        from api.v1.sales import _lead_to_dict

        lead = make_mock_lead()
        result = _lead_to_dict(lead)
        expected_keys = {
            "id", "name", "email", "company", "role", "phone",
            "source", "stage", "score", "score_factors",
            "assigned_agent_id", "assigned_human",
            "budget", "authority", "need", "timeline",
            "last_contacted_at", "next_followup_at",
            "followup_count", "demo_scheduled_at",
            "trial_started_at", "deal_value_usd",
            "lost_reason", "notes", "created_at", "updated_at",
        }
        assert set(result.keys()) == expected_keys

    def test_datetime_fields_isoformat(self):
        from api.v1.sales import _lead_to_dict

        dt = datetime(2026, 3, 15, 10, 30, 0, tzinfo=UTC)
        lead = make_mock_lead(
            last_contacted_at=dt,
            next_followup_at=dt,
            demo_scheduled_at=dt,
            trial_started_at=dt,
            created_at=dt,
            updated_at=dt,
        )
        result = _lead_to_dict(lead)
        iso = dt.isoformat()
        assert result["last_contacted_at"] == iso
        assert result["next_followup_at"] == iso
        assert result["demo_scheduled_at"] == iso
        assert result["trial_started_at"] == iso

    def test_none_datetime_fields(self):
        from api.v1.sales import _lead_to_dict

        lead = make_mock_lead(
            last_contacted_at=None,
            next_followup_at=None,
            demo_scheduled_at=None,
            trial_started_at=None,
        )
        result = _lead_to_dict(lead)
        assert result["last_contacted_at"] is None
        assert result["next_followup_at"] is None
        assert result["demo_scheduled_at"] is None
        assert result["trial_started_at"] is None

    def test_deal_value_usd_decimal(self):
        from api.v1.sales import _lead_to_dict

        lead = make_mock_lead(deal_value_usd=Decimal("25000.50"))
        result = _lead_to_dict(lead)
        assert isinstance(result["deal_value_usd"], float)
        assert result["deal_value_usd"] == pytest.approx(25000.50)

    def test_deal_value_usd_none(self):
        from api.v1.sales import _lead_to_dict

        lead = make_mock_lead(deal_value_usd=None)
        result = _lead_to_dict(lead)
        assert result["deal_value_usd"] is None

    def test_assigned_agent_id_set(self):
        from api.v1.sales import _lead_to_dict

        aid = uuid.uuid4()
        lead = make_mock_lead(assigned_agent_id=aid)
        result = _lead_to_dict(lead)
        assert result["assigned_agent_id"] == str(aid)

    def test_assigned_agent_id_none(self):
        from api.v1.sales import _lead_to_dict

        lead = make_mock_lead(assigned_agent_id=None)
        result = _lead_to_dict(lead)
        assert result["assigned_agent_id"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# sales.py — get_pipeline
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetPipeline:
    @pytest.mark.asyncio
    async def test_get_pipeline_no_stage_filter(self, mock_session, tenant_id):
        from api.v1.sales import get_pipeline

        lead1 = make_mock_lead(stage="new", score=80)
        lead2 = make_mock_lead(stage="qualified", score=90)

        # Funnel result
        funnel_row1 = MagicMock()
        funnel_row1.stage = "new"
        funnel_row1.count = 5
        funnel_row2 = MagicMock()
        funnel_row2.stage = "qualified"
        funnel_row2.count = 3
        funnel_result = MagicMock()
        funnel_result.__iter__ = MagicMock(
            return_value=iter([funnel_row1, funnel_row2])
        )

        leads_result = MagicMock()
        leads_result.scalars.return_value.all.return_value = [lead1, lead2]

        mock_session.execute = AsyncMock(
            side_effect=[funnel_result, leads_result]
        )

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_pipeline(tenant_id=tenant_id)

        assert result["total"] == 8
        assert result["funnel"]["new"] == 5
        assert result["funnel"]["qualified"] == 3
        assert len(result["leads"]) == 2

    @pytest.mark.asyncio
    async def test_get_pipeline_with_stage_filter(self, mock_session, tenant_id):
        from api.v1.sales import get_pipeline

        lead1 = make_mock_lead(stage="qualified")

        funnel_row = MagicMock()
        funnel_row.stage = "qualified"
        funnel_row.count = 3
        funnel_result = MagicMock()
        funnel_result.__iter__ = MagicMock(return_value=iter([funnel_row]))

        leads_result = MagicMock()
        leads_result.scalars.return_value.all.return_value = [lead1]

        mock_session.execute = AsyncMock(
            side_effect=[funnel_result, leads_result]
        )

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_pipeline(stage="qualified", tenant_id=tenant_id)

        assert len(result["leads"]) == 1

    @pytest.mark.asyncio
    async def test_get_pipeline_empty(self, mock_session, tenant_id):
        from api.v1.sales import get_pipeline

        funnel_result = MagicMock()
        funnel_result.__iter__ = MagicMock(return_value=iter([]))

        leads_result = MagicMock()
        leads_result.scalars.return_value.all.return_value = []

        mock_session.execute = AsyncMock(
            side_effect=[funnel_result, leads_result]
        )

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_pipeline(tenant_id=tenant_id)

        assert result["total"] == 0
        assert result["leads"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# sales.py — get_lead
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetLead:
    @pytest.mark.asyncio
    async def test_get_lead_found_with_emails(self, mock_session, tenant_id):
        from api.v1.sales import get_lead

        lid = uuid.uuid4()
        lead = make_mock_lead(id=lid, name="Priya")

        email_mock = MagicMock()
        email_mock.id = uuid.uuid4()
        email_mock.sequence_name = "initial_outreach"
        email_mock.step_number = 0
        email_mock.email_subject = "Hello"
        email_mock.status = "sent"
        email_mock.sent_at = datetime(2026, 3, 10, tzinfo=UTC)

        lead_result = MagicMock()
        lead_result.scalar_one_or_none.return_value = lead

        emails_result = MagicMock()
        emails_result.scalars.return_value.all.return_value = [email_mock]

        mock_session.execute = AsyncMock(
            side_effect=[lead_result, emails_result]
        )

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_lead(lead_id=lid, tenant_id=tenant_id)

        assert result["name"] == "Priya"
        assert len(result["emails"]) == 1
        assert result["emails"][0]["subject"] == "Hello"
        assert result["emails"][0]["status"] == "sent"

    @pytest.mark.asyncio
    async def test_get_lead_not_found(self, mock_session, tenant_id):
        from api.v1.sales import get_lead

        lead_result = MagicMock()
        lead_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=lead_result)

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await get_lead(lead_id=uuid.uuid4(), tenant_id=tenant_id)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_lead_no_emails(self, mock_session, tenant_id):
        from api.v1.sales import get_lead

        lid = uuid.uuid4()
        lead = make_mock_lead(id=lid)

        lead_result = MagicMock()
        lead_result.scalar_one_or_none.return_value = lead

        emails_result = MagicMock()
        emails_result.scalars.return_value.all.return_value = []

        mock_session.execute = AsyncMock(
            side_effect=[lead_result, emails_result]
        )

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_lead(lead_id=lid, tenant_id=tenant_id)

        assert result["emails"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# sales.py — update_lead
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdateLead:
    @pytest.mark.asyncio
    async def test_update_lead_bant_fields(self, mock_session, tenant_id):
        from api.v1.sales import LeadUpdate, update_lead

        lid = uuid.uuid4()
        lead = make_mock_lead(id=lid)
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = lead
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = LeadUpdate(
            budget="$50K",
            authority="VP Level",
            need="automation",
            timeline="Q2 2026",
        )

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await update_lead(
                lead_id=lid, body=body, tenant_id=tenant_id
            )

        assert result["updated"] is True
        assert lead.budget == "$50K"
        assert lead.authority == "VP Level"
        assert lead.need == "automation"
        assert lead.timeline == "Q2 2026"

    @pytest.mark.asyncio
    async def test_update_lead_stage_transition(self, mock_session, tenant_id):
        from api.v1.sales import LeadUpdate, update_lead

        lid = uuid.uuid4()
        lead = make_mock_lead(id=lid, stage="new")
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = lead
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = LeadUpdate(stage="qualified")

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await update_lead(
                lead_id=lid, body=body, tenant_id=tenant_id
            )

        assert result["updated"] is True
        assert lead.stage == "qualified"

    @pytest.mark.asyncio
    async def test_update_lead_deal_value(self, mock_session, tenant_id):
        from api.v1.sales import LeadUpdate, update_lead

        lid = uuid.uuid4()
        lead = make_mock_lead(id=lid)
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = lead
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = LeadUpdate(deal_value_usd=75000.0)

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            await update_lead(lead_id=lid, body=body, tenant_id=tenant_id)

        assert lead.deal_value_usd == Decimal("75000.0")

    @pytest.mark.asyncio
    async def test_update_lead_not_found(self, mock_session, tenant_id):
        from api.v1.sales import LeadUpdate, update_lead

        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = LeadUpdate(stage="qualified")

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await update_lead(
                    lead_id=uuid.uuid4(), body=body, tenant_id=tenant_id
                )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_lead_followup_datetime(self, mock_session, tenant_id):
        from api.v1.sales import LeadUpdate, update_lead

        lid = uuid.uuid4()
        lead = make_mock_lead(id=lid)
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = lead
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = LeadUpdate(next_followup_at="2026-04-01T10:00:00+00:00")

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            await update_lead(lead_id=lid, body=body, tenant_id=tenant_id)

        assert lead.next_followup_at == datetime.fromisoformat(
            "2026-04-01T10:00:00+00:00"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# sales.py — get_due_followups
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetDueFollowups:
    @pytest.mark.asyncio
    async def test_get_due_followups_returns_leads(self, mock_session, tenant_id):
        from api.v1.sales import get_due_followups

        lead1 = make_mock_lead(
            name="OverdueLead",
            next_followup_at=datetime(2026, 3, 20, tzinfo=UTC),
        )
        exec_result = MagicMock()
        exec_result.scalars.return_value.all.return_value = [lead1]
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_due_followups(tenant_id=tenant_id)

        assert len(result) == 1
        assert result[0]["name"] == "OverdueLead"

    @pytest.mark.asyncio
    async def test_get_due_followups_empty(self, mock_session, tenant_id):
        from api.v1.sales import get_due_followups

        exec_result = MagicMock()
        exec_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=exec_result)

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_due_followups(tenant_id=tenant_id)

        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# sales.py — get_sales_metrics
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetSalesMetrics:
    @pytest.mark.asyncio
    async def test_get_metrics(self, mock_session, tenant_id):
        from api.v1.sales import get_sales_metrics

        total_result = MagicMock()
        total_result.scalar.return_value = 100

        new_result = MagicMock()
        new_result.scalar.return_value = 15

        funnel_row1 = MagicMock()
        funnel_row1.stage = "new"
        funnel_row1.count = 40
        funnel_row2 = MagicMock()
        funnel_row2.stage = "qualified"
        funnel_row2.count = 30
        funnel_result = MagicMock()
        funnel_result.__iter__ = MagicMock(
            return_value=iter([funnel_row1, funnel_row2])
        )

        avg_result = MagicMock()
        avg_result.scalar.return_value = 72.5

        emails_result = MagicMock()
        emails_result.scalar.return_value = 25

        stale_result = MagicMock()
        stale_result.scalar.return_value = 8

        mock_session.execute = AsyncMock(
            side_effect=[
                total_result, new_result, funnel_result,
                avg_result, emails_result, stale_result,
            ]
        )

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_sales_metrics(tenant_id=tenant_id)

        assert result["total_leads"] == 100
        assert result["new_this_week"] == 15
        assert result["funnel"]["new"] == 40
        assert result["funnel"]["qualified"] == 30
        assert result["avg_score"] == 72.5
        assert result["emails_sent_this_week"] == 25
        assert result["stale_leads"] == 8

    @pytest.mark.asyncio
    async def test_get_metrics_empty_db(self, mock_session, tenant_id):
        from api.v1.sales import get_sales_metrics

        zero_result = MagicMock()
        zero_result.scalar.return_value = 0
        empty_funnel = MagicMock()
        empty_funnel.__iter__ = MagicMock(return_value=iter([]))

        mock_session.execute = AsyncMock(
            side_effect=[
                zero_result,  # total
                zero_result,  # new this week
                empty_funnel,  # funnel
                zero_result,  # avg score
                zero_result,  # emails
                zero_result,  # stale
            ]
        )

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await get_sales_metrics(tenant_id=tenant_id)

        assert result["total_leads"] == 0
        assert result["new_this_week"] == 0
        assert result["avg_score"] == 0
        assert result["emails_sent_this_week"] == 0
        assert result["stale_leads"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# sales.py — seed_target_prospects
# ═══════════════════════════════════════════════════════════════════════════════


class TestSeedTargetProspects:
    @pytest.mark.asyncio
    async def test_seed_all_new(self, mock_session, tenant_id):
        from api.v1.sales import seed_target_prospects

        # No duplicates: every exists check returns None
        exists_result = MagicMock()
        exists_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=exists_result)

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await seed_target_prospects(
                auto_process=False, tenant_id=tenant_id
            )

        assert result["seeded"] == 20
        assert result["skipped_duplicates"] == 0
        assert result["processed_by_agent"] == 0

    @pytest.mark.asyncio
    async def test_seed_with_duplicates(self, mock_session, tenant_id):
        from api.v1.sales import seed_target_prospects

        call_count = [0]

        async def execute_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            # First 5 are duplicates, rest are new
            if call_count[0] <= 5:
                result.scalar_one_or_none.return_value = uuid.uuid4()
            else:
                result.scalar_one_or_none.return_value = None
            return result

        mock_session.execute = AsyncMock(side_effect=execute_side_effect)

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await seed_target_prospects(
                auto_process=False, tenant_id=tenant_id
            )

        assert result["seeded"] == 15
        assert result["skipped_duplicates"] == 5

    @pytest.mark.asyncio
    async def test_seed_all_duplicates(self, mock_session, tenant_id):
        from api.v1.sales import seed_target_prospects

        exists_result = MagicMock()
        exists_result.scalar_one_or_none.return_value = uuid.uuid4()
        mock_session.execute = AsyncMock(return_value=exists_result)

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await seed_target_prospects(
                auto_process=False, tenant_id=tenant_id
            )

        assert result["seeded"] == 0
        assert result["skipped_duplicates"] == 20


# ═══════════════════════════════════════════════════════════════════════════════
# sales.py — import_leads_csv
# ═══════════════════════════════════════════════════════════════════════════════


class TestImportLeadsCsv:
    @pytest.mark.asyncio
    async def test_import_leads_basic(self, mock_session, tenant_id):
        from api.v1.sales import import_leads_csv

        csv_content = (
            "name,email,company,role,phone\n"
            "Amit Sharma,amit@test.com,TestCo,VP,+91-123\n"
            "Priya Mehta,priya@test.com,OtherCo,CFO,+91-456\n"
        )
        file = MagicMock()
        file.read = AsyncMock(return_value=csv_content.encode("utf-8"))

        # No duplicates
        exists_result = MagicMock()
        exists_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=exists_result)

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await import_leads_csv(
                file=file, auto_process=False, tenant_id=tenant_id
            )

        assert result["imported"] == 2
        assert result["skipped"] == 0

    @pytest.mark.asyncio
    async def test_import_leads_missing_name_or_email(self, mock_session, tenant_id):
        from api.v1.sales import import_leads_csv

        csv_content = (
            "name,email,company\n"
            ",missing@test.com,X\n"
            "NoEmail,,Y\n"
        )
        file = MagicMock()
        file.read = AsyncMock(return_value=csv_content.encode("utf-8"))

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await import_leads_csv(
                file=file, auto_process=False, tenant_id=tenant_id
            )

        assert result["imported"] == 0
        assert result["skipped"] == 2

    @pytest.mark.asyncio
    async def test_import_leads_duplicate_detection(self, mock_session, tenant_id):
        from api.v1.sales import import_leads_csv

        csv_content = (
            "name,email,company\n"
            "Dup Lead,dup@test.com,Co\n"
        )
        file = MagicMock()
        file.read = AsyncMock(return_value=csv_content.encode("utf-8"))

        exists_result = MagicMock()
        exists_result.scalar_one_or_none.return_value = uuid.uuid4()  # exists
        mock_session.execute = AsyncMock(return_value=exists_result)

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await import_leads_csv(
                file=file, auto_process=False, tenant_id=tenant_id
            )

        assert result["imported"] == 0
        assert result["skipped"] == 1
        assert result["skip_details"][0]["reason"] == "duplicate email"

    @pytest.mark.asyncio
    async def test_import_leads_bom_handling(self, mock_session, tenant_id):
        from api.v1.sales import import_leads_csv

        # Encode with utf-8-sig to add a BOM prefix (no \ufeff in the string itself)
        csv_content = "name,email,company\nBOM Lead,bom@test.com,BomCo\n"
        file = MagicMock()
        file.read = AsyncMock(return_value=csv_content.encode("utf-8-sig"))

        exists_result = MagicMock()
        exists_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=exists_result)

        with patch("api.v1.sales.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await import_leads_csv(
                file=file, auto_process=False, tenant_id=tenant_id
            )

        assert result["imported"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# agents.py — rollback_agent
# ═══════════════════════════════════════════════════════════════════════════════


class TestRollbackAgent:
    @pytest.mark.asyncio
    async def test_rollback_success(self, mock_session, tenant_id):
        from api.v1.agents import rollback_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, version="2.0.0", status="active")

        prev_version = MagicMock()
        prev_version.version = "1.0.0"
        prev_version.system_prompt = "old_prompt"
        prev_version.authorized_tools = ["tool_a"]
        prev_version.hitl_policy = {"condition": "confidence < 0.9"}
        prev_version.llm_config = {"model": "gemini-2.5-flash"}
        prev_version.confidence_floor = Decimal("0.88")

        agent_result = MagicMock()
        agent_result.scalar_one_or_none.return_value = agent

        version_result = MagicMock()
        version_result.scalar_one_or_none.return_value = prev_version

        mock_session.execute = AsyncMock(
            side_effect=[agent_result, version_result]
        )

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await rollback_agent(agent_id=aid, tenant_id=tenant_id)

        assert result["rolled_back"] is True
        assert result["from_version"] == "2.0.0"
        assert result["to_version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_rollback_no_previous_version(self, mock_session, tenant_id):
        from api.v1.agents import rollback_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid, version="1.0.0")

        agent_result = MagicMock()
        agent_result.scalar_one_or_none.return_value = agent

        version_result = MagicMock()
        version_result.scalar_one_or_none.return_value = None

        mock_session.execute = AsyncMock(
            side_effect=[agent_result, version_result]
        )

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await rollback_agent(agent_id=aid, tenant_id=tenant_id)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_rollback_agent_not_found(self, mock_session, tenant_id):
        from api.v1.agents import rollback_agent

        agent_result = MagicMock()
        agent_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=agent_result)

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await rollback_agent(
                    agent_id=uuid.uuid4(), tenant_id=tenant_id
                )

        assert exc_info.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# sales.py — process_lead_with_agent (POST /sales/pipeline/process-lead)
# ═══════════════════════════════════════════════════════════════════════════════


class TestProcessLeadWithAgent:
    @pytest.mark.asyncio
    async def test_missing_lead_id(self, tenant_id):
        from api.v1.sales import process_lead_with_agent

        with pytest.raises(HTTPException) as exc_info:
            await process_lead_with_agent(payload={}, tenant_id=tenant_id)

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_none_payload(self, tenant_id):
        from api.v1.sales import process_lead_with_agent

        with pytest.raises(HTTPException) as exc_info:
            await process_lead_with_agent(payload=None, tenant_id=tenant_id)

        assert exc_info.value.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# agents.py — replace_agent (PUT /agents/{id})
# ═══════════════════════════════════════════════════════════════════════════════


class TestReplaceAgent:
    @pytest.mark.asyncio
    async def test_replace_agent_success(self, mock_session, tenant_id):
        from api.v1.agents import replace_agent

        aid = uuid.uuid4()
        agent = make_mock_agent(id=aid)
        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = agent
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = MagicMock()
        body.name = "Replaced"
        body.agent_type = "new_type"
        body.domain = "hr"
        body.system_prompt = "new_ref"
        body.prompt_variables = {"k": "v"}
        body.llm = MagicMock()
        body.llm.model = "gemini-2.5-pro"
        body.llm.fallback_model = "gemini-2.5-flash"
        body.llm.model_dump.return_value = {"model": "gemini-2.5-pro"}
        body.confidence_floor = 0.90
        body.hitl_policy = MagicMock()
        body.hitl_policy.condition = "confidence < 0.90"
        body.max_retries = 5
        body.authorized_tools = ["tool_x"]
        body.output_schema = None
        body.shadow_min_samples = 20
        body.shadow_accuracy_floor = 0.96
        body.cost_controls = MagicMock()
        body.cost_controls.model_dump.return_value = {}
        body.scaling = MagicMock()
        body.scaling.model_dump.return_value = {}
        body.ttl_hours = 48
        body.shadow_comparison_agent = None

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            result = await replace_agent(
                agent_id=aid, body=body, tenant_id=tenant_id
            )

        assert result["replaced"] is True
        assert agent.name == "Replaced"
        assert agent.agent_type == "new_type"
        assert agent.max_retries == 5

    @pytest.mark.asyncio
    async def test_replace_agent_not_found(self, mock_session, tenant_id):
        from api.v1.agents import replace_agent

        exec_result = MagicMock()
        exec_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=exec_result)

        body = MagicMock()

        with patch("api.v1.agents.get_tenant_session") as mock_gts:
            mock_gts.return_value = _make_tenant_session_ctx(mock_session)
            with pytest.raises(HTTPException) as exc_info:
                await replace_agent(
                    agent_id=uuid.uuid4(), body=body, tenant_id=tenant_id
                )

        assert exc_info.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# Additional edge-case tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentLifecycleFSM:
    """Test the lifecycle FSM constants are correctly defined."""

    def test_shadow_can_transition(self):
        from api.v1.agents import _LIFECYCLE_FSM

        assert "active" in _LIFECYCLE_FSM["shadow"]
        assert "paused" in _LIFECYCLE_FSM["shadow"]
        assert "retired" in _LIFECYCLE_FSM["shadow"]

    def test_retired_is_terminal(self):
        from api.v1.agents import _LIFECYCLE_FSM

        assert _LIFECYCLE_FSM["retired"] == []

    def test_promote_map(self):
        from api.v1.agents import _PROMOTE_MAP

        assert _PROMOTE_MAP["shadow"] == "active"
        assert "active" not in _PROMOTE_MAP


class TestTargetProspectsConstant:
    """Verify the TARGET_PROSPECTS list is well-formed."""

    def test_has_20_prospects(self):
        from api.v1.sales import TARGET_PROSPECTS

        assert len(TARGET_PROSPECTS) == 20

    def test_all_have_required_fields(self):
        from api.v1.sales import TARGET_PROSPECTS

        for p in TARGET_PROSPECTS:
            assert "name" in p
            assert "email" in p
            assert "company" in p
            assert "role" in p

    def test_emails_unique(self):
        from api.v1.sales import TARGET_PROSPECTS

        emails = [p["email"] for p in TARGET_PROSPECTS]
        assert len(emails) == len(set(emails))


class TestFollowupSchedule:
    def test_schedule_has_5_steps(self):
        from api.v1.sales import FOLLOWUP_SCHEDULE

        assert len(FOLLOWUP_SCHEDULE) == 5

    def test_schedule_monotonically_increasing(self):
        from api.v1.sales import FOLLOWUP_SCHEDULE

        prev = -1
        for step in sorted(FOLLOWUP_SCHEDULE.keys()):
            assert FOLLOWUP_SCHEDULE[step] >= prev
            prev = FOLLOWUP_SCHEDULE[step]
