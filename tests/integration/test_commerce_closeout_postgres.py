# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Commerce close-out on a real Postgres: hosted refunds, unmatched refunds, the email outbox, held stock,
stock short at payment and loyalty on re-price.

The Plural API is an httpx MockTransport fake (it also answers refunds); webhooks are signed with a synthetic
secret. Requires the disposable database in ``AGENTICORG_DB_URL`` (migrated to head by the module fixture).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from core.commerce import plural_hosted
from core.commerce.ucp import handlers
from tests import ucp_txn_fixtures as uf
from tests.integration import test_payment_handoff_postgres as handoff_suite
from tests.integration.test_payment_handoff_postgres import (
    FakeHosted,
    _complete,
    _exec,
    _ledger,
    _plural_payments,
    _ready,
    _rows,
    _runtime,
    _save_plural,
    _signed,
    _state_of,
    _tenant,
    _with_catalog,
)

# The hand-off suite's fixtures: its migrated schema, database, test executor and captured order-email kicks.
migrated_schema = handoff_suite.migrated_schema
db = handoff_suite.db
executor = handoff_suite.executor
kicks = handoff_suite.kicks


class FakePlural(FakeHosted):
    """The hosted-checkout fake plus Plural refunds (``POST /pay/v1/refunds/{order id}``)."""

    def __init__(self) -> None:
        super().__init__()
        self.refund_status = "PROCESSED"
        self.refund_http_status = 200

    def refunds(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (r.url.path.rsplit("/", 1)[-1], json.loads(r.content))
            for r in self.requests
            if "/pay/v1/refunds/" in r.url.path and r.method == "POST"
        ]

    def tokens(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith("/auth/v1/token")]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if "/pay/v1/refunds/" in request.url.path and request.method == "POST":
            self.requests.append(request)
            if self.refund_http_status >= 400:
                return httpx.Response(self.refund_http_status, json={"code": "REFUND_NOT_ALLOWED"})
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "order_id": request.url.path.rsplit("/", 1)[-1],
                        "refund_id": f"refund-{len(self.refunds())}",
                        "merchant_order_reference": body["merchant_order_reference"],
                        "status": self.refund_status,
                    }
                },
            )
        return super()._handle(request)


@pytest.fixture()
def plural(monkeypatch) -> FakePlural:
    fake = FakePlural()
    monkeypatch.setattr(plural_hosted, "transport_override", fake)
    return fake


@pytest.fixture()
def told(monkeypatch) -> list[tuple[str, str, dict[str, Any]]]:
    from core.commerce import order_notifications

    seen: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr(
        order_notifications,
        "kick_unmatched_payment_notification",
        lambda t, c, **details: seen.append((t, c, details)),
    )
    return seen


def _same_catalog(monkeypatch, runtime: handlers.UcpRuntime) -> None:
    """The payment confirmation re-checks stock with the test runtime's catalog."""
    monkeypatch.setattr(handlers, "payment_catalog_for", runtime.catalog_for)


async def _paid_order(db, plural: FakePlural, monkeypatch, *, runtime=None, tenant=None) -> tuple[str, str, int, Any]:
    """A checkout paid on the merchant's Plural page: ``(tenant, order id, total, credentials)``."""
    tenant = tenant or await _tenant(db)
    stored = await _save_plural(tenant)
    runtime = runtime or _runtime(monkeypatch, tenant, _plural_payments())
    _same_catalog(monkeypatch, runtime)
    session = await _ready(runtime, tenant)
    await _complete(runtime, tenant, session["id"])
    body = plural.bodies("/checkout/v1/orders")[-1]
    total = body["order_amount"]["value"]
    order_id_on_plural = f"v1-hosted-{len(plural.orders)}"
    paid = json.dumps(
        {
            "event_type": "ORDER_PROCESSED",
            "data": {
                "order_id": order_id_on_plural,
                "merchant_order_reference": body["merchant_order_reference"],
                "status": "PROCESSED",
                "order_amount": {"value": total, "currency": "INR"},
            },
        }
    ).encode()
    applied = await plural_hosted.apply_webhook(
        tenant_id=tenant,
        webhook_key=stored.webhook_key,
        raw_body=paid,
        headers=_signed(paid, webhook_id=f"wh_{uuid.uuid4().hex[:8]}"),
    )
    assert applied["result"] == "completed", applied
    return tenant, applied["order_id"], total, stored


