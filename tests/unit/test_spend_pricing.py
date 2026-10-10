# SPDX-License-Identifier: Apache-2.0
"""The spend pricing engine: card selection, fallback, in-house, unpriced, money and FX, tiers (pure)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from core.config import settings
from core.spend import clock, pricing, rates, vocab
from core.spend.errors import SpendError
from core.spend.pricing import Card, FxRate, Tier, Usage

D0 = date(2026, 1, 1)
ON = date(2026, 10, 1)


def card(
    unit: str = "1m_input_tokens",
    price: str = "2.5",
    *,
    provider: str = "openai",
    usage_type: str = "llm_tokens",
    sku: str = "gpt-4o",
    source: str = "list",
    start: date = D0,
    end: date | None = None,
    currency: str = "USD",
    cached: str | None = None,
    batch: str = "0",
    status: str = "active",
) -> Card:
    return Card(
        id=uuid.uuid4(),
        provider=provider,
        usage_type=usage_type,
        model_sku=sku,
        unit=unit,
        unit_price=Decimal(price),
        currency=currency,
        cached_unit_price=Decimal(cached) if cached is not None else None,
        batch_discount_pct=Decimal(batch),
        volume_tiers=(),
        tier_mode="graduated",
        effective_from=start,
        effective_to=end,
        source=source,
        status=status,
    )


def usage(
    unit: str = "input_token",
    quantity: str = "1000000",
    *,
    provider: str = "openai",
    usage_type: str = "llm_tokens",
    model: str = "gpt-4o",
    on: date = ON,
    fx_on: date | None = None,
    batch: bool = False,
) -> Usage:
    return Usage(provider, usage_type, unit, Decimal(quantity), model, on, fx_on or on, batch)


def no_fx(currency: str) -> FxRate | None:
    return None


def rates_on(table: dict[tuple[str, date], str]):
    """``rate_for`` answering like ``rate_on``: the date's rate, else the latest earlier one."""

    def build(fx_on: date):
        def rate_for(currency: str) -> FxRate | None:
            found = sorted((d, r) for (c, d), r in table.items() if c == currency and d <= fx_on)
            if not found:
                return None
            day, rate = found[-1]
            return FxRate(currency, day, Decimal(rate))

        return rate_for

    return build


