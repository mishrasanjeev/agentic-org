# SPDX-License-Identifier: Apache-2.0
"""A recorder that stands in for the commerce ledger when a route test replaces the database session."""

from __future__ import annotations

from typing import Any

import pytest

from core.commerce.ledger import commerce_payload_hash


def patch_commerce_ledger(monkeypatch: pytest.MonkeyPatch, module: Any) -> list[dict[str, Any]]:
    """Replace ``module``'s ledger writers with a recorder; returns the recorded events (in-session or own)."""
    events: list[dict[str, Any]] = []

    async def _record(session: Any = None, **kwargs: Any) -> Any:
        payload_hash = kwargs.get("payload_hash") or commerce_payload_hash(kwargs.get("payload"))
        events.append({**kwargs, "payload_hash": payload_hash, "in_session": session is not None})
        return _Entry(kwargs["event"], payload_hash, kwargs["outcome"])

    async def _record_own(**kwargs: Any) -> Any:
        return await _record(None, **kwargs)

    if hasattr(module, "record_commerce_event"):
        monkeypatch.setattr(module, "record_commerce_event", _record)
    if hasattr(module, "record_commerce_event_in_own_transaction"):
        monkeypatch.setattr(module, "record_commerce_event_in_own_transaction", _record_own)
    return events


class _Entry:
    def __init__(self, event: str, payload_hash: str, outcome: str) -> None:
        self.event = event
        self.payload_hash = payload_hash
        self.outcome = outcome

    def to_ref(self) -> dict[str, Any]:
        return {"ledger_ref": "test", "event": self.event, "payload_hash": self.payload_hash, "outcome": self.outcome}
