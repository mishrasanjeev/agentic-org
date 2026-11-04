# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The Insights performance seeder and benchmark against real Postgres, at a small scale.

The seeder writes a tenant, its agents and a few hundred run facts with COPY, the
closed days are rolled up with the real rollup, and the benchmark answers every
landing view and explorer once for that tenant within the budgets.
"""

from __future__ import annotations

import os
import random

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from scripts import insights_perf_bench as bench
from scripts import seed_perf_tenant as seeder

pytestmark = [
    pytest.mark.skipif(not os.getenv("AGENTICORG_DB_URL"), reason="Requires the disposable Docker Postgres database"),
    pytest.mark.timeout(600),
]


@pytest.mark.asyncio
async def test_seed_roll_and_bench_a_small_tenant(monkeypatch) -> None:
    from api.v1 import insights as api

    url = os.environ["AGENTICORG_DB_URL"]
    # The benchmark replaces the flag check for its tenant; restore it after the test.
    monkeypatch.setattr(api, "insights_enabled_for", api.insights_enabled_for)
    tenant, closed = seeder.seed(url, runs=300, days=3, seed_value=random.randint(1, 10**9))
    assert len(closed) == 3
    await seeder.roll(tenant, closed)
    admin = create_async_engine(url, poolclass=NullPool)
    try:
        async with admin.connect() as conn:
            facts = (await conn.execute(text("SELECT count(*) FROM run_facts WHERE tenant_id = :t"),
                                        {"t": tenant})).scalar_one()
            rolled = (await conn.execute(text("SELECT count(*) FROM insights_rollup_state WHERE tenant_id = :t"),
                                         {"t": tenant})).scalar_one()
        assert facts == 300 and rolled == 3
        result = await bench.bench(tenant, 1)
        assert result["views"] and result["explorers"]
        assert bench.report(result) is True
    finally:
        # The tenant stays, as other Postgres tests' do: the disposable test database is reset per CI run, and
        # reading its views seeds rows in more tables (value baselines) than this test should know about.
        await admin.dispose()
