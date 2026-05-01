"""PR-F2 regression pins for SEC-002 — e2e fixtures use cookie-first
auth instead of ``localStorage.setItem("token", ...)``.

Closes the residual called out in PR-F: the production code was
already cookie-first, but Playwright e2e specs still seeded auth via
``page.evaluate(() => localStorage.setItem("token", ...))``. PR-F2
swept all 27 spec files + the shared ``helpers/auth.ts`` to use
``page.context().addCookies(...)`` (via the new ``setSessionToken``
helper). The fixtures now match the production cookie-based posture
exactly, so a Playwright pass means cookie auth actually works
end-to-end.

The test below greps the entire ``ui/e2e`` tree for the forbidden
patterns. Doc-comment occurrences in ``helpers/auth.ts`` are
explicitly excluded — they describe the migration. Any setItem /
getItem of token/user OUTSIDE that helper file fails the test with
file + line + offending snippet.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
UI_E2E = REPO_ROOT / "ui" / "e2e"

# Forbidden: setItem / getItem on token or user keys.
# (removeItem is allowed — used for cleanup of legacy state.)
_FORBIDDEN = [
    re.compile(r'localStorage\s*\.\s*setItem\s*\(\s*["\']token["\']'),
    re.compile(r'localStorage\s*\.\s*getItem\s*\(\s*["\']token["\']'),
    re.compile(r'localStorage\s*\.\s*setItem\s*\(\s*["\']user["\']'),
    re.compile(r'localStorage\s*\.\s*getItem\s*\(\s*["\']user["\']'),
]

# helpers/auth.ts has doc-comment references that explain the
# migration. They are not code; allow them.
_DOCSTRING_OK_FILES = {
    str((UI_E2E / "helpers" / "auth.ts").relative_to(REPO_ROOT)).replace("\\", "/"),
}


def _walk_e2e() -> list[Path]:
    if not UI_E2E.exists():
        pytest.skip(f"ui/e2e not present in checkout at {UI_E2E}")
    return [
        p
        for p in UI_E2E.rglob("*")
        if p.is_file() and p.suffix == ".ts"
    ]


def test_sec_002_pr_f2_no_localstorage_token_set_or_get_in_e2e_specs() -> None:
    offenders: list[str] = []
    for path in _walk_e2e():
        rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            # Skip TypeScript / JS line comments and JSDoc lines that
            # mention the legacy pattern in passing.
            if stripped.startswith(("//", "*", "/*")):
                continue
            for pattern in _FORBIDDEN:
                if pattern.search(line):
                    offenders.append(f"{rel}:{lineno}: {stripped}")
    assert not offenders, (
        "SEC-002 (PR-F2): e2e specs must not seed auth via localStorage. "
        "Use ``setSessionToken(page, token)`` from helpers/auth instead. "
        "Offending lines:\n  "
        + "\n  ".join(offenders)
    )


def test_sec_002_pr_f2_helpers_export_cookie_setter() -> None:
    """The shared helper must export ``setSessionToken`` so spec files
    have one place to convert if the cookie name ever changes."""
    helper = (UI_E2E / "helpers" / "auth.ts").read_text(encoding="utf-8")
    assert "export async function setSessionToken" in helper, (
        "helpers/auth.ts must export setSessionToken for cookie-first "
        "session seeding."
    )
    assert "agenticorg_session" in helper, (
        "setSessionToken must seed the production session cookie name."
    )
    assert "addCookies" in helper, (
        "setSessionToken must use page.context().addCookies(...) "
        "rather than page.evaluate(...localStorage...)."
    )


def test_sec_002_pr_f2_authenticate_uses_cookie_helper() -> None:
    """The shared ``authenticate`` helper must call ``setSessionToken``
    rather than writing to localStorage directly."""
    helper = (UI_E2E / "helpers" / "auth.ts").read_text(encoding="utf-8")
    # Find the authenticate function body.
    m = re.search(
        r"export async function authenticate\([^)]*\)\s*:\s*Promise<void>\s*\{(.*?)\n\}",
        helper,
        re.DOTALL,
    )
    assert m, "authenticate() helper not found in helpers/auth.ts"
    body = m.group(1)
    assert "setSessionToken" in body, (
        "authenticate() must delegate to setSessionToken so the cookie "
        "fixture matches production posture."
    )
    assert 'localStorage.setItem("token"' not in body, (
        "authenticate() must not write to localStorage anymore."
    )
