# SPDX-License-Identifier: Apache-2.0
"""Email webhooks take the tenant from the URL, never from the payload.

The shared email webhook routes (``/api/v1/webhooks/email/{sendgrid,mailchimp,moengage}``) read the
tenant from fields of the signed body (SendGrid ``tenant:`` categories, ``custom_args.tenant_id``,
Mailchimp ``tenant_id`` form fields, MoEngage ``tenant_id``), and each provider has one key for the
whole deployment. A validly signed event could therefore name any tenant and resume that tenant's
``wait_for_event`` steps.

The per-tenant routes (``/api/v1/webhooks/email/{provider}/{tenant_id}/{path_token}``) bind the
tenant to the URL with an HMAC path token, still verify the provider signature, and refuse an event
whose payload names a different tenant. ``AGENTICORG_WEBHOOKS_TENANT_BOUND_PATHS`` makes the shared
routes refuse (409) any delivery with an event that names a tenant; off, they behave as before.

Every delivery here is really signed: SendGrid with an ECDSA P-256 key, Mailchimp with HMAC-SHA1
over URL and fields, MoEngage with HMAC-SHA256 over the body.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from workflows.event_waits import InMemoryWorkflowEventWaitRepository, WorkflowEventWaitStore

TENANT_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
TENANT_B = uuid.UUID("22222222-2222-4222-8222-222222222222")
EMAIL = "lead-01@example.com"
CAMPAIGN = "camp-spring-7"
PROVIDERS = ("sendgrid", "mailchimp", "moengage")

# Placeholders, not real provider secrets.
MAILCHIMP_KEY = "test-mailchimp-webhook-key"
MOENGAGE_KEY = "test-moengage-webhook-key"


# ── App, keys and the durable wait store ─────────────────────────────


@pytest.fixture(scope="module")
def app():
    from api.main import app as _app

    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    _app.router.lifespan_context = _noop_lifespan
    return _app


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_auth_state_redis(monkeypatch):
    """The route rate limiter takes its in-memory path instead of reaching a machine Redis (FINDINGS A-59)."""
    from core import auth_state

    monkeypatch.setattr(auth_state, "_redis", None)
    monkeypatch.setattr(auth_state, "_get_redis", AsyncMock(return_value=None))


@pytest.fixture
def sendgrid_key(monkeypatch):
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_b64 = base64.b64encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode()
    # Real signatures only: the development bypass must not be what lets these through.
    monkeypatch.delenv("AGENTICORG_WEBHOOK_ALLOW_UNSIGNED", raising=False)
    monkeypatch.setenv("SENDGRID_WEBHOOK_KEY", public_b64)
    monkeypatch.setenv("MAILCHIMP_WEBHOOK_KEY", MAILCHIMP_KEY)
    monkeypatch.setenv("MOENGAGE_WEBHOOK_KEY", MOENGAGE_KEY)
    return private_key


@pytest.fixture
def waits(sendgrid_key):
    """An in-memory durable wait store, a Redis double and the resume task, observed."""
    redis = AsyncMock()
    store = WorkflowEventWaitStore(repository=InMemoryWorkflowEventWaitRepository(), redis=AsyncMock())
    delay = MagicMock()
    with (
        patch("api.v1.webhooks._get_redis", return_value=redis),
        patch("api.v1.webhooks._workflow_event_wait_store", return_value=store),
        patch("core.tasks.workflow_tasks.resume_workflow_wait.delay", delay),
    ):
        yield SimpleNamespace(store=store, redis=redis, resumed=delay, sendgrid_key=sendgrid_key)


@pytest.fixture
def bound_paths(monkeypatch):
    monkeypatch.setattr("core.config.settings.webhooks_tenant_bound_paths", True)


async def _register(store: WorkflowEventWaitStore, run_id: str, tenant_id: uuid.UUID | None) -> None:
    await store.register(
        engine_run_id=run_id,
        step_id="wait-open",
        event_type="email.opened",
        match_criteria={"campaign_id": CAMPAIGN, "email": EMAIL},
        timeout_at=datetime.now(UTC) + timedelta(hours=1),
        tenant_id=tenant_id,
    )


def _status(store: WorkflowEventWaitStore, run_id: str) -> str:
    return store.repository.records[(run_id, "wait-open")].status  # type: ignore[attr-defined]


def _resumed_runs(waits: SimpleNamespace) -> list[str]:
    return [call.args[0] for call in waits.resumed.call_args_list]


def _binding_count(provider: str, outcome: str) -> float:
    import core.email_webhooks  # noqa: F401 - registers the counter

    value = REGISTRY.get_sample_value(
        "agenticorg_email_webhook_tenant_binding_total", {"provider": provider, "outcome": outcome}
    )
    return value or 0.0


# ── Signed deliveries ────────────────────────────────────────────────


def _sendgrid_request(private_key, events: list[dict[str, Any]]) -> dict[str, Any]:
    body = json.dumps(events).encode()
    timestamp = "1700000000"
    der = private_key.sign(timestamp.encode() + body, ec.ECDSA(hashes.SHA256()))
    return {
        "content": body,
        "headers": {
            "Content-Type": "application/json",
            "X-Twilio-Email-Event-Webhook-Signature": base64.b64encode(der).decode(),
            "X-Twilio-Email-Event-Webhook-Timestamp": timestamp,
        },
    }


def _mailchimp_request(url: str, form: dict[str, str]) -> dict[str, Any]:
    signed = url + "".join(key + form[key] for key in sorted(form))
    signature = base64.b64encode(hmac.new(MAILCHIMP_KEY.encode(), signed.encode(), hashlib.sha1).digest()).decode()
    return {"data": form, "headers": {"X-Mandrill-Signature": signature}}


def _moengage_request(payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    signature = hmac.new(MOENGAGE_KEY.encode(), body, hashlib.sha256).hexdigest()
    return {"content": body, "headers": {"Content-Type": "application/json", "X-MoEngage-Signature": signature}}


def _sendgrid_event(**tenant_fields: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "email": EMAIL,
        "event": "open",
        "sg_message_id": "msg-0001",
        "timestamp": 1700000000,
        "category": [CAMPAIGN],
    }
    event.update(tenant_fields)
    return event


def _deliver(client: TestClient, waits: SimpleNamespace, provider: str, path: str, tenant_claim: str | None):
    """POST one signed ``open`` event for EMAIL on CAMPAIGN, naming ``tenant_claim`` if given."""
    url = f"http://testserver{path}"
    if provider == "sendgrid":
        fields = {"custom_args": {"tenant_id": tenant_claim}} if tenant_claim else {}
        return client.post(path, **_sendgrid_request(waits.sendgrid_key, [_sendgrid_event(**fields)]))
    if provider == "mailchimp":
        form = {"type": "open", "fired_at": "2026-09-01 10:00:00", "data[email]": EMAIL, "data[id]": CAMPAIGN}
        if tenant_claim:
            form["tenant_id"] = tenant_claim
        return client.post(path, **_mailchimp_request(url, form))
    payload: dict[str, Any] = {
        "event_type": "EMAIL_OPEN",
        "email": EMAIL,
        "campaign_id": CAMPAIGN,
        "timestamp": "2026-09-01T10:00:00Z",
    }
    if tenant_claim:
        payload["tenant_id"] = tenant_claim
    return client.post(path, **_moengage_request(payload))


def _legacy_path(provider: str) -> str:
    return f"/api/v1/webhooks/email/{provider}"


def _bound_path(tenant_id: uuid.UUID, provider: str) -> str:
    from core.email_webhooks import webhook_path

    return webhook_path(tenant_id, provider)


# ── The attack: a signed event naming another tenant ─────────────────


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_a_signed_event_cannot_resume_another_tenants_wait(client, waits, provider) -> None:
    """Tenant A's URL, a valid signature, a payload naming tenant B: nobody's wait resumes."""
    await _register(waits.store, "run-a", TENANT_A)
    await _register(waits.store, "run-b", TENANT_B)
    mismatches = _binding_count(provider, "tenant_mismatch")

    response = _deliver(client, waits, provider, _bound_path(TENANT_A, provider), str(TENANT_B))

    assert response.status_code == 200, response.text
    assert response.json()["refused"] == 1
    assert _binding_count(provider, "tenant_mismatch") == mismatches + 1
    assert _resumed_runs(waits) == []
    assert _status(waits.store, "run-b") == "waiting"
    # Refused, not re-labelled: the event does not resume the bound tenant's wait either.
    assert _status(waits.store, "run-a") == "waiting"
    waits.redis.hset.assert_not_called()


@pytest.mark.parametrize(
    "tenant_fields",
    [
        pytest.param({"category": [CAMPAIGN, f"tenant:{TENANT_B}"]}, id="category"),
        pytest.param({"custom_args": {"agenticorg:tenant_id": str(TENANT_B)}}, id="custom_args-prefixed"),
        pytest.param({"tenant_id": str(TENANT_B)}, id="event-field"),
        pytest.param({"agenticorg:tenant_id": str(TENANT_B)}, id="event-prefixed-field"),
        pytest.param(
            {"category": [CAMPAIGN, f"tenant:{TENANT_A}"], "custom_args": {"tenant_id": str(TENANT_B)}},
            id="one-field-names-the-bound-tenant-another-does-not",
        ),
        pytest.param({"custom_args": {"tenant_id": "not-a-tenant-id"}}, id="not-a-uuid"),
    ],
)
async def test_every_tenant_field_of_a_sendgrid_event_is_checked(client, waits, tenant_fields) -> None:
    await _register(waits.store, "run-a", TENANT_A)
    await _register(waits.store, "run-b", TENANT_B)

    response = client.post(
        _bound_path(TENANT_A, "sendgrid"), **_sendgrid_request(waits.sendgrid_key, [_sendgrid_event(**tenant_fields)])
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "processed": 0, "refused": 1}
    assert _resumed_runs(waits) == []
    assert _status(waits.store, "run-a") == "waiting" and _status(waits.store, "run-b") == "waiting"


async def test_a_sendgrid_batch_refuses_only_the_event_naming_another_tenant(client, waits) -> None:
    """One foreign-tagged event must not hold back the rest of the tenant's batch."""
    await _register(waits.store, "run-a", TENANT_A)
    await _register(waits.store, "run-b", TENANT_B)
    events = [_sendgrid_event(custom_args={"tenant_id": str(TENANT_B)}), _sendgrid_event()]

    response = client.post(_bound_path(TENANT_A, "sendgrid"), **_sendgrid_request(waits.sendgrid_key, events))

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "processed": 1, "refused": 1}
    assert _resumed_runs(waits) == ["run-a"]
    assert _status(waits.store, "run-b") == "waiting"


