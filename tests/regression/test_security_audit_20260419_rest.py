"""Regression tests for the remaining SECURITY_AUDIT_2026-04-19 items.

Covers:
  - CRITICAL-01: auth endpoints set an HttpOnly session cookie and the
    middlewares accept the cookie as an alternative to Bearer headers.
  - HIGH-07:     RAGFlow traffic is scoped to a per-tenant dataset
    (``tenant_<id>``), never the shared ``default``.
  - MEDIUM-10:   Invite and password-reset flows issue short opaque
    codes; the legacy ``token=`` URL path still works for rollout.
  - MEDIUM-13:   Production nginx CSP no longer contains ``unsafe-eval``
    and adds ``base-uri``/``form-action``/``object-src`` hardening.
  - LOW-14:      The public branding endpoint derives the host from the
    request headers instead of an attacker-controlled ``host`` query.
  - LOW-15:      Blacklist key derivation refuses to run with a
    predictable default secret in production/staging.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


# ── CRITICAL-01 ─────────────────────────────────────────────────────


def test_critical01_auth_sets_session_cookie():
    src = _read("api/v1/auth.py")
    assert "_set_session_cookie" in src
    assert "agenticorg_session" in src
    assert "httponly=True" in src
    assert "samesite=\"lax\"" in src


def test_critical01_auth_endpoints_invoke_cookie_helper():
    from api.v1 import auth as auth_module

    for fn_name in ("signup", "login", "google_login"):
        fn_src = inspect.getsource(getattr(auth_module, fn_name))
        assert "_set_session_cookie(response" in fn_src, (
            f"/{fn_name} must call _set_session_cookie on the Response"
        )


def test_critical01_logout_clears_cookie():
    from api.v1 import auth as auth_module

    fn_src = inspect.getsource(auth_module.logout)
    assert "_clear_session_cookie" in fn_src


def test_critical01_middlewares_accept_cookie():
    for mw_path in ("auth/middleware.py", "auth/grantex_middleware.py"):
        src = _read(mw_path)
        assert "agenticorg_session" in src, f"{mw_path} must read the session cookie"
        assert "request.cookies.get" in src


# ── HIGH-07 ─────────────────────────────────────────────────────────


def test_high07_knowledge_uses_tenant_scoped_dataset():
    src = _read("api/v1/knowledge.py")
    # The fixed helper must exist and be used everywhere.
    assert "_dataset_for" in src
    assert "tenant_" in src
    # The shared 'default' dataset must no longer be hit from any proxy
    # helper — grep for the old literals.
    assert "datasets/default/documents" not in src
    assert "dataset_ids\": [\"default\"]" not in src


def test_high07_dataset_for_is_deterministic_and_url_safe():
    from api.v1.knowledge import _dataset_for

    tid = "11111111-2222-3333-4444-555555555555"
    name = _dataset_for(tid)
    assert name == f"tenant_{tid.replace('-', '-')}"  # retains hyphens
    # Control characters / slashes must not survive
    assert _dataset_for("evil/../tenant") == "tenant_eviltenant"


# ── MEDIUM-10 ───────────────────────────────────────────────────────


def test_medium10_invite_uses_opaque_code():
    src = _read("api/v1/org.py")
    assert "issue_code(\"invite\"" in src
    assert "/accept-invite?code=" in src
    # Legacy token path stays for rollout compatibility.
    assert "class AcceptInviteRequest" in src


def test_medium10_reset_uses_opaque_code():
    src = _read("api/v1/auth.py")
    assert "issue_code(\"reset\"" in src
    assert "/reset-password?code=" in src


def test_medium10_one_time_codes_module():
    from auth.one_time_codes import consume, issue, peek

    # Sanity check the public surface — full Redis round-trip is exercised
    # in the integration suite where a Redis instance is available.
    assert issue.__module__ == "auth.one_time_codes"
    assert peek.__module__ == "auth.one_time_codes"
    assert consume.__module__ == "auth.one_time_codes"


# ── MEDIUM-13 ───────────────────────────────────────────────────────


def test_medium13_csp_drops_unsafe_eval():
    src = _read("ui/nginx.conf")
    # Extract the actual CSP header value the server sends to the browser.
    match = re.search(r'Content-Security-Policy\s+"([^"]+)"', src)
    assert match, "CSP header not found in nginx.conf"
    csp = match.group(1)
    assert "'unsafe-eval'" not in csp, "MEDIUM-13 regression: 'unsafe-eval' re-enabled"
    # New clamps introduced in MEDIUM-13
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp
    assert "object-src 'none'" in csp


# ── LOW-14 ──────────────────────────────────────────────────────────


def test_low14_branding_drops_host_query_param():
    src = _read("api/v1/branding.py")
    # The handler signature must no longer accept a ``host`` Query.
    match = re.search(
        r"async def get_public_branding\((.*?)\)\s*->",
        src,
        re.DOTALL,
    )
    assert match, "get_public_branding signature not found"
    sig = match.group(1)
    assert "host:" not in sig, "LOW-14 regression: host query param re-added"
    # Host must come from request headers instead.
    assert 'request.headers.get("x-forwarded-host")' in src


# ── LOW-15 ──────────────────────────────────────────────────────────


def test_low15_secret_defaults_rejected_in_prod(monkeypatch):
    """``_hash_token`` must raise when prod/staging env lacks the key."""
    from core.auth_state import _hash_token

    monkeypatch.delenv("AGENTICORG_SECRET_KEY", raising=False)
    monkeypatch.setenv("AGENTICORG_ENV", "production")
    with pytest.raises(RuntimeError, match="AGENTICORG_SECRET_KEY"):
        _hash_token("some-token")

    monkeypatch.setenv("AGENTICORG_ENV", "staging")
    with pytest.raises(RuntimeError, match="AGENTICORG_SECRET_KEY"):
        _hash_token("some-token")


def test_low15_jwt_blacklist_key_rejects_prod_default(monkeypatch):
    """``auth.jwt._token_redis_key`` must also refuse the default in prod."""
    from auth.jwt import _token_redis_key

    monkeypatch.delenv("AGENTICORG_SECRET_KEY", raising=False)
    monkeypatch.setenv("AGENTICORG_ENV", "production")
    with pytest.raises(RuntimeError, match="AGENTICORG_SECRET_KEY"):
        _token_redis_key("some-token")
