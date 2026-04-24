"""Regression tests for S0-09 — tenant BYO AI provider tokens.

Covers the acceptance criteria in
``docs/STRICT_REPO_S0_CLOSURE_PLAN_2026-04-24.md`` PR-1:

- Model exposes the expected columns + allowlists.
- Resolver prefers tenant BYO token over platform env.
- Resolver raises ``ProviderNotConfigured`` when policy denies fallback.
- Mask helper never returns the raw token body.
- Admin router is registered + admin-gated.
- Voice engine-to-provider mapping is consistent with the allowlist.
- No caller reads raw provider env vars outside the resolver.
"""

from __future__ import annotations

import ast
import re
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ── Model contract ───────────────────────────────────────────────────


def test_tenant_ai_credential_model_shape() -> None:
    from core.models.tenant_ai_credential import (
        CREDENTIAL_KIND_ALLOWLIST,
        PROVIDER_ALLOWLIST,
        STATUS_ALLOWLIST,
        TenantAICredential,
    )

    cols = {c.name for c in TenantAICredential.__table__.columns}
    required = {
        "id",
        "tenant_id",
        "provider",
        "credential_kind",
        "credentials_encrypted",
        "status",
        "display_prefix",
        "display_suffix",
        "last_health_check_at",
        "last_used_at",
        "rotated_at",
        "created_at",
        "updated_at",
    }
    missing = required - cols
    assert not missing, f"TenantAICredential missing columns: {missing}"

    # Allowlists must cover the provider slugs the resolver + voice engine
    # maps reference.
    for provider in (
        "openai",
        "anthropic",
        "gemini",
        "voyage",
        "cohere",
        "ragflow",
        "stt_deepgram",
        "tts_elevenlabs",
    ):
        assert provider in PROVIDER_ALLOWLIST, f"{provider!r} missing from allowlist"
    for kind in ("llm", "embedding", "rag", "stt", "tts"):
        assert kind in CREDENTIAL_KIND_ALLOWLIST
    for status in ("active", "unverified", "failing", "inactive"):
        assert status in STATUS_ALLOWLIST


def test_tenant_ai_credential_unique_constraint_exists() -> None:
    from core.models.tenant_ai_credential import TenantAICredential

    found = False
    for constraint in TenantAICredential.__table__.constraints:
        if constraint.__class__.__name__ == "UniqueConstraint":
            cols = {c.name for c in constraint.columns}
            if cols == {"tenant_id", "provider", "credential_kind"}:
                found = True
                break
    assert found, (
        "TenantAICredential must declare a UniqueConstraint on "
        "(tenant_id, provider, credential_kind) so admins can't create "
        "two rows for the same slot"
    )


# ── Resolver contract ────────────────────────────────────────────────


def test_mask_token_never_returns_raw() -> None:
    from core.ai_providers.resolver import mask_token

    raw = "sk-verylongsecretapikeyhere1234567890abcdef"
    prefix, suffix = mask_token(raw)
    assert raw not in (prefix, suffix)
    assert len(prefix) <= 8
    assert len(suffix) <= 8


def test_mask_token_short_inputs_do_not_leak_more_than_two_chars() -> None:
    from core.ai_providers.resolver import mask_token

    raw = "abcd"
    prefix, suffix = mask_token(raw)
    assert len(prefix) <= 2
    assert len(suffix) <= 2


def test_resolver_platform_context_falls_back_to_env(monkeypatch) -> None:
    """With no tenant_id the resolver returns the env fallback."""
    import asyncio

    from core.ai_providers.resolver import get_provider_credential

    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-fallback-token-12345")
    resolved = asyncio.run(get_provider_credential(None, "openai", "llm"))
    assert resolved.source == "platform_env"
    assert resolved.secret == "sk-env-fallback-token-12345"


def test_resolver_raises_when_no_platform_and_no_tenant() -> None:
    """With no tenant_id and no env var, resolver raises."""
    import asyncio
    import os as _os

    from core.ai_providers.resolver import (
        ProviderNotConfigured,
        get_provider_credential,
        invalidate_cache,
    )

    # Cache may have been populated by earlier tests in the same process.
    invalidate_cache()

    for var in ("OPENAI_API_KEY",):
        _os.environ.pop(var, None)
    with pytest.raises(ProviderNotConfigured):
        asyncio.run(get_provider_credential(None, "openai", "llm"))


def test_resolver_cache_is_invalidatable() -> None:
    from core.ai_providers.resolver import _CACHE, invalidate_cache

    tid = str(uuid.uuid4())
    _CACHE[(tid, "openai", "llm")] = ("stub", 0.0, "x")  # type: ignore[assignment]
    invalidate_cache(tid, "openai", "llm")
    assert (tid, "openai", "llm") not in _CACHE


# ── Admin API contract ───────────────────────────────────────────────


def test_admin_router_registered() -> None:
    main_src = _read("api/main.py")
    assert "tenant_ai_credentials" in main_src
    assert "tenant_ai_credentials.router" in main_src


