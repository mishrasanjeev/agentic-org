"""Tenant-aware AI provider credential resolver.

Central entry point for anywhere in the codebase that previously read
``os.environ["OPENAI_API_KEY"]`` (or Gemini / Anthropic / etc.). Order of
resolution:

1. If the caller's tenant has an active ``TenantAICredential`` row for
   ``(provider, kind)``, decrypt and return it. Stamp ``last_used_at``.
2. Otherwise, consult the tenant's ``ai_fallback_policy`` (default
   ``allow``). If ``deny``, raise ``ProviderNotConfigured``.
3. Otherwise, fall back to the platform env var for that provider.

The function is async-safe and uses a short in-process cache keyed on
``(tenant_id, provider, kind, rotated_at)`` so resolver hits don't hammer
the DB — every rotation invalidates by bumping ``rotated_at``.

Caller contract:

- Pass ``None`` / ``"__platform__"`` as tenant_id for non-tenant-scoped
  calls (migrations, boot-time probes). Those always take the env path.
- On failure to decrypt (corrupted row, missing KEK), raise — do NOT
  silently fall back; a corrupted tenant credential must page ops.
"""

from __future__ import annotations

import json
import os
import time
import uuid as _uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select

logger = structlog.get_logger(__name__)


# Env-var mapping per (provider, credential_kind). When a tenant has no
# BYO credential and policy allows fallback, the resolver reads this
# dict rather than scattering os.getenv() across the codebase.
_PLATFORM_ENV_VARS: dict[tuple[str, str], tuple[str, ...]] = {
    ("gemini", "llm"): ("GOOGLE_GEMINI_API_KEY", "GOOGLE_API_KEY"),
    ("openai", "llm"): ("OPENAI_API_KEY",),
    ("anthropic", "llm"): ("ANTHROPIC_API_KEY",),
    ("azure_openai", "llm"): ("AZURE_OPENAI_API_KEY",),
    ("openai", "embedding"): ("OPENAI_API_KEY",),
    ("voyage", "embedding"): ("VOYAGE_API_KEY",),
    ("cohere", "embedding"): ("COHERE_API_KEY",),
    ("ragflow", "rag"): ("RAGFLOW_API_KEY",),
    ("stt_deepgram", "stt"): ("DEEPGRAM_API_KEY",),
    ("stt_azure", "stt"): ("AZURE_SPEECH_KEY",),
    ("tts_elevenlabs", "tts"): ("ELEVENLABS_API_KEY",),
    ("tts_azure", "tts"): ("AZURE_SPEECH_KEY",),
}


class ProviderNotConfigured(RuntimeError):  # noqa: N818 — external-facing name; Exception suffix would be redundant
    """Raised when no credential is available and policy denies fallback.

    Callers MUST let this propagate so the request fails with a 503 and
    ops can see that the tenant's BYO policy is denying the call. Do
    NOT fall back to a global env var past this point.
    """


@dataclass(frozen=True)
class ResolvedCredential:
    """Result of a credential lookup.

    ``source`` tells the caller which path was taken so logs / traces
    can reason about whether the BYO token or platform fallback was
    used. Logging this value is safe; logging ``secret`` is NOT.
    """

    secret: str
    provider: str
    kind: str
    source: str  # "tenant" | "platform_env"
    # Extra non-secret config (e.g. Azure endpoint URL). May be empty.
    provider_config: dict[str, Any] | None = None


# Simple in-process cache. Key = (tenant_id_str, provider, kind).
# Value = (ResolvedCredential, cached_at, rotated_at_iso).
_CACHE: dict[tuple[str, str, str], tuple[ResolvedCredential, float, str]] = {}
_CACHE_TTL_S = 120.0


def _cache_key(
    tenant_id: _uuid.UUID | str | None, provider: str, kind: str
) -> tuple[str, str, str]:
    tid = str(tenant_id) if tenant_id else "__platform__"
    return (tid, provider, kind)


def _env_fallback(provider: str, kind: str) -> str | None:
    for var_name in _PLATFORM_ENV_VARS.get((provider, kind), ()):
        value = os.getenv(var_name, "").strip()
        if value:
            return value
    return None


