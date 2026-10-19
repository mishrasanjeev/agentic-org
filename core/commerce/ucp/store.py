# SPDX-License-Identifier: Apache-2.0
"""Database reads and writes behind the UCP surfaces.

Host resolution runs before any tenant is known. The verified serving host is
indexed in the tenant catalogue (``tenants.settings["ucp_serving_hosts"]``,
which carries no row-level security) when the operator verifies it; the
public routes read that index through ``get_platform_session`` and then load the
merchant config inside the owning tenant's session, where the attestation is
checked again. The index is only a pointer: a host is served only when the
merchant config's own attestation names it. A host claimed by two tenants is
refused (fail closed) and logged.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select

from core.commerce.oacp_artifacts import DurableOacpArtifactCacheRepository, OacpArtifactCacheRepositoryQuery
from core.commerce.oacp_merchant_config import merchant_config_id
from core.database import get_platform_session, get_tenant_session
from core.models.commerce_c6z_runtime import C6ZConnectorEvidenceRow, C6ZMerchantCommerceConfigRow
from core.models.tenant import Tenant

logger = structlog.get_logger()

HOST_INDEX_KEY = "ucp_serving_hosts"
HOST_INDEX_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class UcpServingBinding:
    host: str
    tenant_id: str
    merchant_id: str
    seller_agent_id: str


# enterprise-gate: process-local-ok reason=host index cache expires on its ttl; the database is the source of truth
_HOST_CACHE: dict[str, tuple[float, UcpServingBinding | None]] = {}


def reset_ucp_host_cache() -> None:
    _HOST_CACHE.clear()


async def find_serving_binding(host: str) -> UcpServingBinding | None:
    """The (tenant, merchant, seller) indexed for ``host``, or None (unknown or ambiguous)."""
    now = time.monotonic()
    cached = _HOST_CACHE.get(host)
    if cached is not None and cached[0] > now:
        return cached[1]
    async with get_platform_session() as session:
        rows = (
            await session.execute(
                select(Tenant.id, Tenant.settings).where(
                    Tenant.deleted_at.is_(None),
                    Tenant.settings[HOST_INDEX_KEY].has_key(host),
                )
            )
        ).all()
    binding: UcpServingBinding | None = None
    if len(rows) > 1:
        logger.error("ucp_serving_host_claimed_by_multiple_tenants", tenant_count=len(rows))
    elif rows:
        tenant_id, settings_value = rows[0]
        entry = ((settings_value or {}).get(HOST_INDEX_KEY) or {}).get(host)
        if isinstance(entry, Mapping) and isinstance(entry.get("merchant_id"), str):
            binding = UcpServingBinding(
                host=host,
                tenant_id=str(tenant_id),
                merchant_id=str(entry["merchant_id"]),
                seller_agent_id=str(entry.get("seller_agent_id") or ""),
            )
    _HOST_CACHE[host] = (now + HOST_INDEX_TTL_SECONDS, binding)
    return binding


async def host_claimed_by_other_tenant(host: str, tenant_id: str) -> bool:
    async with get_platform_session() as session:
        rows = (
            await session.execute(
                select(Tenant.id).where(
                    Tenant.deleted_at.is_(None),
                    Tenant.settings[HOST_INDEX_KEY].has_key(host),
                )
            )
        ).all()
    return any(str(row[0]) != tenant_id for row in rows)


async def write_host_index(
    session: Any,
    *,
    tenant_id: str,
    host: str,
    merchant_id: str | None,
    seller_agent_id: str | None,
    previous_host: str | None = None,
) -> None:
    """Point ``host`` at the merchant in this tenant's catalogue row (``merchant_id=None`` removes it)."""
    tenant = (await session.execute(select(Tenant).where(Tenant.id == uuid.UUID(tenant_id)))).scalar_one_or_none()
    if tenant is None:
        raise LookupError("tenant not found")
    settings_value = dict(tenant.settings or {})
    index = dict(settings_value.get(HOST_INDEX_KEY) or {})
    if previous_host and previous_host != host:
        index.pop(previous_host, None)
    if merchant_id is None:
        index.pop(host, None)
    else:
        index[host] = {"merchant_id": merchant_id, "seller_agent_id": seller_agent_id}
    settings_value[HOST_INDEX_KEY] = index
    tenant.settings = settings_value
    _HOST_CACHE.pop(host, None)
    if previous_host:
        _HOST_CACHE.pop(previous_host, None)


async def load_merchant_config_row(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
) -> C6ZMerchantCommerceConfigRow | None:
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        return await session.get(
            C6ZMerchantCommerceConfigRow,
            merchant_config_id(tenant_id, merchant_id, seller_agent_id),
        )


async def _shared_records(session: Any, tenant_id: str, merchant_id: str, seller_agent_id: str) -> list[Any]:
    repo = DurableOacpArtifactCacheRepository(session)
    records = await repo.list_for_scope(
        OacpArtifactCacheRepositoryQuery(
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=None,
            shared_only=True,
        )
    )
    return list(records)


async def load_shared_cache_records(*, tenant_id: str, merchant_id: str, seller_agent_id: str) -> list[Any]:
    """The scope's buyer-agnostic OACP cache records (the public-discovery gate reads these)."""
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        return await _shared_records(session, tenant_id, merchant_id, seller_agent_id)


async def load_catalog_inputs(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Shared (buyer-agnostic) cache records and synced products for the merchant scope.

    Each product carries ``_evidence_currency`` from its connector evidence row
    as the currency fallback for variants without one.
    """
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        records = await _shared_records(session, tenant_id, merchant_id, seller_agent_id)
        evidence_rows = (
            await session.scalars(
                select(C6ZConnectorEvidenceRow)
                .where(
                    C6ZConnectorEvidenceRow.tenant_id == tenant_id,
                    C6ZConnectorEvidenceRow.merchant_id == merchant_id,
                    C6ZConnectorEvidenceRow.seller_agent_id == seller_agent_id,
                )
                .order_by(C6ZConnectorEvidenceRow.synced_at.desc())
            )
        ).all()
    products: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in evidence_rows:
        for product in row.products or []:
            if not isinstance(product, Mapping):
                continue
            ref = str(product.get("product_ref") or "")
            if not ref or ref in seen:
                continue
            seen.add(ref)
            products.append({**dict(product), "_evidence_currency": row.currency})
    return list(records), products
