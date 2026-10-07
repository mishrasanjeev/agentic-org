# SPDX-License-Identifier: Apache-2.0
"""The debugging console: breakpoints, step-through of a run's checkpoints and variable inspection.

A run's graph (``core/langgraph/agent_graph.py``) writes a checkpoint after
every node. The console reads that history back as **steps**: which node ran,
what it changed, which node is next, and the state as it stood, with secrets
hidden and values bounded. One value at one step can be inspected in full by
its path (``output.summary``, ``messages.3.content``).

An agent may declare **breakpoints**: nodes a run pauses *before*. A paused
run is a debug session the console steps one node at a time or continues to
the next breakpoint; stepping re-enters the graph exactly as an approval
resume does (``core/langgraph/runner.py``).

Everything here is behind ``AGENTICORG_RUNTIME_DEBUG_CONSOLE_ENABLED`` (off
by default): off, no run pauses at a breakpoint and the console endpoints are
not found. The grant token is never shown; keys that look like secrets are
redacted; a pseudonymised run shows the pseudonyms the model saw.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select

from core.config import settings
from core.langgraph.checkpointer import get_checkpointer
from core.langgraph.thread_ids import thread_belongs_to_tenant

logger = structlog.get_logger()

# The graph's nodes, in the order the console lists them. A breakpoint names one.
NODES: tuple[str, ...] = ("reason", "validate_scopes", "execute_tools", "evaluate", "hitl_gate")
DEBUG_KEY = "debug"  # agent.config[DEBUG_KEY] = {"break_before": [...]}
STATUS_PAUSED = "paused"
MAX_STEPS = 200
VIEW_TEXT = 2_000  # characters of one string in a step's state view
VIEW_ITEMS = 50  # items of one list in a step's state view
INSPECT_BYTES = 64_000  # the most one inspected value returns
STALE_RUNNING_SECONDS = 600  # a step that never reported back no longer blocks the session
HIDDEN_KEYS = frozenset({"grant_token"})
_SECRET_RE = re.compile(r"(token|secret|password|passwd|api_key|apikey|authorization|credential|cookie)", re.I)
_BRANCH_PREFIX = "branch:to:"
_SESSION_STATUSES = ("paused", "running", "completed", "failed")


class DebugError(Exception):
    """A refused console request, with the HTTP status it maps to."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def enabled() -> bool:
    return bool(getattr(settings, "runtime_debug_console_enabled", False))


# ── Breakpoints ────────────────────────────────────────────────────────────────


def parse_breakpoints(value: Any) -> list[str]:
    """The nodes a run pauses before: known names, each once, in graph order."""
    if not isinstance(value, list | tuple):
        raise DebugError(422, "breakpoints_invalid", "break_before must be a list of node names")
    names: set[str] = set()
    for item in value:
        if not isinstance(item, str) or item not in NODES:
            raise DebugError(422, "breakpoint_unknown", f"unknown node; the nodes are {', '.join(NODES)}")
        names.add(item)
    return [node for node in NODES if node in names]


def declared(agent: Any) -> list[str]:
    """The breakpoints an agent declares (``config.debug.break_before``), ignoring what is not a node."""
    config = getattr(agent, "config", None)
    if not isinstance(config, dict):
        config = agent.get("config") if isinstance(agent, dict) else None
    debug = (config or {}).get(DEBUG_KEY) if isinstance(config, dict) else None
    raw = debug.get("break_before") if isinstance(debug, dict) else None
    if not isinstance(raw, list):
        return []
    return [node for node in NODES if node in raw]


def breakpoints_for_run(config: dict[str, Any] | None) -> list[str] | None:
    """What the runner compiles with: the declared breakpoints while the console is on, else nothing."""
    if not enabled():
        return None
    nodes = declared({"config": config or {}})
    return nodes or None


# ── State views ────────────────────────────────────────────────────────────────


def next_nodes(values: dict[str, Any] | None) -> list[str]:
    """The nodes a checkpoint is waiting to run: LangGraph marks each with a ``branch:to:<node>`` channel."""
    names = [key[len(_BRANCH_PREFIX) :] for key in (values or {}) if key.startswith(_BRANCH_PREFIX)]
    return sorted(names, key=lambda name: (NODES.index(name) if name in NODES else len(NODES), name))


def _is_secret_key(key: Any) -> bool:
    return isinstance(key, str) and (key in HIDDEN_KEYS or bool(_SECRET_RE.search(key)))


def redact(value: Any, depth: int = 0) -> Any:
    """``value`` with every secret-looking key replaced, to any depth."""
    if depth > 12:
        return "[deep]"
    if isinstance(value, dict):
        return {
            str(key): ("[redacted]" if _is_secret_key(key) else redact(item, depth + 1)) for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item, depth + 1) for item in value]
    return value


