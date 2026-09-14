"""Web Push notification sender using pywebpush."""

from __future__ import annotations

import asyncio
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
# Maps store key (tenant or tenant+user) -> set of JSON-serialized subscriptions.
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


def _redis_key(tenant_id: str, user_id: str = "") -> str:
    """Subscription set key.

    Subscriptions are keyed by tenant **and** user so an approval push
    reaches the users who can act on it, not every browser in the tenant.
    ``push_subs:{tenant}`` (no user) remains the tenant-wide broadcast set.
    """
    if user_id:
        return f"push_subs:{tenant_id}:user:{user_id}"
    return f"push_subs:{tenant_id}"


def _user_index_key(tenant_id: str) -> str:
    return f"push_subs:{tenant_id}:users"


async def save_subscription(
    tenant_id: str, subscription_json: dict, user_id: str = ""
) -> None:
    """Store a push subscription for a tenant (and, when given, a user).

    Persists to the Redis set ``push_subs:{tenant_id}[:user:{user_id}]``.
    Falls back to an in-memory set when Redis is unreachable (useful for
    local development).
    """
    serialized = json.dumps(subscription_json, sort_keys=True)
    key = _redis_key(tenant_id, user_id)

    redis = _get_redis()
    if redis:
        try:
            await redis.sadd(key, serialized)
            if user_id:
                await redis.sadd(_user_index_key(tenant_id), user_id)
            await redis.aclose()
            _log.info("push_subscription_saved", tenant_id=tenant_id, has_user=bool(user_id))
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
    _memory_store.setdefault(key, set()).add(serialized)
    if user_id:
        _memory_store.setdefault(_user_index_key(tenant_id), set()).add(user_id)
    _log.info("push_subscription_saved_memory", tenant_id=tenant_id)


async def remove_subscription(tenant_id: str, endpoint: str, user_id: str = "") -> None:
    """Remove a subscription matching the given endpoint.

    Scans the tenant (or tenant+user) set and removes the one whose
    ``endpoint`` field matches. A user can only remove endpoints stored
    under their own key or the tenant-wide set.
    """
    keys = [_redis_key(tenant_id, user_id)]
    if user_id:
        keys.append(_redis_key(tenant_id))

    redis = _get_redis()
    if redis:
        try:
            for key in keys:
                members = await redis.smembers(key)
                for member in members:
                    sub = json.loads(member)
                    if sub.get("endpoint") == endpoint:
                        await redis.srem(key, member)
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
    for key in keys:
        store = _memory_store.get(key, set())
        to_remove = None
        for member in store:
            sub = json.loads(member)
            if sub.get("endpoint") == endpoint:
                to_remove = member
                break
        if to_remove:
            store.discard(to_remove)
            _log.info("push_subscription_removed_memory", tenant_id=tenant_id)


async def _get_subscriptions(tenant_id: str, user_id: str = "") -> list[dict]:
    """Return stored subscriptions for a tenant (tenant-wide set) or one user."""
    key = _redis_key(tenant_id, user_id)
    redis = _get_redis()
    if redis:
        try:
            members = await redis.smembers(key)
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
    store = _memory_store.get(key, set())
    return [json.loads(m) for m in store]


async def _subscribed_user_ids(tenant_id: str) -> list[str]:
    """Return user ids that hold a per-user subscription in this tenant."""
    key = _user_index_key(tenant_id)
    redis = _get_redis()
    if redis:
        try:
            members = await redis.smembers(key)
            await redis.aclose()
            return sorted(str(m) for m in members)
        except RedisError as exc:
            _log.warning("redis_get_failed_falling_back", error=str(exc))
            try:
                await redis.aclose()
            except RedisError:  # noqa: S110
                pass
            _ensure_memory_fallback_allowed("read", tenant_id)

    _ensure_memory_fallback_allowed("read", tenant_id)
    return sorted(_memory_store.get(key, set()))


