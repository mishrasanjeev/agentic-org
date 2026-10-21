# SPDX-License-Identifier: Apache-2.0
"""On-demand freshness for short-lived OACP artifacts.

Grantex issues the C6Z artifact set with short lifetimes for the facts that
change (inventory 60 s, price 5 min, mandate capability 2 min, public
discovery 15 min). Catalog search and lookup, the ACP feed, checkout decisions
and the public UCP profile refuse anything expired, by design. Nothing renewed
them, so every merchant's catalog went unavailable a minute after an issuance.

Behind ``AGENTICORG_OACP_ON_DEMAND_REFRESH_ENABLED`` (default off), the read
paths call :func:`ensure_fresh` with the artifact types they need before they
decide. When a required type has no usable verified record that outlives
``REFRESH_MARGIN_SECONDS``, one refresh is started for the merchant scope:

- **single flight**: a Redis ``SET NX`` lock per (tenant, merchant, seller
  agent), so concurrent reads and replicas trigger one Grantex issuance;
- **bounded cost**: at most one issuance per ``MIN_REFRESH_INTERVAL_SECONDS``
  per scope, a backoff after a failure (longer after a refusal or when the
  scope cannot be refreshed), and nothing at all when nobody reads: there is
  no loop and no worker of its own;
- **bounded wait**: the read waits at most ``oacp_refresh_wait_ms`` and then
  continues with what is cached, so a stale scope still gets the existing
  fail-closed refusal; the refresh finishes in the background for the next
  read;
- **bounded growth**: after each refresh the scope's cache keeps the newest
  ``KEEP_PER_TYPE`` records of each type and drops records expired for more
  than ``PRUNE_GRACE_SECONDS`` (expired records are refused by every reader).

Without Redis no refresh is attempted (a refresh lock that cannot be shared
across replicas is not taken) and reads behave as with the flag off.

The refresh itself (connector evidence re-read when it is too old for Grantex,
the authority request, verification and caching, every write ledgered) is
``api.v1.commerce_runtime.refresh_scope_artifacts``. The optional sweep
(``AGENTICORG_OACP_REFRESH_SWEEP_ENABLED``, beat every 5 minutes) refreshes
only scopes read in the last ``DEMAND_WINDOW_SECONDS``, so their evidence and
public-discovery artifact stay current off the read path.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from core.commerce.arbiter import REQUIRED_ARTIFACT_FAMILIES
from core.commerce.ucp.catalog import REQUIRED_CATALOG_ARTIFACTS, usable_artifact_ids

logger = structlog.get_logger()

CATALOG_TYPES: tuple[str, ...] = REQUIRED_CATALOG_ARTIFACTS
COMMITMENT_TYPES: tuple[str, ...] = tuple(
    dict.fromkeys((*REQUIRED_CATALOG_ARTIFACTS, *REQUIRED_ARTIFACT_FAMILIES["session_complete"]))
)
DISCOVERY_TYPES: tuple[str, ...] = ("public_discovery",)
HOST_TYPES: tuple[str, ...] = tuple(dict.fromkeys((*DISCOVERY_TYPES, *COMMITMENT_TYPES)))

REFRESH_MARGIN_SECONDS = 10
MIN_REFRESH_INTERVAL_SECONDS = 30
LOCK_TTL_SECONDS = 30
RETRY_BACKOFF_SECONDS = 30
REFUSAL_BACKOFF_SECONDS = 300
NOT_REFRESHABLE_BACKOFF_SECONDS = 600
LOCK_POLL_SECONDS = 0.25
MAX_WAIT_MS = 10_000
DEMAND_WINDOW_SECONDS = 900
SWEEP_HORIZON_SECONDS = 360
SWEEP_MAX_SCOPES = 200
KEEP_PER_TYPE = 6
PRUNE_GRACE_SECONDS = 300

KEY_PREFIX = "oacp:fresh:"
DEMAND_KEY = KEY_PREFIX + "demand"

# Refresh outcomes (api.v1.commerce_runtime.refresh_scope_artifacts).
REFRESHED = "refreshed"
REFUSED = "refused"
NOT_REFRESHABLE = "not_refreshable"
FAILED = "failed"
_BACKOFF_SECONDS: dict[str, int] = {
    REFUSED: REFUSAL_BACKOFF_SECONDS,
    NOT_REFRESHABLE: NOT_REFRESHABLE_BACKOFF_SECONDS,
    FAILED: RETRY_BACKOFF_SECONDS,
}

# enterprise-gate: process-local-ok reason=in-flight-task-references-only-lock-interval-backoff-in-redis
_INFLIGHT: set[asyncio.Task[str]] = set()


@dataclass(frozen=True)
class Scope:
    tenant_id: str
    merchant_id: str
    seller_agent_id: str

    @property
    def digest(self) -> str:
        raw = f"{self.tenant_id}|{self.merchant_id}|{self.seller_agent_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def key(self, kind: str) -> str:
        return f"{KEY_PREFIX}{kind}:{self.digest}"

    @property
    def member(self) -> str:
        return f"{self.tenant_id}|{self.merchant_id}|{self.seller_agent_id}"

    @classmethod
    def from_member(cls, member: str) -> Scope | None:
        parts = member.split("|")
        return cls(*parts) if len(parts) == 3 and all(parts) else None


Refresher = Callable[[Scope], Awaitable[str]]


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def on_demand_enabled() -> bool:
    from core.config import settings

    return bool(getattr(settings, "oacp_on_demand_refresh_enabled", False))


def sweep_enabled() -> bool:
    from core.config import settings

    return on_demand_enabled() and bool(getattr(settings, "oacp_refresh_sweep_enabled", False))


def _wait_seconds() -> float:
    from core.config import settings

    wait_ms = int(getattr(settings, "oacp_refresh_wait_ms", 3000) or 0)
    return max(0, min(wait_ms, MAX_WAIT_MS)) / 1000.0


def missing_types(
    records: Sequence[Any], required: Sequence[str], *, now: datetime, margin_seconds: int = REFRESH_MARGIN_SECONDS
) -> tuple[str, ...]:
    """Required types with no verified, buyer-agnostic record usable now and still valid ``margin_seconds`` on."""
    now_iso = _iso(now)
    horizon = now + timedelta(seconds=margin_seconds)
    missing = []
    for artifact_type in required:
        usable = usable_artifact_ids(cache_records=records, now_iso=now_iso, artifact_type=artifact_type)
        if not any(
            record.artifact_id in usable and record.artifact_type == artifact_type and _expires(record) > horizon
            for record in records
        ):
            missing.append(artifact_type)
    return tuple(missing)


def _expires(record: Any) -> datetime:
    value = str(getattr(record, "expires_at", "") or "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def default_refresher(scope: Scope) -> Awaitable[str]:
    from api.v1 import commerce_runtime

    return commerce_runtime.refresh_scope_artifacts(
        tenant_id=scope.tenant_id, merchant_id=scope.merchant_id, seller_agent_id=scope.seller_agent_id
    )


# Tests replace this; production uses the commerce runtime's refresh.
refresher: Refresher = default_refresher


async def _redis() -> Any:
    from core.async_redis import get_async_redis

    return await get_async_redis()


async def ensure_fresh(
    *,
    tenant_id: str,
    merchant_id: str | None,
    seller_agent_id: str | None,
    records: Sequence[Any],
    required: Sequence[str],
    now: datetime | None = None,
) -> bool:
    """Refresh the scope once if a required artifact is expired or expiring; True when the caller should re-read.

    Never raises for a refresh problem: the read continues with what is cached
    and the existing refusal applies.
    """
    if not on_demand_enabled() or not merchant_id or not seller_agent_id:
        return False
    scope = Scope(str(tenant_id), str(merchant_id), str(seller_agent_id))
    current = now or datetime.now(UTC)
    missing = missing_types(records, required, now=current)
    try:
        client = await _redis()
        if client is None:
            if missing:
                logger.warning("oacp_refresh_skipped", reason="lock_store_unavailable")
            return False
        if sweep_enabled():
            await client.zadd(DEMAND_KEY, {scope.member: time.time()})
        if not missing:
            return False
        return await _refresh_once(client, scope, wait_seconds=_wait_seconds(), reason="on_demand")
    # enterprise-gate: broad-except-ok reason=refresh-problem-never-fails-the-read-cached-refusal-stands
    except Exception as exc:
        logger.warning("oacp_refresh_error", error_type=type(exc).__name__)
        return False


async def _refresh_once(client: Any, scope: Scope, *, wait_seconds: float, reason: str) -> bool:
    if await client.exists(scope.key("backoff"), scope.key("recent")):
        return False
    token = uuid.uuid4().hex
    if await client.set(scope.key("lock"), token, nx=True, ex=LOCK_TTL_SECONDS):
        # A fresh context: the refresh is the system's own work, not the reading request's.
        task = asyncio.get_running_loop().create_task(
            _refresh_and_release(client, scope, token, reason), context=contextvars.Context()
        )
        _INFLIGHT.add(task)
        task.add_done_callback(_INFLIGHT.discard)
        done, _pending = await asyncio.wait({task}, timeout=wait_seconds)
        return task in done and not task.cancelled() and task.exception() is None and task.result() == REFRESHED
    # Another read or replica holds the lock: wait for it within the same budget, then re-read.
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        await asyncio.sleep(LOCK_POLL_SECONDS)
        if not await client.exists(scope.key("lock")):
            return True
    return False


async def _refresh_and_release(client: Any, scope: Scope, token: str, reason: str) -> str:
    from core.commerce.ledger import close_request_scope, open_request_scope

    _scope, ledger_token = open_request_scope(
        declared_event=None, actor_type="system", actor_id="oacp_artifact_refresh"
    )
    outcome = FAILED
    try:
        outcome = await asyncio.wait_for(refresher(scope), timeout=LOCK_TTL_SECONDS - 5)
    except TimeoutError:
        outcome = FAILED
    # enterprise-gate: broad-except-ok reason=refresh-failure-backed-off-reads-keep-fail-closed-refusal
    except Exception as exc:
        logger.warning("oacp_refresh_failed", reason=reason, error_type=type(exc).__name__)
        outcome = FAILED
    finally:
        close_request_scope(ledger_token)
    try:
        if outcome == REFRESHED:
            await client.set(scope.key("recent"), "1", ex=MIN_REFRESH_INTERVAL_SECONDS)
            await prune_scope(scope)
        else:
            await client.set(scope.key("backoff"), outcome, ex=_BACKOFF_SECONDS.get(outcome, RETRY_BACKOFF_SECONDS))
        if await client.get(scope.key("lock")) == token:
            await client.delete(scope.key("lock"))
    # enterprise-gate: broad-except-ok reason=lock-and-backoff-keys-expire-on-their-ttl
    except Exception as exc:
        logger.warning("oacp_refresh_bookkeeping_failed", error_type=type(exc).__name__)
    logger.info("oacp_refresh_finished", reason=reason, outcome=outcome)
    return outcome


async def prune_scope(scope: Scope, *, now: datetime | None = None) -> int:
    """Keep the newest ``KEEP_PER_TYPE`` records per type; drop records expired beyond the grace."""
    from sqlalchemy import delete, select

    from core.database import get_tenant_session
    from core.models.oacp_artifact_cache import OacpArtifactCacheRecordRow as Row

    current = now or datetime.now(UTC)
    async with get_tenant_session(uuid.UUID(scope.tenant_id)) as session:
        rows = (
            await session.execute(
                select(Row.cache_record_id, Row.artifact_type, Row.issued_at, Row.expires_at)
                .where(
                    Row.tenant_id == scope.tenant_id,
                    Row.merchant_id == scope.merchant_id,
                    Row.seller_agent_id == scope.seller_agent_id,
                    Row.buyer_agent_id.is_(None),
                )
                .order_by(Row.artifact_type, Row.issued_at.desc())
            )
        ).all()
        doomed = prune_ids(rows, now=current)
        if doomed:
            await session.execute(delete(Row).where(Row.tenant_id == scope.tenant_id, Row.cache_record_id.in_(doomed)))
    return len(doomed)


def prune_ids(rows: Sequence[Any], *, now: datetime) -> list[str]:
    """Pure: the cache record ids to delete from ``(id, type, issued_at, expires_at)`` rows."""
    cutoff = now - timedelta(seconds=PRUNE_GRACE_SECONDS)
    kept: dict[str, int] = {}
    doomed: list[str] = []
    for record_id, artifact_type, _issued_at, expires_at in sorted(
        rows, key=lambda row: (str(row[1]), row[2]), reverse=True
    ):
        expires = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        kept[artifact_type] = kept.get(artifact_type, 0) + 1
        if expires < cutoff or kept[artifact_type] > KEEP_PER_TYPE:
            doomed.append(str(record_id))
    return doomed


async def sweep(*, now: datetime | None = None) -> dict[str, int]:
    """Refresh recently read scopes whose evidence-bound or discovery artifacts expire before the next sweep."""
    if not sweep_enabled():
        return {"enabled": 0, "scopes": 0, "refreshed": 0}
    client = await _redis()
    if client is None:
        return {"enabled": 1, "scopes": 0, "refreshed": 0}
    from core.commerce.ucp.store import load_shared_cache_records

    current = now or datetime.now(UTC)
    since = current.timestamp() - DEMAND_WINDOW_SECONDS
    await client.zremrangebyscore(DEMAND_KEY, "-inf", since)
    members = await client.zrangebyscore(DEMAND_KEY, since, "+inf", start=0, num=SWEEP_MAX_SCOPES)
    refreshed = 0
    for member in members:
        scope = Scope.from_member(str(member))
        if scope is None:
            continue
        records = await load_shared_cache_records(
            tenant_id=scope.tenant_id, merchant_id=scope.merchant_id, seller_agent_id=scope.seller_agent_id
        )
        # Inventory lapses within a minute anyway; the sweep keeps the slower facts and the evidence current.
        slow = tuple(t for t in HOST_TYPES if t not in {"inventory", "mandate_capability"})
        if not missing_types(records, slow, now=current, margin_seconds=SWEEP_HORIZON_SECONDS):
            continue
        if await _refresh_once(client, scope, wait_seconds=LOCK_TTL_SECONDS, reason="sweep"):
            refreshed += 1
    return {"enabled": 1, "scopes": len(members), "refreshed": refreshed}
