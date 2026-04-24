"""Regression tests for S0-07 — RAG retrieval quality gate (PR-4).

The gold corpus sweep runs in fixture mode so the rubric exercises
without a live embedding model. Live-tenant runs live in
``scripts/rag_eval.py --tenant <uuid>`` and are wired into the
main-branch CI workflow once the first tenant seeds a real index.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ── Rubric ──────────────────────────────────────────────────────────


def test_quality_floor_constant() -> None:
    from core.rag.eval import INGESTION_FLOOR, QUALITY_FLOOR

    # The floor is LOAD-BEARING — never lower it without a plan.
    assert QUALITY_FLOOR == 4.6
    assert INGESTION_FLOOR < QUALITY_FLOOR


def test_score_run_all_perfect_gets_5() -> None:
    from core.rag.eval import GoldQuery, RetrievedChunk, score_run

    q = GoldQuery(
        id="t-01",
        modality="text",
        query="find invoice",
        expected_snippets=["Invoice", "total"],
    )
    retrieved = [
        RetrievedChunk(text="Invoice total: INR 12,000. " + "x" * 200, score=0.9, mode="pgvector"),
        RetrievedChunk(text="Invoice details: total and line items " + "y" * 200, score=0.85, mode="pgvector"),
        RetrievedChunk(text="Another Invoice total reference " + "z" * 200, score=0.8, mode="pgvector"),
    ]
    run = score_run(q, retrieved)
    assert run.score == 5.0
    assert run.passes
    assert not run.reasons


def test_score_run_missing_expected_penalises_semantic_match() -> None:
    from core.rag.eval import GoldQuery, RetrievedChunk, score_run

    q = GoldQuery(
        id="t-02",
        modality="text",
        query="find invoice",
        expected_snippets=["Invoice", "total", "vendor"],
    )
    # No expected snippet present
    retrieved = [
        RetrievedChunk(text="completely unrelated content about weather " + "x" * 200, score=0.4, mode="pgvector"),
    ]
    run = score_run(q, retrieved)
    # semantic_match = 0, no_forbidden = 1, mode = 1, length = 0.8, rank = 1.0
    assert run.score < 4.0
    assert not run.passes


def test_forbidden_snippet_blocks_pass() -> None:
    from core.rag.eval import GoldQuery, RetrievedChunk, score_run

    q = GoldQuery(
        id="t-03",
        modality="text",
        query="safe answer",
        expected_snippets=["safe"],
        forbidden_snippets=["classified"],
    )
    retrieved = [
        RetrievedChunk(
            text="this contains classified material and safe info " + "x" * 200,
            score=0.9,
            mode="pgvector",
        ),
    ]
    run = score_run(q, retrieved)
    assert run.dimensions["no_forbidden"] == 0.0
    assert run.score < 5.0


def test_filename_fallback_penalised() -> None:
    """Keyword / filename modes score lower than pgvector / ragflow."""
    from core.rag.eval import GoldQuery, RetrievedChunk, score_run

    q = GoldQuery(id="t-04", modality="text", query="x", expected_snippets=["hello"])
    retrieved = [
        RetrievedChunk(text="hello world " * 20, score=0.1, mode="filename"),
    ]
    run = score_run(q, retrieved)
    assert run.dimensions["retrieval_mode"] == 0.0
    assert run.score < 5.0


def test_rank_inversion_detected() -> None:
    from core.rag.eval import GoldQuery, RetrievedChunk, score_run

    q = GoldQuery(id="t-05", modality="text", query="x", expected_snippets=["x"])
    retrieved = [
        RetrievedChunk(text="x " * 50, score=0.4, mode="pgvector"),
        RetrievedChunk(text="x " * 50, score=0.8, mode="pgvector"),  # inversion
        RetrievedChunk(text="x " * 50, score=0.3, mode="pgvector"),  # inversion
    ]
    run = score_run(q, retrieved)
    assert run.dimensions["rank_monotonic"] < 1.0


# ── Corpus ───────────────────────────────────────────────────────────


def test_gold_corpus_covers_every_critical_modality() -> None:
    from core.rag.eval import load_gold_corpus

    corpus = load_gold_corpus()
    counts: dict[str, int] = {}
    for q in corpus:
        counts[q.modality] = counts.get(q.modality, 0) + 1
    # Closure plan: "Gold corpus has ≥ 8 queries per critical modality"
    for modality in ("text", "pdf", "docx", "xlsx"):
        assert counts.get(modality, 0) >= 8, (
            f"{modality!r} has only {counts.get(modality, 0)} queries; "
            "closure plan requires ≥ 8"
        )


def test_gold_corpus_query_ids_are_unique() -> None:
    from core.rag.eval import load_gold_corpus

    corpus = load_gold_corpus()
    ids = [q.id for q in corpus]
    assert len(ids) == len(set(ids)), "gold corpus has duplicate query_ids"


# ── Gate decision ────────────────────────────────────────────────────


def test_gate_decision_honest_about_floor() -> None:
    from core.rag.eval import EvalReport, gate_decision

    low = EvalReport(
        total_queries=10,
        passed=5,
        flagged=3,
        failed=2,
        overall_score=4.2,
        per_modality_score={"text": 4.2, "pdf": 4.2},
    )
    passes, reason = gate_decision(low)
    assert not passes
    assert "below floor" in reason

    high = EvalReport(
        total_queries=10,
        passed=10,
        flagged=0,
        failed=0,
        overall_score=4.9,
        per_modality_score={"text": 4.8, "pdf": 4.9},
    )
    passes, reason = gate_decision(high)
    assert passes


def test_per_modality_can_block_even_when_overall_passes() -> None:
    from core.rag.eval import EvalReport, gate_decision

    # Overall is above floor but one modality is below.
    mixed = EvalReport(
        total_queries=12,
        passed=10,
        flagged=1,
        failed=1,
        overall_score=4.7,
        per_modality_score={"text": 4.9, "pdf": 4.9, "docx": 4.3},
    )
    passes, reason = gate_decision(mixed)
    assert not passes
    assert "docx" in reason


# ── Regression detection ────────────────────────────────────────────


def test_regression_detected_when_score_drops_substantially() -> None:
    from core.rag.eval import (
        EvalReport,
        GoldQuery,
        QueryRun,
        detect_regressions,
    )

    # Construct-and-discard: we only need the ID in the QueryRun below.
    GoldQuery(id="r-01", modality="text", query="x")
    report = EvalReport(
        total_queries=1,
        passed=0,
        flagged=0,
        failed=1,
        overall_score=3.5,
        per_modality_score={"text": 3.5},
        runs=[
            QueryRun(
                query_id="r-01",
                modality="text",
                retrieved=[],
                score=3.5,
                dimensions={},
                passes=False,
                reasons=[],
            )
        ],
    )
    baseline = {"r-01": 4.9}
    offenders = detect_regressions(report, baseline, drop=0.2)
    assert "r-01" in offenders


# ── Script integrations ──────────────────────────────────────────────


def test_rag_eval_script_runs_fixture_mode_and_passes() -> None:
    """``scripts/rag_eval.py --fixture`` is the smoke test everything
    else builds on. Fixture retrieval is crafted to score 5.0."""
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell, invokes our own script
        [sys.executable, str(REPO / "scripts/rag_eval.py"), "--fixture", "--format", "text"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"rag_eval.py --fixture exited {result.returncode}.\n"
        f"stderr: {result.stderr}\nstdout: {result.stdout}"
    )
    assert "GATE PASSED" in result.stdout


def test_embedding_rotate_plan_mode_is_always_safe() -> None:
    """``embedding_rotate.py plan`` never writes. Always exits 0 with
    a plan summary for a known-good target."""
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell, invokes our own script
        [
            sys.executable,
            str(REPO / "scripts/embedding_rotate.py"),
            "plan",
            "--target-model",
            "BAAI/bge-small-en-v1.5",
            "--target-dims",
            "384",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Rotation plan" in result.stdout


def test_embedding_rotate_refuses_shadow_without_confirmation() -> None:
    """Destructive phases (shadow/swap/reap) must refuse unless the
    operator passes --i-understand."""
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell, invokes our own script
        [
            sys.executable,
            str(REPO / "scripts/embedding_rotate.py"),
            "shadow",
            "--target-model",
            "BAAI/bge-small-en-v1.5",
            "--target-dims",
            "384",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 3
    assert "--i-understand" in result.stderr


# ── /knowledge/health surface ───────────────────────────────────────


def test_knowledge_health_exposes_last_eval() -> None:
    src = _read("api/v1/knowledge.py")
    assert "last_eval" in src
    assert "quality_floor" in src
    # The float must match the rubric's QUALITY_FLOOR
    assert "4.6" in src
