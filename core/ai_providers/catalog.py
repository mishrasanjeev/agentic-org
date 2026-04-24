"""Allowlist of provider/model combinations that the tenant AI config
may choose from.

Closes S0-08 (PR-2 of the four-PR closure plan). Every model that
admins can select via the ``tenant_ai_settings`` PUT endpoint must be
registered here. Unknown (provider, model) pairs are rejected at the
API boundary so operators can't flip an env var into a
model/dimension mismatch.

The catalog intentionally lives in Python (not the database): the
allowlist changes with code deployments, and we want Git history as
the audit trail for "when did we start accepting model X". Add new
entries via a small PR with a schema-validation test.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMModel:
    provider: str
    model: str
    context_window: int
    max_output_tokens: int
    supports_tools: bool = True
    supports_vision: bool = False
    notes: str = ""


@dataclass(frozen=True)
class EmbeddingModel:
    provider: str
    model: str
    dimensions: int  # Vector size the model emits.
    max_input_tokens: int
    notes: str = ""


# ─── LLM allowlist ───────────────────────────────────────────────────
# When adding a new row: also update
# ``tests/regression/test_s008_tenant_ai_config.py::test_llm_catalog_covers_required_providers``
# so future drift fails loudly.
LLM_CATALOG: tuple[LLMModel, ...] = (
    # Gemini (platform default — free-tier friendly)
    LLMModel("gemini", "gemini-2.5-flash", 1_048_576, 8192),
    LLMModel("gemini", "gemini-2.5-flash-preview-05-20", 1_048_576, 8192),
    LLMModel("gemini", "gemini-2.5-pro", 2_097_152, 8192, supports_vision=True),
    LLMModel("gemini", "gemini-2.0-flash-exp", 1_048_576, 8192),
    # OpenAI
    LLMModel("openai", "gpt-4o", 128_000, 16_384, supports_vision=True),
    LLMModel("openai", "gpt-4o-mini", 128_000, 16_384, supports_vision=True),
    LLMModel("openai", "gpt-4-turbo", 128_000, 4_096, supports_vision=True),
    LLMModel("openai", "o1", 200_000, 100_000, supports_tools=False),
    LLMModel("openai", "o1-mini", 128_000, 65_536, supports_tools=False),
    # Anthropic
    LLMModel("anthropic", "claude-sonnet-4-5-20250929", 200_000, 64_000, supports_vision=True),
    LLMModel("anthropic", "claude-sonnet-4-6-20251001", 200_000, 64_000, supports_vision=True),
    LLMModel("anthropic", "claude-opus-4-1-20250805", 200_000, 32_000, supports_vision=True),
    LLMModel("anthropic", "claude-opus-4-5-20250929", 200_000, 32_000, supports_vision=True),
    LLMModel("anthropic", "claude-3-5-sonnet-20241022", 200_000, 8192, supports_vision=True),
    # Azure OpenAI — model name is the deployment name, not the base model.
    # Admins must set ``provider_config.base_url`` to their Azure endpoint.
    LLMModel(
        "azure_openai",
        "deployment:gpt-4o",
        128_000,
        16_384,
        supports_vision=True,
        notes="deployment name, configure base_url in provider_config",
    ),
    LLMModel(
        "azure_openai",
        "deployment:gpt-4o-mini",
        128_000,
        16_384,
        supports_vision=True,
        notes="deployment name, configure base_url in provider_config",
    ),
    # OpenAI-compatible self-hosted — pass through. Admin chooses the
    # model name served by their endpoint. We register a wildcard marker
    # that the validator special-cases.
    LLMModel(
        "openai_compatible",
        "*",
        128_000,
        16_384,
        notes="admin must set provider_config.base_url; model name is passed through",
    ),
)

# ─── Embedding allowlist ─────────────────────────────────────────────
# Each entry pins a specific dimension; the PUT endpoint rejects a
# tenant setting whose declared ``embedding_dimensions`` doesn't match
# the catalog entry. Prevents model/index drift.
EMBEDDING_CATALOG: tuple[EmbeddingModel, ...] = (
    # Local BGE — the platform default, runs on fastembed ONNX.
    EmbeddingModel(
        "local", "BAAI/bge-small-en-v1.5", 384, 512,
        notes="platform default, self-hosted via fastembed, no BYO key needed",
    ),
    EmbeddingModel("local", "BAAI/bge-base-en-v1.5", 768, 512),
    EmbeddingModel("local", "BAAI/bge-large-en-v1.5", 1024, 512),
    EmbeddingModel("local", "BAAI/bge-m3", 1024, 8192, notes="multilingual"),
    # OpenAI
    EmbeddingModel("openai", "text-embedding-3-small", 1536, 8191),
    EmbeddingModel("openai", "text-embedding-3-large", 3072, 8191),
    # Voyage
    EmbeddingModel("voyage", "voyage-3", 1024, 32_000),
    EmbeddingModel("voyage", "voyage-3-lite", 512, 32_000),
    # Cohere
    EmbeddingModel("cohere", "embed-english-v3.0", 1024, 512),
    EmbeddingModel("cohere", "embed-multilingual-v3.0", 1024, 512),
)


# ─── Lookup helpers ──────────────────────────────────────────────────


def find_llm(provider: str, model: str) -> LLMModel | None:
    """Return the catalog entry for ``(provider, model)`` or ``None``.

    OpenAI-compatible with a `*` wildcard matches any model name (the
    admin's self-hosted endpoint controls the actual available list).
    Azure OpenAI matches on prefix (``deployment:*``) so admins can
    register their specific deployment names.
    """
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    for entry in LLM_CATALOG:
        if entry.provider != provider:
            continue
        if entry.model == "*":
            return entry
        if entry.model == model:
            return entry
        # Azure deployment names are caller-specific; accept any deployment:<name>
        if (
            entry.provider == "azure_openai"
            and entry.model.startswith("deployment:")
            and model.startswith("deployment:")
        ):
            return entry
    return None


def find_embedding(provider: str, model: str) -> EmbeddingModel | None:
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    for entry in EMBEDDING_CATALOG:
        if entry.provider == provider and entry.model == model:
            return entry
    return None


def llm_providers() -> tuple[str, ...]:
    return tuple(sorted({e.provider for e in LLM_CATALOG}))


def embedding_providers() -> tuple[str, ...]:
    return tuple(sorted({e.provider for e in EMBEDDING_CATALOG}))


def llm_models_for(provider: str) -> tuple[str, ...]:
    return tuple(e.model for e in LLM_CATALOG if e.provider == provider)


def embedding_models_for(provider: str) -> tuple[str, ...]:
    return tuple(e.model for e in EMBEDDING_CATALOG if e.provider == provider)
