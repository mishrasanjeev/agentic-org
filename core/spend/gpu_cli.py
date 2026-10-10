# SPDX-License-Identifier: Apache-2.0
"""Operator command for in-house GPU node hours (``python -m core.spend.gpu_cli``).

Node hours are a deployment cost, so they are entered by the platform
operator, not through the tenant API::

    python -m core.spend.gpu_cli record --provider vllm --pool <pool> --models <m1,m2> \\
        --hour-start 2026-10-01T09:00:00Z [--hour-end 2026-10-01T12:00:00Z] --node-hours 2 \\
        --source metrics --actor <name>
    python -m core.spend.gpu_cli list --start 2026-10-01T00:00:00Z --end 2026-10-02T00:00:00Z

``record`` upserts a ``pending`` row per whole UTC hour of ``[hour-start,
hour-end)`` (one hour when no end is given; at most 744), for hours that have
ended and start at most seven days back; an hour already being allocated or
allocated is reported and left alone. The command logs what it recorded
(``spend_gpu_hours_recorded``: the actor, the pool, the first and last hour,
the node hours and the models) and, for each hour it overwrote, the values
the hour had before (``spend_gpu_hour_overwritten``); the audit log needs a
tenant, so a platform input is logged, not audited. Refused while spend
intelligence is off, and for an input the checks refuse (exit code 2).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from core.spend.errors import SpendError


def _instant(text: str) -> datetime:
    try:
        return datetime.fromisoformat(text.strip())
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO instant: {text!r}") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m core.spend.gpu_cli", description="In-house GPU node hours.")
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record", help="record node hours of a pool for whole UTC hours")
    record.add_argument("--provider", required=True, choices=["ollama", "vllm"])
    record.add_argument("--pool", required=True, help="the node pool (served models are listed with --models)")
    record.add_argument("--models", required=True, help="comma-separated model names the pool serves")
    record.add_argument("--hour-start", required=True, type=_instant)
    record.add_argument("--hour-end", type=_instant, default=None, help="exclusive; default one hour")
    record.add_argument("--node-hours", required=True, help="node hours of each hour (above 0, at most 10000)")
    record.add_argument("--source", required=True, choices=["metrics", "manual"])
    record.add_argument("--actor", required=True, help="who records the hours (logged)")
    listing = commands.add_parser("list", help="list pool hours starting in a window (at most 31 days)")
    listing.add_argument("--start", required=True, type=_instant)
    listing.add_argument("--end", required=True, type=_instant)
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    from core.spend import clock, gpu

    if args.command == "record":
        return await gpu.record_hours(
            now=clock.now_utc(),
            provider=args.provider,
            node_pool=args.pool,
            models=[m for m in str(args.models).split(",") if m.strip()],
            hour_start=args.hour_start,
            hour_end=args.hour_end,
            node_hours=args.node_hours,
            source=args.source,
            actor=args.actor,
        )
    return await gpu.list_pool_hours(start=args.start, end=args.end)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command; 0 on success, 2 when refused (spend off, or an input the checks refuse)."""
    from core import spend

    args = build_parser().parse_args(argv)
    if not spend.enabled():
        print("spend intelligence is off (AGENTICORG_SPEND_INTELLIGENCE_ENABLED): nothing recorded", file=sys.stderr)
        return 2
    try:
        out = asyncio.run(_run(args))
    except SpendError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - the operator entry point
    raise SystemExit(main())
