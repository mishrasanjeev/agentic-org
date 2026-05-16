from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

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


def _fixture_env_content(
    *,
    amount: int = 100,
    cap: int = 200,
    include_browse_passport: bool = True,
    include_amount_metadata: bool = True,
    include_cap_metadata: bool = True,
    sensitive_values: dict[str, str] | None = None,
) -> str:
    smoke_url = "https://grantex-auth-smoke-example-uc.a.run.app"
    runtime_values = {
        "GRANTEX_API_KEY": f"auth-{uuid.uuid4().hex}",
        "AGENTICORG_COMMERCE_CHECKOUT_PASSPORT_JWT": f"checkout-{uuid.uuid4().hex}",
        "AGENTICORG_COMMERCE_BROWSE_PASSPORT_JWT": f"browse-{uuid.uuid4().hex}",
        **(sensitive_values or {}),
    }
    lines = [
        f'GRANTEX_COMMERCE_BASE_URL="{smoke_url}"',
        f'AGENTICORG_COMMERCE_ALLOWED_SMOKE_URL="{smoke_url}"',
        'AGENTICORG_COMMERCE_REAL_STAGING="1"',
        'AGENTICORG_COMMERCE_FIXTURE_PROVIDER="mock"',
        'AGENTICORG_COMMERCE_FIXTURE_MERCHANT_ID="mch_staging_electronics_pilot"',
        'AGENTICORG_COMMERCE_FIXTURE_AGENT_ID="cag_staging_agenticorg_sales"',
        'AGENTICORG_COMMERCE_FIXTURE_PRODUCT_ID="cprd_stg_fixture"',
        'AGENTICORG_COMMERCE_FIXTURE_VARIANT_ID="cvar_stg_fixture"',
        'AGENTICORG_COMMERCE_FIXTURE_CURRENCY="INR"',
        f'GRANTEX_API_KEY="{runtime_values["GRANTEX_API_KEY"]}"',
        f'AGENTICORG_COMMERCE_CHECKOUT_PASSPORT_JWT="{runtime_values["AGENTICORG_COMMERCE_CHECKOUT_PASSPORT_JWT"]}"',
    ]
    if include_amount_metadata:
        lines.append(f'AGENTICORG_COMMERCE_FIXTURE_AMOUNT_MINOR_UNITS="{amount}"')
    if include_cap_metadata:
        lines.append(f'AGENTICORG_COMMERCE_FIXTURE_PASSPORT_MAX_AMOUNT_MINOR_UNITS="{cap}"')
    if include_browse_passport:
        lines.append(
            f'AGENTICORG_COMMERCE_BROWSE_PASSPORT_JWT="'
            f'{runtime_values["AGENTICORG_COMMERCE_BROWSE_PASSPORT_JWT"]}"'
        )
    return "\n".join([*lines, ""])


class _FakeRealStagingConnector:
    instances: list[_FakeRealStagingConnector] = []

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.calls: list[tuple[str, dict[str, Any]]] = []
        _FakeRealStagingConnector.instances.append(self)

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def health_check(self) -> dict[str, Any]:
        return {"status": "healthy", "http_status": 200}

    async def merchant_get_profile(self, **params: Any) -> dict[str, Any]:
        self.calls.append(("merchant_get_profile", params))
        return {"data": {"merchant_id": params["merchant_id"], "commerce_status": "enabled"}}

    async def catalog_search(self, **params: Any) -> dict[str, Any]:
        self.calls.append(("catalog_search", params))
        return {"data": {"items": [{"product_id": "cprd_stg_fixture"}]}}

    async def catalog_get_item(self, **params: Any) -> dict[str, Any]:
        self.calls.append(("catalog_get_item", params))
        return {
            "data": {
                "product_id": params["product_id"],
                "variants": [{"variant_id": "cvar_stg_fixture", "price_amount": 100}],
            }
        }

    async def inventory_check(self, **params: Any) -> dict[str, Any]:
        self.calls.append(("inventory_check", params))
        return {"data": {"items": [{"variant_id": "cvar_stg_fixture", "status": "in_stock"}]}}

    async def cart_create(self, **params: Any) -> dict[str, Any]:
        self.calls.append(("cart_create", params))
        return {"data": {"cart_id": "cart_stg_fixture", "status": "draft"}}

    async def consent_request(self, **params: Any) -> dict[str, Any]:
        self.calls.append(("consent_request", params))
        return {"data": {"consent_request_id": "consent_stg_fixture", "passport_type": params["passport_type"]}}

    async def consent_exchange(self, **params: Any) -> dict[str, Any]:
        self.calls.append(("consent_exchange", params))
        return {"data": {}}

    async def payment_create_intent(self, **params: Any) -> dict[str, Any]:
        self.calls.append(("payment_create_intent", params))
        if not params.get("passport_jwt"):
            return {"error": "consent_required", "message": "passport required"}
        if params.get("consent_status") == "denied":
            return {"error": "consent_denied", "message": "consent denied"}
        if params.get("passport_status") == "revoked":
            return {"error": "passport_revoked", "message": "passport revoked"}
        if params.get("passport_status") == "expired":
            return {"error": "passport_expired", "message": "passport expired"}
        if params.get("merchant_status") == "disabled":
            return {"error": "merchant_disabled", "message": "merchant disabled"}
        if params.get("agent_trust_status") == "untrusted":
            return {"error": "agent_untrusted", "message": "agent untrusted"}
        if "passport_max_amount_minor_units" in params and (
            params.get("amount_minor_units", 0) > params.get("passport_max_amount_minor_units", 0)
        ):
            return {"error": "amount_cap_exceeded", "message": "amount cap exceeded"}
        return {"data": {"payment_intent_id": "pi_stg_fixture", "status": "requires_checkout"}}

    async def checkout_create(self, **params: Any) -> dict[str, Any]:
        self.calls.append(("checkout_create", params))
        return {"data": {"checkout_id": "checkout_stg_fixture", "status": "handoff_ready"}}

    async def payment_get_status(self, **params: Any) -> dict[str, Any]:
        self.calls.append(("payment_get_status", params))
        return {"data": {"payment_intent_id": params["payment_intent_id"], "status": "requires_checkout"}}