# ── The bound route resumes the bound tenant's wait ──────────────────


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("claim", [None, str(TENANT_A), str(TENANT_A).upper()], ids=["no-claim", "same", "same-upper"])
async def test_bound_route_resumes_the_bound_tenants_wait(client, waits, provider, claim) -> None:
    await _register(waits.store, "run-a", TENANT_A)
    await _register(waits.store, "run-b", TENANT_B)
    await _register(waits.store, "run-none", None)
    bound = _binding_count(provider, "bound")

    response = _deliver(client, waits, provider, _bound_path(TENANT_A, provider), claim)

    assert response.status_code == 200, response.text
    assert response.json()["refused"] == 0
    assert _binding_count(provider, "bound") == bound + 1
    assert _resumed_runs(waits) == ["run-a"]
    assert _status(waits.store, "run-a") == "matched"
    assert _status(waits.store, "run-b") == "waiting"
    # A wait registered without a tenant is never reachable from a tenant's URL.
    assert _status(waits.store, "run-none") == "waiting"
    matched_event = waits.store.repository.records[("run-a", "wait-open")].matched_event
    assert matched_event["tenant_id"] == str(TENANT_A)


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_the_bound_route_still_verifies_the_provider_signature(client, waits, provider) -> None:
    await _register(waits.store, "run-a", TENANT_A)
    path = _bound_path(TENANT_A, provider)
    signed = {
        "sendgrid": lambda: _sendgrid_request(waits.sendgrid_key, [_sendgrid_event()]),
        "mailchimp": lambda: _mailchimp_request(
            "http://testserver" + path, {"type": "open", "data[email]": EMAIL, "data[id]": CAMPAIGN}
        ),
        "moengage": lambda: _moengage_request({"event_type": "EMAIL_OPEN", "email": EMAIL, "campaign_id": CAMPAIGN}),
    }[provider]()
    header = {
        "sendgrid": "X-Twilio-Email-Event-Webhook-Signature",
        "mailchimp": "X-Mandrill-Signature",
        "moengage": "X-MoEngage-Signature",
    }[provider]
    forged = {**signed, "headers": {**signed["headers"], header: "AAAA"}}

    response = client.post(path, **forged)

    assert response.status_code == 403
    assert _resumed_runs(waits) == []
    assert _status(waits.store, "run-a") == "waiting"


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_a_path_token_for_another_tenant_or_purpose_is_not_found(client, waits, provider) -> None:
    from core.cases.provider_webhooks import webhook_path_token as case_inbox_token

    await _register(waits.store, "run-a", TENANT_A)
    await _register(waits.store, "run-b", TENANT_B)
    token_b = _bound_path(TENANT_B, provider).rsplit("/", 1)[1]
    other_provider = next(p for p in PROVIDERS if p != provider)
    token_other_provider = _bound_path(TENANT_A, other_provider).rsplit("/", 1)[1]
    candidates = [
        f"/api/v1/webhooks/email/{provider}/{TENANT_A}/{token_b}",
        f"/api/v1/webhooks/email/{provider}/{TENANT_A}/{token_other_provider}",
        # A governed-case provider inbox token is derived under a different label.
        f"/api/v1/webhooks/email/{provider}/{TENANT_A}/{case_inbox_token(TENANT_A, provider)}",
        f"/api/v1/webhooks/email/{provider}/{TENANT_A}/{'0' * 32}",
    ]
    unbound = _binding_count(provider, "unbound")
    for path in candidates:
        response = _deliver(client, waits, provider, path, None)
        assert response.status_code == 404, (path, response.text)
    assert _binding_count(provider, "unbound") == unbound + len(candidates)

    assert _resumed_runs(waits) == []
    waits.redis.hset.assert_not_called()


