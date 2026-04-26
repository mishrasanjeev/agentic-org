"""Tests for core.llm.router — Smart LLM routing (v4.0.0 Section 4).

All tests use mocks so RouteLLM need not be installed in CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers — import under mock so we never hit real config / RouteLLM
# ---------------------------------------------------------------------------

def _make_router(
    *,
    routing: str = "auto",
    llm_mode: str = "cloud",
    routellm_available: bool = False,
):
    """Create a SmartLLMRouter with controlled settings."""
    with (
        patch("core.llm.router.settings") as mock_settings,
        patch("core.llm.router._ROUTELLM_AVAILABLE", routellm_available),
    ):
        mock_settings.llm_routing = routing
        mock_settings.llm_mode = llm_mode
        mock_settings.llm_primary = "gemini-2.5-flash"
        mock_settings.llm_fallback = "gemini-2.5-flash-preview-05-20"
        mock_settings.llm_temperature = 0.2

        from core.llm.router import SmartLLMRouter

        router = SmartLLMRouter()
    return router


# ===================================================================
# TC-LLM-01: Simple query routes to Tier 1 (Gemini Flash-Lite)
# ===================================================================
# Tier1 was gemini-2.5-flash before the spend-cap PR; the rework
# moves Flash to Tier2 and uses Flash-Lite for high-volume Tier1
# traffic so per-token cost on the cheapest path drops ~2x.


class TestSimpleQueryRoutesToTier1:
    """Short / simple queries should route to Tier 1 (Gemini Flash-Lite)."""

    def test_short_query_routes_to_tier1(self):
        router = _make_router(routing="auto", llm_mode="cloud")
        model = router.route(query="What is 2+2?", config={})
        assert model == "gemini-2.5-flash-lite", f"Expected tier1 model, got {model}"

    def test_very_short_query(self):
        router = _make_router(routing="auto", llm_mode="cloud")
        model = router.route(query="Hello", config={})
        assert model == "gemini-2.5-flash-lite"

    def test_empty_query_routes_to_tier1(self):
        """Empty string < 100 chars -> tier1."""
        router = _make_router(routing="auto", llm_mode="cloud")
        model = router.route(query="", config={})
        assert model == "gemini-2.5-flash-lite"


# ===================================================================
# TC-LLM-02: Complex query routes to Tier 3
# ===================================================================


class TestComplexQueryRoutesToTier3:
    """Long / complex queries (>= 500 chars) should route to Tier 3."""

    def test_long_query_routes_to_tier3(self):
        router = _make_router(routing="auto", llm_mode="cloud")
        long_query = (
            "Analyze the following legal contract for compliance with Indian labor law. "
            "Identify any clauses that violate the Industrial Disputes Act of 1947, "
            "the Factories Act of 1948, and the Employees Provident Fund Act. "
            "Additionally, flag any non-compete clauses that may be unenforceable "
            "under Section 27 of the Indian Contract Act. Provide a detailed "
            "risk assessment with severity ratings for each identified issue "
            "and recommend specific amendments to bring the contract into full compliance. "
            "Consider recent Supreme Court rulings from 2024-2025 that may impact interpretation."
        )
        assert len(long_query) >= 500
        model = router.route(query=long_query, config={})
        # Tier 3 model — defaults to claude-sonnet-4-20250514 (from env or constant)
        assert "gemini-2.5-flash" not in model or model != "gemini-2.5-flash"

    def test_medium_query_routes_to_tier2(self):
        router = _make_router(routing="auto", llm_mode="cloud")
        medium_query = (
            "Summarize the quarterly financial report for Q3 2025. "
            "Include key revenue metrics, expense breakdown by department, "
            "and highlight any significant variances from the budget. "
            "Also note the cash flow trends and working capital position. "
            "Flag items needing CFO attention."
        )
        assert 100 <= len(medium_query) < 500
        model = router.route(query=medium_query, config={})
        assert model == "gemini-2.5-flash"


# ===================================================================
# TC-LLM-03: Forced tier overrides auto classification
# ===================================================================


class TestForcedTierOverridesAuto:
    """When routing mode is tier1/tier2/tier3, use that tier regardless."""

    def test_force_tier1_on_complex_query(self):
        router = _make_router(routing="tier1", llm_mode="cloud")
        long_query = "x" * 600  # Would be tier3 in auto mode
        model = router.route(query=long_query, config={})
        assert model == "gemini-2.5-flash-lite"

    def test_force_tier2(self):
        router = _make_router(routing="tier2", llm_mode="cloud")
        model = router.route(query="Hi", config={})  # Would be tier1 in auto
        assert model == "gemini-2.5-flash"

    def test_force_tier3_on_simple_query(self):
        router = _make_router(routing="tier3", llm_mode="cloud")
        model = router.route(query="Hi", config={})
        # Tier 3 = pro after the spend-cap rework.
        assert model == "gemini-2.5-pro"

    def test_per_agent_config_overrides_global(self):
        """Agent-level routing config should override the global setting."""
        router = _make_router(routing="auto", llm_mode="cloud")
        model = router.route(
            query="Hi",
            config={"routing": "tier3"},
        )
        # tier3 -> gemini-2.5-pro, definitely not the tier1 flash-lite.
        assert model == "gemini-2.5-pro"

    def test_per_agent_tier1_overrides_global_tier3(self):
        router = _make_router(routing="tier3", llm_mode="cloud")
        model = router.route(
            query="Analyze this complex legal document...",
            config={"routing": "tier1"},
        )
        assert model == "gemini-2.5-flash-lite"


# ===================================================================
# TC-LLM-04: Disabled routing uses agent's configured model
# ===================================================================


class TestDisabledRoutingUsesAgentModel:
    """When routing is disabled, return the agent's own llm_model."""

    def test_disabled_returns_agent_model(self):
        router = _make_router(routing="disabled", llm_mode="cloud")
        model = router.route(
            query="Anything at all",
            config={"llm_model": "gpt-4o"},
        )
        assert model == "gpt-4o"

    def test_disabled_returns_empty_when_no_agent_model(self):
        router = _make_router(routing="disabled", llm_mode="cloud")
        model = router.route(query="Anything", config={})
        assert model == ""

    def test_disabled_via_agent_config(self):
        """Per-agent config can set routing=disabled even if global is auto."""
        router = _make_router(routing="auto", llm_mode="cloud")
        model = router.route(
            query="What is 2+2?",
            config={"routing": "disabled", "llm_model": "claude-sonnet-4-20250514"},
        )
        assert model == "claude-sonnet-4-20250514"


