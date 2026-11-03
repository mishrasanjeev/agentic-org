# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Seed a synthetic performance tenant for the Insights benchmark (docs/INSIGHTS_DASHBOARD_PRD.md, Performance).

One tenant with 40 agents over 12 task types and ``--runs`` run facts (default 1,000,000) spread over the
``--days`` days before today (default 30): log-normal durations, 8% failures, 15% sent to a person, confidence
around 0.85 with a 0.7 floor, token and cost figures. Every row is labelled ``data_quality = seeded``. The closed
days are then rolled up with the real rollup (``core.insights.rollup.rollup_day``), so the landing views read the
daily table as they do in production.

It writes only to a local database unless ``--allow-remote`` is given: a performance tenant never belongs in a
shared environment by accident. The database must already be migrated to head.

    AGENTICORG_DB_URL=postgresql+asyncpg://user:pass@127.0.0.1:5432/db \\
        python scripts/seed_perf_tenant.py [--runs 1000000] [--days 30] [--seed 7]

Prints the tenant id, which ``scripts/insights_perf_bench.py --tenant`` takes.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import math
import os
import random
import sys
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
AGENTS = 40
TASK_TYPES = (
    "invoice_match",
    "payment_run",
    "gst_return",
    "expense_review",
    "leave_request",
    "payroll_check",
    "lead_scoring",
    "content_draft",
    "order_triage",
    "vendor_onboarding",
    "contract_review",
    "kyc_check",
)
DOMAINS = ("finance", "hr", "marketing", "ops", "commerce", "backoffice")
FAILURE_RATE = 0.08
HITL_RATE = 0.15
BATCH = 100_000
COLUMNS = (
    "id",
    "tenant_id",
    "run_key",
    "entrypoint",
    "agent_id",
    "agent_type",
    "domain",
    "task_type",
    "environment",
    "status",
    "confidence",
    "confidence_floor",
    "duration_ms",
    "hitl_required",
    "tokens_in",
    "tokens_out",
    "tokens_cached",
    "tokens_total",
    "cost_usd",
    "provider",
    "model",
    "error_class",
    "outcome_validated",
    "data_quality",
    "started_at",
)


def _sync_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")


def _refuse_remote(url: str, allow_remote: bool) -> None:
    host = urlparse(_sync_dsn(url)).hostname or ""
    if host not in LOCAL_HOSTS and not allow_remote:
        raise SystemExit(f"refusing to seed a performance tenant on {host!r}; pass --allow-remote for a staging database")


def _agents(rng: random.Random) -> list[dict[str, str]]:
    out = []
    for i in range(AGENTS):
        task = TASK_TYPES[i % len(TASK_TYPES)]
        out.append(
            {
                "id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
                "name": f"Perf agent {i + 1:02d}",
                "agent_type": task.split("_")[0] + "_bot",
                "domain": DOMAINS[i % len(DOMAINS)],
                "task_type": task,
            }
        )
    return out


def _rows(rng: random.Random, tenant: str, agents: list[dict[str, str]], runs: int, start: datetime, span: float):
    """Yield one tab-separated COPY line per run fact."""
    for n in range(runs):
        agent = agents[rng.randrange(AGENTS)]
        failed = rng.random() < FAILURE_RATE
        hitl = rng.random() < HITL_RATE
        status = "failed" if failed else "completed"
        duration = int(min(600_000, math.exp(rng.gauss(7.5, 0.9))))  # median about 1.8 s
        tokens_in = rng.randint(200, 4000)
        tokens_out = rng.randint(50, 1200)
        cached = rng.randint(0, tokens_in // 4)
        cost = (tokens_in * 0.0000003 + tokens_out * 0.0000025)
        confidence = max(0.0, min(1.0, rng.gauss(0.85, 0.08)))
        started = start + timedelta(seconds=rng.random() * span)
        yield "\t".join(
            (
                str(uuid.UUID(int=rng.getrandbits(128), version=4)),
                tenant,
                f"perf:{n}",
                "agents",
                agent["id"],
                agent["agent_type"],
                agent["domain"],
                agent["task_type"],
                "production",
                status,
                f"{confidence:.4f}",
                "0.7",
                str(duration),
                "t" if hitl else "f",
                str(tokens_in),
                str(tokens_out),
                str(cached),
                str(tokens_in + tokens_out),
                f"{cost:.6f}",
                "gemini",
                "gemini-2.5-flash",
                "TimeoutError" if failed else "\\N",
                "f" if failed else "t",
                "seeded",
                started.isoformat(),
            )
        ) + "\n"


def seed(url: str, runs: int, days: int, seed_value: int) -> tuple[str, list[date]]:
    import psycopg2

    rng = random.Random(seed_value)
    tenant = str(uuid.UUID(int=rng.getrandbits(128), version=4))
    agents = _agents(rng)
    now = datetime.now(UTC).replace(microsecond=0)
    start = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0)
    span = (now - start).total_seconds()
    # The tenant and agents through the ORM, so every column gets its model default.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from core.models.agent import Agent
    from core.models.tenant import Tenant

    engine = create_engine(_sync_dsn(url).replace("postgresql://", "postgresql+psycopg2://"))
    with Session(engine) as session:
        session.add(Tenant(id=uuid.UUID(tenant), name="Insights performance tenant", slug=f"insights-perf-{tenant}",
                           data_region="IN", settings={}))
        session.flush()
        session.add_all(
            Agent(id=uuid.UUID(a["id"]), tenant_id=uuid.UUID(tenant), name=a["name"], agent_type=a["agent_type"],
                  domain=a["domain"], status="active", system_prompt_ref="prompts/perf.txt",
                  hitl_condition="confidence < 0.7")
            for a in agents
        )
        session.commit()
    engine.dispose()
    with psycopg2.connect(_sync_dsn(url)) as conn, conn.cursor() as cur:
        began = time.monotonic()
        lines = _rows(rng, tenant, agents, runs, start, span)
        written = 0
        while written < runs:
            buffer = io.StringIO()
            for _ in range(min(BATCH, runs - written)):
                buffer.write(next(lines))
            buffer.seek(0)
            cur.copy_expert(f"COPY run_facts ({', '.join(COLUMNS)}) FROM STDIN", buffer)
            written += min(BATCH, runs - written)
            conn.commit()
            print(f"  {written:,} runs in {time.monotonic() - began:.0f} s", file=sys.stderr)
    first = start.date()
    closed = [first + timedelta(days=i) for i in range((now.date() - first).days)]
    return tenant, closed


async def roll(tenant: str, days: list[date]) -> None:
    from core.insights.rollup import rollup_day

    for day in days:
        await rollup_day(uuid.UUID(tenant), day)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=1_000_000)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--allow-remote", action="store_true", help="seed a database that is not on this machine")
    args = parser.parse_args(argv)
    url = os.environ.get("AGENTICORG_DB_URL", "")
    if not url:
        raise SystemExit("set AGENTICORG_DB_URL to the database to seed")
    _refuse_remote(url, args.allow_remote)
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    tenant, closed = seed(url, args.runs, args.days, args.seed)
    asyncio.run(roll(tenant, closed))
    print(tenant)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
