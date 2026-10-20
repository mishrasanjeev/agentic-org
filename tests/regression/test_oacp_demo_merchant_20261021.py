# SPDX-License-Identifier: Apache-2.0
"""Regression: synthetic demo artifacts never back a real merchant, and real merchants are unchanged.

Failure guarded against: with a ``synthetic_demo`` connector source in the
product, a signed artifact that names it (``source_system`` or
``source_lineage.source_system``) could be cached for any tenant, so a real
merchant's catalog, feed or checkout could rest on invented data. Now
``POST /commerce/runtime/artifacts/cache`` (and the refresh route, which shares
the code) refuses such an issuance with ``synthetic_demo_artifact_refused``
unless ``AGENTICORG_OACP_DEMO_MERCHANT_ENABLED`` is on and the tenant carries
the demo marker; the refusal is on the ledger and nothing is stored. A Shopify
issuance never even looks the marker up.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException

from api.v1 import commerce_runtime as commerce_runtime_api
from core.commerce.c6z_runtime_vertical import DEMO_SOURCE_SYSTEM
from core.commerce.oacp_grantex_trust import (
    OacpGrantexArtifactVerifier,
    OacpGrantexTrustConfig,
    reset_oacp_trust_caches,
)
from core.config import settings
from tests.commerce_ledger_fakes import patch_commerce_ledger
from tests.oacp_signing import (
    ISSUER,
    JWKS_URI,
    STATUS_LIST_URI,
    EcSigningKey,
    FakeGrantex,
    RsaSigningKey,
    build_envelope,
    status_list_token,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, demo_flag: bool, demo_tenant: bool) -> None:
        reset_oacp_trust_caches()
        monkeypatch.setattr(settings, "oacp_demo_merchant_enabled", demo_flag)
        self.events = patch_commerce_ledger(monkeypatch, commerce_runtime_api)
        self.key = EcSigningKey(kid="oacp-test-2026-10-demo")
        status_key = RsaSigningKey(kid="platform-test-1")
        self.now = datetime.now(UTC)
        self.grantex = FakeGrantex(jwks={"keys": [self.key.public_jwk(), status_key.public_jwk()]})
        epoch = int(self.now.timestamp())
        self.grantex.status_lists[STATUS_LIST_URI] = status_list_token(
            status_key, uri=STATUS_LIST_URI, statuses={}, iat=epoch - 60, exp=epoch + 3600
        )
        self.stored: list[Any] = []
        self.marker_lookups = 0
        harness = self

        @asynccontextmanager
        async def fake_session(_tenant_id):
            yield object()

        class FakeRepository:
            def __init__(self, _session: object) -> None:
                pass

            async def upsert(self, record) -> dict:
                harness.stored.append(record)
                return {"stored": True, "status": "stored", "cache_record_id": record.cache_record_id}

        async def is_demo_tenant(tenant_id: str) -> bool:
            harness.marker_lookups += 1
            return demo_tenant

        config = OacpGrantexTrustConfig(issuer=ISSUER, jwks_uri=JWKS_URI, status_list_origins=frozenset({ISSUER}))
        monkeypatch.setattr(commerce_runtime_api, "get_tenant_session", fake_session)
        monkeypatch.setattr(commerce_runtime_api, "DurableOacpArtifactCacheRepository", FakeRepository)
        monkeypatch.setattr(commerce_runtime_api, "_is_demo_tenant", is_demo_tenant)
        monkeypatch.setattr(
            commerce_runtime_api,
            "_oacp_artifact_verifier",
            lambda: OacpGrantexArtifactVerifier(config, transport_factory=harness.grantex.transport_factory()),
        )
        monkeypatch.setattr(commerce_runtime_api, "oacp_verify_strict_enabled", lambda: True)

    def artifact(self, *, source_system: str, lineage_source: str | None = None) -> dict[str, Any]:
        payload = {
            "artifact_family": "catalog_snapshot",
            "tenant_id": TENANT_ID,
            "merchant_id": "merchant_1",
            "seller_agent_id": "seller_agent_1",
            "source_system": source_system,
            "source_lineage": {"source_system": lineage_source or source_system},
            "source_evidence_ref": "agenticorg:evidence:abc",
            "allowed_to_execute": False,
            "no_payment_execution": True,
            "no_public_discovery_enablement": True,
            "non_authoritative_for_transaction": True,
        }
        envelope = build_envelope(
            payload,
            key=self.key,
            issued_at=_iso(self.now - timedelta(minutes=1)),
            expires_at=_iso(self.now + timedelta(minutes=30)),
        )
        return {"artifact_family": "catalog_snapshot", "envelope": envelope, "payload": payload}

    async def cache(self, artifact: dict[str, Any]) -> dict[str, Any]:
        return await commerce_runtime_api.cache_grantex_artifacts(
            commerce_runtime_api.CacheArtifactsRequest(artifacts=[artifact]), tenant_id=TENANT_ID
        )

    async def refused(self, artifact: dict[str, Any]) -> dict[str, Any]:
        with pytest.raises(HTTPException) as exc_info:
            await self.cache(artifact)
        assert exc_info.value.status_code == 422
        assert self.stored == []
        assert [(e["event"], e["outcome"]) for e in self.events] == [("commerce.runtime.artifacts.cache", "refused")]
        return exc_info.value.detail


async def test_real_merchant_cannot_cache_synthetic_artifacts(monkeypatch) -> None:
    harness = Harness(monkeypatch, demo_flag=True, demo_tenant=False)
    detail = await harness.refused(harness.artifact(source_system=DEMO_SOURCE_SYSTEM))
    assert detail["refusal_code"] == "synthetic_demo_artifact_refused"


async def test_synthetic_lineage_alone_is_enough_to_refuse(monkeypatch) -> None:
    harness = Harness(monkeypatch, demo_flag=True, demo_tenant=False)
    detail = await harness.refused(harness.artifact(source_system="shopify", lineage_source=DEMO_SOURCE_SYSTEM))
    assert detail["refusal_code"] == "synthetic_demo_artifact_refused"


async def test_demo_tenant_is_refused_too_while_the_flag_is_off(monkeypatch) -> None:
    harness = Harness(monkeypatch, demo_flag=False, demo_tenant=True)
    detail = await harness.refused(harness.artifact(source_system=DEMO_SOURCE_SYSTEM))
    assert detail["refusal_code"] == "synthetic_demo_artifact_refused"
    assert harness.marker_lookups == 0  # the flag decides first


async def test_demo_tenant_caches_verified_synthetic_artifacts_with_the_flag_on(monkeypatch) -> None:
    harness = Harness(monkeypatch, demo_flag=True, demo_tenant=True)
    result = await harness.cache(harness.artifact(source_system=DEMO_SOURCE_SYSTEM))
    assert result["status"] == "cached" and result["verification"]["verified"] == 1
    assert harness.stored[0].verifier_result_ref.startswith("oacp-artifact-jws-v1;")


async def test_shopify_issuance_never_looks_up_the_demo_marker(monkeypatch) -> None:
    harness = Harness(monkeypatch, demo_flag=True, demo_tenant=False)
    result = await harness.cache(harness.artifact(source_system="shopify"))
    assert result["status"] == "cached"
    assert harness.marker_lookups == 0
