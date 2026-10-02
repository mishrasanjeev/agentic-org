# SPDX-License-Identifier: Apache-2.0
"""Per-model limits for the model gateway: concurrency leases and request rates.

A tenant administrator sets, per provider or per model, how many model calls
may be in flight at once (``max_concurrency``) and how many may start per
minute (``requests_per_minute``). The gateway admits a call just before the
model work starts and releases its concurrency slot when the work ends; a call
above a limit is refused with ``E1015`` (retryable, with the seconds to wait)
and the refusal is metered.

Both limits live in Redis so every API and worker process shares them:

* concurrency is a sorted set of leases scored by their expiry, pruned on every
  acquire, so a process that dies without releasing holds its slot for at most
  ``model_gateway_lease_seconds``;
* the request rate is the same token bucket the tool gateway uses.

A provider-wide row (``model`` empty) and a model row both apply to a call on
that model; the call must pass every applicable row. When Redis is unavailable
the call is admitted and the outcome is metered as ``unavailable``: the limits
protect providers and budgets, they are not a security control, and a cache
outage must not stop every model call.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

LIMIT_KINDS: tuple[str, ...] = ("concurrency", "rate")
_CONCURRENCY_PREFIX = "model_gateway:leases:"
_RATE_PREFIX = "model_gateway:rate:"

# KEYS[1] = lease set; ARGV[1] = now, ARGV[2] = expiry of the new lease,
# ARGV[3] = max concurrency, ARGV[4] = lease id. Expired leases are pruned
# first; returns {admitted (0|1), leases in flight after the call}.
_ACQUIRE_LUA = """
local key     = KEYS[1]
local now     = tonumber(ARGV[1])
local expiry  = tonumber(ARGV[2])
local maximum = tonumber(ARGV[3])
local lease   = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
local count = redis.call('ZCARD', key)
if count >= maximum then
    return {0, count}