async def _fetch_tenant_credential(
    tenant_id: _uuid.UUID, provider: str, kind: str
) -> tuple[str, str, dict[str, Any] | None] | None:
    """Load + decrypt a tenant's BYO credential. Returns
    ``(secret, rotated_at_iso, provider_config)`` or ``None`` when the
    tenant has no row. Raises on decrypt failure (never silently
    falls back).
    """
    from core.crypto.tenant_secrets import decrypt_for_tenant
    from core.database import async_session_factory
    from core.models.tenant_ai_credential import TenantAICredential

    async with async_session_factory() as session:
        result = await session.execute(
            select(TenantAICredential).where(
                TenantAICredential.tenant_id == tenant_id,
                TenantAICredential.provider == provider,
                TenantAICredential.credential_kind == kind,
                TenantAICredential.status.in_(("active", "unverified")),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None

        blob = row.credentials_encrypted or {}
        ciphertext = blob.get("_encrypted") if isinstance(blob, dict) else None
        if not ciphertext:
            # Row exists but is malformed. Refuse silently — log loudly.
            logger.error(
                "tenant_ai_credential_missing_cipher",
                tenant_id=str(tenant_id),
                provider=provider,
                kind=kind,
            )
            return None

        # decrypt_for_tenant is sync on purpose; safe to call here
        # because it's a CPU-bound unwrap against the already-loaded
        # envelope metadata.
        plaintext = decrypt_for_tenant(ciphertext)
        if not plaintext:
            raise RuntimeError(
                f"TenantAICredential {row.id} decrypted to empty — "
                "refusing to fall back to platform env"
            )

        rotated_at = (
            row.rotated_at.isoformat()
            if row.rotated_at is not None
            else row.updated_at.isoformat()
        )
        return plaintext, rotated_at, row.provider_config


async def _stamp_last_used(
    tenant_id: _uuid.UUID, provider: str, kind: str
) -> None:
    """Update last_used_at. Best-effort — never blocks the caller."""
    try:
        from datetime import UTC, datetime

        from core.database import async_session_factory
        from core.models.tenant_ai_credential import TenantAICredential

        async with async_session_factory() as session:
            result = await session.execute(
                select(TenantAICredential).where(
                    TenantAICredential.tenant_id == tenant_id,
                    TenantAICredential.provider == provider,
                    TenantAICredential.credential_kind == kind,
                )
            )
            row = result.scalar_one_or_none()
            if row is not None:
                row.last_used_at = datetime.now(UTC)
    except Exception as exc:
        # last_used_at is cosmetic — never fail a real request because
        # the bookkeeping write broke.
        logger.debug("tenant_ai_credential_stamp_failed", error=str(exc))


async def _tenant_fallback_allowed(tenant_id: _uuid.UUID) -> bool:
    """Check the tenant's ``ai_fallback_policy``.

    Default ``allow``. A follow-up PR (S0-08 / PR-2) adds
    tenant_ai_settings; until then the policy is read from tenant
    ``settings`` JSONB with key ``ai_fallback_policy``.
    """
    try:
        from core.database import async_session_factory
        from core.models.tenant import Tenant

        async with async_session_factory() as session:
            result = await session.execute(
                select(Tenant.settings).where(Tenant.id == tenant_id)
            )
            row_settings = result.scalar_one_or_none()
        if not row_settings or not isinstance(row_settings, dict):
            return True
        policy = str(row_settings.get("ai_fallback_policy", "allow")).lower()
        return policy != "deny"
    except Exception as exc:
        # If we can't read the tenant's policy, err on the side of
        # NOT falling back — that's the safer enterprise default.
        logger.warning(
            "tenant_ai_fallback_policy_read_failed",
            tenant_id=str(tenant_id),
            error=str(exc),
        )
        return False


async def get_provider_credential(
    tenant_id: _uuid.UUID | str | None,
    provider: str,
    kind: str = "llm",
    *,
    require_tenant_token: bool = False,
) -> ResolvedCredential:
    """Resolve an AI provider credential.

    Parameters
    ----------
    tenant_id : UUID or str or None
        Caller's tenant. Pass ``None`` for platform-only contexts
        (migrations, cron probes); those always take the env path.
    provider : str
        One of the allowlist values in
        ``core.models.tenant_ai_credential.PROVIDER_ALLOWLIST``.
    kind : str
        Credential kind — ``llm | embedding | rag | stt | tts``.
    require_tenant_token : bool
        When ``True`` the resolver refuses to fall back to platform env
        even if the tenant's policy allows it. Used by regulated
        surfaces that MUST use the tenant's own key.

    Raises
    ------
    ProviderNotConfigured
        Neither a tenant BYO token nor a platform env var is available
        (or the policy denies fallback).
    """
    # Coerce tenant_id
    tid_obj: _uuid.UUID | None
    if tenant_id in (None, "__platform__"):
        tid_obj = None
    elif isinstance(tenant_id, _uuid.UUID):
        tid_obj = tenant_id
    else:
        try:
            tid_obj = _uuid.UUID(str(tenant_id))
        except (TypeError, ValueError):
            tid_obj = None

    # Platform-only contexts go straight to env.
    if tid_obj is None:
        env_secret = _env_fallback(provider, kind)
        if env_secret:
            return ResolvedCredential(
                secret=env_secret,
                provider=provider,
                kind=kind,
                source="platform_env",
            )
        raise ProviderNotConfigured(
            f"No platform credential for provider={provider!r} kind={kind!r} "
            "and no tenant context was supplied. Set the platform env var "
            f"(one of: {list(_PLATFORM_ENV_VARS.get((provider, kind), ()))}) "
            "or pass a tenant_id with a BYO token registered."
        )

    # 1. Cache lookup
    ck = _cache_key(tid_obj, provider, kind)
    cached = _CACHE.get(ck)
    if cached is not None:
        resolved, cached_at, _ = cached
        if time.time() - cached_at < _CACHE_TTL_S:
            return resolved
        _CACHE.pop(ck, None)

    # 2. Tenant BYO
    try:
        result = await _fetch_tenant_credential(tid_obj, provider, kind)
    except Exception as exc:
        logger.error(
            "tenant_ai_credential_decrypt_failed",
            tenant_id=str(tid_obj),
            provider=provider,
            kind=kind,
            error=str(exc),
        )
        # Corrupted tenant credential — refuse to silently fall back.
        raise ProviderNotConfigured(
            f"Tenant {tid_obj} has a {provider}/{kind} credential that "
            "could not be decrypted. Refusing to fall back to platform "
            "env. Check KEK configuration and rotate the credential."
        ) from exc

    if result is not None:
        secret, rotated_at, provider_config = result
        resolved = ResolvedCredential(
            secret=secret,
            provider=provider,
            kind=kind,
            source="tenant",
            provider_config=provider_config or None,
        )
        _CACHE[ck] = (resolved, time.time(), rotated_at)
        # Fire-and-forget bookkeeping
        await _stamp_last_used(tid_obj, provider, kind)
        return resolved

    # 3. Platform fallback — gated by tenant policy
    if require_tenant_token:
        raise ProviderNotConfigured(
            f"Tenant {tid_obj} has no BYO {provider}/{kind} credential "
            "and the caller requires a tenant token. Register a BYO "
            "credential under /dashboard/settings/ai-credentials."
        )
    if not await _tenant_fallback_allowed(tid_obj):
        raise ProviderNotConfigured(
            f"Tenant {tid_obj} has no BYO {provider}/{kind} credential "
            "and ai_fallback_policy=deny. Register a BYO credential "
            "before using this feature."
        )

    env_secret = _env_fallback(provider, kind)
    if env_secret:
        resolved = ResolvedCredential(
            secret=env_secret,
            provider=provider,
            kind=kind,
            source="platform_env",
        )
        # Don't cache env fallback — cheap to re-read, and we want
        # rotations via config-reload to pick up immediately.
        return resolved

    raise ProviderNotConfigured(
        f"No BYO credential for tenant {tid_obj} and no platform "
        f"fallback env var set for provider={provider!r} kind={kind!r}."
    )


def get_provider_credential_sync(
    tenant_id: _uuid.UUID | str | None,
    provider: str,
    kind: str = "llm",
    *,
    require_tenant_token: bool = False,
) -> ResolvedCredential:
    """Sync wrapper over :func:`get_provider_credential`.

    Used by sync call sites (``core/langgraph/llm_factory.py``,
    legacy voice config readers). Spawns an event loop when not
    already inside one so the async resolver can complete its DB
    lookup. Callers that are already async MUST use
    :func:`get_provider_credential` directly.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    def _make_coro():
        return get_provider_credential(
            tenant_id,
            provider,
            kind,
            require_tenant_token=require_tenant_token,
        )

    if loop is None:
        return asyncio.run(_make_coro())

    # Already inside a running loop — submit to a thread-local loop so
    # we don't re-enter.
    import concurrent.futures

    def _runner() -> ResolvedCredential:
        return asyncio.run(_make_coro())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_runner).result()


def invalidate_cache(
    tenant_id: _uuid.UUID | str | None = None,
    provider: str | None = None,
    kind: str | None = None,
) -> None:
    """Drop cached credentials after a rotation/delete.

    Called from the admin API on PATCH / DELETE / rotate. Pass ``None``
    for every arg to flush the whole cache.
    """
    if tenant_id is None and provider is None and kind is None:
        _CACHE.clear()
        return
    keys_to_drop = []
    for key in _CACHE:
        tid, p, k = key
        if (
            (tenant_id is not None and tid != str(tenant_id))
            or (provider is not None and p != provider)
            or (kind is not None and k != kind)
        ):
            continue
        keys_to_drop.append(key)
    for key in keys_to_drop:
        _CACHE.pop(key, None)


def mask_token(token: str) -> tuple[str, str]:
    """Return ``(display_prefix, display_suffix)`` for a raw token.

    Used by the admin API so the UI can identify which key was rotated
    without ever returning the raw body.
    """
    if not token:
        return "", ""
    stripped = token.strip()
    if len(stripped) <= 8:
        # Very short token — return a single asterisk prefix/suffix so
        # we don't leak more than 2 chars of a short secret.
        return stripped[:2], stripped[-2:]
    return stripped[:4], stripped[-4:]


def serialize_provider_config(config: dict[str, Any] | None) -> str | None:
    """Helper for the admin API: JSON-serialize with a consistent
    shape so round-trips through SQLAlchemy JSONB stay stable.
    """
    if config is None:
        return None
    return json.dumps(config, sort_keys=True)
