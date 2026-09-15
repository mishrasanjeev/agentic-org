# SPDX-License-Identifier: Apache-2.0
"""PRD A-8 hand-off on PostgreSQL: case push outbox, signed delivery, retries, dead letters, provider webhooks.

Drives real cases (mock provider, scripted model) and delivers through ``CasePushDispatcher`` with an
in-memory HTTP transport and a controlled clock: the memo reaches the endpoint within 60 seconds of
completion, transient failures retry with backoff, rejections and exhausted retries dead-letter and
replay with the same event id, a failing receiver never loses the case, signing keys rotate, and
inbound provider webhooks are accepted, de-duplicated or treated as untrusted triggers.
"""

from __future__ import annotations

import importlib.util
import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, BaseMessage
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from connectors.framework.verification_provider import ProviderEventType
from connectors.providers.mock import MockConfig, MockProvider
from connectors.providers.mock import webhooks as mock_webhooks
from core.cases import push as case_push
from core.cases.provider_webhooks import receive_provider_webhook
from core.cases.push import CasePushDispatcher, PushSettings, configure_endpoint, verify_signature
from core.cases.runtime import CaseRuntime, investigate_case
from core.cases.store import create_case, get_case
from core.domain_schemas import validate
from core.models.case_push import CasePushOutbox, ProviderWebhookReceipt
from core.test_doubles.scripted_model import final

_DB_URL = os.getenv("AGENTICORG_DB_URL", "")
_SYNC_URL = _DB_URL.replace("postgresql+asyncpg", "postgresql")
_ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
ENDPOINT = "http://receiver.example.com/agenticorg/cases"

pytestmark = pytest.mark.skipif(not _DB_URL, reason="integration tests require AGENTICORG_DB_URL")


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at = self.at + timedelta(seconds=seconds)


class Receiver:
    """In-memory HTTP receiver: answers from a script of status codes (or exceptions), records requests."""

    def __init__(self, *responses: int | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            answer = self.responses.pop(0) if self.responses else 200
            if isinstance(answer, Exception):
                raise answer
            return httpx.Response(answer, json={"ok": answer < 300})

        return httpx.MockTransport(handle)


def _respond(messages: list[BaseMessage]) -> AIMessage:
    context = json.loads(str(messages[-1].content))
    if "rationale for a proposed disposition" in str(messages[0].content):
        return final({"rationale": "The identifier comparisons support the proposed outcome."})
    summaries = [
        {"section_id": s["section_id"], "summary": "Summarised from the findings.", "citations": [0]}
        for s in context["sections"]
        if s["status"] in ("complete", "partial") and s["evidence_count"]
    ]
    return final({"summaries": summaries})


def _load_migration(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _ROOT / "migrations" / "versions" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    import core.models  # noqa: F401 - registers every ORM model
    from core.models.base import BaseModel

    sync_engine = create_engine(_SYNC_URL)
    BaseModel.metadata.create_all(sync_engine)
    with sync_engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        for name in ("v6_z25_governed_cases", "v6_z26_case_push"):
            _load_migration(name).upgrade()
    yield sync_engine
    sync_engine.dispose()


@pytest.fixture(autouse=True)
def fresh_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.database as db_mod

    test_engine = create_async_engine(_DB_URL, poolclass=NullPool)
    monkeypatch.setattr(db_mod, "async_session_factory", async_sessionmaker(test_engine, expire_on_commit=False))


@pytest.fixture
def tenant(engine: Engine) -> str:
    tenant_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (id, name, slug, plan, data_region, settings, byok_kek_resource) "
                "VALUES (:id, :name, :slug, 'enterprise', 'IN', '{}'::jsonb, '')"
            ),
            {"id": tenant_id, "name": f"tenant-{tenant_id[:8]}", "slug": f"tenant-{tenant_id}"},
        )
    return tenant_id


