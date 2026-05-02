"""Pin the fix for CodeQL ``py/stack-trace-exposure`` alerts #68 + #69
(reported 2026-04-28, addressed 2026-05-02).

Before this fix two API endpoints returned exception messages directly
in the response body:

- ``api/v1/tenant_ai_credentials.py`` — ``error: f"{type(exc).__name__}: {exc}"[:500]``
- ``api/v1/connectors.py`` — ``error: f"{err_name}: {err_msg}"`` in the
  unmapped-failure-class fallback branch

``str(exc)`` on common driver errors (httpx, asyncpg, smtplib) carries
URL fragments, header values, and on some chained-exception traces a
truncated stack frame. The CodeQL alert is correct: the operator
testing a connector should NOT see internal exception text in the
response — the exception class name plus the hand-mapped hint plus a
"see server logs" pointer is enough signal. The full traceback is
captured via ``logger.exception`` in both code paths.

These pins are static — they assert the source files no longer carry
the leaky string-format expressions. A future contributor reintroducing
the leak will trip the test before CodeQL re-flags it.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_tenant_ai_credentials_error_field_does_not_leak_str_exc() -> None:
    """``tenant_ai_credentials.py`` test-credential probe must NOT
    expose ``str(exc)`` to the API response body. The exception class
    name alone is enough for operator triage; details live in
    server logs via ``logger.exception``.
    """
    src = (REPO / "api" / "v1" / "tenant_ai_credentials.py").read_text(
        encoding="utf-8"
    )
    assert 'err = f"{type(exc).__name__}: {exc}"[:500]' not in src, (
        "tenant_ai_credentials.py reintroduced the str(exc) leak. "
        "CodeQL alert #69 will re-fire. The fix must keep err to "
        "type(exc).__name__ only."
    )
    # Positive contract — the type-name-only assignment must be
    # present so we don't regress to a different leaky shape.
    assert "err = type(exc).__name__" in src, (
        "tenant_ai_credentials.py probe error path must assign "
        "``err = type(exc).__name__`` (no exception message)."
    )


def test_connectors_test_endpoint_does_not_leak_err_msg_in_fallback_branch() -> None:
    """``connectors.py`` connector-test endpoint's unmapped-failure
    fallback used to return ``f"{err_name}: {err_msg}"`` where
    ``err_msg = str(exc)[:240]``. That's a stack-trace-exposure leak
    (CodeQL alert #68). The fix returns ``f"{err_name} (see server
    logs for details)"`` — class name only, no message body.
    """
    src = (REPO / "api" / "v1" / "connectors.py").read_text(encoding="utf-8")
    assert 'hint = f"{err_name}: {err_msg}"' not in src, (
        "connectors.py reintroduced the err_msg leak in the fallback "
        "hint branch. CodeQL alert #68 will re-fire."
    )
    assert "see server logs for details" in src, (
        "connectors.py test-endpoint fallback hint must point operators "
        "at server logs (where the full traceback is) instead of "
        "echoing the exception message."
    )
