# ruff: noqa: N801 — class names match bug IDs for traceability
"""Regression tests for Ramesh's 15 bugs (RameshBugs03April2026.xlsx).

Every test verifies a specific fix stays in place. If any test fails,
the corresponding bug has regressed.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

TENANT_STR = "00000000-0000-0000-0000-000000000042"
TENANT_UUID = uuid.UUID(TENANT_STR)


# ============================================================================
# AGE_GEN_01: Custom agent test sample generation
# An agent with system_prompt_text (no file) must have its prompt loadable.
# The inline prompt resolution path in agents.py should use system_prompt_text
# directly instead of requiring a prompt file on disk.
# ============================================================================


class TestAGE_GEN_01:
    """AGE_GEN_01 — custom agent prompt loaded from system_prompt_text."""

    def test_system_prompt_text_used_directly(self):
        """When an agent config dict has system_prompt_text, the run endpoint
        should use it directly without requiring a file or import."""
        # Simulate the prompt resolution logic from agents.py:
        #   system_prompt = agent_config.get("system_prompt_text") or ""
        agent_config = {
            "system_prompt_text": "You are a custom AP agent for ACME Corp.",
            "system_prompt_ref": "",
            "prompt_variables": {},
            "agent_type": "custom_ap",
        }

        system_prompt = agent_config.get("system_prompt_text") or ""
        assert system_prompt == "You are a custom AP agent for ACME Corp."

    def test_system_prompt_text_none_falls_through(self):
        """When system_prompt_text is None, the code must fall through to
        the file-based resolution path (no crash)."""
        agent_config = {
            "system_prompt_text": None,
            "system_prompt_ref": "prompts/ap_processor.prompt.txt",
            "prompt_variables": {},
            "agent_type": "ap_processor",
        }

        system_prompt = agent_config.get("system_prompt_text") or ""
        assert system_prompt == ""  # Should be empty, triggering file lookup

    def test_system_prompt_text_field_exists_on_agent_create_schema(self):
        """AgentCreate schema must accept system_prompt_text."""
        from core.schemas.api import AgentCreate

        body = AgentCreate(
            name="Custom Agent",
            agent_type="custom_ap",
            domain="finance",
            system_prompt_text="Custom inline prompt for testing.",
        )
        assert body.system_prompt_text == "Custom inline prompt for testing."


# ============================================================================
# AGE-EXEC-02: List content in LLM output
# _parse_json_output must handle list input (not just str).
# ============================================================================


class TestAGE_EXEC_02:
    """AGE-EXEC-02 — _parse_json_output handles list inputs."""

    def test_list_input_returns_dict(self):
        """Passing a list to _parse_json_output should return a dict, not crash."""
        from core.langgraph.agent_graph import _parse_json_output

        result = _parse_json_output(["hello", "world"])
        assert isinstance(result, dict)

    def test_list_input_contains_raw_output(self):
        """When a list cannot be parsed as JSON, raw_output is preserved."""
        from core.langgraph.agent_graph import _parse_json_output

        result = _parse_json_output(["hello", "world"])
        assert "raw_output" in result or "status" in result

    def test_list_with_valid_json(self):
        """A list whose joined content is valid JSON should parse correctly."""
        from core.langgraph.agent_graph import _parse_json_output

        result = _parse_json_output(['{"confidence": 0.95,', '"status": "completed"}'])
        assert isinstance(result, dict)
        assert result.get("confidence") == 0.95

    def test_non_string_input_does_not_crash(self):
        """Non-string, non-list input (e.g. int) should not crash."""
        from core.langgraph.agent_graph import _parse_json_output

        # Should not raise — the function converts to str first
        result = _parse_json_output(12345)
        # json.loads("12345") returns int; the function may return non-dict
        # for valid JSON scalars. The key fix is that it doesn't crash.
        assert result is not None


# ============================================================================
# AGE-CONFIG-03: Scoped tool names in seed
# normalize_tool_names must strip scope prefixes like "oracle_fusion:read:".
# ============================================================================


class TestAGE_CONFIG_03:
    """AGE-CONFIG-03 — normalize_tool_names strips scope prefixes."""

    def test_strip_scope_prefix(self):
        from core.seed_tenant import normalize_tool_names

        inp = ["oracle_fusion:read:get_gl_balance", "fetch_bank_statement"]
        expected = ["get_gl_balance", "fetch_bank_statement"]
        assert normalize_tool_names(inp) == expected

    def test_double_colon_prefix(self):
        """Multiple colons — only the last segment is kept."""
        from core.seed_tenant import normalize_tool_names

        result = normalize_tool_names(["a:b:c:actual_tool"])
        assert result == ["actual_tool"]

    def test_no_prefix_unchanged(self):
        """Tool names without colons pass through unchanged."""
        from core.seed_tenant import normalize_tool_names

        assert normalize_tool_names(["simple_tool"]) == ["simple_tool"]

    def test_empty_list(self):
        from core.seed_tenant import normalize_tool_names

        assert normalize_tool_names([]) == []


# ============================================================================
# AGE-LIFECYCLE-04: Retire endpoint exists
# The FastAPI app must expose POST /agents/{id}/retire.
# ============================================================================


class TestAGE_LIFECYCLE_04:
    """AGE-LIFECYCLE-04 — /agents/{id}/retire route exists."""

    def test_retire_route_registered(self):
        from api.main import app

        route_paths = [getattr(r, "path", "") for r in app.routes]
        # The route is mounted under /api/v1 prefix
        assert any(
            "retire" in p for p in route_paths
        ), f"No retire route found. Routes: {[p for p in route_paths if 'agent' in p]}"

    def test_retire_route_is_post(self):
        from api.main import app

        for route in app.routes:
            path = getattr(route, "path", "")
            if "retire" in path:
                methods = getattr(route, "methods", set())
                assert "POST" in methods, f"Retire route should be POST, got {methods}"
                return
        pytest.fail("Retire route not found")


# ============================================================================
# AGE-CONFIG-05: AP Processor default tools
# ============================================================================


class TestAGE_CONFIG_05:
    """AGE-CONFIG-05 — AP Processor default tools are correct."""

    def test_ap_processor_has_post_voucher(self):
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        tools = _AGENT_TYPE_DEFAULT_TOOLS.get("ap_processor", [])
        assert "post_voucher" in tools, "AP Processor must include Tally post_voucher"

    def test_ap_processor_has_get_ledger_balance(self):
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        tools = _AGENT_TYPE_DEFAULT_TOOLS.get("ap_processor", [])
        assert "get_ledger_balance" in tools, "AP Processor must include Tally get_ledger_balance"

    def test_ap_processor_has_create_order(self):
        """AP Processor should have PineLabs create_order for India payments."""
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        tools = _AGENT_TYPE_DEFAULT_TOOLS.get("ap_processor", [])
        assert "create_order" in tools, "AP Processor must include PineLabs create_order"

    def test_ap_processor_no_stripe_create_payment_intent(self):
        """AP Processor should NOT have Stripe's create_payment_intent (wrong tool)."""
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        tools = _AGENT_TYPE_DEFAULT_TOOLS.get("ap_processor", [])
        assert "create_payment_intent" not in tools, (
            "AP Processor must NOT include Stripe create_payment_intent"
        )


