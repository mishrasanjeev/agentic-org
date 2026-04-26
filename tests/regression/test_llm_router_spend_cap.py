"""Regression tests for the 3-tier Gemini router + spend cap + CLI.

Pin the contracts:

  1. CLOUD_TIERS routes high-volume traffic to flash-lite (tier1).
  2. ``GEMINI_PRICE_PER_1M`` lists current per-1M USD rates.
  3. ``gemini_cost_usd`` falls back to flash pricing for unknown
     models — never silently zero (would blind budget alerts).
  4. ``DailyBudgetExceeded`` fires when today's spend would exceed
     ``AGENTICORG_GEMINI_DAILY_USD_CAP``.
  5. The cap defaults to $10.
  6. ``core.billing.spend`` CLI exposes ``--period``, ``--provider``,
     ``--top-tenants``, ``--json``, ``--reconcile-gcp``.
"""

from __future__ import annotations

import io
import sys
from unittest.mock import patch

import pytest


def test_tier1_default_is_flash_lite() -> None:
    """Tier1 must be the cheapest model — flash-lite.

    Regressing this would silently route high-volume calls back to
    flash, ~2x the per-token cost.
    """
    from core.llm.router import CLOUD_TIERS

    assert CLOUD_TIERS["tier1"] == "gemini-2.5-flash-lite", (
        f"tier1 must default to gemini-2.5-flash-lite, got {CLOUD_TIERS['tier1']!r}"
    )
    assert CLOUD_TIERS["tier2"] == "gemini-2.5-flash"
    assert CLOUD_TIERS["tier3"] == "gemini-2.5-pro"


def test_pricing_table_includes_flash_lite_and_pro() -> None:
    from core.llm.router import GEMINI_PRICE_PER_1M

    for model in (
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ):
        assert model in GEMINI_PRICE_PER_1M, (
            f"missing pricing for {model} — cost calc will fall back to flash"
        )
        rates = GEMINI_PRICE_PER_1M[model]
        assert rates["input"] > 0
        assert rates["output"] > rates["input"], (
            f"{model}: output rate must exceed input rate (Gemini pricing convention)"
        )

    flash_lite = GEMINI_PRICE_PER_1M["gemini-2.5-flash-lite"]
    flash = GEMINI_PRICE_PER_1M["gemini-2.5-flash"]
    pro = GEMINI_PRICE_PER_1M["gemini-2.5-pro"]
    assert flash_lite["input"] < flash["input"], (
        "flash-lite must be cheaper than flash on input"
    )
    assert flash["input"] < pro["input"], (
        "flash must be cheaper than pro on input"
    )


def test_gemini_cost_usd_uses_known_pricing() -> None:
    from core.llm.router import gemini_cost_usd

    # 1M input + 1M output at flash-lite rates ($0.04 + $0.10)
    cost = gemini_cost_usd("gemini-2.5-flash-lite", 1_000_000, 1_000_000)
    assert cost == pytest.approx(0.14, abs=0.005)

    # 1M input + 1M output at pro rates ($1.25 + $5.00)
    cost_pro = gemini_cost_usd("gemini-2.5-pro", 1_000_000, 1_000_000)
    assert cost_pro == pytest.approx(6.25, abs=0.05)


def test_gemini_cost_usd_unknown_model_falls_back_nonzero() -> None:
    """Unknown model must NEVER return 0 — that would blind budget alerts."""
    from core.llm.router import gemini_cost_usd

    cost = gemini_cost_usd("gemini-3.0-experimental", 1_000_000, 1_000_000)
    assert cost > 0, "unknown model must fall back to a non-zero rate"


def test_daily_cap_defaults_to_10_usd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTICORG_GEMINI_DAILY_USD_CAP", raising=False)
    from core.llm.router import _gemini_daily_cap_usd

    assert _gemini_daily_cap_usd() == 10.0


def test_daily_cap_respects_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_GEMINI_DAILY_USD_CAP", "2.5")
    from core.llm.router import _gemini_daily_cap_usd

    assert _gemini_daily_cap_usd() == 2.5


def test_daily_cap_invalid_value_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad value in env shouldn't disable the cap entirely."""
    monkeypatch.setenv("AGENTICORG_GEMINI_DAILY_USD_CAP", "not-a-number")
    from core.llm.router import _gemini_daily_cap_usd

    assert _gemini_daily_cap_usd() == 10.0


def test_daily_cap_zero_disables_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow ops to disable the cap with `=0` for emergency burst capacity."""
    import asyncio

    monkeypatch.setenv("AGENTICORG_GEMINI_DAILY_USD_CAP", "0")
    from core.llm.router import assert_under_gemini_cap

    # Must not raise even with no DB / no spend lookup.
    asyncio.run(assert_under_gemini_cap())


def test_assert_under_gemini_cap_raises_when_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When today's spend already meets the cap, refuse new calls."""
    import asyncio

    monkeypatch.setenv("AGENTICORG_GEMINI_DAILY_USD_CAP", "5.0")

    async def _fake_spent() -> float:
        return 5.0

    with patch("core.llm.router._todays_gemini_spend_usd", new=_fake_spent):
        from core.llm.router import DailyBudgetExceeded, assert_under_gemini_cap

        with pytest.raises(DailyBudgetExceeded):
            asyncio.run(assert_under_gemini_cap())


def test_spend_cli_help_documents_flags() -> None:
    """Operators script around these flags — keep them stable."""
    from core.billing.spend import main

    captured = io.StringIO()
    sys.stdout = captured
    try:
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
    finally:
        sys.stdout = sys.__stdout__
    out = captured.getvalue()
    assert exc.value.code == 0
    for token in (
        "--period",
        "--provider",
        "--top-tenants",
        "--json",
        "--reconcile-gcp",
    ):
        assert token in out, f"spend --help must document {token}"


def test_spend_cli_period_choices() -> None:
    """Enforce daily / weekly / monthly periods."""
    from core.billing.spend import main

    sys.stderr = io.StringIO()
    try:
        with pytest.raises(SystemExit) as exc:
            main(["--period=hourly"])  # invalid
        assert exc.value.code != 0
    finally:
        sys.stderr = sys.__stderr__
