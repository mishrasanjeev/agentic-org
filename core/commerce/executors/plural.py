# SPDX-License-Identifier: Apache-2.0
"""The Plural executor: the direct Plural (Pine Labs) rail that turns a consumed ``allow`` into money movement.

Decision (docs/oacp/plural-executor.md): AgenticOrg calls the Plural API
directly with the merchant's Plural credentials. The alternative, routing
execution through Grantex Commerce V1, is recorded there and not built.

Every provider call is preceded by a consumed arbiter decision and carries its
``decision_id``:

* ``execute(session, decision)`` takes a ``ConsumedDecision`` for
  ``session_complete`` (only ``executors.consume_decision`` can make one, and
  it runs ``require_allow_decision`` first). It creates a Plural order
  (``pre_auth``), a payment with the tokenised instrument, and captures it.
* ``refund(request, decision)`` takes a ``ConsumedDecision`` for ``refund``
  (``core.commerce.order_refunds`` decides, persists and consumes it first).

Bounds checked before any call: the charge is the session total, at least 1,
in the handler allowance currency and at most the allowance
(``mandate_capability.max_amount_minor_units``); a capture never exceeds the
authorised amount; a refund never exceeds the captured amount less earlier
refunds (``payment_records.payment_state``). A UPI AutoPay instrument needs
the server-side RBI AFA evidence ref.

Idempotency: the merchant order reference is derived from the session id and
the decision id (``order_reference``), the refund reference from the order id
and the refund decision id, and ``Request-ID`` from the reference and the
operation; the journal (``commerce_payment_operations``) records each call
before it is sent, so a repeated attempt is refused without an HTTP call.
Order creation is never retried. Every call is ledgered with its decision id.

The order metadata carries the decision id, the session payload hash and the
intent, cart and payment mandate hashes from the decision evidence (empty
strings until verified mandates populate them).

HTTP: async ``httpx`` over the DNS-pinning egress transport, the URL checked by
the async egress validator first, a 20 s timeout, no redirects, a 256 KiB
response cap. Request and response field names follow the Plural API v1
reference (developer.pinelabsonline.com); ``tests/integration/
test_plural_executor_sandbox.py`` exercises them against the sandbox when its
credentials are configured.

Webhooks use the scheme the billing integration already verifies
(``core.billing.pinelabs_client.plural_signature_valid``): HMAC-SHA256 over
``webhook-id.webhook-timestamp.body`` with ``PLURAL_PINE_WEBHOOK_SECRET``,
a 5 minute timestamp window.

Registration: with ``PLURAL_PINE_LIVE_EXECUTION_ENABLED=true`` the executor is
registered under ``plural_pine`` (a merchant selects it with
``provider_policy.ucp.executor: plural_pine``); otherwise it is not, and UCP
Complete answers ``executor_unavailable``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from core.commerce.executors import (
    REFUND,
    SESSION_COMPLETE,
    ConsumedDecision,
    ExecutionCheckout,
    ExecutionInstrument,
    ExecutionResult,
)
from core.security.egress import EgressValidationError, build_pinned_async_transport, validate_public_url_async

logger = structlog.get_logger()

EXECUTOR_NAME = "plural_pine"
PLURAL_HANDLER_ID = "plural_tokenized"
LIVE_FLAG_ENV = "PLURAL_PINE_LIVE_EXECUTION_ENABLED"
CLIENT_ID_ENV = "PLURAL_PINE_CLIENT_ID"
CLIENT_SECRET_ENV = "PLURAL_PINE_CLIENT_SECRET"
ENVIRONMENT_ENV = "PLURAL_PINE_ENVIRONMENT"
BASE_URL_ENV = "PLURAL_PINE_BASE_URL"
WEBHOOK_SECRET_ENV = "PLURAL_PINE_WEBHOOK_SECRET"
TIMEOUT_SECONDS = 20.0
MAX_RESPONSE_BYTES = 256 * 1024
WEBHOOK_TOLERANCE_SECONDS = 300
AUTHORIZED = frozenset({"AUTHORIZED"})
CAPTURED = frozenset({"PROCESSED", "CAPTURED"})
FAILED = frozenset({"FAILED", "CANCELLED", "DECLINED", "REJECTED"})
MANDATE_AUTHORIZED = frozenset({"ACTIVE", "AUTHORIZED", "APPROVED"})
MANDATE_FAILED = frozenset({"FAILED", "REJECTED", "DECLINED", "CANCELLED", "EXPIRED"})

TransportFactory = Callable[[], httpx.AsyncBaseTransport | Any]


def live_execution_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return str(source.get(LIVE_FLAG_ENV, "") or "").strip().lower() == "true"


# --- credentials and HTTP ------------------------------------------------------------------------


@dataclass(frozen=True)
class PluralCredentials:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    base_url: str
    webhook_secret: str = field(repr=False, default="")


def credentials_from_env(env: Mapping[str, str] | None = None) -> PluralCredentials | None:
    """The merchant's Plural API credentials from the environment, or None when incomplete."""
    from core.billing.pinelabs_client import PLURAL_BASE_URLS

    source = os.environ if env is None else env
    client_id = str(source.get(CLIENT_ID_ENV, "") or "").strip()
    client_secret = str(source.get(CLIENT_SECRET_ENV, "") or "").strip()
    if not client_id or not client_secret:
        return None
    environment = str(source.get(ENVIRONMENT_ENV, "") or "sandbox").strip().lower()
    base_url = str(source.get(BASE_URL_ENV, "") or "").strip() or PLURAL_BASE_URLS.get(environment, "")
    if not base_url.startswith("https://"):
        return None
    return PluralCredentials(
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url.rstrip("/"),
        webhook_secret=str(source.get(WEBHOOK_SECRET_ENV, "") or "").strip(),
    )


