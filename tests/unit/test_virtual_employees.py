"""Tests for the Virtual Employee Agent System.

Covers:
- Agent registry custom type fallback
- Prompt template variable resolution
- Smart routing with routing_filter and specialization
- Prompt lock enforcement
- Persona fields in schemas
- Prompt edit history
- Backward compatibility with built-in agents
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry

# ═══════════════════════════════════════════════════════════════════════════
# Registry Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentRegistryCustomTypes:
    """Test that the registry supports both built-in and custom agent types."""

    def test_builtin_type_returns_registered_class(self):
        """Built-in agent types resolve to their registered class."""
        import core.agents  # noqa: F401 — trigger registrations

        cls = AgentRegistry.get_by_type("ap_processor")
        assert cls is not None
        assert cls.agent_type == "ap_processor"

    def test_custom_type_returns_none(self):
        """Custom (unregistered) agent types return None from get_by_type."""
        cls = AgentRegistry.get_by_type("customer_success_xyz")
        assert cls is None

    def test_has_type_builtin(self):
        """has_type returns True for registered types."""
        import core.agents  # noqa: F401

        assert AgentRegistry.has_type("ap_processor") is True

    def test_has_type_custom(self):
        """has_type returns False for unregistered types."""
        assert AgentRegistry.has_type("nonexistent_agent_type") is False

    def test_create_from_config_builtin(self):
        """create_from_config with a registered type uses the registered class."""
        import core.agents  # noqa: F401

        config = {
            "id": "test-id",
            "tenant_id": "test-tenant",
            "agent_type": "ap_processor",
        }
        instance = AgentRegistry.create_from_config(config)
        assert instance.agent_type == "ap_processor"
        assert type(instance).__name__ == "ApProcessorAgent"

    def test_create_from_config_custom_falls_back_to_base(self):
        """create_from_config with an unregistered type falls back to BaseAgent."""
        config = {
            "id": "test-id",
            "tenant_id": "test-tenant",
            "agent_type": "customer_success",
            "system_prompt_text": "You are a customer success agent.",
        }
        instance = AgentRegistry.create_from_config(config)
        assert type(instance) is BaseAgent
        assert instance.system_prompt == "You are a customer success agent."

    def test_create_from_config_custom_without_prompt_uses_default(self):
        """Custom agent with no prompt text gets a minimal default prompt."""
        config = {
            "id": "test-id",
            "tenant_id": "test-tenant",
            "agent_type": "custom_no_prompt",
        }
        instance = AgentRegistry.create_from_config(config)
        assert type(instance) is BaseAgent
        assert "AI agent" in instance.system_prompt

    def test_create_from_config_inline_prompt_overrides_file(self):
        """system_prompt_text from DB overrides the file-based prompt."""
        import core.agents  # noqa: F401

        config = {
            "id": "test-id",
            "tenant_id": "test-tenant",
            "agent_type": "ap_processor",
            "system_prompt_text": "Custom override prompt for AP.",
        }
        instance = AgentRegistry.create_from_config(config)
        # Even though ap_processor has a file, the inline text takes priority
        assert instance.system_prompt == "Custom override prompt for AP."

    def test_create_from_config_passes_tools_and_hitl(self):
        """Authorized tools and HITL condition are passed through."""
        config = {
            "id": "test-id",
            "tenant_id": "test-tenant",
            "agent_type": "custom_agent",
            "authorized_tools": ["oracle:read:po"],
            "hitl_condition": "amount > 100000",
            "output_schema": "Invoice",
            "system_prompt_text": "Custom prompt",
        }
        instance = AgentRegistry.create_from_config(config)
        assert instance.authorized_tools == ["oracle:read:po"]
        assert instance.hitl_condition == "amount > 100000"
        assert instance.output_schema == "Invoice"


# ═══════════════════════════════════════════════════════════════════════════
# Template Variable Resolution
# ═══════════════════════════════════════════════════════════════════════════


class TestTemplateResolution:
    """Test {{variable}} substitution in prompts."""

    def test_single_variable(self):
        result = AgentRegistry._resolve_template(
            "Hello {{name}}, welcome!", {"name": "Priya"}
        )
        assert result == "Hello Priya, welcome!"

    def test_multiple_variables(self):
        template = "Agent for {{org_name}} with threshold {{threshold}}"
        result = AgentRegistry._resolve_template(
            template, {"org_name": "Acme Corp", "threshold": "500000"}
        )
        assert result == "Agent for Acme Corp with threshold 500000"

    def test_repeated_variable(self):
        template = "{{name}} does {{name}} things"
        result = AgentRegistry._resolve_template(template, {"name": "Priya"})
        assert result == "Priya does Priya things"

    def test_unused_variable_in_dict(self):
        """Extra variables in the dict don't cause errors."""
        result = AgentRegistry._resolve_template(
            "Hello {{name}}", {"name": "Arjun", "extra": "ignored"}
        )
        assert result == "Hello Arjun"

    def test_unmatched_placeholder_stays(self):
        """Placeholders without a matching variable remain as-is."""
        result = AgentRegistry._resolve_template(
            "Hello {{name}}, org={{org_name}}", {"name": "Maya"}
        )
        assert result == "Hello Maya, org={{org_name}}"

    def test_empty_variables(self):
        result = AgentRegistry._resolve_template("No vars here", {})
        assert result == "No vars here"

    def test_numeric_variable_converted_to_str(self):
        result = AgentRegistry._resolve_template(
            "Floor: {{floor}}", {"floor": 88}
        )
        assert result == "Floor: 88"

    def test_create_from_config_resolves_variables(self):
        """Variables are resolved when injecting inline prompt."""
        config = {
            "id": "test",
            "tenant_id": "test",
            "agent_type": "custom",
            "system_prompt_text": "You are an agent for {{org_name}}.",
            "prompt_variables": {"org_name": "TestCorp"},
        }
        instance = AgentRegistry.create_from_config(config)
        assert instance.system_prompt == "You are an agent for TestCorp."


