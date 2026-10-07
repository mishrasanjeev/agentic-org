# SPDX-License-Identifier: Apache-2.0
"""Provenance: where every kept thing came from, which version it is, and the steps that produced it.

A node is one thing the platform keeps or uses, named by its kind and a
stable reference: a source (a URL, a connector object, an upload), a
document, a chunk, an embedding, a transaction record, a transcript, a
finding, a draft, or a model use. A node carries its origin, a version
(a content hash, or the version the source gave) and when it was
observed. A step joins two nodes with what was done between them
(acquire, extract, chunk, embed, transcribe, summarise, detect, draft,
retrieve, generate), the tool that did it and a hash of its parameters,
so any chunk, record or embedding can be traced back to its source, its
version and its processing history. Ingestion notes its chain after the
rows are kept and never fails on it. Everything is tenant scoped under
row-level security and behind ``lineage_enabled`` (default off).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from core.config import settings

logger = structlog.get_logger()

KINDS = ("source", "document", "chunk", "embedding", "record", "transcript", "finding", "draft", "model_use")
STEPS = (
    "acquire",
    "extract",
    "chunk",
    "embed",
    "ingest",
    "transcribe",
    "summarise",
    "detect",
    "draft",
    "retrieve",
    "generate",
)
MAX_HOPS = 8
MAX_NODES = 500
MAX_CHAIN_NODES = 200
MAX_CHAIN_STEPS = 400
MAX_ATTRIBUTES = 4000  # characters of JSON kept on a node or a step
MAX_REF = 500
MAX_VERSION = 80
MAX_TOOL = 128


class LineageError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def enabled() -> bool:
    return bool(getattr(settings, "lineage_enabled", False))


def version_of(data: bytes | str) -> str:
    """A content version: the SHA-256 of the bytes, prefixed and shortened to a label."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:32]


def params_hash(params: Any) -> str:
    """A hash over a step's parameters, so two runs with the same settings are the same step."""
    if not params:
        return ""
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:32]


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _when(value: Any) -> datetime:
    if value is None or value == "":
        return datetime.now(UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise LineageError(422, "node_invalid", f"{value!r} is not a time") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _bounded(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LineageError(422, "node_invalid", f"{name} is an object")
    if len(json.dumps(value, default=str)) > MAX_ATTRIBUTES:
        raise LineageError(422, f"{name}_too_large", f"{name} is at most {MAX_ATTRIBUTES} characters of JSON")
    return dict(value)


def check_node(raw: Any) -> dict[str, Any]:
    """One node as the store keeps it, or why it cannot be."""
    if not isinstance(raw, dict):
        raise LineageError(422, "node_invalid", "each node is an object")
    kind = _text(raw.get("kind"), 32).lower()
    if kind not in KINDS:
        raise LineageError(422, "node_invalid", f"kind is one of {', '.join(KINDS)}")
    ref = _text(raw.get("ref"), MAX_REF)
    if not ref:
        raise LineageError(422, "node_invalid", "ref is required")
    return {
        "kind": kind,
        "ref": ref,
        "source": _text(raw.get("source"), MAX_REF),
        "version": _text(raw.get("version"), MAX_VERSION),
        "observed_at": _when(raw.get("observed_at")),
        "attributes": _bounded(raw.get("attributes"), "attributes"),
    }


def check_step(raw: Any, count: int) -> dict[str, Any]:
    """One step between two nodes of a chain, named by their positions in it."""
    if not isinstance(raw, dict):
        raise LineageError(422, "step_invalid", "each step is an object")
    try:
        source, target = int(raw.get("from")), int(raw.get("to"))
    except (TypeError, ValueError):
        raise LineageError(422, "step_invalid", "from and to are positions in nodes") from None
    if not (0 <= source < count and 0 <= target < count) or source == target:
        raise LineageError(422, "step_invalid", "from and to are two different positions in nodes")
    step = _text(raw.get("step"), 32).lower()
    if step not in STEPS:
        raise LineageError(422, "step_invalid", f"step is one of {', '.join(STEPS)}")
    return {
        "from": source,
        "to": target,
        "step": step,
        "tool": _text(raw.get("tool"), MAX_TOOL),
        "params_hash": params_hash(raw.get("params")) or _text(raw.get("params_hash"), 64),
        "details": _bounded(raw.get("details"), "details"),
        "at": _when(raw.get("at")),
    }


def node_key(node: dict[str, Any]) -> dict[str, str]:
    return {"kind": str(node.get("kind")), "ref": str(node.get("ref")), "version": str(node.get("version") or "")}


def _node_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "ref": row.ref,
        "source": row.source or "",
        "version": row.version or "",
        "observed_at": row.observed_at.isoformat() if row.observed_at else None,
        "attributes": dict(row.attributes or {}),
    }


def _step_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "from_node": str(row.from_node),
        "to_node": str(row.to_node),
        "step": row.step,
        "tool": row.tool or "",
        "params_hash": row.params_hash or "",
        "details": dict(row.details or {}),
        "at": row.at.isoformat() if row.at else None,
    }