async def _admin(db, tenant: str, email: str = "owner@merchant.example") -> None:
    await _exec(
        db,
        "INSERT INTO users (id, tenant_id, email, name, role, status, mfa_enabled) "
        "VALUES (:id, :t, :e, 'Synthetic', 'admin', 'active', false)",
        id=uuid.uuid4(),
        t=uuid.UUID(tenant),
        e=email,
    )


# --- refunds of hosted orders ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_hosted_order_is_refunded_with_the_merchants_plural_credentials_once_per_request_id(
    db, executor, plural, kicks, monkeypatch
):
    from core.commerce.executors.plural import refund_reference
    from core.commerce.order_refunds import refund_order

    tenant, order_id, total, _stored = await _paid_order(db, plural, monkeypatch)
    now = datetime.now(UTC)

    first = await refund_order(
        tenant_id=tenant, order_id=order_id, amount_minor_units=1000, reason="Damaged", now=now, request_id="req-1"
    )
    assert first.status == "succeeded", first
    assert first.adjustment_id and first.order_revision == 2
    assert first.decision and first.decision["outcome"] == "allow"
    refunds = plural.refunds()
    assert refunds == [
        (
            "v1-hosted-1",
            {
                "merchant_order_reference": refund_reference(order_id, "req-1"),
                "refund_amount": {"value": 1000, "currency": "INR"},
                "merchant_metadata": {
                    "oacp_decision_id": first.decision["decision_id"],
                    "oacp_order_id": order_id,
                    "oacp_payload_hash": first.decision["payload_hash"],
                },
            },
        )
    ]
    # The merchant's own credentials (saved in the vault), never platform ones.
    assert plural.tokens()[-1]["client_id"] == "client-example"

    # The same request id again: the earlier refund, no second Plural call and no second adjustment.
    again = await refund_order(
        tenant_id=tenant, order_id=order_id, amount_minor_units=1000, reason="Damaged", now=now, request_id="req-1"
    )
    assert again.status == "succeeded" and again.adjustment_id == first.adjustment_id
    assert again.extra["replayed"] is True and len(plural.refunds()) == 1

    # Refundable is the order total less what was refunded.
    over = await refund_order(
        tenant_id=tenant, order_id=order_id, amount_minor_units=total, reason=None, now=now, request_id="req-2"
    )
    assert over.status == "refused" and over.decision["reason"] == "refund_exceeds_captured"
    assert len(plural.refunds()) == 1
    rest = await refund_order(
        tenant_id=tenant, order_id=order_id, amount_minor_units=total - 1000, reason=None, now=now, request_id="req-3"
    )
    assert rest.status == "succeeded"
    done = await refund_order(
        tenant_id=tenant, order_id=order_id, amount_minor_units=1, reason=None, now=now, request_id="req-4"
    )
    assert done.status == "refused" and done.decision["reason"] == "refund_exceeds_captured"

    orders = await _rows(db, "SELECT snapshot FROM commerce_orders WHERE id = :i", i=order_id)
    adjustments = [a for a in orders[0]["snapshot"]["adjustments"] if a["type"] == "refund"]
    assert [(a["status"], a["totals"][0]["amount"]) for a in adjustments] == [
        ("completed", -1000),
        ("completed", -(total - 1000)),
    ]
    journal = await _rows(
        db,
        "SELECT provider, phase, order_id, amount_minor_units FROM commerce_payment_operations "
        "WHERE tenant_id = :t AND operation = 'refund' ORDER BY created_at",
        t=tenant,
    )
    assert {r["provider"] for r in journal} == {"plural_hosted"} and {r["order_id"] for r in journal} == {order_id}
    assert [r["phase"] for r in journal] == ["requested", "succeeded", "requested", "succeeded"]
    events = await _ledger(db, tenant)
    assert "commerce.ucp.order.refund" in events and "commerce.plural.refund" in events

    from api.v1 import commerce_payments

    view = (await commerce_payments.list_orders(merchant_id=None, limit=25, offset=0, tenant_id=tenant))["orders"][0]
    assert view["refunded_minor_units"] == total and view["refundable_minor_units"] == 0
    assert view["refund_available"] is True and len(view["refunds"]) == 2


