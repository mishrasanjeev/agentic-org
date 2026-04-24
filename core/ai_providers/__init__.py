"""Tenant-aware AI provider resolution.

Exposes a single contract for LLM, embedding, RAG, STT, and TTS callers:
ask ``resolver.get_provider_credential(...)`` and get back either the
tenant's BYO token (decrypted on demand) or, if the tenant's
``ai_fallback_policy`` allows it, the platform env key. Callers NEVER
read env vars directly past this point.

Part of S0-09 closure (PR-1).
"""

from core.ai_providers.resolver import (
    ProviderNotConfigured as ProviderNotConfigured,
)
from core.ai_providers.resolver import (
    ResolvedCredential as ResolvedCredential,
)
from core.ai_providers.resolver import (
    get_provider_credential as get_provider_credential,
)
from core.ai_providers.settings import (
    EffectiveAISetting as EffectiveAISetting,
)
from core.ai_providers.settings import (
    get_effective_ai_setting as get_effective_ai_setting,
)
from core.ai_providers.settings import (
    invalidate_tenant_ai_setting_cache as invalidate_tenant_ai_setting_cache,
)
