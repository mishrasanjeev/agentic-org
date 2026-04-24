"""Deterministic RAG retrieval quality rubric — closes S0-07.

Scores a retrieval run on a 0-5 scale across five dimensions. A run
passes the enterprise gate when the aggregate score is ≥ 4.6/5 AND
every critical modality also scores ≥ 4.6.

The rubric is intentionally deterministic + cheap so it can be run
inline during ingestion regression tests and as a release-branch CI
step. Heavy semantic judgement (LLM-as-judge) is a later
enhancement; PR-4 ships the contract + a text-similarity baseline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Enterprise release gate: score ≥ floor to sign off.
QUALITY_FLOOR = 4.6
# Publish/reject threshold used inside ingestion itself — chunks below
# this don't belong in the index at all.
INGESTION_FLOOR = 4.0


@dataclass
class GoldQuery:
    """A single (query → expected behaviour) row in the gold corpus."""

    id: str
    modality: str  # text | pdf | docx | xlsx | csv | image | audio | video
    query: str
    # Substrings that MUST appear in at least one retrieved chunk.
    expected_snippets: list[str] = field(default_factory=list)
    # Substrings that MUST NOT appear in any retrieved chunk (e.g. a
    # known-bad passage from a stale deploy).
    forbidden_snippets: list[str] = field(default_factory=list)
    # Notes for the review dashboard.
    notes: str = ""


@dataclass
class RetrievedChunk:
    """A single retrieval result."""

    text: str
    score: float
    source: str = ""
    page: int | None = None
    sheet: str | None = None
    mode: str = "pgvector"  # pgvector | ragflow | keyword | filename


@dataclass
class QueryRun:
    """One query → retrieval result pair with its computed score."""

    query_id: str
    modality: str
    retrieved: list[RetrievedChunk]
    score: float  # 0-5
    dimensions: dict[str, float]  # per-dim breakdown
    passes: bool
    reasons: list[str]


@dataclass
class EvalReport:
    """Aggregate of every QueryRun in a corpus sweep."""

    total_queries: int
    passed: int
    flagged: int
    failed: int
    overall_score: float
    per_modality_score: dict[str, float]
    runs: list[QueryRun] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def gate_passes(self) -> bool:
        """Enterprise release gate: overall >= floor AND every modality >= floor."""
        if self.overall_score < QUALITY_FLOOR:
            return False
        return all(score >= QUALITY_FLOOR for score in self.per_modality_score.values())


# ── Scoring dimensions ───────────────────────────────────────────────


def _score_semantic_match(
    query: GoldQuery, retrieved: list[RetrievedChunk]
) -> float:
    """Fraction of expected_snippets that appear in the retrieved chunks."""
    if not query.expected_snippets:
        return 1.0
    combined = "\n".join(chunk.text for chunk in retrieved)
    hits = sum(
        1 for snippet in query.expected_snippets if snippet.lower() in combined.lower()
    )
    return hits / len(query.expected_snippets)


def _score_no_forbidden(
    query: GoldQuery, retrieved: list[RetrievedChunk]
) -> float:
    """1.0 when none of the forbidden snippets appear; 0.0 when any does."""
    if not query.forbidden_snippets:
        return 1.0
    combined = "\n".join(chunk.text for chunk in retrieved)
    for snippet in query.forbidden_snippets:
        if snippet.lower() in combined.lower():
            return 0.0
    return 1.0


def _score_retrieval_mode(retrieved: list[RetrievedChunk]) -> float:
    """Penalise keyword / filename fallbacks.

    pgvector or ragflow → 1.0. keyword → 0.5. filename → 0.0. Mixed runs
    average. This is the dimension that prevents the "seeded content
    native, user-uploaded falls back to filename-only" gap Codex flagged
    on S0-06.
    """
    if not retrieved:
        return 0.0
    per_mode = []
    for chunk in retrieved:
        mode = (chunk.mode or "").lower()
        if mode in ("pgvector", "ragflow"):
            per_mode.append(1.0)
        elif mode == "keyword":
            per_mode.append(0.5)
        else:  # filename or unknown
            per_mode.append(0.0)
    return sum(per_mode) / len(per_mode)


def _score_response_length(retrieved: list[RetrievedChunk]) -> float:
    """Penalise empty + pathologically long retrieval bodies.

    0.0 if zero chunks. 0.5 if one chunk averages < 60 chars. 1.0 when
    at least three usable chunks exist and no single chunk exceeds 3000
    chars (which would indicate a chunker failure).
    """
    if not retrieved:
        return 0.0
    avg_len = sum(len(c.text) for c in retrieved) / len(retrieved)
    if avg_len < 60:
        return 0.5
    too_long = any(len(c.text) > 3000 for c in retrieved)
    if too_long:
        return 0.5
    if len(retrieved) >= 3:
        return 1.0
    return 0.8


def _score_rank_monotonic(retrieved: list[RetrievedChunk]) -> float:
    """1.0 when scores are monotonically non-increasing (ranker looks
    sane). 0.5 when one inversion. 0.0 when unranked / multiple
    inversions."""
    if len(retrieved) < 2:
        return 1.0
    inversions = 0
    for prev, cur in zip(retrieved, retrieved[1:], strict=False):
        if cur.score > prev.score + 1e-6:
            inversions += 1
    if inversions == 0:
        return 1.0
    if inversions == 1:
        return 0.5
    return 0.0


def score_run(query: GoldQuery, retrieved: list[RetrievedChunk]) -> QueryRun:
    """Compute the full 0-5 score for one query's retrieval."""
    dims = {
        "semantic_match": _score_semantic_match(query, retrieved),
        "no_forbidden": _score_no_forbidden(query, retrieved),
        "retrieval_mode": _score_retrieval_mode(retrieved),
        "response_length": _score_response_length(retrieved),
        "rank_monotonic": _score_rank_monotonic(retrieved),
    }
    # Each dimension is worth 1.0; sum to [0, 5].
    total = sum(dims.values())
    reasons = [name for name, v in dims.items() if v < 1.0]
    return QueryRun(
        query_id=query.id,
        modality=query.modality,
        retrieved=retrieved,
        score=round(total, 3),
        dimensions=dims,
        passes=total >= QUALITY_FLOOR,
        reasons=reasons,
    )


