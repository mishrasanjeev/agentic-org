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
    monkeypatch.delenv("PLURAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLURAL_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(billing, "_is_module_installed", lambda name: False)

    result = await billing.billing_health()

    assert result["stripe_secret_configured"] is True
    assert result["stripe_prices_configured"] is True
    assert result["stripe_sdk_installed"] is False
    assert result["stripe_configured"] is False
    assert result["ready_for_release"] is False
    assert "missing the stripe Python package" in result["recommended_checkout_flow"]


@pytest.mark.asyncio
async def test_billing_health_accepts_dynamic_stripe_prices(monkeypatch) -> None:
    from api.v1 import billing

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_configured")
    monkeypatch.delenv("STRIPE_PRICE_PRO", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ENTERPRISE", raising=False)
    monkeypatch.delenv("PLURAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLURAL_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(billing, "_is_module_installed", lambda name: True)

    result = await billing.billing_health()

    assert result["stripe_sdk_installed"] is True
    assert result["stripe_price_ids_configured"] is False
    assert result["stripe_dynamic_prices_configured"] is True
    assert result["stripe_prices_configured"] is True
    assert result["stripe_configured"] is True
    assert result["ready_for_release"] is True


@pytest.mark.asyncio
async def test_billing_health_gates_plural_on_real_client_credentials(monkeypatch) -> None:
    """Audit #3: the legacy PINELABS_API_KEY/PLURAL_API_KEY names are never read by the
    Plural client; readiness must key off PLURAL_CLIENT_ID + PLURAL_CLIENT_SECRET."""
    from api.v1 import billing

    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.setenv("PINELABS_API_KEY", "legacy-name")
    monkeypatch.setenv("PLURAL_API_KEY", "legacy-name")
    monkeypatch.delenv("PLURAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLURAL_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(billing, "_is_module_installed", lambda name: True)

    result = await billing.billing_health()
    assert result["pinelabs_configured"] is False
    assert result["ready_for_release"] is False

    monkeypatch.setenv("PLURAL_CLIENT_ID", "cid")
    monkeypatch.setenv("PLURAL_CLIENT_SECRET", "csecret")
    result = await billing.billing_health()
    assert result["pinelabs_configured"] is True
    assert result["ready_for_release"] is True


@pytest.mark.asyncio
async def test_subscribe_india_503_without_real_plural_credentials(monkeypatch) -> None:
    from fastapi import HTTPException

    from api.v1 import billing

    monkeypatch.setenv("PINELABS_API_KEY", "legacy-name")
    monkeypatch.delenv("PLURAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLURAL_CLIENT_SECRET", raising=False)

    with pytest.raises(HTTPException) as exc_info:
        await billing.subscribe_india(
            billing.IndiaSubscribeRequest(plan="pro"), tenant_id="tenant-a"
        )
    assert exc_info.value.status_code == 503
    assert "PLURAL_CLIENT_ID" in exc_info.value.detail
