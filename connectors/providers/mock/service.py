# SPDX-License-Identifier: Apache-2.0
"""The mock provider as a separate HTTP service, so the provider seam is exercised over the network.

Run it with ``uvicorn connectors.providers.mock.service:app``. It serves one :class:`MockProvider`
configured from ``AGENTICORG_MOCK_PROVIDER_*`` and refuses to start outside local, development and
test environments. The fault-injection, event and reset endpoints under ``/v1/admin`` answer only
when ``AGENTICORG_MOCK_PROVIDER_ADMIN`` is true.

Request bodies are validated strictly; an invalid body is ``400 invalid_query``. Errors are
``{"error": {"reason", "message", "capability", "retry_after_seconds"}}``.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from connectors.framework.verification_provider import (
    INCLUDE_UNTRUSTED_TEXT,
    BusinessQuery,
    BusinessRef,
    BusinessSubject,
    Capability,
    CapabilityNotSupported,
    Deadline,
    InvalidQuery,
    MonitorHandle,
    MonitorOptions,
    NotFound,
    Pending,
    PersonSubject,
    ProviderAuthenticationFailed,
    ProviderError,
    ProviderEventType,
    ProviderRateLimited,
    ProviderResponseInvalid,
    ProviderTimeout,
    ProviderUnavailable,
    ScreenOptions,
    VerificationHandle,
    VerifyOptions,
)
from connectors.providers.mock.config import FaultKind, MockProviderSettings
from connectors.providers.mock.http_client import DEADLINE_HEADER
from connectors.providers.mock.provider import MockProvider
from core.config import is_relaxed_env

DeadlineHeader = Annotated[str | None, Header(alias=DEADLINE_HEADER)]
MAX_DEADLINE_MS = 120_000
DEFAULT_DEADLINE_MS = 30_000

_STATUS: dict[type[ProviderError], int] = {
    InvalidQuery: 400,
    ProviderAuthenticationFailed: 401,
    NotFound: 404,
    ProviderRateLimited: 429,
    CapabilityNotSupported: 501,
    ProviderResponseInvalid: 502,
    ProviderUnavailable: 503,
    ProviderTimeout: 504,
}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResolveBody(_Body):
    query: BusinessQuery


class RefBody(_Body):
    ref: BusinessRef


class VerifyBody(_Body):
    ref: BusinessRef
    opts: VerifyOptions


class VerificationResultBody(_Body):
    handle: VerificationHandle


class ScreenPersonBody(_Body):
    subject: PersonSubject
    opts: ScreenOptions


class ScreenBusinessBody(_Body):
    subject: BusinessSubject
    opts: ScreenOptions


class MonitorBody(_Body):
    ref: BusinessRef
    opts: MonitorOptions


class MonitorAlertsBody(_Body):
    handle: MonitorHandle


class FaultBody(_Body):
    kind: FaultKind
    capability: Capability | None = None
    times: Annotated[int, Field(ge=1, le=1000)] = 1
    delay_seconds: Annotated[float, Field(ge=0, le=600)] = 0.0


class EventBody(_Body):
    ref: BusinessRef
    event_type: ProviderEventType


class ServiceRefusedError(RuntimeError):
    pass


def _error(
    status: int, reason: str, message: str, capability: str | None = None, retry_after: float | None = None
) -> JSONResponse:
    headers = {"Retry-After": str(max(1, round(retry_after)))} if retry_after is not None else None
    content = {
        "error": {"reason": reason, "message": message, "capability": capability, "retry_after_seconds": retry_after}
    }
    return JSONResponse(status_code=status, content=content, headers=headers)


def _deadline(raw: str | None) -> Deadline:
    if raw is None:
        return Deadline.after(DEFAULT_DEADLINE_MS / 1000)
    if not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= MAX_DEADLINE_MS:
        raise InvalidQuery("mock", f"{DEADLINE_HEADER} must be 1..{MAX_DEADLINE_MS} milliseconds")
    return Deadline.after(int(raw) / 1000)


def create_app(provider: MockProvider | None = None, *, admin: bool | None = None) -> FastAPI:
    environment = os.getenv("AGENTICORG_ENV", "")
    if not is_relaxed_env(environment):
        raise ServiceRefusedError(
            "the mock provider service only runs in local, development and test environments "
            f"(AGENTICORG_ENV={environment!r})"
        )
    settings = MockProviderSettings()
    mock = provider or MockProvider(settings.to_config())
    admin_enabled = settings.admin if admin is None else admin

    app = FastAPI(title="AgenticOrg mock verification provider", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.provider = mock

    @app.exception_handler(ProviderError)
    async def _provider_error(_: Request, exc: ProviderError) -> JSONResponse:
        status = next((code for kind, code in _STATUS.items() if isinstance(exc, kind)), 500)
        retry_after = exc.retry_after_seconds if isinstance(exc, ProviderRateLimited) else None
        capability = exc.capability.value if exc.capability else None
        return _error(status, exc.reason, exc.message or exc.reason, capability, retry_after)

    @app.exception_handler(RequestValidationError)
    async def _invalid_body(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(400, InvalidQuery.reason, f"invalid request body ({len(exc.errors())} errors)")

    @app.get("/healthz")
    async def health() -> dict[str, Any]:
        return {"alive": True, "provider": mock.name}

    @app.get("/v1/capabilities")
    async def capabilities() -> dict[str, Any]:
        return {"name": mock.name, "capabilities": sorted(c.value for c in mock.capabilities)}

    @app.post("/v1/businesses/resolve")
    async def resolve(body: ResolveBody, deadline_ms: DeadlineHeader = None) -> dict[str, Any]:
        candidates = await mock.resolve_business(body.query, deadline=_deadline(deadline_ms))
        return {"candidates": [c.model_dump(mode="json") for c in candidates]}

    @app.post("/v1/verifications")
    async def verify(body: VerifyBody, deadline_ms: DeadlineHeader = None) -> dict[str, Any]:
        handle = await mock.verify_business(body.ref, body.opts, deadline=_deadline(deadline_ms))
        return handle.model_dump(mode="json")

    @app.post("/v1/verifications/result")
    async def verification_result(body: VerificationResultBody, deadline_ms: DeadlineHeader = None) -> dict[str, Any]:
        result = await mock.verification_result(body.handle, deadline=_deadline(deadline_ms))
        if isinstance(result, Pending):
            return {"status": "pending", "pending": result.model_dump(mode="json")}
        return {"status": "complete", "verification": result.model_dump(mode="json")}

    @app.post("/v1/ownership")
    async def ownership(body: RefBody, deadline_ms: DeadlineHeader = None) -> dict[str, Any]:
        return (await mock.ownership(body.ref, deadline=_deadline(deadline_ms))).model_dump(mode="json")

    @app.post("/v1/screenings/person")
    async def screen_person(body: ScreenPersonBody, deadline_ms: DeadlineHeader = None) -> dict[str, Any]:
        result = await mock.screen_person(body.subject, body.opts, deadline=_deadline(deadline_ms))
        return result.model_dump(mode="json")

    @app.post("/v1/screenings/business")
    async def screen_business(body: ScreenBusinessBody, deadline_ms: DeadlineHeader = None) -> dict[str, Any]:
        result = await mock.screen_business(body.subject, body.opts, deadline=_deadline(deadline_ms))
        return result.model_dump(mode="json")

    @app.post("/v1/web-presence")
    async def web_presence(body: RefBody, deadline_ms: DeadlineHeader = None) -> dict[str, Any]:
        presence = await mock.web_presence(body.ref, deadline=_deadline(deadline_ms))
        # The provider's own client needs the page content, so it is included explicitly; the
        # client re-wraps it as UntrustedText, which redacts it again on any later serialisation.
        return presence.model_dump(mode="json", context=INCLUDE_UNTRUSTED_TEXT)

    @app.post("/v1/monitors")
    async def monitor_enroll(body: MonitorBody, deadline_ms: DeadlineHeader = None) -> dict[str, Any]:
        return (await mock.monitor_enroll(body.ref, body.opts, deadline=_deadline(deadline_ms))).model_dump(mode="json")

    @app.post("/v1/monitors/alerts")
    async def monitor_alerts(body: MonitorAlertsBody, deadline_ms: DeadlineHeader = None) -> dict[str, Any]:
        alerts = await mock.monitor_result(body.handle, deadline=_deadline(deadline_ms))
        return {"alerts": [a.model_dump(mode="json") for a in alerts]}

    def _admin_disabled() -> JSONResponse | None:
        if admin_enabled:
            return None
        return _error(404, NotFound.reason, "admin endpoints are disabled")

    @app.post("/v1/admin/faults", response_model=None)
    async def inject_fault(body: FaultBody) -> JSONResponse | dict[str, Any]:
        if (refused := _admin_disabled()) is not None:
            return refused
        mock.inject_fault(body.kind, capability=body.capability, times=body.times, delay_seconds=body.delay_seconds)
        return {"accepted": True}

    @app.post("/v1/admin/events", response_model=None)
    async def emit_event(body: EventBody) -> JSONResponse | dict[str, Any]:
        if (refused := _admin_disabled()) is not None:
            return refused
        headers, payload = mock.emit_event(body.ref, body.event_type)
        return {"headers": headers, "body": payload.decode("utf-8")}

    @app.post("/v1/admin/reset", response_model=None)
    async def reset() -> JSONResponse | dict[str, Any]:
        if (refused := _admin_disabled()) is not None:
            return refused
        mock.reset()
        return {"accepted": True}

    return app


def __getattr__(name: str) -> Any:
    # ``uvicorn connectors.providers.mock.service:app`` builds the app on first access, so importing
    # this module (for tests, or by the providers registry) never starts or configures a service.
    if name == "app":
        return create_app()
    raise AttributeError(name)


@contextmanager
def serve_in_thread(
    provider: MockProvider | None = None, *, host: str = "127.0.0.1", port: int = 0, admin: bool = True
) -> Iterator[str]:
    """Run the service on a real socket in a background thread; yields its base URL.

    For tests and local tooling. ``port=0`` picks a free port.
    """
    import socket  # noqa: PLC0415
    import threading  # noqa: PLC0415

    import uvicorn  # noqa: PLC0415

    app = create_app(provider, admin=admin)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((host, port))
    bound_port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="warning", lifespan="off", access_log=False, timeout_graceful_shutdown=2)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        while not server.started:
            if not thread.is_alive() or time.monotonic() - started > 15:
                raise ServiceRefusedError("the mock provider service did not start")
            time.sleep(0.02)
        yield f"http://{host}:{bound_port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
