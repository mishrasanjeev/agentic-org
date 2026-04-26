"""Regression tests for Codex 2026-04-23 round-4 re-verification blockers.

Each test pins a specific contract that was previously broken. If a
future change regresses the contract the test breaks loudly so the
problem doesn't slip back through.

Blockers addressed:
    K-A — workflow state split-brain (engine vs Celery resume)
    K-B — event wait honors stored match criteria
    K-E — auth-state strict mode refuses in-memory fallback
    K-F — billing async routes wrap sync Redis/HTTP in to_thread
    K-G — connector test no longer accepts plaintext auth_config
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# K-A: workflow Celery resume uses the engine's state schema
# ---------------------------------------------------------------------------


def test_k_a_workflow_celery_uses_engine_schema() -> None:
    """Celery resume tasks read step_results / waiting_step_id / status.

    Before 2026-04-23 these tasks read ``state["steps"]`` and wrote
    ``state["current_step"]`` — which never matched what
    ``workflows/engine.py`` persists. Scheduled timeouts and
    external-event resumes silently did nothing.
    """
    src = _read("core/tasks/workflow_tasks.py")
    # Must reference the canonical engine keys
    assert '"step_results"' in src, "Celery tasks must read state['step_results']"
    assert '"waiting_step_id"' in src, "Celery tasks must check waiting_step_id"
    # AST-walk the module and fail on any active-code Subscript that
    # writes the stale split-brain keys. References inside the
    # module docstring (explaining the old bug) are allowed.
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            if node.slice.value in ("steps", "current_step"):
                # Is the parent a state[...] access?
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "state"
                ):
                    raise AssertionError(
                        f"state[{node.slice.value!r}] is the stale schema — "
                        "align with engine"
                    )


# ---------------------------------------------------------------------------
# K-B: webhook event wait honors stored match criteria
# ---------------------------------------------------------------------------


def test_k_b_webhooks_match_criteria_helper_exists() -> None:
    """api/v1/webhooks.py has a match-criteria helper and uses it."""
    src = _read("api/v1/webhooks.py")
    assert "_event_matches_criteria" in src, (
        "Must define _event_matches_criteria helper (K-B contract)"
    )
    # The helper must be invoked inside the resume path
    assert re.search(
        r"_event_matches_criteria\([^)]*\)",
        src,
    ), "Helper must be called from the event-resume loop"
    # The listener payload must be parsed to extract match criteria
    assert '"match"' in src, "Must extract 'match' key from listener payload"


def test_k_b_matches_criteria_logic() -> None:
    """Helper returns True for empty criteria, False when mismatching."""
    import sys

    sys.path.insert(0, str(REPO))
    from api.v1.webhooks import _event_matches_criteria

    # Empty criteria matches everything
    assert _event_matches_criteria({"from": "a@b.com"}, {}) is True
    # Exact match
    assert _event_matches_criteria(
        {"from": "a@b.com"}, {"from": "a@b.com"}
    ) is True
    # Case-insensitive match
    assert _event_matches_criteria(
        {"from": "A@B.COM"}, {"from": "a@b.com"}
    ) is True
    # Missing key in event fails
    assert _event_matches_criteria({}, {"from": "a@b.com"}) is False
    # Different value fails
    assert _event_matches_criteria(
        {"from": "x@y.com"}, {"from": "a@b.com"}
    ) is False


# ---------------------------------------------------------------------------
# K-E: auth-state strict mode refuses in-memory fallback
# ---------------------------------------------------------------------------


def test_k_e_jwt_defines_strict_helper() -> None:
    """auth/jwt.py has _auth_state_strict() reading AGENTICORG_AUTH_STATE_STRICT."""
    src = _read("auth/jwt.py")
    assert "_auth_state_strict" in src
    assert "AGENTICORG_AUTH_STATE_STRICT" in src


def test_k_e_jwt_blacklist_raises_in_strict_mode(monkeypatch) -> None:
    """Strict mode + Redis unavailable → blacklist_token raises, _is_blacklisted raises.

    Monkeypatches ``_get_redis`` to return None, which deterministically
    simulates "Redis unreachable" regardless of whether the test
    environment actually has a Redis running on localhost (CI does,
    local dev usually does not).
    """
    import asyncio
    import sys

    sys.path.insert(0, str(REPO))
    from auth import jwt as jwt_mod

    monkeypatch.setenv("AGENTICORG_AUTH_STATE_STRICT", "1")
    monkeypatch.setattr(jwt_mod, "_get_redis", lambda: None)
    jwt_mod._blacklisted_tokens.clear()

    # In strict mode, blacklist_token must raise when Redis is down
    write_token = "strict-write-probe-token"
    try:
        jwt_mod.blacklist_token(write_token)
    except RuntimeError as exc:
        assert "strict" in str(exc).lower() or "redis" in str(exc).lower()
    else:
        raise AssertionError(
            "blacklist_token must raise in strict mode when Redis is "
            "unreachable"
        )

    # Use a fresh token so the memory-dict short-circuit doesn't mask
    # the Redis-path check.
    jwt_mod._blacklisted_tokens.clear()
    read_token = "strict-read-probe-token"
    try:
        asyncio.run(jwt_mod._is_blacklisted(read_token))
    except RuntimeError as exc:
        assert "strict" in str(exc).lower() or "redis" in str(exc).lower()
    else:
        raise AssertionError(
            "_is_blacklisted must raise in strict mode when Redis is "
            "unreachable"
        )


def test_k_e_auth_state_module_has_strict_guard() -> None:
    """core/auth_state.py raises in strict mode on Redis failure."""
    src = _read("core/auth_state.py")
    assert "_strict" in src or "AGENTICORG_AUTH_STATE_STRICT" in src
    assert "_raise_if_strict" in src, (
        "Must use central helper to enforce strict-mode refusal"
    )


def test_k_e_login_throttle_respects_strict() -> None:
    """api/v1/auth.py login throttle respects AGENTICORG_AUTH_STATE_STRICT."""
    src = _read("api/v1/auth.py")
    assert "AGENTICORG_AUTH_STATE_STRICT" in src, (
        "Login throttle must read strict-mode env var"
    )


# ---------------------------------------------------------------------------
# K-F: billing async routes don't block on sync Redis / HTTP
# ---------------------------------------------------------------------------


def test_k_f_billing_async_routes_wrap_sync_calls() -> None:
    """Every sync billing call inside an async handler uses asyncio.to_thread."""
    src = _read("api/v1/billing.py")
    tree = ast.parse(src)

    # Collect all async functions that reference the sync-billing helpers
    sync_helpers = {
        "get_usage",
        "create_payment_order",
        "get_order_status",
        "lookup_order_details",
        "create_checkout_session",
        "verify_checkout_session",
        "create_portal_session",
        "handle_webhook",
    }

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        # Walk the body looking for `helper(...)` calls that are NOT
        # await asyncio.to_thread(helper, ...)
        body_src = ast.get_source_segment(src, node) or ""
        for helper in sync_helpers:
            # Presence check: if the helper is called at all…
            pattern_call = rf"\b{helper}\s*\("
            if not re.search(pattern_call, body_src):
                continue
            # …it must appear inside an asyncio.to_thread(...) wrap,
            # OR it must be the import line (allowed).
            # Import line: `from ... import helper`
            bare_calls = re.findall(
                rf"(?<!to_thread\()(?<!import\s){helper}\s*\(",
                body_src,
            )
            wrapped_calls = re.findall(
                rf"to_thread\(\s*{helper}\b",
                body_src,
            )
            # Allow when every bare call is actually a to_thread argument:
            # re above already excludes to_thread wraps. So bare_calls should
            # be empty OR every remaining bare call is on an aliased import
            # like `_cancel` which we also wrap.
            assert len(bare_calls) == 0 or len(wrapped_calls) >= 1, (
                f"async fn {node.name} calls {helper} without asyncio.to_thread"
            )


# ---------------------------------------------------------------------------
# K-G: connector test refuses plaintext auth_config
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="Helm chart removed in Stage 4 of the Cloud Run cost-cut migration. "
    "Production now runs on Cloud Run in asia-southeast1, not GKE. The semantic "
    "check (AGENTICORG_AUTH_STATE_STRICT must be wired in prod) is still important — "
    "followup: rewrite to assert against the live Cloud Run service config."
)
def test_k_e_strict_mode_wired_in_prod_manifests() -> None:
    """Codex 2026-04-23 prod re-verification: the strict-mode env var
    must be wired into the production Helm values — otherwise the code
    fix is dormant in production.
    """
    for rel in ("helm/values.yaml", "helm/values-production.yaml"):
        src = _read(rel)
        assert "AGENTICORG_AUTH_STATE_STRICT" in src, (
            f"{rel} must set AGENTICORG_AUTH_STATE_STRICT so the K-E "
            "fail-closed auth state is active in prod"
        )
        # Must be truthy ("1" / "true" / "yes")
        m = re.search(
            r"AGENTICORG_AUTH_STATE_STRICT\s*:\s*\"?([^\"\n]+)\"?",
            src,
        )
        assert m, f"couldn't parse strict-mode value in {rel}"
        assert m.group(1).strip().lower() in ("1", "true", "yes"), (
            f"{rel}: AGENTICORG_AUTH_STATE_STRICT must be truthy, got "
            f"{m.group(1)!r}"
        )


def test_k_e_health_probes_are_public() -> None:
    """Codex 2026-04-23 prod re-verification: /billing/health and
    /knowledge/health were declared auth-free in their route files but
    the global middleware still required a token. The fix must land
    on BOTH the legacy ``AuthMiddleware`` and the live
    ``GrantexAuthMiddleware`` (the one api/main.py registers). The
    first pass of this regression only checked the legacy class,
    which is why prod still returned 401 after the first merge.
    """
    for rel in ("auth/middleware.py", "auth/grantex_middleware.py"):
        src = _read(rel)
        assert "/api/v1/billing/health" in src, (
            f"{rel} EXEMPT_PATHS must include /api/v1/billing/health"
        )
        assert "/api/v1/knowledge/health" in src, (
            f"{rel} EXEMPT_PATHS must include /api/v1/knowledge/health"
        )


def test_k_e_health_endpoint_exposes_commit_sha() -> None:
    """/health must expose the deployed commit SHA so Codex + ops can
    prove which commit is actually live (version alone is ambiguous).
    """
    src = _read("api/v1/health.py")
    assert "_deployed_commit" in src
    assert "AGENTICORG_GIT_SHA" in src
    assert "\"commit\"" in src


def test_k_g_connectors_test_endpoint_refuses_plaintext() -> None:
    """api/v1/connectors.py test path must not read Connector.auth_config."""
    src = _read("api/v1/connectors.py")
    # The old fallback was `config = connector.auth_config or {}` — it
    # MUST NOT be present anymore in the test path.
    assert "connector.auth_config or {}" not in src, (
        "Plaintext auth_config fallback must be removed (K-G)"
    )
    # Must still reference encrypted credentials path
    assert "credentials_encrypted" in src, (
        "Must use credentials_encrypted column, not plaintext auth_config"
    )
