"""Tenant AI setting lookup helpers.

Thin query-layer shims so the rest of the codebase doesn't reach into
``TenantAISetting`` directly. Cached with a short TTL like the
resolver so admin PUTs propagate without restart.
"""

from __future__ import annotations

import time
import uuid as _uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select

logger = structlog.get_logger(__name__)

# enterprise-gate: process-local-ok reason=bounded-ttl-db-backed-ai-setting-cache
_CACHE: dict[str, tuple[EffectiveAISetting, float]] = {}
_CACHE_TTL_S = 60.0
_CACHE_MAX_SIZE = 1_024


@dataclass(frozen=True)
class EffectiveAISetting:
    """Resolved AI setting for a tenant.

    Fields fall through to the platform defaults when the tenant has
    no row. ``source`` is ``"tenant"`` when any field came from the
    tenant row, else ``"platform_default"``.
    """

    tenant_id: str
    llm_provider: str | None
    llm_model: str | None
    llm_fallback_model: str | None
    llm_routing_policy: str
    max_input_tokens: int | None
    embedding_provider: str | None
    embedding_model: str | None
    embedding_dimensions: int | None
    chunk_size: int | None
    chunk_overlap: int | None
    ai_fallback_policy: str
    source: str


_PLATFORM_DEFAULTS = EffectiveAISetting(
    tenant_id="__platform__",
    llm_provider="gemini",
    llm_model="gemini-2.5-flash",
    llm_fallback_model="gemini-2.5-flash-preview-05-20",
    llm_routing_policy="auto",
    max_input_tokens=None,
    embedding_provider="local",
    embedding_model="BAAI/bge-small-en-v1.5",
    embedding_dimensions=384,
    chunk_size=512,
    chunk_overlap=50,
    ai_fallback_policy="allow",
    source="platform_default",
)


def _store_cache(key: str, effective: EffectiveAISetting) -> None:
    now = time.time()
    if key not in _CACHE and len(_CACHE) >= _CACHE_MAX_SIZE:
        expired = [
            cache_key
            for cache_key, (_, cached_at) in _CACHE.items()
            if now - cached_at >= _CACHE_TTL_S
        ]
        for cache_key in expired:
            _CACHE.pop(cache_key, None)
        while len(_CACHE) >= _CACHE_MAX_SIZE:
            oldest_key = min(_CACHE, key=lambda item: _CACHE[item][1])
            _CACHE.pop(oldest_key, None)
    _CACHE[key] = (effective, now)


async def get_effective_ai_setting(
    tenant_id: _uuid.UUID | str | None,
) -> EffectiveAISetting:
    """Return the effective AI setting for a tenant, with cache.

    Falls back to ``_PLATFORM_DEFAULTS`` when the tenant has no row.
    Any read failure (table missing during migration) is treated as
    "use defaults" and logged at DEBUG.
    """
    if tenant_id in (None, "__platform__"):
        return _PLATFORM_DEFAULTS
    try:
        tid = (
            tenant_id
            if isinstance(tenant_id, _uuid.UUID)
            else _uuid.UUID(str(tenant_id))
        )
    except (TypeError, ValueError):
        return _PLATFORM_DEFAULTS

    key = str(tid)
    cached = _CACHE.get(key)
    if cached is not None:
        effective, cached_at = cached
        if time.time() - cached_at < _CACHE_TTL_S:
            return effective

    try:
        from core.database import async_session_factory
        from core.models.tenant_ai_setting import TenantAISetting

        async with async_session_factory() as session:
            result = await session.execute(
                select(TenantAISetting).where(
                    TenantAISetting.tenant_id == tid
                )
            )
            row = result.scalar_one_or_none()
    # enterprise-gate: broad-except-ok reason=tenant-ai-settings-read-failure-returns-explicit-platform-defaults
    except Exception as exc:
        # Table may not exist yet during CI migration rehearsal. Honest
        # fallback: use platform defaults and log once.
        logger.debug(
            "tenant_ai_setting_read_failed",
            tenant_id=str(tid),
            error=str(exc),
        )
        return _PLATFORM_DEFAULTS

    if row is None:
        effective = EffectiveAISetting(
            tenant_id=str(tid),
            llm_provider=_PLATFORM_DEFAULTS.llm_provider,
            llm_model=_PLATFORM_DEFAULTS.llm_model,
            llm_fallback_model=_PLATFORM_DEFAULTS.llm_fallback_model,
            llm_routing_policy=_PLATFORM_DEFAULTS.llm_routing_policy,
            max_input_tokens=None,
            embedding_provider=_PLATFORM_DEFAULTS.embedding_provider,
            embedding_model=_PLATFORM_DEFAULTS.embedding_model,
            embedding_dimensions=_PLATFORM_DEFAULTS.embedding_dimensions,
            chunk_size=_PLATFORM_DEFAULTS.chunk_size,
            chunk_overlap=_PLATFORM_DEFAULTS.chunk_overlap,
            ai_fallback_policy="allow",
            source="platform_default",
        )
    else:
        effective = EffectiveAISetting(
            tenant_id=str(tid),
            llm_provider=row.llm_provider or _PLATFORM_DEFAULTS.llm_provider,
            llm_model=row.llm_model or _PLATFORM_DEFAULTS.llm_model,
            llm_fallback_model=row.llm_fallback_model or _PLATFORM_DEFAULTS.llm_fallback_model,
            llm_routing_policy=row.llm_routing_policy or "auto",
            max_input_tokens=row.max_input_tokens,
            embedding_provider=row.embedding_provider or _PLATFORM_DEFAULTS.embedding_provider,
            embedding_model=row.embedding_model or _PLATFORM_DEFAULTS.embedding_model,
            embedding_dimensions=row.embedding_dimensions or _PLATFORM_DEFAULTS.embedding_dimensions,
            chunk_size=row.chunk_size or _PLATFORM_DEFAULTS.chunk_size,
            chunk_overlap=row.chunk_overlap or _PLATFORM_DEFAULTS.chunk_overlap,
            ai_fallback_policy=row.ai_fallback_policy or "allow",
            source="tenant",
        )

    _store_cache(key, effective)
    return effective


def invalidate_tenant_ai_setting_cache(
    tenant_id: _uuid.UUID | str | None = None,
) -> None:
    """Drop cached setting(s) after a PUT."""
    if tenant_id is None:
        _CACHE.clear()
        return
    _CACHE.pop(str(tenant_id), None)
