"""Regression tests for S0-08 — tenant AI config (PR-2).

Covers acceptance criteria from
``docs/STRICT_REPO_S0_CLOSURE_PLAN_2026-04-24.md`` PR-2:

- tenant_ai_settings ORM + migration + allowlists
- Catalog covers required providers + embedding dimensions are pinned
- Admin API validates (provider, model) + embedding_dimensions
- Settings resolver falls back to platform defaults and caches
- llm_factory routes through the AI resolver (not bare env reads)
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ── Model contract ───────────────────────────────────────────────────


def test_tenant_ai_setting_model_columns() -> None:
    from core.models.tenant_ai_setting import (
        FALLBACK_POLICIES,
        ROUTING_POLICIES,
        TenantAISetting,
    )

    cols = {c.name for c in TenantAISetting.__table__.columns}
    required = {
        "tenant_id",
        "llm_provider",
        "llm_model",
        "llm_fallback_model",
        "llm_routing_policy",
        "max_input_tokens",
        "embedding_provider",
        "embedding_model",
        "embedding_dimensions",
        "chunk_size",
        "chunk_overlap",
        "ai_fallback_policy",
        "updated_by",
        "created_at",
        "updated_at",
    }
    missing = required - cols
    assert not missing, f"TenantAISetting missing columns: {missing}"
    assert "auto" in ROUTING_POLICIES
    assert "deny" in FALLBACK_POLICIES


# ── Catalog ──────────────────────────────────────────────────────────


def test_llm_catalog_covers_required_providers() -> None:
    from core.ai_providers.catalog import LLM_CATALOG, llm_providers

    providers = set(llm_providers())
    for required in ("gemini", "openai", "anthropic", "azure_openai", "openai_compatible"):
        assert required in providers, f"LLM catalog missing provider {required!r}"
    # Each registered model must have positive context window
    for entry in LLM_CATALOG:
        assert entry.context_window > 0, f"{entry.model} missing context_window"
        assert entry.max_output_tokens > 0, f"{entry.model} missing max_output_tokens"


def test_embedding_catalog_pins_dimensions() -> None:
    from core.ai_providers.catalog import EMBEDDING_CATALOG

    # Every entry must declare a positive dimension — that's the pin
    # that prevents dimension/index drift.
    for entry in EMBEDDING_CATALOG:
        assert entry.dimensions > 0
        assert entry.max_input_tokens > 0

    # Specific dimensions we depend on elsewhere in the code:
    from core.ai_providers.catalog import find_embedding

    bge_small = find_embedding("local", "BAAI/bge-small-en-v1.5")
    assert bge_small is not None and bge_small.dimensions == 384
    openai_3_small = find_embedding("openai", "text-embedding-3-small")
    assert openai_3_small is not None and openai_3_small.dimensions == 1536


def test_catalog_lookup_special_cases() -> None:
    from core.ai_providers.catalog import find_llm

    # Azure openai deployment names pass through prefix-match
    az = find_llm("azure_openai", "deployment:my-gpt-4o")
    assert az is not None
    # OpenAI-compatible wildcard accepts any model
    oc = find_llm("openai_compatible", "llama-3.1-70b-instruct")
    assert oc is not None
    # Unknown combination rejects
    assert find_llm("gemini", "gemini-10-nope") is None
    assert find_llm("openai", "gpt-100") is None


# ── Admin API ────────────────────────────────────────────────────────


def test_tenant_ai_settings_router_registered() -> None:
    main = _read("api/main.py")
    assert "tenant_ai_settings" in main
    assert "tenant_ai_settings.router" in main


def test_put_validates_catalog_and_dimensions() -> None:
    """Source-inspection pin: the PUT endpoint must call find_llm +
    find_embedding + enforce embedding_dimensions against catalog.
    """
    src = _read("api/v1/tenant_ai_settings.py")
    assert "find_llm(" in src
    assert "find_embedding(" in src
    assert "embedding_dimensions" in src
    assert "scripts/embedding_rotate.py" in src, (
        "PUT endpoint must mention the rotate script in the mismatch error "
        "so operators know the escape hatch"
    )
    # PUT must be admin-gated
    assert "require_tenant_admin" in src
    # The Out-schema must NOT expose credentials
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "TenantAISettingOut":
            fields = {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
            forbidden = {"api_key", "secret", "credentials", "credentials_encrypted"}
            assert not (fields & forbidden), (
                f"TenantAISettingOut exposes forbidden fields: {fields & forbidden}"
            )
            return


# ── Settings resolver ────────────────────────────────────────────────


def test_effective_setting_falls_back_to_platform_defaults() -> None:
    import asyncio

    from core.ai_providers.settings import (
        _PLATFORM_DEFAULTS,
        get_effective_ai_setting,
    )

    # No tenant context → platform defaults directly.
    result = asyncio.run(get_effective_ai_setting(None))
    assert result.source == "platform_default"
    assert result.llm_provider == _PLATFORM_DEFAULTS.llm_provider
    assert result.embedding_dimensions == _PLATFORM_DEFAULTS.embedding_dimensions


def test_effective_setting_cache_invalidates() -> None:
    from core.ai_providers.settings import _CACHE, invalidate_tenant_ai_setting_cache

    _CACHE["some-tenant"] = ("stub", 0.0)  # type: ignore[assignment]
    invalidate_tenant_ai_setting_cache("some-tenant")
    assert "some-tenant" not in _CACHE


# ── llm_factory integration ──────────────────────────────────────────


def test_llm_factory_routes_through_resolver() -> None:
    src = _read("core/langgraph/llm_factory.py")
    # The new helper must be present and used by _build_model.
    assert "_resolve_cloud_api_key" in src
    assert "get_provider_credential_sync" in src
    # None of the cloud branches should read platform env vars directly
    # (the resolver handles env fallback). Allow references to env vars
    # in code comments or docstrings.
    tree = ast.parse(src)

    def _is_env_read(node: ast.AST) -> bool:
        # os.getenv("OPENAI_API_KEY") or os.environ.get(...)
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute):
            # os.environ.get → Attribute(value=Attribute(value=Name('os'), attr='environ'), attr='get')
            if func.attr == "get" and isinstance(func.value, ast.Attribute) and func.value.attr == "environ":
                return True
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "os" and func.attr == "getenv":
                return True
        return False

    for node in ast.walk(tree):
        if _is_env_read(node):
            # Check the string arg — offending only if it's a provider secret.
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value in (
                        "OPENAI_API_KEY",
                        "ANTHROPIC_API_KEY",
                        "GOOGLE_GEMINI_API_KEY",
                        "GOOGLE_API_KEY",
                    ):
                        raise AssertionError(
                            f"_build_model / helpers in core/langgraph/llm_factory.py "
                            f"still reads {arg.value!r} directly. The resolver must "
                            "own this path."
                        )


def test_create_chat_model_accepts_tenant_id() -> None:
    import inspect

    from core.langgraph.llm_factory import create_chat_model

    sig = inspect.signature(create_chat_model)
    assert "tenant_id" in sig.parameters, (
        "create_chat_model must accept a tenant_id kwarg so callers can "
        "propagate the request's tenant to the resolver."
    )


# ── Migration ID ────────────────────────────────────────────────────


def test_v493_migration_id_within_limit() -> None:
    src = _read("migrations/versions/v4_9_3_tenant_ai_settings.py")
    assert 'revision = "v493_tenant_ai_settings"' in src
    assert 'down_revision = "v492_tenant_ai_creds"' in src
    # Revision ID must be <= 32 chars per preflight
    assert len("v493_tenant_ai_settings") <= 32
