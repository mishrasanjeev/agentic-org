# SPDX-License-Identifier: Apache-2.0
"""Guardrail hooks at the platform's call sites.

The reasoning node passes the newest message of a turn through the ``input``
stage before the model sees it and the model's answer through the ``output``
stage before it travels on; retrieved documents pass the ``retrieval`` stage
before they enter a context; a tool call's arguments pass the ``action``
stage before the connector is called. Each hook resolves the tenant, agent,
use case and correlation id from the run's routing decision when the caller
does not name them, so the engine attributes every outcome to the call that
produced it.

The hooks are behind ``guardrails_hooks_enabled`` (off by default): on, every
stage is evaluated, in flag-only mode until ``guardrails.enforce`` is on for
the tenant. Off, the hooks return what they were given and read nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog

from core.config import settings
from core.governance.guardrails.engine import evaluate
from core.governance.guardrails.schema import GuardrailBlocked, GuardrailResult

logger = structlog.get_logger()


@dataclass(frozen=True)
class RunScope:
    tenant_id: str | None
    agent_id: str | None
    use_case: str | None
    correlation_id: str | None


def hooks_enabled() -> bool:
    return bool(settings.guardrails_hooks_enabled)


def run_scope(tenant_id: str | None = None, agent_id: str | None = None, use_case: str | None = None) -> RunScope:
    """The call's scope: what the caller names, else the run's routing decision."""
    from core.governance.model_gateway import current_route

    route = current_route()
    decision = route.decision if route is not None else None
    return RunScope(
        tenant_id=str(tenant_id or getattr(decision, "tenant_id", None) or "") or None,
        agent_id=str(agent_id or (route.agent_id if route is not None else "") or "") or None,
        use_case=use_case or (route.use_case if route is not None else None) or None,
        correlation_id=str(getattr(decision, "correlation_id", None) or "") or None,
    )


async def guard_text(
    stage: str,
    text: str,
    *,
    tenant_id: str | None = None,
    agent_id: str | None = None,
    use_case: str | None = None,
    context: list[str] | None = None,
    user_input: list[str] | None = None,
) -> GuardrailResult | None:
    """Run the stage's rules over ``text``; None when the hooks are off, the text is empty or no tenant is known."""
    if not hooks_enabled() or not text:
        return None
    scope = run_scope(tenant_id, agent_id, use_case)
    if scope.tenant_id is None:
        return None
    return await evaluate(
        stage,
        text,
        tenant_id=scope.tenant_id,
        agent_id=scope.agent_id,
        use_case=scope.use_case,
        correlation_id=scope.correlation_id,
        # Only a call that has a context names one, so every other stage's evaluation is as it was.
        **({"context": context, "user_input": user_input} if context is not None or user_input is not None else {}),
    )


def run_context(messages: list[Any]) -> tuple[list[str], list[str]]:
    """What a run's conversation holds for the grounding check: the tool results, and what the user wrote."""
    from langchain_core.messages import HumanMessage, ToolMessage

    retrieved: list[str] = []
    written: list[str] = []
    for message in messages or []:
        text = _content_text(message)
        if not text:
            continue
        if isinstance(message, ToolMessage):
            retrieved.append(text)
        elif isinstance(message, HumanMessage):
            written.append(text)
    return retrieved, written


def _content_text(message: Any) -> str | None:
    content = getattr(message, "content", None)
    return content if isinstance(content, str) else None


def _with_content(message: Any, text: str) -> Any:
    copy = getattr(message, "model_copy", None)
    if callable(copy):
        return copy(update={"content": text})
    message.content = text
    return message


async def guard_input_messages(
    messages: list[Any], *, tenant_id: str | None = None, agent_id: str | None = None
) -> list[Any]:
    """The newest human or tool message of the turn passes the input stage; a transform replaces its content."""
    if not hooks_enabled() or not messages:
        return messages
    from langchain_core.messages import HumanMessage, ToolMessage

    last = messages[-1]
    if not isinstance(last, HumanMessage | ToolMessage):
        return messages
    text = _content_text(last)
    if not text:
        return messages
    result = await guard_text("input", text, tenant_id=tenant_id, agent_id=agent_id)
    if result is None or result.text == text:
        return messages
    return [*messages[:-1], _with_content(last, result.text)]


async def guard_output_message(
    message: Any,
    *,
    tenant_id: str | None = None,
    agent_id: str | None = None,
    messages: list[Any] | None = None,
) -> Any:
    """The model's answer passes the output stage; a transform replaces its content, tool calls untouched.

    ``messages`` is the conversation the answer was given to: its tool
    results and the user's own words are the context a grounding rule holds
    the answer against.
    """
    if not hooks_enabled():
        return message
    from langchain_core.messages import AIMessage

    if not isinstance(message, AIMessage):
        return message
    text = _content_text(message)
    if not text:
        return message
    context, user_input = run_context(messages or [])
    result = await guard_text(
        "output", text, tenant_id=tenant_id, agent_id=agent_id, context=context, user_input=user_input
    )
    if result is None or result.text == text:
        return message
    return _with_content(message, result.text)


async def guard_retrieval_texts(
    texts: list[str], *, tenant_id: str | None = None, agent_id: str | None = None, use_case: str | None = None
) -> list[str | None]:
    """Each retrieved text passes the retrieval stage: a blocked one is withheld (None), a transformed one replaced."""
    if not hooks_enabled():
        return list(texts)
    out: list[str | None] = []
    for text in texts:
        try:
            result = await guard_text("retrieval", text, tenant_id=tenant_id, agent_id=agent_id, use_case=use_case)
        except GuardrailBlocked as exc:
            logger.warning("guardrail_retrieval_withheld", rule=exc.rule_name, correlation_id=exc.correlation_id)
            out.append(None)
            continue
        out.append(text if result is None else result.text)
    return out


async def guard_action(
    connector: str, tool: str, params: dict[str, Any], *, tenant_id: str | None = None, agent_id: str | None = None
) -> None:
    """A tool call's arguments pass the action stage: flagged or blocked, never transformed."""
    if not hooks_enabled():
        return
    text = f"{connector}:{tool} {json.dumps(params or {}, sort_keys=True, default=str)}"
    await guard_text("action", text, tenant_id=tenant_id, agent_id=agent_id)
