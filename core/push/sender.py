"""Web Push notification sender using pywebpush."""

from __future__ import annotations

import json
from typing import Any

import structlog

from core.config import is_strict_runtime_env, settings
from core.push.vapid import get_vapid_keys

try:  # Redis is optional for local tests, mandatory for strict runtimes.
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover - exercised only when redis is absent
    RedisError = ConnectionError  # type: ignore[assignment,misc]

_log = structlog.get_logger()

# In-memory fallback when Redis is unavailable (development only).
# Maps tenant_id -> set of JSON-serialized subscription strings.
_memory_store: dict[str, set[str]] = {}

# Contact email for VAPID claims — required by the Web Push protocol.
_VAPID_CONTACT = "mailto:push@agenticorg.com"


class PushSubscriptionStoreUnavailableError(RuntimeError):
    """Raised when strict runtimes cannot reach the push subscription store."""


def _memory_fallback_allowed() -> bool:
    return not is_strict_runtime_env(settings.env)


def _ensure_memory_fallback_allowed(operation: str, tenant_id: str) -> None:
    if _memory_fallback_allowed():
        return
    raise PushSubscriptionStoreUnavailableError(
        f"Push subscription {operation} requires Redis in strict runtime "
        f"for tenant {tenant_id}."
    )


def _get_redis():
    """Return an async Redis client, or None if unavailable."""
    try:
        import redis.asyncio as aioredis

        return aioredis.from_url(settings.redis_url, decode_responses=True)
    except (ImportError, ValueError) as exc:
        if not _memory_fallback_allowed():
            raise PushSubscriptionStoreUnavailableError(
                "Push subscriptions require Redis in strict runtime."
            ) from exc
        _log.warning("push_redis_client_unavailable_dev_fallback", error=str(exc))
        return None


def _redis_key(tenant_id: str) -> str:
    return f"push_subs:{tenant_id}"


async def save_subscription(tenant_id: str, subscription_json: dict) -> None:
    """Store a push subscription for a tenant.

    Persists to Redis set ``push_subs:{tenant_id}``.  Falls back to an
    in-memory set when Redis is unreachable (useful for local development).
    """
    serialized = json.dumps(subscription_json, sort_keys=True)

    redis = _get_redis()
    if redis:
        try:
            await redis.sadd(_redis_key(tenant_id), serialized)
            await redis.aclose()
            _log.info("push_subscription_saved", tenant_id=tenant_id)
            return
        except RedisError as exc:
            _log.warning("redis_save_failed_falling_back", error=str(exc))
            try:
                await redis.aclose()
            except RedisError:  # noqa: S110
                pass
            _ensure_memory_fallback_allowed("save", tenant_id)

    # Fallback: in-memory
    _ensure_memory_fallback_allowed("save", tenant_id)
    _memory_store.setdefault(tenant_id, set()).add(serialized)
    _log.info("push_subscription_saved_memory", tenant_id=tenant_id)


async def remove_subscription(tenant_id: str, endpoint: str) -> None:
    """Remove a subscription matching the given endpoint.

    Scans all subscriptions for the tenant and removes the one whose
    ``endpoint`` field matches.
    """
    redis = _get_redis()
    if redis:
        try:
            members = await redis.smembers(_redis_key(tenant_id))
            for member in members:
                sub = json.loads(member)
                if sub.get("endpoint") == endpoint:
                    await redis.srem(_redis_key(tenant_id), member)
                    _log.info("push_subscription_removed", tenant_id=tenant_id)
                    break
            await redis.aclose()
            return
        except RedisError as exc:
            _log.warning("redis_remove_failed_falling_back", error=str(exc))
            try:
                await redis.aclose()
            except RedisError:  # noqa: S110
                pass
            _ensure_memory_fallback_allowed("remove", tenant_id)

    # Fallback: in-memory
    _ensure_memory_fallback_allowed("remove", tenant_id)
    store = _memory_store.get(tenant_id, set())
    to_remove = None
    for member in store:
        sub = json.loads(member)
        if sub.get("endpoint") == endpoint:
            to_remove = member
            break
    if to_remove:
        store.discard(to_remove)
        _log.info("push_subscription_removed_memory", tenant_id=tenant_id)


async def _get_subscriptions(tenant_id: str) -> list[dict]:
    """Return all stored subscriptions for a tenant."""
    redis = _get_redis()
    if redis:
        try:
            members = await redis.smembers(_redis_key(tenant_id))
            await redis.aclose()
            return [json.loads(m) for m in members]
        except RedisError as exc:
            _log.warning("redis_get_failed_falling_back", error=str(exc))
            try:
                await redis.aclose()
            except RedisError:  # noqa: S110
                pass
            _ensure_memory_fallback_allowed("read", tenant_id)

    # Fallback: in-memory
    _ensure_memory_fallback_allowed("read", tenant_id)
    store = _memory_store.get(tenant_id, set())
    return [json.loads(m) for m in store]


async def send_push_notification(
    tenant_id: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    actions: list[dict[str, str]] | None = None,
) -> dict[str, int]:
    """Send a web push notification to all subscriptions for a tenant.

    Returns:
        Dict with keys ``sent``, ``failed``, ``stale_removed`` indicating
        delivery statistics.
    """
    from pywebpush import WebPushException, webpush

    public_key, private_key = get_vapid_keys()
    subscriptions = await _get_subscriptions(tenant_id)

    if not subscriptions:
        _log.info("no_push_subscriptions", tenant_id=tenant_id)
        return {"sent": 0, "failed": 0, "stale_removed": 0}

    payload = json.dumps({
        "title": title,
        "body": body,
        "data": data or {},
        "actions": actions or [],
    })

    sent = 0
    failed = 0
    stale_removed = 0

    for sub in subscriptions:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": _VAPID_CONTACT},
            )
            sent += 1
        except WebPushException as exc:
            status_code = getattr(exc, "response", None)
            if status_code is not None:
                status_code = getattr(status_code, "status_code", None)

            if status_code == 410:
                # Subscription expired — clean it up
                _log.info("stale_subscription_removed", endpoint=sub.get("endpoint", "")[:60])
                await remove_subscription(tenant_id, sub.get("endpoint", ""))
                stale_removed += 1
            else:
                _log.warning(
                    "push_send_failed",
                    tenant_id=tenant_id,
                    endpoint=sub.get("endpoint", "")[:60],
                    error=str(exc),
                )
                failed += 1
        # enterprise-gate: broad-except-ok reason=push-delivery-failure-records-failed-count
        except Exception as exc:
            _log.warning(
                "push_send_error",
                tenant_id=tenant_id,
                error=str(exc),
            )
            failed += 1

    _log.info(
        "push_batch_complete",
        tenant_id=tenant_id,
        sent=sent,
        failed=failed,
        stale_removed=stale_removed,
    )
    return {"sent": sent, "failed": failed, "stale_removed": stale_removed}