async def _note(session: Any, tenant_id: uuid.UUID, node: dict[str, Any]) -> Any:
    """The node under its key (kind, ref, version), kept once."""
    from core.models.lineage import LineageNode

    existing = (
        (
            await session.execute(
                select(LineageNode).where(
                    LineageNode.tenant_id == tenant_id,
                    LineageNode.kind == node["kind"],
                    LineageNode.ref == node["ref"],
                    LineageNode.version == node["version"],
                )
            )
        )
        .scalars()
        .all()
    )
    if existing:
        return existing[0]
    row = LineageNode(tenant_id=tenant_id, **node)
    session.add(row)
    await session.flush()
    return row


async def _link(session: Any, tenant_id: uuid.UUID, from_row: Any, to_row: Any, step: dict[str, Any]) -> Any:
    """The step between two nodes, kept once under (from, to, step)."""
    from core.models.lineage import LineageStep

    existing = (
        (
            await session.execute(
                select(LineageStep).where(
                    LineageStep.tenant_id == tenant_id,
                    LineageStep.from_node == from_row.id,
                    LineageStep.to_node == to_row.id,
                    LineageStep.step == step["step"],
                )
            )
        )
        .scalars()
        .all()
    )
    if existing:
        return existing[0]
    row = LineageStep(
        tenant_id=tenant_id,
        from_node=from_row.id,
        to_node=to_row.id,
        step=step["step"],
        tool=step["tool"],
        params_hash=step["params_hash"],
        details=step["details"],
        at=step["at"],
    )
    session.add(row)
    return row


async def record_chain(tenant_id: uuid.UUID, nodes: list[Any], steps: list[Any] | None = None) -> dict[str, Any]:
    """Note a chain: the nodes, then the steps between them by position. Idempotent under the keys."""
    from core.database import get_tenant_session

    if not isinstance(nodes, list) or not nodes or len(nodes) > MAX_CHAIN_NODES:
        raise LineageError(422, "chain_invalid", f"nodes is a list of 1 to {MAX_CHAIN_NODES} nodes")
    steps = steps or []
    if not isinstance(steps, list) or len(steps) > MAX_CHAIN_STEPS:
        raise LineageError(422, "chain_invalid", f"steps is a list of at most {MAX_CHAIN_STEPS} steps")
    checked_nodes = [check_node(item) for item in nodes]
    checked_steps = [check_step(item, len(checked_nodes)) for item in steps]
    async with get_tenant_session(tenant_id) as session:
        rows = [await _note(session, tenant_id, node) for node in checked_nodes]
        for step in checked_steps:
            await _link(session, tenant_id, rows[step["from"]], rows[step["to"]], step)
        await session.flush()
    logger.info("lineage_chain_recorded", nodes=len(rows), steps=len(checked_steps))
    return {"nodes": [_node_dict(row) for row in rows], "steps": len(checked_steps)}


async def versions(tenant_id: uuid.UUID, kind: str, ref: str) -> list[dict[str, Any]]:
    """Every version kept of one thing, newest first."""
    from core.database import get_tenant_session
    from core.models.lineage import LineageNode

    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(LineageNode).where(
                        LineageNode.tenant_id == tenant_id,
                        LineageNode.kind == _text(kind, 32).lower(),
                        LineageNode.ref == _text(ref, MAX_REF),
                    )
                )
            )
            .scalars()
            .all()
        )
    found = [_node_dict(row) for row in rows]
    found.sort(key=lambda n: n["observed_at"] or "", reverse=True)
    return found


