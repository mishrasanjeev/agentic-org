# SPDX-License-Identifier: Apache-2.0
"""Seed a synthetic local seller and exercise it from a separate A2A buyer process."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from core.commerce.a2a_buyer_access import mint_buyer_token
from core.commerce.oacp_artifacts import DurableOacpArtifactCacheRepository, OacpPersistentArtifactCacheRecord
from core.database import get_tenant_session
from core.models.commerce_a2a_buyer_access import CommerceA2ABuyerAccess
from core.models.commerce_c6z_runtime import C6ZConnectorEvidenceRow, C6ZSellerOnboardingPacketRow
from core.models.oacp_artifact_cache import OacpArtifactCacheRecordRow


@dataclass(frozen=True)
class DemoScope:
    tenant_id: uuid.UUID
    merchant_id: str
    seller_agent_id: str
    packet_id: str
    evidence_id: str
    cache_record_id: str
    access_id: uuid.UUID
    token: str


def _require_local_demo(base_url: str) -> None:
    db_url = os.getenv("AGENTICORG_DB_URL", "")
    db_host = urlparse(db_url).hostname
    api = urlparse(base_url)
    if (
        os.getenv("AGENTICORG_ENV") not in {"development", "test"}
        or os.getenv("K_SERVICE")
        or db_host not in {"127.0.0.1", "localhost", "postgres"}
        or api.scheme != "http"
        or api.hostname not in {"127.0.0.1", "localhost"}
    ):
        raise RuntimeError("synthetic demo requires a local development database and loopback HTTP API")


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


async def _seed() -> DemoScope:
    now = datetime.now(UTC)
    suffix = uuid.uuid4().hex[:12]
    tenant_id = uuid.uuid4()
    merchant_id = f"synthetic_merchant_{suffix}"
    seller_agent_id = f"synthetic_seller_{suffix}"
    packet_id = f"synthetic_packet_{suffix}"
    evidence_id = f"synthetic_evidence_{suffix}"
    cache_record_id = f"synthetic_cache_{suffix}"
    source_ref = f"agenticorg:synthetic:evidence:{suffix}:redacted"
    token, token_hash = mint_buyer_token(str(tenant_id))
    access_id = uuid.uuid4()
    products = [
        {"product_ref": "demo:canvas-tote", "title": "Canvas Tote", "vendor": "Synthetic Local Store",
         "variants": [{"variant_id": "demo:canvas-tote:natural", "sku": "DEMO-TOTE-1",
                       "price": "1299.00", "currency": "INR", "inventory_quantity_snapshot": 7}]},
        {"product_ref": "demo:ceramic-mug", "title": "Ceramic Mug", "vendor": "Synthetic Local Store",
         "variants": [{"variant_id": "demo:ceramic-mug:white", "sku": "DEMO-MUG-1",
                       "price": "499.00", "currency": "INR", "inventory_quantity_snapshot": 12}]},
        {"product_ref": "demo:pocket-notebook", "title": "Pocket Notebook", "vendor": "Synthetic Local Store",
         "variants": [{"variant_id": "demo:pocket-notebook:plain", "sku": "DEMO-NOTE-1",
                       "price": "249.00", "currency": "INR", "inventory_quantity_snapshot": 20}]},
    ]
    async with get_tenant_session(tenant_id) as session:
        session.add(C6ZSellerOnboardingPacketRow(
            packet_id=packet_id, tenant_id=str(tenant_id), merchant_id=merchant_id,
            seller_agent_id=seller_agent_id, merchant_display_name="Synthetic Local Store",
            commerce_categories=["local-demo"], status="received",
        ))
        session.add(C6ZConnectorEvidenceRow(
            evidence_id=evidence_id, packet_id=packet_id, tenant_id=str(tenant_id),
            merchant_id=merchant_id, seller_agent_id=seller_agent_id,
            source_system="synthetic_demo", source_mode="local_fixture",
            source_evidence_ref=source_ref, source_observed_at=now, synced_at=now,
            products=products, product_count=len(products), variant_count=len(products),
        ))
        session.add(CommerceA2ABuyerAccess(
            id=access_id, tenant_id=str(tenant_id), merchant_id=merchant_id,
            seller_agent_id=seller_agent_id, buyer_agent_id=f"independent_python_buyer_{suffix}",
            token_hash=token_hash, status="active", expires_at=now + timedelta(hours=1),
        ))
        cache = OacpPersistentArtifactCacheRecord(
            cache_record_id=cache_record_id, artifact_id=f"synthetic_catalog_{suffix}",
            artifact_type="catalog_snapshot", authority="synthetic.local.demo",
            issuer="synthetic.local.demo", scope_kind="seller_agent",
            tenant_id=str(tenant_id), merchant_id=merchant_id, seller_agent_id=seller_agent_id,
            buyer_agent_id=None, source_refs=(source_ref,), evidence_refs=(source_ref,),
            generated_at=_iso(now), cached_at=_iso(now), expires_at=_iso(now + timedelta(minutes=5)),
            freshness_status="fresh", revocation_snapshot_status="fresh",
            revocation_snapshot_observed_at=_iso(now), revocation_snapshot_age_seconds=0,
            ttl_policy_seconds=300, risk_tier="low",
            blocked_capabilities=("checkout", "payment", "order", "mandate"),
            unsupported_capabilities=("execution", "public_discovery"),
            verifier_result_ref=f"synthetic:local:catalog:{suffix}:not_external",
        )
        stored = await DurableOacpArtifactCacheRepository(session).upsert(cache)
        if stored.get("stored") is not True:
            raise RuntimeError(f"synthetic catalog cache was refused: {stored.get('refusal_code')}")
    return DemoScope(
        tenant_id, merchant_id, seller_agent_id, packet_id, evidence_id,
        cache_record_id, access_id, token,
    )


async def _revoke(scope: DemoScope) -> None:
    async with get_tenant_session(scope.tenant_id) as session:
        row = await session.get(CommerceA2ABuyerAccess, scope.access_id)
        if row is not None:
            row.status = "revoked"
            row.revoked_at = datetime.now(UTC)


async def _remove(scope: DemoScope) -> None:
    async with get_tenant_session(scope.tenant_id) as session:
        for model, key in (
            (CommerceA2ABuyerAccess, scope.access_id),
            (OacpArtifactCacheRecordRow, scope.cache_record_id),
            (C6ZConnectorEvidenceRow, scope.evidence_id),
            (C6ZSellerOnboardingPacketRow, scope.packet_id),
        ):
            row = await session.get(model, key)
            if row is not None:
                await session.delete(row)


def _buyer_process(base_url: str, token: str, *, expect_denied: bool = False) -> None:
    buyer_script = Path(__file__).with_name("buyer_agent.py")
    env = {**os.environ, "A2A_BUYER_TOKEN": token}
    args = [sys.executable, str(buyer_script), "--base-url", base_url]
    if expect_denied:
        args.append("--expect-denied")
    completed = subprocess.run(  # noqa: S603
        args, env=env, capture_output=True, text=True, timeout=90, check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"independent buyer failed: {completed.stderr.strip()}")
    print(completed.stdout, end="")


async def _run(base_url: str) -> None:
    scope = await _seed()
    print("Synthetic seller: Synthetic Local Store (3 local fixture products)")
    print("Independent buyer: standalone Python A2A v1 HTTP+JSON client")
    try:
        _buyer_process(base_url, scope.token)
        await _revoke(scope)
        _buyer_process(base_url, scope.token, expect_denied=True)
    finally:
        await _remove(scope)
        print("Local synthetic seller, catalogue, artifact, and buyer access removed.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a local-only, non-paying external-buyer A2A commerce demo")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--confirm-local-synthetic", action="store_true")
    args = parser.parse_args()
    if not args.confirm_local_synthetic:
        parser.error("pass --confirm-local-synthetic to create temporary local fixture rows")
    try:
        _require_local_demo(args.base_url)
        asyncio.run(_run(args.base_url))
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"A2A commerce demo failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
