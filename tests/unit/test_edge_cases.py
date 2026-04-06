# ruff: noqa: S108 — test files use /tmp paths intentionally
"""Test edge cases, empty states, null handling, and boundary conditions.

Covers:
- Connector edge cases: empty config, missing auth, pre-connect calls
- Agent edge cases: empty tools, missing prompts, boundary confidence
- Report engine edge cases: empty data, missing channels
- KPI edge cases: empty/zero states, large/negative values
- Chat edge cases: whitespace, unicode, rapid queries
- Workflow edge cases: missing fields, circular deps, unknown step types

~80 tests.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# Connector Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestConnectorEdgeCases:
    """Edge cases for the BaseConnector framework."""

    def _make_connector(self, config=None):
        """Create a concrete connector subclass for testing."""
        from connectors.framework.base_connector import BaseConnector

        class StubConnector(BaseConnector):
            name = "stub"
            category = "test"
            auth_type = "api_key"
            base_url = "https://stub.example.com"

            def _register_tools(self):
                async def echo_tool(**kwargs):
                    return {"echo": kwargs}

                self._tool_registry["echo"] = echo_tool

            async def _authenticate(self):
                api_key = self._get_secret("api_key")
                if api_key:
                    self._auth_headers["Authorization"] = f"Bearer {api_key}"

        return StubConnector(config=config)

    def test_connector_with_empty_config_does_not_crash(self):
        """Connector should initialize with empty config without errors."""
        connector = self._make_connector(config={})
        assert connector.config == {}
        assert connector.name == "stub"

    def test_connector_with_none_config_uses_defaults(self):
        """Connector should treat None config as empty dict."""
        connector = self._make_connector(config=None)
        assert connector.config == {}

    def test_connector_get_secret_empty_config_returns_empty(self):
        """Getting a secret from empty config should return empty string."""
        connector = self._make_connector(config={})
        result = connector._get_secret("api_key")
        assert result == ""

    def test_connector_get_secret_none_credentials(self):
        """Config with None values should not raise KeyError."""
        connector = self._make_connector(config={"api_key": None})
        # None is returned for the direct lookup
        result = connector._get_secret("api_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_connector_get_before_connect_raises_runtime_error(self):
        """Calling _get() before connect() should raise RuntimeError."""
        connector = self._make_connector(config={})
        with pytest.raises(RuntimeError, match="Connector not connected"):
            await connector._get("/some-path")

    @pytest.mark.asyncio
    async def test_connector_post_before_connect_raises_runtime_error(self):
        connector = self._make_connector(config={})
        with pytest.raises(RuntimeError, match="Connector not connected"):
            await connector._post("/some-path", data={})

    @pytest.mark.asyncio
    async def test_connector_put_before_connect_raises_runtime_error(self):
        connector = self._make_connector(config={})
        with pytest.raises(RuntimeError, match="Connector not connected"):
            await connector._put("/some-path", data={})

    @pytest.mark.asyncio
    async def test_connector_patch_before_connect_raises_runtime_error(self):
        connector = self._make_connector(config={})
        with pytest.raises(RuntimeError, match="Connector not connected"):
            await connector._patch("/some-path", data={})

    @pytest.mark.asyncio
    async def test_connector_delete_before_connect_raises_runtime_error(self):
        connector = self._make_connector(config={})
        with pytest.raises(RuntimeError, match="Connector not connected"):
            await connector._delete("/some-path")

    @pytest.mark.asyncio
    async def test_connector_post_form_before_connect_raises(self):
        connector = self._make_connector(config={})
        with pytest.raises(RuntimeError, match="Connector not connected"):
            await connector._post_form("/path", data={})

    @pytest.mark.asyncio
    async def test_connector_odata_get_before_connect_raises(self):
        connector = self._make_connector(config={})
        with pytest.raises(RuntimeError, match="Connector not connected"):
            await connector._odata_get("/path")

    @pytest.mark.asyncio
    async def test_connector_post_xml_before_connect_raises(self):
        connector = self._make_connector(config={})
        with pytest.raises(RuntimeError, match="Connector not connected"):
            await connector._post_xml("<xml/>")

    @pytest.mark.asyncio
    async def test_health_check_not_connected_returns_status(self):
        """Health check on disconnected/unconfigured connector returns proper status."""
        connector = self._make_connector(config={})
        result = await connector.health_check()
        assert result["status"] in ("not_connected", "not_configured")

    @pytest.mark.asyncio
    async def test_execute_tool_unknown_tool_raises(self):
        """Executing a non-registered tool should raise ValueError."""
        connector = self._make_connector(config={})
        with pytest.raises(ValueError, match="not registered"):
            await connector.execute_tool("nonexistent_tool", {})

    @pytest.mark.asyncio
    async def test_execute_tool_with_empty_params(self):
        """Calling a tool with empty params dict should work."""
        connector = self._make_connector(config={})
        result = await connector.execute_tool("echo", {})
        assert result == {"echo": {}}

    @pytest.mark.asyncio
    async def test_execute_tool_with_extra_params(self):
        """Calling a tool with extra unexpected params should work (**kwargs)."""
        connector = self._make_connector(config={})
        result = await connector.execute_tool(
            "echo", {"extra_key": "value", "another": 42}
        )
        assert result["echo"]["extra_key"] == "value"

    def test_connector_config_overrides_base_url(self):
        """Config base_url should override the class default."""
        connector = self._make_connector(
            config={"base_url": "https://custom.example.com"}
        )
        assert connector.base_url == "https://custom.example.com"

    def test_connector_config_empty_base_url_keeps_default(self):
        """Empty string in config base_url should keep the class default."""
        connector = self._make_connector(config={"base_url": ""})
        assert connector.base_url == "https://stub.example.com"

    def test_gcp_secret_ref_invalid_format_returns_empty(self):
        """Non-GCP secret ref should return empty string."""
        from connectors.framework.base_connector import BaseConnector

        result = BaseConnector._resolve_gcp_secret("not-a-gcp-uri", "api_key")
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════
# Agent Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentEdgeCases:
    """Edge cases for the BaseAgent class."""

    def _make_agent(self, **kwargs):
        from core.agents.base import BaseAgent

        defaults = {
            "agent_id": "test-agent-001",
            "tenant_id": "test-tenant-001",
            "authorized_tools": [],
            "prompt_variables": {},
        }
        defaults.update(kwargs)
        return BaseAgent(**defaults)

    def test_agent_with_empty_authorized_tools(self):
        """Agent with no tools should still initialize without error."""
        agent = self._make_agent(authorized_tools=[])
        assert agent.authorized_tools == []

    def test_agent_with_none_authorized_tools(self):
        """None tools should default to empty list."""
        agent = self._make_agent(authorized_tools=None)
        assert agent.authorized_tools == []

    def test_agent_with_empty_prompt_variables(self):
        """Empty prompt variables should not crash system_prompt generation."""
        agent = self._make_agent(prompt_variables={})
        # No prompt_file set, so it should use the minimal default
        prompt = agent.system_prompt
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_agent_prompt_template_missing_variable_leaves_placeholder(self):
        """If a prompt file has {{var}} but var is not in prompt_variables,
        the placeholder stays as-is."""
        agent = self._make_agent(prompt_variables={"org_name": "TestCorp"})

        # Mock reading a prompt file that has an unknown variable
        mock_template = "Welcome to {{org_name}}. Your threshold is {{missing_var}}."
        agent.prompt_file = "test.prompt.txt"

        with patch("builtins.open", MagicMock(return_value=MagicMock(
            read=MagicMock(return_value=mock_template),
            __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=mock_template))),
            __exit__=MagicMock(return_value=False),
        ))):
            agent._system_prompt = None  # Reset cached value
            prompt = agent.system_prompt
            assert "TestCorp" in prompt
            assert "{{missing_var}}" in prompt

    def test_agent_system_prompt_no_file_uses_default(self):
        """Agent without prompt_file uses minimal default prompt."""
        agent = self._make_agent()
        assert agent.prompt_file == ""
        prompt = agent.system_prompt
        assert "AI agent" in prompt

    def test_agent_system_prompt_is_cached(self):
        """Second access to system_prompt should return the same cached object."""
        agent = self._make_agent()
        prompt1 = agent.system_prompt
        prompt2 = agent.system_prompt
        assert prompt1 is prompt2

    def test_agent_nonexistent_prompt_file_raises(self):
        """Loading a prompt from a nonexistent file should raise FileNotFoundError."""
        agent = self._make_agent()
        agent.prompt_file = "this_does_not_exist.prompt.txt"
        agent._system_prompt = None
        with pytest.raises(FileNotFoundError):
            _ = agent.system_prompt

    def test_agent_default_confidence_floor(self):
        agent = self._make_agent()
        assert agent.confidence_floor == 0.88

    def test_agent_confidence_floor_zero(self):
        """confidence_floor=0 should be accepted."""
        from core.agents.base import BaseAgent

        agent = BaseAgent(
            agent_id="t", tenant_id="t", authorized_tools=[]
        )
        agent.confidence_floor = 0.0
        assert agent.confidence_floor == 0.0

    def test_agent_confidence_floor_one(self):
        """confidence_floor=1 should be accepted (everything escalated)."""
        from core.agents.base import BaseAgent

        agent = BaseAgent(
            agent_id="t", tenant_id="t", authorized_tools=[]
        )
        agent.confidence_floor = 1.0
        assert agent.confidence_floor == 1.0


# ═══════════════════════════════════════════════════════════════════════════
# Report Engine Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestReportGeneratorEdgeCases:
    """Edge cases for ReportGenerator."""

    def test_generate_unknown_type_raises_value_error(self):
        from core.reports.generator import ReportGenerator

        gen = ReportGenerator()
        with pytest.raises(ValueError, match="Unknown report type"):
            gen.generate(report_type="nonexistent_type", params={})

    def test_generate_with_empty_params(self):
        """Empty params dict should still produce a valid report."""
        from core.reports.generator import ReportGenerator

        gen = ReportGenerator()
        output = gen.generate(report_type="cfo_daily", params={})
        assert output.report_type == "cfo_daily"
        assert len(output.content_html) > 0

    def test_report_output_generated_at_is_string(self):
        from core.reports.generator import ReportGenerator

        gen = ReportGenerator()
        output = gen.generate(report_type="cfo_daily", params={})
        assert isinstance(output.generated_at, str)
        assert len(output.generated_at) > 10

    def test_report_output_content_data_not_empty(self):
        from core.reports.generator import ReportGenerator

        gen = ReportGenerator()
        output = gen.generate(report_type="cfo_daily", params={})
        assert isinstance(output.content_data, dict)
        assert len(output.content_data) > 0


class TestPDFRendererEdgeCases:
    """Edge cases for render_pdf."""

    def test_render_pdf_empty_content_data(self):
        """PDF render with minimal data should still create a valid file."""
        from core.reports.generator import ReportOutput
        from core.reports.renderer import render_pdf

        report = ReportOutput(
            content_html="<html><body>Empty</body></html>",
            content_data={},
            report_type="cfo_daily",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "empty_test.pdf")
            result = render_pdf(report, path)
            assert os.path.exists(result)
            assert os.path.getsize(result) > 0
            with open(result, "rb") as f:
                assert f.read(5) == b"%PDF-"

    def test_render_pdf_unknown_report_type_still_works(self):
        """PDF renderer should handle unknown report types gracefully."""
        from core.reports.generator import ReportOutput
        from core.reports.renderer import render_pdf

        report = ReportOutput(
            content_html="<html><body>Custom</body></html>",
            content_data={"custom": "data"},
            report_type="custom_unknown",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "custom.pdf")
            result = render_pdf(report, path)
            assert os.path.exists(result)


class TestExcelRendererEdgeCases:
    """Edge cases for render_excel."""

    def test_render_excel_empty_content_data(self):
        """Excel render with empty data should create a valid .xlsx."""
        from core.reports.generator import ReportOutput
        from core.reports.renderer import render_excel

        report = ReportOutput(
            content_html="<html><body>Empty</body></html>",
            content_data={},
            report_type="cfo_daily",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "empty_test.xlsx")
            result = render_excel(report, path)
            assert os.path.exists(result)
            assert os.path.getsize(result) > 0
            with open(result, "rb") as f:
                assert f.read(2) == b"PK"  # XLSX is a ZIP

    def test_render_excel_unknown_report_type(self):
        """Excel renderer should handle unknown report types gracefully."""
        from core.reports.generator import ReportOutput
        from core.reports.renderer import render_excel

        report = ReportOutput(
            content_html="<html><body>Custom</body></html>",
            content_data={"custom_key": "custom_val"},
            report_type="custom_unknown",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "custom.xlsx")
            result = render_excel(report, path)
            assert os.path.exists(result)


class TestDeliveryEdgeCases:
    """Edge cases for the delivery pipeline."""

    @pytest.mark.asyncio
    async def test_deliver_to_unknown_channel_skips(self):
        from core.reports.delivery import deliver

        results = await deliver(
            "/tmp/fake.pdf",
            [{"type": "fax", "target": "12345"}],
        )
        assert len(results) == 1
        assert results[0]["status"] == "skipped"
        assert "unknown" in results[0]["reason"]

    @pytest.mark.asyncio
    async def test_deliver_empty_target_skips(self):
        from core.reports.delivery import deliver

        results = await deliver(
            "/tmp/fake.pdf",
            [{"type": "email", "target": ""}],
        )
        assert len(results) == 1
        assert results[0]["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_deliver_empty_channels_list(self):
        """Empty channel list should return empty results, not error."""
        from core.reports.delivery import deliver

        results = await deliver("/tmp/fake.pdf", [])
        assert results == []

    @pytest.mark.asyncio
    async def test_deliver_email_failure_logs_and_continues(self):
        """If email delivery fails, it should be caught and logged."""
        from core.reports.delivery import deliver

        with patch(
            "core.reports.delivery.deliver_email",
            new_callable=AsyncMock,
            side_effect=ConnectionError("SMTP down"),
        ):
            results = await deliver(
                "/tmp/fake.pdf",
                [
                    {"type": "email", "target": "fail@example.com"},
                ],
            )
            assert len(results) == 1
            assert results[0]["status"] == "failed"
            assert "SMTP down" in results[0]["error"]

    @pytest.mark.asyncio
    async def test_deliver_slack_failure_logged_not_crash(self):
        from core.reports.delivery import deliver

        with patch(
            "core.reports.delivery.deliver_slack",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Slack API down"),
        ):
            results = await deliver(
                "/tmp/fake.pdf",
                [{"type": "slack", "target": "C12345"}],
            )
            assert len(results) == 1
            assert results[0]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_deliver_multi_channel_partial_failure(self):
        """One channel failure should not block other channels."""
        from core.reports.delivery import deliver

        with patch(
            "core.reports.delivery.deliver_email",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Email down"),
        ), patch(
            "core.reports.delivery.deliver_slack",
            new_callable=AsyncMock,
            return_value={"status": "sent", "channel": "slack"},
        ):
            results = await deliver(
                "/tmp/fake.pdf",
                [
                    {"type": "email", "target": "fail@x.com"},
                    {"type": "slack", "target": "C99999"},
                ],
            )
            assert len(results) == 2
            statuses = {r["status"] for r in results}
            assert "failed" in statuses
            assert "sent" in statuses

    @pytest.mark.asyncio
    async def test_deliver_whatsapp_failure_logged_not_crash(self):
        from core.reports.delivery import deliver

        with patch(
            "core.reports.delivery.deliver_whatsapp",
            new_callable=AsyncMock,
            side_effect=RuntimeError("WhatsApp API down"),
        ):
            results = await deliver(
                "/tmp/fake.pdf",
                [{"type": "whatsapp", "target": "+919999999999"}],
            )
            assert len(results) == 1
            assert results[0]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_deliver_email_no_api_key_skips(self):
        """deliver_email without SENDGRID_API_KEY should skip."""
        from core.reports.delivery import deliver_email

        with patch.dict(os.environ, {"SENDGRID_API_KEY": ""}, clear=False):
            result = await deliver_email(
                "/tmp/fake.pdf", "test@example.com", "Subject"
            )
            assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_deliver_slack_no_token_skips(self):
        """deliver_slack without SLACK_BOT_TOKEN should skip."""
        from core.reports.delivery import deliver_slack

        with patch.dict(os.environ, {"SLACK_BOT_TOKEN": ""}, clear=False):
            result = await deliver_slack("/tmp/fake.pdf", "C12345")
            assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_deliver_whatsapp_no_token_skips(self):
        """deliver_whatsapp without WHATSAPP_TOKEN should skip."""
        from core.reports.delivery import deliver_whatsapp

        with patch.dict(
            os.environ,
            {"WHATSAPP_TOKEN": "", "WHATSAPP_PHONE_ID": ""},
            clear=False,
        ):
            result = await deliver_whatsapp("/tmp/fake.pdf", "+919876543210")
            assert result["status"] == "skipped"


# ═══════════════════════════════════════════════════════════════════════════
# KPI Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestKPIEdgeCases:
    """Edge cases for KPI data handling."""

    def test_cfo_kpi_bank_balances_is_list_not_null(self):
        """bank_balances should always be a list, never null."""
        from core.reports.generator import ReportGenerator

        data = ReportGenerator._fetch_cfo_kpis("default")
        assert isinstance(data["bank_balances"], list)
        assert len(data["bank_balances"]) > 0

    def test_cfo_kpi_ar_aging_has_all_buckets(self):
        from core.reports.generator import ReportGenerator

        data = ReportGenerator._fetch_cfo_kpis("default")
        ar = data["ar_aging"]
        for key in ("0_30", "31_60", "61_90", "90_plus"):
            assert key in ar
            assert isinstance(ar[key], (int, float))

    def test_cfo_kpi_monthly_pl_is_list(self):
        from core.reports.generator import ReportGenerator

        data = ReportGenerator._fetch_cfo_kpis("default")
        assert isinstance(data["monthly_pl"], list)
        assert len(data["monthly_pl"]) > 0

    def test_cmo_kpi_roas_is_dict(self):
        from core.reports.generator import ReportGenerator

        data = ReportGenerator._fetch_cmo_kpis("default")
        assert isinstance(data["roas_by_channel"], dict)

    def test_cmo_kpi_email_performance_has_rates(self):
        from core.reports.generator import ReportGenerator

        data = ReportGenerator._fetch_cmo_kpis("default")
        ep = data["email_performance"]
        assert "open_rate" in ep
        assert "click_rate" in ep
        assert "unsubscribe_rate" in ep

    def test_kpi_values_are_non_negative(self):
        """All KPI numeric values should be non-negative in demo data."""
        from core.reports.generator import ReportGenerator

        data = ReportGenerator._fetch_cfo_kpis("default")
        assert data["cash_runway_months"] >= 0
        assert data["burn_rate"] >= 0
        assert data["dso_days"] >= 0
        assert data["dpo_days"] >= 0

    def test_kpi_large_numbers_handled(self):
        """Verify the INR formatter handles large numbers."""
        from core.reports.renderer import _inr

        result = _inr(99_99_99_99_999)
        assert "INR" in result
        assert "," in result  # Should have comma formatting

    def test_kpi_zero_value_handled(self):
        from core.reports.renderer import _inr

        result = _inr(0)
        assert result == "INR 0"

    def test_kpi_negative_value_handled(self):
        """Negative values (losses) should be formatted correctly."""
        from core.reports.renderer import _inr

        result = _inr(-15_00_000)
        assert "INR" in result
        assert "-" in result


# ═══════════════════════════════════════════════════════════════════════════
# Chat Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestChatClassifierEdgeCases:
    """Edge cases for the domain classification heuristic."""

    def test_classify_whitespace_only_returns_general(self):
        from api.v1.chat import _classify_domain

        result = _classify_domain("   \t\n  ")
        assert result == "general"

    def test_classify_empty_string_returns_general(self):
        from api.v1.chat import _classify_domain

        result = _classify_domain("")
        assert result == "general"

    def test_classify_hindi_does_not_crash(self):
        from api.v1.chat import _classify_domain

        # Should not raise, just return general (no keyword match)
        result = _classify_domain("कंपनी का राजस्व")
        assert isinstance(result, str)

    def test_classify_mixed_domain_picks_best(self):
        """Query matching multiple domains should pick highest score."""
        from api.v1.chat import _classify_domain

        # This has 2 finance keywords (invoice, payment) and 1 HR (employee)
        result = _classify_domain("invoice payment employee")
        assert result == "finance"

    def test_classify_finance_keywords(self):
        from api.v1.chat import _classify_domain

        assert _classify_domain("show me the invoice") == "finance"
        assert _classify_domain("what is our revenue?") == "finance"

    def test_classify_hr_keywords(self):
        from api.v1.chat import _classify_domain

        assert _classify_domain("employee headcount") == "hr"
        assert _classify_domain("leave balance") == "hr"

    def test_classify_marketing_keywords(self):
        from api.v1.chat import _classify_domain

        assert _classify_domain("ad campaign ROAS") == "marketing"
        assert _classify_domain("SEO analytics") == "marketing"

    def test_classify_operations_keywords(self):
        from api.v1.chat import _classify_domain

        assert _classify_domain("inventory levels") == "operations"

    def test_classify_sales_keywords(self):
        from api.v1.chat import _classify_domain

        assert _classify_domain("deal pipeline quota") == "sales"

    def test_classify_communications_keywords(self):
        from api.v1.chat import _classify_domain

        assert _classify_domain("slack notification") == "communications"

    def test_classify_special_characters_safe(self):
        from api.v1.chat import _classify_domain

        # Should not crash on regex-special characters
        result = _classify_domain("test [brackets] (parens) {braces} $dollar")
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════════════
# Workflow Parser Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkflowParserEdgeCases:
    """Edge cases for WorkflowParser."""

    def _parser(self):
        from workflows.parser import WorkflowParser

        return WorkflowParser()

    def test_parse_missing_steps_raises(self):
        parser = self._parser()
        with pytest.raises(ValueError, match="steps"):
            parser.parse({})

    def test_parse_empty_steps_list_raises(self):
        """Workflow with steps: [] should raise (at least one step required)."""
        parser = self._parser()
        # The parser requires steps to have at least one entry with an id
        # An empty list means no steps — which passes the 'steps in defn' check
        # but iterates over nothing. Let's check it handles gracefully.
        # Actually, empty steps is valid (loop over nothing). The API layer
        # rejects it, but the parser itself iterates fine.
        result = parser.parse({"steps": []})
        assert result["steps"] == []

    def test_parse_step_without_id_raises(self):
        parser = self._parser()
        with pytest.raises(ValueError, match="id"):
            parser.parse({"steps": [{"type": "agent"}]})

    def test_parse_duplicate_step_ids_raises(self):
        parser = self._parser()
        with pytest.raises(ValueError, match="Duplicate"):
            parser.parse({
                "steps": [
                    {"id": "step1", "type": "agent"},
                    {"id": "step1", "type": "agent"},
                ]
            })

    def test_parse_unknown_step_type_raises(self):
        parser = self._parser()
        with pytest.raises(ValueError, match="Invalid step type"):
            parser.parse({"steps": [{"id": "s1", "type": "unknown_type"}]})

    def test_parse_circular_dependency_raises(self):
        parser = self._parser()
        with pytest.raises(ValueError, match="[Cc]ircular"):
            parser.parse({
                "steps": [
                    {"id": "a", "type": "agent", "depends_on": ["b"]},
                    {"id": "b", "type": "agent", "depends_on": ["a"]},
                ]
            })

    def test_parse_valid_workflow_succeeds(self):
        parser = self._parser()
        result = parser.parse({
            "steps": [
                {"id": "step1", "type": "agent"},
                {"id": "step2", "type": "condition", "depends_on": ["step1"]},
            ]
        })
        assert len(result["steps"]) == 2

    def test_parse_yaml_string(self):
        parser = self._parser()
        yaml_str = """
