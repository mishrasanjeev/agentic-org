from __future__ import annotations

import pathlib
import tomllib

import pytest


def test_stripe_sdk_is_a_production_dependency() -> None:
    pyproject = tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]

    assert any(dep.lower().startswith("stripe>=") for dep in dependencies)


@pytest.mark.asyncio
async def test_billing_health_refuses_ready_when_stripe_sdk_missing(monkeypatch) -> None:
    from api.v1 import billing

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_configured")
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro")
    monkeypatch.setenv("STRIPE_PRICE_ENTERPRISE", "price_enterprise")
    monkeypatch.delenv("PINELABS_API_KEY", raising=False)
    monkeypatch.delenv("PLURAL_API_KEY", raising=False)
    monkeypatch.setattr(billing, "_is_module_installed", lambda name: False)

    result = await billing.billing_health()

    assert result["stripe_secret_configured"] is True
    assert result["stripe_prices_configured"] is True
    assert result["stripe_sdk_installed"] is False
    assert result["stripe_configured"] is False
    assert result["ready_for_release"] is False
    assert "missing the stripe Python package" in result["recommended_checkout_flow"]


@pytest.mark.asyncio
async def test_billing_health_requires_stripe_price_ids(monkeypatch) -> None:
    from api.v1 import billing

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_configured")
    monkeypatch.delenv("STRIPE_PRICE_PRO", raising=False)
    monkeypatch.setenv("STRIPE_PRICE_ENTERPRISE", "price_enterprise")
    monkeypatch.delenv("PINELABS_API_KEY", raising=False)
    monkeypatch.delenv("PLURAL_API_KEY", raising=False)
    monkeypatch.setattr(billing, "_is_module_installed", lambda name: True)

    result = await billing.billing_health()

    assert result["stripe_sdk_installed"] is True
    assert result["stripe_prices_configured"] is False
    assert result["stripe_configured"] is False
    assert result["ready_for_release"] is False
