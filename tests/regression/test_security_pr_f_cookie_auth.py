"""PR-F regression pins for SEC-002 (cookie-first browser auth).

Closes:

- SEC-2026-05-P0-002 — Browser bearer tokens were persisted in
  localStorage. Any XSS, malicious browser extension, or third-party
  script could steal them. PR-F removes every ``localStorage.setItem``
  + ``localStorage.getItem`` of ``token`` / ``user`` from ``ui/src``
  so the HttpOnly ``agenticorg_session`` cookie is the only browser
  session carrier.

The test below greps the entire ``ui/src`` tree (production code +
unit tests + e2e specs are scoped separately) for the forbidden
patterns. ``localStorage.removeItem(...)`` is INTENTIONALLY allowed
so legacy state from older clients can be cleaned up on logout / 401.

If a future contributor adds a ``localStorage.setItem("token", ...)``
or ``localStorage.getItem("token")`` back, this test fails immediately
with the file + line that reintroduced the gap.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
UI_SRC = REPO_ROOT / "ui" / "src"

# Patterns that MUST NOT appear in browser source code.
# The wildcard ``\s*`` between ``localStorage.setItem`` and the open paren
# tolerates code-style whitespace; the alternation handles single + double
# quotes around the key name.
_FORBIDDEN_PATTERNS = [
    re.compile(r'localStorage\s*\.\s*setItem\s*\(\s*["\']token["\']'),
    re.compile(r'localStorage\s*\.\s*getItem\s*\(\s*["\']token["\']'),
    re.compile(r'localStorage\s*\.\s*setItem\s*\(\s*["\']user["\']'),
    re.compile(r'localStorage\s*\.\s*getItem\s*\(\s*["\']user["\']'),
]

# Some directories are intentionally allowed to keep legacy patterns:
# - e2e specs simulate an older client to verify backwards-compat
#   purge behavior; they are not in ``ui/src``.
# - unit tests in ``ui/src/__tests__`` should ALSO be cookie-first now,
#   so we DO scan them.
_ALLOWED_FILES: set[str] = set()


def _walk_ui_src() -> list[Path]:
    if not UI_SRC.exists():
        pytest.skip(f"ui/src not present in checkout at {UI_SRC}")
    return [
        p
        for p in UI_SRC.rglob("*")
        if p.is_file()
        and p.suffix in {".ts", ".tsx", ".js", ".jsx"}
    ]


def test_sec_002_no_localstorage_token_set_or_get_in_ui_src() -> None:
    """SEC-002 pin — fail with file+line if any forbidden pattern
    re-appears in ``ui/src``.
    """
    offenders: list[str] = []
    for path in _walk_ui_src():
        if str(path.relative_to(REPO_ROOT)) in _ALLOWED_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in _FORBIDDEN_PATTERNS:
                if pattern.search(line):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}"
                    )
    assert not offenders, (
        "SEC-002 (PR-F): browser source code must not store token / user "
        "in localStorage. Use the HttpOnly session cookie instead. "
        "Offending lines:\n  "
        + "\n  ".join(offenders)
    )


def test_sec_002_authcontext_does_not_persist_token() -> None:
    """The AuthContext must not write ``token`` to localStorage. The
    only acceptable localStorage write/remove is the legacy purge in
    ``_purgeLegacyTokenStorage``."""
    auth_ctx = (UI_SRC / "contexts" / "AuthContext.tsx").read_text(encoding="utf-8")
    assert 'localStorage.setItem("token"' not in auth_ctx
    assert 'localStorage.setItem("user"' not in auth_ctx
    assert 'localStorage.getItem("token")' not in auth_ctx
    assert 'localStorage.getItem("user")' not in auth_ctx


def test_sec_002_api_client_does_not_inject_bearer_from_localstorage() -> None:
    """``ui/src/lib/api.ts`` must rely on the cookie (withCredentials)
    rather than a bearer token pulled from localStorage."""
    api_ts = (UI_SRC / "lib" / "api.ts").read_text(encoding="utf-8")
    assert "withCredentials: true" in api_ts, (
        "axios client must keep withCredentials=true so the browser ships "
        "the session cookie on every request."
    )
    assert 'localStorage.getItem("token")' not in api_ts, (
        "api.ts must not read the bearer token from localStorage; the "
        "session cookie carries the user identity."
    )
    assert "Authorization: `Bearer ${token}`" not in api_ts, (
        "api.ts must not synthesise an Authorization: Bearer header from "
        "localStorage; cookie-first."
    )


def test_sec_002_onboarding_uses_cookie_api_client_not_context_token() -> None:
    """Onboarding must not send ``Authorization: Bearer null`` after
    AuthContext made browser ``token`` intentionally null."""
    onboarding = (UI_SRC / "pages" / "Onboarding.tsx").read_text(encoding="utf-8")
    assert "import api from" in onboarding
    assert "const { user, token } = useAuth()" not in onboarding
    assert "Authorization: `Bearer ${token}`" not in onboarding
    assert 'api.post("/org/invite"' in onboarding
    assert 'api.put("/org/onboarding"' in onboarding


def test_sec_002_authcontext_purges_legacy_token_storage() -> None:
    """First-render cleanup must remove any legacy localStorage left
    over from pre-PR-F clients so the regression-test grep stays
    clean even on upgrade."""
    auth_ctx = (UI_SRC / "contexts" / "AuthContext.tsx").read_text(encoding="utf-8")
    assert "_purgeLegacyTokenStorage" in auth_ctx, (
        "AuthContext must call _purgeLegacyTokenStorage on mount so "
        "stale tokens from older clients are wiped."
    )
    assert 'localStorage.removeItem("token")' in auth_ctx
    assert 'localStorage.removeItem("user")' in auth_ctx
