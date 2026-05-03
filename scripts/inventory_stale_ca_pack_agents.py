#!/usr/bin/env python3
"""Read-only inventory of CA-firm pack agents stuck on pre-PR-#434 state.

Reports tenants where the CA-firm pack is installed but per-tenant
agent rows still carry empty ``connector_ids``. These rows were
created by the pre-#434 installer code and need PR #434's repair
branch to fire (idempotent reinstall via ``POST /api/v1/packs/ca-firm/install``,
or a one-shot ``sync_company_pack_assets_for_session`` job).

Pure read — no UPDATE, no INSERT, no schema change. Safe to run any
time. Intended to be deployed as a Cloud Run one-shot job alongside
the existing ``agenticorg-bge-m3-backfill`` pattern.

Outputs JSON-lines so a downstream remediation job can consume it
without re-querying.

Usage::

    python scripts/inventory_stale_ca_pack_agents.py
    # → one JSON line per affected tenant on stdout, plus a summary
    #   line at the end.

Requires the same environment as the api service (DATABASE_URL +
the alembic-managed schema). When run as a Cloud Run job, attach the
same CloudSQL instance and secret refs the api uses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from sqlalchemy import text

# core.database exposes a module-level ``engine`` (an AsyncEngine
# instance) — there is no ``get_engine()`` factory. The first cut of
# this script imported ``get_engine`` and the Cloud Run job exited 1
# with ``ImportError`` on first run (2026-05-03 audit). Use the real
# attribute so this is repeatable from the repo without an inline
# ``python -c`` workaround.
from core.database import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("inventory_stale_ca_pack_agents")

CA_PACK_AGENT_TYPES = (
    "gst_filing_agent",
    "tds_compliance_agent",
    "bank_reconciliation_agent",
    "fp_a_analyst_agent",
    "ar_collections_agent",
)


async def _inventory() -> int:
    affected_tenants = 0
    total_stale_agents = 0

    async with engine.connect() as conn:
        # Find every tenant that has the CA-firm pack installed.
        installs = await conn.execute(
            text(
                """
                SELECT DISTINCT tenant_id
                FROM industry_pack_installs
                WHERE pack_name = 'ca-firm'
                """
            )
        )
        tenant_ids = [row[0] for row in installs.fetchall()]

        for tenant_id in tenant_ids:
            stale = await conn.execute(
                text(
                    """
                    SELECT id, agent_type, company_id
                    FROM agents
                    WHERE tenant_id = :tenant_id
                      AND agent_type = ANY(:agent_types)
                      AND (
                        connector_ids IS NULL
                        OR connector_ids = '[]'::jsonb
                        OR jsonb_array_length(connector_ids) = 0
                      )
                    ORDER BY company_id, agent_type
                    """
                ),
                {"tenant_id": tenant_id, "agent_types": list(CA_PACK_AGENT_TYPES)},
            )
            rows = stale.fetchall()
            if not rows:
                continue
            affected_tenants += 1
            total_stale_agents += len(rows)
            payload: dict[str, Any] = {
                "tenant_id": str(tenant_id),
                "stale_agent_count": len(rows),
                "agents": [
                    {
                        "agent_id": str(r[0]),
                        "agent_type": r[1],
                        "company_id": str(r[2]) if r[2] else None,
                    }
                    for r in rows
                ],
            }
            print(json.dumps(payload))

    summary = {
        "summary": True,
        "tenants_with_ca_pack_installed": len(tenant_ids),
        "tenants_with_stale_agents": affected_tenants,
        "total_stale_agents": total_stale_agents,
        "remediation": (
            "POST /api/v1/packs/ca-firm/install per affected tenant — "
            "the installer's else-branch (PR #434) repairs existing "
            "agents idempotently. Verify before/after via "
            "GET /api/v1/agents/{id} for connector_ids and "
            "config.tool_connectors keys."
        ),
    }
    print(json.dumps(summary))
    logger.info(
        "inventory_complete tenants_installed=%s tenants_stale=%s agents_stale=%s",
        len(tenant_ids),
        affected_tenants,
        total_stale_agents,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_inventory()))