end
redis.call('ZADD', key, expiry, lease)
redis.call('EXPIRE', key, math.ceil(expiry - now) + 1)
return {1, count + 1}
"""


@dataclass(frozen=True)
class Limit:
    """One row of ``model_limits``: a provider-wide limit when ``model`` is None."""

    id: str
    provider: str
    model: str | None = None
    enabled: bool = True
    max_concurrency: int | None = None
    requests_per_minute: int | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "model": self.model,
            "enabled": self.enabled,
            "max_concurrency": self.max_concurrency,
            "requests_per_minute": self.requests_per_minute,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Limit:
        return cls(
            **{k: data.get(k) for k in ("id", "provider", "model", "max_concurrency", "requests_per_minute")},
            enabled=bool(data.get("enabled", True)),
            reason=data.get("reason") or "",
        )

    def applies_to(self, provider: str | None, model: str) -> bool:
        if (provider or "").strip().lower() != self.provider.strip().lower():
            return False
        return self.model is None or self.model.strip().lower() == (model or "").strip().lower()


@dataclass(frozen=True)
class Lease:
    """The concurrency slots a call holds; released when its model work ends."""

    lease_id: str
    keys: tuple[str, ...] = ()
    outcome: str = "allowed"

    @property
    def held(self) -> bool:
        return bool(self.keys)


@dataclass(frozen=True)
class LimitRejected:
    limit: Limit
    kind: str
    retry_after_seconds: float
    in_flight: int = 0


@dataclass
class Admission:
    lease: Lease
    rejected: LimitRejected | None = None
    checked: list[str] = field(default_factory=list)


def _meter(kind: str, outcome: str) -> None:
    try:
        from observability.metrics import model_gateway_limit_outcomes_total

        model_gateway_limit_outcomes_total.labels(limit=kind, outcome=outcome).inc()
    # enterprise-gate: broad-except-ok reason=metrics-outage-degrades-to-an-unmetered-admission-never-changes-it
    except Exception:
        logger.debug("model_gateway_limit_metric_skipped", limit=kind, outcome=outcome)


def _concurrency_key(tenant_id: str, limit: Limit) -> str:
    return f"{_CONCURRENCY_PREFIX}{tenant_id}:{limit.id}"


def _rate_key(tenant_id: str, limit: Limit) -> str:
    return f"{_RATE_PREFIX}{tenant_id}:{limit.id}"


async def _acquire(redis: Any, key: str, maximum: int, lease_id: str, now: float) -> tuple[bool, int]:
    expiry = now + float(settings.model_gateway_lease_seconds)
    result = await redis.eval(_ACQUIRE_LUA, 1, key, str(now), str(expiry), str(maximum), lease_id)
    return int(result[0]) == 1, int(result[1])


async def _rate_allowed(redis: Any, key: str, rpm: int, now: float) -> tuple[bool, float]:
    from core.tool_gateway.rate_limiter import _TOKEN_BUCKET_LUA

    result = await redis.eval(_TOKEN_BUCKET_LUA, 1, key, str(rpm), str(rpm / 60.0), str(now))
    allowed = int(result[0]) == 1
    return allowed, 0.0 if allowed else float(result[2])


async def admit(
    tenant_id: str, provider: str | None, model: str, limits: list[Limit], *, correlation_id: str
) -> Admission:
    """Admit a call on ``model`` under every enabled limit that applies, or say which one refuses it.

    The concurrency slots acquired before a later limit refuses the call are
    released again, so a refused call holds nothing.
    """
    applicable = [limit for limit in limits if limit.enabled and limit.applies_to(provider, model)]
    lease_id = correlation_id or uuid.uuid4().hex
    if not applicable:
        return Admission(lease=Lease(lease_id=lease_id, outcome="unlimited"))
    from core.async_redis import get_async_redis

    try:
        redis = await get_async_redis()
    # enterprise-gate: broad-except-ok reason=cache-outage-degrades-to-an-admitted-metered-call
    except Exception as exc:
        logger.warning("model_gateway_limit_store_failed", error_type=type(exc).__name__)
        redis = None
    if redis is None:
        for limit in applicable:
            if limit.max_concurrency is not None:
                _meter("concurrency", "unavailable")
            if limit.requests_per_minute is not None:
                _meter("rate", "unavailable")
        logger.warning(
            "model_gateway_limits_unavailable", correlation_id=correlation_id, provider=provider, model=model
        )
        return Admission(lease=Lease(lease_id=lease_id, outcome="unavailable"))

    now = time.time()
    held: list[str] = []
    checked: list[str] = []
    try:
        for limit in applicable:
            if limit.requests_per_minute is not None:
                allowed, retry_after = await _rate_allowed(
                    redis, _rate_key(tenant_id, limit), limit.requests_per_minute, now
                )
                checked.append(f"rate:{limit.id}")
                _meter("rate", "allowed" if allowed else "rejected")
                if not allowed:
                    await _release_keys(redis, held, lease_id)
                    return Admission(
                        lease=Lease(lease_id=lease_id, outcome="rejected"),
                        rejected=LimitRejected(limit=limit, kind="rate", retry_after_seconds=max(retry_after, 0.001)),
                        checked=checked,
                    )
            if limit.max_concurrency is not None:
                key = _concurrency_key(tenant_id, limit)
                admitted, in_flight = await _acquire(redis, key, limit.max_concurrency, lease_id, now)
                checked.append(f"concurrency:{limit.id}")
                _meter("concurrency", "allowed" if admitted else "rejected")
                if not admitted:
                    await _release_keys(redis, held, lease_id)
                    return Admission(
                        lease=Lease(lease_id=lease_id, outcome="rejected"),
                        rejected=LimitRejected(
                            limit=limit, kind="concurrency", retry_after_seconds=1.0, in_flight=in_flight
                        ),
                        checked=checked,
                    )
                held.append(key)
    # enterprise-gate: broad-except-ok reason=cache-outage-mid-check-degrades-to-an-admitted-metered-call
    except Exception as exc:
        logger.warning("model_gateway_limit_check_failed", error_type=type(exc).__name__, correlation_id=correlation_id)
        await _release_keys(redis, held, lease_id)
        _meter("concurrency", "unavailable")
        return Admission(lease=Lease(lease_id=lease_id, outcome="unavailable"), checked=checked)
    return Admission(lease=Lease(lease_id=lease_id, keys=tuple(held)), checked=checked)


async def _release_keys(redis: Any, keys: list[str] | tuple[str, ...], lease_id: str) -> None:
    for key in keys:
        try:
            await redis.zrem(key, lease_id)
        # enterprise-gate: broad-except-ok reason=an-unreleased-lease-expires-on-its-own-ttl
        except Exception as exc:
            logger.warning("model_gateway_lease_release_failed", error_type=type(exc).__name__, key=key)


async def release(lease: Lease | None) -> None:
    """Give back the concurrency slots a lease holds; a lease that holds none is a no-op."""
    if lease is None or not lease.held:
        return
    from core.async_redis import get_async_redis

    try:
        redis = await get_async_redis()
    # enterprise-gate: broad-except-ok reason=an-unreleased-lease-expires-on-its-own-ttl
    except Exception as exc:
        logger.warning("model_gateway_lease_release_failed", error_type=type(exc).__name__)
        return
    if redis is None:
        return
    await _release_keys(redis, lease.keys, lease.lease_id)


def validate_limit_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Normalise and check a limit row's fields; raise ``ValueError`` on an unusable one."""
    from core.governance.model_gateway import normalise_provider

    provider = normalise_provider(fields.get("provider"))
    if not provider:
        raise ValueError("a limit needs a provider")
    model = str(fields.get("model") or "").strip() or None
    if model:
        from core.ai_providers.catalog import validate_llm_selection

        provider, model = validate_llm_selection(provider, model)
    out: dict[str, Any] = {"provider": provider, "model": model}
    for name in ("max_concurrency", "requests_per_minute"):
        value = fields.get(name)
        if value is None:
            out[name] = None
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer when given")
        out[name] = value
    if out["max_concurrency"] is None and out["requests_per_minute"] is None:
        raise ValueError("a limit must set max_concurrency, requests_per_minute or both")
    out["enabled"] = bool(fields.get("enabled", True))
    out["reason"] = (fields.get("reason") or "").strip()
    return out
