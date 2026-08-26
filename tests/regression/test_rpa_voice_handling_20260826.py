"""Regression coverage for RPA execution and voice setup truthfulness."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[2]


def test_scheduled_browser_result_preserves_script_chunks() -> None:
    from core.tasks.rpa_tasks import _script_payload

    chunks = [{"content": "useful", "source_url": "https://example.com"}]
    result = _script_payload(
        {"success": True, "data": {"success": True, "chunks": chunks}},
        http_only=False,
    )
    assert result["chunks"] == chunks


def test_failed_schedule_advances_next_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1 import rpa_schedules
    from core.tasks.rpa_tasks import _advance_next_run

    sentinel = object()
    monkeypatch.setattr(rpa_schedules, "_compute_next_run", lambda _cron: sentinel)
    row = SimpleNamespace(
        id="schedule-1",
        enabled=True,
        cron_expression="every_5_minutes",
        next_run_at=None,
    )

    _advance_next_run(row)

    assert row.next_run_at is sentinel


def test_schedule_rejects_plaintext_credentials_and_allows_refs() -> None:
    from pydantic import ValidationError

    from api.v1.rpa_schedules import RPAScheduleCreate

    with pytest.raises(ValidationError, match="scheduled secret inputs are disabled"):
        RPAScheduleCreate(
            name="unsafe",
            script_key="generic_portal",
            params={"username": "operator", "password": "plain-text"},
        )

    safe = RPAScheduleCreate(
        name="reference-only",
        script_key="generic_portal",
        params={
            "credential_ref": "vault://tenant/rpa/portal",
            "token_budget": 500,
        },
    )
    assert safe.params["credential_ref"].startswith("vault://")
    assert safe.params["token_budget"] == 500


def test_voice_credential_encryption_never_falls_back_to_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.voice.sip_config import (
        VoiceCredentialEncryptionError,
        encrypt_credentials,
    )

    monkeypatch.delenv("AGENTICORG_SECRET_KEY", raising=False)
    with pytest.raises(VoiceCredentialEncryptionError):
        encrypt_credentials({"auth_token": "must-not-remain-plain"})


def test_voice_record_contains_only_encrypted_sip_credentials() -> None:
    from api.v1.voice import VoiceConfig, VoiceCredentials, _voice_config_record

    config = VoiceConfig(
        agent_id="5b286bd9-dc61-4f45-8a41-edda463b5769",
        sip_provider="twilio",
        credentials=VoiceCredentials(account_sid="AC123", auth_token="secret"),
        phone_number="+919876543210",
        stt_engine="whisper_local",
        tts_engine="piper_local",
    )

    record = _voice_config_record(config, "ciphertext")

    assert "credentials" not in record
    assert record["credentials_encrypted"] == {"_encrypted": "ciphertext"}
    assert "secret" not in repr(record)


def test_voice_config_masks_custom_sip_uri_completely() -> None:
    from api.v1.voice import _mask_voice_config

    masked = _mask_voice_config(
        {
            "credentials": {
                "account_sid": "",
                "auth_token": "",
                "custom_url": "sips:merchant-user@example.com;transport=tls?token=private",
            }
        }
    )

    assert masked["credentials"]["custom_url"] == "***"
    assert "merchant-user" not in repr(masked)
    assert "private" not in repr(masked)


@pytest.mark.asyncio
async def test_vonage_unauthorized_is_not_reported_as_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.v1 import voice

    response = SimpleNamespace(status_code=401)
    client = AsyncMock()
    client.get.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = False
    monkeypatch.setattr(voice.httpx, "AsyncClient", lambda **_kwargs: context)

    result = await voice.test_connection(
        voice.VoiceTestRequest(
            provider="vonage",
            credentials=voice.VoiceCredentials(
                account_sid="api-key",
                auth_token="api-secret",
            ),
        ),
        tenant_id="5b286bd9-dc61-4f45-8a41-edda463b5769",
    )

    assert result.status == "invalid_credentials"
    client.get.assert_awaited_once()
    assert client.get.await_args.kwargs["params"] == {
        "api_key": "api-key",
        "api_secret": "api-secret",
    }


@pytest.mark.asyncio
async def test_voice_status_does_not_load_or_decrypt_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.v1 import voice

    loader = AsyncMock(
        return_value={
            "agent_id": "5b286bd9-dc61-4f45-8a41-edda463b5769",
            "sip_provider": "twilio",
            "phone_number": "+919876543210",
            "stt_engine": "whisper_local",
            "tts_engine": "piper_local",
            "credentials_encrypted": {"_encrypted": "opaque"},
        }
    )
    monkeypatch.setattr(voice, "_load_voice_record", loader)

    result = await voice.get_voice_status(
        agent_id="5b286bd9-dc61-4f45-8a41-edda463b5769",
        tenant_id="6ca1f55f-33ba-41dc-a790-b6268272a55e",
    )

    assert result.configured is True
    assert result.runtime_status == "configuration_only"
    assert "credential" not in result.model_dump()


def test_agent_voice_tab_uses_real_runtime_and_call_history() -> None:
    source = (REPO / "ui/src/pages/AgentDetail.tsx").read_text(encoding="utf-8")

    assert "Mock call log data" not in source
    assert 'api.get("/voice/config"' not in source
    assert '.get("/voice/status"' in source
    assert '.get("/voice/runtime/health"' in source
    assert '.get("/voice/calls"' in source
    assert '.post("/voice/calls/outbound"' in source


@pytest.mark.asyncio
async def test_rpa_executor_enforces_wall_clock_timeout() -> None:
    fake_module = ModuleType("rpa.scripts.timeout_script")

    async def slow_run(_page, _params):
        await asyncio.sleep(5)
        return {"success": True}

    fake_module.run = slow_run  # type: ignore[attr-defined]
    page = AsyncMock()
    page.on = MagicMock()
    context = AsyncMock()
    context.new_page.return_value = page
    context.set_default_timeout = MagicMock()
    browser = AsyncMock()
    browser.new_context.return_value = context
    playwright = AsyncMock()
    playwright.chromium.launch.return_value = browser
    manager = AsyncMock()
    manager.__aenter__.return_value = playwright
    manager.__aexit__.return_value = False

    with (
        patch.dict(sys.modules, {"rpa.scripts.timeout_script": fake_module}),
        patch("core.rpa.executor._PLAYWRIGHT_AVAILABLE", True),
        patch("core.rpa.executor.async_playwright", return_value=manager),
    ):
        from core.rpa.executor import execute_rpa_script

        result = await execute_rpa_script("timeout_script", {}, timeout_s=1)

    assert result["success"] is False
    assert "execution timeout" in result["error"]
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_generic_portal_rejects_unknown_action_before_navigation() -> None:
    from rpa.scripts.generic_portal import run

    page = AsyncMock()
    result = await run(
        page,
        {
            "portal_url": "https://example.com/login",
            "username": "user",
            "password": "secret",
            "action": "delete_everything",
        },
    )

    assert result["logged_in"] is False
    assert "action must be one of" in result["error"]
    page.goto.assert_not_awaited()
