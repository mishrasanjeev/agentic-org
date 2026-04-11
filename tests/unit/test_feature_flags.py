"""Tests for core.feature_flags — cache, rollout bucketing, tenant override."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from core.feature_flags import _bucket, clear_cache, is_enabled


class TestBucket:
    def test_deterministic(self):
        """Same (flag, subject) must always hash to the same bucket."""
        assert _bucket("workflow_builder", "user-123") == _bucket("workflow_builder", "user-123")

    def test_different_subjects_different_buckets(self):
        """Different subjects should usually land in different buckets."""
        buckets = {_bucket("flag", f"user-{i}") for i in range(50)}
        # 50 hashes mod 100 should give us a lot of distinct values
        assert len(buckets) > 20

    def test_bucket_range(self):
        """Bucket must always be in [0, 100)."""
        for i in range(100):
            b = _bucket("flag", f"sub-{i}")
            assert 0 <= b < 100


class TestIsEnabled:
    def setup_method(self):
        clear_cache()

    @pytest.mark.asyncio
    async def test_missing_flag_returns_default(self):
        """When the flag doesn't exist, fall back to `default`."""
        with patch("core.feature_flags._load_flag", AsyncMock(return_value=None)):
            assert await is_enabled("unknown_flag") is False
            assert await is_enabled("unknown_flag", default=True) is True

    @pytest.mark.asyncio
    async def test_disabled_flag_returns_false(self):
        with patch(
            "core.feature_flags._load_flag",
            AsyncMock(return_value={"enabled": False, "rollout_percentage": 100}),
        ):
            assert await is_enabled("flag") is False

    @pytest.mark.asyncio
    async def test_100_percent_rollout_on(self):
        with patch(
            "core.feature_flags._load_flag",
            AsyncMock(return_value={"enabled": True, "rollout_percentage": 100}),
        ):
            assert await is_enabled("flag", user_id=uuid.uuid4()) is True

    @pytest.mark.asyncio
    async def test_0_percent_rollout_off(self):
        with patch(
            "core.feature_flags._load_flag",
            AsyncMock(return_value={"enabled": True, "rollout_percentage": 0}),
        ):
            assert await is_enabled("flag", user_id=uuid.uuid4()) is False

    @pytest.mark.asyncio
    async def test_partial_rollout_is_deterministic(self):
        """Same user at 50% rollout gets a stable answer across calls."""
        user_id = uuid.uuid4()
        with patch(
            "core.feature_flags._load_flag",
            AsyncMock(return_value={"enabled": True, "rollout_percentage": 50}),
        ):
            first = await is_enabled("flag", user_id=user_id)
            second = await is_enabled("flag", user_id=user_id)
            assert first == second

    @pytest.mark.asyncio
    async def test_partial_rollout_splits_users_roughly(self):
        """Over many users, a 50% rollout should hit ~50% (within noise)."""
        with patch(
            "core.feature_flags._load_flag",
            AsyncMock(return_value={"enabled": True, "rollout_percentage": 50}),
        ):
            hits = 0
            for i in range(200):
                if await is_enabled("f", user_id=uuid.UUID(int=i)):
                    hits += 1
            # 200 * 0.5 = 100, allow +/- 25 slack
            assert 75 <= hits <= 125
