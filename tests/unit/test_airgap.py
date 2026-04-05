"""Tests for air-gapped deployment — Ollama + vLLM LLM factory integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure no leftover env vars leak between tests."""
    for var in (
        "AGENTICORG_LLM_MODE",
        "OLLAMA_BASE_URL",
        "OLLAMA_HOST",
        "VLLM_BASE_URL",
        "VLLM_API_BASE",
        "VLLM_API_KEY",
        "AGENTICORG_LOCAL_TIER1",
        "AGENTICORG_LOCAL_TIER2",
        "AGENTICORG_LOCAL_TIER3",
        "AGENTICORG_LLM_PRIMARY",
        "AGENTICORG_LLM_ROUTING",
        "GOOGLE_GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def _mock_chat_openai(**kwargs):
    """Return a mock ChatOpenAI that records its init kwargs."""
    mock = MagicMock()
    mock._init_kwargs = kwargs
    return mock


class TestLocalModeUsesOllamaEndpoint:
    """AGENTICORG_LLM_MODE=local should create a model pointing to Ollama."""

    def test_local_mode_uses_ollama_endpoint(self, monkeypatch):
        monkeypatch.setenv("AGENTICORG_LLM_MODE", "local")
        monkeypatch.setenv("AGENTICORG_LOCAL_TIER1", "gemma3:7b")

        with (
            patch("core.langgraph.llm_factory._is_local_endpoint_available") as mock_avail,
            patch("langchain_openai.ChatOpenAI", side_effect=_mock_chat_openai) as mock_cls,
        ):
            mock_avail.side_effect = lambda host, port, **kw: port == 11434

            from core.langgraph.llm_factory import create_chat_model

            create_chat_model()

            # Should have called ChatOpenAI with ollama base_url
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args
            assert "ollama" in str(call_kwargs).lower() or "11434" in str(call_kwargs)


class TestAutoModeDetectsLocal:
    """AGENTICORG_LLM_MODE=auto should try local first, fall back to cloud."""

    def test_auto_mode_detects_local(self, monkeypatch):
        monkeypatch.setenv("AGENTICORG_LLM_MODE", "auto")

        with (
            patch("core.langgraph.llm_factory._is_local_endpoint_available") as mock_avail,
            patch("langchain_openai.ChatOpenAI", side_effect=_mock_chat_openai) as mock_cls,
        ):
            # Ollama is available
            mock_avail.side_effect = lambda host, port, **kw: port == 11434

            from core.langgraph.llm_factory import create_chat_model

            create_chat_model()

            # Should use Ollama (ChatOpenAI with ollama key)
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args
            assert "ollama" in str(call_kwargs).lower()


class TestCloudModeIgnoresLocal:
    """AGENTICORG_LLM_MODE=cloud (default) should not probe local endpoints."""

    def test_cloud_mode_ignores_local(self, monkeypatch):
        monkeypatch.setenv("AGENTICORG_LLM_MODE", "cloud")
        monkeypatch.setenv("AGENTICORG_LLM_ROUTING", "disabled")
        monkeypatch.setenv("GOOGLE_GEMINI_API_KEY", "test-key")

        with patch("core.langgraph.llm_factory._is_local_endpoint_available") as mock_avail:
            from core.langgraph.llm_factory import create_chat_model

            try:
                create_chat_model(model="gemini-2.5-flash")
            except Exception:  # noqa: BLE001, S110
                pass  # May fail without real API key; that's fine

            # _is_local_endpoint_available should NOT have been called
            mock_avail.assert_not_called()


class TestOllamaModelPrefixHandled:
    """Models prefixed with 'ollama:' should be routed to Ollama regardless of mode."""

    def test_ollama_model_prefix_handled(self, monkeypatch):
        monkeypatch.setenv("AGENTICORG_LLM_MODE", "cloud")

        with patch("langchain_openai.ChatOpenAI", side_effect=_mock_chat_openai) as mock_cls:
            from core.langgraph.llm_factory import create_chat_model

            create_chat_model(model="ollama:llama3.1:8b")

            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args
            # Model name should have the prefix stripped
            assert call_kwargs[1]["model"] == "llama3.1:8b" or call_kwargs.kwargs.get("model") == "llama3.1:8b"


class TestVllmModelPrefixHandled:
    """Models prefixed with 'vllm:' should be routed to vLLM regardless of mode."""

    def test_vllm_model_prefix_handled(self, monkeypatch):
        monkeypatch.setenv("AGENTICORG_LLM_MODE", "cloud")

        with patch("langchain_openai.ChatOpenAI", side_effect=_mock_chat_openai) as mock_cls:
            from core.langgraph.llm_factory import create_chat_model

            create_chat_model(model="vllm:meta-llama/Llama-3.1-70B")

            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args
            # Model name should have the prefix stripped
            assert (
                call_kwargs[1].get("model") == "meta-llama/Llama-3.1-70B"
                or call_kwargs.kwargs.get("model") == "meta-llama/Llama-3.1-70B"
            )
            # Should use vLLM endpoint (port 8000)
            base_url_arg = str(call_kwargs)
            assert "8000" in base_url_arg