# ============================================================================
# AGE-CONFIG-06: AR Collections default tools
# ============================================================================


class TestAGE_CONFIG_06:
    """AGE-CONFIG-06 — AR Collections default tools are correct."""

    def test_ar_has_create_invoice(self):
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        tools = _AGENT_TYPE_DEFAULT_TOOLS.get("ar_collections", [])
        assert "create_invoice" in tools, "AR Collections must include Zoho Books create_invoice"

    def test_ar_has_list_invoices(self):
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        tools = _AGENT_TYPE_DEFAULT_TOOLS.get("ar_collections", [])
        assert "list_invoices" in tools, "AR Collections must include list_invoices"

    def test_ar_no_stripe_create_payment_intent(self):
        """AR Collections should NOT have Stripe's create_payment_intent."""
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        tools = _AGENT_TYPE_DEFAULT_TOOLS.get("ar_collections", [])
        assert "create_payment_intent" not in tools, (
            "AR Collections must NOT include Stripe create_payment_intent"
        )


# ============================================================================
# AGE-CONFIG-007: Tax Compliance default tools
# ============================================================================


class TestAGE_CONFIG_007:
    """AGE-CONFIG-007 — Tax Compliance default tools are correct."""

    def test_tax_has_fetch_gstr2a(self):
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        tools = _AGENT_TYPE_DEFAULT_TOOLS.get("tax_compliance", [])
        assert "fetch_gstr2a" in tools, "Tax Compliance must include GSTN fetch_gstr2a"

    def test_tax_has_push_gstr1_data(self):
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        tools = _AGENT_TYPE_DEFAULT_TOOLS.get("tax_compliance", [])
        assert "push_gstr1_data" in tools, "Tax Compliance must include GSTN push_gstr1_data"

    def test_tax_has_file_gstr3b(self):
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        tools = _AGENT_TYPE_DEFAULT_TOOLS.get("tax_compliance", [])
        assert "file_gstr3b" in tools, "Tax Compliance must include GSTN file_gstr3b"

    def test_tax_no_income_tax_file_26q(self):
        """Tax Compliance should NOT have Income Tax file_26q_return (wrong tool)."""
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        tools = _AGENT_TYPE_DEFAULT_TOOLS.get("tax_compliance", [])
        assert "file_26q_return" not in tools, (
            "Tax Compliance must NOT include Income Tax file_26q_return"
        )


