from __future__ import annotations

import time


def test_bounded_feature_flag_cache_evicts_oldest(monkeypatch) -> None:
    from core import feature_flags

    feature_flags.clear_cache()
    monkeypatch.setattr(feature_flags, "_CACHE_MAX_SIZE", 2)
    expires_at = time.monotonic() + 600

    feature_flags._store_cache(("tenant", "a"), {"enabled": True}, expires_at)
    feature_flags._store_cache(("tenant", "b"), {"enabled": True}, expires_at)
    feature_flags._store_cache(("tenant", "c"), {"enabled": True}, expires_at)

    assert set(feature_flags._cache) == {("tenant", "b"), ("tenant", "c")}
    feature_flags.clear_cache()


def test_bounded_ai_setting_cache_evicts_oldest(monkeypatch) -> None:
    from core.ai_providers import settings as ai_settings

    ai_settings._CACHE.clear()
    monkeypatch.setattr(ai_settings, "_CACHE_MAX_SIZE", 2)

    ai_settings._store_cache("tenant-a", ai_settings._PLATFORM_DEFAULTS)
    ai_settings._store_cache("tenant-b", ai_settings._PLATFORM_DEFAULTS)
    ai_settings._store_cache("tenant-c", ai_settings._PLATFORM_DEFAULTS)

    assert set(ai_settings._CACHE) == {"tenant-b", "tenant-c"}
    ai_settings._CACHE.clear()


def test_bounded_ai_credential_cache_evicts_oldest(monkeypatch) -> None:
    from core.ai_providers import resolver

    resolver._CACHE.clear()
    monkeypatch.setattr(resolver, "_CACHE_MAX_SIZE", 2)
    credential = resolver.ResolvedCredential(
        secret="secret",
        provider="openai",
        kind="llm",
        source="tenant",
    )

    resolver._store_cache(("tenant-a", "openai", "llm"), credential, "r1")
    resolver._store_cache(("tenant-b", "openai", "llm"), credential, "r1")
    resolver._store_cache(("tenant-c", "openai", "llm"), credential, "r1")

    assert set(resolver._CACHE) == {
        ("tenant-b", "openai", "llm"),
        ("tenant-c", "openai", "llm"),
    }
    resolver._CACHE.clear()


def test_bounded_branding_cache_evicts_oldest(monkeypatch) -> None:
    from api.v1 import branding

    branding._clear_branding_cache()
    monkeypatch.setattr(branding, "_BRANDING_MAX_CACHE_ENTRIES", 2)
    expires_at = time.time() + 600

    branding._store_branding_cache("host-a", branding._DEFAULT_BRANDING, expires_at)
    branding._store_branding_cache("host-b", branding._DEFAULT_BRANDING, expires_at)
    branding._store_branding_cache("host-c", branding._DEFAULT_BRANDING, expires_at)

    assert set(branding._branding_cache) == {"host-b", "host-c"}
    branding._clear_branding_cache()