def test_the_path_token_is_an_hmac_of_the_application_secret_under_its_own_label() -> None:
    """Pinned: changing the derivation silently changes every tenant's configured URL."""
    from core.config import settings
    from core.email_webhooks import webhook_path, webhook_path_token

    expected = (
        hmac.new(settings.secret_key.encode(), f"email-webhook:v1:{TENANT_A}:sendgrid".encode(), hashlib.sha256)
        .digest()[:16]
        .hex()
    )
    assert webhook_path_token(TENANT_A, "sendgrid") == expected
    assert webhook_path(TENANT_A, "sendgrid") == f"/api/v1/webhooks/email/sendgrid/{TENANT_A}/{expected}"
    assert webhook_path_token(TENANT_B, "sendgrid") != expected


# ── The shared routes ────────────────────────────────────────────────


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_legacy_route_unchanged_with_the_setting_off(client, waits, provider) -> None:
    """Default off: the shared route still resumes the wait of the tenant the payload names."""
    from core.config import settings

    assert settings.webhooks_tenant_bound_paths is False
    await _register(waits.store, "run-a", TENANT_A)
    await _register(waits.store, "run-b", TENANT_B)
    named = _binding_count(provider, "shared_path_tenant_named")

    response = _deliver(client, waits, provider, _legacy_path(provider), str(TENANT_B))

    assert response.status_code == 200, response.text
    assert "refused" not in response.json()
    assert _resumed_runs(waits) == ["run-b"]
    assert _status(waits.store, "run-a") == "waiting"
    # What switching the setting on would refuse is counted, so the switch can be timed.
    assert _binding_count(provider, "shared_path_tenant_named") == named + 1


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_legacy_route_refuses_cross_tenant_resume_with_the_setting_on(
    client, waits, bound_paths, provider
) -> None:
    await _register(waits.store, "run-a", TENANT_A)
    await _register(waits.store, "run-b", TENANT_B)
    refused = _binding_count(provider, "shared_path_refused")

    response = _deliver(client, waits, provider, _legacy_path(provider), str(TENANT_B))

    assert response.status_code == 409, response.text
    assert "tenant" in response.json()["detail"]
    assert _binding_count(provider, "shared_path_refused") == refused + 1
    assert _resumed_runs(waits) == []
    assert _status(waits.store, "run-a") == "waiting" and _status(waits.store, "run-b") == "waiting"
    # Refused, not half-processed: nothing is recorded, so the provider's redelivery is the only copy.
    waits.redis.hset.assert_not_called()


