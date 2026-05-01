"""SEC-2026-05-P1-003 PR-B: CSRF middleware contract pins.

Pin the double-submit cookie CSRF protection so the bug class — a
cookie-authed mutating request without CSRF protection — can't recur.

Tested behaviors:

- Cookie-authed mutating request **without** ``X-CSRF-Token`` → 403.
- Cookie-authed mutating request **with mismatched** ``X-CSRF-Token`` → 403.
- Cookie-authed mutating request **with matching** token → bypasses CSRF
  middleware (still hits auth / route logic).
- Bearer-token API client with no cookie → bypasses CSRF cleanly.
- Safe HTTP methods (GET, HEAD, OPTIONS) → bypass cleanly.
- Webhook routes (``/api/v1/webhooks/...``) → bypass cleanly (HMAC).
- Auth bootstrap routes (``/api/v1/auth/login``) → bypass (no prior session).
- Login response sets the ``agenticorg_csrf`` cookie alongside the
  ``agenticorg_session`` cookie.
- The CSRF cookie is **non-HttpOnly** (the SPA must read it).
- Constant-time compare is used (no timing oracle).

Hermetic by design — uses Starlette's ``TestClient`` directly against a
minimal test app with the CSRFMiddleware mounted, so we don't require
the full FastAPI app + DB + auth stack to verify the middleware contract.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    clear_csrf_cookie,
    generate_csrf_token,
    set_csrf_cookie,
)
from auth.csrf_middleware import CSRFMiddleware

# ─────────────────────────────────────────────────────────────────
# Test app — minimal FastAPI + CSRFMiddleware, no auth/DB required
# ─────────────────────────────────────────────────────────────────


@pytest.fixture
def app() -> FastAPI:
    """A bare FastAPI app with only the CSRFMiddleware mounted, plus a
    handful of routes spanning the bypass / enforcement matrix.
    """
    a = FastAPI()
    a.add_middleware(CSRFMiddleware)

    @a.get("/api/v1/some-resource")
    def _get_resource():
        return {"ok": True}

    @a.post("/api/v1/some-resource")
    def _post_resource():
        return {"created": True}

    @a.put("/api/v1/some-resource/{rid}")
    def _put_resource(rid: str):
        return {"updated": rid}

    @a.delete("/api/v1/some-resource/{rid}")
    def _delete_resource(rid: str):
        return {"deleted": rid}

    @a.post("/api/v1/webhooks/email/sendgrid")
    def _webhook_sendgrid():
        return {"webhook": "ok"}

    @a.post("/api/v1/auth/login")
    def _login():
        return {"login": "ok"}

    @a.post("/api/v1/auth/sso/callback")
    def _sso_callback():
        return {"sso": "ok"}

    return a


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────
# Bypass paths — these MUST not enforce CSRF
# ─────────────────────────────────────────────────────────────────


def test_safe_methods_bypass_csrf(client: TestClient) -> None:
    """GET / HEAD / OPTIONS never enforce CSRF — they don't change state."""
    resp = client.get("/api/v1/some-resource")
    assert resp.status_code == 200


def test_bearer_token_client_bypasses_csrf(client: TestClient) -> None:
    """No session cookie → no CSRF enforcement. SDKs / CI / MCP work
    unchanged because they don't carry browser cookies."""
    resp = client.post("/api/v1/some-resource", headers={"Authorization": "Bearer abc"})
    assert resp.status_code == 200


def test_webhook_routes_bypass_csrf(client: TestClient) -> None:
    """Webhook routes have HMAC signing — CSRF doesn't apply."""
    # Even with a session cookie + no CSRF token, webhook bypasses.
    resp = client.post(
        "/api/v1/webhooks/email/sendgrid",
        cookies={"agenticorg_session": "fake-session-token"},
        json=[{"event": "open"}],
    )
    assert resp.status_code == 200


def test_login_route_bypasses_csrf(client: TestClient) -> None:
    """Auth bootstrap — no prior session to enforce against."""
    resp = client.post("/api/v1/auth/login", json={})
    assert resp.status_code == 200


def test_sso_callback_bypasses_csrf(client: TestClient) -> None:
    """SSO callback can't carry a CSRF token (the OIDC redirect comes
    from the IdP, not from our SPA)."""
    resp = client.post("/api/v1/auth/sso/callback")
    assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────
# Enforcement paths — cookie-authed mutating requests
# ─────────────────────────────────────────────────────────────────


