"""Eval runner — loads golden datasets, simulates agent output, scores, generates scorecard."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.scorer import (
    composite_score,
    cost_score,
    grade,
    performance_score,
    quality_score,
    reliability_score,
    safety_score,
    security_score,
)

GOLDEN_DIR = Path(__file__).resolve().parent / "golden_datasets"
DOMAINS = ["finance", "hr", "marketing", "ops"]


# ---------------------------------------------------------------------------
# Deterministic perturbation — simulates agent output that is *almost* correct
# ---------------------------------------------------------------------------

def _deterministic_seed(case_id: str) -> int:
    """Produce a stable integer seed from a case id (no randomness)."""
    return int(hashlib.md5(case_id.encode()).hexdigest()[:8], 16)  # noqa: S324


def _perturb_value(value: Any, seed: int) -> Any:
    """Apply a small deterministic perturbation to a value for realism."""
    # Vary numeric values by a tiny deterministic fraction
    if isinstance(value, float):
        # Perturbation between -2% and +2% based on seed
        factor = 1.0 + ((seed % 41) - 20) / 1000.0  # +-2%
        return round(value * factor, 2)
    if isinstance(value, int) and not isinstance(value, bool):
        # Small int perturbation (only for values > 10)
        if abs(value) > 10:
            delta = (seed % 3) - 1  # -1, 0, or +1
            return value + delta
        return value
    if isinstance(value, dict):
        return _perturb_dict(value, seed)
    if isinstance(value, list):
        return [_perturb_value(item, seed + i) for i, item in enumerate(value)]
    return value


def _perturb_dict(d: dict, seed: int) -> dict:
    """Deterministically perturb dict values."""
    result = {}
    for i, (k, v) in enumerate(d.items()):
        result[k] = _perturb_value(v, seed + i * 7)
    return result


def simulate_agent_output(case: dict) -> dict:
    """Simulate agent output by applying small deterministic perturbations to expected output."""
    seed = _deterministic_seed(case["id"])
    return _perturb_dict(case["expected_output"], seed)


# ---------------------------------------------------------------------------
# Simulate operational metrics (deterministic, based on case id)
# ---------------------------------------------------------------------------

def _simulate_metrics(case_id: str) -> dict:
    """Generate deterministic operational metrics for a test case."""
    seed = _deterministic_seed(case_id)
    # Latency: 800-2500ms range (deterministic)
    latency_ms = 800 + (seed % 1700)
    sla_ms = 3000
    # Retries: 0-2
    retries = seed % 3
    recovery = retries == 0 or (seed % 5 != 0)
    # Tokens: 1500-6000
    tokens_used = 1500 + (seed % 4500)
    token_budget = 8000
    # Scopes: 2-4
    all_scopes = ["read:data", "write:data", "execute:agent", "read:config", "admin:tenant"]
    n_scopes = 2 + (seed % 3)
    scopes = all_scopes[:n_scopes]
    # Violations: 0 most of the time
    violations: list[str] = []
    if seed % 17 == 0:
        violations = ["unauthorized_scope_escalation"]

    return {
        "latency_ms": latency_ms,
        "sla_ms": sla_ms,
        "retries": retries,
        "recovery_success": recovery,
        "tokens_used": tokens_used,
        "token_budget": token_budget,
        "scopes": scopes,
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# Core evaluation logic
# ---------------------------------------------------------------------------

def load_golden_dataset(domain: str) -> list[dict]:
    """Load a golden dataset by domain name."""
    path = GOLDEN_DIR / f"{domain}.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def evaluate_case(case: dict) -> dict:
    """Evaluate a single test case and return detailed scores."""
    actual_output = simulate_agent_output(case)
    metrics = _simulate_metrics(case["id"])

    scores = {
        "quality": quality_score(case["expected_output"], actual_output),
        "safety": safety_score(actual_output),
        "performance": performance_score(metrics["latency_ms"], metrics["sla_ms"]),
        "reliability": reliability_score(metrics["retries"], metrics["recovery_success"]),
        "security": security_score(metrics["scopes"], metrics["violations"]),
        "cost": cost_score(metrics["tokens_used"], metrics["token_budget"]),
    }

    comp = composite_score(scores)

    return {
        "case_id": case["id"],
        "agent_type": case["agent_type"],
        "domain": case.get("domain", ""),
        "description": case["description"],
        "scores": scores,
        "composite": comp,
        "grade": grade(comp),
        "metrics": {
            "latency_ms": metrics["latency_ms"],
            "retries": metrics["retries"],
            "tokens_used": metrics["tokens_used"],
        },
    }


def run_eval(
    domain_filter: str | None = None,
    agent_filter: str | None = None,
) -> dict:
    """Run evaluation across all (or filtered) golden datasets."""
    domains_to_run = [domain_filter] if domain_filter else DOMAINS
    all_results: list[dict] = []
    domain_aggregates: dict[str, dict] = {}

    for domain in domains_to_run:
        cases = load_golden_dataset(domain)
        if agent_filter:
            cases = [c for c in cases if c["agent_type"] == agent_filter]
        if not cases:
            continue

        domain_results = []
        for case in cases:
            case["domain"] = domain  # inject domain from filename
            result = evaluate_case(case)
            domain_results.append(result)
            all_results.append(result)

        # Aggregate for domain
        if domain_results:
            domain_scores: dict[str, list[float]] = {}
            for r in domain_results:
                for dim, val in r["scores"].items():
                    domain_scores.setdefault(dim, []).append(val)

            domain_composites = [r["composite"] for r in domain_results]
            domain_aggregates[domain] = {
                "cases_evaluated": len(domain_results),
                "avg_scores": {dim: round(sum(vals) / len(vals), 4) for dim, vals in domain_scores.items()},
                "avg_composite": round(sum(domain_composites) / len(domain_composites), 4),
                "grade": grade(sum(domain_composites) / len(domain_composites)),
                "min_composite": round(min(domain_composites), 4),
                "max_composite": round(max(domain_composites), 4),
            }

    # Agent-level aggregates
    agent_aggregates: dict[str, dict] = {}
    agent_groups: dict[str, list[dict]] = {}
    for r in all_results:
        agent_groups.setdefault(r["agent_type"], []).append(r)

    for agent_type, results in agent_groups.items():
        agent_scores: dict[str, list[float]] = {}
        for r in results:
            for dim, val in r["scores"].items():
                agent_scores.setdefault(dim, []).append(val)

        composites = [r["composite"] for r in results]
        agent_aggregates[agent_type] = {
            "cases_evaluated": len(results),
            "avg_scores": {dim: round(sum(vals) / len(vals), 4) for dim, vals in agent_scores.items()},
            "avg_composite": round(sum(composites) / len(composites), 4),
            "grade": grade(sum(composites) / len(composites)),
            "min_composite": round(min(composites), 4),
            "max_composite": round(max(composites), 4),
        }

    # Platform-level metrics
    all_composites = [r["composite"] for r in all_results]
    platform_metrics = {}
    if all_composites:
        platform_metrics = {
            "total_cases": len(all_results),
            "total_agents": len(agent_aggregates),
            "total_domains": len(domain_aggregates),
            "avg_composite": round(sum(all_composites) / len(all_composites), 4),
            "grade": grade(sum(all_composites) / len(all_composites)),
            "min_composite": round(min(all_composites), 4),
            "max_composite": round(max(all_composites), 4),
            "agents_passing": sum(1 for a in agent_aggregates.values() if a["avg_composite"] >= 0.80),
            "agents_failing": sum(1 for a in agent_aggregates.values() if a["avg_composite"] < 0.80),
        }

    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "version": "1.0.0",
        "platform_metrics": platform_metrics,
        "domain_aggregates": domain_aggregates,
        "agent_aggregates": agent_aggregates,
        "case_results": all_results,
    }


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="AgenticOrg Evaluation Runner")
    parser.add_argument("--domain", type=str, default=None, help="Filter by domain (finance, hr, marketing, ops)")
    parser.add_argument("--agent", type=str, default=None, help="Filter by agent type")
    parser.add_argument("--output", type=str, default="scorecard.json", help="Output path for scorecard")
    parser.add_argument("--ci", action="store_true", help="CI mode — exit 1 if any agent composite < 0.80")
    args = parser.parse_args()

    scorecard = run_eval(domain_filter=args.domain, agent_filter=args.agent)

    # Write scorecard
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(scorecard, f, indent=2, ensure_ascii=False)

    # Print summary
    pm = scorecard["platform_metrics"]
    if pm:
        print(f"Evaluation complete: {pm['total_cases']} cases across {pm['total_domains']} domains")  # noqa: T201
        print(f"Platform grade: {pm['grade']} (avg composite: {pm['avg_composite']:.4f})")  # noqa: T201
        print(f"Agents passing: {pm['agents_passing']}/{pm['total_agents']}")  # noqa: T201
    else:
        print("No cases evaluated.")  # noqa: T201

    print(f"\nScorecard written to: {output_path}")  # noqa: T201

    # CI gate
    if args.ci:
        failing = [
            agent_type
            for agent_type, agg in scorecard["agent_aggregates"].items()
            if agg["avg_composite"] < 0.80
        ]
        if failing:
            print(f"\nCI GATE FAILED — {len(failing)} agent(s) below 0.80 threshold:")  # noqa: T201
            for agent in failing:
                agg = scorecard["agent_aggregates"][agent]
                print(f"  {agent}: {agg['avg_composite']:.4f} ({agg['grade']})")  # noqa: T201
            sys.exit(1)
        else:
            print("\nCI GATE PASSED — all agents above 0.80 threshold.")  # noqa: T201


if __name__ == "__main__":
    main()