async def test_legacy_sendgrid_batch_is_refused_whole_with_the_setting_on(client, waits, bound_paths) -> None:
    await _register(waits.store, "run-none", None)
    await _register(waits.store, "run-b", TENANT_B)
    events = [_sendgrid_event(), _sendgrid_event(category=[CAMPAIGN, f"tenant:{TENANT_B}"])]

    response = client.post(_legacy_path("sendgrid"), **_sendgrid_request(waits.sendgrid_key, events))

    assert response.status_code == 409, response.text
    assert _resumed_runs(waits) == []
    assert _status(waits.store, "run-none") == "waiting" and _status(waits.store, "run-b") == "waiting"
    waits.redis.hset.assert_not_called()


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_legacy_route_still_accepts_events_that_name_no_tenant_with_the_setting_on(
    client, waits, bound_paths, provider
) -> None:
    """An event naming no tenant can only match a wait registered without one, as before."""
    await _register(waits.store, "run-none", None)
    await _register(waits.store, "run-a", TENANT_A)

    response = _deliver(client, waits, provider, _legacy_path(provider), None)

    assert response.status_code == 200, response.text
    assert _resumed_runs(waits) == ["run-none"]
    assert _status(waits.store, "run-a") == "waiting"


# ── Reading the per-tenant URLs ──────────────────────────────────────


