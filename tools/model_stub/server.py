# SPDX-License-Identifier: Apache-2.0
"""OpenAI-compatible model stub: protocol logic and HTTP server.

``POST /v1/chat/completions`` answers in one of two ways, chosen by the
requested model id:

* ``scripted/<name>`` - the next turn of ``<name>.json`` in the scripts
  directory. The turn is the number of assistant messages already in the
  conversation, so the stub holds no state. Tool-call ids come from
  ``core.test_doubles.scripted_model.tool_call`` and match the in-process
  scripted model. A conversation longer than the script, a tool the request
  did not bind, or an unknown script is an error.
* any other model id - a cassette. The request is converted to LangChain
  messages and keyed with ``core.model_replay.request_key`` (model id,
  messages, tool schemas, temperature, max tokens, stop), the same algorithm
  and file format as the in-process harness. In ``replay`` mode (the default)
  a missing cassette is an error; nothing is ever forwarded. In ``record``
  mode the request is forwarded to an OpenAI-compatible upstream with
  ``MODEL_RECORD_API_KEY`` and the answer saved; the stub refuses to start in
  record mode without that key.

Every request field that can change the answer is part of the cassette key
(:data:`KEYED_OPTIONS` join the harness's temperature, max tokens and stop when
present); any other field is rejected rather than ignored. Streaming, ``n``
other than 1 and malformed requests are rejected with OpenAI-style error
bodies, and an invalid script is a 422, never a dropped connection.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from langchain_core.messages import AIMessage, BaseMessage, convert_to_messages

from core import model_replay
from core.test_doubles.scripted_model import final, tool_call
from tools.model_stub.guard import StartupRefusedError, assert_development_runtime

__all__ = ["ConfigError", "ModelStub", "StartupRefusedError", "StubError", "StubSettings", "settings_from_env"]

MODE_REPLAY = "replay"
MODE_RECORD = "record"
SCRIPTED_PREFIX = "scripted/"
DEFAULT_UPSTREAM = "https://api.openai.com/v1"
DEFAULT_SCRIPTS_DIR = Path(__file__).with_name("scripts")
MAX_BODY_BYTES = 8 * 1024 * 1024
UPSTREAM_TIMEOUT_SECONDS = 120.0

_SCRIPT_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
# Request fields that change what a model returns. They are added to the key's
# params only when present, so requests without them keep the in-process key.
KEYED_OPTIONS = ("tool_choice", "response_format", "top_p", "seed", "parallel_tool_calls",
                 "frequency_penalty", "presence_penalty", "logit_bias", "reasoning_effort")
# Fields that are understood but do not change the answer.
_HANDLED_FIELDS = frozenset({"model", "messages", "tools", "temperature", "max_tokens", "max_completion_tokens",
                             "stop", "stream", "n", "user"})
_TOOL_NAME_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_ROLES = frozenset({"system", "developer", "user", "assistant", "tool"})
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


class ConfigError(ValueError):
    """The stub's settings are invalid."""


