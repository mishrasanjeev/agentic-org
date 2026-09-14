# SPDX-License-Identifier: Apache-2.0
"""Record and replay model calls so agent tests are deterministic.

``AGENTICORG_MODEL_MODE`` selects how model calls behave:

* ``live``   - call the provider (the default outside CI).
* ``record`` - call the provider and store each request/response pair as a
  cassette.
* ``replay`` - answer from cassettes only. A request with no matching
  cassette raises :class:`CassetteMissError`; it never falls through to a
  live call. This is the default when ``CI`` is set and the call runs inside
  a cassette directory (``cassette_scope`` or ``AGENTICORG_CASSETTE_DIR``),
  which is how a test opts in. Code that never opts in keeps its current
  behaviour, so tests that only inspect how a model is constructed are
  unaffected.

Cassettes are keyed by :func:`request_key`: a SHA-256 over the model id, the
rendered messages (including tool calls and tool results), the bound tool
schemas and the sampling parameters. Any change to a prompt, a tool
definition or tool output therefore misses loudly instead of replaying stale
text. Record and replay are refused outside local and test runtimes.

Both model entry points use this module: LangChain chat models built by
``core.langgraph.llm_factory.create_chat_model`` are wrapped in
:class:`ReplayChatModel`, and ``core.llm.router.LLMRouter`` calls go through
:func:`replay_router_call`. See ``docs/testing/record-replay.md``.
"""

from __future__ import annotations

import contextlib
import contextvars
import enum
import hashlib
import json
import os
import tempfile
from collections.abc import Awaitable, Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, message_to_dict, messages_from_dict
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field

from core.config import is_relaxed_env

MODE_ENV = "AGENTICORG_MODEL_MODE"
CASSETTE_DIR_ENV = "AGENTICORG_CASSETTE_DIR"
CASSETTE_FORMAT = 1
_TRUTHY = frozenset({"1", "true", "yes"})
_PREVIEW_CHARS = 160

_cassette_dir: contextvars.ContextVar[str | None] = contextvars.ContextVar("model_replay_cassette_dir", default=None)


class ModelMode(enum.StrEnum):
    LIVE = "live"
    RECORD = "record"
    REPLAY = "replay"


class ModelModeError(RuntimeError):
    """The requested model mode is invalid or not allowed in this runtime."""


class CassetteError(RuntimeError):
    """A cassette could not be located, read or written."""


class CassetteMissError(CassetteError):
    """Replay found no cassette for a request."""


def _runtime_env() -> str:
    from core.config import settings  # noqa: PLC0415 - read at call time so tests and reloads see changes

    return str(settings.env)


def current_mode(*, hermetic_fallback: bool = False) -> ModelMode:
    """Return the model mode for this call.

    ``hermetic_fallback`` lets a caller that already has a deterministic
    stand-in (the router's fake LLM) keep using it when no mode was set
    explicitly, instead of taking the CI replay default.
    """
    raw = os.getenv(MODE_ENV, "").strip().lower()
    env = _runtime_env()
    relaxed = is_relaxed_env(env)

    if not raw:
        in_ci = os.getenv("CI", "").strip().lower() in _TRUTHY
        opted_in = bool(_cassette_dir.get() or os.getenv(CASSETTE_DIR_ENV))
        if relaxed and in_ci and opted_in and not hermetic_fallback:
            return ModelMode.REPLAY
        return ModelMode.LIVE

    try:
        mode = ModelMode(raw)
    except ValueError as exc:
        raise ModelModeError(f"{MODE_ENV}={raw!r} is not one of: live, record, replay") from exc

    if mode is not ModelMode.LIVE and not relaxed:
        raise ModelModeError(f"{MODE_ENV}={mode} is only allowed in local and test runtimes; runtime is {env!r}")
    return mode


# ── Request identity ────────────────────────────────────────────────────────


def _normalise_message(message: BaseMessage | dict[str, Any]) -> dict[str, Any]:
    if isinstance(message, dict):
        return dict(message)
    if not isinstance(message, BaseMessage):
        raise CassetteError(f"cannot key a request containing {type(message).__name__}")
    out: dict[str, Any] = {"role": message.type, "content": message.content}
    if message.name:
        out["name"] = message.name
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        out["tool_calls"] = [{"name": tc["name"], "args": tc["args"], "id": tc.get("id")} for tc in tool_calls]
    tool_call_id = getattr(message, "tool_call_id", None)
    if tool_call_id:
        out["tool_call_id"] = tool_call_id
    return out


