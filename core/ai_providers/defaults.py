# SPDX-License-Identifier: Apache-2.0
"""Defaults for new configurations only; persisted user selections are not rewritten."""

import os

DEFAULT_REASONING_MODEL = "gemini-3.5-flash-lite"


def primary_model() -> str:
    return os.getenv("AGENTICORG_LLM_PRIMARY", "").strip() or DEFAULT_REASONING_MODEL


def fallback_model() -> str:
    # No implicit cross-provider fallback or retired preview alias.
    return os.getenv("AGENTICORG_LLM_FALLBACK", "").strip() or primary_model()


def primary_provider() -> str:
    from core.ai_providers.catalog import LLM_CATALOG

    return next((entry.provider for entry in LLM_CATALOG if entry.model == primary_model()), "gemini")
