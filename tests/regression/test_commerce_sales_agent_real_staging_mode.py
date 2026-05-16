from __future__ import annotations

from pathlib import Path

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


def _write_tmp_fixture(name: str, content: str) -> Path:
    path = Path(".tmp") / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


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


def test_real_staging_loads_fixture_env_from_tmp_only() -> None:
    smoke_url = "https://grantex-auth-smoke-example-uc.a.run.app"
    fixture = _write_tmp_fixture(
        "commerce-agent-real-staging-test.env",
        "\n".join(
            [
                f'GRANTEX_COMMERCE_BASE_URL="{smoke_url}"',
                f'GRANTEX_BASE_URL="{smoke_url}"',
                f'AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL="{smoke_url}"',
                'AGENTICORG_COMMERCE_REAL_STAGING="1"',
                'AGENTICORG_COMMERCE_FIXTURE_PROVIDER="mock"',
                'AGENTICORG_COMMERCE_FIXTURE_MERCHANT_ID="mch_staging_electronics_pilot"',
                'AGENTICORG_COMMERCE_FIXTURE_AGENT_ID="cag_staging_agenticorg_sales"',
                'AGENTICORG_COMMERCE_FIXTURE_PRODUCT_ID="cprd_stg_fixture"',
                'AGENTICORG_COMMERCE_FIXTURE_VARIANT_ID="cvar_stg_fixture"',
                'GRANTEX_API_KEY="fixture-value-for-test"',
                'AGENTICORG_COMMERCE_CHECKOUT_PASSPORT_JWT="fixture-value-for-test"',
                "",
            ]
        ),
    )
    try:
        config = validate_real_staging_config(fixture_env_path=str(fixture), environ=_env())
    finally:
        fixture.unlink(missing_ok=True)

    assert config.grantex_base_url == smoke_url
    assert config.auth_env_name == "GRANTEX_API_KEY"
    assert config.auth_config_key == "api_key"
    assert config.fixture.env_path == ".tmp/commerce-agent-real-staging-test.env"
    assert config.fixture.product_id == "cprd_stg_fixture"
    assert config.fixture.variant_id == "cvar_stg_fixture"
    assert config.fixture.sensitive_value_hashes


def test_real_staging_refuses_fixture_env_outside_tmp() -> None:
    with pytest.raises(RealStagingConfigError) as excinfo:
        validate_real_staging_config(
            fixture_env_path="docs/commerce-agent-real-staging.env",
            environ=_env(),
        )

    assert excinfo.value.code == "fixture_env_outside_tmp"


def test_real_staging_fixture_preserves_exactly_one_auth_source_rule() -> None:
    smoke_url = "https://grantex-auth-smoke-example-uc.a.run.app"
    fixture = _write_tmp_fixture(
        "commerce-agent-real-staging-ambiguous.env",
        "\n".join(
            [
                f'GRANTEX_COMMERCE_BASE_URL="{smoke_url}"',
                f'AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL="{smoke_url}"',
                'GRANTEX_API_KEY="fixture-value-for-test"',
                'GRANTEX_AGENT_ASSERTION="fixture-value-for-test"',
                "",
            ]
        ),
    )
    try:
        with pytest.raises(RealStagingConfigError) as excinfo:
            validate_real_staging_config(fixture_env_path=str(fixture), environ=_env())
    finally:
        fixture.unlink(missing_ok=True)

    assert excinfo.value.code == "ambiguous_staging_auth"


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
    auth_header = "Bearer " + "fixture-redaction-token"
    redacted = redact_value(
        {
            "authorization": auth_header,
            "passport_jwt": "fixture-redaction-passport",
            "idempotency_key": "fixture-redaction-idempotency",
            "request_payload": {"nested": "raw"},
            "provider": {"credential": "fixture-redaction-provider-material"},
            "safe": {"status": "pass"},
        }
    )

    assert redacted["authorization"] == "[redacted]"
    assert redacted["passport_jwt"] == "[redacted]"
    assert redacted["idempotency_key"] == "[redacted]"
    assert redacted["request_payload"] == "[redacted]"
    assert redacted["provider"]["credential"] == "[redacted]"
    assert redacted["safe"]["status"] == "pass"