class StubError(Exception):
    """A request the stub cannot answer; rendered as an OpenAI error body."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# ── Settings ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StubSettings:
    mode: str
    cassette_dir: Path
    scripts_dir: Path = DEFAULT_SCRIPTS_DIR
    upstream_url: str = DEFAULT_UPSTREAM
    record_api_key: str | None = field(default=None, repr=False)


def settings_from_env(environ: Mapping[str, str]) -> StubSettings:
    assert_development_runtime(environ)
    mode = environ.get("MODEL_STUB_MODE", MODE_REPLAY).strip().lower()
    if mode not in (MODE_REPLAY, MODE_RECORD):
        raise ConfigError(f"MODEL_STUB_MODE must be replay or record, got {mode!r}")
    cassette_dir = Path(environ.get("MODEL_STUB_CASSETTE_DIR", "/cassettes"))
    if not cassette_dir.is_dir():
        raise ConfigError(f"cassette directory {cassette_dir} does not exist")
    scripts_dir = Path(environ.get("MODEL_STUB_SCRIPTS_DIR", str(DEFAULT_SCRIPTS_DIR)))
    if not scripts_dir.is_dir():
        raise ConfigError(f"scripts directory {scripts_dir} does not exist")
    if mode == MODE_REPLAY:
        return StubSettings(mode=mode, cassette_dir=cassette_dir, scripts_dir=scripts_dir)

    api_key = environ.get("MODEL_RECORD_API_KEY", "").strip()
    if not api_key:
        raise StartupRefusedError("record mode calls a real model and needs MODEL_RECORD_API_KEY")
    upstream = environ.get("MODEL_STUB_UPSTREAM_URL", DEFAULT_UPSTREAM).strip().rstrip("/")
    parts = urlsplit(upstream)
    loopback_http = parts.scheme == "http" and parts.hostname in _LOOPBACK
    if not (parts.scheme == "https" or loopback_http) or not parts.netloc or parts.query or parts.fragment:
        raise ConfigError("MODEL_STUB_UPSTREAM_URL must be an https base URL (plain http only on loopback)")
    return StubSettings(
        mode=mode, cassette_dir=cassette_dir, scripts_dir=scripts_dir, upstream_url=upstream, record_api_key=api_key
    )


# ── Wire format ──────────────────────────────────────────────────────────────


def _to_langchain(messages: Any) -> list[BaseMessage]:
    if not isinstance(messages, list) or not messages:
        raise StubError(HTTPStatus.BAD_REQUEST, "invalid_request_error", "messages must be a non-empty list")
    wire = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") not in _ROLES:
            raise StubError(HTTPStatus.BAD_REQUEST, "invalid_request_error", f"messages[{index}] has no valid role")
        entry = dict(message)
        if entry.get("content") is None:
            entry["content"] = ""
        wire.append(entry)
    try:
        return convert_to_messages(wire)
    except (ValueError, TypeError, KeyError, NotImplementedError) as exc:
        raise StubError(HTTPStatus.BAD_REQUEST, "invalid_request_error", f"messages could not be read: {exc}") from exc


def _tools(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    tools = body.get("tools") or []
    if not isinstance(tools, list) or not all(isinstance(t, dict) for t in tools):
        raise StubError(HTTPStatus.BAD_REQUEST, "invalid_request_error", "tools must be a list of objects")
    return tools


def _reject_unknown_fields(body: Mapping[str, Any]) -> None:
    unknown = sorted(set(body) - _HANDLED_FIELDS - set(KEYED_OPTIONS))
    if unknown:
        raise StubError(
            HTTPStatus.BAD_REQUEST,
            "unsupported_parameter",
            f"unsupported request field(s): {', '.join(unknown)}; the stub cannot key or honour them",
        )


def _params(body: Mapping[str, Any]) -> dict[str, Any]:
    """The key's params: the harness's sampling parameters plus any keyed option present."""
    stop = body.get("stop")
    if isinstance(stop, str):
        stop = [stop]
    max_tokens = body.get("max_completion_tokens", body.get("max_tokens"))
    params: dict[str, Any] = {"temperature": body.get("temperature"), "max_tokens": max_tokens, "stop": stop}
    for option in KEYED_OPTIONS:
        if option in body:
            params[option] = body[option]
    return params


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return ""


def _invalid_script(name: str, detail: str) -> StubError:
    return StubError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_script", f"script {name!r}: {detail}")


def completion_body(model: str, key: str, message: AIMessage) -> dict[str, Any]:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    tool_calls = [
        {
            "id": call.get("id") or f"call-{digest[:12]}-{index}",
            "type": "function",
            "function": {"name": call["name"], "arguments": json.dumps(call["args"], sort_keys=True)},
        }
        for index, call in enumerate(message.tool_calls)
    ]
    wire: dict[str, Any] = {"role": "assistant", "content": _text(message.content) or (None if tool_calls else "")}
    if tool_calls:
        wire["tool_calls"] = tool_calls
    return {
        "id": f"chatcmpl-stub-{digest[:24]}",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "system_fingerprint": "agenticorg-model-stub",
        "choices": [
            {"index": 0, "message": wire, "logprobs": None, "finish_reason": "tool_calls" if tool_calls else "stop"}
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def ai_message_from_completion(body: Any) -> AIMessage:
    """Read an upstream chat completion back into the harness's message form."""
    try:
        message = body["choices"][0]["message"]
        calls = [
            {"name": c["function"]["name"], "args": json.loads(c["function"]["arguments"] or "{}"), "id": c.get("id"),
             "type": "tool_call"}
            for c in message.get("tool_calls") or []
        ]
        return AIMessage(content=message.get("content") or "", tool_calls=calls)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        message_text = "upstream response is not a chat completion"
        raise StubError(HTTPStatus.BAD_GATEWAY, "upstream_invalid_response", message_text) from exc


# ── The stub ─────────────────────────────────────────────────────────────────