@pytest.mark.asyncio
async def test_a_hosted_refund_plural_has_not_confirmed_counts_as_refunded(db, executor, plural, kicks, monkeypatch):
    from core.commerce.order_refunds import refund_order

    tenant, order_id, total, _stored = await _paid_order(db, plural, monkeypatch)
    plural.refund_status = "PENDING"
    pending = await refund_order(
        tenant_id=tenant,
        order_id=order_id,
        amount_minor_units=total,
        reason=None,
        now=datetime.now(UTC),
        request_id="req-p",
    )
    assert pending.status == "pending"
    more = await refund_order(
        tenant_id=tenant,
        order_id=order_id,
        amount_minor_units=1,
        reason=None,
        now=datetime.now(UTC),
        request_id="req-q",
    )
    assert more.status == "refused" and more.decision["reason"] == "refund_exceeds_captured"
    # A refund Plural refuses is recorded as failed and frees the amount again.
    tenant2, order2, total2, _ = await _paid_order(db, plural, monkeypatch)
    plural.refund_http_status = 422
    failed = await refund_order(
        tenant_id=tenant2,
        order_id=order2,
        amount_minor_units=total2,
        reason=None,
        now=datetime.now(UTC),
        request_id="req-f",
    )
    assert failed.status == "failed"
    replay = await refund_order(
        tenant_id=tenant2,
        order_id=order2,
        amount_minor_units=total2,
        reason=None,
        now=datetime.now(UTC),
        request_id="req-f",
    )
    assert replay.status == "failed" and replay.extra["replayed"] is True
    plural.refund_http_status = 200
    plural.refund_status = "PROCESSED"
    retried = await refund_order(
        tenant_id=tenant2,
        order_id=order2,
        amount_minor_units=total2,
        reason=None,
        now=datetime.now(UTC),
        request_id="req-g",
    )
    assert retried.status == "succeeded"


# --- a payment no order matches -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_payment_no_order_matches_is_kept_emailed_from_the_outbox_and_refunded_once(
    db, executor, plural, kicks, told, monkeypatch
):
    from api.v1 import commerce_payments
    from core.commerce.hosted_refunds import refund_unmatched_payment

    tenant = await _tenant(db)
    stored = await _save_plural(tenant)
    runtime = _runtime(monkeypatch, tenant, _plural_payments())
    session = await _ready(runtime, tenant)
    await _complete(runtime, tenant, session["id"])
    reference = plural.bodies("/checkout/v1/orders")[0]["merchant_order_reference"]
    short = json.dumps(
        {
            "event_type": "ORDER_PROCESSED",
            "data": {
                "order_id": "v1-hosted-1",
                "merchant_order_reference": reference,
                "status": "PROCESSED",
                "order_amount": {"value": 100, "currency": "INR"},
            },
        }
    ).encode()
    for webhook_id in ("wh_s1", "wh_s2"):
        result = await plural_hosted.apply_webhook(
            tenant_id=tenant,
            webhook_key=stored.webhook_key,
            raw_body=short,
            headers=_signed(short, webhook_id=webhook_id),
        )
        assert result["result"] == "amount_mismatch"

    kept = await _rows(
        db,
        "SELECT id, reason, amount_minor_units, currency, credential_ref, refund_status, checkout_session_id "
        "FROM commerce_unmatched_payments WHERE tenant_id = :t",
        t=tenant,
    )
    assert len(kept) == 1 and kept[0]["reason"] == "amount_mismatch" and kept[0]["amount_minor_units"] == 100
    assert kept[0]["credential_ref"] == plural_hosted.credential_ref_for(uf.fx.MERCHANT)
    queued = await _rows(
        db,
        "SELECT kind, payload_ref, status, details FROM commerce_merchant_notifications WHERE tenant_id = :t",
        t=tenant,
    )
    assert [(q["kind"], q["payload_ref"], q["status"]) for q in queued] == [
        ("payment_attention", str(kept[0]["id"]), "pending")
    ]
    assert queued[0]["details"]["result"] == "amount_mismatch" and len(told) == 1

    listed = await commerce_payments.list_unmatched_payments(merchant_id=None, limit=25, offset=0, tenant_id=tenant)
    assert [(p["reason"], p["refundable"], p["refund_status"]) for p in listed["payments"]] == [
        ("amount_mismatch", True, "none")
    ]
    assert listed["payments"][0]["amount"] == "Rs 1.00"

    payment_id = str(kept[0]["id"])
    refunded = await refund_unmatched_payment(tenant_id=tenant, payment_id=payment_id, reason=None)
    assert refunded.status == "succeeded", refunded
    assert refunded.payment["refund_status"] == "refunded"
    assert [(order, body["refund_amount"]) for order, body in plural.refunds()] == [
        ("v1-hosted-1", {"value": 100, "currency": "INR"})
    ]
    assert plural.refunds()[0][1]["merchant_order_reference"].startswith("aou-")
    once = await refund_unmatched_payment(tenant_id=tenant, payment_id=payment_id, reason=None)
    assert once.status == "refused" and once.reason == "refund_already_requested"
    assert len(plural.refunds()) == 1
    events = await _ledger(db, tenant)
    assert events.count("commerce.payment.unmatched_refund") == 2 and "commerce.plural.refund" in events
    assert await _rows(db, "SELECT id FROM commerce_orders WHERE tenant_id = :t", t=tenant) == []


