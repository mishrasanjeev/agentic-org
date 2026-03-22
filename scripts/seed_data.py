#!/usr/bin/env python3
"""Seed default tenant, admin user, schemas, and agents into AgenticOrg.

Idempotent — safe to run multiple times.  Reads AGENTICORG_DB_URL from the
environment or from a .env file in the project root.

Usage:
    python -m scripts.seed_data
    # or
    python scripts/seed_data.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import asyncpg

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = PROJECT_ROOT / "schemas"

DEFAULT_TENANT = {
    "name": "Default Org",
    "slug": "default",
    "plan": "enterprise",
    "data_region": "IN",
}

DEFAULT_ADMIN = {
    "email": "admin@agenticorg.local",
    "role": "admin",
    "domain": "all",
    "name": "System Admin",
}

# All 25 system agents with their configuration
SYSTEM_AGENTS: list[dict[str, Any]] = [
    # Finance
    {"agent_type": "ap_processor", "domain": "finance", "confidence_floor": 0.880},
    {"agent_type": "ar_collections", "domain": "finance", "confidence_floor": 0.850},
    {"agent_type": "recon_agent", "domain": "finance", "confidence_floor": 0.950},
    {"agent_type": "tax_compliance", "domain": "finance", "confidence_floor": 0.920},
    {"agent_type": "close_agent", "domain": "finance", "confidence_floor": 0.800},
    {"agent_type": "fpa_agent", "domain": "finance", "confidence_floor": 0.780},
    # HR
    {"agent_type": "talent_acquisition", "domain": "hr", "confidence_floor": 0.880},
    {"agent_type": "onboarding_agent", "domain": "hr", "confidence_floor": 0.950},
    {"agent_type": "payroll_engine", "domain": "hr", "confidence_floor": 0.990},
    {"agent_type": "performance_coach", "domain": "hr", "confidence_floor": 0.800},
    {"agent_type": "ld_coordinator", "domain": "hr", "confidence_floor": 0.820},
    {"agent_type": "offboarding_agent", "domain": "hr", "confidence_floor": 0.950},
    # Marketing
    {"agent_type": "content_factory", "domain": "marketing", "confidence_floor": 0.880},
    {"agent_type": "campaign_pilot", "domain": "marketing", "confidence_floor": 0.850},
    {"agent_type": "seo_strategist", "domain": "marketing", "confidence_floor": 0.900},
    {"agent_type": "crm_intelligence", "domain": "marketing", "confidence_floor": 0.880},
    {"agent_type": "brand_monitor", "domain": "marketing", "confidence_floor": 0.850},
    # Ops
    {"agent_type": "vendor_manager", "domain": "ops", "confidence_floor": 0.880},
    {"agent_type": "contract_intelligence", "domain": "ops", "confidence_floor": 0.820},
    {"agent_type": "support_triage", "domain": "ops", "confidence_floor": 0.850},
    {"agent_type": "compliance_guard", "domain": "ops", "confidence_floor": 0.950},
    {"agent_type": "it_operations", "domain": "ops", "confidence_floor": 0.880},
    # Backoffice
    {"agent_type": "legal_ops", "domain": "backoffice", "confidence_floor": 0.900},
    {"agent_type": "risk_sentinel", "domain": "backoffice", "confidence_floor": 0.950},
    {"agent_type": "facilities_agent", "domain": "backoffice", "confidence_floor": 0.800},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_db_url() -> str:
    """Return an asyncpg-compatible DSN from env or .env file."""
    url = os.environ.get("AGENTICORG_DB_URL", "")

    if not url:
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip() == "AGENTICORG_DB_URL":
                    url = val.strip().strip("\"'")
                    break

    if not url:
        print("ERROR: AGENTICORG_DB_URL is not set.  Export it or add to .env")
        sys.exit(1)

    # asyncpg needs postgresql:// (not postgresql+asyncpg://)
    url = re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)
    return url


def _humanise(agent_type: str) -> str:
    """Turn 'ap_processor' into 'AP Processor'."""
    return agent_type.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------


async def _ensure_tenant(conn: asyncpg.Connection) -> str:
    """Insert default tenant if missing.  Returns tenant id."""
    row = await conn.fetchrow("SELECT id FROM tenants WHERE slug = $1", DEFAULT_TENANT["slug"])
    if row:
        print(f"  Tenant '{DEFAULT_TENANT['slug']}' already exists: {row['id']}")
        return str(row["id"])

    row = await conn.fetchrow(
        """
        INSERT INTO tenants (name, slug, plan, data_region)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        DEFAULT_TENANT["name"],
        DEFAULT_TENANT["slug"],
        DEFAULT_TENANT["plan"],
        DEFAULT_TENANT["data_region"],
    )
    tid = str(row["id"])
    print(f"  Created tenant '{DEFAULT_TENANT['slug']}': {tid}")
    return tid