class ModelStub:
    def __init__(
        self,
        settings: StubSettings,
        upstream: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.settings = settings
        self._upstream = upstream or self._post_upstream

    def models(self) -> dict[str, Any]:
        names = sorted(path.stem for path in self.settings.scripts_dir.glob("*.json"))
        data = [{"id": f"{SCRIPTED_PREFIX}{name}", "object": "model", "created": 0, "owned_by": "agenticorg-model-stub"}
                for name in names if _SCRIPT_NAME_RE.fullmatch(name)]
        return {"object": "list", "data": data}

    def chat_completions(self, body: Any) -> tuple[dict[str, Any], str]:
        """Return the completion body and how it was produced (``scripted``, ``replay`` or ``record``)."""
        if not isinstance(body, dict):
            raise StubError(HTTPStatus.BAD_REQUEST, "invalid_request_error", "the body must be a JSON object")
        if body.get("stream"):
            raise StubError(HTTPStatus.BAD_REQUEST, "invalid_request_error", "streaming is not supported")
        if body.get("n", 1) != 1:
            raise StubError(HTTPStatus.BAD_REQUEST, "invalid_request_error", "only n=1 is supported")
        model = body.get("model")
        if not isinstance(model, str) or not model:
            raise StubError(HTTPStatus.BAD_REQUEST, "invalid_request_error", "model is required")
        _reject_unknown_fields(body)
        messages = _to_langchain(body.get("messages"))
        tools = _tools(body)
        if model.startswith(SCRIPTED_PREFIX):
            key, message = self._scripted(model.removeprefix(SCRIPTED_PREFIX), messages, tools)
            return completion_body(model, key, message), "scripted"
        key, message = self._cassette(body, model, messages, tools)
        return completion_body(model, key, message), self.settings.mode

    def _scripted(self, name: str, messages: list[BaseMessage], tools: list[dict[str, Any]]) -> tuple[str, AIMessage]:
        if not _SCRIPT_NAME_RE.fullmatch(name):
            raise StubError(HTTPStatus.BAD_REQUEST, "invalid_request_error", "script names use a-z, 0-9, '-' and '_'")
        path = self.settings.scripts_dir / f"{name}.json"
        if not path.is_file():
            raise StubError(HTTPStatus.NOT_FOUND, "unknown_script", f"no script named {name!r}")
        try:
            steps = json.loads(path.read_bytes().decode("utf-8"))["steps"]
            if not isinstance(steps, list) or not steps:
                raise ValueError("steps must be a non-empty list")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise _invalid_script(name, str(exc)) from exc

        turn = sum(1 for message in messages if isinstance(message, AIMessage))
        if turn >= len(steps):
            noun = "step" if len(steps) == 1 else "steps"
            raise StubError(
                HTTPStatus.CONFLICT,
                "script_exhausted",
                f"script {name!r}: the conversation asked for turn {turn + 1} after all {len(steps)} scripted {noun}",
            )
        step = steps[turn]
        where = f"step {turn + 1}"
        if not isinstance(step, dict) or set(step) - {"tool_calls", "content"} or len(step) != 1:
            raise _invalid_script(name, f"{where} must be an object with exactly one of 'tool_calls' or 'content'")
        if "content" in step:
            if not isinstance(step["content"], str | dict):
                raise _invalid_script(name, f"{where}: 'content' must be a string or an object")
            return f"scripted:{name}:{turn}", final(step["content"])

        planned = step["tool_calls"]
        if not isinstance(planned, list) or not planned:
            raise _invalid_script(name, f"{where}: 'tool_calls' must be a non-empty list")
        bound = {
            t["function"].get("name")
            for t in tools
            if t.get("type") == "function" and isinstance(t.get("function"), dict)
        }
        calls = []
        for index, call in enumerate(planned):
            call_where = f"{where} tool_calls[{index}]"
            if not isinstance(call, dict) or set(call) - {"name", "arguments"}:
                raise _invalid_script(name, f"{call_where} must be an object with 'name' and optional 'arguments'")
            tool = call.get("name")
            arguments = call.get("arguments", {})
            if not isinstance(tool, str) or not _TOOL_NAME_RE.fullmatch(tool):
                raise _invalid_script(name, f"{call_where}: 'name' must be a tool name")
            if not isinstance(arguments, dict):
                raise _invalid_script(name, f"{call_where}: 'arguments' must be an object")
            if "call_id" in arguments:
                raise _invalid_script(name, f"{call_where}: an argument may not be called 'call_id'")
            if tool not in bound:
                raise StubError(
                    HTTPStatus.BAD_REQUEST,
                    "script_mismatch",
                    f"script {name!r} turn {turn + 1} calls {tool!r}, which the request did not bind",
                )
            calls.extend(tool_call(tool, **arguments).tool_calls)
        return f"scripted:{name}:{turn}", AIMessage(content="", tool_calls=calls)

    def _cassette(
        self, body: dict[str, Any], model: str, messages: list[BaseMessage], tools: list[dict[str, Any]]
    ) -> tuple[str, AIMessage]:
        params = _params(body)
        try:
            key = model_replay.request_key(model, messages, tools=tools, params=params)
            request = model_replay._render(model, messages, tools, params)
        except model_replay.CassetteError as exc:
            raise StubError(HTTPStatus.BAD_REQUEST, "invalid_request_error", str(exc)) from exc
        with model_replay.cassette_scope(self.settings.cassette_dir):
            if self.settings.mode == MODE_REPLAY:
                try:
                    stored = model_replay._restore_message(model_replay._load(key, request)["response"])
                except model_replay.CassetteMissError as exc:
                    raise StubError(HTTPStatus.NOT_FOUND, "cassette_miss", str(exc)) from exc
                except model_replay.CassetteError as exc:
                    raise StubError(HTTPStatus.INTERNAL_SERVER_ERROR, "cassette_unreadable", str(exc)) from exc
                if not isinstance(stored, AIMessage):
                    raise StubError(HTTPStatus.INTERNAL_SERVER_ERROR, "cassette_unreadable", f"{key} is not an AI turn")
                return key, stored
            message = ai_message_from_completion(self._upstream({**body, "stream": False}))
            model_replay._save(key, request, model_replay._dump_message(message))
            return key, message

    def _post_upstream(self, body: dict[str, Any]) -> Any:
        if not self.settings.record_api_key:
            raise StubError(HTTPStatus.INTERNAL_SERVER_ERROR, "record_disabled", "no MODEL_RECORD_API_KEY")
        request = urllib.request.Request(  # noqa: S310 - scheme validated in settings_from_env
            f"{self.settings.upstream_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.settings.record_api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT_SECONDS) as response:  # noqa: S310
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise StubError(HTTPStatus.BAD_GATEWAY, "upstream_error", f"upstream returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise StubError(HTTPStatus.BAD_GATEWAY, "upstream_unavailable", type(exc).__name__) from exc


# ── HTTP server ──────────────────────────────────────────────────────────────


def _log(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True), file=sys.stderr, flush=True)


def make_handler(stub: ModelStub) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "agenticorg-model-stub"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature from the base class
            return

        def _send(self, status: int, payload: Mapping[str, Any], **log: Any) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            _log("model_stub_request", method=self.command, path=urlsplit(self.path).path, status=int(status), **log)

        def _error(self, error: StubError) -> None:
            payload = {"error": {"message": error.message, "type": error.code, "code": error.code, "param": None}}
            self._send(error.status, payload, error=error.code)

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            path = urlsplit(self.path).path
            if path == "/healthz":
                self._send(HTTPStatus.OK, {"status": "ok", "mode": stub.settings.mode})
            elif path == "/v1/models":
                self._send(HTTPStatus.OK, stub.models())
            else:
                self._error(StubError(HTTPStatus.NOT_FOUND, "not_found", "unknown path"))

        def do_POST(self) -> None:  # noqa: N802 - http.server naming
            if urlsplit(self.path).path != "/v1/chat/completions":
                self._error(StubError(HTTPStatus.NOT_FOUND, "not_found", "unknown path"))
                return
            length_header = self.headers.get("Content-Length", "")
            length = int(length_header) if length_header.isdigit() else 0
            if length <= 0 or length > MAX_BODY_BYTES:
                self._error(StubError(HTTPStatus.BAD_REQUEST, "invalid_request_error", "missing or oversized body"))
                return
            try:
                body = json.loads(self.rfile.read(length))
            except ValueError:
                self._error(StubError(HTTPStatus.BAD_REQUEST, "invalid_request_error", "body is not JSON"))
                return
            try:
                payload, source = stub.chat_completions(body)
            except StubError as error:
                self._error(error)
            else:
                self._send(HTTPStatus.OK, payload, source=source)

    return Handler


def make_server(stub: ModelStub, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(stub))
    server.daemon_threads = True
    return server