def _runtime(clock: Clock, provider: MockProvider | None = None, **overrides: Any) -> CaseRuntime:
    backend = provider or MockProvider(MockConfig(clock=clock))

    async def enabled(tenant_id: uuid.UUID) -> bool:
        return True

    values: dict[str, Any] = {
        "provider_factory": lambda name: backend,
        "flag": enabled,
        "clock": clock,
        "llm_model": "scripted",
        "require_os_isolation": False,
        "push_kick": lambda tenant_id: None,
    }
    values.update(overrides)
    return CaseRuntime(**values)


async def _configure(tenant: str, clock: Clock) -> str:
    from core.database import get_tenant_session

    async with get_tenant_session(uuid.UUID(tenant)) as session:
        _, key = await configure_endpoint(session, uuid.UUID(tenant), url=ENDPOINT, enabled=True, now=clock())
    assert key is not None
    return key.secret


async def _completed_case(tenant: str, clock: Clock, key: str = "gb-clean-brightwater", **runtime: Any) -> str:
    from core.database import get_tenant_session

    application = MockProvider().fixture(key).application
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        case = await create_case(
            session, tenant_id=tenant, application=application, purpose="aml.cdd.onboarding", provider="mock",
            policy_id="business_onboarding_uk" if key.startswith("gb-") else "business_onboarding_us",
            created_by="user:submitter", now=clock(),
        )  # fmt: skip
        case_ref = case.case_ref
    output = await investigate_case(tenant, case_ref, runtime=_runtime(clock, **runtime), actor="workflow:test")
    assert output["state"] == "awaiting_decision", output
    return case_ref


async def _outbox(tenant: str) -> list[CasePushOutbox]:
    from core.database import get_tenant_session

    async with get_tenant_session(uuid.UUID(tenant)) as session:
        rows = await session.execute(
            select(CasePushOutbox)
            .where(CasePushOutbox.tenant_id == uuid.UUID(tenant))
            .order_by(CasePushOutbox.created_at)
        )
        return list(rows.scalars())


def _dispatcher(receiver: Receiver, clock: Clock, **settings: Any) -> CasePushDispatcher:
    return CasePushDispatcher(transport_factory=receiver.transport, clock=clock, settings=PushSettings(**settings))


# --- outbox and delivery --------------------------------------------------------------------------


async def test_memo_is_pushed_signed_within_60_seconds_of_case_completion(
    engine: Engine, tenant: str, scripted_model: Any
) -> None:
    scripted_model([_respond])
    clock = Clock(T0)
    secret = await _configure(tenant, clock)
    case_ref = await _completed_case(tenant, clock)

    [row] = await _outbox(tenant)
    assert (row.status, row.event_type, row.attempts) == ("pending", "case.completed", 0)
    validate("case_push", row.payload)

    receiver = Receiver(200)
    clock.advance(5)
    report = await _dispatcher(receiver, clock).dispatch_tenant(uuid.UUID(tenant))
    assert (report.delivered, report.retried, report.dead_lettered) == (1, 0, 0)

    [request] = receiver.requests
    body = request.content
    headers = dict(request.headers)
    [key_id] = [part.split(":")[0].removeprefix("v1=") for part in headers["agenticorg-signature"].split(", ")]
    assert verify_signature(headers, body, keys={key_id: secret}, now=int(clock().timestamp())) == ""
    delivered = json.loads(body)
    validate("case_push", delivered)
    assert delivered["case"]["case_id"] == case_ref and delivered["memo"]["case_id"] == case_ref
    assert headers["agenticorg-event-id"] == delivered["event_id"] == str(row.event_id)

    [row] = await _outbox(tenant)
    from core.database import get_tenant_session

    async with get_tenant_session(uuid.UUID(tenant)) as session:
        case = await get_case(session, tenant, case_ref)
    assert row.status == "delivered" and row.delivered_at is not None
    assert (row.delivered_at - case.completed_at).total_seconds() <= 60


