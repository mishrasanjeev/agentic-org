# SPDX-License-Identifier: Apache-2.0
"""Development model stub (tools/model_stub): scripted turns, replay, record and guards."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai import ChatOpenAI

from core import model_replay
from core.test_doubles.scripted_model import tool_call
from tools.model_stub import __main__ as stub_main
from tools.model_stub import server as ms

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_KEY = "unit-test-placeholder-key"


@tool
def lookup_case(case_id: str) -> str:
    """Look up a case by its id."""
    return case_id


@contextmanager
def serving(stub: ms.ModelStub) -> Iterator[str]:
    server = ms.make_server(stub, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def cassettes(tmp_path: Path) -> Path:
    path = tmp_path / "cassettes"
    path.mkdir()
    return path


@pytest.fixture()
def scripts(tmp_path: Path) -> Path:
    path = tmp_path / "scripts"
    path.mkdir()
    review = {
        "steps": [
            {"tool_calls": [{"name": "lookup_case", "arguments": {"case_id": "CASE-0001"}}]},
            {"content": {"decision": "refer", "confidence": 0.5}},
        ]
    }
    (path / "review-case.json").write_text(json.dumps(review), encoding="utf-8")
    return path


def replay_stub(cassettes: Path, scripts: Path, **kwargs: Any) -> ms.ModelStub:
    return ms.ModelStub(ms.StubSettings(mode="replay", cassette_dir=cassettes, scripts_dir=scripts), **kwargs)


def chat(base: str, model: str) -> ChatOpenAI:
    return ChatOpenAI(
        model=model, base_url=f"{base}/v1", api_key=FAKE_KEY, temperature=0.1, max_tokens=256, max_retries=0, timeout=10
    )


PROMPT = [SystemMessage("You review business cases."), HumanMessage("Review case CASE-0001.")]


def post(base: str, body: dict[str, Any]) -> httpx.Response:
    return httpx.post(f"{base}/v1/chat/completions", json=body, timeout=10)


# ── Scripted sequences ──────────────────────────────────────────────────────


def test_scripted_sequence_calls_a_tool_then_answers_through_an_openai_client(cassettes: Path, scripts: Path) -> None:
    with serving(replay_stub(cassettes, scripts)) as base:
        model = chat(base, "scripted/review-case").bind_tools([lookup_case])
        first = model.invoke(PROMPT)
        assert isinstance(first, AIMessage)
        expected = tool_call("lookup_case", case_id="CASE-0001").tool_calls[0]
        assert [(c["name"], c["args"], c["id"]) for c in first.tool_calls] == [
            (expected["name"], expected["args"], expected["id"])
        ]

        tool_result = ToolMessage(content='{"status": "open"}', tool_call_id=expected["id"])
        second = model.invoke([*PROMPT, first, tool_result])
        assert second.tool_calls == []
        assert json.loads(str(second.content)) == {"confidence": 0.5, "decision": "refer"}


def test_committed_example_script_answers_without_tools(cassettes: Path) -> None:
    stub = replay_stub(cassettes, ms.DEFAULT_SCRIPTS_DIR)
    with serving(stub) as base:
        answer = chat(base, "scripted/final-only").invoke(PROMPT)
    assert json.loads(str(answer.content))["status"] == "completed"


def test_conversation_longer_than_the_script_is_rejected(cassettes: Path, scripts: Path) -> None:
    with serving(replay_stub(cassettes, scripts)) as base:
        body = {
            "model": "scripted/review-case",
            "messages": [
                {"role": "user", "content": "go"},
                {"role": "assistant", "content": "one"},
                {"role": "assistant", "content": "two"},
            ],
        }
        response = post(base, body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "script_exhausted"


def test_scripted_tool_the_request_did_not_bind_is_rejected(cassettes: Path, scripts: Path) -> None:
    with serving(replay_stub(cassettes, scripts)) as base:
        response = post(base, {"model": "scripted/review-case", "messages": [{"role": "user", "content": "go"}]})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "script_mismatch"


@pytest.mark.parametrize(("model", "status", "code"), [
    ("scripted/no-such-script", 404, "unknown_script"),
    ("scripted/../secrets", 400, "invalid_request_error"),
    ("scripted/UPPER", 400, "invalid_request_error"),
])
def test_unknown_or_unsafe_script_names_are_rejected(
    cassettes: Path, scripts: Path, model: str, status: int, code: str
) -> None:
    with serving(replay_stub(cassettes, scripts)) as base:
        response = post(base, {"model": model, "messages": [{"role": "user", "content": "go"}]})
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)


# ── Replay and record ───────────────────────────────────────────────────────


class FakeUpstream:
    """An OpenAI-compatible upstream on loopback that records what it was sent."""

    def __init__(self, status: int = 200) -> None:
        self.requests: list[tuple[dict[str, str], dict[str, Any]]] = []
        self.status = status

    @contextmanager
    def running(self) -> Iterator[str]:
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                upstream.requests.append((dict(self.headers), body))
                payload = {
                    "id": "chatcmpl-upstream",
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
                    "choices": [{
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {"role": "assistant", "content": None, "tool_calls": [{
                            "id": "call-upstream-1", "type": "function",
                            "function": {"name": "lookup_case", "arguments": '{"case_id": "CASE-0001"}'},
                        }]},
                    }],
                }
                data = json.dumps(payload).encode()
                self.send_response(upstream.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}/v1"
        finally:
            server.shutdown()
            server.server_close()


def record_settings(cassettes: Path, scripts: Path, upstream_url: str) -> ms.StubSettings:
    env = {
        "AGENTICORG_ENV": "test",
        "MODEL_STUB_MODE": "record",
        "MODEL_RECORD_API_KEY": FAKE_KEY,
        "MODEL_STUB_CASSETTE_DIR": str(cassettes),
        "MODEL_STUB_SCRIPTS_DIR": str(scripts),
        "MODEL_STUB_UPSTREAM_URL": upstream_url,
    }
    return ms.settings_from_env(env)


def test_record_then_replay_serves_the_same_answer_without_the_upstream(cassettes: Path, scripts: Path) -> None:
    upstream = FakeUpstream()
    with upstream.running() as url, serving(ms.ModelStub(record_settings(cassettes, scripts, url))) as base:
        recorded = chat(base, "acme-model-1").bind_tools([lookup_case]).invoke(PROMPT)
    assert len(upstream.requests) == 1
    headers, sent = upstream.requests[0]
    assert headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert sent["stream"] is False
    [cassette] = list(cassettes.glob("*.json"))

    params = {"temperature": 0.1, "max_tokens": 256, "stop": None}
    in_process_key = model_replay.request_key(
        "acme-model-1", PROMPT, tools=[convert_to_openai_tool(lookup_case)], params=params
    )
    assert cassette.stem == in_process_key.removeprefix("sha256:"), "keyed exactly as the in-process harness keys"
    assert FAKE_KEY not in cassette.read_text(encoding="utf-8")

    def no_upstream(body: dict[str, Any]) -> Any:
        raise AssertionError("replay must never call the upstream")

    with serving(replay_stub(cassettes, scripts, upstream=no_upstream)) as base:
        replayed = chat(base, "acme-model-1").bind_tools([lookup_case]).invoke(PROMPT)
    assert replayed.tool_calls == recorded.tool_calls
    assert replayed.tool_calls[0]["args"] == {"case_id": "CASE-0001"}


def test_replay_miss_is_an_error_and_never_calls_the_upstream(cassettes: Path, scripts: Path) -> None:
    def no_upstream(body: dict[str, Any]) -> Any:
        raise AssertionError("replay must never call the upstream")

    with serving(replay_stub(cassettes, scripts, upstream=no_upstream)) as base:
        with pytest.raises(openai.NotFoundError, match="cassette"):
            chat(base, "acme-model-1").invoke(PROMPT)
        response = post(base, {"model": "acme-model-1", "messages": [{"role": "user", "content": "hello"}]})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "cassette_miss"


def test_upstream_failure_while_recording_saves_nothing(cassettes: Path, scripts: Path) -> None:
    upstream = FakeUpstream(status=500)
    with upstream.running() as url, serving(ms.ModelStub(record_settings(cassettes, scripts, url))) as base:
        response = post(base, {"model": "acme-model-1", "messages": [{"role": "user", "content": "hello"}]})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"
    assert list(cassettes.glob("*.json")) == []


# ── Request validation and endpoints ────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        {"model": "scripted/review-case", "messages": [{"role": "user", "content": "x"}], "stream": True},
        {"model": "scripted/review-case", "messages": [{"role": "user", "content": "x"}], "n": 2},
        {"model": "scripted/review-case", "messages": []},
        {"model": "scripted/review-case", "messages": [{"role": "robot", "content": "x"}]},
        {"messages": [{"role": "user", "content": "x"}]},
        ["not", "an", "object"],
    ],
)
def test_malformed_requests_are_rejected(cassettes: Path, scripts: Path, body: Any) -> None:
    with serving(replay_stub(cassettes, scripts)) as base:
        response = httpx.post(f"{base}/v1/chat/completions", json=body, timeout=10)
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_non_json_body_is_rejected(cassettes: Path, scripts: Path) -> None:
    with serving(replay_stub(cassettes, scripts)) as base:
        response = httpx.post(f"{base}/v1/chat/completions", content=b"{nope", timeout=10)
    assert response.status_code == 400


def test_models_and_health_endpoints(cassettes: Path, scripts: Path) -> None:
    with serving(replay_stub(cassettes, scripts)) as base:
        models = httpx.get(f"{base}/v1/models", timeout=10).json()
        health = httpx.get(f"{base}/healthz", timeout=10).json()
        missing = httpx.get(f"{base}/v1/elsewhere", timeout=10)
    assert [m["id"] for m in models["data"]] == ["scripted/review-case"]
    assert health == {"status": "ok", "mode": "replay"}
    assert missing.status_code == 404


# ── Settings and startup guards ─────────────────────────────────────────────


def test_record_mode_without_a_key_refuses_to_start(cassettes: Path) -> None:
    env = {"AGENTICORG_ENV": "development", "MODEL_STUB_MODE": "record", "MODEL_STUB_CASSETTE_DIR": str(cassettes)}
    with pytest.raises(ms.StartupRefusedError, match="MODEL_RECORD_API_KEY"):
        ms.settings_from_env(env)


def test_main_exits_without_serving_in_record_mode_without_a_key(
    cassettes: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_server(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not bind a port")

    monkeypatch.setattr(ms, "make_server", no_server)
    for name, value in {"AGENTICORG_ENV": "development", "MODEL_STUB_MODE": "record",
                        "MODEL_STUB_CASSETTE_DIR": str(cassettes)}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("MODEL_RECORD_API_KEY", raising=False)
    assert stub_main.main() == stub_main.EXIT_REFUSED
    assert "MODEL_RECORD_API_KEY" in capsys.readouterr().err

    monkeypatch.setenv("AGENTICORG_ENV", "production")
    assert stub_main.main() == stub_main.EXIT_REFUSED
    assert "production-like runtime" in capsys.readouterr().err


@pytest.mark.parametrize(
    "environ",
    [{}, {"AGENTICORG_ENV": "production"}, {"AGENTICORG_ENV": "development", "APP_ENV": "staging"}],
)
def test_refuses_to_start_outside_development(environ: dict[str, str], cassettes: Path) -> None:
    with pytest.raises(ms.StartupRefusedError):
        ms.settings_from_env({**environ, "MODEL_STUB_CASSETTE_DIR": str(cassettes)})


def test_replay_is_the_default_and_holds_no_key(cassettes: Path) -> None:
    settings = ms.settings_from_env(
        {"AGENTICORG_ENV": "test", "MODEL_STUB_CASSETTE_DIR": str(cassettes), "MODEL_RECORD_API_KEY": FAKE_KEY}
    )
    assert settings.mode == "replay"
    assert settings.record_api_key is None


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        ({"MODEL_STUB_MODE": "live"}, "replay or record"),
        ({"MODEL_STUB_CASSETTE_DIR": "/definitely/not/here"}, "does not exist"),
        ({"MODEL_STUB_MODE": "record", "MODEL_RECORD_API_KEY": FAKE_KEY,
          "MODEL_STUB_UPSTREAM_URL": "http://upstream.example.com/v1"}, "https base URL"),
    ],
)
def test_invalid_settings_are_rejected(cassettes: Path, extra: dict[str, str], reason: str) -> None:
    env = {"AGENTICORG_ENV": "test", "MODEL_STUB_CASSETTE_DIR": str(cassettes), **extra}
    with pytest.raises(ms.ConfigError, match=reason):
        ms.settings_from_env(env)


def test_record_key_is_not_in_the_settings_repr(cassettes: Path, scripts: Path) -> None:
    settings = record_settings(cassettes, scripts, "https://upstream.example.com/v1")
    assert FAKE_KEY not in repr(settings)
