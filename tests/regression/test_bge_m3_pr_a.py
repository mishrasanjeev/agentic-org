"""Regression tests for the BGE-M3 embedding upgrade PR-A.

PR-A adds the ``embedding_bge_m3`` column + IVFFlat index alongside
the existing ``embedding`` column. It does NOT flip the platform
default. PR-B (cutover) is gated on the backfill script reporting
zero orphan-risk rows.

These tests pin contracts that, if regressed, would silently break
the rollout:

  1. The ``embedding_bge_m3`` column is exposed by the migration.
  2. The default model is still ``bge-small-en-v1.5`` — PR-A must
     not change ``DEFAULT_EMBEDDING_MODEL``.
  3. ``embed_with("BAAI/bge-m3", ...)`` returns 1024-dim vectors.
  4. The backfill CLI's ``--verify`` mode exits non-zero when rows
     are still pending and zero when they aren't.
"""

from __future__ import annotations

import importlib

import pytest

# ---------------------------------------------------------------------------
# Migration + default-model pins (do not require fastembed at import time)
# ---------------------------------------------------------------------------


def test_pr_a_migration_file_exists() -> None:
    """PR-A is delivered by v495_bge_m3_column."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    migration = repo / "migrations" / "versions" / "v4_9_5_bge_m3_column.py"
    src = migration.read_text(encoding="utf-8")
    assert 'revision = "v495_bge_m3_column"' in src
    assert "embedding_bge_m3 vector(1024)" in src
    assert "ix_knowledge_documents_embedding_bge_m3" in src
    # Must be guarded so it's safe on environments where
    # knowledge_documents was never created.
    assert "information_schema.tables" in src


def test_pr_a_does_not_flip_default_model() -> None:
    """The platform default MUST stay bge-small until PR-B cutover.

    PR-B is the one that drops the old column and renames the new
    one. If PR-A flips ``DEFAULT_EMBEDDING_MODEL``, the request path
    starts emitting 1024-dim vectors that won't fit ``vector(384)``
    and every ``/knowledge/search`` call 500s.
    """
    embeddings = importlib.import_module("core.embeddings")
    assert embeddings.DEFAULT_EMBEDDING_MODEL == "BAAI/bge-small-en-v1.5"
    # Sanity — the bge-m3 dim entry is present so the backfill knows
    # the column dimensionality without having to load fastembed.
    assert embeddings._MODEL_DIMS["BAAI/bge-m3"] == 1024


def test_model_dim_helper_returns_1024_for_bge_m3() -> None:
    embeddings = importlib.import_module("core.embeddings")
    assert embeddings.model_dim("BAAI/bge-m3") == 1024
    assert embeddings.model_dim("BAAI/bge-small-en-v1.5") == 384


# ---------------------------------------------------------------------------
# Embedding round-trip (skipped when fastembed isn't installed in the env)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_embed_with_bge_m3_returns_1024_dim_vectors() -> None:
    """End-to-end smoke test for bge-m3 via FlagEmbedding.

    Skipped when FlagEmbedding isn't in the env — the package pulls
    ~2.3 GB of weights and PyTorch on first call, which we don't want
    to install just to satisfy a unit-test runner. The Cloud Run
    backfill image MUST install it.

    Also skipped when FlagEmbedding *is* installed but the BGE-M3
    weights aren't already in the local HuggingFace cache. Without
    this gate, a fresh dev machine with FlagEmbedding installed
    triggers an unbounded ~2.3 GB cold download mid-pytest which
    hangs the preflight gate and looks like a code regression
    rather than a missing-cache environment issue.
    """
    flag = pytest.importorskip(
        "FlagEmbedding", reason="FlagEmbedding not installed in this env"
    )
    del flag
    # Honor the docstring intent: only run when the env can produce
    # vectors quickly. Probe the HF cache for the actual weight file
    # (the large pytorch_model.bin or its safetensors equivalent) —
    # checking the small config.json alone isn't enough because a
    # partial / interrupted download may have landed only the
    # metadata files. If the weights aren't cached, FlagEmbedding's
    # constructor would trigger a multi-GB cold download mid-pytest
    # which isn't acceptable in a unit-test gate.
    from huggingface_hub import try_to_load_from_cache  # noqa: PLC0415

    weights_cached = any(
        try_to_load_from_cache("BAAI/bge-m3", fname) is not None
        for fname in ("pytorch_model.bin", "model.safetensors")
    )
    if not weights_cached:
        pytest.skip(
            "BGE-M3 model weights not in local HuggingFace cache "
            "(config.json may be present but the large weight file "
            "isn't). Pre-download with `python -c \"from huggingface_hub "
            "import snapshot_download; snapshot_download('BAAI/bge-m3')\"` "
            "to enable this test, or run it inside the Cloud Run "
            "backfill image which has the weights baked in."
        )
    from core.embeddings import embed_bge_m3

    vectors = embed_bge_m3(["hello world", "second sample"])
    assert len(vectors) == 2
    assert all(len(v) == 1024 for v in vectors)
    assert all(isinstance(x, float) for x in vectors[0])


# ---------------------------------------------------------------------------
# Backfill CLI surface tests (no DB required)
# ---------------------------------------------------------------------------


def test_backfill_cli_help_documents_dry_run_and_verify() -> None:
    """The CLI flags must stay stable — operators script around them."""
    import io
    import sys

    from core.embeddings_backfill import main

    captured = io.StringIO()
    sys.stdout = captured
    try:
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
    finally:
        sys.stdout = sys.__stdout__
    out = captured.getvalue()
    assert exc.value.code == 0
    for token in ("--tenant", "--batch-size", "--dry-run", "--verify"):
        assert token in out, f"backfill --help must document {token}"


def test_backfill_target_model_dim_matches_column() -> None:
    """The dimension contract that prevents a silent vector-size mismatch."""
    from core.embeddings_backfill import (
        TARGET_COLUMN,
        TARGET_MODEL,
        _validate_target_dim,
    )

    assert TARGET_COLUMN == "embedding_bge_m3"
    assert TARGET_MODEL == "BAAI/bge-m3"
    # Should not raise.
    _validate_target_dim()


# ---------------------------------------------------------------------------
# Feature-flag wiring (RAG_USE_BGE_M3) — the gate that PR-A introduces so the
# RAG ingest + search paths flip atomically. Must default OFF.
# ---------------------------------------------------------------------------


def test_rag_use_bge_m3_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default is OFF — flipping requires explicit env opt-in.

    If this regresses to ON-by-default, every fresh deploy starts
    embedding queries with bge-m3 against a `vector(384)` column on
    environments where the backfill hasn't run yet.
    """
    monkeypatch.delenv("AGENTICORG_RAG_USE_BGE_M3", raising=False)
    from core.embeddings import rag_embedding_column, rag_use_bge_m3

    assert rag_use_bge_m3() is False
    assert rag_embedding_column() == "embedding"


