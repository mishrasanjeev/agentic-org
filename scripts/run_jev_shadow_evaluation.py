"""Run the explicit, redacted Jev routing shadow evaluation.

The default command is a local dry-run. Provider calls require ``--live`` and
the server-side ``TYPESAFE_API_KEY`` environment variable. This script never
executes tools, changes agent routing, or prints credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Allow direct execution from the repository root without requiring an
# editable install first.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import external_keys, settings  # noqa: E402
from core.decisioning.evaluation import (  # noqa: E402
    RoutingEvaluationReport,
    assess_routing_evaluation_report,
    build_routing_evaluation_plan,
    evaluate_routing_cases,
    load_routing_cases,
)  # noqa: E402
from core.decisioning.jev import JevDecisionProvider  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate the corpus and print the plan (default)")
    mode.add_argument("--live", action="store_true", help="make bounded Jev evaluation calls")
    parser.add_argument("--corpus", type=Path, default=None, help="synthetic routing corpus JSON path")
    parser.add_argument("--output", type=Path, help="write the redacted plan/report JSON to this path")
    parser.add_argument("--force", action="store_true", help="replace an existing output file")
    parser.add_argument("--max-calls", type=int, default=100)
    parser.add_argument("--failure-threshold", type=int, default=3)
    parser.add_argument("--sample-rate", type=float, default=1.0)
    parser.add_argument("--cooldown-seconds", type=float, default=60.0)
    parser.add_argument("--min-agreement-rate", type=float, default=0.95)
    parser.add_argument("--max-failures", type=int, default=0)
    parser.add_argument("--max-p95-latency-ms", type=float, default=800.0)
    parser.add_argument("--input-cost-per-million-usd", type=float)
    parser.add_argument("--output-cost-per-million-usd", type=float)
    parser.add_argument("--max-estimated-cost-usd", type=float)
    parser.add_argument("--timeout-seconds", type=float, default=settings.jev_timeout_seconds)
    parser.add_argument("--base-url", default=external_keys.typesafe_api_base_url)
    parser.add_argument("--model", default=external_keys.typesafe_model)
    return parser


def _write_or_print(document: dict[str, Any], output: Path | None, *, force: bool) -> None:
    rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(rendered, end="")  # noqa: T201
        return
    if output.exists() and not force:
        raise FileExistsError(f"output exists; pass --force to replace it: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(f"Redacted Jev evaluation written to: {output}")  # noqa: T201


def _plan_document(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_routing_cases(args.corpus) if args.corpus else load_routing_cases()
    plan = build_routing_evaluation_plan(
        cases,
        sample_rate=args.sample_rate,
        max_calls=args.max_calls,
        failure_threshold=args.failure_threshold,
        cooldown_seconds=args.cooldown_seconds,
    )
    plan["reporting"]["status"] = "not_run"
    return {
        "schema_version": "agenticorg.jev-shadow-plan.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "document_kind": "jev_shadow_evaluation_plan",
        "plan": plan,
    }


async def _run_live(args: argparse.Namespace) -> dict[str, Any]:
    api_key = os.getenv("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("TYPESAFE_API_KEY is required for --live and is never printed")
    if args.input_cost_per_million_usd is None and args.output_cost_per_million_usd is not None:
        raise ValueError("both cost rates are required together")
    if args.output_cost_per_million_usd is None and args.input_cost_per_million_usd is not None:
        raise ValueError("both cost rates are required together")

    cases = load_routing_cases(args.corpus) if args.corpus else load_routing_cases()
    provider = JevDecisionProvider(
        api_key,
        base_url=args.base_url,
        model=args.model,
        timeout_seconds=args.timeout_seconds,
    )
    report: RoutingEvaluationReport = await evaluate_routing_cases(
        cases,
        provider,
        max_calls=args.max_calls,
        failure_threshold=args.failure_threshold,
        sample_rate=args.sample_rate,
        input_cost_per_million_usd=args.input_cost_per_million_usd,
        output_cost_per_million_usd=args.output_cost_per_million_usd,
    )
    gates = assess_routing_evaluation_report(
        report,
        minimum_agreement_rate=args.min_agreement_rate,
        maximum_failures=args.max_failures,
        maximum_p95_latency_ms=args.max_p95_latency_ms,
        maximum_estimated_cost_usd=args.max_estimated_cost_usd,
    )
    return {
        "schema_version": "agenticorg.jev-shadow-report.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "document_kind": "jev_shadow_evaluation_report",
        "provider": "jev",
        "execution_mode": "offline_provider_evaluation",
        "active_routing_enabled": False,
        "non_executing": True,
        "report": report.to_dict(),
        "gates": gates,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.live:
            document = asyncio.run(_run_live(args))
            _write_or_print(document, args.output, force=args.force)
            return 0 if document["gates"]["passed"] else 1
        document = _plan_document(args)
        _write_or_print(document, args.output, force=args.force)
        return 0
    except (FileExistsError, OSError, ValueError) as exc:
        print(f"Jev shadow evaluation refused: {exc}", file=sys.stderr)  # noqa: T201
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
