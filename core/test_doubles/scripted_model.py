# SPDX-License-Identifier: Apache-2.0
"""Scripted chat model for testing agent graph mechanics without model text.

Graph behaviour - routing to tools, retries, interrupts and resume - should be
testable without any model output at all. ``ScriptedChatModel`` returns a fixed
sequence of messages, typically tool calls followed by a final structured
answer, and fails loudly when the graph asks for more turns than were
scripted, asks it to call a tool that was never bound, or finishes with steps
left over.

Use the ``scripted_model`` fixture from ``tests/conftest.py``, which installs
the model where agent graphs build theirs. For recorded model output instead,
see ``core/model_replay.py``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field

type Step = AIMessage | Callable[[list[BaseMessage]], AIMessage]


class ScriptExhaustedError(AssertionError):
    """The graph asked the model for more turns than the script has."""


class ScriptMismatchError(AssertionError):
    """A scripted step does not fit how the graph used the model."""


def tool_call(name: str, *, call_id: str | None = None, **args: Any) -> AIMessage:
    """An assistant turn that calls one tool.

    The call id is derived from the tool name and arguments, so it is the same
    on every run and distinct for different arguments.
    """
    if call_id is None:
        digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode("utf-8")).hexdigest()[:8]
        call_id = f"call-{name}-{digest}"
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def final(output: dict[str, Any] | str) -> AIMessage:
    """A final assistant turn; a dict is serialised as the agent's JSON output."""
    content = output if isinstance(output, str) else json.dumps(output, sort_keys=True)
    return AIMessage(content=content)


class _Script:
    """Shared by a model and every copy made by ``bind_tools``."""

    def __init__(self, steps: Sequence[Step]) -> None:
        self.steps = list(steps)
        self.used = 0
        self.calls: list[list[BaseMessage]] = []


class ScriptedChatModel(BaseChatModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    script: _Script = Field(exclude=True)
    bound_tool_names: list[str] = Field(default_factory=list)

    def __init__(self, *, steps: Sequence[Step], **kwargs: Any) -> None:
        super().__init__(script=_Script(steps), **kwargs)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @property
    def remaining(self) -> int:
        return len(self.script.steps) - self.script.used

    @property
    def calls(self) -> list[list[BaseMessage]]:
        """The messages the graph sent on each turn, oldest first."""
        return self.script.calls

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedChatModel:  # type: ignore[override]
        names = [convert_to_openai_tool(t)["function"]["name"] for t in tools]
        return self.model_copy(update={"bound_tool_names": names})

    def assert_consumed(self) -> None:
        if self.remaining:
            noun = "step was" if self.remaining == 1 else "steps were"
            raise ScriptMismatchError(f"{self.remaining} scripted {noun} never used; the graph took fewer turns")

    def _next(self, messages: list[BaseMessage]) -> ChatResult:
        total = len(self.script.steps)
        if self.script.used >= total:
            noun = "step" if total == 1 else "steps"
            raise ScriptExhaustedError(f"the graph asked for turn {total + 1} after all {total} scripted {noun}")
        step = self.script.steps[self.script.used]
        self.script.used += 1
        self.script.calls.append(list(messages))
        message = step(list(messages)) if callable(step) else step
        if not isinstance(message, AIMessage):
            raise ScriptMismatchError(f"scripted step {self.script.used} produced {type(message).__name__}")
        if self.bound_tool_names:
            unknown = [tc["name"] for tc in message.tool_calls if tc["name"] not in self.bound_tool_names]
            if unknown:
                raise ScriptMismatchError(
                    f"scripted step {self.script.used} calls {unknown}, but the graph bound {self.bound_tool_names}"
                )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._next(messages)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._next(messages)
