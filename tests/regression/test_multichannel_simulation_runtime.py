"""Regression coverage for the local voice/email/RPA/OCR simulation contract."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[2]


def _smtp_env(**overrides: str) -> dict[str, str]:
    values = {
        "AGENTICORG_ENV": "development",
        "AGENTICORG_SMTP_HOST": "smtp.gmail.com",
        "AGENTICORG_SMTP_PORT": "465",
        "AGENTICORG_SMTP_SECURITY": "ssl",
        "AGENTICORG_SMTP_LOGIN": "sender@real.invalid",
        "AGENTICORG_GMAIL_APP_PASSWORD": "synthetic-password",
        "AGENTICORG_TEST_FAKE_MAIL": "",
    }
    values.update(overrides)
    return values


def test_local_loopback_mail_capture_uses_real_smtp_without_authentication() -> None:
    from core.email import send_email

    smtp = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = smtp
    context.__exit__.return_value = False
    with (
        patch.dict(
            "os.environ",
            _smtp_env(
                AGENTICORG_SMTP_HOST="127.0.0.1",
                AGENTICORG_SMTP_PORT="1025",
                AGENTICORG_SMTP_SECURITY="plain",
                AGENTICORG_SMTP_LOGIN="",
                AGENTICORG_GMAIL_APP_PASSWORD="",
            ),
            clear=False,
        ),
        patch("core.email.validate_email_domain", return_value=(True, "OK")),
        patch("core.email.smtplib.SMTP", return_value=context) as smtp_class,
    ):
        assert send_email("recipient@real.invalid", "Simulation", "<p>captured</p>") is True

    smtp_class.assert_called_once_with("127.0.0.1", 1025, timeout=15)
    smtp.login.assert_not_called()
    smtp.send_message.assert_called_once()


@pytest.mark.parametrize("runtime_env", ["production", "staging", "preview", "unknown"])
def test_plain_smtp_is_rejected_outside_relaxed_loopback(runtime_env: str) -> None:
    from core.email import send_email

    with (
        patch.dict(
            "os.environ",
            _smtp_env(
                AGENTICORG_ENV=runtime_env,
                AGENTICORG_SMTP_HOST="127.0.0.1",
                AGENTICORG_SMTP_SECURITY="plain",
            ),
            clear=False,
        ),
        patch("core.email.validate_email_domain", return_value=(True, "OK")),
        patch("core.email.smtplib.SMTP") as smtp_class,
    ):
        assert send_email("recipient@real.invalid", "Blocked", "<p>blocked</p>") is False

    smtp_class.assert_not_called()


def test_plain_smtp_is_rejected_for_remote_host_even_in_local_environment() -> None:
    from core.email import send_email

    with (
        patch.dict(
            "os.environ",
            _smtp_env(
                AGENTICORG_ENV="local",
                AGENTICORG_SMTP_HOST="smtp.remote.invalid",
                AGENTICORG_SMTP_SECURITY="plain",
            ),
            clear=False,
        ),
        patch("core.email.validate_email_domain", return_value=(True, "OK")),
        patch("core.email.smtplib.SMTP") as smtp_class,
    ):
        assert send_email("recipient@real.invalid", "Blocked", "<p>blocked</p>") is False

    smtp_class.assert_not_called()


def test_starttls_transport_authenticates_before_delivery() -> None:
    from core.email import send_email

    smtp = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = smtp
    context.__exit__.return_value = False
    with (
        patch.dict(
            "os.environ",
            _smtp_env(
                AGENTICORG_SMTP_HOST="smtp.provider.invalid",
                AGENTICORG_SMTP_PORT="587",
                AGENTICORG_SMTP_SECURITY="starttls",
            ),
            clear=False,
        ),
        patch("core.email.validate_email_domain", return_value=(True, "OK")),
        patch("core.email.smtplib.SMTP", return_value=context),
    ):
        assert send_email("recipient@real.invalid", "TLS", "<p>encrypted</p>") is True

    assert smtp.ehlo.call_count == 2
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("sender@real.invalid", "synthetic-password")
    smtp.send_message.assert_called_once()


def test_production_image_packages_playwright_and_chromium() -> None:
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")

    assert '"playwright>=1.62.0,<2"' in pyproject
    assert "python -m playwright install chromium" in dockerfile
    assert "python -m playwright install-deps chromium" in dockerfile
    assert "PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers" in dockerfile


def test_mailpit_simulation_image_is_digest_pinned() -> None:
    compose = (REPO / "docker-compose.simulation.yml").read_text(encoding="utf-8")

    assert "axllent/mailpit:v1.27.8@sha256:" in compose
    assert '"1025"' in compose
    assert '"8025"' in compose