async def test_transient_failures_retry_with_backoff_and_still_deliver_within_60_seconds(
    engine: Engine, tenant: str, scripted_model: Any
) -> None:
    scripted_model([_respond])
    clock = Clock(T0)
    await _configure(tenant, clock)
    await _completed_case(tenant, clock)
    receiver = Receiver(503, httpx.ConnectError("refused"), 429, 200)
    dispatcher = _dispatcher(receiver, clock)

    for _ in range(4):
        await dispatcher.dispatch_tenant(uuid.UUID(tenant))
        [row] = await _outbox(tenant)
        if row.status == "delivered":
            break
        assert row.next_attempt_at > clock()
        clock.at = row.next_attempt_at
    [row] = await _outbox(tenant)
    assert row.status == "delivered" and row.attempts == 4
    assert (row.delivered_at - T0).total_seconds() <= 60
    assert len(receiver.requests) == 4
    assert len({r.headers["agenticorg-event-id"] for r in receiver.requests}) == 1


async def test_rejections_and_exhausted_retries_dead_letter_and_replay_keeps_the_event_id(
    engine: Engine, tenant: str, scripted_model: Any
) -> None:
    from core.database import get_tenant_session

    scripted_model([_respond, _respond])
    clock = Clock(T0)
    await _configure(tenant, clock)
    await _completed_case(tenant, clock)

    await _dispatcher(Receiver(400), clock).dispatch_tenant(uuid.UUID(tenant))
    [row] = await _outbox(tenant)
    assert row.status == "dead_lettered" and row.last_error == "endpoint_rejected:http_400"
    event_id = row.event_id

    async with get_tenant_session(uuid.UUID(tenant)) as session:
        [listed] = await case_push.list_dead_letters(session, uuid.UUID(tenant))
        assert listed.id == row.id
        await case_push.replay_dead_letter(session, uuid.UUID(tenant), row.id, actor="user:admin", now=clock())
    receiver = Receiver(200)
    await _dispatcher(receiver, clock).dispatch_tenant(uuid.UUID(tenant))
    [row] = await _outbox(tenant)
    assert (row.status, row.replay_count, row.event_id) == ("delivered", 1, event_id)
    assert receiver.requests[0].headers["agenticorg-event-id"] == str(event_id)

    async with get_tenant_session(uuid.UUID(tenant)) as session:
        with pytest.raises(Exception, match="dead_letter_not_replayable"):
            await case_push.replay_dead_letter(session, uuid.UUID(tenant), row.id, actor="user:admin", now=clock())

    await _completed_case(tenant, clock, "us-clean-hollowbrook")
    exhausting = _dispatcher(Receiver(500, 502), clock, max_attempts=2)
    for _ in range(2):
        await exhausting.dispatch_tenant(uuid.UUID(tenant))
        clock.advance(3600)
    [exhausted] = [r for r in await _outbox(tenant) if r.event_id != event_id]
    assert exhausted.status == "dead_lettered" and exhausted.last_error == "max_attempts_exceeded:http_502"


