# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Real HTTP/DB/worker test on an isolated local Docker stack; no provider delivery."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from urllib.parse import urlsplit

import httpx
import pytest

BASE = os.getenv("AGENTICORG_E2E_BASE_URL", "").rstrip("/")
# These tests sign up disposable tenants, so they run only against a local stack. A remote
# origin (the post-deploy production run sets the same variable) skips them; the asserts in
# each test stay as a second guard.
LOCAL_HOSTS = {"127.0.0.1", "localhost", "api"}
pytestmark = [
    pytest.mark.skipif(not BASE, reason="requires local Docker origin"),
    pytest.mark.skipif(
        bool(BASE) and urlsplit(BASE).hostname not in LOCAL_HOSTS,
        reason="signs up disposable tenants; local Docker origin only",
    ),
    pytest.mark.timeout(240),
]


def checked(response, code=200):
    assert response.status_code == code, f"HTTP {response.status_code}, expected {code}"
    return response.json()


def test_signed_channel_to_durable_workflow_and_revocation():
    # This signs up disposable tenants. Never run it against production or a remote host.
    assert urlsplit(BASE).hostname in LOCAL_HOSTS
    run = uuid.uuid4().hex[:12]
    with httpx.Client(base_url=BASE + "/api/v1", timeout=60) as client:
        deadline = time.monotonic() + 60
        while True:
            try:
                if client.get("/health").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() >= deadline:
                pytest.fail("Local API did not become healthy within 60 seconds")
            time.sleep(2)
        signup = checked(
            client.post(
                "/auth/signup",
                json={
                    "org_name": f"Channel test {run}",
                    "admin_name": "Channel Test Admin",
                    "admin_email": f"channel-{run}@example.test",
                    "password": secrets.token_urlsafe(24) + "aA9!",
                },
            ),
            201,
        )
        client.cookies.clear()
        client.headers["Authorization"] = "Bearer " + signup["access_token"]
        workflow = checked(
            client.post(
                "/workflows",
                json={
                    "name": f"Channel condition {run}",
                    "definition": {
                        "steps": [
                            {"id": "check", "name": "Check request", "type": "condition", "condition": "true"},
                        ]
                    },
                },
            ),
            201,
        )
        secret = secrets.token_hex(32)
        binding = checked(
            client.post(
                "/channels",
                json={
                    "name": "Signed test inbox",
                    "provider": "signed_webhook",
                    "target_kind": "workflow",
                    "target_id": workflow["workflow_id"],
                    "credentials": {"webhook_secret": secret},
                },
            ),
            201,
        )
        bid = binding["id"]
        callback = "/channels/webhooks/" + "/".join(urlsplit(binding["callback_url"]).path.split("/")[-2:])
        assert binding["enabled"] is False
        assert secret not in json.dumps(binding)
        # Provider setup remains owner-authenticated and rejects the wrong adapter before any external call.
        checked(client.post(f"/channels/bindings/{bid}/connect-telegram"), 422)

        def send(event, text, sender="private-sender", bad=False):
            body = json.dumps({"event_id": event, "sender_id": sender, "text": text}, separators=(",", ":")).encode()
            timestamp = str(int(time.time()))
            signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
            return client.post(
                callback,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-AgenticOrg-Timestamp": timestamp,
                    "X-AgenticOrg-Signature": "bad" if bad else signature,
                },
            )

        assert send("disabled", "Do not run").status_code == 404
        checked(client.patch(f"/channels/bindings/{bid}", json={"enabled": True}))
        assert send("bad", "Do not run", bad=True).status_code == 403
        checked(send("review", "Review the request"))
        inbox = checked(client.get(f"/channels/bindings/{bid}/deliveries"))["deliveries"]
        assert inbox[0]["status"] == "needs_review"
        checked(client.post(f"/channels/bindings/{bid}/deliveries/{inbox[0]['id']}/approve"))

        def wait_completed(text):
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                receipts = checked(client.get(f"/channels/bindings/{bid}/deliveries"))["deliveries"]
                item = next((row for row in receipts if row["text"] == text), None)
                if item and item["status"] not in {"pending", "processing"}:
                    assert item["status"] == "completed", (item["status"], item["error_code"])
                    assert item["result"]["status"] == "completed"
                    return item
                time.sleep(2)
            pytest.fail("Durable channel worker did not finish within 90 seconds")

        wait_completed("Review the request")
        command = checked(client.post(f"/channels/bindings/{bid}/link-code"))["command"]
        checked(send("link", command))
        checked(send("run", "Process linked request"))
        wait_completed("Process linked request")
        checked(send("run", "Process linked request"))
        assert send("run", "Changed contents").status_code == 403
        receipts = checked(client.get(f"/channels/bindings/{bid}/deliveries"))["deliveries"]
        assert sum(row["text"] == "Process linked request" for row in receipts) == 1
        assert command not in json.dumps(receipts)
        # A consumed code cannot delegate a second sender.
        checked(send("reuse-link", command, sender="other-sender"))
        checked(send("other", "Unlinked sender", sender="other-sender"))
        assert checked(client.get(f"/channels/bindings/{bid}/deliveries"))["deliveries"][0]["status"] == "needs_review"
        checked(client.patch(f"/channels/bindings/{bid}", json={"enabled": False}))
        assert send("stopped", "Do not run").status_code == 404
        checked(client.patch(f"/channels/bindings/{bid}", json={"enabled": True}))
        checked(send("revoked", "Previously linked sender"))
        assert checked(client.get(f"/channels/bindings/{bid}/deliveries"))["deliveries"][0]["status"] == "needs_review"
        rotated = checked(
            client.patch(f"/channels/bindings/{bid}", json={"credentials": {"webhook_secret": secrets.token_hex(32)}})
        )
        assert rotated["enabled"] is False
        assert secret not in json.dumps(checked(client.get("/channels")))

        # Native, provider-shaped messages traverse the actual public callback
        # middleware and encrypted inbox, without contacting the provider.
        for provider in ["messenger", "whatsapp", "telegram", "viber", "mailgun"]:
            native_secret = secrets.token_hex(32)
            if provider in {"messenger", "whatsapp"}:
                creds = {
                    "app_secret": native_secret,
                    "access_token": "synthetic-token",
                    "verify_token": native_secret,
                    "receiver_id": "10001",
                    "api_version": "v25.0",
                }
                if provider == "messenger":
                    data = {
                        "entry": [
                            {
                                "id": "10001",
                                "messaging": [
                                    {
                                        "sender": {"id": "20001"},
                                        "recipient": {"id": "10001"},
                                        "message": {"mid": "message-1", "text": "Native request"},
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
                                                    "id": "message-1",
                                                    "from": "20001",
                                                    "type": "text",
                                                    "text": {"body": "Native request"},
                                                }
                                            ],
                                        }
                                    }
                                ]
                            }
                        ]
                    }
            elif provider == "telegram":
                creds = {"access_token": "123:synthetic", "webhook_secret": native_secret}
                data = {
                    "update_id": 1,
                    "message": {
                        "from": {"id": 20001},
                        "chat": {"id": 20001, "type": "private"},
                        "text": "Native request",
                    },
                }
            elif provider == "viber":
                creds = {"access_token": native_secret}
                data = {
                    "event": "message",
                    "message_token": 1,
                    "sender": {"id": "20001"},
                    "message": {"type": "text", "text": "Native request"},
                }
            else:
                creds = {"signing_key": native_secret, "receiver_id": "inbox@example.test"}
                data = {}
            native = checked(
                client.post(
                    "/channels",
                    json={
                        "name": provider,
                        "provider": provider,
                        "target_kind": "workflow",
                        "target_id": workflow["workflow_id"],
                        "credentials": creds,
                    },
                ),
                201,
            )
            native_callback = "/channels/webhooks/" + "/".join(urlsplit(native["callback_url"]).path.split("/")[-2:])
            checked(client.patch(f"/channels/bindings/{native['id']}", json={"enabled": True}))
            with httpx.Client(base_url=BASE + "/api/v1", timeout=30) as public:
                if provider in {"messenger", "whatsapp"}:
                    challenge = public.get(
                        native_callback,
                        params={
                            "hub.mode": "subscribe",
                            "hub.verify_token": native_secret,
                            "hub.challenge": "challenge-123",
                        },
                    )
                    assert challenge.status_code == 200 and challenge.text == "challenge-123"
                if provider == "mailgun":
                    timestamp, token = str(int(time.time())), secrets.token_hex(12)
                    signature = hmac.new(
                        native_secret.encode(), (timestamp + token).encode(), hashlib.sha256
                    ).hexdigest()
                    form = {
                        "timestamp": timestamp,
                        "token": token,
                        "signature": signature,
                        "sender": "sender@example.test",
                        "recipient": "inbox@example.test",
                        "stripped-text": "Native request",
                    }
                    checked(public.post(native_callback, files={key: (None, value) for key, value in form.items()}))
                    assert (
                        public.post(native_callback, files={"attachment": ("file.txt", b"not ingested")}).status_code
                        == 403
                    )
                else:
                    body = json.dumps(data).encode()
                    headers = {"Content-Type": "application/json"}
                    if provider == "telegram":
                        headers["X-Telegram-Bot-Api-Secret-Token"] = native_secret
                    else:
                        digest = hmac.new(native_secret.encode(), body, hashlib.sha256).hexdigest()
                        headers["X-Viber-Content-Signature" if provider == "viber" else "X-Hub-Signature-256"] = (
                            digest if provider == "viber" else "sha256=" + digest
                        )
                    checked(public.post(native_callback, content=body, headers=headers))
                    assert (
                        public.post(
                            native_callback, content=body, headers={"Content-Type": "application/json"}
                        ).status_code
                        == 403
                    )
            receipt = checked(client.get(f"/channels/bindings/{native['id']}/deliveries"))["deliveries"][0]
            assert receipt["status"] == "needs_review"
            assert receipt["text"] == "Native request"
            checked(client.patch(f"/channels/bindings/{native['id']}", json={"enabled": False}))

        # Exercise the real LangGraph agent dispatch separately from workflows.
        # Only the model response is scripted; the API, authority checks, queue,
        # graph execution and encrypted result persistence are real.
        agent = checked(
            client.post(
                "/agents",
                json={
                    "name": "Channel local agent",
                    "agent_type": "custom",
                    "domain": "ops",
                    "initial_status": "active",
                    "system_prompt_text": "Answer the supplied task without using tools.",
                    "authorized_tools": [],
                    "llm_routing": "disabled",
                    "llm": {"model": "vllm:scripted/final-only"},
                },
            ),
            201,
        )
        secret = secrets.token_hex(32)
        agent_binding = checked(
            client.post(
                "/channels",
                json={
                    "name": "Local agent inbox",
                    "provider": "signed_webhook",
                    "target_kind": "agent",
                    "target_id": agent["agent_id"],
                    "credentials": {"webhook_secret": secret},
                },
            ),
            201,
        )
        bid = agent_binding["id"]
        callback = "/channels/webhooks/" + "/".join(urlsplit(agent_binding["callback_url"]).path.split("/")[-2:])
        checked(client.patch(f"/channels/bindings/{bid}", json={"enabled": True}))
        command = checked(client.post(f"/channels/bindings/{bid}/link-code"))["command"]
        checked(send("agent-link", command))
        checked(send("agent-run", "Answer the local channel task"))
        completed = wait_completed("Answer the local channel task")
        assert completed["result"]["runtime"] == "langgraph"
        assert completed["result"]["output"]
        checked(client.post(f"/agents/{agent['agent_id']}/pause"))
        checked(send("paused-agent", "Do not execute a paused agent"))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            item = checked(client.get(f"/channels/bindings/{bid}/deliveries"))["deliveries"][0]
            if item["status"] == "needs_review":
                assert item["error_code"] == "execution_not_completed"
                break
            time.sleep(2)
        else:
            pytest.fail("Paused agent was not refused by channel dispatch")
        checked(client.patch(f"/channels/bindings/{bid}", json={"enabled": False}))


