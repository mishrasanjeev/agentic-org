"""LLM router and adapters.

Exports:
  - SmartLLMRouter / smart_router: Tier-based smart model selection (v4.0.0)
  - LLMRouter / llm_router: Legacy primary/fallback router (backward compat)
  - LLMResponse: Standardized response dataclass
"""

from core.llm.router import (
    LLMResponse,
    LLMRouter,
    SmartLLMRouter,
    llm_router,
    smart_router,
)

__all__ = [
    "LLMResponse",
    "LLMRouter",
    "SmartLLMRouter",
    "llm_router",
    "smart_router",
]