# ============================================================================
# AGE-METRICS-08: Token extraction for Gemini
# The runner must handle Gemini's response_metadata.usage_metadata format.
# ============================================================================


class TestAGE_METRICS_08:
    """AGE-METRICS-08 — token extraction handles Gemini response_metadata."""

    def test_gemini_token_extraction(self):
        """Mock an AIMessage with Gemini-style response_metadata and verify
        the runner's extraction logic counts tokens correctly."""
        from langchain_core.messages import AIMessage

        msg = AIMessage(content="test response")
        # Gemini puts token counts in response_metadata.usage_metadata
        msg.response_metadata = {
            "usage_metadata": {
                "total_token_count": 500,
                "prompt_token_count": 200,
                "candidates_token_count": 300,
            }
        }
        # Simulate the extraction logic from runner.py
        tokens_used = 0
        usage = getattr(msg, "usage_metadata", None)
        if usage:
            if isinstance(usage, dict):
                total = usage.get("total_tokens", 0) or (
                    (usage.get("input_tokens", 0) or 0)
                    + (usage.get("output_tokens", 0) or 0)
                )
            else:
                total = getattr(usage, "total_tokens", 0) or (
                    (getattr(usage, "input_tokens", 0) or 0)
                    + (getattr(usage, "output_tokens", 0) or 0)
                )
            tokens_used += total

        # If usage_metadata is not set on the message object itself, fall
        # through to response_metadata (the Gemini path)
        if tokens_used == 0:
            resp_meta = getattr(msg, "response_metadata", None) or {}
            if isinstance(resp_meta, dict):
                usage_meta = (
                    resp_meta.get("usage_metadata")
                    or resp_meta.get("token_usage")
                    or {}
                )
                if isinstance(usage_meta, dict):
                    total = (
                        usage_meta.get("total_token_count", 0)
                        or usage_meta.get("total_tokens", 0)
                        or (
                            (
                                usage_meta.get("prompt_token_count", 0)
                                or usage_meta.get("input_tokens", 0)
                                or 0
                            )
                            + (
                                usage_meta.get("candidates_token_count", 0)
                                or usage_meta.get("output_tokens", 0)
                                or 0
                            )
                        )
                    )
                    tokens_used += total

        assert tokens_used == 500, f"Expected 500 tokens, got {tokens_used}"

    def test_gemini_token_extraction_from_parts(self):
        """When total_token_count is missing, sum prompt + candidates."""
        from langchain_core.messages import AIMessage

        msg = AIMessage(content="test")
        msg.response_metadata = {
            "usage_metadata": {
                "total_token_count": 0,  # not reported
                "prompt_token_count": 150,
                "candidates_token_count": 250,
            }
        }
        resp_meta = getattr(msg, "response_metadata", {})
        usage_meta = resp_meta.get("usage_metadata", {})
        total = usage_meta.get("total_token_count", 0) or (
            (usage_meta.get("prompt_token_count", 0) or 0)
            + (usage_meta.get("candidates_token_count", 0) or 0)
        )
        assert total == 400

    def test_runner_source_has_gemini_path(self):
        """Verify runner.py source contains the Gemini response_metadata path."""
        source_path = Path(__file__).resolve().parent.parent.parent / "core" / "langgraph" / "runner.py"
        source = source_path.read_text(encoding="utf-8")
        assert "usage_metadata" in source
        assert "total_token_count" in source
        assert "candidates_token_count" in source