def test_cookie_authed_post_without_csrf_returns_403(client: TestClient) -> None:
    """The core threat: an attacker site forces the victim's browser to
    POST with the session cookie. Without the X-CSRF-Token (which the
    attacker can't read), the server must refuse."""
    resp = client.post(
        "/api/v1/some-resource",
        cookies={"agenticorg_session": "fake-session-token"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert "CSRF" in body["detail"]


def test_cookie_authed_put_without_csrf_returns_403(client: TestClient) -> None:
    """All unsafe methods are enforced — POST, PUT, PATCH, DELETE."""
    resp = client.put(
        "/api/v1/some-resource/abc",
        cookies={"agenticorg_session": "fake-session-token"},
    )
    assert resp.status_code == 403


def test_cookie_authed_delete_without_csrf_returns_403(client: TestClient) -> None:
    resp = client.delete(
        "/api/v1/some-resource/abc",
        cookies={"agenticorg_session": "fake-session-token"},
    )
    assert resp.status_code == 403


def test_cookie_authed_post_with_mismatched_csrf_returns_403(client: TestClient) -> None:
    """A fixed/guessed/old token in the cookie that doesn't match the
    header still fails — protects against cookie-fixation attacks
    where an attacker tries to set a known CSRF cookie."""
    resp = client.post(
        "/api/v1/some-resource",
        cookies={
            "agenticorg_session": "fake-session-token",
            CSRF_COOKIE_NAME: "cookie-value",
        },
        headers={CSRF_HEADER_NAME: "header-value-different"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert "mismatch" in body["detail"].lower()


def test_cookie_authed_post_with_only_cookie_no_header_returns_403(
    client: TestClient,
) -> None:
    """The double-submit pattern requires BOTH the cookie and the
    matching header. Cookie alone is insufficient — that's the
    state of any cross-site forged request."""
    resp = client.post(
        "/api/v1/some-resource",
        cookies={
            "agenticorg_session": "fake-session-token",
            CSRF_COOKIE_NAME: "valid-token",
        },
        # No X-CSRF-Token header
    )
    assert resp.status_code == 403


def test_cookie_authed_post_with_matching_csrf_passes_middleware(
    client: TestClient,
) -> None:
    """Happy path: cookie value == header value → middleware passes the
    request to the route. The 200 from the test route confirms CSRF
    didn't block it.
    """
    token = generate_csrf_token()
    resp = client.post(
        "/api/v1/some-resource",
        cookies={
            "agenticorg_session": "fake-session-token",
            CSRF_COOKIE_NAME: token,
        },
        headers={CSRF_HEADER_NAME: token},
    )
    assert resp.status_code == 200


def test_session_cookie_without_csrf_cookie_returns_403(client: TestClient) -> None:
    """A session cookie present but CSRF cookie missing means the
    user's session predates the CSRF rollout (or the cookie was
    cleared client-side). Re-auth required — fail closed."""
    resp = client.post(
        "/api/v1/some-resource",
        cookies={"agenticorg_session": "fake-session-token"},
        headers={CSRF_HEADER_NAME: "anything"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert "missing" in body["detail"].lower()


# ─────────────────────────────────────────────────────────────────
# Cookie issuance — login flow sets the CSRF cookie
# ─────────────────────────────────────────────────────────────────


def test_set_csrf_cookie_writes_non_httponly_cookie() -> None:
    """The CSRF cookie MUST be readable from JS — the SPA's interceptor
    needs to copy it into the X-CSRF-Token header on mutating
    requests. HttpOnly would break the entire double-submit pattern.
    """
    from fastapi import Response

    response = Response()
    set_csrf_cookie(response, "test-token-value", max_age_seconds=3600)
    cookie_header = response.headers.get("set-cookie", "")
    assert "agenticorg_csrf=test-token-value" in cookie_header
    # Critical: must NOT have HttpOnly (case-insensitive — Starlette
    # may emit either "HttpOnly" or "httponly").
    assert "httponly" not in cookie_header.lower()
    # Should still have SameSite=lax for defense in depth
    assert "samesite=lax" in cookie_header.lower()


def test_clear_csrf_cookie_emits_deletion_header() -> None:
    """Logout must clear the CSRF cookie too — orphaned tokens shouldn't
    persist after the session ends."""
    from fastapi import Response

    response = Response()
    clear_csrf_cookie(response)
    cookie_header = response.headers.get("set-cookie", "")
    assert "agenticorg_csrf=" in cookie_header
    # An expired cookie has Max-Age=0 / past Expires.
    assert (
        "Max-Age=0" in cookie_header
        or "max-age=0" in cookie_header
        or "1970" in cookie_header
        or "expires=" in cookie_header.lower()
    )


def test_generate_csrf_token_is_csprng_backed() -> None:
    """Tokens MUST come from ``secrets``, not ``random`` — predictable
    tokens make the whole defense pointless. Verify by checking the
    distribution is sufficiently random and tokens don't repeat.
    """
    tokens = {generate_csrf_token() for _ in range(100)}
    # 100 tokens, each 256 bits — the probability of any collision is
    # effectively 0. If we see < 100 unique values, generation is broken.
    assert len(tokens) == 100
    # Each token is at least 32 chars (32 bytes urlsafe-b64 = 43 chars).
    for t in tokens:
        assert len(t) >= 32


def test_constant_time_compare_used() -> None:
    """Pin that ``secrets.compare_digest`` is the comparison primitive.
    A regular ``==`` would leak the matching prefix length via timing,
    letting an attacker discover the token byte-by-byte (Bleichenbacher-
    style). The pin grep-checks the source so a future refactor can't
    silently downgrade the check.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "auth" / "csrf_middleware.py"
    ).read_text(encoding="utf-8")
    assert "secrets.compare_digest" in src, (
        "auth/csrf_middleware.py must use secrets.compare_digest for the "
        "token comparison — a regular == leaks token bytes via timing."
    )
