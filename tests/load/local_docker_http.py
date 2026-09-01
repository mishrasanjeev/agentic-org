"""Repeatable HTTP stress harness for a local AgenticOrg Docker container."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx


@dataclass(frozen=True)
class PhaseResult:
    name: str
    path: str
    requests: int
    concurrency: int
    elapsed_seconds: float
    requests_per_second: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    statuses: dict[str, int]
    error_count: int
    errors: dict[str, int]


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 2)


async def _run_phase(
    client: httpx.AsyncClient,
    *,
    name: str,
    path: str,
    requests: int,
    concurrency: int,
) -> PhaseResult:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    statuses: Counter[str] = Counter()
    errors: Counter[str] = Counter()

    async def request_once() -> None:
        async with semaphore:
            started = time.perf_counter()
            try:
                response = await client.get(path)
                statuses[str(response.status_code)] += 1
            except Exception as exc:  # enterprise-gate: broad-except-ok reason=load-harness-aggregates-client-errors
                errors[type(exc).__name__] += 1
            finally:
                latencies.append((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    await asyncio.gather(*(request_once() for _ in range(requests)))
    elapsed = time.perf_counter() - started
    return PhaseResult(
        name=name,
        path=path,
        requests=requests,
        concurrency=concurrency,
        elapsed_seconds=round(elapsed, 3),
        requests_per_second=round(requests / elapsed, 2),
        p50_ms=_percentile(latencies, 0.50),
        p95_ms=_percentile(latencies, 0.95),
        p99_ms=_percentile(latencies, 0.99),
        max_ms=round(max(latencies), 2),
        statuses=dict(statuses),
        error_count=sum(errors.values()),
        errors=dict(errors),
    )


async def _run(args: argparse.Namespace) -> dict[str, object]:
    maximum = max(args.steady_concurrency, args.burst_concurrency)
    limits = httpx.Limits(max_connections=maximum, max_keepalive_connections=maximum)
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        limits=limits,
        timeout=httpx.Timeout(args.timeout_seconds),
    ) as client:
        phases = [
            await _run_phase(
                client,
                name="liveness_steady",
                path="/api/v1/health/liveness",
                requests=args.steady_requests,
                concurrency=args.steady_concurrency,
            ),
            await _run_phase(
                client,
                name="dependency_readiness",
                path="/api/v1/health",
                requests=args.readiness_requests,
                concurrency=args.steady_concurrency,
            ),
            await _run_phase(
                client,
                name="liveness_burst",
                path="/api/v1/health/liveness",
                requests=args.burst_requests,
                concurrency=args.burst_concurrency,
            ),
        ]

    failures: list[str] = []
    for phase in phases:
        if phase.error_count:
            failures.append(f"{phase.name}: {phase.error_count} client errors")
        non_200 = sum(count for status, count in phase.statuses.items() if status != "200")
        if non_200:
            failures.append(f"{phase.name}: {non_200} non-200 responses")
    readiness = next(phase for phase in phases if phase.name == "dependency_readiness")
    if readiness.p95_ms > args.max_readiness_p95_ms:
        failures.append(
            f"dependency_readiness: p95 {readiness.p95_ms}ms exceeds "
            f"{args.max_readiness_p95_ms}ms"
        )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "workload": "local_docker_http",
        "phases": [asdict(phase) for phase in phases],
        "thresholds": {"max_readiness_p95_ms": args.max_readiness_p95_ms},
        "passed": not failures,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--steady-requests", type=int, default=3000)
    parser.add_argument("--readiness-requests", type=int, default=1500)
    parser.add_argument("--burst-requests", type=int, default=5000)
    parser.add_argument("--steady-concurrency", type=int, default=50)
    parser.add_argument("--burst-concurrency", type=int, default=150)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--max-readiness-p95-ms", type=float, default=750.0)
    args = parser.parse_args()

    report = asyncio.run(_run(args))
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
