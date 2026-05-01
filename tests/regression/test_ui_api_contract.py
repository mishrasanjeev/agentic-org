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


# Match either a plain string literal: api.get('/foo'), api.get("/foo")
# or a template literal: api.get(`/foo/${bar}`)
_API_CALL_RE = re.compile(
    r"""api\.(?:get|post|put|patch|delete)\(\s*"""
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


def _normalise_route_path(raw: str) -> str:
    """Replace bracketed FastAPI path params with {X} for shape comparison."""
    out = re.sub(r"\{[^}]+\}", "{X}", raw)
    if out.endswith("/") and len(out) > 1:
        out = out[:-1]
    return out


def _collect_ui_calls() -> dict[str, list[Path]]:
    """Return ``{normalised_url: [files_that_call_it]}``."""
    calls: dict[str, list[Path]] = {}
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
                calls.setdefault(normalised, []).append(path)
    return calls


def _collect_backend_routes() -> set[str]:
    """Return the set of normalised paths registered on the FastAPI app.

    Imported lazily so this test doesn't pay the API import cost when
    only the UI call extractor is used.
    """
    from api.main import app  # noqa: PLC0415

    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        # FastAPI mounts /api/v1/* under a prefix; the UI calls the
        # path WITHOUT the /api/v1 prefix because the shared Axios
        # client adds it. Strip the prefix here so they compare.
        if path.startswith("/api/v1/"):
            path = path[len("/api/v1") :]
        elif path.startswith("/api/v1"):
            path = path[len("/api/v1") :] or "/"
        paths.add(_normalise_route_path(path))
    return paths


def test_every_ui_api_call_has_a_matching_backend_route() -> None:
    """Every ``api.<method>('/url')`` in ui/src must hit a real route.

    Failure here means the UI is making requests that the API can't
    answer — exactly the silent 404 + empty-state pattern the
    2026-04-30 enterprise gap analysis flagged.
    """
    ui_calls = _collect_ui_calls()
    backend_routes = _collect_backend_routes()

    missing: dict[str, list[Path]] = {}
    for url, callers in ui_calls.items():
        if url in ALLOWLIST:
            continue
        if url not in backend_routes:
            missing[url] = callers

    if missing:
        lines = ["UI calls these URLs but the backend has no matching route:"]
        for url, callers in sorted(missing.items()):
            rel_callers = sorted({str(p.relative_to(REPO_ROOT)) for p in callers})
            lines.append(f"  {url}")
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
    for must_find in ("/audit", "/health", "/agents"):
        assert must_find in ui_calls, (
            f"UI URL extractor regression: {must_find!r} should have been "
            "found in ui/src — the regex broke or the call site moved."
        )


def test_allowlist_entries_are_not_actually_in_backend() -> None:
    """An ALLOWLIST entry that's actually on the backend is dead config —
    drop it. This prevents the allow-list from masking real coverage."""
    if not ALLOWLIST:
        return
    backend_routes = _collect_backend_routes()
    stale = ALLOWLIST & backend_routes
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
    backend_routes = _collect_backend_routes()
    required = {
        "/audit/enforce",
        "/health/checks",
        "/health/uptime",
        "/workflows/runs/{X}/cancel",
    }
    missing = required - backend_routes
    assert not missing, (
        f"Routes from the 2026-04-30 enterprise gap analysis are not "
        f"registered on the FastAPI app: {sorted(missing)}"
    )
