# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Time the Insights landing views and explorers against a seeded tenant (``scripts/seed_perf_tenant.py``).

Each landing view and each explorer is requested ``--repeat`` times through the real API handlers (the same
queries, statement timeout and tenant session as production, without HTTP), as an administrator, for the last
30 days. The script prints p50 and p95 per view and explorer and checks them against the PRD's budgets: landing
views p95 under 1.5 s, explorers p95 under 3 s. ``--json`` writes the figures for the acceptance record.

    AGENTICORG_DB_URL=... python scripts/insights_perf_bench.py --tenant <id> [--repeat 20] [--json out.json]

Exits 1 when a budget is missed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEW_BUDGET_S = 1.5
EXPLORER_BUDGET_S = 3.0


def _admin(tenant: str) -> SimpleNamespace:
    claims = {
        "sub": "perf-bench@example.test",
        "role": "admin",
        "agenticorg:user_id": str(uuid.uuid4()),
        "agenticorg:domains": None,
        "grantex:scopes": ["agenticorg:admin"],
    }
    return SimpleNamespace(
        state=SimpleNamespace(claims=claims, scopes=["agenticorg:admin"], auth_mode=None), query_params={}
    )


def p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


async def _time(call: Any, repeat: int) -> list[float]:
    samples = []
    for _ in range(repeat):
        began = time.perf_counter()
        await call()
        samples.append(time.perf_counter() - began)
    return samples


async def bench(tenant: str, repeat: int) -> dict[str, Any]:
    from api.v1 import insights as api
    from api.v1 import insights_explore as explore
    from core.insights import explore_registry, registry

    async def enabled(_tid: Any) -> bool:
        return True

    api.insights_enabled_for = enabled  # the benchmark tenant need not have the flag row
    request = _admin(tenant)
    out: dict[str, Any] = {"tenant": tenant, "repeat": repeat, "views": {}, "explorers": {}}
    for view in registry.allowed_views("admin", is_admin=True):
        samples = await _time(lambda v=view: api.insights_view(v, request, tenant_id=tenant), repeat)
        out["views"][view] = {"p50": statistics.median(samples), "p95": p95(samples)}
    for key in explore_registry.EXPLORERS:
        samples = await _time(lambda k=key: explore.insights_explore(k, request, tenant_id=tenant), repeat)
        out["explorers"][key] = {"p50": statistics.median(samples), "p95": p95(samples)}
    return out


def report(result: dict[str, Any]) -> bool:
    ok = True
    for group, budget in (("views", VIEW_BUDGET_S), ("explorers", EXPLORER_BUDGET_S)):
        print(f"{group} (p95 budget {budget:.1f} s)")
        for key, figures in result[group].items():
            within = figures["p95"] < budget
            ok = ok and within
            print(f"  {key:<14} p50 {figures['p50'] * 1000:8.1f} ms   p95 {figures['p95'] * 1000:8.1f} ms   "
                  f"{'ok' if within else 'OVER BUDGET'}")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--repeat", type=int, default=20)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    result = asyncio.run(bench(str(uuid.UUID(args.tenant)), args.repeat))
    ok = report(result)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if ok else 1



if __name__ == "__main__":
    raise SystemExit(main())
