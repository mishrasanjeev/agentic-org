# SPDX-License-Identifier: Apache-2.0
"""Keep the synthetic external-buyer runner off remote and production targets."""

from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
from datetime import UTC
from io import BytesIO
from urllib.error import HTTPError

import pytest

from examples.a2a_commerce_demo import buyer_agent, run_demo
from examples.a2a_commerce_demo.run_demo import DemoScope, _require_local_demo


def test_synthetic_demo_accepts_local_development_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTICORG_ENV", "development")
    monkeypatch.setenv(
        "AGENTICORG_DB_URL",
        "postgresql+asyncpg://agenticorg:local@127.0.0.1:55480/agenticorg",
    )
    monkeypatch.delenv("K_SERVICE", raising=False)
    _require_local_demo("http://127.0.0.1:18081")


@pytest.mark.parametrize(
    ("environment", "database", "api", "cloud_service"),
    [
        ("production", "127.0.0.1", "http://127.0.0.1:18081", ""),
        ("development", "prod.example.com", "http://127.0.0.1:18081", ""),
        ("development", "127.0.0.1", "https://api.example.com", ""),
        ("development", "127.0.0.1", "http://127.0.0.1:18081", "api-service"),
    ],
)
def test_synthetic_demo_refuses_unsafe_targets(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    database: str,
    api: str,
    cloud_service: str,
) -> None:
    monkeypatch.setenv("AGENTICORG_ENV", environment)
    monkeypatch.setenv(
        "AGENTICORG_DB_URL",
        f"postgresql+asyncpg://agenticorg:local@{database}:5432/agenticorg",
    )
    if cloud_service:
        monkeypatch.setenv("K_SERVICE", cloud_service)
    else:
        monkeypatch.delenv("K_SERVICE", raising=False)
    with pytest.raises(RuntimeError, match="local development database and loopback"):
        _require_local_demo(api)


class _Response:
    status = 200

    def __init__(self, body: dict) -> None:
        self.body = BytesIO(json.dumps(body).encode())

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.body.close()

    def read(self, size: int = -1) -> bytes:
        return self.body.read(size)


def test_external_buyer_sends_a2a_v1_message_without_application_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: object, *, timeout: int) -> _Response:
        assert timeout == 15
        assert request.full_url == "http://127.0.0.1:18081/api/v1/a2a/message:send"
        assert request.get_header("Authorization") == "Bearer buyer-token"
        assert request.get_header("A2a-version") == "1.0"
        assert request.get_header("Content-type") == "application/a2a+json"
        payload = json.loads(request.data)
        assert payload["message"]["role"] == "ROLE_USER"
        assert payload["message"]["parts"] == [{"text": "Show catalogue"}]
        uuid.UUID(payload["message"]["messageId"])
        return _Response({"message": {"metadata": {"status": "answered"}}})

    monkeypatch.setattr(buyer_agent, "urlopen", fake_urlopen)
    status, response = buyer_agent._request(
        "http://127.0.0.1:18081", "/api/v1/a2a/message:send",
        token="buyer-token", question="Show catalogue",
    )
    assert status == 200
    assert response["message"]["metadata"]["status"] == "answered"


