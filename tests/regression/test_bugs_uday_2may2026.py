"""Regression pins for Uday CA Firms 2026-05-02 sweep.

Source: ``C:\\Users\\mishr\\Downloads\\CA_FIRMS_TEST_REPORTUday2May2026.md``.

These tests replay each tester step from the bug report so a future
regression cannot silently un-fix any of:

- BUG-07 — grantex scope drift on PATCH /agents (config.grantex.grantex_scopes
  must be recomputed when authorized_tools changes).
- BUG-08 — POST /agents/{id}/run with action="shadow_sample" must rewrite
  the user message into an exploratory instruction so the LLM exercises a
  registered tool instead of refusing the request at confidence 0.40.
- BUG-09 — POST /api/v1/auth/google must NOT be blocked by CSRF middleware
  when a stale agenticorg_session cookie is present (auth bootstrap path).
- BUG-10 — covered by ui/e2e/qa-uday-2may2026.spec.ts (Playwright); a
  source-shape test pinned here ensures ProtectedRoute reads isHydrating.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.csrf import CSRF_COOKIE_NAME
from auth.csrf_middleware import CSRFMiddleware

# ─────────────────────────────────────────────────────────────────
# BUG-09 — auth-bootstrap routes are CSRF-exempt
# ─────────────────────────────────────────────────────────────────


@pytest.fixture
def csrf_app() -> FastAPI:
    a = FastAPI()
    a.add_middleware(CSRFMiddleware)

    @a.post("/api/v1/auth/google")
    def _google():
        return {"ok": True}

    @a.post("/api/v1/auth/forgot-password")
    def _forgot():
        return {"ok": True}

    @a.post("/api/v1/auth/reset-password")
    def _reset():
        return {"ok": True}

    @a.post("/api/v1/auth/login")
    def _login():
        return {"ok": True}

    @a.post("/api/v1/agents/x/run")
    def _agents_run():
        return {"ok": True}

    return a


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/google",
        "/api/v1/auth/forgot-password",
        "/api/v1/auth/reset-password",
        "/api/v1/auth/login",
    ],
)
def test_bug09_auth_bootstrap_routes_csrf_exempt_with_stale_session(
    csrf_app: FastAPI, path: str
) -> None:
    """BUG-09 — tester sees ``CSRF token mismatch (SEC-2026-05-P1-003)``
    when clicking "Sign in with Google". The cause: a stale
    ``agenticorg_session`` cookie in the browser plus a missing
    X-CSRF-Token header (raw fetch, not axios) made the CSRF middleware
    reject the bootstrap request. Auth-bootstrap routes must bypass
    cleanly even when a stale session cookie is present, because that is
    EXACTLY the scenario in which the user is trying to re-authenticate.
    """
    client = TestClient(csrf_app)
    # Simulate stale session cookie + stale CSRF cookie. No X-CSRF-Token
    # header attached (loginWithGoogle uses raw fetch).
    client.cookies.set("agenticorg_session", "stale.jwt.value")
    client.cookies.set(CSRF_COOKIE_NAME, "stale-csrf-token")
    res = client.post(path, json={})
    assert res.status_code == 200, (
        f"Auth-bootstrap path {path} must bypass CSRF middleware — "
        f"got {res.status_code}: {res.text}"
    )


def test_bug09_non_bootstrap_route_still_enforced(csrf_app: FastAPI) -> None:
    """Negative pin: non-bootstrap mutating routes still enforce CSRF
    when a session cookie is present. Prevents the fix from drifting
    into a blanket exemption.
    """
    client = TestClient(csrf_app)
    client.cookies.set("agenticorg_session", "session.jwt.value")
    client.cookies.set(CSRF_COOKIE_NAME, "real-csrf-token")
    # No X-CSRF-Token header; mismatch path → 403.
    res = client.post("/api/v1/agents/x/run", json={})
    assert res.status_code == 403


# ─────────────────────────────────────────────────────────────────
# BUG-08 — shadow_sample sentinel is rewritten by _build_user_message
# ─────────────────────────────────────────────────────────────────


def test_bug08_shadow_sample_sentinel_rewrites_to_exploratory_prompt() -> None:
    """The dashboard "Generate Test Sample" button posts
    ``action="shadow_sample"``. Before the fix, runner.py concatenated
    that verbatim into the user message and the LLM correctly refused
    because no such tool exists, producing tool_calls=[] and
    confidence=0.40. After the fix, the sentinel is replaced with an
    exploratory instruction telling the LLM to invoke ONE of its
    registered tools with safe defaults.
    """
    from core.langgraph.runner import _build_user_message

    msg = _build_user_message(
        {
            "action": "shadow_sample",
            "inputs": {"mode": "test", "generate_sample": True},
        }
    )
    lower = msg.lower()
    # The literal sentinel must NOT leak into the prompt.
    assert "shadow_sample" not in msg, (
        "shadow_sample must be rewritten, not echoed to the LLM"
    )
    # Prompt must instruct the LLM to actually call a tool.
    assert "tool" in lower
    assert "shadow" in lower
    # Anti-fabrication clause stays — tester explicitly required
    # "Do not make up data" in the agent prompt.
    assert "fabricate" in lower or "make up" in lower


def test_bug08_normal_action_unchanged() -> None:
    """Negative pin: non-sentinel actions must continue to flow through
    unchanged so domain-specific verbs (process_invoice,
    daily_reconciliation, etc.) reach the LLM as before.
    """
    from core.langgraph.runner import _build_user_message

    msg = _build_user_message(
        {"action": "process_invoice", "inputs": {"invoice_id": "INV-1"}}
    )
    assert "process_invoice" in msg
    assert "INV-1" in msg


# ─────────────────────────────────────────────────────────────────
# BUG-07 — PATCH /agents updates grantex_scopes in lockstep
# ─────────────────────────────────────────────────────────────────


def test_bug07_tools_to_scopes_disambiguates_with_connector_names() -> None:
    """When the user reassigns Aryan to Zoho-only tools, the recomputed
    Grantex scopes must reference zoho_books — never the stale
    quickbooks/stripe wildcards stored at create time.

    Several tool names are ambiguous across connectors (``list_invoices``
    is exposed by QuickBooks, Zoho Books, Xero, etc.). Without the
    connector_names hint the unscoped index returns the
    alphabetically-first match, which is exactly how Aryan ended up
    with quickbooks scopes after being moved to Zoho.
    """
    from auth.grantex_registration import _tools_to_scopes

    scopes = _tools_to_scopes(
        ["list_invoices", "get_balance_sheet", "get_profit_loss"],
        domain="finance",
        connector_names=["zoho_books"],
    )
    joined = " ".join(scopes).lower()
    assert "agenticorg:finance:read" in joined
    assert "zoho_books" in joined, (
        "scoped resolution must produce zoho_books scopes when "
        "connector_names hints zoho_books"
    )
    assert "quickbooks" not in joined, (
        "stale scopes must not appear when the agent is wired to Zoho"
    )
    assert "stripe" not in joined


def test_bug07_patch_handler_calls_scope_refresh_inline() -> None:
    """Source-shape pin: the PATCH /agents handler must recompute and
    persist grantex_scopes whenever authorized_tools changes. Walked by
    grep so a future refactor that drops the inline refresh fails
    immediately rather than silently drifting again.
    """
    text = (
        Path(__file__).resolve().parent.parent.parent
        / "api"
        / "v1"
        / "agents.py"
    ).read_text(encoding="utf-8")
    # Locate the authorized_tools update block.
    marker = '        if "authorized_tools" in update_data:'
    idx = text.find(marker)
    assert idx != -1, "authorized_tools PATCH branch missing"
    block = text[idx : idx + 3000]
    assert "_tools_to_scopes" in block, (
        "PATCH /agents must call _tools_to_scopes when authorized_tools "
        "changes (BUG-07)"
    )
    assert 'cfg["grantex"]' in block or '"grantex"' in block, (
        "refreshed scopes must be persisted into agent.config[grantex]"
    )


# ─────────────────────────────────────────────────────────────────
# BUG-10 — ProtectedRoute respects isHydrating
# ─────────────────────────────────────────────────────────────────


def test_bug10_protected_route_reads_is_hydrating() -> None:
    """Source-shape pin: ProtectedRoute MUST consume isHydrating from
    the auth context and gate the redirect on it. Without this, a hard
    refresh on /dashboard fires <Navigate to="/login"> before /auth/me
    resolves and the user is silently logged out (BUG-10).
    """
    src = (
        Path(__file__).resolve().parent.parent.parent
        / "ui"
        / "src"
        / "components"
        / "ProtectedRoute.tsx"
    ).read_text(encoding="utf-8")
    assert "isHydrating" in src, (
        "ProtectedRoute must read isHydrating from useAuth (BUG-10)"
    )
    # The hydrating branch must short-circuit BEFORE the !isAuthenticated
    # redirect; verified by ordering of substrings.
    hydrate_idx = src.find("isHydrating")
    redirect_idx = src.find('Navigate to="/login"')
    assert 0 < hydrate_idx < redirect_idx, (
        "isHydrating gate must come before the unauthenticated redirect"
    )


def test_bug10_auth_context_exposes_is_hydrating() -> None:
    """The AuthProvider must continue to expose isHydrating in the
    context value — a future refactor that drops it would silently
    re-enable BUG-10.
    """
    src = (
        Path(__file__).resolve().parent.parent.parent
        / "ui"
        / "src"
        / "contexts"
        / "AuthContext.tsx"
    ).read_text(encoding="utf-8")
    assert "isHydrating" in src
    assert "setIsHydrating(false)" in src