async def _run_real_staging_with_fake(
    monkeypatch: pytest.MonkeyPatch,
    fixture: Path,
    *,
    evidence_report: Path | None = None,
) -> tuple[dict[str, Any], _FakeRealStagingConnector]:
    _FakeRealStagingConnector.instances = []
    monkeypatch.setattr(commerce_sales_agent_demo, "GrantexCommerceConnector", _FakeRealStagingConnector)
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_CONNECTORS", "0")
    result = await commerce_sales_agent_demo.run_real_staging_demo(
        fixture_env_path=str(fixture),
        evidence_report=str(evidence_report) if evidence_report else None,
    )
    return result, _FakeRealStagingConnector.instances[0]


_LOCAL_PAYMENT_GUARD_KEYS = (
    "consent_status",
    "passport_status",
    "merchant_status",
    "agent_trust_status",
    "passport_max_amount_minor_units",
)


def _positive_payment_calls(connector: _FakeRealStagingConnector) -> list[dict[str, Any]]:
    return [
        params
        for name, params in connector.calls
        if name == "payment_create_intent"
        and params.get("cart_id") == "cart_stg_fixture"
        and params.get("passport_jwt")
        and not any(key in params for key in _LOCAL_PAYMENT_GUARD_KEYS)
    ]


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


@pytest.mark.asyncio
async def test_real_staging_inventory_passes_browse_passport_from_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browse_passport = f"browse-{uuid.uuid4().hex}"
    fixture = _write_tmp_fixture(
        "commerce-agent-real-staging-c2e-inventory.env",
        _fixture_env_content(
            sensitive_values={"AGENTICORG_COMMERCE_BROWSE_PASSPORT_JWT": browse_passport},
        ),
    )
    try:
        result, connector = await _run_real_staging_with_fake(monkeypatch, fixture)
    finally:
        fixture.unlink(missing_ok=True)

    inventory_call = next(params for name, params in connector.calls if name == "inventory_check")
    assert inventory_call["passport_jwt"] == browse_passport
    assert "grantex_commerce:inventory_check" in result["audit_summary"]["tool_sequence"]


@pytest.mark.asyncio
async def test_real_staging_skips_inventory_when_browse_passport_fixture_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _write_tmp_fixture(
        "commerce-agent-real-staging-c2e-inventory-missing.env",
        _fixture_env_content(include_browse_passport=False),
    )
    report = Path(".tmp/commerce-agent-real-staging-c2e-inventory-missing.md")
    try:
        result, connector = await _run_real_staging_with_fake(monkeypatch, fixture, evidence_report=report)
        report_text = report.read_text(encoding="utf-8")
    finally:
        fixture.unlink(missing_ok=True)
        report.unlink(missing_ok=True)

    assert not any(name == "inventory_check" for name, _params in connector.calls)
    assert "grantex_commerce:inventory_check" not in result["audit_summary"]["tool_sequence"]
    assert "| inventory_check | skipped |  |  |  |  | requires browse passport fixture |" in report_text


