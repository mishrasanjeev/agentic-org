# SPDX-License-Identifier: Apache-2.0
"""Graph retrieval over the entities found in the knowledge base.

While ``AGENTICORG_KNOWLEDGE_GRAPH_RETRIEVAL_ENABLED`` is on, ingestion
records the entities each chunk mentions (``extract``, ``index_chunk``):
names (capitalised phrases such as ``Reserve Bank of India`` or ``Form
16``), codes (``KYC-2024``, ``ISO 27001``), amounts (``INR 5,000``,
``₹ 1,200``) and dates. The rule is a fixed set of patterns, not a model;
any code with six or more digits in a row (an account, card or identity
number) is never recorded. The entities are the nodes of a graph and two
entities are linked when a chunk mentions both.

At search time (``expand``) the entities named in the query, and the
entities whose names contain a query term, are matched; the entities that
share a chunk with them are their neighbours; and the chunks that mention
any of those are candidates the search fuses with its own hits, so a
question about ``Form 16`` also reaches the chunk that names the ``Income
Tax Department`` and the filing date beside it. Every lookup is tenant
scoped, honours document-level access (``core/rag/access.py``) and reads
ready chunks only, and ``GET /knowledge/graph`` shows the matched
entities, their neighbours and the links between them.

Off, nothing here runs: ingestion writes no entity rows and a search is
exactly what it was.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from core.config import settings
from core.rag import access as knowledge_access
from core.rag.citations import PROVENANCE_COLUMNS, PROVENANCE_JOIN
from core.rag.rerank import terms

logger = structlog.get_logger()

KINDS: tuple[str, ...] = ("name", "code", "amount", "date")
MAX_PER_CHUNK = 50
MAX_ENTITY_CHARS = 200
MAX_QUERY_TERMS = 8
MAX_MATCHED = 10
MAX_NEIGHBOURS = 20
MAX_CHUNKS = 40
MIN_TERM_CHARS = 4

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
    "|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
_NAME = re.compile(
    r"\b[A-Z][A-Za-z'&-]+(?:\s+(?:of|and|for|&)\s+|\s+)(?:[A-Z][A-Za-z'&-]+(?:\s+(?:of|and|for|&)\s+|\s+)?){1,4}"
)
_CODE = re.compile(r"\b(?:[A-Z][A-Za-z]+[-/ ]?\d{1,5}[A-Z0-9-]*|[A-Z]{2,}(?:-[A-Z0-9]{2,})+)\b")
_AMOUNT = re.compile(
    r"(?:₹|Rs\.?|INR|USD|EUR|GBP|\$|€|£)\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:lakhs?|crores?|million|billion|k)\b)?", re.I
)
_DATE = re.compile(
    rf"\b(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}\s+(?:{_MONTHS})\.?\s+\d{{4}}|(?:{_MONTHS})\.?\s+\d{{1,2}},?\s+\d{{4}})\b",
    re.I,
)
_LONG_DIGITS = re.compile(r"\d{6,}")
_STARTERS = frozenset(
    {
        "the",
        "this",
        "these",
        "that",
        "those",
        "a",
        "an",
        "in",
        "on",
        "for",
        "if",
        "when",
        "where",
        "what",
        "how",
        "please",
        "note",
        "see",
        "all",
        "any",
        "each",
        "no",
        "our",
        "your",
        "their",
        "its",
        "it",
        "we",
        "you",
        "they",
    }
)


def enabled() -> bool:
    return bool(settings.knowledge_graph_retrieval_enabled)


@dataclass(frozen=True)
class Entity:
    entity: str
    kind: str
    mentions: int = 1


def _normalise(text: str, kind: str) -> str:
    value = " ".join(str(text).split()).strip(" .,;:")
    if kind == "amount":
        value = value.replace(",", "").replace(" ", "")
    return value.lower()[:MAX_ENTITY_CHARS]


def _name_key(match: str) -> str | None:
    words = match.split()
    while words and words[0].lower() in _STARTERS:
        words = words[1:]
    while words and words[-1].lower() in ("of", "and", "for", "&"):
        words = words[:-1]
    if len(words) < 2:
        return None
    return " ".join(words)


def extract(text: str) -> list[Entity]:
    """The entities a chunk mentions, each with its kind and how often; bounded and never an identity number."""
    found: dict[tuple[str, str], int] = {}
    source = str(text or "")
    for match in _NAME.finditer(source):
        name = _name_key(match.group(0))
        if name:
            key = (_normalise(name, "name"), "name")
            found[key] = found.get(key, 0) + 1
    for match in _CODE.finditer(source):
        code = match.group(0)
        if _LONG_DIGITS.search(code) or code.lower() in _STARTERS:
            continue
        key = (_normalise(code, "code"), "code")
        found[key] = found.get(key, 0) + 1
    for pattern, kind in ((_AMOUNT, "amount"), (_DATE, "date")):
        for match in pattern.finditer(source):
            if _LONG_DIGITS.search(match.group(0)):
                continue
            key = (_normalise(match.group(0), kind), kind)
            found[key] = found.get(key, 0) + 1
    ordered = sorted(found.items(), key=lambda item: (-item[1], item[0][1], item[0][0]))
    return [Entity(entity=k[0], kind=k[1], mentions=n) for k, n in ordered[:MAX_PER_CHUNK] if k[0]]


async def index_chunk(session: Any, tenant_id: uuid.UUID, chunk_source: str, text: str) -> int:
    """Record the entities of one ingested chunk; the chunk row is found by its source. Returns the rows written."""
    from sqlalchemy import text as sqltext

    found = extract(text)
    if not found:
        return 0
    row = (
        await session.execute(
            sqltext("SELECT id FROM knowledge_documents WHERE tenant_id = :tid AND source = :source LIMIT 1"),
            {"tid": str(tenant_id), "source": chunk_source},
        )
    ).fetchone()
    if row is None:
        return 0
    await session.execute(
        sqltext(
            "INSERT INTO knowledge_entities (id, tenant_id, document_id, entity, kind, mentions, created_at) "
            "VALUES (gen_random_uuid(), :tid, :doc, :entity, :kind, :mentions, now()) "
            "ON CONFLICT (document_id, entity) DO NOTHING"
        ),
        [
            {"tid": str(tenant_id), "doc": str(row[0]), "entity": e.entity, "kind": e.kind, "mentions": e.mentions}
            for e in found
        ],
    )
    return len(found)


def query_keys(query: str) -> tuple[list[str], list[str]]:
    """What a query names: the entities found in it, and its longer terms for a name-contains match."""
    keys = [e.entity for e in extract(query)]
    words = []
    for term in terms(query):
        if len(term) >= MIN_TERM_CHARS and term not in words:
            words.append(term)
    return keys[:MAX_MATCHED], words[:MAX_QUERY_TERMS]


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def match_clause(keys: list[str], words: list[str], *, prefix: str = "g") -> tuple[str, dict[str, Any]]:
    """``(e.entity IN (...) OR ' ' || e.entity || ' ' LIKE ...)`` with every value bound.

    Empty when there is nothing to match.
    """
    parts: list[str] = []
    params: dict[str, Any] = {}
    if keys:
        names = []
        for index, key in enumerate(keys):
            params[f"{prefix}_k{index}"] = key
            names.append(f":{prefix}_k{index}")
        parts.append(f"e.entity IN ({', '.join(names)})")
    for index, word in enumerate(words):
        params[f"{prefix}_w{index}"] = f"% {_escape_like(word)} %"
        parts.append(f"' ' || e.entity || ' ' LIKE :{prefix}_w{index} ESCAPE '\\'")
    if not parts:
        return "", {}
    return "(" + " OR ".join(parts) + ")", params


def _in_clause(values: list[str], *, prefix: str) -> tuple[str, dict[str, Any]]:
    params = {f"{prefix}_{index}": value for index, value in enumerate(values)}
    return "(" + ", ".join(f":{key}" for key in params) + ")", params


async def matched_entities(
    session: Any, tenant_id: uuid.UUID, query: str, domains: list[str] | None, *, limit: int = MAX_MATCHED
) -> list[dict[str, Any]]:
    """The entities the query names or whose names contain a query term, with how many ready chunks mention them."""
    from sqlalchemy import text as sqltext

    keys, words = query_keys(query)
    clause, params = match_clause(keys, words)
    if not clause:
        return []
    acl_sql, acl_params = knowledge_access.sql_clause(domains)
    rows = (
        await session.execute(
            sqltext(
                "SELECT e.entity, e.kind, COUNT(DISTINCT e.document_id) AS chunks, SUM(e.mentions) AS mentions "  # noqa: S608  # nosec B608 — clauses from core/rag (fixed column names, bound parameters), nothing from the request
                "FROM knowledge_entities e JOIN knowledge_documents d ON d.id = e.document_id "
                f"WHERE e.tenant_id = :tid AND d.status = 'ready'{acl_sql} AND {clause} "
                "GROUP BY e.entity, e.kind ORDER BY chunks DESC, e.entity ASC LIMIT :limit"
            ),
            {"tid": str(tenant_id), "limit": int(limit), **params, **acl_params},
        )
    ).fetchall()
    return [
        {"entity": r[0], "kind": r[1], "chunks": int(r[2] or 0), "mentions": int(r[3] or 0), "matched": True}
        for r in rows
    ]


async def neighbours(
    session: Any, tenant_id: uuid.UUID, entities: list[str], domains: list[str] | None, *, limit: int = MAX_NEIGHBOURS
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The entities that share a ready chunk with the given ones, and the links (weight = shared chunks)."""
    from sqlalchemy import text as sqltext

    if not entities:
        return [], []
    in_sql, in_params = _in_clause(entities[:MAX_MATCHED], prefix="n")
    acl_sql, acl_params = knowledge_access.sql_clause(domains)
    rows = (
        await session.execute(
            sqltext(
                "SELECT m.entity, n.entity, n.kind, COUNT(DISTINCT n.document_id) AS weight "  # noqa: S608  # nosec B608 — clauses from core/rag (fixed column names, bound parameters), nothing from the request
                "FROM knowledge_entities m "
                "JOIN knowledge_entities n ON n.document_id = m.document_id AND n.tenant_id = m.tenant_id "
                "AND n.entity <> m.entity "
                "JOIN knowledge_documents d ON d.id = n.document_id "
                f"WHERE m.tenant_id = :tid AND m.entity IN {in_sql} AND d.status = 'ready'{acl_sql} "
                "GROUP BY m.entity, n.entity, n.kind ORDER BY weight DESC, n.entity ASC LIMIT :limit"
            ),
            {"tid": str(tenant_id), "limit": int(limit), **in_params, **acl_params},
        )
    ).fetchall()
    found: dict[str, dict[str, Any]] = {}
    edges = []
    for source, target, kind, weight in rows:
        found.setdefault(target, {"entity": target, "kind": kind, "chunks": 0, "mentions": 0, "matched": False})
        found[target]["chunks"] = max(found[target]["chunks"], int(weight or 0))
        edges.append({"source": source, "target": target, "weight": int(weight or 0)})
    return list(found.values()), edges


