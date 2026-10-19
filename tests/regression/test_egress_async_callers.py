# SPDX-License-Identifier: Apache-2.0
"""Egress validation must not block the event loop (audit 2026-09-13).

``core.security.egress.validate_public_url`` resolves DNS with the blocking
``socket.getaddrinfo``. Every caller that lives inside an ``async def`` must
use ``validate_public_url_async`` (``loop.getaddrinfo``) instead. The two
variants share one policy implementation, so the parity test below pins
that no policy check is duplicated or drifted.
"""

from __future__ import annotations

import ast
import asyncio
import ipaddress
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("api", "core", "connectors", "auth", "workflows")
SYNC_VARIANTS = frozenset(
    {"validate_public_url", "validate_public_hostname", "resolve_public_hostname_addresses"}
)


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _sync_egress_calls_inside_async_defs() -> list[str]:
    offenders: list[str] = []
    for root in SCAN_ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            if path.name == "egress.py":
                continue
            source = path.read_text(encoding="utf-8")
            # Parse only files that name a sync variant: a whole-tree AST walk
            # under coverage tracing is slow enough to trip the per-test timeout.
            if not any(name in source for name in SYNC_VARIANTS):
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                # Sandbox interpreters older than the repo target skip the file.
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.AsyncFunctionDef):
                    continue
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and _call_name(sub) in SYNC_VARIANTS:
                        rel = path.relative_to(REPO)
                        offenders.append(f"{rel}:{sub.lineno} async {node.name}() -> {_call_name(sub)}")
    return offenders


def test_no_async_def_calls_the_sync_egress_validator() -> None:
    offenders = _sync_egress_calls_inside_async_defs()
    assert offenders == [], (
        "sync DNS egress validation inside async code (blocks the event loop); "
        "use validate_public_url_async / validate_public_hostname_async:\n" + "\n".join(offenders)
    )


def test_async_only_helpers_are_async() -> None:
    """Helpers that only serve async request handlers must be coroutines."""
    import inspect

    from api.v1.connectors import _assert_public_base_url
    from api.v1.tenant_ai_credentials import _validate_provider_config
    from connectors.framework.base_connector import _validate_connector_egress_url_async
    from core.ai_providers.health import _openai_models_url, _validate_openai_probe_base

    for fn in (
        _assert_public_base_url,
        _validate_provider_config,
        _validate_connector_egress_url_async,
        _openai_models_url,
        _validate_openai_probe_base,
    ):
        assert inspect.iscoroutinefunction(fn), fn.__qualname__


@pytest.mark.parametrize(
    ("url", "kwargs", "reason"),
    [
        ("http://example.com", {}, "scheme"),
        ("https://user:pw@example.com", {}, "userinfo"),
        ("https:///path", {}, "missing_host"),
        ("https://localhost/x", {}, "local_host"),
        ("https://10.0.0.1/x", {}, "ip_literal"),
        ("https://evil.example", {"allowed_hosts": ("good.example",)}, "host_not_allowed"),
        ("https://evil.example", {"allowed_domains": ("good.example",)}, "domain_not_allowed"),
    ],
)
def test_async_variant_applies_the_same_policy_as_sync(url: str, kwargs: dict, reason: str) -> None:
    from core.security.egress import EgressValidationError, validate_public_url, validate_public_url_async

    with pytest.raises(EgressValidationError) as sync_exc:
        validate_public_url(url, require_dns=False, **kwargs)
    with pytest.raises(EgressValidationError) as async_exc:
        asyncio.run(validate_public_url_async(url, require_dns=False, **kwargs))
    assert sync_exc.value.reason == reason
    assert async_exc.value.reason == reason


def test_async_variant_resolves_dns_on_the_loop_and_blocks_private_answers() -> None:
    from core.security import egress

    async def _fake_async(host: str):
        return (ipaddress.ip_address("10.1.2.3"),)

    def _boom(*_a, **_k):  # pragma: no cover - must never be reached
        raise AssertionError("sync socket.getaddrinfo used from the async variant")

    with patch.object(egress, "_resolve_host_async", _fake_async), patch.object(egress.socket, "getaddrinfo", _boom):
        with pytest.raises(egress.EgressValidationError) as exc:
            asyncio.run(egress.validate_public_url_async("https://example.com", require_dns=True))
    assert exc.value.reason == "blocked_ip"

    async def _public(host: str):
        return (ipaddress.ip_address("93.184.216.34"),)

    with patch.object(egress, "_resolve_host_async", _public), patch.object(egress.socket, "getaddrinfo", _boom):
        validated = asyncio.run(egress.validate_public_url_async("https://example.com", require_dns=True))
    assert validated.host == "example.com"
