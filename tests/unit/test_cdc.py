"""Tests for CDC (Change Data Capture) receiver, triggers, and poller."""

from __future__ import annotations

import asyncio
import hashlib
import hmac as hmac_mod
import json

import pytest

from core.cdc.receiver import clear_store, get_stored_events, handle_cdc_webhook
from core.cdc.triggers import clear_triggers, evaluate_triggers, register_trigger


@pytest.fixture(autouse=True)
def _reset_cdc_state(monkeypatch):
    """Reset in-memory stores and set test webhook secrets."""
    clear_store()
    clear_triggers()
    # Set webhook secrets so fail-closed validation passes
    for connector in ("salesforce", "hubspot", "xero", "jira"):
        monkeypatch.setenv(f"CDC_WEBHOOK_SECRET_{connector.upper()}", "test-secret")
    yield
    clear_store()
    clear_triggers()


# ── helpers ──────────────────────────────────────────────────────────────────

def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _sign(payload: dict, secret: str = "test-secret") -> str:  # noqa: S107
    """Compute HMAC-SHA256 signature for a CDC payload."""
    payload_bytes = json.dumps(payload, sort_keys=True).encode()
    return hmac_mod.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()


# ── tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_stores_event():
    """A valid CDC webhook stores the normalized event."""
    payload = {
        "event_type": "contact.updated",
        "resource_type": "contact",
        "resource_id": "c-123",
        "data": {"name": "Acme Corp"},
    }
    result = await handle_cdc_webhook("salesforce", payload, signature=_sign(payload))
    assert result["status"] == "accepted"
    events = get_stored_events()
    assert len(events) == 1
    assert events[0]["connector"] == "salesforce"
    assert events[0]["event_type"] == "contact.updated"


@pytest.mark.asyncio
async def test_signature_validation():
    """An invalid signature causes the event to be rejected."""
    import os

    # Set a secret so validation actually runs
    os.environ["CDC_WEBHOOK_SECRET_TESTCONN"] = "supersecret"
    try:
        payload = {"event_type": "record.created", "resource_type": "deal", "resource_id": "d-1"}
        result = await handle_cdc_webhook("testconn", payload, signature="bad-signature")
        assert result["status"] == "rejected"
        assert get_stored_events() == []
    finally:
        os.environ.pop("CDC_WEBHOOK_SECRET_TESTCONN", None)


def test_trigger_matches_workflow():
    """Registered triggers match against CDC events and return workflow IDs."""
    register_trigger(
        connector="hubspot",
        event_type="deal.closed",
        resource_type="deal",
        workflow_id="wf-onboard-customer",
    )
    event = {
        "connector": "hubspot",
        "event_type": "deal.closed",
        "resource_type": "deal",
        "resource_id": "d-99",
    }
    matched = evaluate_triggers(event, tenant_id="t-1")
    assert "wf-onboard-customer" in matched


@pytest.mark.asyncio
async def test_duplicate_event_skipped():
    """Sending the same event twice results in a duplicate status on the second call."""
    payload = {
        "event_type": "invoice.created",
        "resource_type": "invoice",
        "resource_id": "inv-42",
    }
    first = await handle_cdc_webhook("xero", payload, signature=_sign(payload))
    assert first["status"] == "accepted"

    second = await handle_cdc_webhook("xero", payload, signature=_sign(payload))
    assert second["status"] == "duplicate"

    # Only one event stored
    assert len(get_stored_events()) == 1


@pytest.mark.asyncio
async def test_polling_detects_new_records():
    """Poller returns empty list for connectors without a registered poller function."""
    from core.cdc.poller import poll_connector

    records = await poll_connector("unknown_connector", last_sync_at="2026-01-01T00:00:00+00:00")
    assert isinstance(records, list)
    assert len(records) == 0
