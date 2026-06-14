"""UI ↔ Backend API contract drift detection.

Pin against the failure mode caught in the 2026-04-30 enterprise gap
analysis: the UI was calling four routes that didn't exist on the
backend (``/audit/enforce``, ``/health/checks``, ``/health/uptime``,
``POST /workflows/runs/{run_id}/cancel``). Each silent 404 rendered as
an empty page, hiding the bug from operators and customers.

This test extracts every ``api.{get,post,put,patch,delete}(...)`` URL
from ``ui/src/**/*.{ts,tsx}`` and asserts each one has a matching
FastAPI route. Failures here block CI before the drift reaches a
deployed surface.

How matching works:
- UI URLs may use template literals like ``/agents/${id}/run``.
  We normalise ``${...}`` → ``{X}``.
- FastAPI routes use bracketed params: ``/agents/{agent_id}/run``.
  We normalise ``{whatever}`` → ``{X}`` for shape comparison.
- Query strings are stripped (``?foo=bar``).
- Hash fragments are stripped (``#section``).
- A small allow-list at the bottom covers external/3rd-party paths
  the UI calls that aren't on this FastAPI app (e.g. demo-request
  proxied to a CRM webhook, OAuth provider callbacks).

Add a new path to ``ALLOWLIST`` only with a specific reason and
ideally a tracking issue link.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.routing import Match

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_SRC = REPO_ROOT / "ui" / "src"

# Paths that are intentionally not on this FastAPI app's surface. Add
# a reason next to each entry. Without an explicit allow-list, every
# external URL would silently pass; with one, drift adds-only is
# caught.
ALLOWLIST: set[str] = {
    # Future Phase-1 work tracked in docs/ENTERPRISE_END_TO_END_GAP_ANALYSIS_2026-04-30.md.
    # Add entries here ONLY with a justification.
}

_HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_SAMPLE_PATH_PARAM = "00000000-0000-0000-0000-000000000001"


# Match either a plain string literal: api.get('/foo'), api.get("/foo")
# or a template literal: api.get(`/foo/${bar}`)
_API_CALL_RE = re.compile(
    r"""api\.(?P<method>get|post|put|patch|delete)\(\s*"""
    r"""(?P<quote>['"`])"""
    r"""(?P<url>/[^'"`]+)"""
    r"""(?P=quote)""",
    re.MULTILINE,
)


def _normalise_ui_path(raw: str) -> str:
    """Drop query/hash and replace template-literal substitutions with {X}."""
    # Strip literal query string and fragment.
    for sep in ("?", "#"):
        idx = raw.find(sep)
        if idx >= 0:
            raw = raw[:idx]
    # Template-literal substitutions: ``${runId}`` or ``${ encodeURIComponent(x) }``
    raw = re.sub(r"\$\{[^}]*\}", "{X}", raw)
    # If a substitution was concatenated to the path WITHOUT a leading
    # slash (e.g. ``/chat/history${params}`` where ``params`` is the whole
    # query string), strip from the `{X}` onward — that's a query-string
    # injection, not a path segment.
    m = re.search(r"[^/]\{X\}", raw)
    if m:
        raw = raw[: m.start() + 1]
    # Trim trailing slash for consistent comparison (FastAPI is /agents not /agents/).
    if raw.endswith("/") and len(raw) > 1:
        raw = raw[:-1]
    return raw


def _collect_ui_calls() -> dict[tuple[str, str], list[Path]]:
    """Return ``{(method, normalised_url): [files_that_call_it]}``."""
    calls: dict[tuple[str, str], list[Path]] = {}
    if not UI_SRC.exists():
        return calls
    for ext in ("ts", "tsx"):
        for path in UI_SRC.rglob(f"*.{ext}"):
            try:
                src = path.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001, S112 — best-effort UI scan, skip unreadable
                continue
            for m in _API_CALL_RE.finditer(src):
                normalised = _normalise_ui_path(m.group("url"))
                method = m.group("method").upper()
                calls.setdefault((method, normalised), []).append(path)
    return calls


def _sample_api_path(ui_path: str) -> str:
    """Convert a normalised UI path into a concrete local API path."""
    path = ui_path.replace("{X}", _SAMPLE_PATH_PARAM)
    if path.startswith("/api/v1"):
        return path
    return f"/api/v1{path}"


def _backend_method_exists(app, method: str, ui_path: str) -> bool:
    scope = {
        "type": "http",
        "path": _sample_api_path(ui_path),
        "root_path": "",
        "method": method.upper(),
    }
    return any(
        route.matches(scope)[0] == Match.FULL
        for route in app.router.routes
        if hasattr(route, "matches")
    )


def _backend_path_exists(app, ui_path: str) -> bool:
    for method in _HTTP_METHODS:
        scope = {
            "type": "http",
            "path": _sample_api_path(ui_path),
            "root_path": "",
            "method": method,
        }
        if any(
            route.matches(scope)[0] != Match.NONE
            for route in app.router.routes
            if hasattr(route, "matches")
        ):
            return True
    return False


def test_every_ui_api_call_has_a_matching_backend_route() -> None:
    """Every ``api.<method>('/url')`` in ui/src must hit a real route.

    Failure here means the UI is making requests that the API can't
    answer — exactly the silent 404 + empty-state pattern the
    2026-04-30 enterprise gap analysis flagged.
    """
    from api.main import app  # noqa: PLC0415

    ui_calls = _collect_ui_calls()
    missing: dict[tuple[str, str], list[Path]] = {}
    for (method, url), callers in ui_calls.items():
        if url in ALLOWLIST:
            continue
        if not _backend_method_exists(app, method, url):
            missing[(method, url)] = callers

    if missing:
        lines = ["UI calls these URLs but the backend has no matching route:"]
        for (method, url), callers in sorted(missing.items()):
            rel_callers = sorted({str(p.relative_to(REPO_ROOT)) for p in callers})
            lines.append(f"  {method} {url}")
            for c in rel_callers:
                lines.append(f"    ← {c}")
        lines.append("")
        lines.append(
            "Fix by either (a) adding the missing FastAPI route, "
            "(b) updating the UI to call an existing route, or "
            "(c) adding the path to ALLOWLIST in this file with "
            "an explicit justification (rare — used only for paths "
            "deliberately served by something other than this app)."
        )
        pytest.fail("\n".join(lines))


def test_ui_call_extractor_finds_known_calls() -> None:
    """Sanity check on the regex itself: a few well-known calls must be
    detected, otherwise the test above would silently pass on an empty set.

    These are stable — we ship these URLs from the UI today.
    """
    ui_calls = _collect_ui_calls()
    urls = {url for _method, url in ui_calls}
    for must_find in ("/audit", "/health", "/agents"):
        assert must_find in urls, (
            f"UI URL extractor regression: {must_find!r} should have been "
            "found in ui/src — the regex broke or the call site moved."
        )


def test_allowlist_entries_are_not_actually_in_backend() -> None:
    """An ALLOWLIST entry that's actually on the backend is dead config —
    drop it. This prevents the allow-list from masking real coverage."""
    if not ALLOWLIST:
        return
    from api.main import app  # noqa: PLC0415

    stale = {path for path in ALLOWLIST if _backend_path_exists(app, path)}
    assert not stale, (
        f"These ALLOWLIST entries are now real backend routes — remove "
        f"them from the allow-list so drift detection re-engages: "
        f"{sorted(stale)}"
    )


def test_phase_1_endpoints_are_now_registered() -> None:
    """Direct pin against the four routes the 2026-04-30 audit called out.

    Even if the contract test above passes (e.g., because the UI call sites
    were edited away), these four MUST exist on the backend going forward.
    """
    required = {
        ("GET", "/audit/enforce"),
        ("GET", "/health/checks"),
        ("GET", "/health/uptime"),
        ("POST", "/workflows/runs/{X}/cancel"),
    }
    from api.main import app  # noqa: PLC0415

    missing = {
        (method, path)
        for method, path in required
        if not _backend_method_exists(app, method, path)
    }
    assert not missing, (
        f"Routes from the 2026-04-30 enterprise gap analysis are not "
        f"registered on the FastAPI app: {sorted(missing)}"
    )
