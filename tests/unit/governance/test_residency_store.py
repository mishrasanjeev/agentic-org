# SPDX-License-Identifier: Apache-2.0
"""Residency store paths: loading the region and attestations, recording and revoking attestations."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.governance import residency as res

TENANT = uuid.uuid4()


def _row(**over):
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "provider": "gemini",
        "data_region": "IN",
        "in_region": True,
        "no_training": True,
        "evidence_ref": "clause 7.2",
        "attested_by": "user:1",
        "attested_at": datetime.now(UTC),
        "expires_at": None,
        "revoked_at": None,
        "revoked_by": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


class _Session:
    def __init__(self, rows, config=None):
        self.rows = rows
        self.config = config
        self.added: list = []

    async def execute(self, _query):
        result = MagicMock()
        result.scalars.return_value = iter(self.rows)
        result.scalar_one_or_none.return_value = self.rows[0] if self.rows else None
        return result

    async def get(self, _model, _key):
        return self.config

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None and hasattr(obj, "provider"):
                obj.id = uuid.uuid4()


@pytest.fixture
def session(monkeypatch):
    sess = _Session([])

    @contextlib.asynccontextmanager
    async def _ctx(_tid):
        yield sess

    monkeypatch.setattr("core.database.get_tenant_session", _ctx)
    monkeypatch.setattr(res.settings, "secret_key", "ci-test-secret-key-minimum-16")
    res.invalidate()
    with patch("core.async_redis.get_async_redis", AsyncMock(return_value=None)):
        yield sess
    res.invalidate()


class TestLoading:
    def test_region_comes_from_the_governance_config_or_the_platform_default(self, session, monkeypatch):
        monkeypatch.setattr(res.settings, "data_region", "eu")
        assert asyncio.run(res._load_region(TENANT)) == "EU"
        session.config = SimpleNamespace(data_region="in")
        assert asyncio.run(res._load_region(TENANT)) == "IN"

    def test_attestations_are_mapped_and_cached(self, session):
        expires = datetime.now(UTC) + timedelta(days=1)
        session.rows = [_row(expires_at=expires), _row(provider="openai", data_region="us", in_region=False)]
        loaded = asyncio.run(res.active_attestations(TENANT))
        assert [a.provider for a in loaded] == ["gemini", "openai"]
        assert loaded[0].expires_at == expires.isoformat() and loaded[1].data_region == "US"
        session.rows = []
        assert asyncio.run(res.active_attestations(TENANT)) == []  # no Redis: every read goes to the database

    def test_an_expired_attestation_is_dropped_even_when_cached(self):
        import json

        expired = res.Attestation(
            id="a1",
            provider="gemini",
            data_region="IN",
            in_region=True,
            no_training=True,
            evidence_ref="",
            attested_by="u",
            expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        )
        live = res.Attestation(**{**expired.to_dict(), "id": "a2", "expires_at": None})
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=json.dumps([expired.to_dict(), live.to_dict()]))
        with patch("core.async_redis.get_async_redis", AsyncMock(return_value=redis)):
            assert [a.id for a in asyncio.run(res.active_attestations(TENANT))] == ["a2"]

    def test_the_shared_cache_is_filled_and_dropped(self, session):
        import json

        session.rows = [_row()]
        store: dict[str, str] = {}
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=lambda key: store.get(key))
        redis.set = AsyncMock(side_effect=lambda key, value, ex=None: store.__setitem__(key, value))
        redis.delete = AsyncMock(side_effect=lambda key: store.pop(key, None))
        with patch("core.async_redis.get_async_redis", AsyncMock(return_value=redis)):
            assert len(asyncio.run(res.active_attestations(TENANT))) == 1
            assert json.loads(next(iter(store.values())))[0]["provider"] == "gemini"
            asyncio.run(res.invalidate_attestations(TENANT))
            assert store == {}
            redis.set.assert_awaited()
            assert redis.set.await_args.kwargs["ex"] == res.ATTESTATION_CACHE_TTL_SECONDS


class TestChanges:
    def test_set_attestation_validates_before_writing(self, session):
        with pytest.raises(ValueError):
            asyncio.run(
                res.set_attestation(
                    TENANT,
                    provider=" ",
                    data_region="IN",
                    in_region=True,
                    no_training=True,
                    evidence_ref="",
                    actor_id="u",
                    expires_at=None,
                )
            )
        with pytest.raises(ValueError):
            asyncio.run(
                res.set_attestation(
                    TENANT,
                    provider="gemini",
                    data_region="MARS",
                    in_region=True,
                    no_training=True,
                    evidence_ref="",
                    actor_id="u",
                    expires_at=None,
                )
            )
        assert session.added == []

    def test_set_attestation_writes_the_row_and_a_signed_audit_entry(self, session):
        placed = asyncio.run(
            res.set_attestation(
                TENANT,
                provider="Gemini",
                data_region="in",
                in_region=True,
                no_training=False,
                evidence_ref=" order form OF-18 ",
                actor_id="user:1",
                expires_at=None,
            )
        )
        assert placed.provider == "gemini" and placed.data_region == "IN" and placed.evidence_ref == "order form OF-18"
        row, audit = session.added
        assert row.provider == "gemini" and row.no_training is False and row.attested_by == "user:1"
        assert audit.event_type == "residency_attestation.set" and audit.details["evidence_ref"] == "order form OF-18"
        assert audit.signature

    def test_revoke_attestation_marks_the_row_and_audits(self, session):
        row = _row()
        session.rows = [row]
        revoked = asyncio.run(res.revoke_attestation(TENANT, row.id, actor_id="api_key:k"))
        assert revoked is not None and revoked.id == str(row.id)
        assert row.revoked_at is not None and row.revoked_by == "api_key:k"
        (audit,) = session.added
        assert audit.event_type == "residency_attestation.revoked"

    def test_revoke_of_an_unknown_attestation_is_none(self, session):
        assert asyncio.run(res.revoke_attestation(TENANT, uuid.uuid4(), actor_id="user:1")) is None


class TestProfile:
    def test_deployment_profile_reports_conformance(self, monkeypatch):
        monkeypatch.setattr(res.settings, "data_region", "IN")
        monkeypatch.setattr(res.settings, "storage_region", "us-central1")
        monkeypatch.setattr(res.settings, "tenancy_profile", "shared")
        monkeypatch.setattr(res.settings, "dr_standby_region", None)
        profile = res.deployment_profile()
        assert profile["storage_region_conforms"] is False
        assert profile["disaster_recovery"]["status"] == "not_configured"
