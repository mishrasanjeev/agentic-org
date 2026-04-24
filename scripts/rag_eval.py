"""Run the RAG quality gate against a live (or seeded) knowledge base.

Usage
-----
    python scripts/rag_eval.py --tenant <uuid> [--baseline path/to/baseline.jsonl]
    python scripts/rag_eval.py --fixture  # offline dry-run with canned retrieval

Exit codes
----------
    0   — gate passed (overall >= 4.6 AND every modality >= 4.6)
    1   — gate failed (below floor, or regression vs. baseline)
    2   — usage / configuration error

CI wiring — PR-4 of docs/STRICT_REPO_S0_CLOSURE_PLAN_2026-04-24.md:
main-branch workflow runs this against the seeded eval index in CI;
prod runs it nightly against the live tenant index. The ``--fixture``
mode is how the unit tests exercise the scoring code without a real
embedding model running.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from core.rag.eval import (
    EvalReport,
    GoldQuery,
    QueryRun,
    RetrievedChunk,
    aggregate,
    detect_regressions,
    gate_decision,
    load_baseline,
    load_gold_corpus,
    score_run,
)


async def _retrieve_live(
    tenant_id: str, query: str, top_k: int = 5
) -> list[RetrievedChunk]:
    """Call the live search path for a tenant.

    Only used when ``--tenant`` is supplied. ``--fixture`` mode skips
    this entirely and exercises the scoring code against canned
    responses.
    """
    from api.v1.knowledge import _native_semantic_search

    results = await _native_semantic_search(tenant_id, query, top_k)
    chunks: list[RetrievedChunk] = []
    for r in results:
        # Codex PR #304 review P1: _native_semantic_search returns
        # SearchResult pydantic instances, not dicts. The dict-only
        # check used to drop every row in live --tenant mode, so
        # every query scored as empty retrieval. Accept both shapes.
        if isinstance(r, dict):
            text = str(
                r.get("content")
                or r.get("text")
                or r.get("chunk_text")
                or ""
            )
            score = float(r.get("score", 0.0) or 0.0)
            source = str(r.get("source") or r.get("document_name") or "")
            page = r.get("page")
            sheet = r.get("sheet")
            mode = str(r.get("mode") or "pgvector")
        else:
            # Pydantic / dataclass / ORM result — pull attributes we
            # know about.
            text = str(
                getattr(r, "chunk_text", None)
                or getattr(r, "content", None)
                or getattr(r, "text", "")
                or ""
            )
            score = float(getattr(r, "score", 0.0) or 0.0)
            source = str(
                getattr(r, "document_name", None)
                or getattr(r, "source", "")
                or ""
            )
            page = getattr(r, "page", None)
            sheet = getattr(r, "sheet", None)
            mode = str(getattr(r, "mode", None) or "pgvector")
        chunks.append(
            RetrievedChunk(
                text=text,
                score=score,
                source=source,
                page=page,
                sheet=sheet,
                mode=mode,
            )
        )
    return chunks


def _fixture_retrieval(query: GoldQuery) -> list[RetrievedChunk]:
    """Canned high-quality retrieval for the given gold query.

    Builds chunks that contain every expected snippet so the scoring
    pipeline returns 5.0. Used in ``--fixture`` mode + unit tests so
    the rubric can be exercised without a live embedding model.
    """
    text = " ".join(query.expected_snippets) if query.expected_snippets else query.query
    padded = text + ". " + ("lorem ipsum dolor sit amet " * 20)
    return [
        RetrievedChunk(text=padded, score=0.92, mode="pgvector"),
        RetrievedChunk(text=padded + " (second chunk)", score=0.88, mode="pgvector"),
        RetrievedChunk(text=padded + " (third chunk)", score=0.81, mode="pgvector"),
    ]


async def _run_eval(
    tenant_id: str | None,
    fixture: bool,
    corpus: list[GoldQuery],
) -> EvalReport:
    runs: list[QueryRun] = []
    for query in corpus:
        if fixture:
            retrieved = _fixture_retrieval(query)
        else:
            assert tenant_id is not None
            retrieved = await _retrieve_live(tenant_id, query.query)
        runs.append(score_run(query, retrieved))
    return aggregate(runs)


def _emit_report(report: EvalReport, fmt: str = "json") -> str:
    payload: dict[str, Any] = {
        "total_queries": report.total_queries,
        "passed": report.passed,
        "flagged": report.flagged,
        "failed": report.failed,
        "overall_score": report.overall_score,
        "per_modality_score": report.per_modality_score,
        "gate_passes": report.gate_passes,
        "runs": [
            {
                "query_id": r.query_id,
                "modality": r.modality,
                "score": r.score,
                "dimensions": r.dimensions,
                "passes": r.passes,
                "reasons": r.reasons,
            }
            for r in report.runs
        ],
    }
    if fmt == "json":
        return json.dumps(payload, indent=2, sort_keys=True)
    # Human-readable compact summary
    lines = [
        f"overall: {report.overall_score:.3f}/5  ({report.total_queries} queries)",
        f"passed {report.passed}  flagged {report.flagged}  failed {report.failed}",
        "per modality:",
    ]
    for mod, score in sorted(report.per_modality_score.items()):
        mark = "OK" if score >= 4.6 else "FAIL"
        lines.append(f"  {mark} {mod:10s} {score:.3f}")
    return "\n".join(lines)


async def _main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Run the RAG retrieval quality gate."
    )
    parser.add_argument("--tenant", help="Tenant UUID to probe live.")
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="Offline dry-run using canned high-quality retrieval.",
    )
    parser.add_argument("--baseline", help="Optional JSONL baseline to diff against.")
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="text",
    )
    parser.add_argument(
        "--output",
        help="Write the JSON report to this file in addition to stdout.",
    )
    args = parser.parse_args(argv)

    if not args.tenant and not args.fixture:
        print("error: --tenant or --fixture required", file=sys.stderr)
        return 2
    if args.tenant:
        try:
            uuid.UUID(args.tenant)
        except ValueError:
            print(f"error: --tenant must be a UUID (got {args.tenant!r})", file=sys.stderr)
            return 2

    corpus = load_gold_corpus()
    report = await _run_eval(
        tenant_id=args.tenant,
        fixture=args.fixture,
        corpus=corpus,
    )

    output = _emit_report(report, fmt=args.format)
    print(output)
    if args.output:
        Path(args.output).write_text(_emit_report(report, fmt="json"), encoding="utf-8")

    # Baseline regression check
    if args.baseline:
        baseline = load_baseline(args.baseline)
        regressions = detect_regressions(report, baseline)
        if regressions:
            print(
                f"\n[warn] regressions vs. baseline: {regressions}",
                file=sys.stderr,
            )
            return 1

    passes, reason = gate_decision(report)
    if not passes:
        print(f"\n[FAIL] GATE FAILED: {reason}", file=sys.stderr)
        return 1
    print(f"\n[OK] GATE PASSED: {reason}")
    return 0


def main() -> int:
    return asyncio.run(_main_async(sys.argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
