"""Test new CFO and CMO agents -- instantiation, prompts, tools, HITL.

Covers all 8 new agents from Phase 2:
- Finance: treasury, expense_manager, rev_rec, fixed_assets
- Marketing: email_marketing, social_media, abm, competitive_intel
"""

from __future__ import annotations

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# Agent specification table — single source of truth for expected values
# ═══════════════════════════════════════════════════════════════════════════

_AGENT_SPECS = {
    "treasury": {
        "module": "core.langgraph.agents.treasury_agent",
        "domain": "finance",
        "confidence_floor": 0.90,
        "expected_tools": [
            "check_account_balance", "fetch_bank_statement", "get_balance",
            "get_balance_sheet", "get_cash_position",
        ],
        "tools_var": "DEFAULT_TOOLS",
        "prompt_keywords": ["Treasury", "HITL"],
    },
    "expense_manager": {
        "module": "core.langgraph.agents.expense_manager",
        "domain": "finance",
        "confidence_floor": 0.85,
        "expected_tools": [
            "record_expense", "create_ap_invoice", "check_order_status",
            "list_invoices", "get_profit_loss",
        ],
        "tools_var": "DEFAULT_TOOLS",
        "prompt_keywords": ["Expense", "HITL"],
    },
    "rev_rec": {
        "module": "core.langgraph.agents.rev_rec_agent",
        "domain": "finance",
        "confidence_floor": 0.92,
        "expected_tools": [
            "query", "create_invoice", "post_journal_entry",
            "get_trial_balance", "list_invoices",
        ],
        "tools_var": "DEFAULT_TOOLS",
        "prompt_keywords": ["Revenue", "HITL"],
    },
    "fixed_assets": {
        "module": "core.langgraph.agents.fixed_assets_agent",
        "domain": "finance",
        "confidence_floor": 0.88,
        "expected_tools": [
            "post_journal_entry", "record_expense", "get_trial_balance",
            "get_balance_sheet", "create_ap_invoice",
        ],
        "tools_var": "DEFAULT_TOOLS",
        "prompt_keywords": ["Fixed Asset", "HITL"],
    },
    "email_marketing": {
        "module": "core.langgraph.agents.email_marketing",
        "domain": "marketing",
        "confidence_floor": 0.85,
        "expected_tools": [
            "send_email", "create_campaign", "send_campaign",
            "get_campaign_report", "add_list_member", "get_campaign_stats",
        ],
        "tools_var": "DEFAULT_TOOLS",
        "prompt_keywords": ["Email", "HITL"],
    },
    "social_media": {
        "module": "core.langgraph.agents.social_media",
        "domain": "marketing",
        "confidence_floor": 0.85,
        "expected_tools": [
            "create_tweet", "create_update", "get_post_analytics",
            "list_channel_videos", "get_campaign_insights",
        ],
        "tools_var": "DEFAULT_TOOLS",
        "prompt_keywords": ["Social Media", "HITL"],
    },
    "abm": {
        "module": "core.langgraph.agents.abm_agent",
        "domain": "marketing",
        "confidence_floor": 0.88,
        "expected_tools": [
            "query", "search_contacts", "get_analytics",
            "get_campaign_performance", "create_campaign",
        ],
        "tools_var": "DEFAULT_TOOLS",
        "prompt_keywords": ["ABM", "HITL"],
    },
    "competitive_intel": {
        "module": "core.langgraph.agents.competitive_intel",
        "domain": "marketing",
        "confidence_floor": 0.82,
        "expected_tools": [
            "get_domain_rating", "get_organic_keywords", "get_mentions",
            "get_share_of_voice", "get_backlinks",
        ],
        "tools_var": "DEFAULT_TOOLS",
        "prompt_keywords": ["Competitive", "HITL"],
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# 1. Module import tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentModuleImports:
    """Verify each new agent module imports without error."""

    @pytest.mark.parametrize("agent_type,spec", list(_AGENT_SPECS.items()))
    def test_module_imports(self, agent_type, spec):
        import importlib
        mod = importlib.import_module(spec["module"])
        assert hasattr(mod, "DEFAULT_TOOLS")
        assert hasattr(mod, "load_prompt")
        assert hasattr(mod, "build_graph")
        assert hasattr(mod, "run")


# ═══════════════════════════════════════════════════════════════════════════
# 2. DEFAULT_TOOLS tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentDefaultTools:
    """Verify DEFAULT_TOOLS list is non-empty and has expected tools."""

    @pytest.mark.parametrize("agent_type,spec", list(_AGENT_SPECS.items()))
    def test_default_tools_non_empty(self, agent_type, spec):
        import importlib
        mod = importlib.import_module(spec["module"])
        tools = getattr(mod, spec["tools_var"])
        assert isinstance(tools, list)
        assert len(tools) > 0

    @pytest.mark.parametrize("agent_type,spec", list(_AGENT_SPECS.items()))
    def test_default_tools_match_spec(self, agent_type, spec):
        import importlib
        mod = importlib.import_module(spec["module"])
        tools = getattr(mod, spec["tools_var"])
        for expected_tool in spec["expected_tools"]:
            assert expected_tool in tools, f"{agent_type}: missing tool {expected_tool}"

    @pytest.mark.parametrize("agent_type,spec", list(_AGENT_SPECS.items()))
    def test_default_tools_no_duplicates(self, agent_type, spec):
        import importlib
        mod = importlib.import_module(spec["module"])
        tools = getattr(mod, spec["tools_var"])
        assert len(tools) == len(set(tools)), f"{agent_type}: duplicate tools found"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Prompt loading tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentPromptLoading:
    """Verify load_prompt() returns a string containing key sections."""

    @pytest.mark.parametrize("agent_type,spec", list(_AGENT_SPECS.items()))
    def test_load_prompt_returns_string(self, agent_type, spec):
        import importlib
        mod = importlib.import_module(spec["module"])
        prompt = mod.load_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 50  # Non-trivial prompt

    @pytest.mark.parametrize("agent_type,spec", list(_AGENT_SPECS.items()))
    def test_prompt_contains_hitl_section(self, agent_type, spec):
        import importlib
        mod = importlib.import_module(spec["module"])
        prompt = mod.load_prompt()
        prompt_lower = prompt.lower()
        assert "hitl" in prompt_lower or "human" in prompt_lower, (
            f"{agent_type}: prompt missing HITL section"
        )

    @pytest.mark.parametrize("agent_type,spec", list(_AGENT_SPECS.items()))
    def test_prompt_contains_role_keyword(self, agent_type, spec):
        import importlib
        mod = importlib.import_module(spec["module"])
        prompt = mod.load_prompt()
        # Check for at least one of the expected keywords
        found = any(kw.lower() in prompt.lower() for kw in spec["prompt_keywords"])
        assert found, f"{agent_type}: prompt missing expected keywords {spec['prompt_keywords']}"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Build graph tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentBuildGraph:
    """Verify build_graph() returns a valid graph object."""

    @pytest.mark.parametrize("agent_type,spec", list(_AGENT_SPECS.items()))
    def test_build_graph_returns_graph(self, agent_type, spec):
        import importlib
        mod = importlib.import_module(spec["module"])
        graph = mod.build_graph()
        compiled = graph.compile()
        nodes = list(compiled.get_graph().nodes.keys())
        assert "reason" in nodes
        assert "evaluate" in nodes

    @pytest.mark.parametrize("agent_type,spec", list(_AGENT_SPECS.items()))
    def test_build_graph_has_execute_tools_node(self, agent_type, spec):
        import importlib
        mod = importlib.import_module(spec["module"])
        graph = mod.build_graph()
        compiled = graph.compile()
        nodes = list(compiled.get_graph().nodes.keys())
        # All agents have tools, so execute_tools should exist
        assert "execute_tools" in nodes


# ═══════════════════════════════════════════════════════════════════════════
# 5. Confidence floor tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentConfidenceFloor:
    """Verify each agent's confidence_floor matches the design spec."""

    @pytest.mark.parametrize("agent_type,spec", list(_AGENT_SPECS.items()))
    def test_confidence_floor_matches_spec(self, agent_type, spec):
        import importlib
        import inspect
        mod = importlib.import_module(spec["module"])
        # Extract default confidence_floor from build_graph signature
        sig = inspect.signature(mod.build_graph)
        floor_param = sig.parameters.get("confidence_floor")
        assert floor_param is not None, f"{agent_type}: build_graph missing confidence_floor param"
        assert floor_param.default == spec["confidence_floor"], (
            f"{agent_type}: expected confidence_floor={spec['confidence_floor']}, "
            f"got {floor_param.default}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 6. Agent registration in _AGENT_TYPE_DEFAULT_TOOLS
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentRegistration:
    """Verify each agent type is registered in the API layer."""

    @pytest.mark.parametrize("agent_type,spec", list(_AGENT_SPECS.items()))
    def test_registered_in_agent_type_default_tools(self, agent_type, spec):
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS
        assert agent_type in _AGENT_TYPE_DEFAULT_TOOLS, (
            f"{agent_type} not in _AGENT_TYPE_DEFAULT_TOOLS"
        )
        registered_tools = _AGENT_TYPE_DEFAULT_TOOLS[agent_type]
        assert len(registered_tools) > 0

    @pytest.mark.parametrize("agent_type,spec", list(_AGENT_SPECS.items()))
    def test_registered_in_domain_map(self, agent_type, spec):
        from api.v1.a2a import _DOMAIN_MAP
        assert agent_type in _DOMAIN_MAP, (
            f"{agent_type} not in _DOMAIN_MAP"
        )
        assert _DOMAIN_MAP[agent_type] == spec["domain"], (
            f"{agent_type}: expected domain={spec['domain']}, "
            f"got {_DOMAIN_MAP[agent_type]}"
        )
