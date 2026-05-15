from __future__ import annotations

import pytest

from core.commerce.staging_evidence import redact_value
from core.commerce.staging_runtime import RealStagingConfigError, validate_real_staging_config
from demos import commerce_sales_agent_demo


def _env(**overrides: str) -> dict[str, str]:
    base = {
        "AGENTICORG_TEST_FAKE_CONNECTORS": "0",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_real_staging_refuses_production_before_connector_auth_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_connector(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("connector should not be created")

    monkeypatch.setattr(commerce_sales_agent_demo, "GrantexCommerceConnector", fail_connector)
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_CONNECTORS", "0")

    with pytest.raises(RealStagingConfigError) as excinfo:
        await commerce_sales_agent_demo.run_real_staging_demo(grantex_base_url="https://api.grantex.dev")

    assert excinfo.value.code == "production_url_refused"


@pytest.mark.asyncio
async def test_real_staging_refuses_arbitrary_run_app_before_connector_auth_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_connector(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("connector should not be created")

    monkeypatch.setattr(commerce_sales_agent_demo, "GrantexCommerceConnector", fail_connector)
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_CONNECTORS", "0")

    with pytest.raises(RealStagingConfigError) as excinfo:
        await commerce_sales_agent_demo.run_real_staging_demo(grantex_base_url="https://example.run.app")

    assert excinfo.value.code == "smoke_url_not_allowlisted"


@pytest.mark.asyncio
async def test_real_staging_refuses_localhost_http_before_connector_auth_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_connector(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("connector should not be created")

    monkeypatch.setattr(commerce_sales_agent_demo, "GrantexCommerceConnector", fail_connector)
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_CONNECTORS", "0")

    with pytest.raises(RealStagingConfigError) as excinfo:
        await commerce_sales_agent_demo.run_real_staging_demo(grantex_base_url="http://localhost:3001")

    assert excinfo.value.code == "non_https_url_refused"


def test_real_staging_allows_exact_smoke_url_only_with_allowlist() -> None:
    smoke_url = "https://grantex-auth-smoke-example-uc.a.run.app"
    config = validate_real_staging_config(
        grantex_base_url=smoke_url,
        allow_smoke_cloud_run_url=smoke_url,
        environ=_env(GRANTEX_AGENT_ASSERTION="present-but-not-printed"),
    )

    assert config.grantex_base_url == smoke_url
    assert config.auth_env_name == "GRANTEX_AGENT_ASSERTION"


def test_real_staging_requires_exactly_one_auth_env_name() -> None:
    with pytest.raises(RealStagingConfigError) as missing:
        validate_real_staging_config(
            grantex_base_url="https://api-staging.grantex.dev",
            environ=_env(),
        )
    assert missing.value.code == "staging_auth_required"

    with pytest.raises(RealStagingConfigError) as ambiguous:
        validate_real_staging_config(
            grantex_base_url="https://api-staging.grantex.dev",
            environ=_env(GRANTEX_AGENT_ASSERTION="one", GRANTEX_API_KEY="two"),
        )
    assert ambiguous.value.code == "ambiguous_staging_auth"


def test_real_staging_refuses_fake_connector_transport() -> None:
    with pytest.raises(RealStagingConfigError) as excinfo:
        validate_real_staging_config(
            grantex_base_url="https://api-staging.grantex.dev",
            environ=_env(AGENTICORG_TEST_FAKE_CONNECTORS="1", GRANTEX_AGENT_ASSERTION="present"),
        )

    assert excinfo.value.code == "fake_connectors_refused"


def test_redaction_removes_auth_passport_idempotency_and_raw_payload_material() -> None:
    redacted = redact_value(
        {
            "authorization": "Bearer secret-token",
            "passport_jwt": "secret-passport",
            "idempotency_key": "secret-idempotency",
            "request_payload": {"nested": "raw"},
            "provider": {"credential": "secret-provider-material"},
            "safe": {"status": "pass"},
        }
    )

    assert redacted["authorization"] == "[redacted]"
    assert redacted["passport_jwt"] == "[redacted]"
    assert redacted["idempotency_key"] == "[redacted]"
    assert redacted["request_payload"] == "[redacted]"
    assert redacted["provider"]["credential"] == "[redacted]"
    assert redacted["safe"]["status"] == "pass"