class PluralUnavailableError(RuntimeError):
    """Plural could not be reached, or answered with something unusable (the outcome is unknown)."""


class PluralRequestError(RuntimeError):
    """Plural refused the request (HTTP 4xx/5xx with a body); ``code`` is Plural's error code when given."""

    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(f"Plural answered HTTP {status_code} ({code})")
        self.status_code = status_code
        self.code = code


class PluralApiClient:
    """Async Plural API v1 client: OAuth client-credentials token, then one JSON call."""

    def __init__(self, credentials: PluralCredentials, *, transport_factory: TransportFactory | None = None) -> None:
        self._credentials = credentials
        self._transport_factory = transport_factory
        self._token: tuple[str, float] | None = None

    async def call(
        self, method: str, path: str, body: Mapping[str, Any] | None, *, request_id: str
    ) -> dict[str, Any]:
        url = f"{self._credentials.base_url}{path}"
        try:
            await validate_public_url_async(
                url, allowed_schemes=("https",), require_dns=self._transport_factory is None
            )
        except EgressValidationError as exc:
            raise PluralUnavailableError("unsafe Plural endpoint") from exc
        transport = (
            self._transport_factory() if self._transport_factory is not None else build_pinned_async_transport()
        )
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS, transport=transport, follow_redirects=False
            ) as client:
                token = await self._access_token(client)
                response = await client.request(
                    method,
                    url,
                    json=dict(body) if body is not None else None,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Request-ID": request_id,
                        "Request-Timestamp": _timestamp(),
                    },
                )
        except (httpx.HTTPError, EgressValidationError, OSError) as exc:
            raise PluralUnavailableError(f"Plural unreachable: {type(exc).__name__}") from exc
        return _parse(response)

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        now = time.time()
        if self._token is not None and self._token[1] > now + 60:
            return self._token[0]
        response = await client.post(
            f"{self._credentials.base_url}/auth/v1/token",
            json={
                "client_id": self._credentials.client_id,
                "client_secret": self._credentials.client_secret,
                "grant_type": "client_credentials",
            },
            headers={"Accept": "application/json"},
        )
        data = _parse(response)
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise PluralUnavailableError("Plural token response without access_token")
        self._token = (token, now + 50 * 60)
        return token


def _timestamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(response: httpx.Response) -> dict[str, Any]:
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise PluralUnavailableError("Plural response exceeds the size limit")
    try:
        parsed = response.json()
    except ValueError as exc:
        raise PluralUnavailableError(f"Plural answered HTTP {response.status_code} without JSON") from exc
    if not isinstance(parsed, dict):
        raise PluralUnavailableError(f"Plural answered HTTP {response.status_code} with a non-object")
    if response.status_code >= 400:
        code = parsed.get("code") or parsed.get("error_code") or f"http_{response.status_code}"
        raise PluralRequestError(response.status_code, str(code)[:64])
    data = parsed.get("data")
    return dict(data) if isinstance(data, Mapping) else parsed