@pytest.mark.asyncio
async def test_a_refused_unmatched_refund_can_be_tried_again(db, executor, plural, kicks, told, monkeypatch):
    from core.commerce.hosted_refunds import UnmatchedRefundError, refund_unmatched_payment

    tenant = await _tenant(db)
    stored = await _save_plural(tenant)
    runtime = _runtime(monkeypatch, tenant, _plural_payments())
    session = await _ready(runtime, tenant)
    await _complete(runtime, tenant, session["id"])
    body = plural.bodies("/checkout/v1/orders")[-1]
    late = json.dumps(
        {
            "event_type": "ORDER_PROCESSED",
            "data": {
                "order_id": "v1-hosted-1",
                "merchant_order_reference": body["merchant_order_reference"],
                "status": "PROCESSED",
                "order_amount": {"value": 1, "currency": "INR"},
            },
        }
    ).encode()
    await plural_hosted.apply_webhook(
        tenant_id=tenant, webhook_key=stored.webhook_key, raw_body=late, headers=_signed(late, webhook_id="wh_late")
    )
    payment_id = str(
        (await _rows(db, "SELECT id FROM commerce_unmatched_payments WHERE tenant_id = :t", t=tenant))[0]["id"]
    )
    plural.refund_http_status = 422
    refused = await refund_unmatched_payment(tenant_id=tenant, payment_id=payment_id, reason=None)
    assert refused.status == "failed" and refused.payment["refund_status"] == "failed"
    plural.refund_http_status = 200
    again = await refund_unmatched_payment(tenant_id=tenant, payment_id=payment_id, reason=None)
    assert again.status == "succeeded"
    references = [body["merchant_order_reference"] for _order, body in plural.refunds()]
    assert len(references) == 2 and references[0] != references[1]
    with pytest.raises(UnmatchedRefundError) as exc:
        await refund_unmatched_payment(tenant_id=tenant, payment_id=str(uuid.uuid4()), reason=None)
    assert exc.value.status == 404
    # Another tenant cannot see or refund it.
    other = await _tenant(db)
    with pytest.raises(UnmatchedRefundError):
        await refund_unmatched_payment(tenant_id=other, payment_id=payment_id, reason=None)


