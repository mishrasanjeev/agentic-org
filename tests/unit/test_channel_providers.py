# SPDX-License-Identifier: Apache-2.0
"""Provider-shaped callbacks: authentication, receiver scope, media and outbound failure boundaries."""

import hashlib
import hmac
import json
import logging
from urllib.parse import urlencode

import httpx
import pytest

from core.channels.providers import CATALOG, ChannelError, decode_messages, send_reply, validate_credentials


def signed(secret, data, prefix=""):
    body = json.dumps(data).encode()
    signature = prefix + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, signature


@pytest.mark.parametrize("provider", ["messenger", "whatsapp"])
def test_meta_signature_and_receiver(provider):
    credentials = {"app_secret": "synthetic-app-secret", "receiver_id": "10001"}
    if provider == "messenger":
        data = {
            "entry": [
                {
                    "id": "10001",
                    "messaging": [
                        {
                            "sender": {"id": "20001"},
                            "recipient": {"id": "10001"},
                            "message": {"mid": "event-1", "text": "Review invoice"},
                        }
                    ],
                }
            ]
        }
    else:
        data = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "10001"},
                                "messages": [
                                    {
                                        "id": "event-1",
                                        "from": "20001",
                                        "type": "text",
                                        "text": {"body": "Review invoice"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
    body, signature = signed(credentials["app_secret"], data, "sha256=")
    messages = decode_messages(provider, credentials, {"x-hub-signature-256": signature}, body)
    assert messages[0].sender == "20001"
    assert messages[0].text == "Review invoice"
    with pytest.raises(ChannelError, match="invalid_signature"):
        decode_messages(provider, credentials, {"x-hub-signature-256": signature}, body + b" ")
    credentials["receiver_id"] = "99999"
    with pytest.raises(ChannelError, match="receiver_mismatch"):
        decode_messages(provider, credentials, {"x-hub-signature-256": signature}, body)


def test_viber_text_signature_and_status_callbacks():
    credentials = {"access_token": "synthetic-bot-token"}
    body, signature = signed(
        credentials["access_token"],
        {
            "event": "message",
            "message_token": 1234,
            "sender": {"id": "sender-1"},
            "message": {"type": "text", "text": "Start workflow"},
        },
    )
    assert decode_messages("viber", credentials, {"x-viber-content-signature": signature}, body)[0].event_id == "1234"
    body, signature = signed(credentials["access_token"], {"event": "webhook", "timestamp": 1000})
    assert decode_messages("viber", credentials, {"x-viber-content-signature": signature}, body) == []
    with pytest.raises(ChannelError, match="invalid_signature"):
        decode_messages("viber", credentials, {}, body)


@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
def test_telegram_groups_cannot_inherit_user_identity(chat_type):
    body = json.dumps(
        {
            "update_id": 11,
            "message": {"text": "Start workflow", "from": {"id": 100}, "chat": {"id": -100, "type": chat_type}},
        }
    ).encode()
    assert (
        decode_messages(
            "telegram", {"webhook_secret": "test-secret"}, {"x-telegram-bot-api-secret-token": "test-secret"}, body
        )
        == []
    )


def test_telegram_private_message_and_secret_required():
    body = json.dumps(
        {
            "update_id": 11,
            "message": {"text": "Start workflow", "from": {"id": 100}, "chat": {"id": 100, "type": "private"}},
        }
    ).encode()
    credentials = {"webhook_secret": "synthetic-secret"}
    assert (
        decode_messages("telegram", credentials, {"x-telegram-bot-api-secret-token": "synthetic-secret"}, body)[
            0
        ].sender
        == "100"
    )
    with pytest.raises(ChannelError, match="invalid_signature"):
        decode_messages("telegram", credentials, {}, body)


def test_signed_gateway_binds_timestamp_to_body_and_rejects_replay_age():
    credentials = {"webhook_secret": "synthetic-signing-secret"}
    body = json.dumps(
        {"event_id": "evt-1", "sender_id": "sender-1", "text": "Process request", "tenant_id": "attacker"}
    ).encode()
    timestamp = "1000000"
    signature = hmac.new(
        credentials["webhook_secret"].encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    headers = {"x-agenticorg-timestamp": timestamp, "x-agenticorg-signature": signature}
    message = decode_messages("signed_webhook", credentials, headers, body, now=1000001)[0]
    assert message.text == "Process request"
    assert not hasattr(message, "tenant_id")
    with pytest.raises(ChannelError, match="stale_signature"):
        decode_messages("signed_webhook", credentials, headers, body, now=1001000)
    with pytest.raises(ChannelError, match="invalid_signature"):
        decode_messages("signed_webhook", credentials, headers, body + b" ", now=1000001)


def test_mailgun_routes_do_not_grant_sender_authority():
    credentials = {"signing_key": "synthetic-mail-key", "receiver_id": "inbox@example.test"}
    timestamp, token = "1000000", "synthetic-token"
    signature = hmac.new(credentials["signing_key"].encode(), (timestamp + token).encode(), hashlib.sha256).hexdigest()
    body = urlencode(
        {
            "timestamp": timestamp,
            "token": token,
            "signature": signature,
            "sender": "sender@example.test",
            "recipient": "inbox@example.test",
            "stripped-text": "Review request",
        }
    ).encode()
    assert decode_messages("mailgun", credentials, {}, body, now=1000001)[0].text == "Review request"
    assert CATALOG["mailgun"]["identity_link"] is False
    assert CATALOG["mailgun"]["reply"] is False
    with pytest.raises(ChannelError, match="stale_signature"):
        decode_messages("mailgun", credentials, {}, body, now=1001000)


@pytest.mark.parametrize("credentials", [{}, {"webhook_secret": ""}, {"webhook_secret": "ok", "tenant_id": "injected"}])
def test_credentials_are_complete_and_never_accept_scope_fields(credentials):
    with pytest.raises(ChannelError):
        validate_credentials("signed_webhook", credentials)


@pytest.mark.parametrize("timestamp", ["NaN", "Infinity", "-Infinity"])
def test_signed_nan_and_infinite_timestamps_cannot_bypass_age(timestamp):
    secret = "synthetic-signing-secret"
    body = b'{"event_id":"event","sender_id":"sender","text":"Request"}'
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    with pytest.raises(ChannelError, match="invalid_timestamp"):
        decode_messages(
            "signed_webhook",
            {"webhook_secret": secret},
            {
                "x-agenticorg-timestamp": timestamp,
                "x-agenticorg-signature": signature,
            },
            body,
        )


def test_non_ascii_signature_is_rejected_not_a_server_error():
    with pytest.raises(ChannelError, match="invalid_signature"):
        decode_messages("viber", {"access_token": "synthetic"}, {"x-viber-content-signature": "\u00e9"}, b"{}")


def test_gateway_secret_cannot_be_short():
    with pytest.raises(ChannelError, match="at_least_24"):
        validate_credentials("signed_webhook", {"webhook_secret": "short"})


def test_telegram_url_token_is_removed_from_httpx_logs(caplog):
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info("HTTP Request: POST %s", "https://api.telegram.org/bot123:private/sendMessage")
    assert "123:private" not in caplog.text
    assert "[redacted]" in caplog.text


@pytest.mark.asyncio
async def test_reply_timeout_is_unconfirmed_without_secret_disclosure(monkeypatch):
    class TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("secret-in-provider-url")

    monkeypatch.setattr("core.channels.providers.httpx.AsyncClient", lambda **kw: TimeoutClient())
    with pytest.raises(ChannelError) as caught:
        await send_reply("telegram", {"access_token": "123:test-token"}, "100", "Completed")
    assert str(caught.value) == "provider_reply_unconfirmed"
    assert "test-token" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "result", "accepted"),
    [
        ("telegram", {"ok": True}, True),
        ("telegram", [], False),
        ("viber", {"status": 0}, True),
        ("viber", {"status": 1}, False),
        ("messenger", {"message_id": "accepted-message"}, True),
        ("messenger", {}, False),
        ("whatsapp", {"messages": [{"id": "accepted-message"}]}, True),
        ("whatsapp", {"messages": ["invalid"]}, False),
    ],
)
async def test_reply_requires_native_acceptance_marker(monkeypatch, provider, result, accepted):
    class ReplyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            assert url.startswith("https://")
            return httpx.Response(200, json=result)

    monkeypatch.setattr("core.channels.providers.httpx.AsyncClient", lambda **kw: ReplyClient())
    credentials = {"access_token": "123:synthetic", "receiver_id": "10001", "api_version": "v24.0"}
    if accepted:
        await send_reply(provider, credentials, "20001", "Task status: completed")
    else:
        with pytest.raises(ChannelError, match="provider_reply_unconfirmed"):
            await send_reply(provider, credentials, "20001", "Task status: completed")