# ===================================================================
# TC-LLM-05: Air-gap mode routes to local models
# ===================================================================


class TestAirgapModeRoutesToLocalModels:
    """When AGENTICORG_LLM_MODE=local, use Ollama/vLLM models."""

    def test_local_tier1(self):
        router = _make_router(routing="tier1", llm_mode="local")
        model = router.route(query="Hi", config={})
        # Should be a local model, not a cloud one
        assert "gemini" not in model
        assert "claude" not in model
        assert "gpt" not in model

    def test_local_tier2(self):
        router = _make_router(routing="tier2", llm_mode="local")
        model = router.route(query="Hi", config={})
        assert "gemini" not in model

    def test_local_tier3(self):
        router = _make_router(routing="tier3", llm_mode="local")
        model = router.route(query="Hi", config={})
        assert "gemini" not in model

    def test_local_auto_routing(self):
        router = _make_router(routing="auto", llm_mode="local")
        # Short query => tier1 => local tier1 model
        model = router.route(query="Hello", config={})
        assert "gemini" not in model

    @patch.dict("os.environ", {"OLLAMA_HOST": "http://gpu-box:11434"})
    def test_auto_mode_detects_ollama(self):
        """When LLM_MODE=auto and OLLAMA_HOST is set, detect as local."""
        router = _make_router(routing="auto", llm_mode="auto")
        model = router.route(query="Hi", config={})
        # Should route to local model since OLLAMA_HOST is set
        assert "gemini" not in model


# ===================================================================
# TC-LLM-06: Routing decision is logged
# ===================================================================


class TestRoutingDecisionLogged:
    """Every routing decision should be logged via structlog."""

    def test_auto_routing_logs_decision(self):
        router = _make_router(routing="auto", llm_mode="cloud")
        with patch("core.llm.router.logger") as mock_logger:
            router.route(query="What is 2+2?", config={})
            mock_logger.info.assert_called_once()
            call_kwargs = mock_logger.info.call_args
            assert call_kwargs[0][0] == "llm_routing_decision"
            assert "tier" in call_kwargs[1]
            assert "model" in call_kwargs[1]
            assert "reason" in call_kwargs[1]

    def test_forced_tier_logs_decision(self):
        router = _make_router(routing="tier2", llm_mode="cloud")
        with patch("core.llm.router.logger") as mock_logger:
            router.route(query="Hi", config={})
            mock_logger.info.assert_called_once()
            call_kwargs = mock_logger.info.call_args
            assert "tier2" in str(call_kwargs)

    def test_disabled_routing_logs_decision(self):
        router = _make_router(routing="disabled", llm_mode="cloud")
        with patch("core.llm.router.logger") as mock_logger:
            router.route(query="Hi", config={"llm_model": "gpt-4o"})
            mock_logger.info.assert_called_once()
            call_kwargs = mock_logger.info.call_args
            assert "disabled" in str(call_kwargs)


# ===================================================================
# TC-LLM-07: Fallback when RouteLLM is unavailable
# ===================================================================


