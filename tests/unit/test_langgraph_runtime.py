"""Tests for the LangGraph agent runtime.

Covers: state, llm_factory, tool_adapter, agent_graph, runner, ap_processor.
"""

from __future__ import annotations

from unittest.mock import patch

# ═══════════════════════════════════════════════════════════════════════════
# State
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentState:
    def test_state_has_all_fields(self):
        from core.langgraph.state import AgentState

        fields = list(AgentState.__annotations__.keys())
        assert "messages" in fields
        assert "agent_id" in fields
        assert "confidence" in fields
        assert "grant_token" in fields
        assert "hitl_trigger" in fields
        assert "output" in fields
        assert len(fields) == 13


# ═══════════════════════════════════════════════════════════════════════════
# LLM Factory
# ═══════════════════════════════════════════════════════════════════════════


class TestLLMFactory:
    def test_resolve_model_default(self):
        from core.langgraph.llm_factory import _resolve_model

        with patch.dict("os.environ", {"AGENTICORG_LLM_PRIMARY": "gemini-2.5-flash"}):
            assert _resolve_model("") == "gemini-2.5-flash"

    def test_resolve_model_gemini_passthrough(self):
        from core.langgraph.llm_factory import _resolve_model

        assert _resolve_model("gemini-2.5-pro") == "gemini-2.5-pro"

    def test_resolve_model_claude_no_key_falls_back(self):
        from core.langgraph.llm_factory import _resolve_model

        with patch.dict("os.environ", {}, clear=True):
            assert _resolve_model("claude-3-5-sonnet") == "gemini-2.5-flash"

    def test_resolve_model_claude_with_key(self):
        from core.langgraph.llm_factory import _resolve_model

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            assert _resolve_model("claude-3-5-sonnet") == "claude-3-5-sonnet"

    def test_resolve_model_gpt_no_key_falls_back(self):
        from core.langgraph.llm_factory import _resolve_model

        with patch.dict("os.environ", {}, clear=True):
            assert _resolve_model("gpt-4o") == "gemini-2.5-flash"

    def test_resolve_model_unknown_falls_back(self):
        from core.langgraph.llm_factory import _resolve_model

        assert _resolve_model("llama-70b") == "gemini-2.5-flash"


# ═══════════════════════════════════════════════════════════════════════════
# Tool Adapter
# ═══════════════════════════════════════════════════════════════════════════


class TestToolAdapter:
    def test_build_tool_index_finds_tools(self):
        from core.langgraph.tool_adapter import _build_tool_index

        index = _build_tool_index()
        assert len(index) > 200
        assert "fetch_bank_statement" in index
        assert "send_message" in index
        assert "create_ticket" in index

    def test_build_tool_index_maps_connector(self):
        from core.langgraph.tool_adapter import _build_tool_index

        index = _build_tool_index()
        connector, desc = index["send_message"]
        assert connector == "slack"

    def test_build_tools_for_agent_returns_langchain_tools(self):
        from core.langgraph.tool_adapter import build_tools_for_agent

        tools = build_tools_for_agent(["fetch_bank_statement", "create_payment_intent"])
        assert len(tools) == 2
        assert tools[0].name == "fetch_bank_statement"
        assert tools[1].name == "create_payment_intent"

    def test_build_tools_for_agent_skips_unknown(self):
        from core.langgraph.tool_adapter import build_tools_for_agent

        tools = build_tools_for_agent(["nonexistent_tool", "fetch_bank_statement"])
        assert len(tools) == 1
        assert tools[0].name == "fetch_bank_statement"

    def test_build_tools_for_agent_deduplicates(self):
        from core.langgraph.tool_adapter import build_tools_for_agent

        tools = build_tools_for_agent(
            ["fetch_bank_statement", "fetch_bank_statement"]
        )
        assert len(tools) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Agent Graph
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentGraph:
    def test_build_graph_with_tools(self):
        from core.langgraph.agent_graph import build_agent_graph

        graph = build_agent_graph(
            system_prompt="Test prompt",
            authorized_tools=["fetch_bank_statement"],
        )
        compiled = graph.compile()
        nodes = list(compiled.get_graph().nodes.keys())
        assert "reason" in nodes
        assert "execute_tools" in nodes
        assert "evaluate" in nodes
        assert "hitl_gate" in nodes

    def test_build_graph_without_tools(self):
        from core.langgraph.agent_graph import build_agent_graph

        graph = build_agent_graph(
            system_prompt="Test prompt",
            authorized_tools=[],
        )
        compiled = graph.compile()
        nodes = list(compiled.get_graph().nodes.keys())
        assert "reason" in nodes
        assert "execute_tools" not in nodes
        assert "evaluate" in nodes

    def test_parse_json_output_valid(self):
        from core.langgraph.agent_graph import _parse_json_output

        result = _parse_json_output('{"status": "completed", "confidence": 0.95}')
        assert result["status"] == "completed"
        assert result["confidence"] == 0.95

    def test_parse_json_output_markdown_wrapped(self):
        from core.langgraph.agent_graph import _parse_json_output

        result = _parse_json_output('```json\n{"status": "matched"}\n```')
        assert result["status"] == "matched"

    def test_parse_json_output_invalid_wraps(self):
        from core.langgraph.agent_graph import _parse_json_output

        result = _parse_json_output("not json at all")
        assert result["raw_output"] == "not json at all"

    def test_extract_confidence_numeric(self):
        from core.langgraph.agent_graph import _extract_confidence

        assert _extract_confidence({"confidence": 0.92}) == 0.92

    def test_extract_confidence_string(self):
        from core.langgraph.agent_graph import _extract_confidence

        assert _extract_confidence({"confidence": "high"}) == 0.95

    def test_extract_confidence_missing(self):
        from core.langgraph.agent_graph import _extract_confidence

        assert _extract_confidence({}) == 0.85

    def test_extract_confidence_clamps(self):
        from core.langgraph.agent_graph import _extract_confidence

        assert _extract_confidence({"confidence": 1.5}) == 1.0
        assert _extract_confidence({"confidence": -0.5}) == 0.0

    def test_check_hitl_trigger_below_floor(self):
        from core.langgraph.agent_graph import _check_hitl_trigger

        trigger = _check_hitl_trigger(0.5, 0.88, "", {})
        assert "confidence" in trigger
        assert "0.500" in trigger

    def test_check_hitl_trigger_above_floor(self):
        from core.langgraph.agent_graph import _check_hitl_trigger

        trigger = _check_hitl_trigger(0.95, 0.88, "", {})
        assert trigger == ""

    def test_check_hitl_trigger_condition(self):
        from core.langgraph.agent_graph import _check_hitl_trigger

        trigger = _check_hitl_trigger(0.95, 0.88, "amount > 500000", {"amount": 1000000})
        assert "condition matched" in trigger


