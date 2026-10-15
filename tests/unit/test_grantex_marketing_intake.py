# SPDX-License-Identifier: Apache-2.0
"""Grantex enquiry routing must not widen tenant, email or agent authority."""
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from api.middleware.marketing_cors import MarketingCORSMiddleware
from api.v1 import demo
from core import demo_delivery


def enquiry(**changes):
    return demo.DemoRequest(name="Synthetic owner", email="buyer@example.org", product="grantex",
                            message="A question\nSecond line", **changes)


@pytest.mark.parametrize("changes", [
    {"product": "other"}, {"message": ""}, {"message": " "},
    {"message": "x" * 5001}, {"message": "private\x00text"}, {"website": "filled"},
])
def test_bounded_enquiry_refuses_invalid_or_bot_input(changes):
    payload = enquiry().model_dump() | changes
    with pytest.raises(ValidationError):
        demo.DemoRequest(**payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,durable", [(False, False), (False, True), (True, False)])
async def test_grantex_requires_both_explicit_rollout_flags(monkeypatch, enabled, durable):
    monkeypatch.setenv("AGENTICORG_GRANTEX_ENQUIRIES_ENABLED", str(enabled))
    monkeypatch.setenv("AGENTICORG_DEMO_DURABLE_DELIVERY_ENABLED", str(durable))
    request = Request({"type": "http", "client": ("198.51.100.15", 1), "headers": []})
    with patch.object(demo, "async_session_factory") as database:
        with pytest.raises(HTTPException) as error:
            await demo.submit_demo_request(enquiry(), request, MagicMock())
    assert error.value.status_code == 503
    database.assert_not_called()


def test_branding_and_notification_recipient_are_server_owned(monkeypatch):
    monkeypatch.setenv("AGENTICORG_DEMO_NOTIFY_TO", "marketing@example.org")
    payload = enquiry().model_dump() | {"message": "<img src=x onerror=alert(1)>\nQuestion",
                                        "recipient": "outsider@example.org", "sender": "outsider@example.org"}
    body = demo.DemoRequest(**payload)
    with patch.object(demo, "send_email", return_value=True) as send:
        assert demo._send_email_notification(body)
        assert send.call_args.args[0] == "marketing@example.org"
        assert send.call_args.args[1].startswith("Grantex Demo Request")
        assert "&lt;img" in send.call_args.args[2]
        assert "<img" not in send.call_args.args[2]
        assert demo._send_trial_confirmation(body)
        assert send.call_args.args[0] == "buyer@example.org"
        assert send.call_args.args[1] == "Grantex demo request received"
        assert "AgenticOrg" not in send.call_args.args[2]


@pytest.mark.asyncio
async def test_grantex_worker_never_runs_a_sales_agent_even_on_pending_legacy_state(monkeypatch):
    job = uuid4()
    claims = iter([None, None, {"payload_encrypted": "synthetic-ciphertext", "lead_id": uuid4()}])
    monkeypatch.setattr(demo_delivery, "_claim", AsyncMock(side_effect=lambda *args: next(claims)))
    finish = AsyncMock()
    monkeypatch.setattr(demo_delivery, "_finish", finish)
    monkeypatch.setattr(demo_delivery, "decrypt_for_tenant", lambda _: json.dumps(enquiry().model_dump()))
    with patch("api.v1.sales._run_sales_agent_on_lead", new_callable=AsyncMock) as agent:
        assert await demo_delivery.dispatch_one(demo_delivery.MARKETING_TENANT, job) == {"agent": "skipped"}
    agent.assert_not_called()
    finish.assert_awaited_once_with(demo_delivery.MARKETING_TENANT, job, "agent", "skipped", None)


@pytest.mark.asyncio
async def test_prepare_marks_grantex_agent_step_skipped(monkeypatch):
    session = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(demo_delivery, "get_tenant_session", lambda _: context)
    monkeypatch.setattr(demo_delivery, "resolve_tenant_kek", AsyncMock(return_value=""))
    monkeypatch.setattr(demo_delivery, "encrypt_with_kek", lambda *args: "synthetic-ciphertext")
    await demo_delivery.prepare(enquiry().model_dump(), str(uuid4()))
    assert session.execute.call_args.args[1]["agent_status"] == "skipped"
    assert session.execute.call_args.args[1]["tenant"] == demo_delivery.MARKETING_TENANT


def cors_app():
    app = FastAPI()

    @app.post("/api/v1/demo-request")
    @app.post("/api/v1/private-example")
    async def echo():
        return {"status": "received"}

    app.add_middleware(MarketingCORSMiddleware, allow_origins=["https://agenticorg.ai"],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    return TestClient(app)


def test_grantex_cors_is_exact_public_path_post_only_and_never_credentialed():
    with cors_app() as client:
        origin = {"Origin": "https://grantex.dev"}
        preflight = origin | {"Access-Control-Request-Method": "POST",
                              "Access-Control-Request-Headers": "content-type"}
        response = client.options("/api/v1/demo-request", headers=preflight)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://grantex.dev"
        assert "access-control-allow-credentials" not in response.headers
        response = client.post("/api/v1/demo-request", headers=origin, json={})
        assert response.headers["access-control-allow-origin"] == "https://grantex.dev"
        assert "access-control-allow-credentials" not in response.headers
        for path, headers in [
            ("/api/v1/private-example", preflight),
            ("/api/v1/demo-request", preflight | {"Origin": "https://grantex.dev.attacker.example"}),
            ("/api/v1/demo-request", preflight | {"Access-Control-Request-Method": "GET"}),
        ]:
            assert client.options(path, headers=headers).status_code == 400
        response = client.post("/api/v1/private-example", headers={"Origin": "https://agenticorg.ai"})
        assert response.headers["access-control-allow-credentials"] == "true"
