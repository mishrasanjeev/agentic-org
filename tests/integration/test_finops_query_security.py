# SPDX-License-Identifier: Apache-2.0
"""Execute every reporting dimension against PostgreSQL with two isolated tenants."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import MetaData, insert, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from core.finops import attribution, forecast
from core.models.finops_ledger import FinopsCostLedger

DB_URL = os.getenv("AGENTICORG_DB_URL", "")
pytestmark = pytest.mark.skipif(not DB_URL, reason="Requires local PostgreSQL")


@pytest.mark.asyncio
async def test_all_dimensions_bind_the_tenant_and_refuse_sql_fragments() -> None:
    engine = create_async_engine(DB_URL, poolclass=NullPool)
    schema = "finops_security_" + uuid.uuid4().hex
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    today = datetime.now(UTC).date()
    table = FinopsCostLedger.__table__.to_metadata(MetaData(), schema=schema)
    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
            await connection.run_sync(table.create)
            await connection.execute(text("SELECT set_config('search_path', :path, true)"), {"path": schema})
            await connection.execute(
                insert(table),
                [
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": tenant,
                        "period_date": today,
                        "agent_id": uuid.uuid4(),
                        "use_case": label,
                        "application": "chat",
                        "business_unit": "retail",
                        "department_id": uuid.uuid4(),
                        "cost_center_id": uuid.uuid4(),
                        "tokens": tokens,
                        "cost_usd": cost,
                        "calls": 1,
                    }
                    for tenant, label, tokens, cost in (
                        (tenant_a, "tenant-a-only", 100, 0.5),
                        (tenant_b, "tenant-b-private", 900, 9.0),
                    )
                ],
            )
            async with AsyncSession(bind=connection) as session:
                for dimension in attribution.DIMENSIONS:
                    result = await attribution.summary(session, tenant_a, group_by=dimension)
                    assert result["totals"]["tokens"] == 100
                    assert result["totals"]["cost_usd"] == 0.5
                    assert len(result["rows"]) == 1
                    series = await forecast.history(session, tenant_a, group_by=dimension)
                    assert list(series.values()) == [[(today, 100, 0.5)]]
                for fragment in ("use_case; DROP TABLE finops_cost_ledger", "tenant_id", "use_case DESC", '"use_case"'):
                    with pytest.raises(ValueError):
                        await attribution.summary(session, tenant_a, group_by=fragment)
                    with pytest.raises(ValueError):
                        await forecast.history(session, tenant_a, group_by=fragment)
                assert (await attribution.summary(session, tenant_b))["rows"][0]["use_case"] == "tenant-b-private"
            await connection.execute(DropSchema(schema, cascade=True))
    finally:
        await engine.dispose()
