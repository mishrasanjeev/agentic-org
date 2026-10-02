# SPDX-License-Identifier: Apache-2.0
"""Per-model limits: admission under concurrency and rate limits, release, and the degraded paths."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from core.governance import model_gateway_limits as lim

TENANT = str(uuid.uuid4())


def _limit(**over) -> lim.Limit:
    base = {"id": str(uuid.uuid4()), "provider": "openai", "model": "gpt-4o", "max_concurrency": 2}
    base.update(over)
    return lim.Limit(**base)


class _Redis:
    """A Redis double that answers the two Lua scripts from scripted outcomes and records releases."""

    def __init__(self, acquire=(1, 1), rate=(1, "9", "0")):
        self.acquire = list(acquire) if isinstance(acquire, list) else acquire
        self.rate = rate
        self.evals: list[tuple[str, tuple]] = []
        self.removed: list[tuple[str, str]] = []

    async def eval(self, script, _numkeys, *args):
        kind = "acquire" if "ZADD" in script else "rate"
        self.evals.append((kind, args))
        if kind == "acquire":
            if isinstance(self.acquire, list):
                return list(self.acquire.pop(0))
            return list(self.acquire)
        return list(self.rate)

    async def zrem(self, key, member):
        self.removed.append((key, member))


def _with(redis):
    return patch("core.async_redis.get_async_redis", AsyncMock(return_value=redis))


class TestLimitRows:
    def test_applies_to_the_model_or_the_whole_provider(self):
        model_limit = _limit()
        provider_limit = _limit(model=None)
        assert model_limit.applies_to("openai", "gpt-4o") and not model_limit.applies_to("openai", "gpt-4o-mini")
        assert provider_limit.applies_to("OpenAI", "gpt-4o-mini") and not provider_limit.applies_to("gemini", "x")
        assert not model_limit.applies_to(None, "gpt-4o")

    def test_round_trips_through_dicts(self):
        limit = _limit(requests_per_minute=30, reason="capacity")
        assert lim.Limit.from_dict(limit.to_dict()) == limit

    def test_validation(self):
        clean = lim.validate_limit_fields({"provider": "OpenAI", "model": "gpt-4o", "max_concurrency": 3})
        assert clean == {
            "provider": "openai",
            "model": "gpt-4o",
            "max_concurrency": 3,
            "requests_per_minute": None,
            "enabled": True,
            "reason": "",
        }
        assert lim.validate_limit_fields({"provider": "ollama", "requests_per_minute": 10})["model"] is None
        with pytest.raises(ValueError, match="needs a provider"):
            lim.validate_limit_fields({"model": "gpt-4o", "max_concurrency": 1})
        with pytest.raises(ValueError, match="positive integer"):
            lim.validate_limit_fields({"provider": "openai", "max_concurrency": 0})
        with pytest.raises(ValueError, match="positive integer"):
            lim.validate_limit_fields({"provider": "openai", "requests_per_minute": True})
        with pytest.raises(ValueError, match="must set"):
            lim.validate_limit_fields({"provider": "openai"})
        with pytest.raises(ValueError):
            lim.validate_limit_fields({"provider": "openai", "model": "not-a-model", "max_concurrency": 1})


class TestAdmit:
    def test_no_applicable_limit_reads_nothing(self):
        with patch("core.async_redis.get_async_redis", AsyncMock()) as store:
            admission = asyncio.run(lim.admit(TENANT, "gemini", "gemini-2.5-flash", [_limit()], correlation_id="c1"))
        assert admission.rejected is None and admission.lease.outcome == "unlimited" and not admission.lease.held
        store.assert_not_called()

    def test_a_disabled_limit_does_not_apply(self):
        with patch("core.async_redis.get_async_redis", AsyncMock()) as store:
            admission = asyncio.run(lim.admit(TENANT, "openai", "gpt-4o", [_limit(enabled=False)], correlation_id="c1"))
        assert admission.lease.outcome == "unlimited"
        store.assert_not_called()

    def test_an_admitted_call_holds_a_slot_per_applicable_limit_and_releases_them(self):
        redis = _Redis()
        model_limit, provider_limit = _limit(), _limit(model=None, max_concurrency=5)
        with _with(redis):
            admission = asyncio.run(
                lim.admit(TENANT, "openai", "gpt-4o", [model_limit, provider_limit], correlation_id="c1")
            )
            assert admission.rejected is None and admission.lease.held and len(admission.lease.lease_id) == 32
            assert admission.lease.keys == (
                f"model_gateway:leases:{TENANT}:{model_limit.id}",
                f"model_gateway:leases:{TENANT}:{provider_limit.id}",
            )
            # The acquire script gets now, expiry, the maximum and the lease id.
            assert [args[3] for kind, args in redis.evals if kind == "acquire"] == ["2", "5"]
            assert all(args[4] == admission.lease.lease_id for kind, args in redis.evals if kind == "acquire")
            asyncio.run(lim.release(admission.lease))
        assert redis.removed == [(key, admission.lease.lease_id) for key in admission.lease.keys]

    def test_the_concurrency_limit_refuses_above_the_maximum(self):
        redis = _Redis(acquire=(0, 2))
        limit = _limit()
        with _with(redis):
            admission = asyncio.run(lim.admit(TENANT, "openai", "gpt-4o", [limit], correlation_id="c2"))
        assert admission.rejected is not None
        assert admission.rejected.kind == "concurrency" and admission.rejected.limit is limit
        assert admission.rejected.in_flight == 2 and admission.lease.outcome == "rejected" and not admission.lease.held

    def test_the_rate_limit_refuses_with_the_wait_and_gives_back_slots_already_held(self):
        redis = _Redis(acquire=(1, 1), rate=(0, "0", "12.5"))
        provider_limit = _limit(model=None, max_concurrency=4)  # acquired first
        model_limit = _limit(requests_per_minute=60, max_concurrency=None)  # then refuses
        with _with(redis):
            admission = asyncio.run(
                lim.admit(TENANT, "openai", "gpt-4o", [provider_limit, model_limit], correlation_id="c3")
            )
        assert admission.rejected is not None and admission.rejected.kind == "rate"
        assert admission.rejected.retry_after_seconds == 12.5
        assert redis.removed == [(f"model_gateway:leases:{TENANT}:{provider_limit.id}", admission.lease.lease_id)]
        assert not admission.lease.held

    def test_the_rate_bucket_is_sized_from_requests_per_minute(self):
        redis = _Redis()
        with _with(redis):
            asyncio.run(
                lim.admit(
                    TENANT,
                    "openai",
                    "gpt-4o",
                    [_limit(requests_per_minute=120, max_concurrency=None)],
                    correlation_id="c4",
                )
            )
        kind, args = redis.evals[0]
        assert kind == "rate" and args[1] == "120" and float(args[2]) == 2.0

    def test_an_unavailable_store_admits_and_meters_unavailable(self):
        counted: list[tuple[str, str]] = []
        with (
            _with(None),
            patch.object(lim, "_meter", lambda kind, outcome: counted.append((kind, outcome))),
        ):
            admission = asyncio.run(
                lim.admit(TENANT, "openai", "gpt-4o", [_limit(requests_per_minute=10)], correlation_id="c5")
            )
        assert admission.rejected is None and admission.lease.outcome == "unavailable" and not admission.lease.held
        assert sorted(counted) == [("concurrency", "unavailable"), ("rate", "unavailable")]

    def test_a_store_that_fails_mid_check_admits_and_releases_what_was_held(self):
        redis = _Redis()
        calls = {"n": 0}

        async def flaky_eval(script, numkeys, *args):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ConnectionError("gone")
            redis.evals.append(("acquire", args))
            return [1, 1]

        redis.eval = flaky_eval
        with _with(redis):
            admission = asyncio.run(
                lim.admit(
                    TENANT, "openai", "gpt-4o", [_limit(), _limit(model=None, max_concurrency=3)], correlation_id="c6"
                )
            )
        assert admission.lease.outcome == "unavailable" and not admission.lease.held
        assert len(redis.removed) == 1 and redis.removed[0][1] == admission.lease.lease_id

    def test_a_failing_store_lookup_admits(self):
        with patch("core.async_redis.get_async_redis", AsyncMock(side_effect=RuntimeError("no pool"))):
            admission = asyncio.run(lim.admit(TENANT, "openai", "gpt-4o", [_limit()], correlation_id="c7"))
        assert admission.lease.outcome == "unavailable"


class TestRelease:
    def test_release_of_nothing_reads_nothing(self):
        with patch("core.async_redis.get_async_redis", AsyncMock()) as store:
            asyncio.run(lim.release(None))
            asyncio.run(lim.release(lim.Lease(lease_id="x")))
        store.assert_not_called()

    def test_release_survives_a_missing_or_failing_store(self):
        lease = lim.Lease(lease_id="x", keys=("k",))
        with _with(None):
            asyncio.run(lim.release(lease))
        with patch("core.async_redis.get_async_redis", AsyncMock(side_effect=RuntimeError("no pool"))):
            asyncio.run(lim.release(lease))
        redis = _Redis()

        async def failing_zrem(key, member):
            raise ConnectionError("gone")

        redis.zrem = failing_zrem
        with _with(redis):
            asyncio.run(lim.release(lease))  # logged, never raised

    def test_the_metric_is_best_effort(self):
        with patch("observability.metrics.model_gateway_limit_outcomes_total", None):
            lim._meter("rate", "allowed")  # a missing metric never changes an admission
