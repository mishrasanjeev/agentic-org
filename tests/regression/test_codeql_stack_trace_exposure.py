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

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    assert "err = str(exc)" not in src, (
        "tenant_ai_credentials.py must not return exception messages "
        "through the probe error field."
    )
    # Positive contract — the type-name-only assignment must be
    # present so we don't regress to a different leaky shape.
    assert "err = type(exc).__name__" in src, (
        "tenant_ai_credentials.py probe error path must assign "
        "``err = type(exc).__name__`` (no exception message)."
    )


def test_probe_provider_errors_do_not_leak_exception_messages() -> None:
    """``probe_provider`` feeds tenant_ai_credentials.py's response.

    It must not format provider/resolver exception text into the
    returned ``error`` field.
    """
    src = (REPO / "core" / "ai_providers" / "health.py").read_text(
        encoding="utf-8"
    )
    assert 'f"Resolver refused: {exc}"' not in src, (
        "probe_provider must not return ProviderNotConfigured details "
        "to the tenant credential test endpoint."
    )
    assert 'f"{type(exc).__name__}: {exc}"' not in src, (
        "probe_provider must not return provider exception messages "
        "to the tenant credential test endpoint."
    )
    assert "error=str(exc)" not in src, (
        "probe_provider should log stable exception metadata only."
    )


@pytest.mark.asyncio
async def test_probe_provider_resolver_error_is_type_name_only(monkeypatch) -> None:
    from core.ai_providers import health
    from core.ai_providers.resolver import ProviderNotConfigured

    async def refused(*_args, **_kwargs):
        raise ProviderNotConfigured(
            "tenant=7 secret=sk-live-internal traceback=/srv/app/core.py:42"
        )

    monkeypatch.setattr(health, "get_provider_credential", refused)

    result = await health.probe_provider(uuid.uuid4(), "openai", "default")

    assert result == {"ok": False, "error": "ProviderNotConfigured"}


@pytest.mark.asyncio
async def test_probe_provider_runtime_error_is_type_name_only(monkeypatch) -> None:
    from core.ai_providers import health

    async def resolved(*_args, **_kwargs):
        return SimpleNamespace(secret="sk-test", provider_config={})

    async def failing_probe(*_args, **_kwargs):
        raise RuntimeError(
            "Authorization: Bearer sk-live-internal at /srv/app/core.py:42"
        )

    monkeypatch.setattr(health, "get_provider_credential", resolved)
    monkeypatch.setattr(health, "_probe_openai", failing_probe)

    result = await health.probe_provider(uuid.uuid4(), "openai", "default")

    assert result == {"ok": False, "error": "RuntimeError"}


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