# --- references and payloads ---------------------------------------------------------------------


def order_reference(session_id: str, decision_id: str) -> str:
    """The Plural ``merchant_order_reference`` (A-Z a-z 0-9 - _, at most 50) for one session and decision."""
    return "aoc-" + hashlib.sha256(f"{session_id}|{decision_id}".encode()).hexdigest()[:44]


def refund_reference(order_id: str, decision_id: str) -> str:
    return "aor-" + hashlib.sha256(f"{order_id}|{decision_id}".encode()).hexdigest()[:44]


def request_id(reference: str, operation: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"plural:{reference}:{operation}"))


def order_metadata(decision: ConsumedDecision, *, session_id: str, payload_hash: str) -> dict[str, str]:
    """``purchase_details.merchant_metadata``: the decision and the three mandate hashes (empty until known)."""
    hashes = decision.mandate_hashes or {}
    return {
        "oacp_decision_id": decision.decision_id,
        "oacp_checkout_session_id": session_id,
        "oacp_payload_hash": payload_hash,
        "oacp_intent_mandate_hash": str(hashes.get("intent") or ""),
        "oacp_cart_mandate_hash": str(hashes.get("cart") or ""),
        "oacp_payment_mandate_hash": str(hashes.get("payment") or ""),
    }


def payment_option(instrument: ExecutionInstrument, *, mandate_reference: str | None) -> tuple[str, dict[str, Any]]:
    """Plural ``payment_method`` and ``payment_option`` for a tokenised instrument (never a PAN)."""
    from core.commerce.india_rails import INSTRUMENT_UPI_AUTOPAY

    credential = dict(instrument.credential or {})
    kind = credential.get("type")
    if instrument.type == INSTRUMENT_UPI_AUTOPAY:
        return "UPI", {
            "upi_details": {
                "txn_mode": "MANDATE",
                "token": credential.get("token"),
                "mandate_reference": mandate_reference,
            }
        }
    if kind == "network_token" or (kind == "card" and credential.get("card_number_type") == "network_token"):
        return "CARD", {
            "card_token_details": {
                "token": credential.get("number"),
                "cryptogram": credential.get("cryptogram"),
                "token_type": "NETWORK_TOKEN",
                "expiry_month": credential.get("expiry_month"),
                "expiry_year": credential.get("expiry_year"),
            }
        }
    return "CARD", {"card_token_details": {"token": credential.get("token"), "token_type": "PLURAL_TOKEN"}}


def _instrument_problem(instrument: ExecutionInstrument) -> str | None:
    credential = dict(instrument.credential or {})
    kind = credential.get("type")
    if kind in ("pan",) or (kind == "card" and credential.get("card_number_type") == "fpan"):
        return "pan_not_accepted"
    if kind == "plural_pine_token" and credential.get("token"):
        return None
    if kind == "network_token" and credential.get("number") and credential.get("cryptogram"):
        return None
    if kind == "card" and credential.get("card_number_type") == "network_token" and credential.get("number"):
        return None
    return "credential_not_accepted"


# --- the executor -------------------------------------------------------------------------------


@dataclass(frozen=True)
class RefundRequest:
    tenant_id: str
    merchant_id: str
    seller_agent_id: str
    order_id: str
    checkout_session_id: str
    payload_hash: str
    amount_minor_units: int
    currency: str
    order_total_minor_units: int
    allowance_max_minor_units: int | None


@dataclass(frozen=True)
class RefundResult:
    status: str  # "succeeded", "failed", "unknown", "refused"
    reason: str
    provider_refund_ref: str | None = None
    idempotency_key: str | None = None


