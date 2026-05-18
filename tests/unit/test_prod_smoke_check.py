from __future__ import annotations

from pathlib import Path

from pytest import CaptureFixture, MonkeyPatch

from scripts import prod_smoke_check as smoke


def test_redacts_sensitive_values_and_headers() -> None:
    assert smoke.redact_value("super-secret") == "<redacted>"
    assert smoke.redact_value("") == ""
    assert smoke.redact_mapping({"PASSWORD": "p@ss", "SAFE_NAME": "visible"}) == {
        "PASSWORD": "<redacted>",
        "SAFE_NAME": "visible",
    }
    assert smoke.redact_headers({"Authorization": "Bearer token", "Content-Type": "application/json"}) == {
        "Authorization": "<redacted>",
        "Content-Type": "application/json",
    }


def test_authentication_skips_with_exact_missing_env(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTICORG_SMOKE_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("AGENTICORG_SMOKE_EMAIL", raising=False)
    monkeypatch.delenv("AGENTICORG_SMOKE_PASSWORD", raising=False)

    runner = smoke.ProdSmokeRunner(api_base_url="https://example.test/api/v1")
    auth, result = runner.authenticate()

    assert auth is None
    assert result.status == smoke.SKIPPED
    assert result.category == smoke.CATEGORY_AUTHENTICATED
    assert result.detail == "missing required env: AGENTICORG_SMOKE_EMAIL, AGENTICORG_SMOKE_PASSWORD"


def test_authenticated_checks_skip_without_auth_context() -> None:
    runner = smoke.ProdSmokeRunner(api_base_url="https://example.test/api/v1")

    results = runner.authenticated_checks(None)

    assert results == [
        smoke.SmokeResult(
            name="authenticated.checks",
            status=smoke.SKIPPED,
            detail="missing authenticated smoke token or login credentials",
            category=smoke.CATEGORY_AUTHENTICATED,
        )
    ]


def test_cdc_dedupe_skips_by_default_even_when_signed_event_env_exists(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv(smoke.ENABLE_SIGNED_EVENT_ENV, raising=False)
    monkeypatch.setenv("AGENTICORG_SMOKE_CDC_TENANT_ID", "tenant-1")
    monkeypatch.setenv("AGENTICORG_SMOKE_CDC_CONNECTOR", "zoho")
    monkeypatch.setenv("AGENTICORG_SMOKE_CDC_SECRET", "do-not-print")

    runner = smoke.ProdSmokeRunner(api_base_url="https://example.test/api/v1")
    result = runner.cdc_event_dedupe_check()

    assert result.status == smoke.SKIPPED
    assert result.category == smoke.CATEGORY_SIGNED_EVENT
    assert smoke.ENABLE_SIGNED_EVENT_ENV in result.detail


def test_cdc_dedupe_skips_when_enabled_but_signed_event_env_missing(monkeypatch: MonkeyPatch) -> None:
    for name in (
        "AGENTICORG_SMOKE_CDC_TENANT_ID",
        "AGENTICORG_SMOKE_CDC_CONNECTOR",
        "AGENTICORG_SMOKE_CDC_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    runner = smoke.ProdSmokeRunner(api_base_url="https://example.test/api/v1", enable_signed_event=True)
    result = runner.cdc_event_dedupe_check()

    assert result.status == smoke.SKIPPED
    assert result.category == smoke.CATEGORY_SIGNED_EVENT
    assert "AGENTICORG_SMOKE_CDC_SECRET" in result.detail


def test_cdc_dedupe_uses_signature_without_printing_secret(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_SMOKE_CDC_TENANT_ID", "tenant-1")
    monkeypatch.setenv("AGENTICORG_SMOKE_CDC_CONNECTOR", "zoho")
    monkeypatch.setenv("AGENTICORG_SMOKE_CDC_SECRET", "do-not-print")
    monkeypatch.setenv("AGENTICORG_SMOKE_CDC_EVENT_ID", "event-1")
    calls: list[dict[str, object]] = []

    class FakeRunner(smoke.ProdSmokeRunner):
        def request_json(
            self,
            method: str,
            path: str,
            *,
            token: str | None = None,
            payload: dict[str, object] | None = None,
            headers: dict[str, str] | None = None,
        ) -> smoke.HttpResult:
            calls.append({"method": method, "path": path, "payload": payload, "headers": headers})
            if len(calls) == 1:
                return smoke.HttpResult(status_code=202, body={"status": "accepted"}, body_text="")
            return smoke.HttpResult(status_code=200, body={"status": "duplicate"}, body_text="")

    result = FakeRunner(
        api_base_url="https://example.test/api/v1",
        enable_signed_event=True,
    ).cdc_event_dedupe_check()

    assert result.status == smoke.PASS
    assert result.category == smoke.CATEGORY_SIGNED_EVENT
    assert "do-not-print" not in result.detail
    assert len(calls) == 2
    assert calls[0]["headers"]
    assert calls[0]["headers"]["X-CDC-Signature"] != "do-not-print"


def test_cost_risky_checks_skip_by_default_even_when_inputs_exist(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv(smoke.ENABLE_COST_RISKY_ENV, raising=False)
    monkeypatch.setenv("AGENTICORG_SMOKE_KNOWLEDGE_QUERY", "do not call vector search")

    runner = smoke.ProdSmokeRunner(api_base_url="https://example.test/api/v1")
    result = runner.knowledge_search_check(smoke.AuthContext(token="token"))

    assert result.status == smoke.SKIPPED
    assert result.category == smoke.CATEGORY_COST_RISKY
    assert smoke.ENABLE_COST_RISKY_ENV in result.detail


def test_dry_run_skips_mutating_or_provider_backed_checks(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_SMOKE_CHAT_QUERY", "do not call llm")

    class NoNetworkRunner(smoke.ProdSmokeRunner):
        def request_json(self, *args: object, **kwargs: object) -> smoke.HttpResult:
            raise AssertionError("dry-run must not perform provider-backed calls")

    runner = NoNetworkRunner(
        api_base_url="https://example.test/api/v1",
        dry_run=True,
        enable_cost_risky=True,
    )
    result = runner.chat_query_check(smoke.AuthContext(token="token"))

    assert result.status == smoke.SKIPPED
    assert result.category == smoke.CATEGORY_COST_RISKY
    assert "dry-run mode" in result.detail


def test_required_smoke_env_vars_are_documented() -> None:
    runbook = Path("docs/runbooks/production_smoke.md").read_text(encoding="utf-8")

    for name in smoke.REQUIRED_ENV_VARS + smoke.AUTH_ENV_VARS + smoke.OPTIONAL_ENV_VARS:
        assert name in runbook


def test_print_results_includes_check_category(capsys: CaptureFixture[str]) -> None:
    smoke.print_results(
        [
            smoke.SmokeResult(
                name="health.readiness",
                status=smoke.PASS,
                detail="endpoint reachable",
                http_status=200,
                category=smoke.CATEGORY_PUBLIC,
            )
        ]
    )

    captured = capsys.readouterr()
    assert "[public] health.readiness" in captured.out
