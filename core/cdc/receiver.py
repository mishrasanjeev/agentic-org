"""CDC webhook receiver — validates, normalizes, and stores change-data-capture events."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any

# In-memory event store.  Replace with DB-backed store once migration exists.
# Each entry is `{tenant_id, connector, ...}` — callers that read this
# store MUST filter by tenant_id before returning to users. See
# get_stored_events(tenant_id=...) below. The audit in 2026-04-19
# (CRITICAL-03) flagged that a single shared list without tenant tags
# leaked events across tenants through the read API.
_event_store: list[dict[str, Any]] = []
# Dedup set scoped by (tenant_id, connector, resource_type, resource_id,
# event_type) — a different tenant can legitimately emit the same
# fingerprint and we must still accept it.
_seen_ids: set[tuple[str, str]] = set()


def _compute_event_id(connector: str, payload: dict[str, Any]) -> str:
    """Deterministic event fingerprint for deduplication."""
    parts = [connector, payload.get("resource_type", ""), payload.get("resource_id", ""), payload.get("event_type", "")]
    raw = ":".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()


def _validate_signature(payload_bytes: bytes, signature: str, connector: str) -> bool:
    """Validate HMAC-SHA256 signature using per-connector secret.

    Fails closed — rejects all events when no secret is configured.
    """
    import structlog

    _log = structlog.get_logger()
    secret = os.getenv(f"CDC_WEBHOOK_SECRET_{connector.upper()}", "")
    if not secret:
        _log.warning(
            "cdc_webhook_secret_missing",
            connector=connector,
            hint="Set CDC_WEBHOOK_SECRET_<CONNECTOR> env var",
        )
        return False  # fail closed — no secret configured
    expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def handle_cdc_webhook(
    tenant_id: str,
    connector: str,
    payload: dict[str, Any],
    signature: str,
) -> dict[str, Any]:
    """Process an incoming CDC webhook for a specific tenant.

    1. Validate signature (HMAC-SHA256 or provider-specific).
    2. Normalize payload to CDC event format + tag with tenant_id.
    3. Deduplicate within the tenant (skip if already seen).
    4. Store event and trigger workflow evaluation asynchronously.

    Reject the webhook (400) if no tenant_id is provided — a CDC
    event without tenant ownership cannot be routed safely.
    """
    import json as _json

    if not tenant_id:
        return {"status": "rejected", "reason": "missing_tenant"}

    payload_bytes = _json.dumps(payload, sort_keys=True).encode()

    if not _validate_signature(payload_bytes, signature, connector):
        return {"status": "rejected", "reason": "invalid_signature"}

    # Normalize to standard CDC event format. tenant_id is the first
    # field so any downstream consumer MUST see the ownership tag.
    event: dict[str, Any] = {
        "tenant_id": tenant_id,
        "connector": connector,
        "event_type": payload.get("event_type", payload.get("type", "unknown")),
        "resource_type": payload.get("resource_type", payload.get("object", "unknown")),
        "resource_id": str(payload.get("resource_id", payload.get("id", ""))),
        "payload": payload,
        "received_at": time.time(),
    }

    fingerprint = _compute_event_id(connector, event)
    dedup_key = (tenant_id, fingerprint)

    if dedup_key in _seen_ids:
        return {"status": "duplicate", "event_id": fingerprint}

    _seen_ids.add(dedup_key)
    _event_store.append(event)

    # Trigger workflow evaluation asynchronously (best-effort) with
    # the real tenant so downstream routing respects tenancy.
    try:
        from core.cdc.triggers import evaluate_triggers

        evaluate_triggers(event, tenant_id=tenant_id)
    except Exception:  # noqa: S110
        pass  # best-effort trigger evaluation

    return {"status": "accepted", "event_id": fingerprint, "event": event}


def get_stored_events(tenant_id: str | None = None) -> list[dict[str, Any]]:
    """Return stored CDC events, optionally filtered by tenant.

    Callers that serve user requests MUST pass a tenant_id. The
    no-argument form (tenant_id=None) is only for tests and internal
    diagnostics — it returns the full store.
    """
    if tenant_id is None:
        return list(_event_store)
    return [e for e in _event_store if e.get("tenant_id") == tenant_id]


def clear_store() -> None:
    """Clear the in-memory event store and dedup set."""
    _event_store.clear()
    _seen_ids.clear()
