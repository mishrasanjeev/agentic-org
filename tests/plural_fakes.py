# SPDX-License-Identifier: Apache-2.0
"""A fake Plural API (httpx MockTransport) and an in-memory payment journal for executor tests.

Synthetic only: the base URL is on plural.example and the credentials are placeholders.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from core.commerce.executors.plural import PluralCredentials
from core.commerce.payment_records import OperationRecord, PaymentState, payment_state_from

BASE_URL = "https://plural.example/api"
WEBHOOK_SECRET = base64.b64encode(b"synthetic-webhook-secret-for-tests").decode()


def credentials() -> PluralCredentials:
    return PluralCredentials(
        client_id="client-test", client_secret="secret-test", base_url=BASE_URL, webhook_secret=WEBHOOK_SECRET
    )


@dataclass
class FakePlural:
    """Records every request; answers like the Plural v1 API with configurable outcomes."""

    payment_status: str = "AUTHORIZED"
    authorized_amount: int | None = None
    challenge_url: str | None = None
    payment_http_status: int = 200
    capture_status: str = "PROCESSED"
    refund_status: str = "PROCESSED"
    order_http_status: int = 200
    requests: list[httpx.Request] = field(default_factory=list)
    _orders: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def factory(self):
        return self.transport

    @property
    def api_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if not r.url.path.endswith("/auth/v1/token")]

    def bodies(self, suffix: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.api_calls if r.url.path.endswith(suffix) or suffix in r.url.path]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/auth/v1/token"):
            return httpx.Response(200, json={"access_token": "access-test", "expires_at": "2099-01-01T00:00:00Z"})
        body = json.loads(request.content) if request.content else {}
        if path.endswith("/pay/v1/orders") and request.method == "POST":
            if self.order_http_status >= 400:
                return httpx.Response(self.order_http_status, json={"code": "INVALID_REQUEST", "message": "bad"})
            self._orders += 1
            return httpx.Response(
                200,
                json={
                    "data": {
                        "order_id": f"v1-ord-{self._orders}",
                        "merchant_order_reference": body.get("merchant_order_reference"),
                        "status": "CREATED",
                    }
                },
            )
        if path.endswith("/payments"):
            if self.payment_http_status >= 400:
                return httpx.Response(self.payment_http_status, json={"code": "PAYMENT_DECLINED"})
            order_id = path.split("/")[-2]
            amount = body["payments"][0]["payment_amount"]["value"]
            payment: dict[str, Any] = {
                "status": self.payment_status,
                "payment_amount": {"value": self.authorized_amount or amount, "currency": "INR"},
            }
            data: dict[str, Any] = {"order_id": order_id, "status": self.payment_status, "payments": [payment]}
            if self.challenge_url:
                data["challenge_url"] = self.challenge_url
            return httpx.Response(200, json={"data": data})
        if path.endswith("/capture"):
            return httpx.Response(200, json={"data": {"order_id": path.split("/")[-2], "status": self.capture_status}})
        if "/pay/v1/refunds/" in path:
            refunds = len([r for r in self.requests if "/pay/v1/refunds/" in r.url.path])
            status = self.refund_status
            return httpx.Response(
                200,
                json={"data": {"order_id": path.split("/")[-1], "refund_id": f"rf-{refunds}", "status": status}},
            )
        return httpx.Response(404, json={"code": "NOT_FOUND"})


@dataclass
class _Row:
    operation: str
    idempotency_key: str
    phase: str
    amount_minor_units: int | None
    currency: str | None
    provider_ref: str | None
    decision_id: str


class MemoryJournal:
    """``PaymentJournal`` in memory (the same uniqueness rule as ``commerce_payment_operations``)."""

    def __init__(self) -> None:
        self.rows: list[_Row] = []
        self.blocked_reasons: list[tuple[str, str]] = []

    async def begin(self, op: OperationRecord) -> bool:
        if any(
            r.idempotency_key == op.idempotency_key and r.operation == op.operation and r.phase == "requested"
            for r in self.rows
        ):
            return False
        self.rows.append(self._row(op, "requested", None))
        return True

    async def finish(
        self, op: OperationRecord, phase: str, *, provider_ref: str | None, details: Mapping[str, Any]
    ) -> None:
        self.rows.append(self._row(op, phase, provider_ref))

    async def blocked(self, op: OperationRecord, reason: str) -> None:
        self.blocked_reasons.append((op.operation, reason))

    async def state(self, tenant_id: str, checkout_session_id: str) -> PaymentState:
        return payment_state_from(self.rows)

    @staticmethod
    def _row(op: OperationRecord, phase: str, provider_ref: str | None) -> _Row:
        return _Row(
            operation=op.operation,
            idempotency_key=op.idempotency_key,
            phase=phase,
            amount_minor_units=op.amount_minor_units,
            currency=op.currency,
            provider_ref=provider_ref,
            decision_id=op.decision_id,
        )


def signed_headers(body: bytes, *, webhook_id: str = "wh_test_1", timestamp: int, secret: str = WEBHOOK_SECRET):
    signed = f"{webhook_id}.{timestamp}.".encode() + body
    signature = base64.b64encode(hmac.new(base64.b64decode(secret), signed, hashlib.sha256).digest()).decode()
    return {"webhook-id": webhook_id, "webhook-timestamp": str(timestamp), "webhook-signature": f"v1,{signature}"}
