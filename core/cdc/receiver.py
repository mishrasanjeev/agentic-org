"""CDC webhook receiver — validates, normalizes, and stores change-data-capture events."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any

# In-memory event store.  Replace with DB-backed store once migration exists.
_event_store: list[dict[str, Any]] = []
_seen_ids: set[str] = set()


def _compute_event_id(connector: str, payload: dict[str, Any]) -> str:
    """Deterministic event fingerprint for deduplication."""
    parts = [connector, payload.get("resource_type", ""), payload.get("resource_id", ""), payload.get("event_type", "")]
    raw = ":".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()


def _validate_signature(payload_bytes: bytes, signature: str, connector: str) -> bool:
    """Validate HMAC-SHA256 signature using per-connector secret.

    Falls back to accepting all events when no secret is configured (dev mode).
    """
    secret = os.getenv(f"CDC_WEBHOOK_SECRET_{connector.upper()}", "")
    if not secret:
        return True  # dev mode — no secret configured
    expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def handle_cdc_webhook(
    connector: str,
    payload: dict[str, Any],
    signature: str,
) -> dict[str, Any]:
    """Process an incoming CDC webhook.

    1. Validate signature (HMAC-SHA256 or provider-specific).
    2. Normalize payload to CDC event format.
    3. Deduplicate (skip if already seen).
    4. Store event and trigger workflow evaluation asynchronously.
    """
    import json as _json

    payload_bytes = _json.dumps(payload, sort_keys=True).encode()

    if not _validate_signature(payload_bytes, signature, connector):
        return {"status": "rejected", "reason": "invalid_signature"}

    # Normalize to standard CDC event format
    event: dict[str, Any] = {
        "connector": connector,
        "event_type": payload.get("event_type", payload.get("type", "unknown")),
        "resource_type": payload.get("resource_type", payload.get("object", "unknown")),
        "resource_id": str(payload.get("resource_id", payload.get("id", ""))),
        "payload": payload,
        "received_at": time.time(),
    }

    event_id = _compute_event_id(connector, event)

    # Deduplication
    if event_id in _seen_ids:
        return {"status": "duplicate", "event_id": event_id}

    _seen_ids.add(event_id)
    _event_store.append(event)

    # Trigger workflow evaluation asynchronously (best-effort)
    try:
        from core.cdc.triggers import evaluate_triggers

        evaluate_triggers(event, tenant_id="")
    except Exception:  # noqa: S110
        pass  # best-effort trigger evaluation

    return {"status": "accepted", "event_id": event_id, "event": event}


def get_stored_events() -> list[dict[str, Any]]:
    """Return all stored CDC events (for testing / inspection)."""
    return list(_event_store)


def clear_store() -> None:
    """Clear the in-memory event store and dedup set."""
    _event_store.clear()
    _seen_ids.clear()