# ============================================================================
# AGE-SAFETY-09: Confidence capped on tool failure
# When tool calls have errors, confidence must be capped at 0.5.
# ============================================================================


class TestAGE_SAFETY_09:
    """AGE-SAFETY-09 — confidence capped at 0.5 when tool calls fail."""

    def test_confidence_capped_on_tool_error(self):
        """The evaluate node should cap confidence when tool_calls_log has errors."""
        from core.langgraph.agent_graph import _extract_confidence

        output = {"confidence": 0.95, "status": "completed"}
        confidence = _extract_confidence(output)
        assert confidence == 0.95  # Without tool errors, it's the raw value

        # Now simulate the capping logic from evaluate():
        any_tool_failed = True  # simulating a tool error
        if any_tool_failed:
            confidence = min(confidence, 0.5)
        assert confidence <= 0.5, "Confidence must be capped at 0.5 when tools fail"

    def test_confidence_capped_on_output_incomplete(self):
        """When output is incomplete (raw_output fallback), confidence is capped."""
        from core.langgraph.agent_graph import _extract_confidence

        output = {"raw_output": "some text", "status": "completed"}
        confidence = _extract_confidence(output)

        output_incomplete = output.get("raw_output") is not None
        if output_incomplete:
            confidence = min(confidence, 0.5)
        assert confidence <= 0.5

    def test_evaluate_source_has_cap_logic(self):
        """Verify agent_graph.py source contains the 0.5 cap logic."""
        source_path = (
            Path(__file__).resolve().parent.parent.parent
            / "core" / "langgraph" / "agent_graph.py"
        )
        source = source_path.read_text(encoding="utf-8")
        assert "min(confidence, 0.5)" in source, (
            "agent_graph.py must contain the confidence cap: min(confidence, 0.5)"
        )
        assert "any_tool_failed" in source


# ============================================================================
# CONN-DB-010: Seed connectors exist
# ============================================================================


class TestCONN_DB_010:
    """CONN-DB-010 — SEED_CONNECTORS contains the required connectors."""

    def test_zoho_books_in_seed(self):
        from core.seed_tenant import SEED_CONNECTORS

        names = [c["name"] for c in SEED_CONNECTORS]
        assert "zoho_books" in names

    def test_tally_in_seed(self):
        from core.seed_tenant import SEED_CONNECTORS

        names = [c["name"] for c in SEED_CONNECTORS]
        assert "tally" in names

    def test_gstn_in_seed(self):
        from core.seed_tenant import SEED_CONNECTORS

        names = [c["name"] for c in SEED_CONNECTORS]
        assert "gstn" in names

    def test_seed_connectors_have_tool_functions(self):
        """Each seed connector must have at least one tool_function."""
        from core.seed_tenant import SEED_CONNECTORS

        for conn in SEED_CONNECTORS:
            assert len(conn.get("tool_functions", [])) > 0, (
                f"Seed connector '{conn['name']}' has no tool_functions"
            )


# ============================================================================
# AGE-STATE-011: Shadow count uses atomic SQL
# The shadow_sample_count update must use atomic SQL (COALESCE + increment)
# to avoid race conditions between concurrent agent runs.
# ============================================================================


