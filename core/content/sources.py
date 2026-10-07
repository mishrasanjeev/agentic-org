# SPDX-License-Identifier: Apache-2.0
"""Source sets for the content services: inline texts, and approved knowledge-base documents the caller may see."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

MAX_SOURCES = 10
MAX_SOURCE_CHARS = 30_000


@dataclass
class Source:
    id: str
    title: str
    text: str
    origin: str = "inline"  # inline | knowledge

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "origin": self.origin, "chars": len(self.text)}


def inline_sources(items: list[Any] | None) -> list[Source]:
    """Sources given in the request, bounded and with distinct ids."""
    out: list[Source] = []
    seen: set[str] = set()
    for index, item in enumerate(list(items or [])[:MAX_SOURCES]):
        data = item if isinstance(item, dict) else getattr(item, "model_dump", lambda: {})()
        ident = str(data.get("id") or f"s{index + 1}")[:64]
        if ident in seen:
            ident = f"{ident}-{index + 1}"
        seen.add(ident)
        out.append(
            Source(
                id=ident,
                title=str(data.get("title") or ident)[:200],
                text=str(data.get("text") or "")[:MAX_SOURCE_CHARS],
                origin="inline",
            )
        )
    return [source for source in out if source.text.strip()]


async def knowledge_sources(
    tenant_id: uuid.UUID, document_ids: list[str] | None, domains: list[str] | None
) -> list[Source]:
    """Approved knowledge-base documents by id, under the caller's document access, as sources."""
    ids: list[str] = []
    for raw in list(document_ids or [])[:MAX_SOURCES]:
        try:
            ids.append(str(uuid.UUID(str(raw))))
        except ValueError:
            continue
    if not ids:
        return []
    from sqlalchemy import text as sqtext

    from core.database import get_tenant_session
    from core.rag.access import sql_clause

    acl_sql, acl_params = sql_clause(domains)
    async with get_tenant_session(tenant_id) as session:
        rows = (
            await session.execute(
                sqtext(
                    "SELECT d.id, d.title, d.content FROM knowledge_documents d "  # noqa: S608  # nosec B608 — ACL clause from core/rag/access
                    f"WHERE d.tenant_id = :tid AND d.status = 'ready' AND d.id = ANY(:ids){acl_sql}"
                ),
                {"tid": str(tenant_id), "ids": ids, **acl_params},
            )
        ).fetchall()
    found = {str(row[0]): row for row in rows}
    return [
        Source(
            id=ident,
            title=str(found[ident][1] or ident)[:200],
            text=str(found[ident][2] or "")[:MAX_SOURCE_CHARS],
            origin="knowledge",
        )
        for ident in ids
        if ident in found and str(found[ident][2] or "").strip()
    ]


async def combined_sources(
    tenant_id: uuid.UUID, inline: list[Any] | None, document_ids: list[str] | None, domains: list[str] | None
) -> list[Source]:
    sources = inline_sources(inline)
    sources.extend(await knowledge_sources(tenant_id, document_ids, domains))
    return sources[:MAX_SOURCES]


def by_id(sources: list[Source]) -> dict[str, Source]:
    return {source.id: source for source in sources}