class PluralExecutor:
    """``CheckoutExecutor`` over the Plural API; ``refund`` for the order refund orchestration."""

    name = EXECUTOR_NAME

    def __init__(
        self,
        *,
        credentials: Callable[[], PluralCredentials | None] = credentials_from_env,
        transport_factory: TransportFactory | None = None,
        journal: Any = None,
    ) -> None:
        from core.commerce.payment_records import DbPaymentJournal

        self._credentials = credentials
        self._transport_factory = transport_factory
        self._journal = journal if journal is not None else DbPaymentJournal()

    def _client(self) -> PluralApiClient | None:
        credentials = self._credentials()
        return None if credentials is None else PluralApiClient(credentials, transport_factory=self._transport_factory)

    async def execute(self, session: ExecutionCheckout, decision: ConsumedDecision) -> ExecutionResult:
        from core.commerce.india_rails import INSTRUMENT_UPI_AUTOPAY
        from core.commerce.payment_records import OperationRecord

        reference = order_reference(session.session_id, decision.decision_id)
        amount = session.total_minor_units
        currency = session.currency.upper()

        def op(operation: str) -> OperationRecord:
            return OperationRecord(
                tenant_id=session.tenant_id,
                merchant_id=session.merchant_id,
                seller_agent_id=session.seller_agent_id,
                checkout_session_id=session.session_id,
                decision_id=decision.decision_id,
                operation=operation,
                idempotency_key=reference,
                amount_minor_units=amount,
                currency=currency,
                mandate_hashes=dict(decision.mandate_hashes or {}),
            )

        create = op("create_order")
        problem = self._execution_problem(session, decision)
        plural = [i for i in session.instruments if i.handler_id == PLURAL_HANDLER_ID]
        instrument = plural[0] if len(plural) == 1 and len(session.instruments) == 1 else None
        if problem is None and instrument is None:
            problem = "single_plural_instrument_required"
        if problem is None and instrument is not None:
            problem = _instrument_problem(instrument)
            if problem is None and instrument.amount_minor_units not in (None, amount):
                problem = "instrument_amount_mismatch"
            if problem is None and instrument.type == INSTRUMENT_UPI_AUTOPAY:
                if not session.afa_evidence_ref or not session.upi_mandate_reference:
                    problem = "afa_evidence_missing"
        client = self._client() if problem is None else None
        if problem is None and client is None:
            problem = "plural_credentials_missing"
        if problem is not None or instrument is None or client is None:
            await self._journal.blocked(create, problem or "not_executable")
            return _failed(problem or "not_executable")

        metadata = order_metadata(decision, session_id=session.session_id, payload_hash=session.payload_hash)
        method, option = payment_option(instrument, mandate_reference=session.upi_mandate_reference)
        order_body = {
            "merchant_order_reference": reference,
            "order_amount": {"value": amount, "currency": currency},
            "pre_auth": True,
            "allowed_payment_methods": [method],
            "notes": f"OACP decision {decision.decision_id}",
            "purchase_details": {"merchant_metadata": metadata},
        }
        phase, created = await self._call(client, create, "POST", "/pay/v1/orders", order_body)
        if phase != "succeeded":
            return _failed("payment_failed")
        provider_order = str(created.get("order_id") or "")
        if not provider_order:
            return _failed("payment_failed")

        pay = op("create_payment")
        payment_body = {
            "payments": [
                {
                    "merchant_payment_reference": reference,
                    "payment_method": method,
                    "payment_amount": {"value": amount, "currency": currency},
                    "payment_option": option,
                }
            ]
        }
        phase, paid = await self._call(
            client, pay, "POST", f"/pay/v1/orders/{provider_order}/payments", payment_body, provider_ref=provider_order
        )
        if phase != "succeeded":
            return _failed("payment_declined" if phase == "failed" else "payment_failed")
        status = str(paid.get("status") or "").upper()
        payments = paid.get("payments") if isinstance(paid.get("payments"), list) else []
        first = payments[0] if payments and isinstance(payments[0], Mapping) else {}
        challenge = str(paid.get("challenge_url") or first.get("challenge_url") or "")
        if challenge.startswith("https://"):
            return ExecutionResult(
                status="action_required",
                provider_order_ref=provider_order,
                escalation_url=challenge,
                actions={
                    "dev.ucp.common.payment.three_ds_challenge": [
                        {
                            "id": f"three-ds-{reference[4:20]}",
                            "config": {"payment_instrument_id": instrument.id, "url": challenge},
                        }
                    ]
                },
            )
        if status in FAILED:
            return _failed("payment_declined")
        if status not in AUTHORIZED:
            return _failed("payment_pending")
        authorized_amount = _amount(first.get("payment_amount")) or _amount(paid.get("order_amount"))
        if authorized_amount is not None and authorized_amount != amount:
            await self._journal.blocked(op("capture"), "authorized_amount_mismatch")
            return _failed("payment_failed")

        capture = op("capture")
        phase, _captured = await self._call(
            client,
            capture,
            "PUT",
            f"/pay/v1/orders/{provider_order}/capture",
            {"merchant_capture_reference": reference, "capture_amount": {"value": amount, "currency": currency}},
            provider_ref=provider_order,
            success=lambda data: str(data.get("status") or "").upper() in CAPTURED,
        )
        if phase != "succeeded":
            return _failed("payment_failed")
        return ExecutionResult(status="completed", provider_order_ref=provider_order)

    def _execution_problem(self, session: ExecutionCheckout, decision: ConsumedDecision) -> str | None:
        if decision.action != SESSION_COMPLETE:
            return "decision_action_mismatch"
        if (
            decision.payload_hash != session.payload_hash
            or decision.tenant_id != str(session.tenant_id)
            or decision.merchant_id != session.merchant_id
        ):
            return "decision_scope_mismatch"
        return _amount_problem(
            session.total_minor_units,
            session.currency,
            ceiling=session.total_minor_units,
            allowance_max=session.allowance_max_minor_units,
            allowance_currency=session.allowance_currency,
        )

    async def refund(self, request: RefundRequest, decision: ConsumedDecision) -> RefundResult:
        """Refund ``amount`` of a captured Plural order under a consumed ``refund`` decision."""
        from core.commerce.payment_records import OperationRecord

        key = refund_reference(request.order_id, decision.decision_id)
        record = OperationRecord(
            tenant_id=request.tenant_id,
            merchant_id=request.merchant_id,
            seller_agent_id=request.seller_agent_id,
            checkout_session_id=request.checkout_session_id,
            decision_id=decision.decision_id,
            operation="refund",
            idempotency_key=key,
            amount_minor_units=request.amount_minor_units,
            currency=request.currency.upper(),
            order_id=request.order_id,
            mandate_hashes=dict(decision.mandate_hashes or {}),
        )
        problem: str | None = None
        if decision.action != REFUND:
            problem = "decision_action_mismatch"
        elif decision.payload_hash != request.payload_hash or decision.tenant_id != str(request.tenant_id):
            problem = "decision_scope_mismatch"
        state = await self._journal.state(request.tenant_id, request.checkout_session_id)
        if problem is None:
            problem = _amount_problem(
                request.amount_minor_units,
                request.currency,
                ceiling=min(request.order_total_minor_units, state.refundable_minor_units),
                allowance_max=request.allowance_max_minor_units,
                allowance_currency=state.currency,
                ceiling_reason="refund_exceeds_captured",
            )
        if problem is None and not state.provider_order_ref:
            problem = "order_not_captured"
        client = self._client() if problem is None else None
        if problem is None and client is None:
            problem = "plural_credentials_missing"
        if problem is not None or client is None:
            await self._journal.blocked(record, problem or "not_executable")
            return RefundResult(status="refused", reason=problem or "not_executable", idempotency_key=key)
        body = {
            "merchant_order_reference": key,
            "refund_amount": {"value": request.amount_minor_units, "currency": request.currency.upper()},
            "merchant_metadata": {
                "oacp_decision_id": decision.decision_id,
                "oacp_order_id": request.order_id,
                "oacp_payload_hash": request.payload_hash,
            },
        }
        phase, result = await self._call(
            client,
            record,
            "POST",
            f"/pay/v1/refunds/{state.provider_order_ref}",
            body,
            provider_ref=state.provider_order_ref,
            success=lambda data: str(data.get("status") or "").upper() in CAPTURED | {"SUCCESS", "REFUNDED"},
            pending=lambda data: str(data.get("status") or "").upper() in {"PENDING", "INITIATED", "CREATED"},
        )
        if phase == "replayed":
            return RefundResult(status="refused", reason="operation_already_requested", idempotency_key=key)
        if phase != "succeeded":
            return RefundResult(status=phase, reason=f"refund_{phase}", idempotency_key=key)
        return RefundResult(
            status="succeeded",
            reason="refund_succeeded",
            provider_refund_ref=str(result.get("refund_id") or result.get("order_id") or "") or None,
            idempotency_key=key,
        )

    async def reconcile(self, *, tenant_id: str, outcome: PaymentOutcome, event_sha256: str) -> ReconcileResult:
        """Apply a verified Plural order event to the journal and the session; idempotent per phase.

        The order must be one this executor created in the tenant (a
        ``create_order`` ``succeeded`` row with this provider order ref and
        merchant reference). Then:

        * ``CAPTURED``/``PROCESSED``: a ``capture`` ``succeeded`` row is
          appended (source ``webhook``) unless one exists, and the session's
          order is created.
        * ``AUTHORIZED`` (a payment that completed after the buyer's 3-D Secure
          step, or that was pending): the authorised amount is captured with
          the same merchant reference, unless a capture was already requested,
          under the decision consumed at Complete (the journal refuses a second
          capture). A captured payment then completes the session.
        * ``FAILED`` and the like: a ``create_payment`` ``failed`` row is
          appended and the session returns to ``ready_for_complete``.
        * Anything else (still pending): nothing changes.
        """
        from datetime import UTC, datetime

        from core.commerce.payment_records import OperationRecord
        from core.commerce.ucp.handlers import reconcile_execution

        entries = await self._journal.operations(tenant_id, outcome.merchant_reference)
        created = next(
            (
                e
                for e in entries
                if e.operation == "create_order" and e.phase == "succeeded" and e.provider_ref == outcome.order_id
            ),
            None,
        )
        if created is None:
            return ReconcileResult("unknown_order")
        seller = await self._journal.session_seller(tenant_id, created.checkout_session_id) or ""

        def op(operation: str) -> OperationRecord:
            return OperationRecord(
                tenant_id=tenant_id,
                merchant_id=created.merchant_id,
                seller_agent_id=seller,
                checkout_session_id=created.checkout_session_id,
                decision_id=created.decision_id,
                operation=operation,
                idempotency_key=created.idempotency_key,
                amount_minor_units=created.amount_minor_units,
                currency=created.currency,
                mandate_hashes=dict(created.mandate_hashes),
            )

        def has(operation: str, phase: str) -> bool:
            return any(e.operation == operation and e.phase == phase for e in entries)

        source = {"source": "webhook", "event_sha256": event_sha256, "status": outcome.status}
        journal = "unchanged"
        if outcome.status in CAPTURED:
            if not has("capture", "succeeded"):
                journal = (
                    "capture_recorded"
                    if await self._journal.record_once(
                        op("capture"), "succeeded", provider_ref=outcome.order_id, details=source
                    )
                    else "unchanged"
                )
            session_outcome = "captured"
        elif outcome.status in AUTHORIZED:
            if has("capture", "succeeded"):
                session_outcome = "captured"
            elif has("capture", "requested"):
                return ReconcileResult("capture_already_requested")
            else:
                amount = created.amount_minor_units
                if outcome.amount_minor_units is not None and outcome.amount_minor_units != amount:
                    await self._journal.blocked(op("capture"), "authorized_amount_mismatch")
                    return ReconcileResult("authorized_amount_mismatch")
                client = self._client()
                if client is None or amount is None:
                    await self._journal.blocked(op("capture"), "plural_credentials_missing")
                    return ReconcileResult("plural_credentials_missing")
                phase, _data = await self._call(
                    client,
                    op("capture"),
                    "PUT",
                    f"/pay/v1/orders/{outcome.order_id}/capture",
                    {
                        "merchant_capture_reference": created.idempotency_key,
                        "capture_amount": {"value": amount, "currency": str(created.currency)},
                    },
                    provider_ref=outcome.order_id,
                    success=lambda data: str(data.get("status") or "").upper() in CAPTURED,
                )
                journal = f"capture_{phase}"
                if phase != "succeeded":
                    return ReconcileResult(journal)
                session_outcome = "captured"
        elif outcome.status in FAILED:
            if not has("create_payment", "failed"):
                journal = (
                    "failure_recorded"
                    if await self._journal.record_once(
                        op("create_payment"), "failed", provider_ref=outcome.order_id, details=source
                    )
                    else "unchanged"
                )
            session_outcome = "failed"
        else:
            return ReconcileResult("pending")
        session_result = await reconcile_execution(
            tenant_id=tenant_id,
            session_id=created.checkout_session_id,
            decision_id=created.decision_id,
            provider_order_ref=outcome.order_id,
            outcome=session_outcome,
            now=datetime.now(UTC),
        )
        return ReconcileResult(journal, session=session_result)

    async def _call(
        self,
        client: PluralApiClient,
        record: Any,
        method: str,
        path: str,
        body: Mapping[str, Any],
        *,
        provider_ref: str | None = None,
        success: Callable[[Mapping[str, Any]], bool] | None = None,
        pending: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Journal, call, journal; returns the phase (``succeeded``, ``failed``, ``unknown``, ``replayed``) and data."""
        if not await self._journal.begin(record):
            return "replayed", {}
        try:
            data = await client.call(
                method, path, body, request_id=request_id(record.idempotency_key, record.operation)
            )
        except PluralRequestError as exc:
            await self._journal.finish(
                record, "failed", provider_ref=provider_ref, details={"http_status": exc.status_code, "code": exc.code}
            )
            return "failed", {}
        except PluralUnavailableError as exc:
            logger.warning("plural_call_outcome_unknown", operation=record.operation, error_type=type(exc).__name__)
            await self._journal.finish(record, "unknown", provider_ref=provider_ref, details={"error": "unreachable"})
            return "unknown", {}
        status = str(data.get("status") or "").upper()
        ref = str(data.get("refund_id") or data.get("order_id") or provider_ref or "") or None
        phase = "succeeded"
        if pending is not None and pending(data):
            phase = "unknown"
        elif success is not None and not success(data):
            phase = "failed"
        await self._journal.finish(record, phase, provider_ref=ref, details={"status": status})
        return phase, data


def _failed(code: str) -> ExecutionResult:
    messages = {
        "payment_declined": "The payment was declined.",
        "payment_pending": "The payment is still pending at the provider; try again later.",
        "afa_evidence_missing": "The UPI AutoPay mandate has not been authorised yet.",
    }
    return ExecutionResult(
        status="failed",
        error_code="payment_failed" if code not in messages else code,
        error_message=messages.get(code, "The payment could not be processed."),
    )


def _amount(value: Any) -> int | None:
    if isinstance(value, Mapping):
        value = value.get("value")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _amount_problem(
    amount: int,
    currency: str | None,
    *,
    ceiling: int,
    allowance_max: int | None,
    allowance_currency: str | None,
    ceiling_reason: str = "amount_exceeds_session_total",
) -> str | None:
    """Fail closed: a positive amount, in the allowance currency, within the ceiling and the allowance."""
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
        return "amount_invalid"
    if allowance_max is None or not allowance_currency:
        return "allowance_missing"
    if not currency or currency.upper() != allowance_currency.upper():
        return "currency_mismatch"
    if amount > ceiling:
        return ceiling_reason
    if amount > allowance_max:
        return "amount_exceeds_allowance"
    return None


# --- webhooks -----------------------------------------------------------------------------------


class PluralWebhookError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PluralWebhookEvent:
    webhook_id: str
    event_type: str
    data: dict[str, Any]
    body_sha256: str


def verify_webhook(
    raw_body: bytes, headers: Mapping[str, str], *, secret: str, now: float | None = None
) -> PluralWebhookEvent:
    """Verify a Plural webhook (signature, timestamp window) and parse it; raises ``PluralWebhookError``."""
    from core.billing.pinelabs_client import plural_signature_valid

    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    webhook_id = lowered.get("webhook-id", "")
    timestamp = lowered.get("webhook-timestamp", "")
    signature = lowered.get("webhook-signature", "")
    if not webhook_id or not timestamp or not signature:
        raise PluralWebhookError("headers_missing")
    if not secret:
        raise PluralWebhookError("secret_missing")
    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise PluralWebhookError("timestamp_invalid") from exc
    if abs((time.time() if now is None else now) - sent_at) > WEBHOOK_TOLERANCE_SECONDS:
        raise PluralWebhookError("timestamp_out_of_window")
    if not plural_signature_valid(secret, raw_body, webhook_id, timestamp, signature):
        raise PluralWebhookError("signature_invalid")
    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise PluralWebhookError("body_invalid") from exc
    if not isinstance(payload, Mapping):
        raise PluralWebhookError("body_invalid")
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    return PluralWebhookEvent(
        webhook_id=webhook_id[:200],
        event_type=str(payload.get("event_type") or payload.get("type") or "")[:80],
        data=dict(data),
        body_sha256=hashlib.sha256(raw_body).hexdigest(),
    )


@dataclass(frozen=True)
class PaymentOutcome:
    """A Plural order event for one of our orders: the provider order, our merchant reference, the status."""

    order_id: str
    merchant_reference: str
    status: str
    amount_minor_units: int | None = None


@dataclass(frozen=True)
class ReconcileResult:
    journal: str
    session: str | None = None


def payment_outcome(event: PluralWebhookEvent) -> PaymentOutcome | None:
    """The order outcome a verified event carries for an order this executor created (``aoc-`` reference)."""
    order = event.data.get("order") if isinstance(event.data.get("order"), Mapping) else event.data
    order_id = str(order.get("order_id") or "")
    reference = str(order.get("merchant_order_reference") or "")
    if not order_id or not reference.startswith("aoc-"):
        return None
    payments = order.get("payments") if isinstance(order.get("payments"), list) else []
    first = payments[0] if payments and isinstance(payments[0], Mapping) else {}
    status = str(order.get("status") or first.get("status") or "").upper()
    if not status:
        return None
    amount = _amount(first.get("payment_amount")) or _amount(order.get("order_amount"))
    return PaymentOutcome(
        order_id=order_id[:120], merchant_reference=reference[:64], status=status, amount_minor_units=amount
    )


def afa_outcome(event: PluralWebhookEvent) -> tuple[str, bool, str | None] | None:
    """``(mandate_reference, authorized, provider_mandate_ref)`` for a mandate event of ours, else None."""
    mandate = event.data.get("mandate") if isinstance(event.data.get("mandate"), Mapping) else event.data
    reference = str(mandate.get("merchant_mandate_reference") or mandate.get("mandate_reference") or "")
    if not reference.startswith("aom-"):
        return None
    status = str(mandate.get("status") or "").upper()
    if status not in MANDATE_AUTHORIZED | MANDATE_FAILED:
        return None
    provider_ref = str(mandate.get("mandate_id") or mandate.get("id") or "") or None
    return reference, status in MANDATE_AUTHORIZED, provider_ref


async def apply_plural_event(
    *, tenant_id: str, raw_body: bytes, headers: Mapping[str, str], secret: str | None = None
) -> dict[str, Any]:
    """Verify a Plural event relayed for this tenant and apply what it proves.

    A UPI AutoPay mandate outcome records the RBI AFA evidence. An order
    outcome for an order this executor created reconciles a pending payment
    or a capture after the buyer's 3-D Secure step into the operations journal
    and the session (``PluralExecutor.reconcile``). Raises ``PluralWebhookError``
    when it does not verify (the caller ledgers the refusal). A verified event
    that carries neither is reported as not applied.
    """
    from core.commerce import executors
    from core.commerce.payment_records import record_afa_outcome

    key = secret if secret is not None else (credentials_from_env() or PluralCredentials("", "", "")).webhook_secret
    event = verify_webhook(raw_body, headers, secret=key)
    outcome = afa_outcome(event)
    if outcome is None:
        payment = payment_outcome(event)
        executor = executors.get_executor(EXECUTOR_NAME)
        if payment is not None and isinstance(executor, PluralExecutor):
            reconciled = await executor.reconcile(tenant_id=tenant_id, outcome=payment, event_sha256=event.body_sha256)
            return {
                "verified": True,
                "applied": reconciled.journal not in ("unknown_order", "pending", "unchanged")
                or reconciled.session in ("completed", "returned_to_ready"),
                "payment": {"journal": reconciled.journal, "session": reconciled.session},
                "event_type": event.event_type,
                "body_sha256": event.body_sha256,
            }
        return {"verified": True, "applied": False, "event_type": event.event_type, "body_sha256": event.body_sha256}
    reference, authorized, provider_ref = outcome
    result = await record_afa_outcome(
        tenant_id=tenant_id,
        reference=reference,
        authorized=authorized,
        provider_mandate_ref=provider_ref,
        event_sha256=event.body_sha256,
    )
    return {
        "verified": True,
        "applied": result.status in ("authorized", "failed"),
        "afa": result.status,
        "event_type": event.event_type,
        "body_sha256": event.body_sha256,
    }


# --- registration -------------------------------------------------------------------------------


def sync_registration(env: Mapping[str, str] | None = None) -> bool:
    """Register the executor while ``PLURAL_PINE_LIVE_EXECUTION_ENABLED=true``; unregister it otherwise."""
    from core.commerce import executors

    current = executors.get_executor(EXECUTOR_NAME)
    if live_execution_enabled(env):
        if not isinstance(current, PluralExecutor):
            executors.register_executor(PluralExecutor())
        return True
    if isinstance(current, PluralExecutor):
        executors.unregister_executor(EXECUTOR_NAME)
    return False