@pytest.mark.parametrize("v", ["1", "true", "TRUE", "yes", "on", "True"])
def test_rag_use_bge_m3_truthy_values(
    monkeypatch: pytest.MonkeyPatch, v: str
) -> None:
    monkeypatch.setenv("AGENTICORG_RAG_USE_BGE_M3", v)
    from core.embeddings import rag_embedding_column, rag_use_bge_m3

    assert rag_use_bge_m3() is True
    assert rag_embedding_column() == "embedding_bge_m3"


@pytest.mark.parametrize("v", ["", "0", "false", "no", "off", "anything else"])
def test_rag_use_bge_m3_falsy_values(
    monkeypatch: pytest.MonkeyPatch, v: str
) -> None:
    monkeypatch.setenv("AGENTICORG_RAG_USE_BGE_M3", v)
    from core.embeddings import rag_embedding_column, rag_use_bge_m3

    assert rag_use_bge_m3() is False
    assert rag_embedding_column() == "embedding"


def test_rag_search_query_uses_flag_column() -> None:
    """``api/v1/knowledge.py`` search must read the column the flag picks.

    Source-pin: the SELECT must reference ``rag_embedding_column()``,
    not the hard-coded ``embedding`` literal that pre-dated the flag.
    Otherwise flipping the flag rotates the write path without
    rotating reads — every search 500s with a dim mismatch.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "api" / "v1" / "knowledge.py"
    ).read_text(encoding="utf-8")
    assert "rag_embedding_column" in src, (
        "search path must import + use rag_embedding_column() so the "
        "column flip is atomic with embed_one()"
    )


def test_rag_ingest_insert_uses_flag_column() -> None:
    """``core/rag/ingest.py`` INSERT must target the flag-selected column."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "core" / "rag" / "ingest.py"
    ).read_text(encoding="utf-8")
    assert "rag_embedding_column" in src, (
        "ingest path must use rag_embedding_column() so writes land in "
        "the column /search reads from"
    )