def _canonical(payload: Any) -> str:
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except TypeError as exc:
        raise CassetteError(f"request is not JSON-serialisable and cannot be keyed: {exc}") from exc


def _render(
    model: str,
    messages: Sequence[BaseMessage | dict[str, Any]],
    tools: Sequence[dict[str, Any]],
    params: dict[str, Any],
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [_normalise_message(m) for m in messages],
        "tools": list(tools),
        "params": dict(params),
    }


def request_key(
    model: str,
    messages: Sequence[BaseMessage | dict[str, Any]],
    *,
    tools: Sequence[dict[str, Any]],
    params: dict[str, Any],
) -> str:
    """Stable identity of a model request: ``sha256:<hex>``."""
    rendered = _render(model, messages, tools, params)
    return "sha256:" + hashlib.sha256(_canonical({"v": CASSETTE_FORMAT, **rendered}).encode("utf-8")).hexdigest()


# ── Cassette storage ────────────────────────────────────────────────────────


@contextlib.contextmanager
def cassette_scope(directory: str | os.PathLike[str]) -> Iterator[Path]:
    """Store and look up cassettes in ``directory`` for calls made inside the block."""
    token = _cassette_dir.set(str(directory))
    try:
        yield Path(directory)
    finally:
        _cassette_dir.reset(token)


def _directory() -> Path:
    directory = _cassette_dir.get() or os.getenv(CASSETTE_DIR_ENV)
    if not directory:
        raise CassetteError(f"no cassette directory: wrap the call in cassette_scope(...) or set {CASSETTE_DIR_ENV}")
    return Path(directory)


def _path(directory: Path, key: str) -> Path:
    return directory / f"{key.removeprefix('sha256:')}.json"


def _preview(value: Any) -> str:
    text = value if isinstance(value, str) else _canonical(value)
    return text if len(text) <= _PREVIEW_CHARS else text[:_PREVIEW_CHARS] + "..."


