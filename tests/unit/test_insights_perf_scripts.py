# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The Insights performance seeder and benchmark: the local-only guard, the generated rows and the command lines."""

from __future__ import annotations

import json
import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from scripts import insights_perf_bench as bench
from scripts import seed_perf_tenant as seeder


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p@localhost:5432/db",
        "postgresql+asyncpg://u:p@127.0.0.1:5432/db",
        "postgresql+psycopg://u:p@[::1]:5432/db",
    ],
)
def test_local_databases_are_seeded(url):
    seeder._refuse_remote(url, allow_remote=False)
    assert seeder._sync_dsn(url).startswith("postgresql://")


def test_a_remote_database_needs_allow_remote():
    url = "postgresql+asyncpg://u:p@db.example.test:5432/db"
    with pytest.raises(SystemExit, match="refusing to seed"):
        seeder._refuse_remote(url, allow_remote=False)
    seeder._refuse_remote(url, allow_remote=True)


def test_the_generated_rows_are_seeded_run_facts_of_the_tenant():
    rng = random.Random(3)
    tenant = str(uuid.uuid4())
    agents = seeder._agents(rng)
    assert len(agents) == 40 and all(a["id"] and a["agent_type"] and a["domain"] for a in agents)
    start = datetime.now(UTC) - timedelta(days=2)
    lines = seeder._rows(rng, tenant, agents, 500, start, 2 * 86400)
    rows = [next(lines).rstrip("\n").split("\t") for _ in range(500)]
    assert all(len(r) == len(seeder.COLUMNS) for r in rows)
    by_name = [dict(zip(seeder.COLUMNS, r, strict=True)) for r in rows]
    assert {r["tenant_id"] for r in by_name} == {tenant}
    assert {r["data_quality"] for r in by_name} == {"seeded"}
    statuses = {r["status"] for r in by_name}
    assert "failed" in statuses and "completed" in statuses
    assert {r["agent_id"] for r in by_name} <= {a["id"] for a in agents}


def test_the_seeder_command_line(monkeypatch, capsys):
    seen: dict = {}

    def seed(url, runs, days, seed_value):
        seen.update(url=url, runs=runs, days=days, seed=seed_value)
        return "tenant-1", []

    async def roll(tenant, days):
        seen["rolled"] = (tenant, days)

    monkeypatch.setattr(seeder, "seed", seed)
    monkeypatch.setattr(seeder, "roll", roll)
    monkeypatch.delenv("AGENTICORG_DB_URL", raising=False)
    with pytest.raises(SystemExit, match="AGENTICORG_DB_URL"):
        seeder.main([])
    monkeypatch.setenv("AGENTICORG_DB_URL", "postgresql+asyncpg://u:p@db.example.test/db")
    with pytest.raises(SystemExit, match="refusing"):
        seeder.main(["--runs", "10"])
    assert seeder.main(["--runs", "10", "--days", "2", "--seed", "5", "--allow-remote"]) == 0
    assert seen == {"url": "postgresql+asyncpg://u:p@db.example.test/db", "runs": 10, "days": 2, "seed": 5,
                    "rolled": ("tenant-1", [])}
    assert capsys.readouterr().out.strip() == "tenant-1"


def test_p95_and_the_report():
    assert bench.p95([0.1]) == 0.1
    assert bench.p95([i / 100 for i in range(1, 101)]) == 0.95
    within = {"views": {"executive": {"p50": 0.2, "p95": 0.4}}, "explorers": {"runs": {"p50": 0.3, "p95": 1.0}}}
    assert bench.report(within) is True
    over = {"views": {"finance": {"p50": 1.0, "p95": 1.6}}, "explorers": {}}
    assert bench.report(over) is False


def test_the_benchmark_command_line(monkeypatch, tmp_path):
    tenant = str(uuid.uuid4())

    async def fake_bench(tid, repeat):
        return {"tenant": tid, "repeat": repeat, "views": {"executive": {"p50": 0.1, "p95": 0.2}},
                "explorers": {"runs": {"p50": 0.1, "p95": 0.2}}}

    monkeypatch.setattr(bench, "bench", fake_bench)
    out = tmp_path / "result.json"
    assert bench.main(["--tenant", tenant, "--repeat", "2", "--json", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["repeat"] == 2

    async def slow_bench(tid, repeat):
        return {"tenant": tid, "repeat": repeat, "views": {"finance": {"p50": 2.0, "p95": 2.5}}, "explorers": {}}

    monkeypatch.setattr(bench, "bench", slow_bench)
    assert bench.main(["--tenant", tenant, "--repeat", "1"]) == 1


def test_the_benchmark_caller_is_an_administrator():
    request = bench._admin(str(uuid.uuid4()))
    assert request.state.claims["role"] == "admin" and "agenticorg:admin" in request.state.scopes