def test_telegram_reply_settings_cannot_cross_tenant_or_target_boundaries():
    assert urlsplit(BASE).hostname in LOCAL_HOSTS
    with (
        httpx.Client(base_url=BASE + "/api/v1", timeout=60) as owner,
        httpx.Client(base_url=BASE + "/api/v1", timeout=60) as other,
    ):
        targets = []
        for client in (owner, other):
            run = uuid.uuid4().hex[:12]
            signup = checked(
                client.post(
                    "/auth/signup",
                    json={
                        "org_name": f"Reply isolation {run}",
                        "admin_name": "Reply Test Admin",
                        "admin_email": f"reply-isolation-{run}@example.test",
                        "password": secrets.token_urlsafe(24) + "aA9!",
                    },
                ),
                201,
            )
            client.cookies.clear()
            client.headers["Authorization"] = "Bearer " + signup["access_token"]
            agent = checked(
                client.post(
                    "/agents",
                    json={
                        "name": "Synthetic reply isolation",
                        "agent_type": "custom",
                        "domain": "ops",
                        "initial_status": "shadow",
                        "system_prompt_text": "Answer synthetic tasks without tools.",
                        "authorized_tools": [],
                        "llm_routing": "disabled",
                        "llm": {"model": "vllm:scripted/final-only"},
                    },
                ),
                201,
            )
            targets.append(agent["agent_id"])
        binding = checked(
            owner.post(
                "/channels",
                json={
                    "name": "Private isolation bot",
                    "provider": "telegram",
                    "target_kind": "agent",
                    "target_id": targets[0],
                    "credentials": {"access_token": "123:synthetic", "webhook_secret": secrets.token_hex(32)},
                },
            ),
            201,
        )
        path = f"/channels/bindings/{binding['id']}"
        assert other.patch(path, json={"reply_mode": "agent_answer"}).status_code == 404
        assert other.get(path + "/deliveries").status_code == 404
        assert (
            owner.patch(
                path,
                json={
                    "target_kind": "agent",
                    "target_id": targets[1],
                    "reply_mode": "agent_answer",
                },
            ).status_code
            == 404
        )
        unchanged = next(row for row in checked(owner.get("/channels"))["bindings"] if row["id"] == binding["id"])
        assert unchanged["target_id"] == targets[0] and unchanged["reply_mode"] == "status"
        assert unchanged["enabled"] is False
