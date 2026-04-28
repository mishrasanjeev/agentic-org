"""Foundation #7 PR-B — fake mail hermetic seam regressions.

Pinned behaviors:

- ``is_active()`` reflects the env var.
- A real ``send_email`` call captures into the in-process outbox
  when the flag is set; no SMTP connection attempted.
- Domain validation runs BEFORE the fake-mail check so a test
  asserting "invalid domain → no send" still works under the
  fake (Foundation #8 false-green prevention).
- ``capture()``, ``count_to()``, ``last()``, ``reset()`` work as
  documented.
- The conftest sets the flag by default and the autouse fixture
  resets the outbox between tests.
"""

from __future__ import annotations

import os

import pytest

from core.test_doubles import fake_mail


def test_is_active_reflects_env_var(monkeypatch) -> None:
    monkeypatch.delenv("AGENTICORG_TEST_FAKE_MAIL", raising=False)
    assert fake_mail.is_active() is False
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_MAIL", "1")
    assert fake_mail.is_active() is True
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_MAIL", "true")
    assert fake_mail.is_active() is True
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_MAIL", "no")
    assert fake_mail.is_active() is False


def test_capture_records_email() -> None:
    rec = fake_mail.capture(
        to="alice@example.com",
        subject="Hi",
        html="<p>hello</p>",
        sender="bot@agenticorg.ai",
    )
    assert rec.to == "alice@example.com"
    assert rec.subject == "Hi"
    assert "hello" in rec.html
    assert fake_mail.count() == 1
    assert fake_mail.last() is rec


def test_count_to_filters_by_address() -> None:
    fake_mail.capture(to="a@x.com", subject="1", html="x")
    fake_mail.capture(to="b@x.com", subject="2", html="x")
    fake_mail.capture(to="a@x.com", subject="3", html="x")
    assert fake_mail.count_to("a@x.com") == 2
    assert fake_mail.count_to("b@x.com") == 1
    assert fake_mail.count_to("nobody@x.com") == 0


def test_reset_clears_outbox() -> None:
    fake_mail.capture(to="x@y.com", subject="x", html="x")
    assert fake_mail.count() == 1
    fake_mail.reset()
    assert fake_mail.count() == 0
    assert fake_mail.last() is None


def test_send_email_captures_under_fake(monkeypatch) -> None:
    """End-to-end: production send_email path captures the message
    in the fake outbox when the flag is on, never touching SMTP."""
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_MAIL", "1")
    # Ensure the SMTP path would have refused too — proves the
    # capture isn't an accident of missing creds.
    monkeypatch.delenv("AGENTICORG_GMAIL_APP_PASSWORD", raising=False)

    # Real-domain target so domain validation passes (gmail.com
    # has MX records).
    from core.email import send_email

    send_email(
        to="qa@gmail.com",
        subject="Welcome",
        html="<p>welcome</p>",
    )
    assert fake_mail.count() == 1
    rec = fake_mail.last()
    assert rec is not None
    assert rec.to == "qa@gmail.com"
    assert rec.subject == "Welcome"


def test_send_email_skips_invalid_domain_under_fake(monkeypatch) -> None:
    """Domain validation must still gate the fake — an invalid
    domain produces NO captured email. This pins the false-green
    prevention pattern from Foundation #8."""
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_MAIL", "1")

    from core.email import send_email

    send_email(
        to="x@nonexistent-domain-with-no-mx-record-12345.invalid",
        subject="should not capture",
        html="x",
    )
    assert fake_mail.count() == 0


def test_conftest_default_makes_fake_mail_active() -> None:
    """Pin the conftest sets the env var so every test gets the
    fake by default."""
    assert os.environ.get("AGENTICORG_TEST_FAKE_MAIL") == "1"
    assert fake_mail.is_active() is True


def test_autouse_fixture_resets_between_tests_part_1() -> None:
    """First half of the bleed-check pair — capture an email."""
    fake_mail.capture(to="bleed@x.com", subject="bleed", html="x")
    assert fake_mail.count() == 1


def test_autouse_fixture_resets_between_tests_part_2() -> None:
    """Second half of the bleed-check pair — outbox must be empty
    even though part_1 captured one. If this fails, the autouse
    fixture in tests/conftest.py is broken or not registered."""
    assert fake_mail.count() == 0


@pytest.mark.parametrize("addr", ["alice@gmail.com", "bob@gmail.com", "carol@gmail.com"])
def test_each_param_starts_with_empty_outbox(addr) -> None:
    """Parametrized sanity check — every parameter case sees a
    clean outbox at the start."""
    assert fake_mail.count() == 0
    fake_mail.capture(to=addr, subject="x", html="x")
    assert fake_mail.count() == 1