class TestEffectiveDating:
    def test_card_in_force_is_half_open(self):
        dated = card(start=date(2026, 1, 1), end=date(2026, 2, 1))
        assert pricing.in_force(dated, date(2026, 1, 1))
        assert pricing.in_force(dated, date(2026, 1, 31))
        assert not pricing.in_force(dated, date(2026, 2, 1))
        assert not pricing.in_force(dated, date(2025, 12, 31))
        assert pricing.in_force(card(start=date(2026, 1, 1)), date(2099, 1, 1))

    def test_rate_change_prices_each_day_with_its_card(self):
        old = card(price="2", end=date(2026, 10, 1))
        new = card(price="3", start=date(2026, 10, 1))
        before = pricing.price_with(usage(on=date(2026, 9, 30)), [old, new], no_fx)
        after = pricing.price_with(usage(on=date(2026, 10, 1)), [old, new], no_fx)
        assert (before.rate_card_id, before.amount) == (old.id, Decimal("2.0000000000"))
        assert (after.rate_card_id, after.amount) == (new.id, Decimal("3.0000000000"))

    def test_cards_are_dated_by_billing_date_and_fx_by_reporting_date(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_reporting_timezone", "Asia/Kolkata")
        monkeypatch.setattr(settings, "spend_provider_billing_timezones_json", "")
        event = datetime(2026, 9, 30, 23, 0, tzinfo=UTC)
        on, fx_on = clock.billing_date_of("openai", event), clock.event_date_of(event)
        assert (on, fx_on) == (date(2026, 9, 30), date(2026, 10, 1))
        september = card(price="2", end=date(2026, 10, 1))
        october = card(price="3", start=date(2026, 10, 1))
        build = rates_on({("USD", date(2026, 9, 30)): "83", ("USD", date(2026, 10, 1)): "84"})
        priced = pricing.price_with(usage(on=on, fx_on=fx_on), [september, october], build(fx_on))
        assert priced.rate_card_id == september.id
        assert priced.fx_rate == Decimal("84") and priced.fx_rate_date == date(2026, 10, 1)
        assert priced.amount_inr == Decimal("168.0000000000") and not priced.fx_estimated

    def test_retired_card_never_prices(self):
        retired = card(price="1", source="contract", status="retired")
        active = card(price="2")
        assert pricing.price_with(usage(), [retired, active], no_fx).rate_card_id == active.id
        only = pricing.price_with(
            usage(provider="acme_ai", model="m1"), [card(provider="acme_ai", sku="m1", status="retired")], no_fx
        )
        assert only.unpriced and only.amount is None


class TestPrecedence:
    def test_model_specific_card_beats_provider_default(self):
        specific = card(price="3")
        default = card(price="1", sku="")
        assert pricing.select_price(usage(), [default, specific]).card is specific
        assert pricing.select_price(usage(model="gpt-4o-mini"), [default, specific]).card is default

    def test_contract_card_beats_list_card_for_the_same_key_and_date(self):
        listed = card(price="2.5")
        contract = card(price="2.0", source="contract")
        chosen = pricing.select_price(usage(), [listed, contract])
        assert chosen.card is contract and chosen.source == "contract"

    def test_specific_list_card_beats_default_contract_card(self):
        specific = card(price="3")
        default_contract = card(price="1", sku="", source="contract")
        assert pricing.select_price(usage(), [default_contract, specific]).card is specific

    def test_latest_effective_from_breaks_ties(self):
        earlier = card(price="2", start=date(2026, 1, 1))
        later = card(price="3", start=date(2026, 6, 1))
        assert pricing.select_price(usage(), [earlier, later]).card is later

    def test_default_list_cached_card_loses_to_specific_contract_input_card_with_cached_price(self):
        default_cached = card("1m_cached_input_tokens", "0.5", sku="")
        specific_input = card("1m_input_tokens", "2.5", source="contract", cached="1.0")
        chosen = pricing.select_price(usage("cached_input_token"), [default_cached, specific_input])
        assert chosen.card is specific_input and chosen.unit_price == Decimal("1.0") and chosen.path_index == 1

    def test_default_cached_card_never_prices_a_model_with_its_own_input_card(self):
        default_cached = card("1m_cached_input_tokens", "0.1", sku="")
        specific_input = card("1m_input_tokens", "2.5")
        chosen = pricing.select_price(usage("cached_input_token"), [default_cached, specific_input])
        assert chosen.card is specific_input and chosen.unit_price == Decimal("2.5") and chosen.path_index == 2

    def test_same_specificity_cached_card_beats_no_discount_input_price(self):
        cached_list = card("1m_cached_input_tokens", "1.25")
        input_contract = card("1m_input_tokens", "2.0", source="contract")
        chosen = pricing.select_price(usage("cached_input_token"), [input_contract, cached_list])
        assert chosen.card is cached_list and chosen.card_unit == "1m_cached_input_tokens"

    def test_default_1m_tokens_list_card_loses_to_specific_contract_blend(self):
        default_tokens = card("1m_tokens", "0.5", sku="")
        specific_in = card("1m_input_tokens", "2.0", source="contract")
        specific_out = card("1m_output_tokens", "8.0", source="contract")
        chosen = pricing.select_price(usage("token"), [default_tokens, specific_in, specific_out])
        assert chosen.card is specific_in and chosen.blend_card is specific_out and chosen.source == "contract"

    def test_unsplit_tokens_blend_in_decimal_and_records_both_cards(self):
        cards = [card("1m_input_tokens", "0.075"), card("1m_output_tokens", "0.30")]
        priced = pricing.price_with(usage("token"), cards, no_fx)
        assert priced.unit_price == Decimal("0.13125")
        assert priced.rate_card_id == cards[0].id and priced.blend_card_id == cards[1].id
        assert priced.price_estimated and priced.card_unit == "blend"
        mixed = [card("1m_input_tokens", "1"), card("1m_output_tokens", "2", currency="EUR")]
        assert pricing.select_price(usage("token"), mixed) is None  # no pair of one currency

    def test_unknown_record_unit_selects_nothing(self):
        assert pricing.select_price(usage("furlong"), [card()]) is None


class TestFallbackAndInHouse:
    def test_fallback_uses_price_for_list_and_override_and_marks_source(self, monkeypatch):
        monkeypatch.setattr(settings, "model_price_overrides_json", "")
        listed = pricing.price_with(usage(), [], no_fx)
        assert listed.price_source == "fallback_list" and listed.unit_price == Decimal("2.5")
        assert listed.currency == "USD" and listed.rate_card_id is None and listed.unconverted
        out = pricing.price_with(usage("output_token"), [], no_fx)
        assert out.unit_price == Decimal("10.0")
        blend = pricing.price_with(usage("token"), [], no_fx)
        assert blend.unit_price == Decimal("4.375") and blend.price_estimated
        monkeypatch.setattr(settings, "model_price_overrides_json", '{"openai/gpt-4o": {"input": 1.1, "output": 2.2}}')
        override = pricing.price_with(usage("cached_input_token"), [], no_fx)
        assert override.price_source == "fallback_override" and override.unit_price == Decimal("1.1")

    def test_fallback_is_for_llm_tokens_only(self):
        assert pricing.fallback_price(usage("embedding_token", usage_type="embedding_tokens")) is None

    def test_alias_prices_with_the_sku_card(self):
        aliases = {("openai", "gpt-4o-2024-08-06"): "gpt-4o"}
        model = pricing.canonical_model("openai", " GPT-4o-2024-08-06 ", aliases)
        assert model == "gpt-4o"
        sku_card = card(price="2.0", source="contract")
        assert pricing.price_with(usage(model=model), [sku_card], no_fx).rate_card_id == sku_card.id
        assert pricing.canonical_model("openai", "", aliases) == ""
        assert pricing.canonical_model("openai", "=weird name!", aliases) == "=weird name!"

    def test_alias_to_an_sku_without_a_price_keeps_the_called_models_list_price(self, monkeypatch):
        """An alias naming an SKU with no card and no list price must not unprice a call the list prices today."""
        monkeypatch.setattr(settings, "model_price_overrides_json", "")
        aliased = Usage(
            "openai", "llm_tokens", "input_token", Decimal("1000000"), "acme-gpt-4o", ON, ON, False, "gpt-4o"
        )
        priced = pricing.price_with(aliased, [], no_fx)
        assert priced.price_source == "fallback_list" and priced.amount == Decimal("2.5000000000")
        assert pricing.price_with(usage(model="acme-gpt-4o"), [], no_fx).unpriced  # no called name, no price
        # The SKU's own card, and the SKU's own override, still come first.
        sku_card = card(price="2.0", sku="acme-gpt-4o", source="contract")
        assert pricing.price_with(aliased, [sku_card], no_fx).rate_card_id == sku_card.id
        monkeypatch.setattr(
            settings, "model_price_overrides_json", '{"openai/acme-gpt-4o": {"input": 1.9, "output": 7.0}}'
        )
        overridden = pricing.price_with(aliased, [], no_fx)
        assert overridden.price_source == "fallback_override" and overridden.unit_price == Decimal("1.9")

    def test_local_provider_falls_back_to_in_house_zero(self, monkeypatch):
        monkeypatch.setattr(settings, "model_price_overrides_json", "")
        priced = pricing.price_with(usage(provider="ollama", model="llama3"), [], no_fx)
        assert priced.price_source == "in_house" and priced.amount == 0 and priced.currency == "INR"
        assert priced.amount_inr == 0 and not priced.unpriced

    def test_unknown_model_is_unpriced_never_zero(self):
        priced = pricing.price_with(usage(model="gpt-unknown"), [], no_fx)
        assert priced.unpriced and priced.amount is None and priced.price_source == "none"
        assert priced.flags == ["unpriced"]

    def test_unknown_gemini_model_is_unpriced_not_flash_priced(self):
        priced = pricing.price_with(usage(provider="gemini", model="gemini-2.0-flash-exp"), [], no_fx)
        assert priced.unpriced and priced.amount is None

    def test_in_house_ocr_and_whisper_are_zero_without_card(self):
        ocr = pricing.price_with(
            usage("ocr_page", "12", provider="tesseract", usage_type="ocr_pages", model=""), [], no_fx
        )
        speech = pricing.price_with(
            usage("audio_minute", "3.5", provider="faster_whisper", usage_type="speech_minutes", model=""), [], no_fx
        )
        for priced in (ocr, speech):
            assert priced.price_source == "in_house" and priced.amount == 0 and priced.amount_inr == 0

    def test_a_card_on_an_in_house_provider_wins(self):
        paid = card("ocr_page", "0.01", provider="tesseract", usage_type="ocr_pages", sku="", currency="INR")
        priced = pricing.price_with(
            usage("ocr_page", "100", provider="tesseract", usage_type="ocr_pages", model=""), [paid], no_fx
        )
        assert priced.price_source == "list" and priced.amount == Decimal("1.0000000000")

    def test_gpu_hours_and_storage_without_card_are_unpriced(self):
        gpu = pricing.price_with(
            usage("gpu_node_hour", "1", provider="vllm", usage_type="gpu_hours", model=""), [], no_fx
        )
        storage = pricing.price_with(
            usage("gb_day", "5", provider=vocab.STORAGE_PROVIDER, usage_type="storage", model=""), [], no_fx
        )
        assert gpu.unpriced and storage.unpriced


class TestMoney:
    def test_gb_day_priced_from_gb_month_by_days_in_billing_month(self):
        month = card("gb_month", "2.9", provider=vocab.STORAGE_PROVIDER, usage_type="storage", sku="")
        feb = usage(
            "gb_day", "10", provider=vocab.STORAGE_PROVIDER, usage_type="storage", model="", on=date(2028, 2, 10)
        )
        priced = pricing.price_with(feb, [month], no_fx)
        assert priced.amount == Decimal("1.0000000000") and priced.card_unit == "gb_month"
        day = card("gb_day", "0.05", provider=vocab.STORAGE_PROVIDER, usage_type="storage", sku="")
        assert pricing.price_with(feb, [month, day], no_fx).card_unit == "gb_day"

    def test_amount_quantised_to_ten_places_half_even(self):
        down = card("call", "0.00000000025", provider="acme_tools", usage_type="tool_calls", sku="")
        up = card("call", "0.00000000035", provider="acme_tools", usage_type="tool_calls", sku="")
        one = usage("call", "1", provider="acme_tools", usage_type="tool_calls", model="")
        assert pricing.price_with(one, [down], no_fx).amount == Decimal("0.0000000002")
        assert pricing.price_with(one, [up], no_fx).amount == Decimal("0.0000000004")

    def test_inr_card_converts_at_one(self):
        rupee = card(price="200", currency="INR")
        priced = pricing.price_with(usage(fx_on=date(2026, 10, 2)), [rupee], no_fx)
        assert priced.amount_inr == priced.amount == Decimal("200.0000000000")
        assert priced.fx_rate == 1 and priced.fx_rate_date == date(2026, 10, 2) and not priced.fx_estimated

    def test_fx_exact_date_not_estimated(self):
        build = rates_on({("USD", ON): "83.25"})
        priced = pricing.price_with(usage(), [card(price="2")], build(ON))
        assert priced.amount_inr == Decimal("166.5000000000") and not priced.fx_estimated
        assert priced.fx_rate_date == ON

    def test_fx_latest_earlier_rate_marks_estimated_and_keeps_rate_date(self):
        build = rates_on({("USD", date(2026, 9, 28)): "83", ("USD", date(2026, 9, 29)): "83.5"})
        priced = pricing.price_with(usage(), [card(price="2")], build(ON))
        assert priced.fx_estimated and priced.fx_rate_date == date(2026, 9, 29)
        assert priced.amount_inr == Decimal("167.0000000000") and priced.flags == ["fx_estimated"]

    def test_no_fx_rate_leaves_inr_null_and_marks_unconverted(self):
        priced = pricing.price_with(usage(), [card(price="2")], no_fx)
        assert priced.amount == Decimal("2.0000000000") and priced.amount_inr is None
        assert priced.unconverted and priced.fx_rate is None
        assert pricing.convert(Decimal("1"), "USD", ON, FxRate("USD", date(2026, 10, 2), Decimal("80")))[4]

    def test_zero_amount_converts_to_zero_without_rate(self):
        def refuse(currency: str) -> FxRate | None:
            raise AssertionError("no lookup for a zero amount")

        priced = pricing.price_with(usage(quantity="0"), [card(price="2")], refuse)
        assert priced.amount == 0 and priced.amount_inr == 0 and not priced.unconverted and not priced.fx_estimated

    def test_batch_discount_applies_only_to_batch_usage(self):
        discounted = card(price="2", batch="50")
        assert pricing.price_with(usage(), [discounted], no_fx).amount == Decimal("2.0000000000")
        assert pricing.price_with(usage(batch=True), [discounted], no_fx).amount == Decimal("1.0000000000")

    def test_priced_json_carries_decimal_text_and_flags(self):
        priced = pricing.price_with(usage(), [card(price="2")], rates_on({("USD", ON): "83"})(ON))
        out = pricing.priced_json(priced)
        assert out["amount"] == "2.0000000000" and out["amount_inr"] == "166.0000000000"
        assert out["fx_rate"] == "83" and out["fx_rate_date"] == "2026-10-01" and out["flags"] == []
        assert out["rate_card_id"] and out["blend_card_id"] is None
        assert pricing.priced_json(pricing.unpriced())["flags"] == ["unpriced"]


class TestTiers:
    TIERS = (Tier(Decimal("0"), Decimal("10")), Tier(Decimal("100"), Decimal("8")), Tier(Decimal("200"), Decimal("5")))

    def test_tiered_amount_graduated_from_a_start_position(self):
        assert pricing.tiered_amount(self.TIERS, "graduated", Decimal("50"), Decimal("200")) == Decimal("1550")
        assert pricing.tiered_amount(self.TIERS, "graduated", Decimal("0"), Decimal("100")) == Decimal("1000")
        assert pricing.tiered_amount(self.TIERS, "graduated", Decimal("0"), Decimal("0")) == 0
        assert pricing.tiered_amount((), "graduated", Decimal("0"), Decimal("5")) == 0

    def test_tiered_amount_all_units_uses_the_month_total_tier(self):
        assert pricing.tiered_amount(self.TIERS, "all_units", Decimal("150"), Decimal("100")) == Decimal("500")
        # a total of exactly 200 has not passed into the third tier
        assert pricing.tiered_amount(self.TIERS, "all_units", Decimal("100"), Decimal("100")) == Decimal("800")
        assert pricing.tiered_amount(self.TIERS, "all_units", Decimal("0"), Decimal("10")) == Decimal("100")

    def test_validate_tiers_refuses_unsorted_negative_or_unbounded(self):
        good = rates.validate_tiers(
            [{"from_quantity": "0", "unit_price": "2.50"}, {"from_quantity": "1e3", "unit_price": "2"}],
            mode="graduated",
        )
        assert good == [{"from_quantity": "0", "unit_price": "2.50"}, {"from_quantity": "1000", "unit_price": "2"}]
        assert rates.validate_tiers('[{"from_quantity": 0, "unit_price": 1}]', mode="all_units")[0]["unit_price"] == "1"
        assert rates.validate_tiers(None, mode="graduated") == []
        bad = [
            [{"from_quantity": "5", "unit_price": "1"}],  # does not start at 0
            [{"from_quantity": "0", "unit_price": "1"}, {"from_quantity": "0", "unit_price": "1"}],  # not ascending
            [{"from_quantity": "0", "unit_price": "-1"}],
            [{"from_quantity": "0", "unit_price": "1e10"}],
            [{"from_quantity": "0", "unit_price": "1"}, {"from_quantity": "1e16", "unit_price": "1"}],
            [{"from_quantity": str(i), "unit_price": "1"} for i in range(21)],
            "not json",
            {"from_quantity": "0"},
            ["x"],
        ]
        for raw in bad:
            with pytest.raises(SpendError) as info:
                rates.validate_tiers(raw, mode="graduated")
            assert info.value.code == "invalid_number"
        with pytest.raises(SpendError):
            rates.validate_tiers([], mode="stepped")
        assert pricing.parse_tiers(good) == (Tier(Decimal("0"), Decimal("2.5")), Tier(Decimal("1000"), Decimal("2")))


class TestNumbers:
    def test_parse_decimal_refuses_huge_exponent_without_error(self):
        for raw in ("1e100000", "1e-100000", "1e1000000", "NaN", "Infinity", "-1", "abc", "", True, None, "9" * 70):
            with pytest.raises(SpendError) as info:
                vocab.parse_decimal(raw, field="unit_price", minimum=0, maximum=vocab.MAX_UNIT_PRICE, places=10)
            assert info.value.status == 422 and info.value.code == "invalid_number"
        assert vocab.parse_decimal("2.50", field="x", minimum=0, maximum=10, places=1) == Decimal("2.5")
        assert vocab.parse_decimal(Decimal("0.000"), field="x", minimum=0, places=0) == 0
        assert vocab.parse_decimal(7, field="x", places=0) == 7
        assert vocab.parse_decimal(0.1, field="x", places=1) == Decimal("0.1")
        with pytest.raises(SpendError):
            vocab.parse_decimal("0", field="x", minimum=0, strict_minimum=True, places=2)
        with pytest.raises(SpendError):
            vocab.parse_decimal("1.234", field="x", places=2)
