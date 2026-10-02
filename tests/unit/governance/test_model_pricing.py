# SPDX-License-Identifier: Apache-2.0
"""List prices, overrides, local models and the cost of a call."""

from __future__ import annotations

import pytest

from core.governance import model_pricing as pricing


class TestPrices:
    def test_the_gemini_rows_are_the_routers_table(self):
        from core.llm.router import GEMINI_PRICE_PER_1M

        price = pricing.price_for("gemini", "gemini-2.5-flash")
        assert price is not None and price.source == "list"
        assert (price.input_per_million, price.output_per_million) == (
            GEMINI_PRICE_PER_1M["gemini-2.5-flash"]["input"],
            GEMINI_PRICE_PER_1M["gemini-2.5-flash"]["output"],
        )

    def test_every_priced_catalogue_model_has_a_positive_price_and_the_unpriced_ones_none(self):
        from core.ai_providers.catalog import LLM_CATALOG

        for entry in LLM_CATALOG:
            price = pricing.price_for(entry.provider, entry.model)
            if entry.provider == "openai_compatible" or entry.model == "gemini-2.0-flash-exp":
                assert price is None, entry
            else:
                assert price is not None and price.output_per_million > 0, entry

    def test_an_azure_deployment_is_priced_as_its_base_model_and_local_models_cost_nothing(self):
        azure = pricing.price_for("azure_openai", "deployment:gpt-4o-mini")
        base = pricing.price_for("openai", "gpt-4o-mini")
        assert azure is not None and base is not None
        assert (azure.input_per_million, azure.output_per_million) == (base.input_per_million, base.output_per_million)
        local = pricing.price_for("ollama", "ollama:llama3")
        assert local is not None and local.source == "local" and local.blended_per_million == 0.0
        assert pricing.price_for(None, "vllm:qwen").source == "local"
        assert pricing.price_for("openai", "") is None and pricing.price_for("openai", "unknown-model") is None

    def test_an_override_replaces_the_list_and_a_bad_override_is_ignored(self, monkeypatch):
        monkeypatch.setattr(
            pricing.settings,
            "model_price_overrides_json",
            '{"OpenAI/gpt-4o": {"input": 2.0, "output": 8.0}, "mine/x": {"input": 1, "output": 1}}',
        )
        price = pricing.price_for("openai", "gpt-4o")
        assert price is not None and price.source == "override" and price.input_per_million == 2.0
        assert pricing.price_for("mine", "x").source == "override"
        monkeypatch.setattr(pricing.settings, "model_price_overrides_json", "{not json")
        assert pricing.price_for("openai", "gpt-4o").source == "list"
        monkeypatch.setattr(pricing.settings, "model_price_overrides_json", '{"openai/gpt-4o": {"input": 1}}')
        assert pricing.price_for("openai", "gpt-4o").source == "list"

    def test_the_cost_of_a_call_uses_the_split_or_the_blended_rate(self):
        price = pricing.Price("openai", "gpt-4o-mini", 0.15, 0.60)
        assert price.cost_usd(input_tokens=1000, output_tokens=1000, tokens=2000) == pytest.approx(0.00075)
        assert price.blended_per_million == pytest.approx(0.15 * 0.75 + 0.60 * 0.25)
        assert price.cost_usd(input_tokens=None, output_tokens=None, tokens=1_000_000) == pytest.approx(
            price.blended_per_million
        )
        assert price.to_dict()["source"] == "list"

    def test_ranking_puts_the_cheapest_first_and_the_unpriced_last(self):
        cheap = pricing.Price("openai", "gpt-4o-mini", 0.15, 0.60)
        dear = pricing.Price("openai", "gpt-4o", 2.5, 10.0)
        assert sorted([None, dear, cheap], key=pricing.rank_key) == [cheap, dear, None]


class TestRouterPricing:
    def test_the_router_costs_a_call_at_its_models_price_and_keeps_the_flat_rate_for_an_unknown_model(self):
        from core.llm.router import priced_cost_usd

        mini = priced_cost_usd(
            "openai", "gpt-4o-mini", input_tokens=1000, output_tokens=1000, tokens=2000, fallback=0.02
        )
        assert mini == pytest.approx(0.00075)
        unknown = priced_cost_usd(
            "openai", "gpt-unknown", input_tokens=None, output_tokens=None, tokens=2000, fallback=0.02
        )
        assert unknown == 0.02
        opus = priced_cost_usd(
            "anthropic", "claude-opus-4-5-20250929", input_tokens=1000, output_tokens=0, tokens=1000, fallback=0.003
        )
        assert opus == pytest.approx(0.015)

    def test_the_router_call_sites_use_the_priced_cost(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[3] / "core" / "llm" / "router.py").read_text(encoding="utf-8")
        assert src.count("priced_cost_usd(") == 3  # the definition and both call sites
        assert "cost = tokens * 10 / 1_000_000" not in src