async def trace(
    tenant_id: uuid.UUID,
    kind: str,
    ref: str,
    *,
    direction: str = "upstream",
    hops: int = MAX_HOPS,
    version: str | None = None,
) -> dict[str, Any]:
    """The nodes and steps around one thing: upstream to its sources, downstream to what was made of it, or both."""
    from core.database import get_tenant_session
    from core.models.lineage import LineageNode, LineageStep

    if direction not in ("upstream", "downstream", "both"):
        raise LineageError(422, "direction_unknown", "direction is upstream, downstream or both")
    hops = max(1, min(int(hops), MAX_HOPS))
    kind = _text(kind, 32).lower()
    ref = _text(ref, MAX_REF)
    async with get_tenant_session(tenant_id) as session:
        candidates = (
            (
                await session.execute(
                    select(LineageNode).where(
                        LineageNode.tenant_id == tenant_id, LineageNode.kind == kind, LineageNode.ref == ref
                    )
                )
            )
            .scalars()
            .all()
        )
        if version is not None:
            candidates = [row for row in candidates if (row.version or "") == _text(version, MAX_VERSION)]
        if not candidates:
            raise LineageError(404, "node_unknown", "no provenance is kept for this thing")
        root = sorted(candidates, key=lambda row: row.observed_at or datetime.min.replace(tzinfo=UTC), reverse=True)[0]
        nodes: dict[uuid.UUID, Any] = {root.id: root}
        steps: dict[uuid.UUID, Any] = {}
        truncated = False
        for way in ("upstream", "downstream") if direction == "both" else (direction,):
            frontier = [root.id]
            for _hop in range(hops):
                near = LineageStep.to_node if way == "upstream" else LineageStep.from_node
                found = (
                    (
                        await session.execute(
                            select(LineageStep).where(LineageStep.tenant_id == tenant_id, near.in_(frontier))
                        )
                    )
                    .scalars()
                    .all()
                )
                next_ids: list[uuid.UUID] = []
                for step in found:
                    steps[step.id] = step
                    other = step.from_node if way == "upstream" else step.to_node
                    if other not in nodes and other not in next_ids:
                        next_ids.append(other)
                if not next_ids:
                    break
                if len(nodes) + len(next_ids) > MAX_NODES:
                    truncated = True
                    next_ids = next_ids[: max(0, MAX_NODES - len(nodes))]
                if next_ids:
                    rows = (
                        (
                            await session.execute(
                                select(LineageNode).where(
                                    LineageNode.tenant_id == tenant_id, LineageNode.id.in_(next_ids)
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    for row in rows:
                        nodes[row.id] = row
                if truncated:
                    break
                frontier = next_ids
    known = set(nodes)
    return {
        "root": _node_dict(root),
        "direction": direction,
        "hops": hops,
        "nodes": sorted((_node_dict(row) for row in nodes.values()), key=lambda n: (n["observed_at"] or "", n["ref"])),
        "steps": sorted(
            (_step_dict(step) for step in steps.values() if step.from_node in known and step.to_node in known),
            key=lambda s: (s["at"] or "", s["step"]),
        ),
        "truncated": truncated,
    }


async def describe(tenant_id: uuid.UUID, kind: str, ref: str, *, version: str | None = None) -> dict[str, Any]:
    """The provenance of one thing: its versions, its sources and the processing history back to them."""
    found = await trace(tenant_id, kind, ref, direction="upstream", version=version)
    by_id = {node["id"]: node for node in found["nodes"]}
    root = found["root"]
    sources = [node for node in found["nodes"] if node["kind"] == "source"]
    if not sources and root["source"]:
        sources = [{"kind": "source", "ref": root["source"], "version": "", "declared": True}]
    history = [
        {
            "step": step["step"],
            "tool": step["tool"],
            "params_hash": step["params_hash"],
            "at": step["at"],
            "from": node_key(by_id[step["from_node"]]),
            "to": node_key(by_id[step["to_node"]]),
        }
        for step in found["steps"]
    ]
    return {
        "node": root,
        "versions": await versions(tenant_id, kind, ref),
        "sources": sources,
        "history": history,
        "complete": any(node["kind"] == "source" for node in found["nodes"]),
        "truncated": found["truncated"],
    }


def _chains(nodes: list[dict[str, Any]], steps: list[dict[str, Any]], *, shared: int) -> list[tuple[list, list]]:
    """A long chain split into chains the store accepts; the first ``shared`` nodes repeat in each (kept once)."""
    head = nodes[:shared]
    rest = nodes[shared:]
    room = MAX_CHAIN_NODES - shared
    out: list[tuple[list, list]] = []
    for first in range(0, max(1, len(rest)), room):
        window = list(range(shared + first, min(shared + first + room, len(nodes))))
        keep = set(range(shared)) | set(window)
        shift = {
            position: (position if position < shared else shared + position - (shared + first)) for position in keep
        }
        part_steps = [
            dict(step, **{"from": shift[step["from"]], "to": shift[step["to"]]})
            for step in steps
            if step["from"] in keep and step["to"] in keep
        ]
        out.append((head + [nodes[position] for position in window], part_steps))
    return out


async def on_ingest(
    tenant_id: uuid.UUID,
    *,
    source: str,
    stream: bytes,
    extraction_method: str,
    mime_type: str,
    chunks: list[tuple[str, str]],
    embedding_model: str,
    dimensions: int,
) -> None:
    """Ingestion's chain: the source, the document, every chunk and its embedding. Never raises."""
    if not enabled():
        return
    try:
        document_version = version_of(stream)
        nodes: list[dict[str, Any]] = [
            {"kind": "source", "ref": source, "source": source, "version": document_version},
            {
                "kind": "document",
                "ref": source,
                "source": source,
                "version": document_version,
                "attributes": {"mime_type": mime_type, "chunks": len(chunks)},
            },
        ]
        steps: list[dict[str, Any]] = [{"from": 0, "to": 1, "step": "extract", "tool": extraction_method}]
        for canonical, dedup in chunks:
            position = len(nodes)
            nodes.append({"kind": "chunk", "ref": canonical, "source": source, "version": dedup})
            steps.append({"from": 1, "to": position, "step": "chunk", "tool": "core.rag.chunking"})
            nodes.append(
                {
                    "kind": "embedding",
                    "ref": canonical,
                    "source": source,
                    "version": embedding_model,
                    "attributes": {"dimensions": dimensions},
                }
            )
            steps.append({"from": position, "to": position + 1, "step": "embed", "tool": embedding_model})
        for part_nodes, part_steps in _chains(nodes, steps, shared=2):
            await record_chain(tenant_id, part_nodes, part_steps)
    # enterprise-gate: broad-except-ok reason=lineage-nonfatal-logged-primary-operation-unchanged
    except Exception:
        logger.warning("lineage_ingest_not_recorded", exc_info=True)


async def on_records(tenant_id: uuid.UUID, *, source: str, records: list[dict[str, Any]]) -> None:
    """A batch of transaction records: each acquired from its source, versioned by its content. Never raises."""
    if not enabled() or not records:
        return
    try:
        nodes: list[dict[str, Any]] = [{"kind": "source", "ref": source, "source": source}]
        steps: list[dict[str, Any]] = []
        for record in records:
            nodes.append(
                {
                    "kind": "record",
                    "ref": str(record.get("record_ref")),
                    "source": source,
                    "version": version_of(json.dumps(record, sort_keys=True, default=str)),
                    "observed_at": record.get("booked_at"),
                }
            )
            steps.append({"from": 0, "to": len(nodes) - 1, "step": "acquire", "tool": "core.txn.records"})
        for part_nodes, part_steps in _chains(nodes, steps, shared=1):
            await record_chain(tenant_id, part_nodes, part_steps)
    # enterprise-gate: broad-except-ok reason=lineage-nonfatal-logged-primary-operation-unchanged
    except Exception:
        logger.warning("lineage_records_not_recorded", exc_info=True)