async def test_a_failing_receiver_or_invalid_payload_never_loses_the_case(
    engine: Engine, tenant: str, scripted_model: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.database import get_tenant_session

    scripted_model([_respond, _respond])
    clock = Clock(T0)
    await _configure(tenant, clock)
    case_ref = await _completed_case(tenant, clock)
    await _dispatcher(Receiver(httpx.ConnectError("down")), clock).dispatch_tenant(uuid.UUID(tenant))
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        case = await get_case(session, tenant, case_ref)
    assert case.state == "awaiting_decision" and case.memo is not None
    [row] = await _outbox(tenant)
    assert row.status == "pending" and row.last_error.startswith("transport_")

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise case_push.PushPayloadError("simulated")

    monkeypatch.setattr(case_push, "build_case_push", broken)
    second = await _completed_case(tenant, clock, "us-clean-hollowbrook")
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        assert (await get_case(session, tenant, second)).state == "awaiting_decision"
    [invalid] = [r for r in await _outbox(tenant) if r.event_id != row.event_id]
    assert invalid.status == "dead_lettered" and invalid.last_error == "payload_invalid"


async def test_no_outbox_rows_without_an_enabled_endpoint(engine: Engine, tenant: str, scripted_model: Any) -> None:
    scripted_model([_respond])
    await _completed_case(tenant, Clock(T0))
    assert await _outbox(tenant) == []


async def test_signing_keys_rotate_are_stored_encrypted_and_retire(
    engine: Engine, tenant: str, scripted_model: Any
) -> None:
    from core.database import get_tenant_session

    scripted_model([_respond])
    clock = Clock(T0)
    first_secret = await _configure(tenant, clock)
    with engine.begin() as conn:
        stored = conn.execute(
            text("SELECT signing_keys_encrypted::text FROM case_push_endpoints WHERE tenant_id = :t"), {"t": tenant}
        ).scalar_one_or_none()
    # RLS hides the row from a session without tenant context; read it with the tenant context instead.
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        endpoint = await case_push._endpoint(session, uuid.UUID(tenant))
        raw = json.dumps(endpoint.signing_keys_encrypted)
        new_key = await case_push.rotate_signing_key(session, uuid.UUID(tenant), now=clock())
    assert first_secret not in raw and "_encrypted" in raw
    assert stored is None or first_secret not in stored

    await _completed_case(tenant, clock)
    receiver = Receiver(200)
    await _dispatcher(receiver, clock).dispatch_tenant(uuid.UUID(tenant))
    request = receiver.requests[0]
    signature = request.headers["agenticorg-signature"]
    assert signature.startswith(f"v1={new_key.key_id}:") and signature.count("v1=") == 2
    now = int(clock().timestamp())
    for key_id, secret in (
        (new_key.key_id, new_key.secret),
        (signature.split(", ")[1][3:].split(":")[0], first_secret),
    ):
        assert verify_signature(dict(request.headers), request.content, keys={key_id: secret}, now=now) == ""

    async with get_tenant_session(uuid.UUID(tenant)) as session:
        assert await case_push.retire_previous_keys(session, uuid.UUID(tenant), now=clock()) == [new_key.key_id]


# --- provider webhooks ----------------------------------------------------------------------------


async def test_provider_webhooks_valid_forged_and_replayed(engine: Engine, tenant: str, scripted_model: Any) -> None:
    from core.database import get_tenant_session

    scripted_model([_respond, _respond])
    clock = Clock(T0)
    provider = MockProvider(MockConfig(clock=clock))
    case_ref = await _completed_case(tenant, clock, provider=provider)
    runtime = _runtime(clock, provider)
    requeried: list[tuple[str, str]] = []

    async def requery(tenant_id: uuid.UUID, ref: str, actor: str) -> None:
        requeried.append((ref, actor))
        await investigate_case(tenant_id, ref, runtime=runtime, actor=actor)

    async def receive(headers: dict[str, str], body: bytes) -> Any:
        return await receive_provider_webhook(
            tenant_id=uuid.UUID(tenant), provider_name="mock", headers=headers, body=body, runtime=runtime,
            requery=requery, now=clock(),
        )  # fmt: skip

    headers, body = provider.emit_event(provider.ref_for("gb-clean-brightwater"), ProviderEventType.BUSINESS_DISSOLVED)
    valid = await receive(headers, body)
    assert valid.outcome == "accepted" and valid.requeried == (case_ref,)
    async with get_tenant_session(uuid.UUID(tenant)) as session:
        case = await get_case(session, tenant, case_ref)
    # Re-investigated from the provider, which now reports the business dissolved.
    assert case.state == "awaiting_decision" and case.policy_result["tier"] == "blocked"
    assert len(case.agent_records) == 2

    replayed = await receive(headers, body)
    assert replayed.outcome == "duplicate" and replayed.requeried == ()

    forged_body = body.replace(b"business.dissolved", b"business.status_changed")
    forged = await receive(headers, forged_body)
    assert forged.outcome == "unverified"
    # The hint in the forged body matches the case, but it was re-queried from a provider event moments ago.
    assert forged.requeried == ()

    stale_headers = mock_webhooks.sign(
        provider.config.webhook_secret,
        body,
        event_id=headers["X-Mock-Event-Id"],
        timestamp=int(clock().timestamp()) - 3600,
    )
    assert (await receive(stale_headers, body)).outcome == "unverified"
    assert (await receive({}, b"not json")).outcome == "unverified"
    assert requeried == [(case_ref, "provider_event:mock")]

    async with get_tenant_session(uuid.UUID(tenant)) as session:
        receipts = (
            await session.execute(
                select(ProviderWebhookReceipt.outcome, ProviderWebhookReceipt.verified)
                .where(ProviderWebhookReceipt.tenant_id == uuid.UUID(tenant))
                .order_by(ProviderWebhookReceipt.received_at)
            )
        ).all()
    assert sorted(r.outcome for r in receipts) == ["accepted", "duplicate", "unverified", "unverified", "unverified"]
    outbox = await _outbox(tenant)
    assert [r.event_type for r in outbox] == []  # no endpoint configured for this tenant


async def test_an_unverified_trigger_requeries_only_after_the_window(
    engine: Engine, tenant: str, scripted_model: Any
) -> None:
    scripted_model([_respond, _respond])
    clock = Clock(T0)
    provider = MockProvider(MockConfig(clock=clock))
    case_ref = await _completed_case(tenant, clock, provider=provider)
    runtime = _runtime(clock, provider)
    calls: list[str] = []

    async def requery(tenant_id: uuid.UUID, ref: str, actor: str) -> None:
        calls.append(ref)
        await investigate_case(tenant_id, ref, runtime=runtime, actor=actor)

    subject = provider.ref_for("gb-clean-brightwater").model_dump(mode="json")
    forged = json.dumps({"subject": subject, "event_type": "business.dissolved", "note": "trust me"}).encode()
    for _ in range(2):
        result = await receive_provider_webhook(
            tenant_id=uuid.UUID(tenant), provider_name="mock", headers={"X-Mock-Signature": "v1=00"}, body=forged,
            runtime=runtime, requery=requery, now=clock(),
        )  # fmt: skip
        assert result.outcome == "unverified"
    assert calls == [case_ref]


async def test_provider_webhook_http_route(client: Any, engine: Engine) -> None:
    from api.main import app
    from api.v1 import governed_cases as routes
    from tests.integration.conftest import TEST_TENANT_ID

    clock = Clock(T0)
    provider = MockProvider(MockConfig(clock=clock))

    def registry(name: str) -> MockProvider:
        if name != "mock":
            raise LookupError(name)
        return provider

    app.dependency_overrides[routes.get_case_runtime] = lambda: _runtime(clock, provider, provider_factory=registry)
    try:
        headers, body = provider.emit_event(
            provider.ref_for("us-clean-hollowbrook"), ProviderEventType.OFFICERS_CHANGED
        )
        url = f"/api/v1/webhooks/providers/{TEST_TENANT_ID}/mock"
        for request_headers, request_body in ((headers, body), (headers, body), ({"X-Mock-Signature": "v1=ff"}, body)):
            response = await client.post(url, content=request_body, headers=request_headers)
            assert response.status_code == 202 and response.json() == {"status": "received"}
        too_large = await client.post(url, content=b"x" * (256 * 1024 + 1), headers=headers)
        assert too_large.status_code == 413
        unknown = await client.post(f"/api/v1/webhooks/providers/{TEST_TENANT_ID}/unknown_provider", content=body)
        assert unknown.status_code == 404 and unknown.json()["error"]["reason"] == "provider_unknown"
    finally:
        app.dependency_overrides.pop(routes.get_case_runtime, None)


async def test_push_endpoint_and_dead_letter_api(client: Any, auth_headers: dict[str, str], engine: Engine) -> None:
    from api.main import app
    from api.v1 import governed_cases as routes

    clock = Clock(T0)
    app.dependency_overrides[routes.get_case_runtime] = lambda: _runtime(clock)
    try:
        rejected = await client.put(
            "/api/v1/case-push/endpoint", json={"url": "ftp://receiver.example.com/x"}, headers=auth_headers
        )
        assert rejected.status_code == 422 and rejected.json()["error"]["reason"] == "endpoint_url_invalid"
        created = await client.put("/api/v1/case-push/endpoint", json={"url": ENDPOINT}, headers=auth_headers)
        assert created.status_code == 200, created.text
        secret = created.json()["signing_key"]["secret"]
        shown = await client.get("/api/v1/case-push/endpoint", headers=auth_headers)
        assert shown.json()["configured"] is True and secret not in shown.text
        rotated = await client.post("/api/v1/case-push/endpoint/rotate-key", headers=auth_headers)
        assert len((await client.get("/api/v1/case-push/endpoint", headers=auth_headers)).json()["key_ids"]) == 2
        assert (
            rotated.json()["signing_key"]["key_id"]
            == (await client.get("/api/v1/case-push/endpoint", headers=auth_headers)).json()["active_key_id"]
        )
        retired = await client.post("/api/v1/case-push/endpoint/retire-previous-keys", headers=auth_headers)
        assert len(retired.json()["key_ids"]) == 1
        listed = await client.get("/api/v1/case-push/dead-letters", headers=auth_headers)
        assert listed.status_code == 200 and listed.json() == {"dead_letters": []}
        missing = await client.post(f"/api/v1/case-push/dead-letters/{uuid.uuid4()}/replay", headers=auth_headers)
        assert missing.status_code == 404
    finally:
        app.dependency_overrides.pop(routes.get_case_runtime, None)


async def test_push_retrieval_and_dead_letter_replay_over_the_api(
    client: Any, auth_headers: dict[str, str], engine: Engine, scripted_model: Any
) -> None:
    from api.main import app
    from api.v1 import governed_cases as routes
    from tests.integration.conftest import TEST_TENANT_ID

    scripted_model([_respond])
    clock = Clock(T0)
    runtime = _runtime(clock)
    app.dependency_overrides[routes.get_case_runtime] = lambda: runtime
    try:
        assert (await client.get("/api/v1/case-push/endpoint", headers=auth_headers)).status_code == 200
        assert (
            await client.put("/api/v1/case-push/endpoint", json={"url": ENDPOINT}, headers=auth_headers)
        ).status_code == 200
        application = MockProvider().fixture("us-clean-quillfeather").application
        case_ref = (
            await client.post("/api/v1/governed-cases", json={"application": application}, headers=auth_headers)
        ).json()["case_ref"]
        assert (
            await client.post(f"/api/v1/governed-cases/{case_ref}/investigate", headers=auth_headers)
        ).status_code == 202

        payload = await client.get(f"/api/v1/governed-cases/{case_ref}/push-payload", headers=auth_headers)
        assert payload.status_code == 200, payload.text
        validate("case_push", payload.json())
        deliveries = (
            await client.get(f"/api/v1/governed-cases/{case_ref}/push-deliveries", headers=auth_headers)
        ).json()
        [event] = [d for d in deliveries["deliveries"] if d["event_type"] == "case.completed"]
        assert event["event_id"] == payload.json()["event_id"] and event["status"] == "pending"

        await _dispatcher(Receiver(410), clock).dispatch_tenant(uuid.UUID(TEST_TENANT_ID))
        [dead] = [
            d
            for d in (await client.get("/api/v1/case-push/dead-letters", headers=auth_headers)).json()["dead_letters"]
            if d["event_id"] == event["event_id"]
        ]
        assert dead["last_error"] == "endpoint_rejected:http_410"
        replayed = await client.post(f"/api/v1/case-push/dead-letters/{dead['outbox_id']}/replay", headers=auth_headers)
        assert (
            replayed.status_code == 200
            and replayed.json()["status"] == "pending"
            and replayed.json()["replay_count"] == 1
        )
        again = await client.post(f"/api/v1/case-push/dead-letters/{dead['outbox_id']}/replay", headers=auth_headers)
        assert again.status_code == 409
    finally:
        app.dependency_overrides.pop(routes.get_case_runtime, None)
