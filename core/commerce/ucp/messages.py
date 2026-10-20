# SPDX-License-Identifier: Apache-2.0
"""UCP ``messages[]`` builders and the error response shape.

Business outcomes (out of stock, an undeliverable address, a refused payment)
are HTTP 200 with typed messages; protocol errors use 4xx/5xx (UCP checkout
REST binding, Business Outcomes). ``path`` is an RFC 9535 JSONPath relative to
the response root. A ``requires_buyer_input`` or ``requires_buyer_review``
error turns the session into ``requires_escalation``.
"""

from __future__ import annotations

from typing import Any

SEVERITIES = ("recoverable", "requires_buyer_input", "requires_buyer_review", "unrecoverable")
ESCALATING = frozenset({"requires_buyer_input", "requires_buyer_review"})


def error(code: str, content: str, *, severity: str = "recoverable", path: str | None = None) -> dict[str, Any]:
    if severity not in SEVERITIES:
        raise ValueError(f"unknown severity {severity}")
    out: dict[str, Any] = {"type": "error", "code": code, "content": content, "severity": severity}
    if path:
        out["path"] = path
    return out


def warning(code: str, content: str, *, path: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "warning", "code": code, "content": content}
    if path:
        out["path"] = path
    return out


def info(content: str, *, code: str | None = None, path: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "info", "content": content}
    if code:
        out["code"] = code
    if path:
        out["path"] = path
    return out


def has_errors(messages: list[dict[str, Any]]) -> bool:
    return any(message.get("type") == "error" for message in messages)


def escalates(messages: list[dict[str, Any]]) -> bool:
    return any(message.get("type") == "error" and message.get("severity") in ESCALATING for message in messages)


def error_response(version: str, code: str, content: str, *, continue_url: str | None = None) -> dict[str, Any]:
    """``error_response.json``: no resource, ``ucp.status: error`` and at least one message."""
    body: dict[str, Any] = {
        "ucp": {"version": version, "status": "error"},
        "messages": [error(code, content, severity="unrecoverable")],
    }
    if continue_url:
        body["continue_url"] = continue_url
    return body
