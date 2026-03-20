"""Shadow mode comparator -- all 6 quality gates.

Quality gates
-------------
1. output_accuracy        -- Structural + semantic similarity of outputs
2. confidence_calibration -- Pearson correlation of confidence scores (r >= 0.70)
3. hitl_rate_comparison   -- HITL trigger rates within +/- 5 percentage points
4. hallucination_detection-- Shadow output must not contain data absent from tool results
5. tool_error_rate        -- Shadow tool error rate < 2 %
6. latency_comparison     -- Shadow P95 latency <= reference P95 x 1.3
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Gate result container
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    """Result of a single quality gate evaluation."""
    gate: str
    passed: bool
    score: float
    threshold: float
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ShadowComparator
# ---------------------------------------------------------------------------


class ShadowComparator:
    """Runs all 6 quality gates for shadow-mode agent comparison."""

    # Default thresholds (overridable at construction time)
    DEFAULT_ACCURACY_THRESHOLD: float = 0.90
    DEFAULT_CONFIDENCE_R_THRESHOLD: float = 0.70
    DEFAULT_HITL_RATE_TOLERANCE_PP: float = 5.0    # percentage points
    DEFAULT_HALLUCINATION_TOLERANCE: float = 0.0    # zero tolerance
    DEFAULT_TOOL_ERROR_RATE_THRESHOLD: float = 0.02  # 2 %
    DEFAULT_LATENCY_MULTIPLIER: float = 1.3

    def __init__(
        self,
        *,
        accuracy_threshold: float | None = None,
        confidence_r_threshold: float | None = None,
        hitl_rate_tolerance_pp: float | None = None,
        hallucination_tolerance: float | None = None,
        tool_error_rate_threshold: float | None = None,
        latency_multiplier: float | None = None,
    ) -> None:
        self.accuracy_threshold = accuracy_threshold or self.DEFAULT_ACCURACY_THRESHOLD
        self.confidence_r_threshold = confidence_r_threshold or self.DEFAULT_CONFIDENCE_R_THRESHOLD
        self.hitl_rate_tolerance_pp = hitl_rate_tolerance_pp or self.DEFAULT_HITL_RATE_TOLERANCE_PP
        self.hallucination_tolerance = (
            hallucination_tolerance if hallucination_tolerance is not None
            else self.DEFAULT_HALLUCINATION_TOLERANCE
        )
        self.tool_error_rate_threshold = tool_error_rate_threshold or self.DEFAULT_TOOL_ERROR_RATE_THRESHOLD
        self.latency_multiplier = latency_multiplier or self.DEFAULT_LATENCY_MULTIPLIER

    # ------------------------------------------------------------------
    # 1. Output accuracy
    # ------------------------------------------------------------------

    async def output_accuracy(
        self,
        shadow_output: dict[str, Any],
        reference_output: dict[str, Any],
    ) -> GateResult:
        """Compare shadow and reference outputs for structural similarity.

        The score is the fraction of matching key-value pairs over the union of
        keys, with deep comparison for nested dicts/lists.
        """
        exact_match = shadow_output == reference_output
        score = 1.0 if exact_match else self._compute_similarity(shadow_output, reference_output)
        passed = score >= self.accuracy_threshold

        return GateResult(
            gate="output_accuracy",
            passed=passed,
            score=score,
            threshold=self.accuracy_threshold,
            details={
                "exact_match": exact_match,
                "shadow_keys": sorted(shadow_output.keys()) if shadow_output else [],
                "reference_keys": sorted(reference_output.keys()) if reference_output else [],
            },
        )

    def _compute_similarity(self, a: dict, b: dict) -> float:
        """Deep structural similarity between two dicts."""
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0

        all_keys = set(a.keys()) | set(b.keys())
        if not all_keys:
            return 1.0

        matches = 0.0
        for k in all_keys:
            if k not in a or k not in b:
                continue
            va, vb = a[k], b[k]
            if va == vb:
                matches += 1.0
            elif isinstance(va, dict) and isinstance(vb, dict):
                matches += self._compute_similarity(va, vb)
            elif isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                # Numeric closeness (within 1 % relative error)
                denom = max(abs(va), abs(vb), 1e-9)
                if abs(va - vb) / denom < 0.01:
                    matches += 1.0
                else:
                    matches += max(0.0, 1.0 - abs(va - vb) / denom)
            elif isinstance(va, str) and isinstance(vb, str):
                # Simple character-level overlap
                if va.lower() == vb.lower():
                    matches += 0.95
                else:
                    common = sum(1 for c in va if c in vb)
                    matches += common / max(len(va), len(vb), 1)

        return matches / len(all_keys)

    # ------------------------------------------------------------------
    # 2. Confidence calibration
    # ------------------------------------------------------------------

    async def confidence_calibration(
        self,
        shadow_confidences: list[float],
        reference_confidences: list[float],
    ) -> GateResult:
        """Compute Pearson r between shadow and reference confidence scores.

        Requires at least 3 paired samples. If fewer, the gate passes
        trivially (not enough data to judge).
        """
        n = min(len(shadow_confidences), len(reference_confidences))

        if n < 3:
            return GateResult(
                gate="confidence_calibration",
                passed=True,
                score=1.0,
                threshold=self.confidence_r_threshold,
                details={"reason": "insufficient_samples", "n": n},
            )

        s = shadow_confidences[:n]
        r = reference_confidences[:n]
        pearson_r = self._pearson(s, r)
        passed = pearson_r >= self.confidence_r_threshold

        return GateResult(
            gate="confidence_calibration",
            passed=passed,
            score=pearson_r,
            threshold=self.confidence_r_threshold,
            details={
                "pearson_r": pearson_r,
                "n": n,
                "shadow_mean": statistics.mean(s),
                "reference_mean": statistics.mean(r),
            },
        )

    @staticmethod
    def _pearson(x: list[float], y: list[float]) -> float:
        """Compute Pearson correlation coefficient."""
        n = len(x)
        if n == 0:
            return 0.0
        mean_x = sum(x) / n
        mean_y = sum(y) / n

        cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
        std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))

        if std_x < 1e-12 or std_y < 1e-12:
            return 0.0

        return cov / (std_x * std_y)

    # ------------------------------------------------------------------
    # 3. HITL rate comparison
    # ------------------------------------------------------------------

    async def hitl_rate_comparison(
        self,
        shadow_hitl_rate: float,
        reference_hitl_rate: float,
    ) -> GateResult:
        """Check that HITL trigger rates are within tolerance.

        Rates are expressed as percentages (0-100).
        """
        diff_pp = abs(shadow_hitl_rate - reference_hitl_rate)
        passed = diff_pp <= self.hitl_rate_tolerance_pp

        return GateResult(
            gate="hitl_rate_comparison",
            passed=passed,
            score=diff_pp,
            threshold=self.hitl_rate_tolerance_pp,
            details={
                "shadow_hitl_rate_pct": shadow_hitl_rate,
                "reference_hitl_rate_pct": reference_hitl_rate,
                "difference_pp": diff_pp,
            },
        )

    # ------------------------------------------------------------------
    # 4. Hallucination detection
    # ------------------------------------------------------------------

    async def hallucination_detection(
        self,
        shadow_output: dict[str, Any],
        tool_call_results: list[dict[str, Any]],
    ) -> GateResult:
        """Check if shadow output contains data not present in tool call results.

        Flattens both the shadow output and tool results into sets of
        ``(key, value)`` leaf pairs and checks for ungrounded leaves.
        """
        shadow_leaves = self._flatten_leaves(shadow_output)
        tool_leaves: set[str] = set()
        for result in tool_call_results:
            tool_leaves |= self._flatten_leaves(result)

        # Also include string-level containment: a shadow value is grounded
        # if any tool result contains it as a substring.
        tool_text_corpus = " ".join(str(v) for r in tool_call_results for v in self._leaf_values(r))

        ungrounded: set[str] = set()
        for leaf in shadow_leaves:
            # leaf is "key=value" string
            if leaf in tool_leaves:
                continue
            # Check substring containment for the value portion
            value_part = leaf.split("=", 1)[1] if "=" in leaf else leaf
            if value_part and value_part in tool_text_corpus:
                continue
            # Ignore purely structural keys (empty values, booleans, small ints)
            if value_part in ("", "True", "False", "None") or (
                value_part.lstrip("-").isdigit() and abs(int(value_part)) < 100
            ):
                continue
            ungrounded.add(leaf)

        hallucination_rate = len(ungrounded) / max(len(shadow_leaves), 1)
        passed = hallucination_rate <= self.hallucination_tolerance

        return GateResult(
            gate="hallucination_detection",
            passed=passed,
            score=hallucination_rate,
            threshold=self.hallucination_tolerance,
            details={
                "shadow_leaf_count": len(shadow_leaves),
                "ungrounded_count": len(ungrounded),
                "ungrounded_samples": sorted(ungrounded)[:10],
            },
        )

    @staticmethod
    def _flatten_leaves(d: dict | list | Any, prefix: str = "") -> set[str]:
        """Recursively flatten a nested structure into ``key=value`` leaf strings."""
        leaves: set[str] = set()
        if isinstance(d, dict):
            for k, v in d.items():
                path = f"{prefix}.{k}" if prefix else k
                leaves |= ShadowComparator._flatten_leaves(v, path)
        elif isinstance(d, (list, tuple)):
            for i, v in enumerate(d):
                path = f"{prefix}[{i}]"
                leaves |= ShadowComparator._flatten_leaves(v, path)
        else:
            leaves.add(f"{prefix}={d}")
        return leaves

    @staticmethod
    def _leaf_values(d: dict | list | Any) -> list[Any]:
        """Extract all leaf values from a nested structure."""
        values: list[Any] = []
        if isinstance(d, dict):
            for v in d.values():
                values.extend(ShadowComparator._leaf_values(v))
        elif isinstance(d, (list, tuple)):
            for v in d:
                values.extend(ShadowComparator._leaf_values(v))
        else:
            values.append(d)
        return values

    # ------------------------------------------------------------------
    # 5. Tool error rate
    # ------------------------------------------------------------------

    async def tool_error_rate(
        self,
        shadow_tool_total: int,
        shadow_tool_errors: int,
    ) -> GateResult:
        """Check that the shadow agent's tool error rate is below threshold."""
        rate = shadow_tool_errors / max(shadow_tool_total, 1)
        passed = rate < self.tool_error_rate_threshold

        return GateResult(
            gate="tool_error_rate",
            passed=passed,
            score=rate,
            threshold=self.tool_error_rate_threshold,
            details={
                "total_calls": shadow_tool_total,
                "error_calls": shadow_tool_errors,
                "error_rate": rate,
            },
        )

    # ------------------------------------------------------------------
    # 6. Latency comparison
    # ------------------------------------------------------------------

    async def latency_comparison(
        self,
        shadow_latencies_ms: list[float],
        reference_p95_ms: float,
    ) -> GateResult:
        """Check shadow P95 latency is within acceptable multiplier of reference.

        shadow_latencies_ms:
            Raw latency samples from the shadow agent (milliseconds).
        reference_p95_ms:
            The reference agent's measured P95 latency (milliseconds).
        """
        if not shadow_latencies_ms:
            return GateResult(
                gate="latency_comparison",
                passed=True,
                score=0.0,
                threshold=reference_p95_ms * self.latency_multiplier,
                details={"reason": "no_shadow_samples"},
            )

        sorted_latencies = sorted(shadow_latencies_ms)
        idx = int(math.ceil(0.95 * len(sorted_latencies))) - 1
        shadow_p95 = sorted_latencies[max(idx, 0)]

        threshold_ms = reference_p95_ms * self.latency_multiplier
        passed = shadow_p95 <= threshold_ms

        return GateResult(
            gate="latency_comparison",
            passed=passed,
            score=shadow_p95,
            threshold=threshold_ms,
            details={
                "shadow_p95_ms": shadow_p95,
                "reference_p95_ms": reference_p95_ms,
                "multiplier": self.latency_multiplier,
                "threshold_ms": threshold_ms,
                "shadow_sample_count": len(shadow_latencies_ms),
                "shadow_median_ms": statistics.median(sorted_latencies),
            },
        )

    # ------------------------------------------------------------------
    # Full quality check
    # ------------------------------------------------------------------

    async def full_quality_check(
        self,
        *,
        shadow_output: dict[str, Any],
        reference_output: dict[str, Any],
        shadow_confidences: list[float] | None = None,
        reference_confidences: list[float] | None = None,
        shadow_hitl_rate: float = 0.0,
        reference_hitl_rate: float = 0.0,
        tool_call_results: list[dict[str, Any]] | None = None,
        shadow_tool_total: int = 0,
        shadow_tool_errors: int = 0,
        shadow_latencies_ms: list[float] | None = None,
        reference_p95_ms: float = 0.0,
    ) -> dict[str, Any]:
        """Run all 6 quality gates and return aggregate pass/fail with details.

        Returns
        -------
        dict with keys:
            passed: bool           -- True only if ALL gates pass
            gates_passed: int      -- count of passing gates
            gates_total: int       -- always 6
            gates: list[dict]      -- per-gate result dicts
            summary: str           -- human-readable summary
        """
        results: list[GateResult] = []

        # 1. Output accuracy
        results.append(await self.output_accuracy(shadow_output, reference_output))

        # 2. Confidence calibration
        results.append(
            await self.confidence_calibration(
                shadow_confidences or [],
                reference_confidences or [],
            )
        )

        # 3. HITL rate
        results.append(
            await self.hitl_rate_comparison(shadow_hitl_rate, reference_hitl_rate)
        )

        # 4. Hallucination detection
        results.append(
            await self.hallucination_detection(
                shadow_output,
                tool_call_results or [],
            )
        )

        # 5. Tool error rate
        results.append(
            await self.tool_error_rate(shadow_tool_total, shadow_tool_errors)
        )

        # 6. Latency comparison
        results.append(
            await self.latency_comparison(
                shadow_latencies_ms or [],
                reference_p95_ms,
            )
        )

        all_passed = all(g.passed for g in results)
        gates_passed = sum(1 for g in results if g.passed)
        failed_names = [g.gate for g in results if not g.passed]

        summary = (
            f"Shadow quality check: {gates_passed}/6 gates passed."
            if all_passed
            else f"Shadow quality check FAILED: {gates_passed}/6 gates passed. "
                 f"Failed: {', '.join(failed_names)}"
        )

        logger.info(
            "shadow_quality_check",
            passed=all_passed,
            gates_passed=gates_passed,
            failed=failed_names,
        )

        return {
            "passed": all_passed,
            "gates_passed": gates_passed,
            "gates_total": 6,
            "gates": [
                {
                    "gate": g.gate,
                    "passed": g.passed,
                    "score": g.score,
                    "threshold": g.threshold,
                    "details": g.details,
                }
                for g in results
            ],
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # Legacy compat
    # ------------------------------------------------------------------

    async def compare(
        self,
        shadow_output: dict[str, Any],
        reference_output: dict[str, Any],
    ) -> dict[str, Any]:
        """Backward-compatible comparison (delegates to output_accuracy)."""
        result = await self.output_accuracy(shadow_output, reference_output)
        return {
            "outputs_match": result.details.get("exact_match", False),
            "match_score": result.score,
            "passed": result.passed,
        }