def aggregate(runs: list[QueryRun]) -> EvalReport:
    """Fold a batch of QueryRuns into an EvalReport."""
    if not runs:
        return EvalReport(
            total_queries=0,
            passed=0,
            flagged=0,
            failed=0,
            overall_score=0.0,
            per_modality_score={},
            runs=[],
        )
    passed = sum(1 for r in runs if r.score >= QUALITY_FLOOR)
    flagged = sum(
        1
        for r in runs
        if INGESTION_FLOOR <= r.score < QUALITY_FLOOR
    )
    failed = sum(1 for r in runs if r.score < INGESTION_FLOOR)
    overall = sum(r.score for r in runs) / len(runs)
    per_mod: dict[str, list[float]] = {}
    for run in runs:
        per_mod.setdefault(run.modality, []).append(run.score)
    per_mod_avg = {
        modality: round(sum(scores) / len(scores), 3)
        for modality, scores in per_mod.items()
    }
    return EvalReport(
        total_queries=len(runs),
        passed=passed,
        flagged=flagged,
        failed=failed,
        overall_score=round(overall, 3),
        per_modality_score=per_mod_avg,
        runs=runs,
    )


# ── Gold corpus ──────────────────────────────────────────────────────


def load_gold_corpus() -> list[GoldQuery]:
    """Canonical corpus. Every critical modality gets ≥ 2 queries.

    Extend by appending entries; DO NOT rewrite or reorder existing IDs
    — CI diffs the report against a baseline keyed by ``query_id``.
    """
    return [
        # ── text / markdown (8 queries) ────────────────────────────
        GoldQuery(
            id="text-01",
            modality="text",
            query="What was the total invoice amount?",
            expected_snippets=["Invoice", "total", "amount"],
        ),
        GoldQuery(
            id="text-02",
            modality="text",
            query="Summarise the onboarding SOP.",
            expected_snippets=["onboarding", "SOP"],
        ),
        GoldQuery(
            id="text-03",
            modality="text",
            query="Find the escalation policy for HITL approvals.",
            expected_snippets=["escalation", "HITL"],
        ),
        GoldQuery(
            id="text-04",
            modality="text",
            query="What is the refund cutoff window?",
            expected_snippets=["refund", "cutoff", "window"],
        ),
        GoldQuery(
            id="text-05",
            modality="text",
            query="List the vendor payment terms.",
            expected_snippets=["vendor", "payment", "terms"],
        ),
        GoldQuery(
            id="text-06",
            modality="text",
            query="How is PII redaction configured?",
            expected_snippets=["PII", "redaction"],
        ),
        GoldQuery(
            id="text-07",
            modality="text",
            query="Where is GSTN connector credential storage?",
            expected_snippets=["GSTN", "credential"],
        ),
        GoldQuery(
            id="text-08",
            modality="text",
            query="What is the quality gate target?",
            expected_snippets=["4.6", "quality"],
        ),
        # ── PDF (8 queries) ────────────────────────────────────────
        GoldQuery(
            id="pdf-01",
            modality="pdf",
            query="Find the repo rate from the RBI April 2026 press release.",
            expected_snippets=["repo rate", "Reserve Bank"],
        ),
        GoldQuery(
            id="pdf-02",
            modality="pdf",
            query="What was the Monetary Policy Committee's vote split?",
            expected_snippets=["Monetary Policy", "Committee"],
        ),
        GoldQuery(
            id="pdf-03",
            modality="pdf",
            query="Locate the GSTR-3B late filing penalty table.",
            expected_snippets=["GSTR", "penalty"],
        ),
        GoldQuery(
            id="pdf-04",
            modality="pdf",
            query="What were the Q4 consolidated revenues?",
            expected_snippets=["revenue", "consolidated"],
        ),
        GoldQuery(
            id="pdf-05",
            modality="pdf",
            query="Find the DSC certificate expiry guidance.",
            expected_snippets=["DSC", "certificate"],
        ),
        GoldQuery(
            id="pdf-06",
            modality="pdf",
            query="What payroll deduction rules apply?",
            expected_snippets=["payroll", "deduction"],
        ),
        GoldQuery(
            id="pdf-07",
            modality="pdf",
            query="Summarise the compliance calendar for April.",
            expected_snippets=["compliance", "calendar"],
        ),
        GoldQuery(
            id="pdf-08",
            modality="pdf",
            query="Find the PF contribution rate.",
            expected_snippets=["PF", "contribution"],
        ),
        # ── DOCX (8) ───────────────────────────────────────────────
        GoldQuery(
            id="docx-01",
            modality="docx",
            query="Find the signing authority clause.",
            expected_snippets=["signing", "authority"],
        ),
        GoldQuery(
            id="docx-02",
            modality="docx",
            query="What indemnity terms apply?",
            expected_snippets=["indemnity"],
        ),
        GoldQuery(
            id="docx-03",
            modality="docx",
            query="Locate the change-request approval matrix.",
            expected_snippets=["change", "request", "approval"],
        ),
        GoldQuery(
            id="docx-04",
            modality="docx",
            query="What is the contract renewal period?",
            expected_snippets=["contract", "renewal"],
        ),
        GoldQuery(
            id="docx-05",
            modality="docx",
            query="List the termination conditions.",
            expected_snippets=["termination"],
        ),
        GoldQuery(
            id="docx-06",
            modality="docx",
            query="Find the confidentiality obligations.",
            expected_snippets=["confidential"],
        ),
        GoldQuery(
            id="docx-07",
            modality="docx",
            query="What governing law applies?",
            expected_snippets=["governing", "law"],
        ),
        GoldQuery(
            id="docx-08",
            modality="docx",
            query="Summarise the liability cap.",
            expected_snippets=["liability"],
        ),
        # ── XLSX (8) ───────────────────────────────────────────────
        GoldQuery(
            id="xlsx-01",
            modality="xlsx",
            query="Find the FY24 revenue by region.",
            expected_snippets=["revenue", "region"],
        ),
        GoldQuery(
            id="xlsx-02",
            modality="xlsx",
            query="What were the headcount deltas per department?",
            expected_snippets=["headcount", "department"],
        ),
        GoldQuery(
            id="xlsx-03",
            modality="xlsx",
            query="Locate the top 5 aged payables.",
            expected_snippets=["aged", "payables"],
        ),
        GoldQuery(
            id="xlsx-04",
            modality="xlsx",
            query="Find the April reconciliation summary.",
            expected_snippets=["reconciliation", "April"],
        ),
        GoldQuery(
            id="xlsx-05",
            modality="xlsx",
            query="What is the marketing spend breakdown by campaign?",
            expected_snippets=["marketing", "spend"],
        ),
        GoldQuery(
            id="xlsx-06",
            modality="xlsx",
            query="Get the GST paid vs. collected deltas.",
            expected_snippets=["GST"],
        ),
        GoldQuery(
            id="xlsx-07",
            modality="xlsx",
            query="Find the procurement vendor scorecard.",
            expected_snippets=["vendor", "scorecard"],
        ),
        GoldQuery(
            id="xlsx-08",
            modality="xlsx",
            query="Summarise Q2 customer churn.",
            expected_snippets=["churn", "customer"],
        ),
    ]


