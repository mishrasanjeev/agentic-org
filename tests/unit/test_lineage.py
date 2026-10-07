# SPDX-License-Identifier: Apache-2.0
"""Provenance and lineage, part 1: nodes and steps, chains kept once, traces and descriptions, the hooks, the routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import operators
from sqlalchemy.sql.dml import Insert
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, Grouping

from api.v1 import lineage as api
from core.config import settings
from core.lineage import provenance
from core.lineage.provenance import LineageError

TENANT = uuid.uuid4()
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


def _matches(clause, row) -> bool:
    """A where clause of equalities and IN lists, evaluated against one row."""
    if clause is None:
        return True
    if isinstance(clause, BooleanClauseList):
        return all(_matches(part, row) for part in clause.clauses)
    if isinstance(clause, Grouping):
        return _matches(clause.element, row)
    if isinstance(clause, BinaryExpression):
        attr = getattr(row, clause.left.name)
        if clause.operator is operators.eq:
            return attr == clause.right.value
        if clause.operator is operators.in_op:
            return attr in list(clause.right.value)
    raise NotImplementedError(str(clause))


class _Session:
    def __init__(self):
        self.rows = []
        self.queries = 0

    async def execute(self, statement):
        self.queries += 1
        if isinstance(statement, Insert):
            # an insert that does nothing on conflict: the row is added once under its unique key
            from core.models.lineage import LineageNode, LineageStep

            table = statement.table.name
            values = statement.compile(dialect=postgresql.dialect()).params
            model, key = {
                "lineage_nodes": (LineageNode, ("tenant_id", "kind", "ref", "version")),
                "lineage_steps": (LineageStep, ("tenant_id", "from_node", "to_node", "step")),
            }[table]
            assert statement._post_values_clause is not None  # on_conflict_do_nothing
            if not any(r.__tablename__ == table and all(getattr(r, c) == values[c] for c in key) for r in self.rows):
                self.rows.append(model(**values))
            return _Result([])
        table = statement.get_final_froms()[0].name
        return _Result([r for r in self.rows if r.__tablename__ == table and _matches(statement.whereclause, r)])

    def add(self, row):
        row.id = row.id or uuid.uuid4()
        self.rows.append(row)

    async def flush(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _use(monkeypatch, session):
    import core.database

    monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: session)
    monkeypatch.setattr(settings, "lineage_enabled", True)


def node(kind, ref, version="", **over):
    base = {"kind": kind, "ref": ref, "source": "upload://a.pdf", "version": version, "observed_at": T0.isoformat()}
    base.update(over)
    return base


CHAIN_NODES = [
    node("source", "upload://a.pdf", "sha256:1"),
    node("document", "upload://a.pdf", "sha256:1", observed_at=(T0 + timedelta(seconds=1)).isoformat()),
    node("chunk", "upload://a.pdf#chunk1-ab", "ab", observed_at=(T0 + timedelta(seconds=2)).isoformat()),
    node("embedding", "upload://a.pdf#chunk1-ab", "openai/small", observed_at=(T0 + timedelta(seconds=3)).isoformat()),
]
CHAIN_STEPS = [
    {"from": 0, "to": 1, "step": "extract", "tool": "pypdfium2", "at": (T0 + timedelta(seconds=1)).isoformat()},
    {"from": 1, "to": 2, "step": "chunk", "tool": "core.rag.chunking", "at": (T0 + timedelta(seconds=2)).isoformat()},
    {"from": 2, "to": 3, "step": "embed", "tool": "openai/small", "at": (T0 + timedelta(seconds=3)).isoformat()},
]


class TestChecks:
    def test_versions_and_parameter_hashes_are_stable_labels(self):
        assert provenance.version_of(b"abc") == provenance.version_of("abc")
        assert provenance.version_of(b"abc").startswith("sha256:") and len(provenance.version_of(b"abc")) == 39
        assert provenance.params_hash({"b": 1, "a": 2}) == provenance.params_hash({"a": 2, "b": 1})
        assert provenance.params_hash(None) == "" and len(provenance.params_hash({"x": 1})) == 32

    def test_a_node_is_checked_and_bounded(self):
        item = provenance.check_node(
            {"kind": "Chunk", "ref": " r ", "version": "v", "observed_at": "2026-10-01T09:00:00Z"}
        )
        assert item["kind"] == "chunk" and item["ref"] == "r" and item["observed_at"] == T0 and item["attributes"] == {}
        assert provenance.check_node({"kind": "source", "ref": "x"})["observed_at"].tzinfo is not None
        for bad in (
            {"kind": "thing", "ref": "x"},
            {"kind": "source"},
            "x",
            {"kind": "source", "ref": "x", "observed_at": "soon"},
        ):
            with pytest.raises(LineageError) as refused:
                provenance.check_node(bad)
            assert refused.value.status == 422
        with pytest.raises(LineageError) as refused:
            provenance.check_node(
                {"kind": "source", "ref": "x", "attributes": {"blob": "x" * provenance.MAX_ATTRIBUTES}}
            )
        assert refused.value.code == "attributes_too_large"
        # an identifier is refused when oversized, never cut
        for oversized in (
            {"kind": "chunk", "ref": "r" * (provenance.MAX_REF + 1)},
            {"kind": "chunk", "ref": "r", "version": "v" * 81},
        ):
            with pytest.raises(LineageError) as refused:
                provenance.check_node(oversized)
            assert refused.value.code == "node_invalid" and "at most" in refused.value.message
        assert (
            len(provenance.check_node({"kind": "chunk", "ref": "r", "source": "s" * 600})["source"])
            == provenance.MAX_REF
        )

    def test_a_step_names_two_positions_and_a_known_step(self):
        step = provenance.check_step({"from": 0, "to": 1, "step": "Embed", "tool": "m", "params": {"dims": 3}}, 2)
        assert step["step"] == "embed" and len(step["params_hash"]) == 32 and step["details"] == {}
        for bad in (
            {"from": 0, "to": 0, "step": "embed"},
            {"from": 0, "to": 5, "step": "embed"},
            {"from": "a", "to": 1},
        ):
            with pytest.raises(LineageError):
                provenance.check_step(bad, 2)
        with pytest.raises(LineageError) as refused:
            provenance.check_step({"from": 0, "to": 1, "step": "teleport"}, 2)
        assert refused.value.code == "step_invalid"


class TestStore:
    @pytest.mark.asyncio
    async def test_a_chain_is_kept_once_under_its_keys(self, monkeypatch):
        session = _Session()
        _use(monkeypatch, session)
        first = await provenance.record_chain(TENANT, CHAIN_NODES, CHAIN_STEPS)
        assert len(first["nodes"]) == 4 and first["steps"] == 3 and len(session.rows) == 7
        again = await provenance.record_chain(TENANT, CHAIN_NODES, CHAIN_STEPS)
        assert [n["id"] for n in again["nodes"]] == [n["id"] for n in first["nodes"]] and len(session.rows) == 7
        # a new version of the document is a new node beside the old one
        later = (T0 + timedelta(days=1)).isoformat()
        await provenance.record_chain(TENANT, [node("document", "upload://a.pdf", "sha256:2", observed_at=later)])
        assert len(session.rows) == 8
        found = await provenance.versions(TENANT, "document", "upload://a.pdf")
        assert [v["version"] for v in found] == ["sha256:2", "sha256:1"]  # newest first
        with pytest.raises(LineageError):
            await provenance.record_chain(TENANT, [])
        with pytest.raises(LineageError):
            await provenance.record_chain(TENANT, [node("source", "x")], [{}] * (provenance.MAX_CHAIN_STEPS + 1))

    @pytest.mark.asyncio
    async def test_a_trace_walks_upstream_downstream_or_both_within_the_bounds(self, monkeypatch):
        session = _Session()
        _use(monkeypatch, session)
        await provenance.record_chain(TENANT, CHAIN_NODES, CHAIN_STEPS)
        up = await provenance.trace(TENANT, "embedding", "upload://a.pdf#chunk1-ab")
        assert up["root"]["kind"] == "embedding" and [n["kind"] for n in up["nodes"]] == [
            "source",
            "document",
            "chunk",
            "embedding",
        ]
        assert [s["step"] for s in up["steps"]] == ["extract", "chunk", "embed"] and up["truncated"] is False
        down = await provenance.trace(TENANT, "source", "upload://a.pdf", direction="downstream", hops=1)
        assert [n["kind"] for n in down["nodes"]] == ["source", "document"] and len(down["steps"]) == 1
        assert down["truncated"] is True  # cut at the hop bound with lineage beyond it
        full = await provenance.trace(TENANT, "source", "upload://a.pdf", direction="downstream", hops=3)
        assert len(full["nodes"]) == 4 and full["truncated"] is False  # the bound was enough
        tip = await provenance.trace(TENANT, "embedding", "upload://a.pdf#chunk1-ab", direction="downstream", hops=1)
        assert len(tip["nodes"]) == 1 and tip["truncated"] is False  # nothing beyond
        both = await provenance.trace(TENANT, "chunk", "upload://a.pdf#chunk1-ab", direction="both")
        assert len(both["nodes"]) == 4 and len(both["steps"]) == 3
        monkeypatch.setattr(provenance, "MAX_NODES", 2)
        cut = await provenance.trace(TENANT, "embedding", "upload://a.pdf#chunk1-ab")
        assert cut["truncated"] is True and len(cut["nodes"]) == 2
        with pytest.raises(LineageError) as refused:
            await provenance.trace(TENANT, "chunk", "nowhere")
        assert refused.value.status == 404
        with pytest.raises(LineageError):
            await provenance.trace(TENANT, "chunk", "upload://a.pdf#chunk1-ab", direction="sideways")

    @pytest.mark.asyncio
    async def test_a_description_names_the_sources_and_the_history_in_order(self, monkeypatch):
        session = _Session()
        _use(monkeypatch, session)
        await provenance.record_chain(TENANT, CHAIN_NODES, CHAIN_STEPS)
        found = await provenance.describe(TENANT, "embedding", "upload://a.pdf#chunk1-ab")
        assert found["complete"] is True and found["sources"][0]["ref"] == "upload://a.pdf"
        assert [h["step"] for h in found["history"]] == ["extract", "chunk", "embed"]
        assert found["history"][0]["from"] == {"kind": "source", "ref": "upload://a.pdf", "version": "sha256:1"}
        assert found["history"][2]["to"]["kind"] == "embedding" and found["versions"][0]["version"] == "openai/small"
        # a node with a declared origin but no source node is described as incomplete, naming the origin
        await provenance.record_chain(TENANT, [node("record", "r-1", "sha256:9", source="core-banking")])
        lone = await provenance.describe(TENANT, "record", "r-1")
        assert lone["complete"] is False and lone["sources"] == [
            {"kind": "source", "ref": "core-banking", "version": "", "declared": True}
        ]
        # the version asked for is the root
        await provenance.record_chain(TENANT, [node("document", "upload://a.pdf", "sha256:2")])
        old = await provenance.describe(TENANT, "document", "upload://a.pdf", version="sha256:1")
        assert old["node"]["version"] == "sha256:1" and old["complete"] is True


class TestSearch:
    @pytest.mark.asyncio
    async def test_the_search_filters_by_kind_and_a_literal_substring_newest_first(self, monkeypatch):
        import core.database

        class _Capture:
            def __init__(self):
                self.statements = []

            async def execute(self, statement):
                self.statements.append(statement)
                return _Result(
                    [
                        SimpleNamespace(
                            **dict(
                                node("chunk", "upload://a.pdf#chunk1", "ab", observed_at=T0),
                                id=uuid.uuid4(),
                                attributes={},
                            )
                        )
                    ]
                )

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        capture = _Capture()
        monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: capture)
        monkeypatch.setattr(settings, "lineage_enabled", True)
        found = await provenance.search(TENANT, kind="Chunk", query="50%_off", limit=500)
        assert found[0]["kind"] == "chunk" and found[0]["ref"] == "upload://a.pdf#chunk1"
        compiled = capture.statements[0].compile(dialect=postgresql.dialect())
        text = str(compiled)
        assert (
            "lineage_nodes.kind = " in text
            and "LIKE" in text
            and "ESCAPE" in text
            and "ORDER BY lineage_nodes.observed_at DESC" in text
        )
        assert "50/%/_off" in str(compiled.params) and provenance.MAX_SEARCH in compiled.params.values()
        await provenance.search(TENANT)
        assert "LIKE" not in str(capture.statements[1].compile(dialect=postgresql.dialect()))
        with pytest.raises(LineageError) as refused:
            await provenance.search(TENANT, kind="thing")
        assert refused.value.code == "kind_unknown"

    @pytest.mark.asyncio
    async def test_the_search_route_is_off_with_the_flag_and_maps_refusals(self, monkeypatch):
        from unittest.mock import AsyncMock as _Async

        monkeypatch.setattr(settings, "lineage_enabled", False)
        with pytest.raises(HTTPException) as refused:
            await api.search_nodes(kind=None, q=None, limit=10, tenant_id=str(TENANT))
        assert refused.value.status_code == 404
        monkeypatch.setattr(settings, "lineage_enabled", True)
        monkeypatch.setattr(provenance, "search", _Async(return_value=[{"id": "n1"}]))
        assert await api.search_nodes(kind="chunk", q="a", limit=10, tenant_id=str(TENANT)) == {
            "nodes": [{"id": "n1"}],
            "total": 1,
        }
        monkeypatch.setattr(provenance, "search", _Async(side_effect=LineageError(422, "kind_unknown", "no")))
        with pytest.raises(HTTPException) as refused:
            await api.search_nodes(kind="thing", q=None, limit=10, tenant_id=str(TENANT))
        assert refused.value.status_code == 422 and refused.value.detail["error"] == "kind_unknown"


class TestHooks:
    @pytest.mark.asyncio
    async def test_ingestion_notes_its_chain_and_never_fails_on_it(self, monkeypatch):
        monkeypatch.setattr(settings, "lineage_enabled", True)
        recorded = AsyncMock(return_value={"nodes": [], "steps": 0})
        monkeypatch.setattr(provenance, "record_chain", recorded)
        await provenance.on_ingest(
            TENANT,
            source="upload://a.pdf",
            stream=b"pdf bytes",
            extraction_method="pypdfium2",
            mime_type="application/pdf",
            chunks=[("upload://a.pdf#chunk1-ab", "ab"), ("upload://a.pdf#chunk2-cd", "cd")],
            embedding_model="openai/small",
            dimensions=3,
        )
        nodes, steps = recorded.call_args.args[1], recorded.call_args.args[2]
        assert [n["kind"] for n in nodes] == ["source", "document", "chunk", "embedding", "chunk", "embedding"]
        assert nodes[0]["version"] == provenance.version_of(b"pdf bytes") and nodes[3]["version"] == "openai/small"
        assert [(s["from"], s["to"], s["step"]) for s in steps] == [
            (0, 1, "extract"),
            (1, 2, "chunk"),
            (2, 3, "embed"),
            (1, 4, "chunk"),
            (4, 5, "embed"),
        ]
        recorded.side_effect = RuntimeError("store down")
        await provenance.on_ingest(
            TENANT,
            source="upload://a.pdf",
            stream=b"x",
            extraction_method="text",
            mime_type="text/plain",
            chunks=[],
            embedding_model="m",
            dimensions=1,
        )  # logged, not raised
        monkeypatch.setattr(settings, "lineage_enabled", False)
        recorded.reset_mock(side_effect=True)
        await provenance.on_ingest(
            TENANT,
            source="s",
            stream=b"x",
            extraction_method="text",
            mime_type="t",
            chunks=[],
            embedding_model="m",
            dimensions=1,
        )
        assert recorded.call_count == 0

    @pytest.mark.asyncio
    async def test_a_long_chain_is_sent_in_parts_that_share_the_head(self, monkeypatch):
        monkeypatch.setattr(settings, "lineage_enabled", True)
        monkeypatch.setattr(provenance, "MAX_CHAIN_NODES", 6)
        recorded = AsyncMock(return_value={"nodes": [], "steps": 0})
        monkeypatch.setattr(provenance, "record_chain", recorded)
        chunks = [(f"upload://a.pdf#chunk{i}-k{i}", f"k{i}") for i in range(1, 6)]  # 2 + 10 nodes
        await provenance.on_ingest(
            TENANT,
            source="upload://a.pdf",
            stream=b"x",
            extraction_method="text",
            mime_type="t",
            chunks=chunks,
            embedding_model="m",
            dimensions=1,
        )
        parts = [(call.args[1], call.args[2]) for call in recorded.call_args_list]
        assert len(parts) == 3 and all(len(nodes) <= 6 for nodes, _ in parts)
        assert all(nodes[0]["kind"] == "source" and nodes[1]["kind"] == "document" for nodes, _ in parts)
        for nodes, steps in parts:
            assert all(0 <= s["from"] < len(nodes) and 0 <= s["to"] < len(nodes) for s in steps)
            for s in steps:
                if s["step"] == "embed":
                    assert nodes[s["from"]]["kind"] == "chunk" and nodes[s["to"]]["ref"] == nodes[s["from"]]["ref"]
        assert sum(1 for nodes, _ in parts for n in nodes if n["kind"] == "chunk") == 5
        assert sum(1 for _, steps in parts for s in steps if s["step"] == "embed") == 5

    @pytest.mark.asyncio
    async def test_transaction_records_are_acquired_from_their_source(self, monkeypatch):
        monkeypatch.setattr(settings, "lineage_enabled", True)
        recorded = AsyncMock(return_value={"nodes": [], "steps": 0})
        monkeypatch.setattr(provenance, "record_chain", recorded)
        records = [
            {"record_ref": "r-1", "account": "A1", "amount": 5.0, "booked_at": T0},
            {"record_ref": "r-2", "account": "A1", "amount": 6.0, "booked_at": T0 + timedelta(hours=1)},
        ]
        await provenance.on_records(TENANT, source="core-banking", records=records)
        nodes, steps = recorded.call_args.args[1], recorded.call_args.args[2]
        assert [n["kind"] for n in nodes] == ["source", "record", "record"] and nodes[1]["ref"] == "r-1"
        assert nodes[1]["version"] != nodes[2]["version"] and nodes[2]["observed_at"] == T0 + timedelta(hours=1)
        assert [(s["from"], s["to"], s["step"]) for s in steps] == [(0, 1, "acquire"), (0, 2, "acquire")]
        await provenance.on_records(TENANT, source="core-banking", records=[])
        assert recorded.call_count == 1

    @pytest.mark.asyncio
    async def test_knowledge_ingestion_calls_the_hook_with_its_chunks(self, monkeypatch):
        import core.database
        from core.rag import ingest
        from core.rag.chunking import ChunkPlan
        from core.rag.extractors import ExtractedContent, ExtractedSpan

        class _IngestSession:
            def __init__(self):
                self.statements = []
                self.committed = False

            async def execute(self, statement, params=None):
                self.statements.append(str(statement))

            async def commit(self):
                self.committed = True

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        session = _IngestSession()
        monkeypatch.setattr(settings, "lineage_enabled", True)
        monkeypatch.setattr(core.database, "async_session_factory", lambda: session)
        monkeypatch.setattr(ingest, "_resolve_embedding_profile", AsyncMock(return_value=("openai", "small", 3)))
        monkeypatch.setattr(ingest, "_resolve_chunk_plan", AsyncMock(return_value=ChunkPlan()))
        monkeypatch.setattr(
            ingest, "_embed_chunks", AsyncMock(side_effect=lambda texts, model=None: [[0.1, 0.2, 0.3]] * len(texts))
        )
        monkeypatch.setattr(ingest.entities, "enabled", lambda: False)
        noted = AsyncMock()
        monkeypatch.setattr(provenance, "on_ingest", noted)
        content = ExtractedContent(
            spans=[ExtractedSpan(text="The quick brown fox jumps over the lazy dog. " * 12, page=1)],
            mime_type="text/plain",
            extraction_method="text",
        )
        result = await ingest.ingest_document(
            tenant_id=TENANT,
            title="Fox",
            stream=b"fox bytes",
            mime_type="text/plain",
            filename="fox.txt",
            extracted_content=content,
        )
        assert result.chunks_indexed >= 1 and session.committed
        kwargs = noted.call_args.kwargs
        assert (
            noted.call_args.args[0] == TENANT
            and kwargs["source"] == "upload://fox.txt"
            and kwargs["stream"] == b"fox bytes"
        )
        assert (
            kwargs["embedding_model"] == "openai/small"
            and kwargs["dimensions"] == 3
            and kwargs["extraction_method"] == "text"
        )
        assert len(kwargs["chunks"]) == result.chunks_indexed and kwargs["chunks"][0][0].startswith(
            "upload://fox.txt#chunk1-"
        )
        assert kwargs["chunks"][0][0].endswith(kwargs["chunks"][0][1])

    @pytest.mark.asyncio
    async def test_transaction_ingestion_calls_the_hook_with_the_kept_records(self, monkeypatch):
        import core.database
        from core.txn import records as txn_records

        class _TxnSession:
            def __init__(self):
                self.rows = []

            async def execute(self, statement):
                return _Result([])

            def add(self, row):
                self.rows.append(row)

            async def flush(self):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: _TxnSession())
        noted = AsyncMock()
        monkeypatch.setattr(provenance, "on_records", noted)
        raw = [
            {
                "account": "A1",
                "direction": "credit",
                "amount": 10,
                "booked_at": "2026-10-01T09:00:00Z",
                "description": "cash",
            }
        ]
        # without a batch source, each record keeps its own
        noted.reset_mock()
        mixed = [
            dict(raw[0], source="statement:stmt-1"),
            dict(raw[0], amount=11, source="statement:stmt-1"),
            dict(raw[0], amount=12, source="switch"),
        ]
        out = await txn_records.ingest(TENANT, mixed)
        assert out["kept"] == 3
        assert sorted((c.kwargs["source"], len(c.kwargs["records"])) for c in noted.call_args_list) == [
            ("statement:stmt-1", 2),
            ("switch", 1),
        ]
        noted.reset_mock()
        out = await txn_records.ingest(TENANT, raw, source="core-banking")
        assert out["kept"] == 1
        assert (
            noted.call_args.kwargs["source"] == "core-banking"
            and noted.call_args.kwargs["records"][0]["account"] == "A1"
        )


class TestRoutes:
    @pytest.mark.asyncio
    async def test_off_the_status_says_so_and_the_rest_is_not_found(self, monkeypatch):
        monkeypatch.setattr(settings, "lineage_enabled", False)
        tid = str(TENANT)
        assert (await api.status(tenant_id=tid))["enabled"] is False
        for call in (
            api.record_chain(api.ChainIn(nodes=[{"kind": "source", "ref": "x"}]), tenant_id=tid),
            api.describe(kind="chunk", ref="x", version=None, tenant_id=tid),
            api.trace(kind="chunk", ref="x", direction="upstream", hops=2, version=None, tenant_id=tid),
        ):
            with pytest.raises(HTTPException) as refused:
                await call
            assert refused.value.status_code == 404 and refused.value.detail["error"] == "lineage_disabled"

    @pytest.mark.asyncio
    async def test_on_a_chain_is_noted_and_read_back_and_refusals_are_mapped(self, monkeypatch):
        session = _Session()
        _use(monkeypatch, session)
        tid = str(TENANT)
        status = await api.status(tenant_id=tid)
        assert (
            status["enabled"] is True
            and "embedding" in status["kinds"]
            and status["limits"]["hops"] == provenance.MAX_HOPS
        )
        noted = await api.record_chain(api.ChainIn(nodes=CHAIN_NODES, steps=CHAIN_STEPS), tenant_id=tid)
        assert len(noted["nodes"]) == 4 and noted["steps"] == 3
        described = await api.describe(kind="embedding", ref="upload://a.pdf#chunk1-ab", version=None, tenant_id=tid)
        assert described["complete"] is True and len(described["history"]) == 3
        traced = await api.trace(
            kind="source", ref="upload://a.pdf", direction="downstream", hops=8, version=None, tenant_id=tid
        )
        assert len(traced["nodes"]) == 4
        with pytest.raises(HTTPException) as refused:
            await api.describe(kind="chunk", ref="nowhere", version=None, tenant_id=tid)
        assert refused.value.status_code == 404 and refused.value.detail["error"] == "node_unknown"
        with pytest.raises(HTTPException) as refused:
            await api.record_chain(api.ChainIn(nodes=[{"kind": "mystery", "ref": "x"}]), tenant_id=tid)
        assert refused.value.status_code == 422 and refused.value.detail["error"] == "node_invalid"

    def test_the_router_is_registered_behind_the_lineage_scope_family(self):
        from api.main import app
        from api.route_enforcement import SCOPE_FAMILIES

        # The OpenAPI paths hold the full path whether routers are included eagerly or lazily.
        paths = set(app.openapi()["paths"])
        assert {"/api/v1/lineage/status", "/api/v1/lineage", "/api/v1/lineage/nodes/{kind}/{ref}"} <= paths
        assert "/api/v1/lineage/trace/{kind}/{ref}" in paths
        assert SCOPE_FAMILIES["lineage"] == ("audit:read", "approvals:write")


def test_the_migration_and_the_models_are_shaped():
    from core.models.lineage import LineageNode, LineageStep

    text = Path("migrations/versions/v6_z75_lineage.py").read_text(encoding="utf-8")
    assert 'revision = "v6z75_lineage"' in text and 'down_revision = "v6z74_txn_narratives"' in text
    for table in ("lineage_nodes", "lineage_steps"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in text
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;" in text
        assert f"CREATE POLICY {table}_tenant_isolation" in text
    # every foreign key of a step carries a leading index
    assert "from_node UUID NOT NULL REFERENCES lineage_nodes(id) ON DELETE CASCADE" in text
    assert "ix_lineage_steps_from_node ON lineage_steps(from_node)" in text
    assert "ix_lineage_steps_to_node ON lineage_steps(to_node)" in text
    assert "ux_lineage_nodes_tenant_key ON lineage_nodes(tenant_id, kind, ref, version)" in text
    assert LineageNode.__tablename__ == "lineage_nodes" and LineageStep.__tablename__ == "lineage_steps"
    index_names = {index.name for index in LineageStep.__table__.indexes}
    assert {"ix_lineage_steps_from_node", "ix_lineage_steps_to_node", "ux_lineage_steps_tenant_edge"} <= index_names
    assert {fk.column.table.name for fk in LineageStep.__table__.foreign_keys} == {"lineage_nodes"}