def test_external_buyer_rejects_invalid_origin_and_handles_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        buyer_agent._request("file:///tmp/demo", "/agent-card.json")

    def denied(*_args: object, **_kwargs: object) -> None:
        raise HTTPError("http://127.0.0.1/a2a", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(buyer_agent, "urlopen", denied)
    assert buyer_agent._request("http://127.0.0.1", "/a2a", token="revoked") == (401, {})


def test_external_buyer_validates_cards_answers_refusal_and_revocation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    names = "Canvas Tote Ceramic Mug Pocket Notebook"
    responses = iter([
        (200, {"supportedInterfaces": [{"protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}]}),
        (200, {"name": "Synthetic seller", "skills": [{"id": "seller_commerce_query"}]}),
        (200, {"message": {"metadata": {
            "status": "answered", "allowedToExecute": False, "nonAuthoritativeForTransaction": True,
            "sourceLabel": "synthetic local demo", "freshnessLabel": "fresh",
        }, "parts": [{"text": names}]}}),
        (200, {"message": {"metadata": {
            "status": "answered", "allowedToExecute": False, "nonAuthoritativeForTransaction": True,
            "sourceLabel": "synthetic local demo",
        }, "parts": [{"text": "Canvas Tote"}]}}),
        (200, {"message": {"metadata": {
            "status": "refused", "allowedToExecute": False, "nonAuthoritativeForTransaction": True,
        }, "parts": [{"text": "No purchase execution"}]}}),
        (401, {}),
    ])
    monkeypatch.setattr(buyer_agent, "_request", lambda *_args, **_kwargs: next(responses))
    buyer_agent.run("http://127.0.0.1:18081", "token")
    buyer_agent.run("http://127.0.0.1:18081", "token", expect_denied=True)
    output = capsys.readouterr().out
    assert names in output
    assert "Revocation: external buyer credential rejected" in output


@pytest.mark.parametrize("step", ["card", "seller", "status", "execution", "catalogue", "source", "revocation"])
def test_external_buyer_fails_closed_on_bad_response(monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    good_card = (200, {"supportedInterfaces": [{"protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}]})
    good_seller = (200, {"name": "Seller", "skills": [{"id": "seller_commerce_query"}]})
    good_answer = (200, {"message": {"metadata": {
        "status": "answered", "allowedToExecute": False, "nonAuthoritativeForTransaction": True,
        "sourceLabel": "synthetic local demo",
    }, "parts": [{"text": "Canvas Tote Ceramic Mug Pocket Notebook"}]}})
    responses = [good_card, good_seller, good_answer]
    if step == "card":
        responses[0] = (200, {"supportedInterfaces": []})
    elif step == "seller":
        responses[1] = (200, {"name": "Seller", "skills": []})
    elif step == "status":
        responses[2] = (200, {"message": {"metadata": {"status": "unknown"}}})
    elif step == "execution":
        responses[2] = (200, {"message": {"metadata": {
            "status": "answered", "allowedToExecute": True, "nonAuthoritativeForTransaction": True,
        }}})
    elif step == "catalogue":
        responses[2] = (200, {"message": {"metadata": {
            "status": "answered", "allowedToExecute": False, "nonAuthoritativeForTransaction": True,
        }, "parts": [{"text": "Canvas Tote"}]}})
    elif step == "source":
        responses[2] = (200, {"message": {"metadata": {
            "status": "answered", "allowedToExecute": False, "nonAuthoritativeForTransaction": True,
        }, "parts": [{"text": "Canvas Tote Ceramic Mug Pocket Notebook"}]}})
    else:
        responses = [(200, {})]
    monkeypatch.setattr(buyer_agent, "_request", lambda *_args, **_kwargs: responses.pop(0))
    with pytest.raises(RuntimeError):
        buyer_agent.run("http://127.0.0.1", "token", expect_denied=step == "revocation")


def test_external_buyer_cli_requires_token_and_returns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(buyer_agent.sys, "argv", ["buyer_agent.py", "--base-url", "http://127.0.0.1"])
    monkeypatch.delenv("A2A_BUYER_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        buyer_agent.main()
    monkeypatch.setenv("A2A_BUYER_TOKEN", "test-token")
    monkeypatch.setattr(buyer_agent, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("denied")))
    assert buyer_agent.main() == 1


def _scope() -> DemoScope:
    return DemoScope(uuid.uuid4(), "merchant", "seller", "packet", "evidence", "cache", uuid.uuid4(), "token")


def test_runner_passes_token_only_to_independent_process(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[-3:] == ["--base-url", "http://127.0.0.1:18081", "--expect-denied"]
        assert kwargs["env"]["A2A_BUYER_TOKEN"] == "test-token"
        return subprocess.CompletedProcess(args, 0, "Revocation: denied\n", "")

    monkeypatch.setattr(run_demo.subprocess, "run", fake_run)
    run_demo._buyer_process("http://127.0.0.1:18081", "test-token", expect_denied=True)
    monkeypatch.setattr(
        run_demo.subprocess, "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", "client failed"),
    )
    with pytest.raises(RuntimeError, match="client failed"):
        run_demo._buyer_process("http://127.0.0.1:18081", "test-token")


def test_runner_revokes_and_removes_only_its_own_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = _scope()
    rows = {
        (run_demo.CommerceA2ABuyerAccess, scope.access_id): type("Row", (), {"status": "active", "revoked_at": None})(),
    }
    deleted: list[object] = []

    class Session:
        async def get(self, model: object, key: object) -> object | None:
            return rows.get((model, key))

        async def delete(self, row: object) -> None:
            deleted.append(row)

    class Context:
        async def __aenter__(self) -> Session:
            return Session()

        async def __aexit__(self, *_args: object) -> None:
            pass

    monkeypatch.setattr(run_demo, "get_tenant_session", lambda tenant: Context())
    asyncio.run(run_demo._revoke(scope))
    assert rows[(run_demo.CommerceA2ABuyerAccess, scope.access_id)].status == "revoked"
    assert rows[(run_demo.CommerceA2ABuyerAccess, scope.access_id)].revoked_at.tzinfo is UTC
    asyncio.run(run_demo._remove(scope))
    assert deleted == [rows[(run_demo.CommerceA2ABuyerAccess, scope.access_id)]]


def test_runner_seed_writes_scoped_synthetic_evidence_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    added: list[object] = []
    stored: list[object] = []

    class Session:
        def add(self, row: object) -> None:
            added.append(row)

    class Context:
        async def __aenter__(self) -> Session:
            return Session()

        async def __aexit__(self, *_args: object) -> None:
            pass

    class Repository:
        def __init__(self, session: Session) -> None:
            pass

        async def upsert(self, record: object) -> dict:
            stored.append(record)
            return {"stored": True}

    monkeypatch.setattr(run_demo, "get_tenant_session", lambda tenant: Context())
    monkeypatch.setattr(run_demo, "DurableOacpArtifactCacheRepository", Repository)
    scope = asyncio.run(run_demo._seed())
    assert len(added) == 3
    assert added[0].tenant_id == str(scope.tenant_id)
    assert added[1].source_mode == "local_fixture"
    assert added[1].product_count == 3
    assert added[2].token_hash != scope.token
    assert stored[0].authority == "synthetic.local.demo"
    assert stored[0].risk_tier == "low"


def test_runner_cleanup_executes_after_buyer_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = _scope()
    events: list[str] = []

    async def seed() -> DemoScope:
        return scope

    async def remove(_scope: DemoScope) -> None:
        events.append("remove")

    monkeypatch.setattr(run_demo, "_seed", seed)
    monkeypatch.setattr(run_demo, "_remove", remove)
    def fail_buyer(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("buyer failed")

    monkeypatch.setattr(run_demo, "_buyer_process", fail_buyer)
    with pytest.raises(RuntimeError, match="buyer failed"):
        asyncio.run(run_demo._run("http://127.0.0.1:18081"))
    assert events == ["remove"]


def test_runner_cli_refuses_unguarded_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_demo.sys, "argv", ["run_demo.py"])
    with pytest.raises(SystemExit):
        run_demo.main()
    monkeypatch.setattr(run_demo.sys, "argv", ["run_demo.py", "--confirm-local-synthetic"])
    monkeypatch.setattr(run_demo, "_require_local_demo", lambda *_args: (_ for _ in ()).throw(RuntimeError("blocked")))
    assert run_demo.main() == 1