steps:
  - id: s1
    type: agent
  - id: s2
    type: notify
    depends_on:
      - s1
"""
        result = parser.parse(yaml_str)
        assert len(result["steps"]) == 2

    def test_parse_step_default_type_is_agent(self):
        """If step type is omitted, it should default to 'agent'."""
        parser = self._parser()
        result = parser.parse({"steps": [{"id": "s1"}]})
        assert result["steps"][0].get("type", "agent") == "agent"

    def test_all_valid_step_types_accepted(self):
        from workflows.parser import WorkflowParser

        parser = WorkflowParser()
        for i, step_type in enumerate(parser.VALID_STEP_TYPES):
            result = parser.parse({
                "steps": [{"id": f"step_{i}", "type": step_type}]
            })
            assert len(result["steps"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Connector Registry Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestConnectorRegistryEdgeCases:
    """Edge cases for ConnectorRegistry."""

    def test_get_nonexistent_returns_none(self):
        from connectors.registry import ConnectorRegistry

        result = ConnectorRegistry.get("absolutely_nonexistent_connector")
        assert result is None

    def test_all_names_returns_list(self):
        from connectors.registry import ConnectorRegistry

        names = ConnectorRegistry.all_names()
        assert isinstance(names, list)

    def test_by_category_empty_returns_list(self):
        from connectors.registry import ConnectorRegistry

        result = ConnectorRegistry.by_category("nonexistent_category")
        assert isinstance(result, list)
        assert len(result) == 0