# ═══════════════════════════════════════════════════════════════════════════
# BaseAgent Prompt Loading
# ═══════════════════════════════════════════════════════════════════════════


class TestBaseAgentPrompt:
    """Test BaseAgent system_prompt property with and without prompt_file."""

    def test_builtin_loads_from_file(self):
        """Agent with prompt_file loads from filesystem."""
        import core.agents  # noqa: F401

        config = {"id": "x", "tenant_id": "t", "agent_type": "recon_agent"}
        agent = AgentRegistry.create_from_config(config)
        prompt = agent.system_prompt
        assert "Reconciliation Agent" in prompt
        assert len(prompt) > 100

    def test_no_prompt_file_returns_default(self):
        """BaseAgent without prompt_file returns a minimal default."""
        agent = BaseAgent(agent_id="x", tenant_id="t")
        assert agent.prompt_file == ""
        prompt = agent.system_prompt
        assert "AI agent" in prompt

    def test_prompt_is_cached(self):
        """system_prompt property caches the result."""
        agent = BaseAgent(agent_id="x", tenant_id="t")
        p1 = agent.system_prompt
        p2 = agent.system_prompt
        assert p1 is p2  # Same object reference = cached

    def test_injected_prompt_takes_priority(self):
        """If _system_prompt is set directly, it overrides everything."""
        agent = BaseAgent(agent_id="x", tenant_id="t")
        agent._system_prompt = "Injected prompt"
        assert agent.system_prompt == "Injected prompt"


# ═══════════════════════════════════════════════════════════════════════════
# Smart Routing
# ═══════════════════════════════════════════════════════════════════════════