# --- the email outbox --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_order_email_outbox_retries_a_failed_send_and_dead_letters_after_the_last_attempt(
    db, executor, plural, kicks, monkeypatch
):
    from core import email as core_email
    from core.commerce import merchant_notifications as outbox

    tenant = await _tenant(db)
    await _admin(db, tenant)
    tenant, order_id, _total, _stored = await _paid_order(db, plural, monkeypatch, tenant=tenant)
    rows = await _rows(
        db,
        "SELECT id, kind, payload_ref, status, attempts FROM commerce_merchant_notifications WHERE tenant_id = :t",
        t=tenant,
    )
    # Written in the order's own transaction, before any send.
    assert [(r["kind"], r["payload_ref"], r["status"], r["attempts"]) for r in rows] == [
        ("order", order_id, "pending", 0)
    ]
    assert kicks == [(tenant, order_id)]

    def smtp_down(*_a: Any, **_k: Any) -> bool:
        raise OSError("smtp unreachable")

    monkeypatch.setattr(core_email, "send_email", smtp_down)
    now = datetime.now(UTC)
    report = await outbox.deliver_tenant(tenant, now=now)
    assert report.as_dict() == {"sent": 0, "retried": 1, "dead": 0}
    failed = (await _rows(db, "SELECT * FROM commerce_merchant_notifications WHERE tenant_id = :t", t=tenant))[0]
    assert failed["status"] == "failed" and failed["attempts"] == 1
    assert failed["last_error"] == "no_email_accepted" and failed["next_attempt_at"] > now
    # Not due yet: nothing is sent again.
    assert (await outbox.deliver_tenant(tenant, now=now)).as_dict() == {"sent": 0, "retried": 0, "dead": 0}

    sent: list[str] = []
    monkeypatch.setattr(core_email, "send_email", lambda to, subject, html, **_: sent.append(to) or True)
    later = now + timedelta(hours=1)
    assert (await outbox.deliver_tenant(tenant, now=later)).as_dict() == {"sent": 1, "retried": 0, "dead": 0}
    done = (await _rows(db, "SELECT * FROM commerce_merchant_notifications WHERE tenant_id = :t", t=tenant))[0]
    assert (
        done["status"] == "sent"
        and done["attempts"] == 2
        and done["sent_count"] == 1
        and sent == ["owner@merchant.example"]
    )
    # Sent rows are never sent again.
    assert (await outbox.deliver_tenant(tenant, now=later + timedelta(days=1))).sent == 0

    # The last allowed attempt that fails dead-letters the row.
    await _exec(
        db,
        "UPDATE commerce_merchant_notifications SET status = 'failed', attempts = :a, next_attempt_at = :n "
        "WHERE tenant_id = :t",
        a=outbox.MAX_ATTEMPTS - 1,
        n=later,
        t=uuid.UUID(tenant),
    )
    monkeypatch.setattr(core_email, "send_email", lambda *_a, **_k: False)
    assert (await outbox.deliver_tenant(tenant, now=later)).as_dict() == {"sent": 0, "retried": 0, "dead": 1}
    dead = (
        await _rows(db, "SELECT status, last_error FROM commerce_merchant_notifications WHERE tenant_id = :t", t=tenant)
    )[0]
    assert dead["status"] == "dead" and dead["last_error"].startswith("max_attempts_exceeded")


@pytest.mark.asyncio
async def test_a_second_confirmation_never_queues_a_second_email(db, executor, plural, kicks, monkeypatch):
    tenant, order_id, total, stored = await _paid_order(db, plural, monkeypatch)
    reference = plural.bodies("/checkout/v1/orders")[0]["merchant_order_reference"]
    again = json.dumps(
        {
            "event_type": "ORDER_PROCESSED",
            "data": {
                "order_id": "v1-hosted-1",
                "merchant_order_reference": reference,
                "status": "PROCESSED",
                "order_amount": {"value": total, "currency": "INR"},
            },
        }
    ).encode()
    result = await plural_hosted.apply_webhook(
        tenant_id=tenant, webhook_key=stored.webhook_key, raw_body=again, headers=_signed(again, webhook_id="wh_again")
    )
    assert result["result"] == "already_completed"
    rows = await _rows(db, "SELECT kind FROM commerce_merchant_notifications WHERE tenant_id = :t", t=tenant)
    assert [r["kind"] for r in rows] == ["order"]


# --- stock between a Plural link and its payment ----------------------------------------------------------


@pytest.mark.asyncio
async def test_stock_held_by_another_buyers_payable_link_refuses_a_new_link_until_it_frees(
    db, executor, plural, kicks, monkeypatch
):
    tenant = await _tenant(db)
    stored = await _save_plural(tenant)
    runtime = _with_catalog(_runtime(monkeypatch, tenant, _plural_payments()), tenant, inventory_quantity_snapshot=1)
    first = await _ready(runtime, tenant)
    linked = await _complete(runtime, tenant, first["id"])
    assert linked.body["status"] == "requires_escalation"
    second = await _ready(runtime, tenant)
    refused = await _complete(runtime, tenant, second["id"])
    errors = [m for m in refused.body["messages"] if m["type"] == "error"]
    assert [m["code"] for m in errors] == ["out_of_stock"], refused.body
    assert "held by another buyer's pending payment until about" in errors[0]["content"]
    assert "Only 0 of" in errors[0]["content"]
    assert len(plural.bodies("/checkout/v1/orders")) == 1 and refused.body["status"] == "ready_for_complete"

    # The first buyer's payment fails: its link no longer holds the stock.
    failed = json.dumps(
        {
            "event_type": "ORDER_FAILED",
            "data": {
                "order_id": "v1-hosted-1",
                "merchant_order_reference": plural.bodies("/checkout/v1/orders")[0]["merchant_order_reference"],
                "status": "FAILED",
                "order_amount": {"value": 1, "currency": "INR"},
            },
        }
    ).encode()
    retired = await plural_hosted.apply_webhook(
        tenant_id=tenant, webhook_key=stored.webhook_key, raw_body=failed, headers=_signed(failed, webhook_id="wh_f")
    )
    assert retired["result"] == "link_retired"
    opened = await _complete(runtime, tenant, second["id"])
    assert opened.body["status"] == "requires_escalation" and len(plural.bodies("/checkout/v1/orders")) == 2


