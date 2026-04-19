"""Regression tests for the 2026-04-19 security audit fixes.

Each test pins a specific finding from SECURITY_AUDIT_2026-04-19.md so
the regression can't silently return. One test per finding where
meaningful; combined where assertions share setup.
"""

from __future__ import annotations

import inspect

import pytest

# ---------------------------------------------------------------------------
# CRITICAL-02 — filing-approval authorization must bind to caller identity
# ---------------------------------------------------------------------------

class TestFilingApprovalAuthz:
    """Authz must bind to the current caller's identity, never to role
    entries belonging to other users of the company. The pre-fix code
    iterated role values and granted partner authority to any caller
    if *any* user in the company had partner role.
    """

    def test_approve_filing_source_scans_caller_ids_only(self) -> None:
        """The approve_filing handler source must not scan role values
        looking for a partner match — authz must key off caller ids.
        """
        from api.v1 import companies as mod

        src = inspect.getsource(mod.approve_filing)
        # Pre-fix pattern: `for _key, _role in roles.items(): if _role == partner.value:`
        assert "for _key, _role in roles.items()" not in src, (
            "approve_filing must not iterate role entries to locate partner "
            "status — caller identity MUST key the lookup. See "
            "SECURITY_AUDIT_2026-04-19.md CRITICAL-02."
        )
        # Post-fix sentinel: caller-id sweep
        assert "caller_ids" in src, (
            "approve_filing must build a caller_ids list from the JWT and "
            "look up the role ONLY from those identifiers."
        )

    def test_reject_filing_source_scans_caller_ids_only(self) -> None:
        from api.v1 import companies as mod

        src = inspect.getsource(mod.reject_filing)
        assert "for _key, _role in roles.items()" not in src, (
            "reject_filing must not iterate role entries. CRITICAL-02 fix."
        )
        assert "caller_ids" in src, (
            "reject_filing must use caller_ids for the authz lookup."
        )


# ---------------------------------------------------------------------------
# CRITICAL-03 — CDC events must not leak across tenants
# ---------------------------------------------------------------------------

class TestCDCTenantIsolation:

    @pytest.mark.asyncio
    async def test_events_are_tenant_tagged_and_scoped(self) -> None:
        """handle_cdc_webhook must tag every stored event with the
        tenant_id it was received for, and get_stored_events(tenant_id=)
        must never return events from a different tenant."""
        import hashlib
        import hmac
        import json
        import os

        from core.cdc.receiver import (
            clear_store,
            get_stored_events,
            handle_cdc_webhook,
        )

        # Per-connector HMAC secret (fail-closed path requires this).
        os.environ["CDC_WEBHOOK_SECRET_TESTCONN"] = "sec-key"
        try:
            clear_store()
            payload = {
                "event_type": "record.updated",
                "resource_type": "contact",
                "resource_id": "c-42",
            }
            sig = hmac.new(
                b"sec-key",
                json.dumps(payload, sort_keys=True).encode(),
                hashlib.sha256,
            ).hexdigest()

            # Tenant A ingests
            result_a = await handle_cdc_webhook("tenant-a", "testconn", payload, sig)
            assert result_a["status"] == "accepted"
            assert result_a["event"]["tenant_id"] == "tenant-a"

            # Tenant B's read must be empty — no cross-tenant leak
            assert get_stored_events(tenant_id="tenant-b") == []

            # Tenant A's read returns the event
            a_events = get_stored_events(tenant_id="tenant-a")
            assert len(a_events) == 1
            assert a_events[0]["tenant_id"] == "tenant-a"
        finally:
            os.environ.pop("CDC_WEBHOOK_SECRET_TESTCONN", None)
            clear_store()

    @pytest.mark.asyncio
    async def test_missing_tenant_id_is_rejected(self) -> None:
        """A webhook with an empty tenant_id must be rejected — an
        event without ownership cannot be routed safely."""
        from core.cdc.receiver import handle_cdc_webhook

        result = await handle_cdc_webhook("", "anyconnector", {}, "any-sig")
        assert result["status"] == "rejected"
        assert result["reason"] == "missing_tenant"

    def test_legacy_unscoped_webhook_url_returns_410(self) -> None:
        """The pre-fix POST /webhooks/cdc/{connector} endpoint must
        now respond 410 Gone — any call to it was silently storing
        events without tenant tags."""
        from api.v1 import cdc_webhooks as mod

        # Source sanity: the legacy handler decorator carries
        # status_code=410 and a helpful redirection body.
        src = inspect.getsource(mod.cdc_webhook_legacy)
        assert "410" in src
        assert "endpoint_removed" in src


# ---------------------------------------------------------------------------
# CDC read API tenant-filter regression
# ---------------------------------------------------------------------------

def test_cdc_list_endpoint_declares_tenant_dependency() -> None:
    """GET /cdc/events must take tenant_id via Depends(get_current_tenant).
    The pre-fix endpoint had no auth gate at all."""
    from api.v1 import cdc_webhooks as mod

    src = inspect.getsource(mod.list_cdc_events)
    assert "get_current_tenant" in src, (
        "/cdc/events must bind tenant_id via Depends(get_current_tenant). "
        "CRITICAL-03 fix — no more unauthenticated read of the global store."
    )
