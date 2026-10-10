# SPDX-License-Identifier: Apache-2.0
"""Incremental re-indexing of the knowledge base.

A chunk is stale when its embedding was made by a model other than the one
the tenant embeds with now, or, while graph retrieval is on, when it has no
entity rows yet. While ``AGENTICORG_KNOWLEDGE_REINDEX_ENABLED`` is on,
``POST /knowledge/reindex`` lists the stale chunks of the tenant
(``stale_chunks``: oldest first, bounded, optionally only those created
since a date) and, unless it is a dry run, re-embeds the ones whose model
is stale and records the entities of the ones that lack them (``reindex``).
Nothing is deleted and nothing is re-chunked: a chunk keeps its id, its
text, its provenance and its domain, so citations and access stay valid.
Each call does at most ``MAX_LIMIT`` chunks; the caller repeats until the
count of candidates is zero.

Off, the endpoint is not found and nothing here runs.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from core import spend
from core.config import settings
from core.rag import entities

logger = structlog.get_logger()

MAX_LIMIT = 500
EMBED_BATCH = 32


def enabled() -> bool:
    return bool(settings.knowledge_reindex_enabled)


async def stale_chunks(
    session: Any,
    tenant_id: uuid.UUID,
    *,
    model_name: str,
    since: Any = None,
    limit: int = 100,
    want_entities: bool = False,
) -> list[tuple[Any, ...]]:
    """Ready chunks whose embedding model is not ``model_name`` or, when asked, that have no entities:
    ``(id, content, source, embedding_model, has_entities)``, oldest first."""
    from sqlalchemy import text as sqltext

    conditions = ["d.embedding_model IS DISTINCT FROM :model"]
    if want_entities:
        conditions.append("NOT EXISTS (SELECT 1 FROM knowledge_entities e WHERE e.document_id = d.id)")
    since_sql = " AND d.created_at >= :since" if since is not None else ""
    params: dict[str, Any] = {"tid": str(tenant_id), "model": model_name, "limit": max(1, min(int(limit), MAX_LIMIT))}
    if since is not None:
        params["since"] = since
    rows = (
        await session.execute(
            sqltext(
                "SELECT d.id, d.content, d.source, d.embedding_model, "  # noqa: S608  # nosec B608 — fixed conditions, bound parameters, nothing from the request
                "EXISTS (SELECT 1 FROM knowledge_entities e WHERE e.document_id = d.id) AS has_entities "
                "FROM knowledge_documents d "
                "WHERE d.tenant_id = :tid AND d.status = 'ready' AND d.content IS NOT NULL AND length(d.content) > 0 "
                f"AND ({' OR '.join(conditions)}){since_sql} "
                "ORDER BY d.created_at ASC, d.id ASC LIMIT :limit"
            ),
            params,
        )
    ).fetchall()
    return list(rows)


def counts(rows: list[tuple[Any, ...]], *, model_name: str, want_entities: bool) -> dict[str, int]:
    stale = sum(1 for r in rows if (r[3] or "") != model_name)
    missing = sum(1 for r in rows if want_entities and not r[4]) if want_entities else 0
    return {"candidates": len(rows), "stale_embeddings": stale, "missing_entities": missing}


async def reindex(
    session: Any,
    tenant_id: uuid.UUID,
    rows: list[tuple[Any, ...]],
    *,
    model_name: str,
    column: str,
    embed: Callable[[list[str]], Awaitable[list[list[float]]]],
    want_entities: bool = False,
) -> dict[str, int]:
    """Re-embed the chunks whose model is stale and record the entities of those lacking them."""
    from sqlalchemy import text as sqltext

    if column not in ("embedding", "embedding_bge_m3"):
        raise ValueError("unknown embedding column")
    stale = [r for r in rows if (r[3] or "") != model_name]
    re_embedded = 0
    run_ref = uuid.uuid4().hex  # keys this call's embedding usage, one record per batch (core/spend/metering.py)
    for start in range(0, len(stale), EMBED_BATCH):
        batch = stale[start : start + EMBED_BATCH]
        vectors = await embed([str(r[1] or "") for r in batch])
        if spend.enabled():
            spend.note("embeddings", tenant_id, items=batch, purpose="reindex", run_ref=run_ref, start=start)
        if len(vectors) != len(batch):
            raise RuntimeError("the embedder returned a different number of vectors than chunks")
        for row, vector in zip(batch, vectors, strict=True):
            await session.execute(
                sqltext(
                    f"UPDATE knowledge_documents SET {column} = CAST(:vec AS vector), "  # noqa: S608  # nosec B608 — column is one of two fixed names checked above
                    "embedding_model = :model WHERE id = :id AND tenant_id = :tid"
                ),
                {
                    "vec": "[" + ",".join(f"{float(v):.6f}" for v in vector) + "]",
                    "model": model_name,
                    "id": str(row[0]),
                    "tid": str(tenant_id),
                },
            )
            re_embedded += 1
    indexed = 0
    if want_entities:
        for row in rows:
            if not row[4]:
                indexed += (
                    1 if await entities.index_chunk(session, tenant_id, str(row[2] or ""), str(row[1] or "")) else 0
                )
    logger.info("knowledge_reindex_done", re_embedded=re_embedded, entities_indexed=indexed, candidates=len(rows))
    return {"re_embedded": re_embedded, "entities_indexed": indexed}
