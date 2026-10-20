# SPDX-License-Identifier: Apache-2.0
"""Replay the lead receipt, SMTP formatting and protected spend failures."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_outbox_ciphertext_is_in_the_key_retirement_scan():
    from core.crypto.verify_all import _SCANNERS, KeyRef, _collect_key_refs

    label = "demo_delivery_jobs.payload_encrypted"
    assert (label, "core.models.demo_delivery:DemoDeliveryJob:payload_encrypted") in _SCANNERS
    refs = {}
    _collect_key_refs(refs, label, ['{"kek":"synthetic-kek","wrapped_dek":"synthetic-key"}'])
    assert refs[label] == {KeyRef(kind="envelope", ref="synthetic-kek")}


@pytest.mark.parametrize("host,password", [
    ("smtp.example.org", " leading space remains "),
    ("smtp.gmail.com", "not-a-google-app-password"),
])
def test_unrecognized_or_non_google_password_is_not_rewritten(monkeypatch, host, password):
    from core.email import send_email

    monkeypatch.setenv("AGENTICORG_ENV", "production")
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_MAIL", "")
    monkeypatch.setenv("AGENTICORG_SMTP_HOST", host)
    monkeypatch.setenv("AGENTICORG_SMTP_LOGIN", "sender@example.org")
    monkeypatch.setenv("AGENTICORG_GMAIL_APP_PASSWORD", password)
    with patch("core.email.validate_email_domain", return_value=(True, "OK")), \
            patch("core.email.smtplib.SMTP_SSL") as connect:
        assert send_email("buyer@example.org", "Receipt", "<p>Received</p>")
        transport = connect.return_value.__enter__.return_value
    if host == "smtp.gmail.com":
        assert transport.password == password
    else:
        transport.login.assert_called_once_with("sender@example.org", password)


def test_authentication_failure_is_not_sent_and_never_logs_credentials(monkeypatch, caplog):
    import smtplib

    from core.email import send_email

    monkeypatch.setenv("AGENTICORG_ENV", "production")
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_MAIL", "")
    monkeypatch.setenv("AGENTICORG_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("AGENTICORG_SMTP_LOGIN", "sender@example.org")
    monkeypatch.setenv("AGENTICORG_GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    with patch("core.email.validate_email_domain", return_value=(True, "OK")), \
            patch("core.email.smtplib.SMTP_SSL") as connect:
        transport = connect.return_value.__enter__.return_value
        transport.auth.side_effect = smtplib.SMTPAuthenticationError(535, b"synthetic-private-auth-detail")
        assert send_email("buyer@example.org", "Receipt", "<p>Received</p>") is False
        transport.send_message.assert_not_called()
    assert "535" in caplog.text
    assert "synthetic-private-auth-detail" not in caplog.text
    assert "abcdefghijklmnop" not in caplog.text


def test_google_app_password_display_spaces_are_not_authentication_bytes(monkeypatch):
    from core.email import send_email

    monkeypatch.setenv("AGENTICORG_ENV", "production")
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_MAIL", "")
    monkeypatch.setenv("AGENTICORG_SMTP_LOGIN", "sender@example.org")
    monkeypatch.setenv("AGENTICORG_SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("AGENTICORG_GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    transport = MagicMock()
    with (
        patch("core.email.validate_email_domain", return_value=(True, "OK")),
        patch("core.email.smtplib.SMTP_SSL") as connect,
    ):
        connect.return_value.__enter__.return_value = transport
        assert send_email("buyer@example.org", "Receipt", "<p>Received</p>")
    assert transport.user == "sender@example.org"
    assert transport.password == "abcdefghijklmnop"
    transport.auth.assert_called_once_with("LOGIN", transport.auth_login, initial_response_ok=False)
    transport.login.assert_not_called()


@pytest.mark.asyncio
async def test_platform_spend_reads_only_the_bounded_aggregate_function(monkeypatch):
    from core.llm.router import _todays_gemini_spend_usd

    session = AsyncMock()
    result = MagicMock()
    result.scalar_one.return_value = 4.25
    session.execute.return_value = result
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("core.database.async_session_factory", factory)
    assert await _todays_gemini_spend_usd() == 4.25
    statements = [str(call.args[0]) for call in session.execute.call_args_list]
    assert statements == ["SELECT public.gemini_daily_platform_spend()"]


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -1])
@pytest.mark.asyncio
async def test_missing_or_invalid_spend_refuses_model_call(monkeypatch, value):
    from core.llm.router import DailyBudgetExceeded, _todays_gemini_spend_usd

    session = AsyncMock()
    result = MagicMock()
    result.scalar_one.return_value = value
    session.execute.return_value = result
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("core.database.async_session_factory", factory)
    with pytest.raises(DailyBudgetExceeded, match="lookup unavailable"):
        await _todays_gemini_spend_usd()


@pytest.mark.parametrize("field,value", [
    ("name", "  "), ("name", "Buyer\r\nBcc: outsider@example.org"),
    ("email", "buyer@example.org,outsider@example.org"),
    ("email", "buyer@example.org\r\nBcc: outsider@example.org"),
    ("company", "x" * 256),
])
def test_contact_validation_refuses_header_injection_and_unbounded_fields(field, value):
    from pydantic import ValidationError

    from api.v1.demo import DemoRequest

    payload = {"name": "Synthetic owner", "email": "owner@example.org", field: value}
    with pytest.raises(ValidationError):
        DemoRequest(**payload)


@pytest.mark.asyncio
async def test_broker_failure_leaves_job_pending_for_recovery(monkeypatch):
    from uuid import uuid4

    from core import demo_delivery

    publish = MagicMock(side_effect=ConnectionError("broker unavailable"))
    monkeypatch.setattr("core.tasks.demo_tasks.dispatch.apply_async", publish)
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = uuid4()
    session.execute.return_value = result
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(demo_delivery, "get_tenant_session", lambda _: factory())
    assert await demo_delivery.enqueue(uuid4()) is False
    publish.assert_called_once()
    assert publish.call_args.kwargs["expires"] == 300


@pytest.mark.asyncio
@pytest.mark.parametrize("durable", [False, True])
async def test_receipt_contract_is_gated_and_durable_delivery_never_claimed_early(monkeypatch, durable):
    from uuid import uuid4

    from starlette.requests import Request

    from api.v1 import demo

    monkeypatch.setenv("AGENTICORG_DEMO_DURABLE_DELIVERY_ENABLED", str(durable).lower())
    monkeypatch.setattr(demo.demo_delivery, "prepare", AsyncMock(return_value=uuid4()))
    monkeypatch.setattr(demo.demo_delivery, "enqueue", AsyncMock(return_value=True))
    session = AsyncMock()
    session.add = MagicMock()
    result = MagicMock()
    result.fetchone.return_value = None
    session.execute.return_value = result
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(demo, "get_platform_session", factory)
    monkeypatch.setattr(demo, "get_tenant_session", lambda _: factory())
    monkeypatch.setattr(demo.auth_state, "check_window_rate", AsyncMock(return_value=False))
    request = Request({"type": "http", "client": ("198.51.100.15", 1), "headers": []})
    response = await demo.submit_demo_request(
        demo.DemoRequest(name="Owner-only test", email="owner@example.org"), request, MagicMock(),
    )
    assert response["status"] == "received"
    if not durable:
        assert set(response) == {"status", "message", "lead_id", "agent_triggered", "email"}
        assert response["email"] == {"internal_notification_sent": True, "requester_confirmation_sent": True}
        assert response["agent_triggered"] is True
        assert response["message"] == "We'll be in touch within 2 minutes."
        return
    assert response["email"]["internal_notification_sent"] is False
    assert response["email"]["requester_confirmation_sent"] is False
    assert response["agent_triggered"] is False
    assert "2 minutes" not in response["message"]


@pytest.mark.parametrize("status", ["failed", "hitl_triggered", "halted"])
@pytest.mark.asyncio
async def test_non_completed_sales_result_cannot_send_or_update_contact_history(monkeypatch, status):
    from types import SimpleNamespace
    from uuid import uuid4

    from api.v1 import sales

    tenant, lead_id = uuid4(), uuid4()
    lead = SimpleNamespace(id=lead_id)
    agent = SimpleNamespace(id=uuid4(), agent_type="sales_agent", authorized_tools=[],
                            prompt_variables={}, hitl_condition="", output_schema=None, system_prompt_text="test")
    lead_result, agent_result = MagicMock(), MagicMock()
    lead_result.scalar_one_or_none.return_value = lead
    agent_result.scalar_one_or_none.return_value = agent
    session = AsyncMock()
    session.add = MagicMock()
    session.execute.side_effect = [lead_result, agent_result]
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(sales, "get_tenant_session", lambda _: factory())
    monkeypatch.setattr(sales, "_lead_to_dict", lambda _: {})
    instance = MagicMock()
    instance.execute = AsyncMock(return_value=SimpleNamespace(status=status, output={"lead_stage": "contacted"}))
    monkeypatch.setattr(sales.AgentRegistry, "create_from_config", lambda _: instance)
    with patch("core.email.send_email") as send:
        response = await sales._run_sales_agent_on_lead(str(tenant), str(lead_id))
    assert response["status"] == status
    assert session.execute.await_count == 2
    session.add.assert_not_called()
    send.assert_not_called()
