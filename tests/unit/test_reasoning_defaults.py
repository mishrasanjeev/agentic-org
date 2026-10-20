# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Creation defaults, explicit tenant choices, provider dispatch and published pricing agree."""

from core.ai_providers.catalog import find_llm
from core.ai_providers.defaults import DEFAULT_REASONING_MODEL, fallback_model, primary_model, primary_provider
from core.models.agent import Agent
from core.schemas.api import AgentCreate, LLMConfig


def test_new_configuration_uses_stable_flash_lite(monkeypatch):
    monkeypatch.delenv("AGENTICORG_LLM_PRIMARY", raising=False)
    monkeypatch.delenv("AGENTICORG_LLM_FALLBACK", raising=False)
    config = LLMConfig()
    assert config.model == config.fallback_model == DEFAULT_REASONING_MODEL
    assert AgentCreate(name="Test", agent_type="custom", domain="ops").llm.model == DEFAULT_REASONING_MODEL
    assert Agent.__table__.c.llm_model.default.arg(None) == DEFAULT_REASONING_MODEL
    entry = find_llm("gemini", DEFAULT_REASONING_MODEL)
    assert entry and entry.supports_tools and entry.supports_vision
    assert entry.context_window == 1_048_576


def test_explicit_agent_choice_is_not_rewritten():
    assert LLMConfig(provider="gemini", model="gemini-2.5-flash").model == "gemini-2.5-flash"
    assert LLMConfig(provider="openai", model="gpt-4o-mini").model == "gpt-4o-mini"


def test_opt_out_default_is_used_on_every_new_config(monkeypatch):
    monkeypatch.setenv("AGENTICORG_LLM_PRIMARY", "gpt-4o-mini")
    monkeypatch.delenv("AGENTICORG_LLM_FALLBACK", raising=False)
    assert primary_model() == fallback_model() == LLMConfig().model == "gpt-4o-mini"
    assert primary_provider() == "openai"


def test_registry_exposes_default_and_nonfree_rates():
    from core.llm.router import GEMINI_PRICE_PER_1M

    assert GEMINI_PRICE_PER_1M[DEFAULT_REASONING_MODEL] == {"input": 0.30, "output": 2.50}


def test_new_reasoning_usage_uses_input_and_output_prices():
    from langchain_core.messages import AIMessage

    from core.langgraph.runner import _sum_usage, _usage_since

    first = AIMessage(content="Done", usage_metadata={"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100})
    second = AIMessage(
        content="Done", usage_metadata={"input_tokens": 2000, "output_tokens": 200, "total_tokens": 2200}
    )
    assert _sum_usage([first], "gemini-3.5-flash-lite") == (1100, 0.00055)
    assert _usage_since(1100, [first, second], "gemini-3.5-flash-lite") == (2200, 0.0011)