# ---------------------------------------------------------------------------
# Mock-DB exercise of the backfill flow — bumps coverage on the script's
# DB-touching helpers without needing a real Postgres.
# ---------------------------------------------------------------------------


import asyncio  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402


def _make_mock_session(scalar_values: list, fetch_rows: list | None = None):
    """Return an AsyncMock session yielding controlled SELECT/UPDATE results."""
    session = AsyncMock()
    session.commit = AsyncMock()

    call_idx = {"n": 0}

    async def execute(_stmt, _params=None):
        result = MagicMock()
        # Distribute scalar/all results round-robin in call order so a
        # single test can stage multiple SELECT counts.
        if call_idx["n"] < len(scalar_values):
            result.scalar_one_or_none.return_value = scalar_values[call_idx["n"]]
            result.scalar_one.return_value = scalar_values[call_idx["n"]]
        else:
            result.scalar_one_or_none.return_value = None
            result.scalar_one.return_value = 0
        if fetch_rows is not None and call_idx["n"] < len(fetch_rows):
            result.all.return_value = fetch_rows[call_idx["n"]]
        else:
            result.all.return_value = []
        call_idx["n"] += 1
        return result

    session.execute = execute
    return session


@asynccontextmanager
async def _session_cm(session):
    yield session


def test_backfill_verify_returns_2_when_column_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--verify`` exits 2 when the destination column doesn't exist."""
    from core import embeddings_backfill as eb

    session = _make_mock_session(scalar_values=[None])  # column lookup -> None
    monkeypatch.setattr(eb, "async_session_factory", lambda: _session_cm(session))
    rc = asyncio.run(eb._verify(None))
    assert rc == 2


def test_backfill_verify_returns_0_when_no_orphans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--verify`` exits 0 when zero rows would be orphaned."""
    from core import embeddings_backfill as eb

    # First execute: column exists (returns 1). Second: orphan count = 0.
    session = _make_mock_session(scalar_values=[1, 0])
    monkeypatch.setattr(eb, "async_session_factory", lambda: _session_cm(session))
    rc = asyncio.run(eb._verify(None))
    assert rc == 0


def test_backfill_verify_returns_1_when_orphans_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--verify`` exits 1 when rows would still be orphaned by cutover."""
    from core import embeddings_backfill as eb

    # column exists (1), 17 rows with old col but no new col
    session = _make_mock_session(scalar_values=[1, 17])
    monkeypatch.setattr(eb, "async_session_factory", lambda: _session_cm(session))
    rc = asyncio.run(eb._verify(None))
    assert rc == 1


