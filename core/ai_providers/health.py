"""Provider health probes for BYO AI credentials.

Each provider exposes a cheap identity endpoint that proves a token
works without running a real completion. ``probe_provider`` routes a
credential to the matching probe and returns ``{ok, error, latency_ms,
raw_status}`` — never the token body or any response data past status.

Called from ``api/v1/tenant_ai_credentials.py::test_credential``.
"""

from __future__ import annotations

import time
import uuid as _uuid
from typing import Any

import httpx
import structlog

from core.ai_providers.resolver import ProviderNotConfigured, get_provider_credential

logger = structlog.get_logger(__name__)

_PROBE_TIMEOUT_S = 10.0


async def _probe_openai(credential: str, base_url: str | None = None) -> dict[str, Any]:
    """OpenAI + OpenAI-compatible: GET /v1/models (lists available models)."""
    url = f"{(base_url or 'https://api.openai.com').rstrip('/')}/v1/models"
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
        start = time.time()
        resp = await client.get(url, headers={"Authorization": f"Bearer {credential}"})
        latency_ms = int((time.time() - start) * 1000)
    if resp.status_code == 200:
        return {"ok": True, "latency_ms": latency_ms, "raw_status": 200}
    return {
        "ok": False,
        "latency_ms": latency_ms,
        "raw_status": resp.status_code,
        "error": _classify_http_error(resp.status_code),
    }


async def _probe_anthropic(credential: str) -> dict[str, Any]:
    """Anthropic: HEAD /v1/messages fails cheap on bad key (401)."""
    url = "https://api.anthropic.com/v1/messages"
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
        start = time.time()
        # POST with an empty body — Anthropic returns 400 for missing
        # required fields when auth is valid, 401 when auth is bad.
        resp = await client.post(
            url,
            headers={
                "x-api-key": credential,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={},
        )
        latency_ms = int((time.time() - start) * 1000)
    if resp.status_code in (400, 422):
        # Auth accepted; body validation fired — key is good.
        return {"ok": True, "latency_ms": latency_ms, "raw_status": resp.status_code}
    if resp.status_code == 200:
        return {"ok": True, "latency_ms": latency_ms, "raw_status": 200}
    return {
        "ok": False,
        "latency_ms": latency_ms,
        "raw_status": resp.status_code,
        "error": _classify_http_error(resp.status_code),
    }


async def _probe_gemini(credential: str) -> dict[str, Any]:
    """Gemini: GET /v1beta/models?key=..."""
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
        start = time.time()
        resp = await client.get(url, params={"key": credential})
        latency_ms = int((time.time() - start) * 1000)
    if resp.status_code == 200:
        return {"ok": True, "latency_ms": latency_ms, "raw_status": 200}
    return {
        "ok": False,
        "latency_ms": latency_ms,
        "raw_status": resp.status_code,
        "error": _classify_http_error(resp.status_code),
    }


async def _probe_voyage(credential: str) -> dict[str, Any]:
    """Voyage embedding: a minimal embed call against the cheapest model."""
    url = "https://api.voyageai.com/v1/embeddings"
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
        start = time.time()
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {credential}"},
            json={"input": ["ping"], "model": "voyage-3-lite"},
        )
        latency_ms = int((time.time() - start) * 1000)
    if resp.status_code == 200:
        return {"ok": True, "latency_ms": latency_ms, "raw_status": 200}
    return {
        "ok": False,
        "latency_ms": latency_ms,
        "raw_status": resp.status_code,
        "error": _classify_http_error(resp.status_code),
    }


async def _probe_cohere(credential: str) -> dict[str, Any]:
    """Cohere: GET /v1/models."""
    url = "https://api.cohere.ai/v1/models"
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
        start = time.time()
        resp = await client.get(url, headers={"Authorization": f"Bearer {credential}"})
        latency_ms = int((time.time() - start) * 1000)
    if resp.status_code == 200:
        return {"ok": True, "latency_ms": latency_ms, "raw_status": 200}
    return {
        "ok": False,
        "latency_ms": latency_ms,
        "raw_status": resp.status_code,
        "error": _classify_http_error(resp.status_code),
    }


def _classify_http_error(status_code: int) -> str:
    """Map HTTP status to a specific, actionable operator message."""
    if status_code == 401 or status_code == 403:
        return (
            f"Provider returned {status_code}: key is invalid, expired, or "
            "lacks required scopes."
        )
    if status_code == 429:
        return "Provider returned 429: rate-limited or quota exhausted."
    if 500 <= status_code < 600:
        return f"Provider returned {status_code}: upstream is unhealthy."
    return f"Provider returned unexpected status {status_code}."


async def probe_provider(
    tenant_id: _uuid.UUID, provider: str, kind: str
) -> dict[str, Any]:
    """Resolve the tenant credential and run the provider's probe.

    Returns ``{ok, error?, latency_ms?, raw_status?}`` regardless of
    success. Never raises — callers expect a dict.
    """
    try:
        resolved = await get_provider_credential(tenant_id, provider, kind)
    except ProviderNotConfigured as exc:
        return {"ok": False, "error": f"Resolver refused: {exc}"}

    secret = resolved.secret
    base_url = (
        (resolved.provider_config or {}).get("base_url")
        if resolved.provider_config else None
    )

    try:
        if provider == "openai":
            return await _probe_openai(secret, base_url)
        if provider == "openai_compatible":
            return await _probe_openai(secret, base_url)
        if provider == "azure_openai":
            # Azure shape needs endpoint + deployment; require base_url
            if not base_url:
                return {
                    "ok": False,
                    "error": (
                        "Azure OpenAI requires provider_config.base_url "
                        "(your Azure resource endpoint)."
                    ),
                }
            return await _probe_openai(secret, base_url)
        if provider == "anthropic":
            return await _probe_anthropic(secret)
        if provider == "gemini":
            return await _probe_gemini(secret)
        if provider == "voyage":
            return await _probe_voyage(secret)
        if provider == "cohere":
            return await _probe_cohere(secret)
        # STT/TTS + RAGFlow probes can be added here as the feature lands.
        return {
            "ok": False,
            "error": (
                f"No probe implemented for provider={provider!r}. The "
                "credential is stored but cannot be health-tested."
            ),
        }
    except TimeoutError:
        return {"ok": False, "error": f"Probe timed out after {_PROBE_TIMEOUT_S}s."}
    except Exception as exc:
        logger.warning(
            "provider_probe_failed",
            provider=provider,
            kind=kind,
            error=str(exc),
        )
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
