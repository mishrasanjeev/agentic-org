# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The Celery entry points for payment hand-offs: the Plural status poll and the new-order email."""

from __future__ import annotations

import asyncio

import pytest

from core.commerce import order_notifications, plural_hosted
from core.config import settings
from core.tasks import payment_handoff_tasks as tasks
from core.tasks.celery_app import app


@pytest.fixture()
def poll(monkeypatch):
    calls: list[dict] = []

    async def poll_open_handoffs(**kwargs):
        calls.append(kwargs)
        return {"completed": 2, "status_unavailable": 1}

    monkeypatch.setattr(plural_hosted, "poll_open_handoffs", poll_open_handoffs)
    return calls


def test_the_poll_is_skipped_while_checkout_is_disabled(monkeypatch, poll):
    monkeypatch.setattr(settings, "ucp_checkout_enabled", False)
    monkeypatch.setattr(settings, "acp_checkout_enabled", False)

    def run_async(_coro):
        raise AssertionError("the poll must not run while checkout is disabled")

    monkeypatch.setattr(tasks, "run_async", run_async)
    assert tasks.poll_plural_handoffs() == {"skipped": "checkout_disabled"}
    assert poll == []


@pytest.mark.parametrize(("ucp", "acp"), [(True, False), (False, True), (True, True)])
def test_the_poll_runs_when_either_checkout_is_enabled(monkeypatch, poll, ucp, acp):
    monkeypatch.setattr(settings, "ucp_checkout_enabled", ucp)
    monkeypatch.setattr(settings, "acp_checkout_enabled", acp)
    monkeypatch.setattr(tasks, "run_async", asyncio.run)
    assert tasks.poll_plural_handoffs() == {"completed": 2, "status_unavailable": 1}
    # Every tenant with pending work is polled (no tenant filter, the live transport).
    assert poll == [{}]


def test_notify_order_reports_how_many_emails_went_out(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def notify_merchant_of_order(tenant_id, order_id):
        calls.append((tenant_id, order_id))
        return 2

    monkeypatch.setattr(order_notifications, "notify_merchant_of_order", notify_merchant_of_order)
    monkeypatch.setattr(tasks, "run_async", asyncio.run)
    assert tasks.notify_order("00000000-0000-0000-0000-0000000000d1", "ord_1") == {"sent": 2}
    assert calls == [("00000000-0000-0000-0000-0000000000d1", "ord_1")]


def test_the_tasks_are_registered_scheduled_and_routed():
    poll_name = "core.tasks.payment_handoff_tasks.poll_plural_handoffs"
    notify_name = "core.tasks.payment_handoff_tasks.notify_order"
    assert tasks.poll_plural_handoffs.name == poll_name and tasks.notify_order.name == notify_name
    assert poll_name in app.tasks and notify_name in app.tasks
    beat = app.conf.beat_schedule["poll-plural-handoffs"]
    assert beat["task"] == poll_name and beat["schedule"] == 60.0
    assert app.conf.task_routes[notify_name] == {"queue": "delivery"}
