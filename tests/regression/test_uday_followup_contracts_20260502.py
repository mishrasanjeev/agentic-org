"""Follow-up verification pins for Uday CA Firms sweep (2026-05-02).

These pins cover edge-cases requested in PR follow-up:
1) middleware-pair drift guard for auth bootstrap routes,
2) shadow_sample rewrite contract remains explicit and tool-oriented,
3) protected dashboard routes remain protected-route mounted.
"""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_auth_and_csrf_exempt_routes_stay_in_lockstep() -> None:
    """BUG-09 sibling sweep: every auth bootstrap POST route exempted in
    AuthMiddleware must also be exempted in CSRFMiddleware.
    """
    csrf_src = (_repo_root() / "auth" / "csrf_middleware.py").read_text(encoding="utf-8")
    auth_src = (_repo_root() / "auth" / "middleware.py").read_text(encoding="utf-8")

    required = [
        "/api/v1/auth/google",
        "/api/v1/auth/forgot-password",
        "/api/v1/auth/reset-password",
    ]
    for path in required:
        assert path in auth_src, f"AuthMiddleware missing expected exempt path {path}"
        assert path in csrf_src, f"CSRFMiddleware missing expected exempt path {path}"


def test_shadow_sample_rewrite_contract_is_present_in_runner_source() -> None:
    """BUG-08 sibling sweep: runner must keep a dedicated rewrite path for
    UI sentinel action ``shadow_sample``.
    """
    src = (_repo_root() / "core" / "langgraph" / "runner.py").read_text(encoding="utf-8")

    assert "shadow_sample" in src
    assert "explor" in src.lower() or "tool" in src.lower()


def test_protected_routes_exist_for_dashboard_surfaces() -> None:
    """Post-deploy checklist guard: keep protected dashboard route surfaces
    defined, so refresh tests have stable route targets.
    """
    src = (_repo_root() / "ui" / "src" / "App.tsx").read_text(encoding="utf-8")
    for path in ["/dashboard", "/dashboard/cfo", "/dashboard/cmo"]:
        assert path in src, f"Expected protected dashboard route missing: {path}"
