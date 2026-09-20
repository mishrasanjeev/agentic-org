# SPDX-License-Identifier: Apache-2.0
"""One guarded model call for a governed case agent.

Governed case agents use the model to write prose only. :func:`call_case_model` sends a versioned
system prompt and a context already rendered by ``build_model_context`` through the standard
LangGraph agent graph with no tools, with:

- the untrusted-content guard on every message (a leak raises and fails the run);
- a pseudonymisation session keyed by the server-generated run id when the tenant's
  ``pseudonymisation.pre_model`` flag is on. If the flag or the map cannot be read, the call is not
  made.

Any other failure - the model is unreachable, times out or answers badly - returns no output and
a reason code, because the agents' deterministic documents are complete without prose. A
record-and-replay cassette miss is re-raised: it is a test error, not an outage.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from auth.grant_enforcement import EnforcementMode
from auth.run_grants import RunGrant
from core.extraction import UntrustedContentLeakError, UntrustedTextRegistry
from core.model_replay import CassetteError

logger = structlog.get_logger()


@dataclass(frozen=True)
class CaseModelResult:
    #: The parsed JSON object the model answered with, or ``None`` when no call succeeded.
    output: Mapping[str, Any] | None
    #: ``""`` on success, else ``pseudonymisation_unavailable`` or ``model_call_failed``.
    failure: str
    pseudonymised: bool
    #: Restores pseudonyms in model text; the identity when pseudonymisation was off.
    restore: Callable[[str], str]


def _identity(text: str) -> str:
    return text


async def call_case_model(
    *,
    agent: str,
    tenant_id: str,
    run_id: str,
    system_prompt: str,
    context: str,
    untrusted: UntrustedTextRegistry,
    llm_model: str = "",
    llm_provider: str | None = None,
    pseudonym_store: Any = None,
) -> CaseModelResult:
    from core.langgraph.agent_graph import build_agent_graph
    from core.pii import pseudonymiser as pseudonymisation

    session = None
    try:
        if await pseudonymisation.pseudonymisation_enabled(tenant_id):
            session = await pseudonymisation.open_session(
                tenant_id, pseudonymisation.case_key(run_id), store=pseudonym_store
            )
            system_prompt = pseudonymisation.with_model_guidance(system_prompt)
    except pseudonymisation.PseudonymisationError as exc:
        logger.warning("case_model_call_skipped", agent=agent, reason="pseudonymisation_unavailable", detail=exc.reason)
        return CaseModelResult(None, "pseudonymisation_unavailable", False, _identity)

    graph = build_agent_graph(
        system_prompt=system_prompt,
        authorized_tools=[],
        llm_model=llm_model,
        confidence_floor=0.0,
        tenant_id=tenant_id or None,
        llm_provider=llm_provider,
        context_guard=untrusted.guard_messages,
        pseudonymiser=session,
        # This graph is deliberately built without tools. Pass an explicit
        # non-enforcing grant rather than the test-only None sentinel required
        # by tool-capable production graphs.
        run_grant=RunGrant(mode=EnforcementMode.OFF, source="case_model_no_tools"),
    )
    state = {
        "messages": [SystemMessage(content=system_prompt), HumanMessage(content=context)],
        "agent_id": agent,
        "agent_type": agent,
        "domain": "compliance",
        "tenant_id": tenant_id,
        "grant_token": "",
        "confidence": 0.0,
        "status": "running",
        "output": {},
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
    }
    restore = session.restore_text if session is not None else _identity
    try:
        final = await graph.compile().ainvoke(state)
    except (UntrustedContentLeakError, CassetteError, asyncio.CancelledError):
        raise
    # enterprise-gate: broad-except-ok reason=model-failure-degrades-to-a-document-without-prose
    except Exception as exc:
        logger.warning("case_model_call_skipped", agent=agent, reason="model_call_failed", error=type(exc).__name__)
        return CaseModelResult(None, "model_call_failed", session is not None, restore)
    output = final.get("output") if isinstance(final, Mapping) else None
    return CaseModelResult(output if isinstance(output, Mapping) else {}, "", session is not None, restore)
