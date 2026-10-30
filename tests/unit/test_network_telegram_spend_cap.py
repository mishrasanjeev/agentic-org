# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The Telegram shopper and the Gemini daily caps.

The shopper belongs to no tenant. A check without a tenant applies the
per-tenant cap (``AGENTICORG_GEMINI_DAILY_USD_CAP``, $10) to every tenant's
spend combined, so once recorded spend was on, $10 of router spend across all
tenants would stop the bot for the rest of the UTC day. With
``AGENTICORG_RECORDED_SPEND_CONTROLS_ENABLED`` on, the shopper is held to the
platform cap (``AGENTICORG_GEMINI_PLATFORM_DAILY_USD_CAP``, $100) only. Off,
it keeps the previous check. Router calls without a tenant keep both caps.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import OperationalError

from core.commerce import network_telegram as bot
from core.config import settings
from core.llm import router
from core.test_doubles.scripted_model import ScriptedChatModel, final

SHOP_STATE = {"shop": "tea-corner", "shop_name": "Tea Corner", "turns": []}


@pytest.fixture(autouse=True)
def default_caps(monkeypatch):
    monkeypatch.delenv("AGENTICORG_GEMINI_DAILY_USD_CAP", raising=False)
    monkeypatch.delenv("AGENTICORG_GEMINI_PLATFORM_DAILY_USD_CAP", raising=False)


def _platform_spend(monkeypatch, usd: float) -> AsyncMock:
    spend = AsyncMock(return_value=usd)
    monkeypatch.setattr(router, "_todays_gemini_spend_usd", spend)
    return spend


def _answering_model() -> ScriptedChatModel:
    return ScriptedChatModel(steps=[final("Tea Corner has three teas.")])


async def test_platform_check_passes_above_the_per_tenant_cap(monkeypatch):
    spend = _platform_spend(monkeypatch, 15.0)

    await router.assert_under_gemini_platform_cap(0.0)
    spend.assert_awaited_once_with()

    # A router call without a tenant still has both caps: $15 combined passes $10.
    with pytest.raises(router.DailyBudgetExceeded, match="AGENTICORG_GEMINI_DAILY_USD_CAP"):
        await router.assert_under_gemini_cap(0.0)


@pytest.mark.parametrize(("spent", "estimate"), [(100.0, 0.0), (99.5, 0.6)])
async def test_platform_check_refuses_at_the_platform_cap(monkeypatch, spent, estimate):
    _platform_spend(monkeypatch, spent)

    with pytest.raises(router.DailyBudgetExceeded, match="AGENTICORG_GEMINI_PLATFORM_DAILY_USD_CAP"):
        await router.assert_under_gemini_platform_cap(estimate)


async def test_a_zero_platform_cap_turns_the_check_off(monkeypatch):
    monkeypatch.setenv("AGENTICORG_GEMINI_PLATFORM_DAILY_USD_CAP", "0")
    spend = _platform_spend(monkeypatch, 500.0)

    await router.assert_under_gemini_platform_cap(0.0)
    spend.assert_not_awaited()


async def test_platform_check_fails_closed_when_the_spend_cannot_be_read(monkeypatch):
    class _BrokenSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *args, **kwargs):
            raise OperationalError("SELECT public.gemini_daily_ledger_spend()", {}, Exception("down"))

    monkeypatch.setattr(settings, "recorded_spend_controls_enabled", True)
    monkeypatch.setattr("core.database.async_session_factory", lambda: _BrokenSession())

    with pytest.raises(router.DailyBudgetExceeded):
        await router.assert_under_gemini_platform_cap(0.0)


async def test_shopper_is_held_to_the_platform_cap_with_recorded_spend(monkeypatch):
    monkeypatch.setattr(settings, "recorded_spend_controls_enabled", True)
    spend = _platform_spend(monkeypatch, 15.0)

    # $15 across all tenants used to stop the bot; it now answers.
    reply = await bot.run_shopper(dict(SHOP_STATE), "show me your teas", model_factory=_answering_model)
    assert reply == "Tea Corner has three teas."
    spend.assert_awaited_once_with()

    # At the platform cap the turn is refused before the model is called.
    _platform_spend(monkeypatch, 100.0)
    model = ScriptedChatModel(steps=[])
    with pytest.raises(router.DailyBudgetExceeded, match="AGENTICORG_GEMINI_PLATFORM_DAILY_USD_CAP"):
        await bot.run_shopper(dict(SHOP_STATE), "show me your teas", model_factory=lambda: model)
    assert model.calls == []


async def test_shopper_keeps_the_previous_check_with_recorded_spend_off(monkeypatch):
    monkeypatch.setattr(settings, "recorded_spend_controls_enabled", False)
    _platform_spend(monkeypatch, 15.0)

    with pytest.raises(router.DailyBudgetExceeded, match="AGENTICORG_GEMINI_DAILY_USD_CAP"):
        await bot.run_shopper(dict(SHOP_STATE), "show me your teas", model_factory=_answering_model)