@pytest.mark.asyncio
async def test_a_lapsed_link_no_longer_holds_stock(db, executor, plural, kicks, monkeypatch):
    from core.commerce.payment_handoff import iso

    tenant = await _tenant(db)
    await _save_plural(tenant)
    runtime = _with_catalog(_runtime(monkeypatch, tenant, _plural_payments()), tenant, inventory_quantity_snapshot=1)
    first = await _ready(runtime, tenant)
    await _complete(runtime, tenant, first["id"])
    _status, state = await _state_of(db, first["id"])
    reference = state["handoff"]["reference"]
    lapsed = iso(datetime.now(UTC) - timedelta(minutes=1))
    await _exec(
        db,
        "UPDATE commerce_checkout_sessions SET state = jsonb_set(state, CAST(:path AS text[]), "
        "to_jsonb(CAST(:iso AS text))) WHERE id = :i",
        path=["handoff_orders", reference, "expires_at"],
        iso=lapsed,
        i=first["id"],
    )
    second = await _ready(runtime, tenant)
    opened = await _complete(runtime, tenant, second["id"])
    assert opened.body["status"] == "requires_escalation", opened.body


@pytest.mark.asyncio
async def test_a_payment_after_the_stock_ran_out_still_becomes_an_order_marked_short_and_the_merchant_is_told(
    db, executor, plural, kicks, told, monkeypatch
):
    from api.v1 import commerce_payments
    from core.commerce.hosted_refunds import refund_unmatched_payment

    tenant = await _tenant(db)
    stored = await _save_plural(tenant)
    runtime = _runtime(monkeypatch, tenant, _plural_payments())
    session = await _ready(runtime, tenant)
    await _complete(runtime, tenant, session["id"])
    # The shop sold its last unit elsewhere before the buyer paid.
    sold_out = _with_catalog(runtime, tenant, inventory_quantity_snapshot=0)
    monkeypatch.setattr(handlers, "payment_catalog_for", sold_out.catalog_for)
    body = plural.bodies("/checkout/v1/orders")[0]
    paid = json.dumps(
        {
            "event_type": "ORDER_PROCESSED",
            "data": {
                "order_id": "v1-hosted-1",
                "merchant_order_reference": body["merchant_order_reference"],
                "status": "PROCESSED",
                "order_amount": body["order_amount"],
            },
        }
    ).encode()
    applied = await plural_hosted.apply_webhook(
        tenant_id=tenant, webhook_key=stored.webhook_key, raw_body=paid, headers=_signed(paid, webhook_id="wh_short")
    )
    assert applied["result"] == "completed" and applied["applied"] is True
    order_id = applied["order_id"]
    snapshot = (await _rows(db, "SELECT snapshot FROM commerce_orders WHERE id = :i", i=order_id))[0]["snapshot"]
    lines = snapshot["stock_short"]["lines"]
    assert snapshot["stock_short"]["reason"] == "out_of_stock_at_payment"
    assert [(line["quantity"], line["in_stock"]) for line in lines] == [(1, 0)]
    kept = await _rows(
        db, "SELECT id, reason, order_id FROM commerce_unmatched_payments WHERE tenant_id = :t", t=tenant
    )
    assert [(k["reason"], k["order_id"]) for k in kept] == [("out_of_stock_at_payment", order_id)]
    queued = await _rows(
        db, "SELECT kind FROM commerce_merchant_notifications WHERE tenant_id = :t ORDER BY kind", t=tenant
    )
    assert [q["kind"] for q in queued] == ["order", "payment_attention"]
    assert [d["result"] for _t, _c, d in told] == ["out_of_stock_at_payment"]
    assert kicks == [(tenant, order_id)]
    view = (await commerce_payments.list_orders(merchant_id=None, limit=25, offset=0, tenant_id=tenant))["orders"][0]
    assert view["stock_short"] == lines

    # Refunding it from the list refunds what is left of the order, through the order.
    refunded = await refund_unmatched_payment(tenant_id=tenant, payment_id=str(kept[0]["id"]), reason=None)
    assert refunded.status == "succeeded" and refunded.payment["refund_status"] == "refunded"
    assert [body["refund_amount"]["value"] for _o, body in plural.refunds()] == [body["order_amount"]["value"]]
    snapshot = (await _rows(db, "SELECT snapshot FROM commerce_orders WHERE id = :i", i=order_id))[0]["snapshot"]
    assert [a["status"] for a in snapshot["adjustments"] if a["type"] == "refund"] == ["completed"]