async def _ensure_admin(conn: asyncpg.Connection, tenant_id: str) -> str:
    """Insert default admin user if missing.  Returns user id."""
    import uuid as _uuid

    from passlib.hash import bcrypt as bcrypt_hash

    tid = _uuid.UUID(tenant_id)

    # Ensure password_hash column exists (idempotent DDL)
    await conn.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)"
    )

    row = await conn.fetchrow(
        "SELECT id FROM users WHERE tenant_id = $1 AND email = $2",
        tid,
        DEFAULT_ADMIN["email"],
    )
    if row:
        print(f"  Admin '{DEFAULT_ADMIN['email']}' already exists: {row['id']}")
        uid = str(row["id"])
    else:
        row = await conn.fetchrow(
            """
            INSERT INTO users (tenant_id, email, name, role, domain)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            tid,
            DEFAULT_ADMIN["email"],
            DEFAULT_ADMIN["name"],
            DEFAULT_ADMIN["role"],
            DEFAULT_ADMIN["domain"],
        )
        uid = str(row["id"])
        print(f"  Created admin '{DEFAULT_ADMIN['email']}': {uid}")

    # Always set/update the password hash for the admin user
    hashed = bcrypt_hash.hash("admin123!")
    await conn.execute(
        "UPDATE users SET password_hash = $1 WHERE id = $2",
        hashed,
        _uuid.UUID(uid),
    )
    print(f"  Set password_hash for admin '{DEFAULT_ADMIN['email']}'")
    return uid


async def _seed_schemas(conn: asyncpg.Connection, tenant_id: str) -> None:
    """Load all *.schema.json files into schema_registry with is_default=True."""
    import uuid as _uuid

    tid = _uuid.UUID(tenant_id)
    schema_files = sorted(SCHEMAS_DIR.glob("*.schema.json"))
    if not schema_files:
        print("  WARNING: No schema files found in schemas/")
        return

    for path in schema_files:
        name = path.stem.replace(".schema", "")  # e.g. "invoice"
        exists = await conn.fetchval(
            """
            SELECT 1 FROM schema_registry
            WHERE tenant_id = $1 AND name = $2 AND version = '1'
            """,
            tid,
            name,
        )
        if exists:
            print(f"  Schema '{name}' already exists — skipped")
            continue

        schema_json = json.loads(path.read_text(encoding="utf-8"))
        description = schema_json.get("title", name)

        await conn.execute(
            """
            INSERT INTO schema_registry
                (tenant_id, name, version, description, json_schema, is_default)
            VALUES ($1, $2, '1', $3, $4::jsonb, TRUE)
            """,
            tid,
            name,
            description,
            json.dumps(schema_json),
        )
        print(f"  Inserted schema '{name}'")

    print(f"  Processed {len(schema_files)} schema files")


async def _seed_agents(conn: asyncpg.Connection, tenant_id: str) -> None:
    """Insert all system agents with status='active'."""
    import uuid as _uuid

    tid = _uuid.UUID(tenant_id)

    for agent in SYSTEM_AGENTS:
        agent_type = agent["agent_type"]
        exists = await conn.fetchval(
            """
            SELECT 1 FROM agents
            WHERE tenant_id = $1 AND agent_type = $2 AND version = '1.0.0'
            """,
            tid,
            agent_type,
        )
        if exists:
            print(f"  Agent '{agent_type}' already exists — skipped")
            continue

        name = _humanise(agent_type)
        await conn.execute(
            """
            INSERT INTO agents (
                tenant_id, name, agent_type, domain, description,
                system_prompt_ref, confidence_floor, hitl_condition,
                status
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8,
                'active'
            )
            """,
            tid,
            name,
            agent_type,
            agent["domain"],
            f"System {agent['domain']} agent: {name}",
            f"prompts/{agent_type}.prompt.txt",
            agent["confidence_floor"],
            f"confidence < {agent['confidence_floor']}",
        )
        print(
            f"  Inserted agent '{agent_type}' ({agent['domain']}, floor={agent['confidence_floor']})"
        )

    print(f"  Processed {len(SYSTEM_AGENTS)} agents")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    dsn = _load_db_url()
    print("Connecting to database ...")
    conn = await asyncpg.connect(dsn)
    try:
        print("\n[1/4] Ensuring default tenant")
        tenant_id = await _ensure_tenant(conn)

        print("\n[2/4] Ensuring admin user")
        await _ensure_admin(conn, tenant_id)

        print("\n[3/4] Seeding schema registry")
        await _seed_schemas(conn, tenant_id)

        print("\n[4/4] Seeding system agents")
        await _seed_agents(conn, tenant_id)

        print("\nSeed complete.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