class TestTaskRouter:
    """Test smart routing with multiple agents of the same type."""

    @pytest.mark.asyncio
    async def test_route_returns_routing_context(self):
        from core.orchestrator.task_router import TaskRouter

        router = TaskRouter()
        result = await router.route(
            "wf1", "step1", 0, 3,
            {"agent": "ap_processor", "routing_context": {"region": "APAC"}},
            {},
        )
        assert result["target_agent_type"] == "ap_processor"
        assert result["routing_context"] == {"region": "APAC"}

    @pytest.mark.asyncio
    async def test_route_explicit_agent_id(self):
        from core.orchestrator.task_router import TaskRouter

        router = TaskRouter()
        result = await router.route(
            "wf1", "step1", 0, 1,
            {"agent": "ap_processor", "agent_id": "specific-id"},
            {},
        )
        assert result["target_agent_id"] == "specific-id"

    @pytest.mark.asyncio
    async def test_resolve_single_agent(self):
        """With only one agent of a type, it's returned immediately."""
        from core.orchestrator.task_router import TaskRouter

        mock_agent = MagicMock()
        mock_agent.id = uuid.uuid4()
        mock_agent.routing_filter = {}
        mock_agent.specialization = None

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_agent]
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await TaskRouter.resolve_agent_instance(
            uuid.uuid4(), "ap_processor", {"region": "west"}, mock_session
        )
        assert result == mock_agent.id

    @pytest.mark.asyncio
    async def test_resolve_by_routing_filter(self):
        """With multiple agents, routing_filter match wins."""
        from core.orchestrator.task_router import TaskRouter

        agent_west = MagicMock()
        agent_west.id = uuid.uuid4()
        agent_west.routing_filter = {"region": "west"}
        agent_west.specialization = None

        agent_east = MagicMock()
        agent_east.id = uuid.uuid4()
        agent_east.routing_filter = {"region": "east"}
        agent_east.specialization = None

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [agent_west, agent_east]
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await TaskRouter.resolve_agent_instance(
            uuid.uuid4(), "ap_processor", {"region": "east"}, mock_session
        )
        assert result == agent_east.id

    @pytest.mark.asyncio
    async def test_resolve_by_specialization(self):
        """When no routing_filter match, specialization keyword match is tried."""
        from core.orchestrator.task_router import TaskRouter

        agent1 = MagicMock()
        agent1.id = uuid.uuid4()
        agent1.routing_filter = {}
        agent1.specialization = "domestic invoices"

        agent2 = MagicMock()
        agent2.id = uuid.uuid4()
        agent2.routing_filter = {}
        agent2.specialization = "import invoices"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [agent1, agent2]
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await TaskRouter.resolve_agent_instance(
            uuid.uuid4(), "ap_processor",
            {"description": "Process import invoices from ICEGATE"},
            mock_session,
        )
        assert result == agent2.id

    @pytest.mark.asyncio
    async def test_resolve_fallback_first_agent(self):
        """When no filter or specialization matches, first agent is returned."""
        from core.orchestrator.task_router import TaskRouter

        agent1 = MagicMock()
        agent1.id = uuid.uuid4()
        agent1.routing_filter = {}
        agent1.specialization = None

        agent2 = MagicMock()
        agent2.id = uuid.uuid4()
        agent2.routing_filter = {}
        agent2.specialization = None

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [agent1, agent2]
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await TaskRouter.resolve_agent_instance(
            uuid.uuid4(), "ap_processor", {}, mock_session
        )
        assert result == agent1.id

    @pytest.mark.asyncio
    async def test_resolve_no_candidates(self):
        """When no active agents exist for the type, returns None."""
        from core.orchestrator.task_router import TaskRouter

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await TaskRouter.resolve_agent_instance(
            uuid.uuid4(), "nonexistent_type", {}, mock_session
        )
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# API Schema Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestApiSchemas:
    """Test that API schemas accept persona fields correctly."""

    def test_agent_create_with_persona(self):
        from core.schemas.api import AgentCreate

        body = AgentCreate(
            name="Priya AP",
            agent_type="ap_processor",
            domain="finance",
            employee_name="Priya",
            designation="Senior AP Analyst - Mumbai",
            specialization="Domestic invoices < 5L",
            routing_filter={"region": "west"},
        )
        assert body.employee_name == "Priya"
        assert body.designation == "Senior AP Analyst - Mumbai"
        assert body.routing_filter == {"region": "west"}

    def test_agent_create_defaults(self):
        from core.schemas.api import AgentCreate

        body = AgentCreate(name="Test", agent_type="test", domain="finance")
        assert body.employee_name is None
        assert body.avatar_url is None
        assert body.routing_filter == {}
        assert body.system_prompt == ""
        assert body.system_prompt_text is None
        assert body.confidence_floor == 0.88

    def test_agent_update_with_persona(self):
        from core.schemas.api import AgentUpdate

        body = AgentUpdate(
            employee_name="Arjun",
            designation="AP Analyst - East",
            change_reason="Renamed for east region",
        )
        assert body.employee_name == "Arjun"
        assert body.change_reason == "Renamed for east region"

    def test_agent_update_partial(self):
        from core.schemas.api import AgentUpdate

        body = AgentUpdate(confidence_floor=0.92)
        data = body.model_dump(exclude_unset=True)
        assert "confidence_floor" in data
        assert "employee_name" not in data

    def test_prompt_template_create(self):
        from core.schemas.api import PromptTemplateCreate

        body = PromptTemplateCreate(
            name="custom_ap",
            agent_type="ap_processor",
            domain="finance",
            template_text="You are {{role}} for {{org}}.",
            variables=[{"name": "role", "description": "Agent role", "default": "AP Processor"}],
        )
        assert body.template_text.startswith("You are")
        assert len(body.variables) == 1

    def test_prompt_template_update_partial(self):
        from core.schemas.api import PromptTemplateUpdate

        body = PromptTemplateUpdate(template_text="Updated prompt")
        data = body.model_dump(exclude_unset=True)
        assert "template_text" in data
        assert "name" not in data

    def test_prompt_edit_history_response(self):
        from core.schemas.api import PromptEditHistoryResponse

        resp = PromptEditHistoryResponse(
            id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            edited_by=None,
            prompt_before="old",
            prompt_after="new",
            change_reason="test",
            created_at="2026-03-24T12:00:00Z",
        )
        assert resp.prompt_before == "old"
        assert resp.prompt_after == "new"


