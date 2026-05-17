from __future__ import annotations

import json
import re
import uuid
from argparse import Namespace
from pathlib import Path

import httpx
import pytest

from scripts import commerce_agent_hosted_smoke as runner

AGENTICORG_SMOKE = "https://agenticorg-api-commerce-smoke-example-uc.a.run.app"
GRANTEX_SMOKE = "https://grantex-auth-smoke-example-uc.a.run.app"


def _args(*extra: str) -> Namespace:
    return runner.build_parser().parse_args(
        [
            "--agenticorg-base",
            AGENTICORG_SMOKE,
            "--allow-agenticorg-cloud-run-url",
            AGENTICORG_SMOKE,
            "--grantex-base",
            GRANTEX_SMOKE,
            "--allow-grantex-cloud-run-url",
            GRANTEX_SMOKE,
            "--auth-source-env-name",
            "GRANTEX_API_KEY",
            *extra,
        ]
    )


def _validate(*extra: str, environ: dict[str, str] | None = None) -> runner.HostedSmokeConfig:
    return runner.validate_config(_args(*extra), environ=environ or {})


def _write_tmp_fixture(name: str, *, auth_key: str = "GRANTEX_API_KEY") -> Path:
    path = Path(".tmp") / name
    path.parent.mkdir(exist_ok=True)
    lines = [
        f'{auth_key}="auth-{uuid.uuid4().hex}"',
        f'AGENTICORG_COMMERCE_CHECKOUT_PASSPORT_JWT="checkout-{uuid.uuid4().hex}"',
        'AGENTICORG_COMMERCE_FIXTURE_MERCHANT_ID="mch_staging_electronics_pilot"',
        'AGENTICORG_COMMERCE_FIXTURE_AGENT_ID="cag_staging_agenticorg_sales"',
        'AGENTICORG_COMMERCE_FIXTURE_PRODUCT_ID="cprd_smoke_fixture"',
        'AGENTICORG_COMMERCE_FIXTURE_VARIANT_ID="cvar_smoke_fixture"',
    ]
    path.write_text("\n".join([*lines, ""]), encoding="utf-8")
    return path


def test_dry_run_is_default_and_validates_smoke_inputs() -> None:
    args = _args()
    config = runner.validate_config(args, environ={})

    assert args.run is False
    assert config.agenticorg_base_url == AGENTICORG_SMOKE
    assert config.grantex_base_url == GRANTEX_SMOKE
    assert config.auth_source_env_name == "GRANTEX_API_KEY"


@pytest.mark.parametrize(
    ("flag", "value", "code"),
    [
        ("--agenticorg-base", "https://app.agenticorg.ai", "agenticorg_production_url_refused"),
        ("--grantex-base", "https://api.grantex.dev", "grantex_production_url_refused"),
        ("--agenticorg-base", "http://localhost:8000", "agenticorg_non_https_url_refused"),
        ("--grantex-base", "http://localhost:3001", "grantex_non_https_url_refused"),
    ],
)
def test_url_refusals_fail_before_network(flag: str, value: str, code: str) -> None:
    base_args = [
        "--agenticorg-base",
        AGENTICORG_SMOKE,
        "--allow-agenticorg-cloud-run-url",
        AGENTICORG_SMOKE,
        "--grantex-base",
        GRANTEX_SMOKE,
        "--allow-grantex-cloud-run-url",
        GRANTEX_SMOKE,
        "--auth-source-env-name",
        "GRANTEX_API_KEY",
    ]
    idx = base_args.index(flag) + 1
    base_args[idx] = value
    with pytest.raises(runner.HostedSmokeConfigError) as excinfo:
        runner.validate_config(runner.build_parser().parse_args(base_args), environ={})

    assert excinfo.value.code == code


def test_arbitrary_run_app_is_refused_without_exact_allowlist() -> None:
    args = runner.build_parser().parse_args(
        [
            "--agenticorg-base",
            "https://agenticorg-api-commerce-smoke-example-uc.a.run.app",
            "--allow-agenticorg-cloud-run-url",
            "https://different-agenticorg-smoke-uc.a.run.app",
            "--grantex-base",
            GRANTEX_SMOKE,
            "--allow-grantex-cloud-run-url",
            GRANTEX_SMOKE,
            "--auth-source-env-name",
            "GRANTEX_API_KEY",
        ]
    )
    with pytest.raises(runner.HostedSmokeConfigError) as excinfo:
        runner.validate_config(args, environ={})

    assert excinfo.value.code == "agenticorg_smoke_url_not_allowlisted"


def test_live_flags_are_refused() -> None:
    with pytest.raises(runner.HostedSmokeConfigError) as excinfo:
        _validate(environ={"COMMERCE_LIVE_MODE_ENABLED": "true"})

    assert excinfo.value.code == "live_flags_refused"

    with pytest.raises(runner.HostedSmokeConfigError) as cli_excinfo:
        _validate("--plural-live-mode")

    assert cli_excinfo.value.code == "live_flags_refused"


def test_fixture_env_must_stay_in_tmp_and_match_single_auth_source() -> None:
    with pytest.raises(runner.HostedSmokeConfigError) as outside:
        _validate("--fixture-env", "docs/commerce-agent-real-staging.env")
    assert outside.value.code == "fixture_env_outside_tmp"

    fixture = _write_tmp_fixture("commerce-agent-c3-hosted-smoke.env", auth_key="GRANTEX_AGENT_ASSERTION")
    try:
        with pytest.raises(runner.HostedSmokeConfigError) as mismatch:
            _validate("--fixture-env", str(fixture))
    finally:
        fixture.unlink(missing_ok=True)

    assert mismatch.value.code == "fixture_auth_source_mismatch"