async def linked_chunks(
    session: Any, tenant_id: uuid.UUID, entities: list[str], domains: list[str] | None, *, limit: int = MAX_CHUNKS
) -> list[tuple[Any, ...]]:
    """Ready chunks that mention any of the entities, most-linked first: ``(id, title, content, source, provenance...,
    linked)``."""
    from sqlalchemy import text as sqltext

    if not entities:
        return []
    in_sql, in_params = _in_clause(entities[: MAX_MATCHED + MAX_NEIGHBOURS], prefix="c")
    acl_sql, acl_params = knowledge_access.sql_clause(domains)
    return list(
        (
            await session.execute(
                sqltext(
                    f"SELECT d.id, d.title, d.content, d.source, {PROVENANCE_COLUMNS}, "  # noqa: S608  # nosec B608 — clauses from core/rag (fixed column names, bound parameters), nothing from the request
                    "COUNT(DISTINCT e.entity) AS linked "
                    f"FROM knowledge_entities e JOIN knowledge_documents d ON d.id = e.document_id{PROVENANCE_JOIN} "
                    f"WHERE e.tenant_id = :tid AND d.status = 'ready'{acl_sql} AND e.entity IN {in_sql} "
                    f"GROUP BY d.id, d.title, d.content, d.source, {PROVENANCE_COLUMNS} "
                    "ORDER BY linked DESC, d.id ASC LIMIT :limit"
                ),
                {"tid": str(tenant_id), "limit": int(limit), **in_params, **acl_params},
            )
        ).fetchall()
    )


async def expand(
    session: Any, tenant_id: uuid.UUID, query: str, domains: list[str] | None, *, limit: int = MAX_CHUNKS
) -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
    """The chunks the graph reaches from a query, and what was matched on the way (for the retrieval trace)."""
    matched = await matched_entities(session, tenant_id, query, domains)
    names = [m["entity"] for m in matched]
    related, edges = await neighbours(session, tenant_id, names, domains)
    reached = names + [r["entity"] for r in related]
    rows = await linked_chunks(session, tenant_id, reached, domains, limit=limit) if reached else []
    detail = {"matched": names, "neighbours": len(related), "edges": len(edges), "chunks": len(rows)}
    return rows, detail