# ── Convenience helpers ─────────────────────────────────────────────


def gate_decision(report: EvalReport) -> tuple[bool, str]:
    """Return ``(pass, reason)``. Suitable for CI exit codes."""
    if report.total_queries == 0:
        return False, "corpus empty — no queries ran"
    if report.overall_score < QUALITY_FLOOR:
        return (
            False,
            f"overall score {report.overall_score:.3f} below floor {QUALITY_FLOOR}",
        )
    for modality, score in report.per_modality_score.items():
        if score < QUALITY_FLOOR:
            return (
                False,
                f"modality {modality!r} score {score:.3f} below floor {QUALITY_FLOOR}",
            )
    return True, (
        f"overall {report.overall_score:.3f} >= {QUALITY_FLOOR} across "
        f"{report.total_queries} queries, all modalities pass"
    )


def load_baseline(baseline_path: str) -> dict[str, float]:
    """Load a pinned baseline so regressions surface per-query.

    File format: JSONL with ``{"query_id": "...", "score": 4.83}`` per
    line. Non-existent file returns empty dict (first-run mode).
    """
    import json as _json
    from pathlib import Path

    p = Path(baseline_path)
    if not p.exists():
        return {}
    out: dict[str, float] = {}
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = _json.loads(line)
                out[data["query_id"]] = float(data["score"])
            except (ValueError, KeyError):
                continue
    return out


def detect_regressions(
    report: EvalReport, baseline: dict[str, float], drop: float = 0.2
) -> list[str]:
    """Return query IDs whose current score dropped more than ``drop``
    points from the baseline."""
    offenders: list[str] = []
    for run in report.runs:
        old = baseline.get(run.query_id)
        if old is None:
            continue
        if math.isclose(old, run.score, abs_tol=1e-3):
            continue
        if run.score + drop < old:
            offenders.append(run.query_id)
    return offenders