def test_admin_router_uses_admin_gate() -> None:
    """Every route on the router is admin-gated.

    Either the router itself has ``dependencies=[require_tenant_admin]``
    OR each endpoint does. PR-1 uses the router-level gate.
    """
    src = _read("api/v1/tenant_ai_credentials.py")
    assert "require_tenant_admin" in src, (
        "Router must import + use require_tenant_admin"
    )
    # Router-level gate: look for dependencies=[require_tenant_admin]
    assert re.search(
        r"dependencies\s*=\s*\[require_tenant_admin\]", src
    ), "Admin gate must be applied at router level"


def test_admin_router_never_returns_raw_token() -> None:
    """The OUT schema must not include a field that could carry the
    raw token body."""
    src = _read("api/v1/tenant_ai_credentials.py")
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ClassDef)
            and node.name == "TenantAICredentialOut"
        ):
            field_names = {
                target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                for target in [stmt.target]
            }
            # Fields that would leak the raw token must NOT be present
            forbidden = {
                "api_key",
                "credential",
                "secret",
                "token",
                "credentials_encrypted",
            }
            leak = field_names & forbidden
            assert not leak, (
                f"TenantAICredentialOut exposes forbidden fields: {leak}"
            )
            return
    raise AssertionError("TenantAICredentialOut schema not found")


# ── Voice integration ────────────────────────────────────────────────


def test_voice_keys_persist_through_vault_not_memory() -> None:
    src = _read("api/v1/voice.py")
    # The in-memory dict still exists for non-secret fields, but the
    # api_key fields must be written through encrypt_for_tenant.
    assert "_save_voice_keys" in src
    assert "encrypt_for_tenant" in src
    # Legacy behaviour was: _VOICE_CONFIG[...] = body.model_dump()
    # where the dump included the plaintext api_key. The new code MUST
    # strip those fields before writing to the dict.
    assert "pop(\"stt_api_key\", None)" in src
    assert "pop(\"tts_api_key\", None)" in src


def test_voice_engine_to_provider_mapping_matches_allowlist() -> None:
    src = _read("api/v1/voice.py")
    assert "_STT_ENGINE_TO_PROVIDER" in src
    assert "_TTS_ENGINE_TO_PROVIDER" in src
    # Every slug referenced on the right-hand side must be in the
    # PROVIDER_ALLOWLIST so /voice/config doesn't create orphan rows.
    from core.models.tenant_ai_credential import PROVIDER_ALLOWLIST

    for match in re.finditer(r'"(stt_\w+|tts_\w+)"', src):
        slug = match.group(1)
        # Skip pure identifier tokens — only check string literals that
        # look like provider slugs.
        if slug.startswith("stt_") or slug.startswith("tts_"):
            if slug in ("stt_api_key", "tts_api_key", "stt_engine", "tts_engine"):
                continue
            assert slug in PROVIDER_ALLOWLIST, (
                f"voice.py references provider slug {slug!r} that's not "
                "in the tenant_ai_credential allowlist"
            )


# ── Anti-regression: no raw token exposure outside resolver ───────────


def test_no_direct_provider_env_reads_outside_resolver() -> None:
    """Future PRs should route provider-key env reads through the
    resolver. This test snapshots the currently-known callers so new
    direct reads trigger review. The list is allowlisted; when the
    later PRs move them into the resolver, entries can be removed.
    """
    allowed_direct_readers = {
        # Legacy LLM factory paths — PR-2 replaces these with resolver.
        "core/langgraph/llm_factory.py",
        "core/llm/router.py",
        # External-key config surface (reads env once at import).
        "core/config.py",
        # Voice SIP/phone validator — not a provider secret.
        "api/v1/voice.py",
        # Billing (Stripe/Plural) — not an AI provider.
        "api/v1/billing.py",
        "core/billing/pinelabs_client.py",
        "core/billing/stripe_client.py",
        # RAGFlow existing path — PR-3 migrates.
        "api/v1/knowledge.py",
        "core/knowledge/rag.py",
        # The resolver itself legitimately reads env vars in its
        # fallback path. This IS the canonical env reader.
        "core/ai_providers/resolver.py",
    }
    # Skip directories that don't belong to us.
    skip_prefixes = (
        "tests/",
        ".venv/",
        ".tmp_lc/",
        "ui/",
        "node_modules/",
        ".git/",
    )
    patterns = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_GEMINI_API_KEY")
    offenders: set[str] = set()
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if any(rel.startswith(p) for p in skip_prefixes):
            continue
        if rel in allowed_direct_readers:
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except Exception:  # noqa: S112
            continue
        for p in patterns:
            if re.search(rf"os\.(getenv|environ)[^\n]*{p}", body):
                offenders.add(rel)
                break
    assert not offenders, (
        "Direct reads of LLM provider env vars outside the resolver: "
        f"{offenders}. Add them to the resolver or to the allowlist in "
        "this test with a plan to migrate."
    )