def test_backfill_run_dry_run_does_not_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--dry-run`` reports the count and returns 0 without calling embed_with."""
    from core import embeddings_backfill as eb

    # column exists (1), 5 pending
    session = _make_mock_session(scalar_values=[1, 5])
    monkeypatch.setattr(eb, "async_session_factory", lambda: _session_cm(session))
    with patch.object(eb, "embed_with") as embed_mock:
        rc = asyncio.run(eb.run(tenant_id=None, batch_size=10, dry_run=True))
    assert rc == 0
    embed_mock.assert_not_called()


def test_backfill_run_aborts_when_column_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run`` exits 2 when the destination column doesn't exist."""
    from core import embeddings_backfill as eb

    session = _make_mock_session(scalar_values=[None])
    monkeypatch.setattr(eb, "async_session_factory", lambda: _session_cm(session))
    rc = asyncio.run(eb.run(tenant_id=None, batch_size=10, dry_run=False))
    assert rc == 2


def test_backfill_run_writes_then_terminates_on_empty_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One batch then empty -> writes 2 rows and returns 0."""
    from core import embeddings_backfill as eb

    # Sequence:
    #  1) _column_exists -> 1
    #  2) _count_pending -> 2
    #  3) (loop iter 1) _fetch_batch -> 2 rows
    #  4) UPDATE x2 + commit (results not consumed)
    #  5) (loop iter 2) _fetch_batch -> empty
    fetch_rows = [None, None, [("id-1", "hello"), ("id-2", "world")], None, None, []]
    session = _make_mock_session(scalar_values=[1, 2], fetch_rows=fetch_rows)
    monkeypatch.setattr(eb, "async_session_factory", lambda: _session_cm(session))

    def fake_embed(model, texts):
        # Return 1024-dim zero vectors so the float formatting works.
        return [[0.0] * 1024 for _ in texts]

    monkeypatch.setattr(eb, "embed_with", fake_embed)

    rc = asyncio.run(eb.run(tenant_id=None, batch_size=2, dry_run=False))
    assert rc == 0


def test_backfill_run_returns_1_on_embed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Embedding failure surfaces non-zero exit code, not silent skip."""
    from core import embeddings_backfill as eb

    fetch_rows = [None, None, [("id-1", "hello")]]
    session = _make_mock_session(scalar_values=[1, 1], fetch_rows=fetch_rows)
    monkeypatch.setattr(eb, "async_session_factory", lambda: _session_cm(session))

    def boom(_m, _t):
        raise RuntimeError("model not loaded")

    monkeypatch.setattr(eb, "embed_with", boom)

    rc = asyncio.run(eb.run(tenant_id=None, batch_size=1, dry_run=False))
    assert rc == 1


# ---------------------------------------------------------------------------
# Embedding helpers (no network, no fastembed) — exercise the dispatch paths.
# ---------------------------------------------------------------------------


def test_embed_with_unknown_model_raises_not_implemented() -> None:
    """``embed_with`` rejects unsupported model names loudly."""
    from core.embeddings import embed_with

    with pytest.raises(NotImplementedError):
        embed_with("totally-fake-model", ["one"])


def test_embed_with_empty_texts_returns_empty() -> None:
    """Both paths short-circuit on empty input — no model load."""
    from core.embeddings import embed_with

    assert embed_with("BAAI/bge-m3", []) == []


def test_embed_one_routes_through_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`embed_one` is a thin wrapper around `embed`."""
    from core import embeddings as emb

    monkeypatch.setattr(emb, "embed", lambda texts: [[0.5] * 4 for _ in texts])
    assert emb.embed_one("hi") == [0.5, 0.5, 0.5, 0.5]


def test_embed_routes_to_bge_m3_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag flip routes the default embed() through bge-m3."""
    from core import embeddings as emb

    monkeypatch.setenv("AGENTICORG_RAG_USE_BGE_M3", "1")
    monkeypatch.setattr(emb, "embed_bge_m3", lambda texts: [[1.0] * 1024 for _ in texts])
    out = emb.embed(["q"])
    assert len(out) == 1
    assert len(out[0]) == 1024
