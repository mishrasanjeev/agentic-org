"""One-time backfill: migrate plaintext Connector.auth_config to encrypted ConnectorConfig.

Run via: python -m core.crypto.backfill_connector_secrets

For each Connector row with non-empty auth_config:
  1. Encrypt the value via encrypt_for_tenant()
  2. Write to ConnectorConfig.credentials_encrypted
  3. Clear Connector.auth_config to empty dict

Idempotent — skips connectors that already have a ConnectorConfig entry.
"""

from __future__ import annotations

import asyncio
import json
import logging

import structlog
from sqlalchemy import select, text, update

from core.database import async_session_factory, get_tenant_session
from core.models.connector import Connector
from core.models.connector_config import ConnectorConfig
from core.models.tenant import Tenant

logger = structlog.get_logger(__name__)


async def backfill() -> dict[str, int]:
    """Migrate all plaintext auth_config to encrypted ConnectorConfig."""
    migrated = 0
    skipped = 0
    errors = 0

    # ConnectorConfig is FORCE-RLS protected.  Connector is tenant-global,
    # so enumerate tenants first and do every credential read/write with an
    # explicit global (company_id NULL) tenant context.
    async with async_session_factory() as session:
        # Fail closed if the maintenance role cannot enumerate the tenant
        # catalog; row_security=off never grants a bypass.
        await session.execute(text("SET LOCAL row_security = off"))
        tenant_ids = list((await session.scalars(select(Tenant.id))).all())

    connectors: list[Connector] = []
    for tenant_id in tenant_ids:
        async with get_tenant_session(tenant_id) as session:
            connectors.extend(
                list(
                    (
                        await session.scalars(
                            select(Connector).where(Connector.tenant_id == tenant_id)
                        )
                    ).all()
                )
            )

    for conn in connectors:
        if not conn.auth_config or conn.auth_config == {}:
            skipped += 1
            continue

        # Check if ConnectorConfig already exists
        async with get_tenant_session(conn.tenant_id) as session:
            existing = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == conn.tenant_id,
                    ConnectorConfig.company_id.is_(None),
                    ConnectorConfig.connector_name == conn.name,
                )
            )
            if existing.scalar_one_or_none():
                skipped += 1
                continue

        # Encrypt and store
        try:
            from core.crypto.tenant_secrets import encrypt_for_tenant

            plaintext = json.dumps(conn.auth_config)
            encrypted = await encrypt_for_tenant(plaintext, conn.tenant_id)

            async with get_tenant_session(conn.tenant_id) as session:
                cc = ConnectorConfig(
                    tenant_id=conn.tenant_id,
                    company_id=None,
                    connector_name=conn.name,
                    config={},
                    credentials_encrypted={"_encrypted": encrypted},
                )
                session.add(cc)
                await session.flush()

                # Clear the plaintext field
                await session.execute(
                    update(Connector)
                    .where(Connector.id == conn.id)
                    .values(auth_config={})
                )

            migrated += 1
            logger.info(
                "backfill_migrated",
                connector_id=str(conn.id),
                connector_name=conn.name,
                tenant_id=str(conn.tenant_id),
            )
        # enterprise-gate: broad-except-ok reason=crypto-backfill-row-failure-records-error-count
        except Exception as e:
            errors += 1
            logger.error(
                "backfill_failed",
                connector_id=str(conn.id),
                error=str(e)[:200],
            )

    result = {"migrated": migrated, "skipped": skipped, "errors": errors}
    logger.info("backfill_complete", **result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(asyncio.run(backfill()))
