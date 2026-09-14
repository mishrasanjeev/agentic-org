"""Create LangChain ChatModel instances from config.

Supports Gemini (default), Claude, GPT, and local models (Ollama/vLLM)
with automatic fallback and smart tier-based routing via LLMRouter.

Air-gapped deployment modes (AGENTICORG_LLM_MODE):
  - cloud: default behaviour, use cloud LLM providers
  - local: require local endpoint (Ollama/vLLM), fail if unavailable
  - auto: try local Ollama first, fallback to cloud
"""

from __future__ import annotations

import os
import socket

import structlog
from langchain_core.language_models import BaseChatModel

from core import model_replay
from core.ai_providers.catalog import find_llm, validate_llm_selection
from core.llm.router import LLMProviderConfigurationError, smart_router

logger = structlog.get_logger()


def _is_local_endpoint_available(host: str = "localhost", port: int = 11434, timeout: float = 1.0) -> bool:
    """Check if a local LLM endpoint (Ollama/vLLM) is reachable."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError, TimeoutError):
        return False


def _get_llm_mode() -> str:
    """Return the LLM mode: 'cloud', 'local', or 'auto'."""
    return os.getenv("AGENTICORG_LLM_MODE", "cloud").lower()


def create_chat_model(
    model: str = "",
    temperature: float = 0.1,
    max_tokens: int = 4096,
    *,
    query: str = "",
    routing_config: dict | None = None,
    tenant_id: str | None = None,
    provider: str | None = None,
) -> BaseChatModel:
    """Create a LangChain ChatModel, optionally using smart routing.

    When *query* is provided and routing is not disabled, the SmartLLMRouter
    selects the optimal model tier.  Otherwise the explicitly requested
    *model* (or the env-var default) is used directly.

    Air-gapped mode handling:
      - ``AGENTICORG_LLM_MODE=local``: forces Ollama/vLLM endpoints, raises
        if no local endpoint is available.
      - ``AGENTICORG_LLM_MODE=auto``: tries localhost:11434 (Ollama) first,
        falls back to cloud providers on failure.
      - ``AGENTICORG_LLM_MODE=cloud``: existing cloud behaviour (default).

    Models prefixed with ``ollama:`` or ``vllm:`` are routed to the
    corresponding local backend regardless of mode.

    Args:
        model: Explicit model name override.  When empty, the router or
               env default decides.
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens.
        query: The user query / task text.  Passed to the router for
               complexity classification when routing is enabled.
        routing_config: Optional dict from the agent's ``llm_config``
                        (may contain ``routing`` and ``provider`` keys).
        provider: Explicit catalog provider id (``gemini`` / ``openai`` /
                  ``anthropic`` / ``openai_compatible``). Bug sheet
                  2026-09-14 #31: when set (directly or via
                  ``routing_config["provider"]``) the provider is never
                  inferred from the model name and an unknown model is
                  never downgraded to the Gemini default — a mismatch
                  raises ``ValueError``. ``None`` keeps the legacy
                  inference for rows that predate ``agents.llm_provider``.

    Returns:
        A LangChain ``BaseChatModel`` ready for ``.ainvoke()``.

    In ``record`` and ``replay`` model modes (``AGENTICORG_MODEL_MODE``, see
    ``core.model_replay``) the model is wrapped for cassettes, and the real
    model is only built when a live call is made.
    """
    mode = model_replay.current_mode()
    if mode is not model_replay.ModelMode.LIVE:
        pinned = _normalise_provider(provider or (routing_config or {}).get("provider"))
        return model_replay.ReplayChatModel(
            model_name=f"{pinned}/{model}" if pinned else (model or "default"),
            temperature=temperature,
            max_tokens=max_tokens,
            live_factory=lambda: _create_live_chat_model(
                model,
                temperature,
                max_tokens,
                query=query,
                routing_config=routing_config,
                tenant_id=tenant_id,
                provider=provider,
            ),
        )
    return _create_live_chat_model(
        model,
        temperature,
        max_tokens,
        query=query,
        routing_config=routing_config,
        tenant_id=tenant_id,
        provider=provider,
    )


def _create_live_chat_model(
    model: str,
    temperature: float,
    max_tokens: int,
    *,
    query: str,
    routing_config: dict | None,
    tenant_id: str | None,
    provider: str | None,
) -> BaseChatModel:
    routing_config = routing_config or {}
    routing_mode = routing_config.get("routing", os.getenv("AGENTICORG_LLM_ROUTING", "auto"))
    llm_mode = _get_llm_mode()
    provider = _normalise_provider(provider or routing_config.get("provider"))

    # ── Handle explicit ollama:/vllm: prefixes regardless of mode ────
    if model.startswith("ollama:"):
        return _build_ollama_model(model[len("ollama:"):], temperature, max_tokens)
    if model.startswith("vllm:"):
        return _build_vllm_model(model[len("vllm:"):], temperature, max_tokens)

    # ── Air-gapped: local mode — require local endpoint ──────────────
    if llm_mode == "local":
        ollama_url = os.getenv("OLLAMA_BASE_URL", os.getenv("OLLAMA_HOST", "http://localhost:11434"))
        vllm_url = os.getenv("VLLM_BASE_URL", os.getenv("VLLM_API_BASE", "http://localhost:8000"))

        # Try Ollama first
        if _is_local_endpoint_available("localhost", 11434):
            local_model = model or os.getenv("AGENTICORG_LOCAL_TIER1", "gemma3:7b")
            logger.info("llm_mode_local_ollama", model=local_model, base_url=ollama_url)
            return _build_ollama_model(local_model, temperature, max_tokens)

        # Try vLLM
        if _is_local_endpoint_available("localhost", 8000):
            local_model = model or os.getenv("AGENTICORG_LOCAL_TIER2", "llama3.1:8b")
            logger.info("llm_mode_local_vllm", model=local_model, base_url=vllm_url)
            return _build_vllm_model(local_model, temperature, max_tokens)

        raise ConnectionError(
            "AGENTICORG_LLM_MODE=local but no local LLM endpoint is available. "
            "Start Ollama (port 11434) or vLLM (port 8000)."
        )

    # ── Auto mode: try local first, fallback to cloud ────────────────
    if llm_mode == "auto":
        if _is_local_endpoint_available("localhost", 11434):
            local_model = model or os.getenv("AGENTICORG_LOCAL_TIER1", "gemma3:7b")
            logger.info("llm_mode_auto_using_local", model=local_model)
            return _build_ollama_model(local_model, temperature, max_tokens)
        logger.debug("llm_mode_auto_no_local_falling_back_to_cloud")
        # Fall through to cloud path

    # ── Cloud mode (default) ─────────────────────────────────────────
    # If routing is not disabled and we have a query, ask the smart router
    if routing_mode != "disabled" and query:
        try:
            # Let the router pick the best model; pass the agent's own model
            # so "disabled" routing can fall back to it
            cfg = {**routing_config, "llm_model": model}
            routed_model = smart_router.route(query=query, config=cfg)
            if routed_model and provider and find_llm(provider, routed_model) is None:
                # A pinned provider must not be crossed by tier routing.
                logger.info(
                    "smart_routing_ignored_provider_pinned",
                    provider=provider,
                    routed_model=routed_model,
                    model=model,
                )
            elif routed_model:
                model = routed_model
        # enterprise-gate: broad-except-ok reason=smart-routing-failure-falls-back-to-explicit-model
        except Exception as exc:  # noqa: BLE001
            logger.warning("smart_routing_failed", error=str(exc), fallback=model)
            # Fall through to normal model resolution

    if provider:
        # Explicit provider pins dispatch: validate the pair against the
        # catalog (ValueError on mismatch) instead of substring-guessing.
        provider, resolved = validate_llm_selection(provider, model)
        return _build_model(resolved, temperature, max_tokens, tenant_id=tenant_id, provider=provider)

    resolved = _resolve_model(model)
    return _build_model(resolved, temperature, max_tokens, tenant_id=tenant_id)


def _normalise_provider(value: object) -> str | None:
    clean = str(value or "").strip().lower()
    return clean or None


def _build_ollama_model(model_name: str, temperature: float, max_tokens: int) -> BaseChatModel:
    """Create a ChatOpenAI pointing to the Ollama OpenAI-compatible endpoint."""
    from langchain_openai import ChatOpenAI

    base_url = os.getenv("OLLAMA_BASE_URL", os.getenv("OLLAMA_HOST", "http://localhost:11434")) + "/v1"
    logger.info("building_ollama_model", model=model_name, base_url=base_url)
    return ChatOpenAI(
        model=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        openai_api_key="ollama",  # Ollama doesn't need a real key
        openai_api_base=base_url,
    )


def _build_vllm_model(model_name: str, temperature: float, max_tokens: int) -> BaseChatModel:
    """Create a ChatOpenAI pointing to the vLLM OpenAI-compatible endpoint."""
    from langchain_openai import ChatOpenAI

    base_url = os.getenv("VLLM_BASE_URL", os.getenv("VLLM_API_BASE", "http://localhost:8000")) + "/v1"
    logger.info("building_vllm_model", model=model_name, base_url=base_url)
    return ChatOpenAI(
        model=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        openai_api_key=os.getenv("VLLM_API_KEY", "vllm"),
        openai_api_base=base_url,
    )


def _resolve_cloud_credential(provider: str, tenant_id: str | None = None):
    """Resolve the tenant-aware credential record for a cloud LLM provider.

    S0-08 (PR-2): the earlier version read ``os.getenv(...)`` directly.
    Route through the tenant-aware resolver so BYO tokens and the
    tenant's ``ai_fallback_policy`` take effect. On any resolver error
    we return ``None`` so callers that run outside a tenant context
    (cron, boot probes) surface a clear provider-not-configured error.
    Returns the full ``ResolvedCredential`` because ``openai_compatible``
    also needs the non-secret ``provider_config.base_url``.
    """
    from core.ai_providers.resolver import (
        ProviderNotConfigured,
        get_provider_credential_sync,
    )

    try:
        return get_provider_credential_sync(tenant_id, provider, "llm")
    except ProviderNotConfigured:
        # No tenant BYO and no platform env — let the caller raise the
        # provider-specific configuration error so the symptom is actionable.
        return None
    # enterprise-gate: broad-except-ok reason=llm-key-resolver-failure-returns-provider-unavailable
    except Exception as exc:
        logger.warning("llm_key_resolve_failed", provider=provider, error=str(exc))
        return None


def _resolve_cloud_api_key(provider: str, tenant_id: str | None = None) -> str:
    """Resolve the API key for a cloud LLM provider ("" when unavailable)."""
    resolved = _resolve_cloud_credential(provider, tenant_id)
    return resolved.secret if resolved is not None else ""


def _build_gemini_model(
    model_name: str, temperature: float, max_tokens: int, tenant_id: str | None
) -> BaseChatModel:
    api_key = _resolve_cloud_api_key("gemini", tenant_id)
    if not api_key:
        raise LLMProviderConfigurationError("Gemini provider is not configured")

    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=model_name,
        temperature=temperature,
        max_output_tokens=max_tokens,
        google_api_key=api_key,
    )


def _build_anthropic_model(
    model_name: str, temperature: float, max_tokens: int, tenant_id: str | None
) -> BaseChatModel:
    api_key = _resolve_cloud_api_key("anthropic", tenant_id)
    if not api_key:
        raise LLMProviderConfigurationError("Anthropic provider is not configured")

    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(  # type: ignore[call-arg]
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        anthropic_api_key=api_key,
    )


def _build_openai_model(
    model_name: str, temperature: float, max_tokens: int, tenant_id: str | None
) -> BaseChatModel:
    api_key = _resolve_cloud_api_key("openai", tenant_id)
    if not api_key:
        raise LLMProviderConfigurationError("OpenAI provider is not configured")

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        openai_api_key=api_key,
    )


def _build_openai_compatible_model(
    model_name: str, temperature: float, max_tokens: int, tenant_id: str | None
) -> BaseChatModel:
    """Bug sheet 2026-09-14 #38: dispatch for the catalog's ``openai_compatible`` provider.

    ``base_url`` comes from the tenant's saved BYO credential
    (``tenant_ai_credentials.provider_config.base_url`` — the record the
    AI Credentials save path validates as a public HTTPS host and the
    health probe exercises). There is no platform env fallback for this
    provider, so a tenant without that credential fails closed with a
    clear configuration error rather than hitting the wrong endpoint.
    The health probe treats ``base_url`` as the endpoint root
    (``{base_url}/v1/models``), so the ``/v1`` prefix is appended here
    when the admin did not include it.
    """
    resolved = _resolve_cloud_credential("openai_compatible", tenant_id)
    if resolved is None or not resolved.secret:
        raise LLMProviderConfigurationError(
            "openai_compatible provider is not configured: register a BYO credential "
            "with provider_config.base_url under AI Credentials"
        )
    base_url = str((resolved.provider_config or {}).get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise LLMProviderConfigurationError(
            "openai_compatible provider requires provider_config.base_url on the tenant AI credential"
        )
    if not base_url.lower().startswith("https://"):
        raise LLMProviderConfigurationError(
            "openai_compatible provider_config.base_url must be an https:// endpoint"
        )
    if not base_url.endswith("/v1"):
        base_url = base_url + "/v1"

    from langchain_openai import ChatOpenAI

    logger.info("building_openai_compatible_model", model=model_name, base_url=base_url)
    return ChatOpenAI(
        model=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        openai_api_key=resolved.secret,
        openai_api_base=base_url,
    )


def _build_provider_model(
    provider: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
    tenant_id: str | None,
) -> BaseChatModel:
    """Dispatch strictly by catalog provider id (no model-name inference)."""
    if provider == "gemini":
        return _build_gemini_model(model_name, temperature, max_tokens, tenant_id)
    if provider == "anthropic":
        return _build_anthropic_model(model_name, temperature, max_tokens, tenant_id)
    if provider == "openai":
        return _build_openai_model(model_name, temperature, max_tokens, tenant_id)
    if provider == "openai_compatible":
        return _build_openai_compatible_model(model_name, temperature, max_tokens, tenant_id)
    raise LLMProviderConfigurationError(
        f"LLM provider {provider!r} has no runtime dispatch; "
        "use gemini, anthropic, openai or openai_compatible"
    )


def _build_model(
    resolved: str,
    temperature: float,
    max_tokens: int,
    tenant_id: str | None = None,
    provider: str | None = None,
) -> BaseChatModel:
    """Instantiate the correct LangChain ChatModel for *resolved* model name.

    When ``tenant_id`` is provided, credentials route through the
    tenant AI resolver (``core.ai_providers.resolver``) so BYO tokens
    take effect. Sync-over-async via a short-lived threadpool —
    expensive per call only when the resolver cache misses (60-120s
    TTL).

    When ``provider`` is given the dispatch is by provider id only. The
    substring inference below is the legacy path for rows without an
    explicit provider.
    """
    if provider:
        return _build_provider_model(provider, resolved, temperature, max_tokens, tenant_id)

    # Local models via Ollama (OpenAI-compatible API)
    if _is_ollama_model(resolved):
        return _build_ollama_model(resolved, temperature, max_tokens)

    # Local models via vLLM (OpenAI-compatible API)
    if _is_vllm_model(resolved):
        return _build_vllm_model(resolved, temperature, max_tokens)

    # Cloud: Gemini
    if "gemini" in resolved:
        return _build_gemini_model(resolved, temperature, max_tokens, tenant_id)

    # Cloud: Claude
    if "claude" in resolved:
        return _build_anthropic_model(resolved, temperature, max_tokens, tenant_id)

    # Cloud: GPT
    if "gpt" in resolved:
        return _build_openai_model(resolved, temperature, max_tokens, tenant_id)

    # Default fallback — Gemini Flash (free)
    return _build_gemini_model("gemini-2.5-flash", temperature, max_tokens, tenant_id)


def _resolve_model(model: str) -> str:
    """Resolve the model name without constructing optional provider clients."""
    if not model:
        return os.getenv("AGENTICORG_LLM_PRIMARY", "gemini-2.5-flash")

    m = model.lower()

    # Local models don't need API key checks
    if _is_ollama_model(m) or _is_vllm_model(m):
        return model

    if "gemini" in m:
        return model

    # Explicit cloud-provider selections are honored here. Missing
    # credentials are reported by _build_model as provider configuration
    # errors instead of silently falling back to another provider.
    if "claude" in m:
        return model

    if "gpt" in m:
        return model

    return "gemini-2.5-flash"


def _is_ollama_model(model: str) -> bool:
    """Check if this is an Ollama-served local model."""
    m = model.lower()
    # Ollama model patterns: "llama3.2:3b", "mistral:7b", etc.
    ollama_prefixes = ("llama", "mistral", "phi", "qwen", "codellama", "deepseek", "gemma")
    return any(m.startswith(prefix) for prefix in ollama_prefixes) or "ollama" in m


def _is_vllm_model(model: str) -> bool:
    """Check if this is a vLLM-served model."""
    m = model.lower()
    return "vllm/" in m or os.getenv("VLLM_API_BASE", "") != "" and ":" in m and not _is_ollama_model(m)
