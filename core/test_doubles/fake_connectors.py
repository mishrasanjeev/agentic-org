"""Hermetic fake connector HTTP layer (Foundation #7 PR-D).

Connectors call third-party APIs (Gmail, Tally, GSTN, Stripe,
Salesforce, ...) through ``httpx.AsyncClient`` instances built
in ``connectors.framework.base_connector.BaseConnector.connect()``.

Real calls are slow, flaky, charge money, and require credentials
that PR CI can't (and shouldn't) carry. This module provides a
single stub-server seam: every connector's AsyncClient gets a
``httpx.MockTransport`` whose router consults an in-process
registry of ``(method, url_pattern) -> response`` rules.

Activation: ``AGENTICORG_TEST_FAKE_CONNECTORS=1``. The conftest
sets this by default for every test run.

Default behavior for unregistered URLs: **404**, not 200. Tests
that hit an endpoint they didn't mock should fail loudly so the
gap is visible — not silently succeed with a generic body. Tests
that legitimately don't care about a particular call can register
a wildcard rule.

Registration::

    from core.test_doubles import fake_connectors

    fake_connectors.register(
        method="GET",
        url_contains="/api/customers",
        status=200,
        json={"customers": [{"id": 1, "name": "Acme"}]},
    )

    # Or for the wildcard match-all per connector:
    fake_connectors.register(
        method="*",
        url_contains="api.gmail.com",
        status=200,
        json={"messages": []},
    )

Inspection::

    fake_connectors.request_log()  # list of every call attempted
    fake_connectors.count()        # how many requests made
    fake_connectors.reset()        # clear registry + log

The autouse fixture in tests/conftest.py calls ``reset()`` before
each test so registrations + the request log don't leak.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class StubRule:
    """One registered stub response."""

    method: str  # "GET", "POST", "*" for any
    url_contains: str  # case-insensitive substring match
    status: int = 200
    json_body: Any = None
    text_body: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class CapturedRequest:
    """One captured outbound HTTP attempt."""

    method: str
    url: str
    matched_rule_index: int | None = None  # None = default 404
    timestamp: float = field(default_factory=time.time)


_RULES: list[StubRule] = []
_REQUEST_LOG: list[CapturedRequest] = []


def is_active() -> bool:
    """True iff ``AGENTICORG_TEST_FAKE_CONNECTORS`` is truthy."""
    return os.getenv("AGENTICORG_TEST_FAKE_CONNECTORS", "").lower() in (
        "1",
        "true",
        "yes",
    )


def register(
    *,
    method: str,
    url_contains: str,
    status: int = 200,
    json: Any = None,
    text: str | None = None,
    headers: dict[str, str] | None = None,
) -> None:
    """Register a stub response for any request matching the rule.

    Match precedence: most-recently-registered wins. The ``method``
    can be ``"*"`` for any HTTP verb. ``url_contains`` is a
    case-insensitive substring match.

    If both ``json`` and ``text`` are given, ``text`` wins.
    """
    if not url_contains.strip():
        raise ValueError("url_contains must be non-empty")
    _RULES.append(
        StubRule(
            method=method.upper(),
            url_contains=url_contains.lower(),
            status=status,
            json_body=json,
            text_body=text,
            headers=headers or {},
        )
    )


def request_log() -> list[CapturedRequest]:
    """Return a copy of every captured request, oldest first."""
    return list(_REQUEST_LOG)


def count() -> int:
    """Total requests captured since the last reset."""
    return len(_REQUEST_LOG)


def reset() -> None:
    """Clear the rule registry + request log."""
    _RULES.clear()
    _REQUEST_LOG.clear()


# ─────────────────────────────────────────────────────────────────
# The MockTransport handler
# ─────────────────────────────────────────────────────────────────


def _match(request: httpx.Request) -> tuple[int | None, StubRule | None]:
    url_l = str(request.url).lower()
    method_u = request.method.upper()
    # Most-recently-registered wins → walk in reverse.
    for i in range(len(_RULES) - 1, -1, -1):
        rule = _RULES[i]
        if rule.method != "*" and rule.method != method_u:
            continue
        if rule.url_contains in url_l:
            return i, rule
    return None, None


def _handler(request: httpx.Request) -> httpx.Response:
    idx, rule = _match(request)
    _REQUEST_LOG.append(
        CapturedRequest(
            method=request.method,
            url=str(request.url),
            matched_rule_index=idx,
        )
    )
    if rule is None:
        # Default: 404 with a marker body so tests see a clear
        # "you didn't mock this" signal rather than a silent 200.
        return httpx.Response(
            404,
            json={
                "fake_connectors_error": "no_rule_matched",
                "method": request.method,
                "url": str(request.url),
                "hint": (
                    "Add a fake_connectors.register(method=, "
                    "url_contains=, ...) call in your test."
                ),
            },
        )
    if rule.text_body is not None:
        return httpx.Response(
            rule.status, text=rule.text_body, headers=rule.headers
        )
    return httpx.Response(
        rule.status, json=rule.json_body, headers=rule.headers
    )


def build_transport() -> httpx.MockTransport:
    """Return a MockTransport bound to the in-process registry.

    Every connector AsyncClient that wants hermetic mode constructs
    one of these and passes it as ``transport=``. The handler
    closure references the module-level _RULES + _REQUEST_LOG so a
    single transport works for the whole process.
    """
    return httpx.MockTransport(_handler)


# ─────────────────────────────────────────────────────────────────
# Global httpx patch — covers auth-time + one-off clients
# ─────────────────────────────────────────────────────────────────
#
# BaseConnector.connect() attaches the MockTransport to the long-
# lived ``self._client``, but many connectors build their own
# short-lived ``httpx.AsyncClient`` inside ``_authenticate`` (OAuth
# refresh in Gmail, Google Calendar, Salesforce) or in one-off
# helpers (GSTN, ServiceNow, Brandwatch, ...). Those clients bypass
# the per-instance transport and would hit the real network.
#
# We patch ``httpx.AsyncClient.__init__`` once so that whenever the
# flag is active AND the caller didn't pass an explicit
# ``transport=``, the MockTransport is injected. Activation is re-
# checked on every call, so per-test opt-out
# (``AGENTICORG_TEST_FAKE_CONNECTORS=""``) still works. Explicit
# ``transport=`` arguments are preserved.

_original_async_client_init: Any = None
_patch_installed = False


def install_global_patch() -> None:
    """Patch ``httpx.AsyncClient`` so auth-time clients also stub.

    Idempotent. Called once from ``tests/conftest.py`` at import
    time. Outside the test suite this is never installed, so the
    production cold path is untouched.
    """
    global _original_async_client_init, _patch_installed
    if _patch_installed:
        return
    _original_async_client_init = httpx.AsyncClient.__init__

    def _patched_init(
        self: httpx.AsyncClient, *args: Any, **kwargs: Any
    ) -> None:
        if is_active() and "transport" not in kwargs:
            kwargs["transport"] = build_transport()
        _original_async_client_init(self, *args, **kwargs)

    httpx.AsyncClient.__init__ = _patched_init  # type: ignore[method-assign]
    _patch_installed = True