def bounded(value: Any, *, text: int = VIEW_TEXT, items: int = VIEW_ITEMS, depth: int = 0) -> Any:
    """``value`` with long strings cut and long lists shortened, saying so."""
    if depth > 12:
        return "[deep]"
    if isinstance(value, str):
        return value if len(value) <= text else value[:text] + f"… [{len(value) - text} more characters]"
    if isinstance(value, dict):
        return {str(key): bounded(item, text=text, items=items, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, list | tuple):
        shown = [bounded(item, text=text, items=items, depth=depth + 1) for item in list(value)[:items]]
        if len(value) > items:
            shown.append(f"… [{len(value) - items} more items]")
        return shown
    if isinstance(value, int | float | bool) or value is None:
        return value
    return str(value)


def message_view(message: Any, *, text: int = VIEW_TEXT) -> dict[str, Any]:
    """One message of the thread: its type, content, the tools it called and the tokens it used."""
    if isinstance(message, dict):
        kind = str(message.get("type") or message.get("role") or "message")
        content = message.get("content", "")
        tool_calls = message.get("tool_calls") or []
        usage = message.get("usage_metadata") or {}
        name = message.get("name")
    else:
        kind = str(getattr(message, "type", None) or type(message).__name__)
        content = getattr(message, "content", "")
        tool_calls = getattr(message, "tool_calls", None) or []
        usage = getattr(message, "usage_metadata", None) or {}
        name = getattr(message, "name", None)
    if not isinstance(content, str):
        content = json.dumps(content, default=str)
    view: dict[str, Any] = {"type": kind, "content": bounded(content, text=text)}
    if name:
        view["name"] = str(name)
    names = [str(call.get("name") if isinstance(call, dict) else getattr(call, "name", "")) for call in tool_calls]
    if names:
        view["tool_calls"] = names
    total = usage.get("total_tokens") if isinstance(usage, dict) else None
    if isinstance(total, int | float):
        view["tokens"] = int(total)
    return view


def state_values(checkpoint_values: dict[str, Any] | None) -> dict[str, Any]:
    """The agent state in a checkpoint: the input step keeps it under ``__start__``; branch channels are not state."""
    values = dict(checkpoint_values or {})
    start = values.get("__start__")
    if isinstance(start, dict) and len(values) == 1:
        values = dict(start)
    return {key: item for key, item in values.items() if not key.startswith(_BRANCH_PREFIX) and key != "__start__"}


def view(values: dict[str, Any] | None) -> dict[str, Any]:
    """A step's state as the console shows it: secrets hidden, messages summarised, values bounded."""
    shown: dict[str, Any] = {}
    for key, item in state_values(values).items():
        if key in HIDDEN_KEYS:
            continue
        if key == "messages" and isinstance(item, list | tuple):
            messages = [message_view(message) for message in list(item)[-VIEW_ITEMS:]]
            if len(item) > VIEW_ITEMS:
                messages.insert(0, {"type": "…", "content": f"[{len(item) - VIEW_ITEMS} earlier messages]"})
            shown[key] = messages
            continue
        shown[key] = bounded(redact(item))
    return shown


def changed(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    """The state keys whose view differs between two steps, in the order they appear."""
    before = before or {}
    after = after or {}
    keys = list(after) + [key for key in before if key not in after]
    return [key for key in keys if before.get(key) != after.get(key)]


# ── Step-through ───────────────────────────────────────────────────────────────


def _config(thread_id: str, checkpoint_id: str | None = None) -> dict[str, Any]:
    configurable: dict[str, Any] = {"thread_id": thread_id, "checkpoint_ns": ""}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def _own_thread(tenant_id: str | uuid.UUID, thread_id: str) -> None:
    if not thread_belongs_to_tenant(thread_id, tenant_id):
        raise DebugError(403, "thread_tenant_mismatch", "That thread does not belong to this tenant")


async def steps(tenant_id: str | uuid.UUID, thread_id: str, *, limit: int = MAX_STEPS) -> dict[str, Any]:
    """Every checkpoint of a thread as a step, oldest first: the node that ran, what changed, what is next."""
    _own_thread(tenant_id, thread_id)
    checkpointer = await get_checkpointer()
    saved = [item async for item in checkpointer.alist(_config(thread_id), limit=max(1, min(limit, MAX_STEPS)))]
    saved.reverse()
    out: list[dict[str, Any]] = []
    previous_view: dict[str, Any] | None = None
    previous_next: list[str] = []
    pseudonymised = False
    for index, item in enumerate(saved):
        raw = dict(item.checkpoint.get("channel_values") or {})
        metadata = dict(item.metadata or {})
        source = str(metadata.get("source") or "")
        waiting = next_nodes(raw)
        current = view(raw)
        if current.get("pseudonym_case_id"):
            pseudonymised = True
        if source == "input":
            node = ""  # the input itself
        elif not out or out[-1]["source"] == "input":
            node = "__start__"  # the input applied
        else:
            node = ", ".join(previous_next)
        out.append(
            {
                "index": index,
                "checkpoint_id": str((item.config.get("configurable") or {}).get("checkpoint_id") or ""),
                "step": metadata.get("step"),
                "source": source,
                "node": node,
                "next": waiting,
                "changed": changed(previous_view, current),
                "state": current,
            }
        )
        previous_view, previous_next = current, waiting
    last_next = out[-1]["next"] if out else []
    return {
        "thread_id": thread_id,
        "total": len(out),
        "paused": bool(last_next),
        "next": last_next,
        "pseudonymised": pseudonymised,
        "steps": out,
    }


def _walk(value: Any, path: str) -> Any:
    current = value
    for part in [piece for piece in path.split(".") if piece]:
        if _is_secret_key(part):
            raise DebugError(403, "value_hidden", "That value is not shown by the console")
        if isinstance(current, dict):
            if part not in current:
                raise DebugError(404, "path_not_found", f"no {part!r} at this step")
            current = current[part]
        elif isinstance(current, list | tuple):
            try:
                current = list(current)[int(part)]
            except (ValueError, IndexError):
                raise DebugError(404, "path_not_found", f"no item {part!r} at this step") from None
        else:
            raise DebugError(404, "path_not_found", f"nothing below {part!r}")
    return current


def _plain(value: Any) -> Any:
    """Messages and other objects as data, so a value can be walked and serialised."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if hasattr(value, "type") and hasattr(value, "content"):
        out: dict[str, Any] = {"type": str(value.type), "content": value.content}
        for attr in ("name", "tool_calls", "usage_metadata", "tool_call_id"):
            item = getattr(value, attr, None)
            if item:
                out[attr] = _plain(item)
        return out
    return value


async def inspect(tenant_id: str | uuid.UUID, thread_id: str, checkpoint_id: str, path: str = "") -> dict[str, Any]:
    """One value of the state at one step, by its dotted path, in full up to ``INSPECT_BYTES``."""
    _own_thread(tenant_id, thread_id)
    checkpointer = await get_checkpointer()
    saved = await checkpointer.aget_tuple(_config(thread_id, checkpoint_id))
    if saved is None:
        raise DebugError(404, "step_not_found", "No such step on this thread")
    values = {
        key: item
        for key, item in state_values(saved.checkpoint.get("channel_values")).items()
        if key not in HIDDEN_KEYS
    }
    value = redact(_plain(_walk(values, path)))
    text = json.dumps(value, default=str, ensure_ascii=False)
    truncated = len(text.encode("utf-8")) > INSPECT_BYTES
    if truncated:
        value = text[:INSPECT_BYTES] + "…"
    return {
        "thread_id": thread_id,
        "checkpoint_id": checkpoint_id,
        "path": path,
        "value": value,
        "truncated": truncated,
        "bytes": len(text.encode("utf-8")),
    }


# ── The runner's paused result ────────────────────────────────────────────────


async def waiting_nodes(compiled: Any, config: dict[str, Any]) -> list[str]:
    """The nodes a compiled graph's thread is paused before (empty when it finished or cannot be read)."""
    try:
        snapshot = await compiled.aget_state(config)
    # enterprise-gate: broad-except-ok reason=checkpoint-state-read-failure-means-not-paused
    except Exception as exc:
        logger.warning("debug_state_read_failed", error_type=type(exc).__name__)
        return []
    return [str(node) for node in (getattr(snapshot, "next", None) or ())]


def paused_result(
    values: dict[str, Any],
    waiting: list[str],
    *,
    thread_id: str,
    latency_ms: int,
    tokens_used: int,
    cost_usd: float,
) -> dict[str, Any]:
    """What the runner returns for a run paused at a breakpoint: the state so far and where it stopped."""
    log = list(values.get("tool_calls_log") or [])
    return {
        "status": STATUS_PAUSED,
        "paused_before": list(waiting),
        "thread_id": thread_id,
        "output": values.get("output", {}),
        "confidence": values.get("confidence", 0.0),
        "reasoning_trace": list(values.get("reasoning_trace") or []),
        "tool_calls_log": log,
        "tool_calls": log,
        "hitl_trigger": "",
        "error": "",
        "explanation": {},
        "performance": {
            "total_latency_ms": latency_ms,
            "llm_tokens_used": tokens_used,
            "llm_cost_usd": cost_usd,
        },
    }


# ── Debug sessions ────────────────────────────────────────────────────────────


@dataclass
class Claim:
    """A debug session claimed for one step: what the runner needs to re-enter the graph."""

    refusal: str = ""
    status: int = 409
    thread_id: str = ""
    breakpoints: list[str] = field(default_factory=list)
    spec: dict[str, Any] = field(default_factory=dict)
    system_prompt: str = ""


def session_dict(row: Any) -> dict[str, Any]:
    """A session as the console lists it; the resume spec stays server-side."""
    return {
        "id": str(row.id),
        "agent_id": str(row.agent_id),
        "thread_id": row.thread_id,
        "status": row.status,
        "paused_before": list(row.paused_before or []),
        "breakpoints": list(row.breakpoints or []),
        "steps_taken": int(row.steps_taken or 0),
        "last_status": row.last_status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def open_session(
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    thread_id: str,
    paused_before: list[str],
    breakpoints: list[str],
    spec: dict[str, Any],
    created_by: uuid.UUID | None,
) -> None:
    """Record a run paused at a breakpoint so the console can step it."""
    from core.database import get_tenant_session
    from core.models.agent_debug_session import AgentDebugSession

    async with get_tenant_session(tenant_id) as session:
        session.add(
            AgentDebugSession(
                tenant_id=tenant_id,
                agent_id=agent_id,
                thread_id=thread_id,
                status="paused",
                paused_before=list(paused_before),
                breakpoints=list(breakpoints),
                spec=dict(spec),
                created_by=created_by,
            )
        )


async def list_sessions(tenant_id: uuid.UUID, agent_id: uuid.UUID, *, limit: int = 50) -> list[dict[str, Any]]:
    from core.database import get_tenant_session
    from core.models.agent_debug_session import AgentDebugSession

    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(AgentDebugSession)
                    .where(AgentDebugSession.tenant_id == tenant_id, AgentDebugSession.agent_id == agent_id)
                    .order_by(AgentDebugSession.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [session_dict(row) for row in rows]


async def claim_session(tenant_id: uuid.UUID, agent_id: uuid.UUID, thread_id: str) -> Claim:
    """Lock a paused session for one step; a second step while one runs is refused until it is stale."""
    from core.database import get_tenant_session
    from core.models.agent import Agent
    from core.models.agent_debug_session import AgentDebugSession

    if not thread_belongs_to_tenant(thread_id, tenant_id):
        return Claim(refusal="thread_tenant_mismatch", status=403)
    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(AgentDebugSession)
                .where(
                    AgentDebugSession.tenant_id == tenant_id,
                    AgentDebugSession.agent_id == agent_id,
                    AgentDebugSession.thread_id == thread_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return Claim(refusal="session_not_found", status=404)
        stale_after = datetime.now(UTC) - timedelta(seconds=STALE_RUNNING_SECONDS)
        updated = row.updated_at if row.updated_at and row.updated_at.tzinfo else None
        running_stale = row.status == "running" and updated is not None and updated < stale_after
        if row.status != "paused" and not running_stale:
            return Claim(refusal=f"session_{row.status}", status=409)
        agent = (
            await session.execute(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if agent is None:
            return Claim(refusal="agent_not_found", status=404)
        row.status = "running"
        row.updated_at = datetime.now(UTC)
        return Claim(
            thread_id=thread_id,
            breakpoints=list(row.breakpoints or []),
            spec=dict(row.spec or {}),
            system_prompt=str(getattr(agent, "system_prompt_text", "") or ""),
        )


async def release_session(tenant_id: uuid.UUID, agent_id: uuid.UUID, thread_id: str) -> None:
    """Put a claimed session back to paused when its step was refused before the run was re-entered."""
    from core.database import get_tenant_session
    from core.models.agent_debug_session import AgentDebugSession

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(AgentDebugSession)
                .where(
                    AgentDebugSession.tenant_id == tenant_id,
                    AgentDebugSession.agent_id == agent_id,
                    AgentDebugSession.thread_id == thread_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is not None and row.status == "running":
            row.status = "paused"
            row.updated_at = datetime.now(UTC)


async def finish_session(tenant_id: uuid.UUID, agent_id: uuid.UUID, thread_id: str, result: dict[str, Any]) -> dict:
    """Record where a step left the session: paused again, finished, or failed."""
    from core.database import get_tenant_session
    from core.models.agent_debug_session import AgentDebugSession

    status = str(result.get("status") or "failed")
    if status == STATUS_PAUSED:
        state = "paused"
    elif status in ("completed", "hitl_triggered"):
        state = "completed"
    else:
        state = "failed"
    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(AgentDebugSession)
                .where(
                    AgentDebugSession.tenant_id == tenant_id,
                    AgentDebugSession.agent_id == agent_id,
                    AgentDebugSession.thread_id == thread_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return {"status": state, "paused_before": [], "steps_taken": 0}
        row.status = state
        row.paused_before = list(result.get("paused_before") or [])
        row.steps_taken = int(row.steps_taken or 0) + 1
        row.last_status = status[:32]
        row.updated_at = datetime.now(UTC)
        return session_dict(row)
