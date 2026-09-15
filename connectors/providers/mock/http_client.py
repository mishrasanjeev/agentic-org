# SPDX-License-Identifier: Apache-2.0
"""``MockHttpProvider``: the ``mock`` provider reached over HTTP (see ``service.py``).

Every call carries the remaining deadline in ``X-Request-Deadline-Ms`` and is bounded by it
locally. Errors come back as ``{"error": {"reason": ...}}`` and are raised as the matching
:class:`ProviderError`; transport failures are ``ProviderUnavailable``, a client-side timeout is
``ProviderTimeout``, and a body that is not a valid domain value is ``ProviderResponseInvalid``.
Webhooks are verified locally with the shared secret; no network call is made.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from connectors.framework.verification_provider import (
    BusinessCandidate,
    BusinessQuery,
    BusinessRef,
    BusinessSubject,
    BusinessVerification,
    Capability,
    CapabilityNotSupported,
    Deadline,
    InvalidQuery,
    MonitorAlert,
    MonitorHandle,
    MonitorOptions,
    NotFound,
    OwnershipGraph,
    Pending,
    PersonSubject,
    ProviderAuthenticationFailed,
    ProviderError,
    ProviderEvent,
    ProviderEventType,
    ProviderRateLimited,
    ProviderResponseInvalid,
    ProviderTimeout,
    ProviderUnavailable,
    ScreeningResult,
    ScreenOptions,
    VerificationHandle,
    VerificationProvider,
    VerifyOptions,
    WebPresence,
)
from connectors.providers.mock import webhooks
from connectors.providers.mock.config import FaultKind, MockConfig
from connectors.providers.mock.provider import PROVIDER_NAME

DEADLINE_HEADER = "X-Request-Deadline-Ms"

_ERRORS: dict[str, type[ProviderError]] = {
    error.reason: error
    for error in (
        InvalidQuery,
        NotFound,
        ProviderAuthenticationFailed,
        ProviderRateLimited,
        ProviderResponseInvalid,
        ProviderTimeout,
        ProviderUnavailable,
    )
}

_CANDIDATES = TypeAdapter(list[BusinessCandidate])
_ALERTS = TypeAdapter(list[MonitorAlert])


class MockHttpProvider(VerificationProvider):
    name = PROVIDER_NAME

    def __init__(
        self,
        base_url: str,
        *,
        config: MockConfig | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an http or https URL")
        self.base_url = base_url.rstrip("/")
        self.config = config or MockConfig()
        self.capabilities = self.config.capabilities
        self._transport = transport

    # --- transport ---------------------------------------------------------------------------------

    async def _post(
        self, capability: Capability | None, path: str, payload: Mapping[str, Any], deadline: Deadline
    ) -> Any:
        async with deadline.enforce(self.name, capability):
            remaining = deadline.remaining()
            headers = {DEADLINE_HEADER: str(max(1, math.floor(remaining * 1000)))}
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url, timeout=remaining, transport=self._transport
                ) as client:
                    response = await client.post(path, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                raise ProviderTimeout(self.name, "request timed out", capability=capability) from exc
            except httpx.TransportError as exc:
                raise ProviderUnavailable(
                    self.name, f"transport error: {type(exc).__name__}", capability=capability
                ) from exc
        return self._decode(response, capability)

    def _decode(self, response: httpx.Response, capability: Capability | None) -> Any:
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.is_success:
            if body is None:
                raise ProviderResponseInvalid(self.name, "response body is not JSON", capability=capability)
            return body
        error = body.get("error") if isinstance(body, dict) else None
        reason = error.get("reason") if isinstance(error, dict) else None
        message = "service returned an error"
        if reason == CapabilityNotSupported.reason and capability is not None:
            raise CapabilityNotSupported(self.name, capability, message)
        if reason == ProviderRateLimited.reason:
            retry_after = error.get("retry_after_seconds") if isinstance(error, dict) else None
            raise ProviderRateLimited(
                self.name,
                message,
                capability=capability,
                retry_after_seconds=float(retry_after) if isinstance(retry_after, int | float) else None,
            )
        known = _ERRORS.get(reason) if isinstance(reason, str) else None
        if known is not None:
            raise known(self.name, message, capability=capability)
        if response.status_code >= 500:
            raise ProviderUnavailable(self.name, f"HTTP {response.status_code}", capability=capability)
        raise ProviderResponseInvalid(self.name, f"unexpected HTTP {response.status_code}", capability=capability)

    def _parse[M: BaseModel](self, model: type[M], value: Any, capability: Capability) -> M:
        try:
            return model.model_validate(value)
        except ValidationError as exc:
            raise ProviderResponseInvalid(
                self.name, "response is not a valid domain value", capability=capability
            ) from exc

    @staticmethod
    def _json(value: BaseModel) -> Any:
        return value.model_dump(mode="json")

    # --- interface ---------------------------------------------------------------------------------

    async def resolve_business(self, q: BusinessQuery, *, deadline: Deadline) -> list[BusinessCandidate]:
        self.require(Capability.RESOLVE)
        body = await self._post(Capability.RESOLVE, "/v1/businesses/resolve", {"query": self._json(q)}, deadline)
        try:
            return _CANDIDATES.validate_python(body.get("candidates") if isinstance(body, dict) else None)
        except ValidationError as exc:
            raise ProviderResponseInvalid(self.name, "candidates are invalid", capability=Capability.RESOLVE) from exc

    async def verify_business(self, ref: BusinessRef, opts: VerifyOptions, *, deadline: Deadline) -> VerificationHandle:
        self.require(Capability.VERIFY)
        payload = {"ref": self._json(ref), "opts": self._json(opts)}
        body = await self._post(Capability.VERIFY, "/v1/verifications", payload, deadline)
        return self._parse(VerificationHandle, body, Capability.VERIFY)

    async def verification_result(self, h: VerificationHandle, *, deadline: Deadline) -> BusinessVerification | Pending:
        self.require(Capability.VERIFY)
        body = await self._post(Capability.VERIFY, "/v1/verifications/result", {"handle": self._json(h)}, deadline)
        status = body.get("status") if isinstance(body, dict) else None
        if status == "pending":
            return self._parse(Pending, body.get("pending"), Capability.VERIFY)
        if status == "complete":
            return self._parse(BusinessVerification, body.get("verification"), Capability.VERIFY)
        raise ProviderResponseInvalid(self.name, "unknown verification status", capability=Capability.VERIFY)

    async def ownership(self, ref: BusinessRef, *, deadline: Deadline) -> OwnershipGraph:
        self.require(Capability.OWNERSHIP)
        body = await self._post(Capability.OWNERSHIP, "/v1/ownership", {"ref": self._json(ref)}, deadline)
        return self._parse(OwnershipGraph, body, Capability.OWNERSHIP)

    async def screen_person(self, s: PersonSubject, opts: ScreenOptions, *, deadline: Deadline) -> ScreeningResult:
        self.require(Capability.SCREEN_PERSON)
        payload = {"subject": self._json(s), "opts": self._json(opts)}
        body = await self._post(Capability.SCREEN_PERSON, "/v1/screenings/person", payload, deadline)
        return self._parse(ScreeningResult, body, Capability.SCREEN_PERSON)

    async def screen_business(self, s: BusinessSubject, opts: ScreenOptions, *, deadline: Deadline) -> ScreeningResult:
        self.require(Capability.SCREEN_BUSINESS)
        payload = {"subject": self._json(s), "opts": self._json(opts)}
        body = await self._post(Capability.SCREEN_BUSINESS, "/v1/screenings/business", payload, deadline)
        return self._parse(ScreeningResult, body, Capability.SCREEN_BUSINESS)

    async def web_presence(self, ref: BusinessRef, *, deadline: Deadline) -> WebPresence:
        self.require(Capability.WEB_PRESENCE)
        body = await self._post(Capability.WEB_PRESENCE, "/v1/web-presence", {"ref": self._json(ref)}, deadline)
        return self._parse(WebPresence, body, Capability.WEB_PRESENCE)

    async def monitor_enroll(self, ref: BusinessRef, opts: MonitorOptions, *, deadline: Deadline) -> MonitorHandle:
        self.require(Capability.MONITOR)
        payload = {"ref": self._json(ref), "opts": self._json(opts)}
        body = await self._post(Capability.MONITOR, "/v1/monitors", payload, deadline)
        return self._parse(MonitorHandle, body, Capability.MONITOR)

    async def monitor_result(self, h: MonitorHandle, *, deadline: Deadline) -> list[MonitorAlert]:
        self.require(Capability.MONITOR)
        body = await self._post(Capability.MONITOR, "/v1/monitors/alerts", {"handle": self._json(h)}, deadline)
        try:
            return _ALERTS.validate_python(body.get("alerts") if isinstance(body, dict) else None)
        except ValidationError as exc:
            raise ProviderResponseInvalid(self.name, "alerts are invalid", capability=Capability.MONITOR) from exc

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> ProviderEvent | None:
        return webhooks.verify(
            headers,
            body,
            secrets=(self.config.webhook_secret,),
            provider=self.name,
            now=int(self.config.clock().timestamp()),
            tolerance_seconds=self.config.webhook_tolerance_seconds,
        )

    # --- service controls (need AGENTICORG_MOCK_PROVIDER_ADMIN on the service) ----------------------

    async def inject_fault(
        self,
        kind: FaultKind,
        *,
        capability: Capability | None = None,
        times: int = 1,
        delay_seconds: float = 0.0,
        deadline: Deadline,
    ) -> None:
        payload = {
            "kind": kind.value,
            "capability": capability.value if capability else None,
            "times": times,
            "delay_seconds": delay_seconds,
        }
        await self._post(None, "/v1/admin/faults", payload, deadline)

    async def emit_event(
        self, ref: BusinessRef, event_type: ProviderEventType, *, deadline: Deadline
    ) -> tuple[dict[str, str], bytes]:
        body = await self._post(
            None, "/v1/admin/events", {"ref": self._json(ref), "event_type": event_type.value}, deadline
        )
        if not (isinstance(body, dict) and isinstance(body.get("headers"), dict) and isinstance(body.get("body"), str)):
            raise ProviderResponseInvalid(self.name, "event delivery is invalid")
        return {str(k): str(v) for k, v in body["headers"].items()}, body["body"].encode("utf-8")

    async def reset(self, *, deadline: Deadline) -> None:
        await self._post(None, "/v1/admin/reset", {}, deadline)
