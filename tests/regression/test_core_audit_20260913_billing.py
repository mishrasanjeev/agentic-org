"""Core audit 2026-09-13 — billing findings (1, 4, 5, 7, 8).

1. Stripe webhook verification with an EMPTY secret is forgeable → fail closed.
4. Cancellation events must match the stored ``external_id``; Plural
   activation must not be rewritten by a replayed / stale order.
5. Budget-alert emails go to a tenant-owned recipient, never a hard-coded
   internal address.
7. Plan is derived from the live price id before stale ``metadata.plan``.
8. Monthly invoices run only after the billed month has closed in UTC.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

TENANT = "11111111-1111-1111-1111-111111111111"


# ── Finding 1: empty STRIPE_WEBHOOK_SECRET ──────────────────────────


def test_stripe_webhook_rejects_event_when_secret_unset(monkeypatch):
    from core.billing import stripe_client

    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(stripe_client, "_STRIPE_WEBHOOK_SECRET", "")
    stripe = MagicMock()

    with patch.object(stripe_client, "_get_stripe", return_value=stripe):
        with pytest.raises(stripe_client.StripeWebhookNotConfiguredError):
            stripe_client.handle_webhook(b'{"type":"checkout.session.completed"}', "t=1,v1=forged")

    # construct_event must never run against an empty secret.
    stripe.Webhook.construct_event.assert_not_called()


def test_stripe_webhook_secret_is_read_at_call_time(monkeypatch):
    from core.billing import stripe_client

    monkeypatch.setattr(stripe_client, "_STRIPE_WEBHOOK_SECRET", "")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_late")
    stripe = MagicMock()
    stripe.Webhook.construct_event.return_value = {
        "type": "invoice.paid",
        "data": {"object": {"metadata": {"tenant_id": TENANT}}},
    }

    with patch.object(stripe_client, "_get_stripe", return_value=stripe):
        result = stripe_client.handle_webhook(b"{}", "sig")

    assert result["processed"] is True
    stripe.Webhook.construct_event.assert_called_once_with(b"{}", "sig", "whsec_late")


@pytest.mark.asyncio
async def test_stripe_webhook_endpoint_returns_503_when_secret_unset(monkeypatch):
    from api.v1 import billing
    from core.billing import stripe_client

    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(stripe_client, "_STRIPE_WEBHOOK_SECRET", "")
    request = SimpleNamespace(
        body=AsyncMock(return_value=b"{}"),
        headers={"Stripe-Signature": "t=1,v1=forged"},
    )

    with patch.object(stripe_client, "_get_stripe", return_value=MagicMock()):
        with pytest.raises(HTTPException) as exc_info:
            await billing.stripe_webhook(request)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 503


def test_get_stripe_requires_secret_key(monkeypatch):
    from core.billing import stripe_client

    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.setattr(stripe_client, "_STRIPE_SECRET_KEY", "")
    with patch.object(stripe_client, "_stripe", MagicMock()):
        with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY"):
            stripe_client._get_stripe()


# ── Finding 7: price id before stale metadata ───────────────────────


def test_plan_from_subscription_prefers_live_price_id(monkeypatch):
    from core.billing import stripe_client

    monkeypatch.setitem(stripe_client.PLAN_PRICE_MAP, "pro", "price_pro")
    monkeypatch.setitem(stripe_client.PLAN_PRICE_MAP, "enterprise", "price_ent")

    stale_metadata_sub = {
        "metadata": {"plan": "enterprise"},  # written at checkout, now stale
        "items": {"data": [{"price": {"id": "price_pro"}}]},
    }
    assert stripe_client._plan_from_subscription(stale_metadata_sub) == "pro"

    unmapped_price_sub = {
        "metadata": {"plan": "pro"},
        "items": {"data": [{"price": {"id": "price_unknown"}}]},
    }
    assert stripe_client._plan_from_subscription(unmapped_price_sub) == "pro"
    assert stripe_client._plan_from_subscription({"items": {"data": []}}) == ""


# ── Finding 4: cancellation must match the stored subscription id ───


class _DeactivateSession:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount
        self.executed: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params or {}))
        return SimpleNamespace(rowcount=self.rowcount)


@pytest.mark.asyncio
async def test_deactivate_sql_is_guarded_by_external_id():
    from core.billing import subscriptions as subs

    session = _DeactivateSession(rowcount=0)
    await subs._deactivate(
        session, uuid.UUID(TENANT), status="canceled", provider_subscription_id="sub_old"
    )
    sql, params = session.executed[0]
    assert "external_id" in sql
    assert params["external_id"] == "sub_old"

    # Authenticated cancel endpoint path: no id → unconditional tenant match.
    session = _DeactivateSession(rowcount=1)
    await subs._deactivate(session, uuid.UUID(TENANT), status="cancelled")
    assert session.executed[0][1]["external_id"] == ""


def test_stripe_deleted_event_forwards_subscription_id(monkeypatch):
    from core.billing import stripe_client

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    stripe = MagicMock()
    stripe.Webhook.construct_event.return_value = {
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_old", "metadata": {"tenant_id": TENANT}}},
    }
    with (
        patch.object(stripe_client, "_get_stripe", return_value=stripe),
        patch("core.billing.subscriptions.deactivate_subscription_sync") as deactivate,
    ):
        stripe_client.handle_webhook(b"{}", "sig")

    deactivate.assert_called_once_with(TENANT, status="cancelled", provider_subscription_id="sub_old")


def test_stripe_sync_canceled_status_forwards_subscription_id():
    from core.billing import stripe_client

    subscription = {
        "id": "sub_replaced",
        "status": "canceled",
        "customer": "cus_1",
        "metadata": {"tenant_id": TENANT, "plan": "pro"},
        "items": {"data": []},
    }
    with patch("core.billing.subscriptions.deactivate_subscription_sync") as deactivate:
        stripe_client._sync_subscription_state(subscription)

    deactivate.assert_called_once_with(
        TENANT, status="canceled", provider_subscription_id="sub_replaced"
    )


def test_plural_expiry_passes_stored_order_id():
    from core.billing import subscriptions as subs

    src = inspect.getsource(subs.expire_overdue_plural_subscriptions)
    assert 'provider_subscription_id=sub["order_id"]' in src


# ── Finding 4 sibling: Plural activation replay / stale-order guard ─


def _paid_plural(order_id: str, plan: str, started: datetime) -> dict:
    return {
        "tenant_id": TENANT,
        "plan": plan,
        "tier": plan,
        "status": "active",
        "provider": "plural",
        "order_id": order_id,
        "provider_customer_id": "",
        "current_period_start": started.isoformat(),
        "current_period_end": (started + timedelta(days=30)).isoformat(),
        "is_paid": True,
    }


def test_plural_activation_ignores_replayed_order(monkeypatch):
    from core.billing import pinelabs_client

    now = datetime.now(UTC)
    monkeypatch.setattr(
        "core.billing.subscriptions.get_subscription_sync",
        lambda tenant_id: _paid_plural("order_A", "enterprise", now - timedelta(hours=1)),
    )
    record = MagicMock()
    monkeypatch.setattr("core.billing.subscriptions.record_subscription_sync", record)

    pinelabs_client._activate_subscription(TENANT, "enterprise", "order_A", ordered_at=now)
    record.assert_not_called()


def test_plural_activation_ignores_order_older_than_active_period(monkeypatch):
    from core.billing import pinelabs_client

    now = datetime.now(UTC)
    # Order B activated 10 minutes ago; a late webhook for order A (placed
    # an hour ago, lower plan) must not downgrade the tenant.
    monkeypatch.setattr(
        "core.billing.subscriptions.get_subscription_sync",
        lambda tenant_id: _paid_plural("order_B", "enterprise", now - timedelta(minutes=10)),
    )
    record = MagicMock()
    monkeypatch.setattr("core.billing.subscriptions.record_subscription_sync", record)

    pinelabs_client._activate_subscription(
        TENANT, "pro", "order_A", ordered_at=now - timedelta(hours=1)
    )
    record.assert_not_called()


def test_plural_activation_applies_newer_order_even_if_lower_plan(monkeypatch):
    from core.billing import pinelabs_client

    now = datetime.now(UTC)
    monkeypatch.setattr(
        "core.billing.subscriptions.get_subscription_sync",
        lambda tenant_id: _paid_plural("order_B", "enterprise", now - timedelta(hours=1)),
    )
    record = MagicMock(return_value={"current_period_end": None})
    monkeypatch.setattr("core.billing.subscriptions.record_subscription_sync", record)

    pinelabs_client._activate_subscription(TENANT, "pro", "order_C", ordered_at=now)
    assert record.call_args.kwargs["plan"] == "pro"
    assert record.call_args.kwargs["provider_subscription_id"] == "order_C"


def test_plural_order_mapping_records_created_at(monkeypatch):
    from core.billing import pinelabs_client

    monkeypatch.setattr(pinelabs_client, "_redis_client", lambda: None)
    monkeypatch.setattr(pinelabs_client, "_strict_order_mapping", lambda: False)
    pinelabs_client.store_order_mapping("ref_1", "order_1", tenant_id=TENANT, plan="pro")
    stored = pinelabs_client.lookup_order_details("ref_1")
    assert pinelabs_client._parse_ordered_at(stored["created_at"]) is not None
    assert pinelabs_client._parse_ordered_at(None) is None
    assert pinelabs_client._parse_ordered_at("garbage") is None


# ── Finding 5: budget alert recipient is tenant-owned ───────────────


class _RecipientSession:
    def __init__(self, company_email, admin_email):
        self.company_email = company_email
        self.admin_email = admin_email

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        value = self.company_email if "compliance_alerts_email" in sql else self.admin_email
        return SimpleNamespace(scalar_one_or_none=lambda: value)


def _alert(company_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(TENANT),
        company_id=company_id,
        name="ops",
        threshold_usd=100,
        period="monthly",
        notify_channels="email",
    )


@pytest.mark.asyncio
async def test_budget_alert_email_goes_to_tenant_admin(monkeypatch):
    from core.billing import budget_evaluator as be

    entered: list = []

    def _tenant_session(tid):
        entered.append(tid)
        return _RecipientSession(company_email=None, admin_email="admin@tenant.example")

    monkeypatch.setattr(be, "get_tenant_session", _tenant_session)
    sent: list = []
    monkeypatch.setattr("core.email.send_email", lambda to, subject, html: sent.append(to) or True)

    await be._send_notification(_alert(), spend=be.Decimal("90"), percent=90)

    assert sent == ["admin@tenant.example"]
    assert entered == [uuid.UUID(TENANT)]


@pytest.mark.asyncio
async def test_budget_alert_email_prefers_company_compliance_email(monkeypatch):
    from core.billing import budget_evaluator as be

    monkeypatch.setattr(
        be,
        "get_tenant_session",
        lambda tid: _RecipientSession(company_email="cfo@corp.in", admin_email="admin@x"),
    )
    sent: list = []
    monkeypatch.setattr("core.email.send_email", lambda to, subject, html: sent.append(to) or True)

    await be._send_notification(_alert(company_id=uuid.uuid4()), spend=be.Decimal("90"), percent=90)
    assert sent == ["cfo@corp.in"]


@pytest.mark.asyncio
async def test_budget_alert_email_skipped_without_recipient_no_fallback(monkeypatch):
    from core.billing import budget_evaluator as be

    monkeypatch.setattr(
        be, "get_tenant_session", lambda tid: _RecipientSession(company_email=None, admin_email=None)
    )
    sent: list = []
    monkeypatch.setattr("core.email.send_email", lambda to, subject, html: sent.append(to) or True)

    await be._send_notification(_alert(), spend=be.Decimal("90"), percent=90)
    assert sent == []

    src = inspect.getsource(be)
    assert "sanjeev@" not in src and "@agenticorg.ai" not in src


# ── Finding 8: invoices only after the month has closed in UTC ──────


def test_monthly_invoice_beat_runs_after_utc_month_close():
    from core.tasks.celery_app import app

    entry = app.conf.beat_schedule["generate-monthly-invoices"]
    schedule = entry["schedule"]
    assert app.conf.timezone == "Asia/Kolkata"
    # 06:30 IST on the 1st == 01:00 UTC on the 1st (month closed in UTC).
    assert schedule.hour == {6}
    assert schedule.minute == {30}
    assert schedule.day_of_month == {1}


@pytest.mark.asyncio
async def test_invoice_generator_refuses_before_period_closes(monkeypatch):
    from core.billing import invoice_generator as ig

    def _boom():
        raise AssertionError("no DB access when the period is not closed")

    monkeypatch.setattr(ig, "async_session_factory", _boom)

    # 01:00 IST on Sep 1 == 19:30 UTC on Aug 31: August is not over yet.
    result = await ig.generate_invoices_for_period(ref=datetime(2026, 8, 31, 19, 30, tzinfo=UTC))

    assert result["created"] == 0
    assert result["skipped_reason"] == "period_not_closed"
    assert result["period_end"] == datetime(2026, 9, 1, tzinfo=UTC).isoformat()


@pytest.mark.asyncio
async def test_invoice_generator_runs_once_period_closed(monkeypatch):
    from core.billing import invoice_generator as ig

    class _NoTenants:
        executed: list = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt, params=None):
            self.executed.append(str(stmt))
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    monkeypatch.setattr(ig, "async_session_factory", lambda: _NoTenants())

    result = await ig.generate_invoices_for_period(ref=datetime(2026, 9, 1, 1, 0, tzinfo=UTC))

    assert "skipped_reason" not in result
    assert result["period_start"] == datetime(2026, 8, 1, tzinfo=UTC).isoformat()
    assert any("row_security = off" in sql for sql in _NoTenants.executed)