@pytest.mark.asyncio
async def test_real_staging_consent_request_uses_grantex_checkout_scope_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _write_tmp_fixture("commerce-agent-real-staging-c2e-consent.env", _fixture_env_content())
    try:
        _result, connector = await _run_real_staging_with_fake(monkeypatch, fixture)
    finally:
        fixture.unlink(missing_ok=True)

    consent_call = next(params for name, params in connector.calls if name == "consent_request")
    assert consent_call["requested_scopes"] == commerce_sales_agent_demo.GRANTEX_CHECKOUT_CONSENT_SCOPES


@pytest.mark.asyncio
async def test_real_staging_positive_payment_sends_only_grantex_supported_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _write_tmp_fixture(
        "commerce-agent-real-staging-c2f-payment.env",
        _fixture_env_content(amount=100, cap=200),
    )
    try:
        result, connector = await _run_real_staging_with_fake(monkeypatch, fixture)
    finally:
        fixture.unlink(missing_ok=True)

    positive_payment_calls = _positive_payment_calls(connector)
    assert len(positive_payment_calls) == 1
    payment_request = positive_payment_calls[0]
    assert set(payment_request) <= set(commerce_sales_agent_demo.GRANTEX_PAYMENT_CREATE_INTENT_FIELDS)
    assert set(payment_request) == {
        "merchant_id",
        "cart_id",
        "passport_jwt",
        "amount_minor_units",
        "currency",
        "provider_key",
        "idempotency_key",
    }
    assert "passport_max_amount_minor_units" not in payment_request
    assert "grantex_commerce:payment_create_intent" in result["audit_summary"]["tool_sequence"]


@pytest.mark.asyncio
async def test_real_staging_skips_positive_payment_when_fixture_cap_metadata_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _write_tmp_fixture(
        "commerce-agent-real-staging-c2f-missing-cap.env",
        _fixture_env_content(include_cap_metadata=False),
    )
    report = Path(".tmp/commerce-agent-real-staging-c2f-missing-cap.md")
    try:
        result, connector = await _run_real_staging_with_fake(monkeypatch, fixture, evidence_report=report)
        report_text = report.read_text(encoding="utf-8")
    finally:
        fixture.unlink(missing_ok=True)
        report.unlink(missing_ok=True)

    assert _positive_payment_calls(connector) == []
    assert "grantex_commerce:payment_create_intent" not in result["audit_summary"]["tool_sequence"]
    assert (
        "| payment_create_intent | skipped |  |  |  |  | requires fixture amount and passport cap metadata |"
        in report_text
    )


@pytest.mark.asyncio
async def test_real_staging_amount_cap_negative_uses_local_guardrail_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _write_tmp_fixture(
        "commerce-agent-real-staging-c2f-negative-amount-cap.env",
        _fixture_env_content(amount=100, cap=200),
    )
    report = Path(".tmp/commerce-agent-real-staging-c2f-negative-amount-cap.md")
    try:
        _result, connector = await _run_real_staging_with_fake(monkeypatch, fixture, evidence_report=report)
        report_text = report.read_text(encoding="utf-8")
    finally:
        fixture.unlink(missing_ok=True)
        report.unlink(missing_ok=True)

    assert not any(
        name == "payment_create_intent" and params.get("passport_max_amount_minor_units") == 1
        for name, params in connector.calls
    )
    assert not any(
        name == "payment_create_intent" and params.get("cart_id") == "staging-cart-for-amount-cap"
        for name, params in connector.calls
    )
    assert "| amount_cap_breach | pass |  |  |  | amount_cap_exceeded |  |" in report_text


@pytest.mark.asyncio
async def test_real_staging_skips_positive_payment_when_fixture_amount_exceeds_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _write_tmp_fixture(
        "commerce-agent-real-staging-c2e-amount-cap.env",
        _fixture_env_content(amount=300, cap=200),
    )
    report = Path(".tmp/commerce-agent-real-staging-c2e-amount-cap.md")
    try:
        result, connector = await _run_real_staging_with_fake(monkeypatch, fixture, evidence_report=report)
        report_text = report.read_text(encoding="utf-8")
    finally:
        fixture.unlink(missing_ok=True)
        report.unlink(missing_ok=True)

    assert _positive_payment_calls(connector) == []
    assert not any(
        name == "payment_create_intent" and params.get("passport_max_amount_minor_units") == 1
        for name, params in connector.calls
    )
    assert "grantex_commerce:payment_create_intent" not in result["audit_summary"]["tool_sequence"]
    assert "| payment_create_intent | skipped |  |  |  |  | fixture amount exceeds passport cap |" in report_text
    assert "| amount_cap_breach | pass |  |  |  | amount_cap_exceeded |  |" in report_text


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