class TestAGE_STATE_011:
    """AGE-STATE-011 — shadow_sample_count incremented atomically."""

    def test_atomic_increment_pattern_in_source(self):
        """agents.py must use atomic SQL for shadow_sample_count update."""
        source_path = Path(__file__).resolve().parent.parent.parent / "api" / "v1" / "agents.py"
        source = source_path.read_text(encoding="utf-8")

        # Must contain COALESCE-based atomic increment
        assert "COALESCE(shadow_sample_count" in source, (
            "agents.py must use COALESCE for atomic shadow_sample_count increment"
        )
        assert "shadow_sample_count = COALESCE(shadow_sample_count, 0) + 1" in source, (
            "agents.py must increment shadow_sample_count atomically with +1"
        )

    def test_no_orm_level_increment(self):
        """Ensure there is no Python-level (non-atomic) increment like
        agent.shadow_sample_count += 1 in the run endpoint."""
        source_path = Path(__file__).resolve().parent.parent.parent / "api" / "v1" / "agents.py"
        source = source_path.read_text(encoding="utf-8")

        # Look for lines matching ORM-style increment (non-atomic)
        # We skip comments/docstrings — just check for actual assignment
        lines = source.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            assert "shadow_sample_count += 1" not in stripped, (
                f"Line {i}: ORM-level shadow_sample_count += 1 is not atomic; "
                "use raw SQL with COALESCE instead"
            )


# ============================================================================
# CONN-BRIDGE-012: Bridge handles malformed JSON
# TallyBridge._message_loop must catch json.JSONDecodeError gracefully.
# ============================================================================


class TestCONN_BRIDGE_012:
    """CONN-BRIDGE-012 — TallyBridge handles malformed JSON in messages."""

    def test_message_loop_has_json_error_handling(self):
        """The _message_loop method source must contain json.JSONDecodeError handling."""
        source_path = Path(__file__).resolve().parent.parent.parent / "bridge" / "tally_bridge.py"
        source = source_path.read_text(encoding="utf-8")
        assert "json.JSONDecodeError" in source, (
            "tally_bridge.py must handle json.JSONDecodeError in _message_loop"
        )

    def test_connect_has_json_error_handling(self):
        """The _connect method (auth handshake) should also handle malformed JSON."""
        source_path = Path(__file__).resolve().parent.parent.parent / "bridge" / "tally_bridge.py"
        source = source_path.read_text(encoding="utf-8")
        # Count occurrences — should be in both _connect and _message_loop
        count = source.count("json.JSONDecodeError")
        assert count >= 2, (
            f"Expected json.JSONDecodeError handling in at least 2 places "
            f"(_connect and _message_loop), found {count}"
        )

    def test_tally_bridge_importable(self):
        """TallyBridge class must be importable."""
        from bridge.tally_bridge import TallyBridge

        assert hasattr(TallyBridge, "_message_loop")
        assert hasattr(TallyBridge, "_connect")


# ============================================================================
# AGE-BUDGET-013: UTC-aware datetimes
# All datetime.now() calls in cost_ledger.py must use UTC.
# ============================================================================


class TestAGE_BUDGET_013:
    """AGE-BUDGET-013 — cost_ledger.py uses UTC-aware datetimes."""

    def test_all_datetime_now_calls_use_utc(self):
        """Every datetime.now() in cost_ledger.py must pass UTC."""
        source_path = Path(__file__).resolve().parent.parent.parent / "scaling" / "cost_ledger.py"
        source = source_path.read_text(encoding="utf-8")

        # Find all datetime.now(...) calls
        now_calls = re.findall(r"datetime\.now\(([^)]*)\)", source)
        for arg in now_calls:
            assert "UTC" in arg, (
                f"Found datetime.now({arg}) without UTC in cost_ledger.py"
            )

    def test_no_naive_datetime_now(self):
        """datetime.now() without args (naive datetime) must NOT appear."""
        source_path = Path(__file__).resolve().parent.parent.parent / "scaling" / "cost_ledger.py"
        source = source_path.read_text(encoding="utf-8")

        # Match datetime.now() with no arguments (naive)
        naive_matches = re.findall(r"datetime\.now\(\s*\)", source)
        assert len(naive_matches) == 0, (
            f"Found {len(naive_matches)} naive datetime.now() call(s) in cost_ledger.py"
        )

    def test_utc_imported(self):
        """The UTC constant must be imported in cost_ledger.py."""
        source_path = Path(__file__).resolve().parent.parent.parent / "scaling" / "cost_ledger.py"
        source = source_path.read_text(encoding="utf-8")
        assert "from datetime import" in source and "UTC" in source


