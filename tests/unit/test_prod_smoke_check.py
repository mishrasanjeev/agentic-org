from __future__ import annotations

from pytest import MonkeyPatch

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
    assert result.detail == "missing required env: AGENTICORG_SMOKE_EMAIL, AGENTICORG_SMOKE_PASSWORD"


def test_authenticated_checks_skip_without_auth_context() -> None:
    runner = smoke.ProdSmokeRunner(api_base_url="https://example.test/api/v1")

    results = runner.authenticated_checks(None)

    assert results == [
        smoke.SmokeResult(
            name="authenticated.checks",
            status=smoke.SKIPPED,
            detail="missing authenticated smoke token or login credentials",
        )
    ]


def test_cdc_dedupe_skips_when_signed_event_env_missing(monkeypatch: MonkeyPatch) -> None:
    for name in (
        "AGENTICORG_SMOKE_CDC_TENANT_ID",
        "AGENTICORG_SMOKE_CDC_CONNECTOR",
        "AGENTICORG_SMOKE_CDC_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    runner = smoke.ProdSmokeRunner(api_base_url="https://example.test/api/v1")
    result = runner.cdc_event_dedupe_check()

    assert result.status == smoke.SKIPPED
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

    result = FakeRunner(api_base_url="https://example.test/api/v1").cdc_event_dedupe_check()

    assert result.status == smoke.PASS
    assert "do-not-print" not in result.detail
    assert len(calls) == 2
    assert calls[0]["headers"]
    assert calls[0]["headers"]["X-CDC-Signature"] != "do-not-print"
