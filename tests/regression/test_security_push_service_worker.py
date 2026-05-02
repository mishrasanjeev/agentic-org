"""Push service-worker security regression pins."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SW = REPO / "ui" / "public" / "sw.js"


def test_push_service_worker_does_not_store_or_send_bearer_tokens() -> None:
    src = SW.read_text(encoding="utf-8")
    assert "Authorization" not in src
    assert "Bearer" not in src
    assert "payload.data?.token" not in src
    assert "notification.data?.token" not in src
    assert "token:" not in src


def test_push_service_worker_restricts_notification_urls_to_relative_paths() -> None:
    src = SW.read_text(encoding="utf-8")
    assert "function safeNotificationUrl" in src
    assert "!value.startsWith(\"/\")" in src
    assert "value.startsWith(\"//\")" in src
    assert "safeNotificationUrl(payload.data?.url)" in src
    assert "safeNotificationUrl(notification.data?.url)" in src