async def _deliver(
    tenant_id: str,
    subscriptions: list[dict],
    payload: str,
    private_key: str,
    user_id: str = "",
) -> dict[str, int]:
    from pywebpush import WebPushException, webpush

    sent = 0
    failed = 0
    stale_removed = 0

    for sub in subscriptions:
        try:
            # pywebpush is synchronous (requests + VAPID signing); run it off
            # the event loop so a slow push service cannot stall the API.
            await asyncio.to_thread(
                webpush,
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
                await remove_subscription(tenant_id, sub.get("endpoint", ""), user_id=user_id)
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

    return {"sent": sent, "failed": failed, "stale_removed": stale_removed}


def _payload(
    title: str, body: str, data: dict[str, Any] | None, actions: list[dict[str, str]] | None
) -> str:
    return json.dumps({
        "title": title,
        "body": body,
        "data": data or {},
        "actions": actions or [],
    })


async def send_push_notification(
    tenant_id: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    actions: list[dict[str, str]] | None = None,
) -> dict[str, int]:
    """Send a web push notification to the tenant-wide subscription set.

    Returns:
        Dict with keys ``sent``, ``failed``, ``stale_removed`` indicating
        delivery statistics.
    """
    public_key, private_key = get_vapid_keys()
    subscriptions = await _get_subscriptions(tenant_id)

    if not subscriptions:
        _log.info("no_push_subscriptions", tenant_id=tenant_id)
        return {"sent": 0, "failed": 0, "stale_removed": 0}

    result = await _deliver(
        tenant_id, subscriptions, _payload(title, body, data, actions), private_key
    )
    _log.info("push_batch_complete", tenant_id=tenant_id, **result)
    return result


async def send_push_notification_for_user(
    tenant_id: str,
    user_id: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    actions: list[dict[str, str]] | None = None,
) -> dict[str, int]:
    """Send a web push notification to one user's subscriptions only."""
    if not user_id:
        raise ValueError("send_push_notification_for_user requires a user_id")
    public_key, private_key = get_vapid_keys()
    subscriptions = await _get_subscriptions(tenant_id, user_id)

    if not subscriptions:
        _log.info("no_push_subscriptions", tenant_id=tenant_id, scope="user")
        return {"sent": 0, "failed": 0, "stale_removed": 0}

    result = await _deliver(
        tenant_id,
        subscriptions,
        _payload(title, body, data, actions),
        private_key,
        user_id=user_id,
    )
    _log.info("push_user_batch_complete", tenant_id=tenant_id, **result)
    return result


async def _approval_agent_scope(tenant_id: str, item_id: str) -> tuple[str, str | None] | None:
    """``(visibility, owner_user_id)`` of a committed HITL item's agent, or None."""
    import uuid

    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.agent import Agent
    from core.models.hitl import HITLQueue

    try:
        tid = uuid.UUID(str(tenant_id))
        hid = uuid.UUID(str(item_id))
    except (TypeError, ValueError):
        return None
    async with get_tenant_session(tid) as session:
        row = (
            await session.execute(
                select(Agent.visibility, Agent.owner_user_id)
                .join(HITLQueue, HITLQueue.agent_id == Agent.id)
                .where(HITLQueue.id == hid, HITLQueue.tenant_id == tid)
            )
        ).one_or_none()
    if row is None:
        return None
    return str(row[0] or "tenant"), (str(row[1]) if row[1] else None)


async def notify_approval_created(
    tenant_id: str,
    *,
    item_id: str,
    agent_name: str = "",
    action: str = "",
    user_ids: list[str] | None = None,
    agent_visibility: str | None = None,
    agent_owner_user_id: str | None = None,
) -> dict[str, int]:
    """Push "approval needed" to the users who can act on a new HITL item.

    ``user_ids`` scopes delivery to specific approvers; when omitted every
    user with a per-user subscription in the tenant is notified (approval
    visibility is still enforced server-side by ``/approvals``). Never
    raises — approval creation must not fail because push is down.

    Bug sheet 2026-09-14 row 30: an item for a ``personal`` agent is pushed
    to the agent's owner only (nobody when ownerless). Callers should pass
    ``agent_visibility``/``agent_owner_user_id``; when they do not, the
    committed item's agent is looked up, and an item whose agent cannot be
    resolved is not pushed at all (fail closed).
    """
    totals = {"sent": 0, "failed": 0, "stale_removed": 0}
    try:
        if agent_visibility is None:
            scope = await _approval_agent_scope(tenant_id, item_id)
            if scope is None:
                _log.warning("approval_push_skipped_unknown_agent_scope", tenant_id=tenant_id)
                return totals
            agent_visibility, agent_owner_user_id = scope
        if agent_visibility == "personal":
            targets = [str(agent_owner_user_id)] if agent_owner_user_id else []
        else:
            targets = user_ids if user_ids is not None else await _subscribed_user_ids(tenant_id)
        title = "Approval needed"
        body = f"{agent_name or 'An agent'} needs approval" + (f": {action}" if action else "")
        data = {"url": "/dashboard/approvals", "approval_id": str(item_id)}
        for uid in targets:
            result = await send_push_notification_for_user(
                tenant_id, str(uid), title, body, data=data
            )
            for k in totals:
                totals[k] += result.get(k, 0)
    # enterprise-gate: broad-except-ok reason=approval-push-failure-does-not-block-hitl-creation-logged-only
    except Exception as exc:
        _log.warning("approval_push_failed", tenant_id=tenant_id, error=str(exc))
    return totals