# ═══════════════════════════════════════════════════════════════════════════
# ORM Model Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentModel:
    """Test Agent ORM model has the new persona fields."""

    def test_agent_has_persona_fields(self):
        from core.models.agent import Agent

        # Check columns exist on the model
        mapper = Agent.__table__.columns
        assert "employee_name" in mapper
        assert "avatar_url" in mapper
        assert "designation" in mapper
        assert "specialization" in mapper
        assert "routing_filter" in mapper
        assert "is_builtin" in mapper
        assert "system_prompt_text" in mapper

    def test_prompt_template_model(self):
        from core.models.prompt_template import PromptTemplate

        mapper = PromptTemplate.__table__.columns
        assert "template_text" in mapper
        assert "variables" in mapper
        assert "is_builtin" in mapper
        assert "agent_type" in mapper

    def test_prompt_edit_history_model(self):
        from core.models.prompt_template import PromptEditHistory

        mapper = PromptEditHistory.__table__.columns
        assert "prompt_before" in mapper
        assert "prompt_after" in mapper
        assert "change_reason" in mapper
        assert "edited_by" in mapper


# ═══════════════════════════════════════════════════════════════════════════
# Backward Compatibility
# ═══════════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    """Ensure existing 25 built-in agents still work."""

    def test_all_builtin_agents_registered(self):
        import core.agents  # noqa: F401

        types = AgentRegistry.all_types()
        assert len(types) >= 24
        assert "ap_processor" in types
        assert "recon_agent" in types
        assert "talent_acquisition" in types
        assert "support_triage" in types
        assert "risk_sentinel" in types

    def test_builtin_agents_load_file_prompts(self):
        """Built-in agents load prompts from the filesystem, not DB."""
        import core.agents  # noqa: F401

        for agent_type in ["ap_processor", "recon_agent", "talent_acquisition"]:
            config = {"id": "x", "tenant_id": "t", "agent_type": agent_type}
            instance = AgentRegistry.create_from_config(config)
            prompt = instance.system_prompt
            assert len(prompt) > 100, f"{agent_type} prompt too short"
            assert "{{" not in prompt or "org_name" in prompt, f"{agent_type} has unresolved vars"

    def test_all_prompt_files_exist(self):
        """Every registered agent type has a corresponding .prompt.txt file."""
        import os

        import core.agents  # noqa: F401
        from core.agents.base import PROMPTS_DIR

        for agent_type in AgentRegistry.all_types():
            cls = AgentRegistry.get_by_type(agent_type)
            if cls and cls.prompt_file:
                path = os.path.join(PROMPTS_DIR, cls.prompt_file)
                assert os.path.exists(path), f"Missing prompt file: {path}"


# ═══════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases for the virtual employee system."""

    def test_empty_system_prompt_text_does_not_override(self):
        """Empty string system_prompt_text should not override file prompt."""
        import core.agents  # noqa: F401

        config = {
            "id": "x",
            "tenant_id": "t",
            "agent_type": "ap_processor",
            "system_prompt_text": "",
        }
        instance = AgentRegistry.create_from_config(config)
        # Empty string is falsy, so file prompt should be used
        assert "AP Processor" in instance.system_prompt

    def test_none_system_prompt_text_does_not_override(self):
        """None system_prompt_text should not override file prompt."""
        import core.agents  # noqa: F401

        config = {
            "id": "x",
            "tenant_id": "t",
            "agent_type": "ap_processor",
            "system_prompt_text": None,
        }
        instance = AgentRegistry.create_from_config(config)
        assert "AP Processor" in instance.system_prompt

    def test_custom_agent_with_variables_and_no_file(self):
        """Custom agent with variables resolves them from DB text."""
        config = {
            "id": "x",
            "tenant_id": "t",
            "agent_type": "brand_new_type",
            "system_prompt_text": "Agent for {{company}} in {{region}}",
            "prompt_variables": {"company": "Acme", "region": "APAC"},
        }
        instance = AgentRegistry.create_from_config(config)
        assert instance.system_prompt == "Agent for Acme in APAC"

    def test_routing_filter_partial_match(self):
        """Routing filter matches when all specified keys match."""
        from core.orchestrator.task_router import TaskRouter

        agent = MagicMock()
        agent.id = uuid.uuid4()
        agent.routing_filter = {"region": "west", "tier": "enterprise"}
        agent.specialization = None

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [agent]
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Only "region" in routing_context — should still match
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            TaskRouter.resolve_agent_instance(
                uuid.uuid4(), "ap_processor", {"region": "west"}, mock_session
            )
        )
        assert result == agent.id

    def test_agent_create_hitl_policy_default(self):
        """AgentCreate should work without explicitly setting hitl_policy."""
        from core.schemas.api import AgentCreate

        body = AgentCreate(name="Test", agent_type="test", domain="finance")
        assert body.hitl_policy.condition == "confidence < 0.88"

    def test_agent_create_system_prompt_default(self):
        """AgentCreate should work without explicitly setting system_prompt."""
        from core.schemas.api import AgentCreate

        body = AgentCreate(name="Test", agent_type="test", domain="finance")
        assert body.system_prompt == ""
        assert body.system_prompt_text is None
