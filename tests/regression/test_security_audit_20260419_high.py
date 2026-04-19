"""Regression tests for HIGH findings in SECURITY_AUDIT_2026-04-19.md.

Covers:
  - HIGH-04: Webhook signature verification fails closed.
  - HIGH-05: Voice config endpoints are admin-gated and mask secrets.
  - HIGH-06: Voice SIP test rejects private/reserved IP ranges.
  - HIGH-08: RPA execution history is tenant-scoped.
  - HIGH-09: RPA generic_portal requires tenant admin.
  - MEDIUM-11: Billing redirect URLs validated against allowlist.
  - MEDIUM-12: Connector base_url SSRF guard rejects private IPs.

These tests read the source files and assert the remediation is present,
rather than depending on the full FastAPI runtime. Source-level assertions
are intentionally strict: if the fix is refactored the test will flag it
for re-review.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


# ── HIGH-04 ─────────────────────────────────────────────────────────


def test_high04_sendgrid_fail_closed():
    src = _read("api/v1/webhooks.py")
    assert "AGENTICORG_WEBHOOK_ALLOW_UNSIGNED" in src
    # Must not blindly return True when key missing
    assert "_verify_sendgrid_signature" in src
    assert "sendgrid_webhook_key_not_configured" in src


def test_high04_mailchimp_signature_verified():
    src = _read("api/v1/webhooks.py")
    assert "_verify_mailchimp_signature" in src
    assert "X-Mandrill-Signature" in src
    assert "MAILCHIMP_WEBHOOK_KEY" in src


def test_high04_moengage_signature_verified():
    src = _read("api/v1/webhooks.py")
    assert "_verify_moengage_signature" in src
    assert "X-MoEngage-Signature" in src
    assert "MOENGAGE_WEBHOOK_KEY" in src


def test_high04_webhook_functions_fail_closed_callsites():
    """Each provider handler must reject on missing signature."""
    from api.v1 import webhooks

    sg = inspect.getsource(webhooks.sendgrid_webhook)
    assert "_verify_sendgrid_signature" in sg
    assert "Invalid webhook signature" in sg

    mc = inspect.getsource(webhooks.mailchimp_webhook)
    assert "_verify_mailchimp_signature" in mc

    mo = inspect.getsource(webhooks.moengage_webhook)
    assert "_verify_moengage_signature" in mo


# ── HIGH-05 / HIGH-06 ────────────────────────────────────────────────


def test_high05_voice_config_admin_gated():
    src = _read("api/v1/voice.py")
    # Both save and get config must list require_tenant_admin in deps.
    assert src.count("require_tenant_admin") >= 3  # import + 3 endpoints
    assert "_mask_voice_config" in src
    assert "_mask_secret" in src


def test_high06_voice_sip_private_ip_blocked():
    src = _read("api/v1/voice.py")
    assert "_resolve_and_block_private" in src
    # The function must cover major blocked categories
    assert "is_private" in src
    assert "is_loopback" in src
    assert "is_link_local" in src


def test_high06_resolver_rejects_private_range():
    """Unit-test the private-IP resolver directly."""
    from fastapi import HTTPException

    from api.v1.voice import _resolve_and_block_private

    with pytest.raises(HTTPException) as excinfo:
        _resolve_and_block_private("127.0.0.1")
    assert excinfo.value.status_code == 403

    with pytest.raises(HTTPException) as excinfo:
        _resolve_and_block_private("169.254.169.254")  # AWS/GCP metadata
    assert excinfo.value.status_code == 403

    with pytest.raises(HTTPException) as excinfo:
        _resolve_and_block_private("10.0.0.1")
    assert excinfo.value.status_code == 403


# ── HIGH-08 / HIGH-09 ────────────────────────────────────────────────


def test_high08_rpa_history_tenant_scoped():
    src = _read("api/v1/rpa.py")
    # Shared global list must be gone; per-tenant dict in its place.
    assert "_execution_history: dict[str, list[dict[str, Any]]]" in src
    assert "_execution_history.get(str(tenant_id)" in src
    assert "_execution_history.setdefault(str(tenant_id)" in src


def test_high09_rpa_generic_portal_admin_gated():
    src = _read("api/v1/rpa.py")
    assert "_ADMIN_ONLY_SCRIPTS" in src
    assert "generic_portal" in src
    # Must reject non-admins with 403
    assert "requires tenant admin" in src


# ── MEDIUM-11 ────────────────────────────────────────────────────────


def test_medium11_billing_redirect_allowlist():
    src = _read("api/v1/billing.py")
    assert "_validate_redirect_url" in src
    assert "_allowed_redirect_hosts" in src
    assert "success_url" in src and "cancel_url" in src


def test_medium11_validate_redirect_blocks_offsite(monkeypatch):
    from fastapi import HTTPException

    from api.v1.billing import _validate_redirect_url

    monkeypatch.setenv("AGENTICORG_FRONTEND_URL", "https://app.example.com")
    monkeypatch.setenv("AGENTICORG_BILLING_REDIRECT_ALLOWLIST", "")
    # Same-origin is fine
    assert _validate_redirect_url(
        "https://app.example.com/billing/done",
        "success_url",
    ) == "https://app.example.com/billing/done"
    # Different origin is rejected
    with pytest.raises(HTTPException) as excinfo:
        _validate_redirect_url("https://attacker.example.net/phish", "success_url")
    assert excinfo.value.status_code == 400
    # Empty is allowed (caller opts into gateway default)
    assert _validate_redirect_url("", "success_url") == ""


# ── MEDIUM-12 ────────────────────────────────────────────────────────


def test_medium12_connector_base_url_guard():
    src = _read("api/v1/connectors.py")
    assert "_assert_public_base_url" in src
    # Must be called on register, update, and test paths.
    assert src.count("_assert_public_base_url(") >= 3


def test_medium12_base_url_blocks_private():
    from fastapi import HTTPException

    from api.v1.connectors import _assert_public_base_url

    with pytest.raises(HTTPException) as excinfo:
        _assert_public_base_url("http://127.0.0.1:8080")
    assert excinfo.value.status_code == 403

    with pytest.raises(HTTPException) as excinfo:
        _assert_public_base_url("http://169.254.169.254/latest/meta-data/")
    assert excinfo.value.status_code == 403

    with pytest.raises(HTTPException) as excinfo:
        _assert_public_base_url("ftp://example.com/")
    assert excinfo.value.status_code == 400

    # Empty is a no-op — class-level base_url still applies.
    _assert_public_base_url("")
