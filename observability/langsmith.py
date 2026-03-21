"""LangSmith integration for agent trace logging.

Posts run traces to the LangSmith API so that every agent reasoning cycle,
tool call, and workflow execution is visible in the LangSmith dashboard.

The implementation uses ``httpx`` for async HTTP and is safe to call even when
the ``langsmith_api_key`` is not configured (it becomes a no-op).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from core.config import external_keys

logger = structlog.get_logger()

_LANGSMITH_BASE_URL = "https://api.smith.langchain.com"
_RUN_ENDPOINT = "/api/v1/runs"
_BATCH_ENDPOINT = "/api/v1/runs/batch"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def _headers() -> dict[str, str]:
    return {
        "x-api-key": external_keys.langsmith_api_key,
        "Content-Type": "application/json",
    }


def _is_configured() -> bool:
    return bool(external_keys.langsmith_api_key)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Single-run trace
# ---------------------------------------------------------------------------

async def log_trace(
    agent_id: str,
    run_data: dict[str, Any],
    *,
    run_type: str = "chain",
    parent_run_id: str | None = None,
    tags: list[str] | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> str | None:
    """Log a single run trace to LangSmith.

    Parameters
    ----------
    agent_id:
        The platform agent identifier (stored as metadata).
    run_data:
        Must contain at minimum ``name`` (str), ``inputs`` (dict), and
        optionally ``outputs`` (dict), ``error`` (str).
    run_type:
        One of ``chain``, ``llm``, ``tool``, ``retriever``.
    parent_run_id:
        If this trace is a child span, the parent's run id.
    tags:
        Arbitrary string tags for filtering in the dashboard.
    extra_metadata:
        Additional key-value pairs attached to the run.

    Returns
    -------
    str | None
        The generated run id, or *None* if LangSmith is not configured.
    """
    if not _is_configured():
        return None

    run_id = str(uuid.uuid4())
    now = _utcnow_iso()

    payload: dict[str, Any] = {
        "id": run_id,
        "name": run_data.get("name", f"agent-{agent_id}"),
        "run_type": run_type,
        "inputs": run_data.get("inputs", {}),
        "start_time": run_data.get("start_time", now),
        "session_name": external_keys.langsmith_project,
        "tags": tags or [],
        "extra": {
            "metadata": {
                "agent_id": agent_id,
                "platform": "agenticorg-os",
                **(extra_metadata or {}),
            },
        },
    }

    if parent_run_id:
        payload["parent_run_id"] = parent_run_id

    if "outputs" in run_data:
        payload["outputs"] = run_data["outputs"]
        payload["end_time"] = run_data.get("end_time", now)

    if "error" in run_data:
        payload["error"] = run_data["error"]
        payload["end_time"] = run_data.get("end_time", now)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_LANGSMITH_BASE_URL}{_RUN_ENDPOINT}",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()
            logger.debug("langsmith_trace_logged", run_id=run_id, agent_id=agent_id)
            return run_id
    except httpx.HTTPStatusError as exc:
        logger.error(
            "langsmith_trace_http_error",
            status=exc.response.status_code,
            body=exc.response.text[:500],
            agent_id=agent_id,
        )
        return None
    except httpx.RequestError as exc:
        logger.error(
            "langsmith_trace_request_error",
            error=str(exc),
            agent_id=agent_id,
        )
        return None


# ---------------------------------------------------------------------------
# Update an existing run (e.g. add outputs after completion)
# ---------------------------------------------------------------------------

async def update_run(
    run_id: str,
    *,
    outputs: dict[str, Any] | None = None,
    error: str | None = None,
    end_time: str | None = None,
    tags: list[str] | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> bool:
    """Patch an existing LangSmith run with outputs or error.

    Returns True on success, False otherwise.
    """
    if not _is_configured():
        return False

    patch_data: dict[str, Any] = {
        "end_time": end_time or _utcnow_iso(),
    }
    if outputs is not None:
        patch_data["outputs"] = outputs
    if error is not None:
        patch_data["error"] = error
    if tags is not None:
        patch_data["tags"] = tags
    if extra_metadata is not None:
        patch_data["extra"] = {"metadata": extra_metadata}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.patch(
                f"{_LANGSMITH_BASE_URL}{_RUN_ENDPOINT}/{run_id}",
                headers=_headers(),
                json=patch_data,
            )
            resp.raise_for_status()
            logger.debug("langsmith_run_updated", run_id=run_id)
            return True
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("langsmith_update_error", run_id=run_id, error=str(exc))
        return False


# ---------------------------------------------------------------------------
# Batch trace upload
# ---------------------------------------------------------------------------

async def log_batch(
    traces: list[dict[str, Any]],
    *,
    agent_id: str = "",
) -> int:
    """Post multiple run traces in a single batch request.

    Each item in *traces* follows the same schema as the ``run_data`` dict in
    :func:`log_trace`.

    Returns the number of successfully submitted traces.
    """
    if not _is_configured() or not traces:
        return 0

    now = _utcnow_iso()
    post_items: list[dict[str, Any]] = []
    patch_items: list[dict[str, Any]] = []

    for t in traces:
        run_id = t.get("id", str(uuid.uuid4()))
        item: dict[str, Any] = {
            "id": run_id,
            "name": t.get("name", f"agent-{agent_id}"),
            "run_type": t.get("run_type", "chain"),
            "inputs": t.get("inputs", {}),
            "start_time": t.get("start_time", now),
            "session_name": external_keys.langsmith_project,
            "tags": t.get("tags", []),
            "extra": {
                "metadata": {
                    "agent_id": agent_id,
                    "platform": "agenticorg-os",
                    **t.get("extra_metadata", {}),
                },
            },
        }
        if "outputs" in t:
            item["outputs"] = t["outputs"]
            item["end_time"] = t.get("end_time", now)
        if "error" in t:
            item["error"] = t["error"]
            item["end_time"] = t.get("end_time", now)
        if "parent_run_id" in t:
            item["parent_run_id"] = t["parent_run_id"]

        post_items.append(item)

    payload = {"post": post_items, "patch": patch_items}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_LANGSMITH_BASE_URL}{_BATCH_ENDPOINT}",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()
            logger.debug("langsmith_batch_logged", count=len(post_items))
            return len(post_items)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.error("langsmith_batch_error", error=str(exc), count=len(post_items))
        return 0


# ---------------------------------------------------------------------------
# Workflow-level convenience helpers
# ---------------------------------------------------------------------------

async def log_workflow_run(
    run_id: str,
    workflow_name: str,
    tenant_id: str,
    *,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    error: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    child_runs: list[dict[str, Any]] | None = None,
) -> str | None:
    """Log a full workflow execution as a LangSmith trace with optional children."""
    parent_id = await log_trace(
        agent_id=f"workflow:{workflow_name}",
        run_data={
            "name": f"workflow:{workflow_name}",
            "inputs": inputs or {"run_id": run_id, "tenant_id": tenant_id},
            "outputs": outputs or {},
            **({"error": error} if error else {}),
            **({"start_time": start_time} if start_time else {}),
            **({"end_time": end_time} if end_time else {}),
        },
        run_type="chain",
        tags=["workflow", tenant_id],
        extra_metadata={"run_id": run_id, "tenant_id": tenant_id},
    )

    if parent_id and child_runs:
        for child in child_runs:
            child["parent_run_id"] = parent_id
        await log_batch(child_runs, agent_id=f"workflow:{workflow_name}")

    return parent_id


async def log_agent_reasoning(
    agent_id: str,
    model: str,
    prompt: str,
    completion: str,
    *,
    parent_run_id: str | None = None,
    tokens_used: int = 0,
    latency_ms: float = 0.0,
    confidence: float = 0.0,
) -> str | None:
    """Log a single agent LLM reasoning step."""
    return await log_trace(
        agent_id=agent_id,
        run_data={
            "name": f"agent:{agent_id}:reason",
            "inputs": {"prompt": prompt},
            "outputs": {"completion": completion},
        },
        run_type="llm",
        parent_run_id=parent_run_id,
        tags=["agent", model],
        extra_metadata={
            "model": model,
            "tokens_used": tokens_used,
            "latency_ms": latency_ms,
            "confidence": confidence,
        },
    )


async def log_tool_call(
    agent_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    tool_output: dict[str, Any] | None = None,
    *,
    parent_run_id: str | None = None,
    error: str | None = None,
    latency_ms: float = 0.0,
) -> str | None:
    """Log a tool / connector invocation."""
    run_data: dict[str, Any] = {
        "name": f"tool:{tool_name}",
        "inputs": tool_input,
    }
    if tool_output is not None:
        run_data["outputs"] = tool_output
    if error is not None:
        run_data["error"] = error

    return await log_trace(
        agent_id=agent_id,
        run_data=run_data,
        run_type="tool",
        parent_run_id=parent_run_id,
        tags=["tool", tool_name],
        extra_metadata={"latency_ms": latency_ms},
    )