@pytest.mark.asyncio
async def test_the_stock_short_email_names_the_order_and_what_to_do(db, executor, plural, kicks, told, monkeypatch):
    from core import email as core_email
    from core.commerce import merchant_notifications as outbox

    tenant = await _tenant(db)
    await _admin(db, tenant)
    stored = await _save_plural(tenant)
    runtime = _runtime(monkeypatch, tenant, _plural_payments())
    session = await _ready(runtime, tenant)
    await _complete(runtime, tenant, session["id"])
    monkeypatch.setattr(
        handlers, "payment_catalog_for", _with_catalog(runtime, tenant, inventory_quantity_snapshot=0).catalog_for
    )
    body = plural.bodies("/checkout/v1/orders")[0]
    paid = json.dumps(
        {
            "event_type": "ORDER_PROCESSED",
            "data": {
                "order_id": "v1-hosted-1",
                "merchant_order_reference": body["merchant_order_reference"],
                "status": "PROCESSED",
                "order_amount": body["order_amount"],
            },
        }
    ).encode()
    applied = await plural_hosted.apply_webhook(
        tenant_id=tenant, webhook_key=stored.webhook_key, raw_body=paid, headers=_signed(paid, webhook_id="wh_mail")
    )
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        core_email, "send_email", lambda to, subject, html, **_: sent.append((to, subject, html)) or True
    )
    assert (await outbox.deliver_tenant(tenant)).sent == 2
    subjects = sorted(s[1] for s in sent)
    assert any(s.startswith("New order") for s in subjects)
    short = next(s for s in sent if "stock is short" in s[1])
    assert applied["order_id"] in short[1] and "Ship what you can and refund the rest" in short[2]
    assert "/dashboard/orders" in short[2] and "ordered 1, in stock 0" in short[2]


# --- loyalty when Complete re-prices -----------------------------------------------------------------------


def _forecast(state: dict[str, Any]) -> int | None:
    loyalty = state.get("loyalty") or {}
    membership = next(iter(loyalty.values()), None) if isinstance(loyalty, dict) else None
    if not membership:
        return None
    return membership["rewards"][0]["earning_forecast"]["amount"]


def _total(state: dict[str, Any]) -> int:
    return next(t["amount"] for t in state["totals"] if t["type"] == "total")


@pytest.mark.asyncio
@pytest.mark.parametrize("reads_available", [True, False])
async def test_loyalty_follows_the_new_total_when_complete_reprices_and_is_kept_without_shopify_reads(
    db, executor, kicks, monkeypatch, reads_available
):
    import dataclasses

    tenant = await _tenant(db)
    runtime = _runtime(monkeypatch, tenant, {"mode": "shopify_cart"})
    loyal = {
        "buyer": {"email": "buyer@example.com"},
        "context": {"eligibility": ["com.merchant.rewards"]},
    }
    session = await _ready(runtime, tenant, **loyal)
    _status, before = await _state_of(db, session["id"])
    assert _forecast(before) == _total(before) // 100 * 2, before.get("loyalty")

    changed = _with_catalog(runtime, tenant, price="300.00")
    if not reads_available:

        async def _no_reads(_m: Any) -> None:
            return None

        changed = dataclasses.replace(changed, shopify_reads_for=_no_reads)
    refused = await _complete(changed, tenant, session["id"], body=uf.complete_body())
    assert [m["code"] for m in refused.body["messages"] if m["type"] == "error"] == ["price_changed"]
    _status, after = await _state_of(db, session["id"])
    assert _total(after) != _total(before)
    if reads_available:
        assert _forecast(after) == _total(after) // 100 * 2
    else:
        assert after["loyalty"] == before["loyalty"]