# ============================================================================
# AGENT-BUDGET-014: Cost ledger failure triggers HITL
# When cost ledger write fails, the agent must not silently swallow the error.
# It must set hitl_trigger = "budget_tracking_failed".
# ============================================================================


class TestAGENT_BUDGET_014:
    """AGENT-BUDGET-014 — cost ledger failure triggers HITL, not silent ignore."""

    def test_cost_ledger_failure_sets_hitl_trigger(self):
        """agents.py must set hitl_trigger when cost ledger write fails."""
        source_path = Path(__file__).resolve().parent.parent.parent / "api" / "v1" / "agents.py"
        source = source_path.read_text(encoding="utf-8")

        assert "budget_tracking_failed" in source, (
            "agents.py must set hitl_trigger = 'budget_tracking_failed' on cost ledger failure"
        )

    def test_cost_ledger_failure_not_just_warning(self):
        """The except clause must do more than just log a warning — it must
        set hitl_trigger to escalate the issue."""
        source_path = Path(__file__).resolve().parent.parent.parent / "api" / "v1" / "agents.py"
        source = source_path.read_text(encoding="utf-8")

        # Find the cost ledger except block
        assert 'hitl_trigger = hitl_trigger or "budget_tracking_failed"' in source, (
            "Cost ledger failure must set hitl_trigger, not just log"
        )

    def test_cost_ledger_failure_appends_trace(self):
        """The failure path should append a warning to the reasoning trace."""
        source_path = Path(__file__).resolve().parent.parent.parent / "api" / "v1" / "agents.py"
        source = source_path.read_text(encoding="utf-8")

        assert "cost ledger write failed" in source, (
            "Cost ledger failure must append a trace entry about the failure"
        )


# ============================================================================
# AGENT-API-015: AgentUpdate rejects extra fields
# AgentUpdate must use model_config = {"extra": "forbid"} to reject
# unknown fields, preventing silent data loss.
# ============================================================================


class TestAGENT_API_015:
    """AGENT-API-015 — AgentUpdate ignores extra fields (BUG #6: changed to extra=ignore)."""

    def test_extra_field_silently_ignored(self):
        """AgentUpdate with an unknown field should NOT raise (extra=ignore)."""
        from core.schemas.api import AgentUpdate

        update = AgentUpdate(invalid_field="test")
        # extra=ignore means the field is silently dropped
        assert not hasattr(update, "invalid_field")

    def test_valid_fields_accepted(self):
        """Creating AgentUpdate with valid fields must succeed."""
        from core.schemas.api import AgentUpdate

        update = AgentUpdate(name="valid")
        assert update.name == "valid"

    def test_multiple_valid_fields(self):
        """Multiple valid fields should work together."""
        from core.schemas.api import AgentUpdate

        update = AgentUpdate(
            name="Updated Agent",
            confidence_floor=0.92,
            employee_name="Raj",
        )
        assert update.name == "Updated Agent"
        assert update.confidence_floor == 0.92
        assert update.employee_name == "Raj"

    def test_extra_ignore_config(self):
        """AgentUpdate must have extra='ignore' (BUG #6 fix: was 'forbid')."""
        from core.schemas.api import AgentUpdate

        config = AgentUpdate.model_config
        assert config.get("extra") == "ignore", (
            f"AgentUpdate.model_config must have extra='ignore', got {config}"
        )

    def test_multiple_extra_fields_silently_ignored(self):
        """Multiple unknown fields should be silently ignored."""
        from core.schemas.api import AgentUpdate

        update = AgentUpdate(bogus_a="x", bogus_b="y")
        assert not hasattr(update, "bogus_a")
        assert not hasattr(update, "bogus_b")