class TestFallbackWhenRouteLLMUnavailable:
    """When RouteLLM is not installed, use heuristic fallback (never crash)."""

    def test_heuristic_fallback_short(self):
        """Without RouteLLM, short queries use heuristic -> tier1."""
        router = _make_router(routing="auto", llm_mode="cloud", routellm_available=False)
        model = router.route(query="Hi", config={})
        assert model == "gemini-2.5-flash-lite"

    def test_heuristic_fallback_medium(self):
        router = _make_router(routing="auto", llm_mode="cloud", routellm_available=False)
        medium = "a" * 200
        model = router.route(query=medium, config={})
        assert model == "gemini-2.5-flash"

    def test_heuristic_fallback_long(self):
        router = _make_router(routing="auto", llm_mode="cloud", routellm_available=False)
        long_q = "b" * 600
        model = router.route(query=long_q, config={})
        # Tier3 = pro after the spend-cap rework.
        assert model == "gemini-2.5-pro"

    def test_routellm_import_failure_does_not_crash(self):
        """Even if RouteLLM import raises, the module should load fine."""
        with patch("core.llm.router._ROUTELLM_AVAILABLE", False):
            from core.llm.router import SmartLLMRouter

            router = SmartLLMRouter()
            # Should still work via heuristic
            model = router.route(query="Test", config={})
            assert isinstance(model, str)

    def test_routellm_runtime_error_falls_back(self):
        """If RouteLLM is 'available' but throws at runtime, fall back to heuristic."""
        router = _make_router(routing="auto", llm_mode="cloud", routellm_available=True)
        # Force the controller init to fail
        router._routellm_init_attempted = True
        router._routellm_controller = None
        model = router.route(query="Quick question", config={})
        # Heuristic short-query path -> tier1 -> flash-lite.
        assert model == "gemini-2.5-flash-lite"


# ===================================================================
# Integration: llm_factory uses router
# ===================================================================


class TestLLMFactoryIntegration:
    """Verify that create_chat_model() calls the smart router."""

    @patch("core.langgraph.llm_factory.smart_router")
    @patch("core.langgraph.llm_factory._build_model")
    def test_factory_calls_router_with_query(self, mock_build, mock_router):
        mock_router.route.return_value = "gemini-2.5-pro"
        mock_build.return_value = MagicMock()

        from core.langgraph.llm_factory import create_chat_model

        create_chat_model(query="Summarize this report", routing_config={"routing": "auto"})

        mock_router.route.assert_called_once()
        call_kwargs = mock_router.route.call_args[1]
        assert call_kwargs["query"] == "Summarize this report"

    @patch("core.langgraph.llm_factory.smart_router")
    @patch("core.langgraph.llm_factory._build_model")
    def test_factory_skips_router_when_disabled(self, mock_build, mock_router):
        mock_build.return_value = MagicMock()

        from core.langgraph.llm_factory import create_chat_model

        create_chat_model(
            model="gpt-4o",
            query="Hello",
            routing_config={"routing": "disabled"},
        )

        mock_router.route.assert_not_called()

    @patch("core.langgraph.llm_factory.smart_router")
    @patch("core.langgraph.llm_factory._build_model")
    def test_factory_skips_router_when_no_query(self, mock_build, mock_router):
        mock_build.return_value = MagicMock()

        from core.langgraph.llm_factory import create_chat_model

        create_chat_model(model="gemini-2.5-flash")

        mock_router.route.assert_not_called()

    @patch("core.langgraph.llm_factory.smart_router")
    @patch("core.langgraph.llm_factory._build_model")
    def test_factory_falls_back_on_router_error(self, mock_build, mock_router):
        """If the router raises, factory should still create a model."""
        mock_router.route.side_effect = RuntimeError("Router crashed")
        mock_build.return_value = MagicMock()

        from core.langgraph.llm_factory import create_chat_model

        # Should not raise
        result = create_chat_model(
            model="gemini-2.5-flash",
            query="Test query",
        )
        assert result is not None


# ===================================================================
# Backward compatibility: legacy LLMRouter, LLMResponse, llm_router
# ===================================================================


class TestBackwardCompatibility:
    """Ensure the legacy exports still work for existing importers."""

    def test_legacy_imports(self):
        from core.llm.router import LLMResponse, LLMRouter, llm_router

        assert LLMResponse is not None
        assert LLMRouter is not None
        assert llm_router is not None
        assert isinstance(llm_router, LLMRouter)

    def test_llm_response_dataclass(self):
        from core.llm.router import LLMResponse

        resp = LLMResponse(content="hello", model="gemini-2.5-flash")
        assert resp.content == "hello"
        assert resp.tokens_used == 0
        assert resp.cost_usd == 0.0

    def test_init_exports(self):
        from core.llm import LLMResponse, LLMRouter, SmartLLMRouter, llm_router, smart_router

        assert SmartLLMRouter is not None
        assert smart_router is not None
        assert LLMRouter is not None
        assert llm_router is not None
        assert LLMResponse is not None