# ═══════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════


class TestRunner:
    def test_build_user_message(self):
        from core.langgraph.runner import _build_user_message

        msg = _build_user_message({
            "action": "process_invoice",
            "inputs": {"invoice_id": "INV-001"},
        })
        assert "process_invoice" in msg
        assert "INV-001" in msg

    def test_build_user_message_empty(self):
        from core.langgraph.runner import _build_user_message

        msg = _build_user_message({})
        assert "process" in msg


# ═══════════════════════════════════════════════════════════════════════════
# AP Processor
# ═══════════════════════════════════════════════════════════════════════════


class TestApProcessor:
    def test_load_prompt(self):
        from core.langgraph.agents.ap_processor import load_ap_processor_prompt

        prompt = load_ap_processor_prompt({"org_name": "TestCorp"})
        assert "AP Processor Agent" in prompt
        assert "TestCorp" in prompt
        assert "{{org_name}}" not in prompt

    def test_load_prompt_preserves_unset_vars(self):
        from core.langgraph.agents.ap_processor import load_ap_processor_prompt

        prompt = load_ap_processor_prompt({})
        assert "{{org_name}}" in prompt

    def test_build_graph(self):
        from core.langgraph.agents.ap_processor import build_ap_processor_graph

        graph = build_ap_processor_graph(
            prompt_variables={"org_name": "TestCorp"},
        )
        compiled = graph.compile()
        nodes = list(compiled.get_graph().nodes.keys())
        assert "reason" in nodes
        assert "execute_tools" in nodes

    def test_default_tools(self):
        from core.langgraph.agents.ap_processor import AP_PROCESSOR_TOOLS

        assert "fetch_bank_statement" in AP_PROCESSOR_TOOLS
        assert "create_payment_intent" in AP_PROCESSOR_TOOLS
        assert "initiate_neft" not in AP_PROCESSOR_TOOLS  # AA is read-only
        assert len(AP_PROCESSOR_TOOLS) == 4


# ═══════════════════════════════════════════════════════════════════════════
# Grantex Registration
# ═══════════════════════════════════════════════════════════════════════════


class TestGrantexRegistration:
    def test_tools_to_scopes(self):
        from auth.grantex_registration import _tools_to_scopes

        scopes = _tools_to_scopes(["fetch_bank_statement", "send_message"], "finance")
        assert "agenticorg:finance:read" in scopes
        assert any("banking_aa" in s for s in scopes)
        assert any("slack" in s for s in scopes)

    def test_register_agent_no_api_key_returns_none(self):
        from auth.grantex_registration import register_agent

        with patch.dict("os.environ", {"GRANTEX_API_KEY": ""}, clear=False):
            result = register_agent("Test", "test_agent", "finance", ["fetch_bank_statement"])
            assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# Grantex Middleware
# ═══════════════════════════════════════════════════════════════════════════


class TestGrantexMiddleware:
    def test_is_grantex_token_rs256(self):
        # RS256 header: {"alg":"RS256","typ":"JWT"}
        import base64

        from auth.grantex_middleware import _is_grantex_token
        header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
        fake_token = f"{header}.payload.signature"
        assert _is_grantex_token(fake_token) is True

    def test_is_grantex_token_hs256(self):
        import base64

        from auth.grantex_middleware import _is_grantex_token
        header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
        fake_token = f"{header}.payload.signature"
        assert _is_grantex_token(fake_token) is False

    def test_is_grantex_token_invalid(self):
        from auth.grantex_middleware import _is_grantex_token

        assert _is_grantex_token("not-a-jwt") is False
        assert _is_grantex_token("") is False