def test_fixture_env_summary_records_names_and_hashes_only() -> None:
    fixture = _write_tmp_fixture("commerce-agent-c3-hosted-smoke-summary.env")
    try:
        config = _validate("--fixture-env", str(fixture))
    finally:
        fixture.unlink(missing_ok=True)

    assert "GRANTEX_API_KEY" in config.fixture.variable_names
    assert config.fixture.synthetic_ids["AGENTICORG_COMMERCE_FIXTURE_MERCHANT_ID"] == "mch_staging_electronics_pilot"
    assert config.fixture.sensitive_value_hashes
    assert all(
        "sha256_12" in item and "auth-" not in item["sha256_12"]
        for item in config.fixture.sensitive_value_hashes
    )


def test_production_resource_and_secret_names_are_refused() -> None:
    with pytest.raises(runner.HostedSmokeConfigError) as prod_service:
        _validate("--agenticorg-service", "agenticorg-api")
    assert prod_service.value.code == "production_resource_name_refused"

    with pytest.raises(runner.HostedSmokeConfigError) as non_smoke_secret:
        _validate("--secret-name", "AGENTICORG_SECRET_KEY")
    assert non_smoke_secret.value.code == "smoke_resource_name_required"


def test_fixture_sources_are_not_ambiguous() -> None:
    fixture = _write_tmp_fixture("commerce-agent-c3-hosted-smoke-ambiguous.env")
    try:
        with pytest.raises(runner.HostedSmokeConfigError) as excinfo:
            _validate(
                "--fixture-env",
                str(fixture),
                "--fixture-secret-name",
                "agenticorg-commerce-smoke-fixture-env",
            )
    finally:
        fixture.unlink(missing_ok=True)

    assert excinfo.value.code == "fixture_source_ambiguous"


def test_hosted_checks_validate_health_mcp_and_a2a_discovery() -> None:
    config = _validate()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/health/liveness":
            return httpx.Response(200, json={"status": "alive"})
        if path == "/api/v1/health":
            return httpx.Response(200, json={"status": "healthy"})
        if path == "/api/v1/mcp/tools":
            return httpx.Response(200, json={"tools": [{"name": "agenticorg_commerce_sales_agent"}]})
        if path == "/api/v1/a2a/.well-known/agent.json":
            return httpx.Response(
                200,
                json={
                    "url": f"{AGENTICORG_SMOKE}/api/v1/a2a",
                    "authentication": {
                        "issuer": GRANTEX_SMOKE,
                        "jwksUri": f"{GRANTEX_SMOKE}/.well-known/jwks.json",
                    },
                },
            )
        if path == "/api/v1/a2a/agents":
            return httpx.Response(
                200,
                json={"agents": [{"id": "commerce_sales_agent", "tools": ["grantex_commerce:catalog_search"]}]},
            )
        return httpx.Response(404, json={"error": "not_found"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cases = runner.run_hosted_checks(config, client=client)

    assert {case.name for case in cases} >= {
        "liveness",
        "health",
        "mcp_commerce_sales_agent_discovery",
        "a2a_card_uses_agenticorg_smoke_origin",
        "a2a_card_uses_grantex_smoke_issuer",
        "a2a_card_uses_grantex_smoke_jwks",
        "a2a_commerce_sales_agent_discovery",
        "a2a_commerce_tools_grantex_only",
    }
    assert all(case.status == "pass" for case in cases)


def test_consent_exchange_expected_skip_requires_exact_blocker() -> None:
    passing = Path(".tmp/commerce-agent-c3-consent-passing.md")
    failed = Path(".tmp/commerce-agent-c3-consent-failed.md")
    wrong = Path(".tmp/commerce-agent-c3-consent-wrong.md")
    passing.parent.mkdir(exist_ok=True)
    try:
        passing.write_text(
            "| consent_exchange | skipped |  |  |  |  | "
            f"{runner.EXPECTED_CONSENT_EXCHANGE_BLOCKER} |\n",
            encoding="utf-8",
        )
        failed.write_text(
            "| consent_exchange | failed |  |  |  | consent_not_granted |  |\n",
            encoding="utf-8",
        )
        wrong.write_text(
            "| consent_exchange | skipped |  |  |  |  | other blocker |\n",
            encoding="utf-8",
        )

        assert runner.validate_consent_exchange_evidence(passing).status == "pass"
        assert (
            runner.validate_consent_exchange_evidence(failed).error_code
            == "consent_exchange_reported_failed"
        )
        assert (
            runner.validate_consent_exchange_evidence(wrong).error_code
            == "consent_exchange_expected_blocker_missing"
        )
    finally:
        passing.unlink(missing_ok=True)
        failed.unlink(missing_ok=True)
        wrong.unlink(missing_ok=True)


def test_evidence_report_contains_only_redacted_fixture_metadata() -> None:
    fixture = _write_tmp_fixture("commerce-agent-c3-hosted-smoke-evidence.env")
    report = Path(".tmp/commerce-agent-c3-hosted-smoke-evidence.md")
    try:
        config = _validate("--fixture-env", str(fixture), "--evidence-report", str(report))
        runner.write_evidence_report(
            config,
            [runner.SmokeCase(name="mcp_tools", status="pass")],
            dry_run=False,
            path=report,
        )
        text = report.read_text(encoding="utf-8")
    finally:
        fixture.unlink(missing_ok=True)
        report.unlink(missing_ok=True)

    data = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert data["redaction"]["secret_values_recorded"] is False
    assert data["redaction"]["passport_values_recorded"] is False
    assert "GRANTEX_API_KEY" in data["fixture"]["env_var_names"]
    assert re.search(r"auth-[0-9a-f]{32}", text) is None
    assert re.search(r"checkout-[0-9a-f]{32}", text) is None