def _nearest_difference(directory: Path, request: dict[str, Any]) -> str:
    """Describe where ``request`` first differs from the closest recorded request."""
    best: tuple[int, str] | None = None
    for candidate in sorted(directory.glob("*.json")):
        try:
            recorded = json.loads(candidate.read_text(encoding="utf-8"))["request"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        ours, theirs = request["messages"], recorded.get("messages", [])
        shared = 0
        while shared < min(len(ours), len(theirs)) and ours[shared] == theirs[shared]:
            shared += 1
        if shared < max(len(ours), len(theirs)):
            ours_part = ours[shared] if shared < len(ours) else "<absent>"
            theirs_part = theirs[shared] if shared < len(theirs) else "<absent>"
            detail = (
                f"differs from the nearest recording at messages[{shared}] ({candidate.name}):\n"
                f"    recorded: {_preview(theirs_part)}\n"
                f"    now:      {_preview(ours_part)}"
            )
        else:
            fields = [f for f in ("model", "tools", "params") if request.get(f) != recorded.get(f)]
            detail = f"matches the messages of {candidate.name} but differs in: {', '.join(fields) or 'format'}"
        if best is None or shared > best[0]:
            best = (shared, detail)
    return best[1] if best else "no cassettes are recorded in that directory"


def _load(key: str, request: dict[str, Any]) -> dict[str, Any]:
    directory = _directory()
    path = _path(directory, key)
    if not path.is_file():
        raise CassetteMissError(
            f"no cassette for model request {key} in {directory}.\n"
            f"  The request {_nearest_difference(directory, request)}\n"
            f"  If the change is intended, re-record deliberately with {MODE_ENV}=record "
            "and review the cassette diff in the pull request."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CassetteError(f"cassette {path} is unreadable: {exc}") from exc
    if not isinstance(data, dict) or data.get("key") != key or "response" not in data:
        raise CassetteError(f"cassette {path} is unreadable: missing response or key mismatch")
    return data


def _save(key: str, request: dict[str, Any], response: dict[str, Any]) -> Path:
    directory = _directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = _path(directory, key)
    body = {"format": CASSETTE_FORMAT, "key": key, "model": request["model"], "request": request, "response": response}
    text = json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".cassette-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return path


def _dump_message(message: BaseMessage) -> dict[str, Any]:
    dumped = message_to_dict(message)
    data = dumped["data"]
    # Provider ids and response metadata change on every call; keep cassettes reviewable.
    data.pop("id", None)
    data["response_metadata"] = {}
    return dumped


def _restore_message(response: dict[str, Any]) -> BaseMessage:
    try:
        return messages_from_dict([response])[0]
    except (KeyError, TypeError, ValueError) as exc:
        raise CassetteError(f"cassette response cannot be restored as a message: {exc}") from exc


# ── LangChain chat model ────────────────────────────────────────────────────


class ReplayChatModel(BaseChatModel):
    """Chat model that records to, or replays from, cassettes.

    The live model is built by ``live_factory`` only when a live call is
    actually made, so replay needs no provider credentials.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_name: str
    temperature: float = 0.1
    max_tokens: int = 4096
    live_factory: Callable[[], BaseChatModel] | None = Field(default=None, exclude=True)
    tool_schemas: list[dict[str, Any]] = Field(default_factory=list)
    bound_tools: list[Any] = Field(default_factory=list, exclude=True)
    tool_choice: str | None = None

    @property
    def _llm_type(self) -> str:
        return "model-replay"

    def bind_tools(  # type: ignore[override]
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> ReplayChatModel:
        return self.model_copy(
            update={
                "tool_schemas": [convert_to_openai_tool(t) for t in tools],
                "bound_tools": list(tools),
                "tool_choice": tool_choice,
            }
        )

    def _request(self, messages: list[BaseMessage], stop: list[str] | None) -> tuple[str, dict[str, Any]]:
        params = {"temperature": self.temperature, "max_tokens": self.max_tokens, "stop": stop}
        key = request_key(self.model_name, messages, tools=self.tool_schemas, params=params)
        return key, _render(self.model_name, messages, self.tool_schemas, params)

    def _live(self) -> Any:
        if self.live_factory is None:
            raise CassetteError(f"no live model is configured for {self.model_name!r}")
        live = self.live_factory()
        if self.bound_tools:
            kwargs = {"tool_choice": self.tool_choice} if self.tool_choice else {}
            live = live.bind_tools(self.bound_tools, **kwargs)
        return live

    @staticmethod
    def _result(message: BaseMessage) -> ChatResult:
        if not isinstance(message, AIMessage):
            raise CassetteError(f"model response is a {type(message).__name__}, expected AIMessage")
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        mode = current_mode()
        if mode is ModelMode.LIVE:
            return self._result(await self._live().ainvoke(messages, stop=stop))
        key, request = self._request(messages, stop)
        if mode is ModelMode.REPLAY:
            return self._result(_restore_message(_load(key, request)["response"]))
        message = await self._live().ainvoke(messages, stop=stop)
        _save(key, request, _dump_message(message))
        return self._result(message)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        mode = current_mode()
        if mode is ModelMode.LIVE:
            return self._result(self._live().invoke(messages, stop=stop))
        key, request = self._request(messages, stop)
        if mode is ModelMode.REPLAY:
            return self._result(_restore_message(_load(key, request)["response"]))
        message = self._live().invoke(messages, stop=stop)
        _save(key, request, _dump_message(message))
        return self._result(message)


# ── LLMRouter completions ───────────────────────────────────────────────────


async def replay_router_call(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    live_call: Callable[[], Awaitable[dict[str, Any]]],
    mode: ModelMode | None = None,
) -> dict[str, Any]:
    """Record or replay one ``LLMRouter`` completion; returns ``LLMResponse`` fields."""
    mode = mode or current_mode()
    if mode is ModelMode.LIVE:
        return await live_call()
    params = {"temperature": temperature, "max_tokens": max_tokens}
    key = request_key(model, messages, tools=[], params=params)
    request = _render(model, messages, [], params)
    if mode is ModelMode.REPLAY:
        response = _load(key, request)["response"]
        if not isinstance(response, dict):
            raise CassetteError(f"cassette for {key} does not hold a router response")
        return response
    response = await live_call()
    _save(key, request, response)
    return response