def _inbox_app(state: dict[str, Any], human_admin: bool):
    from fastapi import FastAPI, Request

    from api.deps import ActiveHumanAdmin, get_active_human_admin
    from api.v1 import webhooks

    inbox_app = FastAPI()

    @inbox_app.middleware("http")
    async def _authenticate(request: Request, call_next):
        for key, value in state.items():
            setattr(request.state, key, value)
        return await call_next(request)

    inbox_app.include_router(webhooks.router, prefix="/api/v1")
    if human_admin:
        inbox_app.dependency_overrides[get_active_human_admin] = lambda: ActiveHumanAdmin(
            user_id=uuid.uuid4(), tenant_id=uuid.UUID(state["tenant_id"]), email="admin@example.com", role="admin"
        )
    return inbox_app


def test_a_tenant_admin_reads_the_tenants_own_webhook_urls() -> None:
    from core.email_webhooks import webhook_path

    state = {"tenant_id": str(TENANT_A), "scopes": ["agenticorg:admin"], "claims": {"sub": "admin@example.com"}}
    response = TestClient(_inbox_app(state, human_admin=True)).get("/api/v1/email-webhook-inbox")

    assert response.status_code == 200, response.text
    assert response.json() == {"inboxes": [{"provider": p, "path": webhook_path(TENANT_A, p)} for p in PROVIDERS]}
    assert all(str(TENANT_B) not in entry["path"] for entry in response.json()["inboxes"])


def test_the_webhook_urls_need_the_admin_scope() -> None:
    state = {"tenant_id": str(TENANT_A), "scopes": ["agents:read"], "claims": {"sub": "user@example.com"}}
    response = TestClient(_inbox_app(state, human_admin=True)).get("/api/v1/email-webhook-inbox")
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("auth_mode", "claims"),
    [("api_key", {"sub": "apikey:ops"}), ("grantex", {"sub": "agent", "grantex:grant_id": "grant-1"})],
)
def test_the_webhook_urls_are_not_readable_with_an_api_key_or_agent_token(auth_mode, claims) -> None:
    """The path is a credential for the tenant, so a machine credential holding the admin scope is refused."""
    state = {"tenant_id": str(TENANT_A), "scopes": ["agenticorg:admin"], "claims": claims, "auth_mode": auth_mode}
    response = TestClient(_inbox_app(state, human_admin=False)).get("/api/v1/email-webhook-inbox")
    assert response.status_code == 403
