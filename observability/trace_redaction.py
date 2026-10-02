# SPDX-License-Identifier: Apache-2.0
"""Keep grant tokens out of LangSmith traces of agent graph state.

``AgentState.grant_token`` carries the run's Grantex grant (a credential). When
LangSmith tracing is switched on through the environment
(``LANGSMITH_TRACING`` / ``LANGCHAIN_TRACING_V2``), LangChain records every
graph node's inputs and outputs - the whole state. ``install_trace_redaction``
configures the process-wide LangSmith client to replace any value under a
credential key with ``[redacted]`` before a run is sent. With tracing off it
does nothing.

Residency: with deployment-wide enforcement the exporter is never installed.
With tenant-scoped enforcement the hook withholds the payload of a run whose
tenant enforces residency (or was never read), and any payload that names no
tenant while some tenant in the process enforces it; only ``hidden`` and the
tenant id are exported for those.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import structlog

logger = structlog.get_logger()

REDACTED = "[redacted]"
HIDDEN = {"hidden": "residency"}
# Keys whose values are grant or caller tokens anywhere in traced payloads.
CREDENTIAL_KEYS = frozenset({"grant_token", "caller_token", "parent_grant_token", "root_grant_token"})
_TRACING_ENV_VARS = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING")
_installed = False


def redact_credentials(value: Any) -> Any:
    """Return ``value`` with every credential key's value replaced, recursively."""
    if isinstance(value, Mapping):
        return {
            key: (REDACTED if isinstance(key, str) and key in CREDENTIAL_KEYS and item else redact_credentials(item))
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(redact_credentials(item) for item in value)
    return value


def _tenant_of(payload: Any) -> str | None:
    value = payload.get("tenant_id") if isinstance(payload, Mapping) else None
    return str(value) if value else None


def _withheld_for_residency(payload: Any) -> bool:
    """Whether the payload must be withheld: its tenant enforces residency (or was
    never read), or it names no tenant while some tenant in this process does."""
    from core.governance import residency

    tenant = _tenant_of(payload)
    if tenant is None:
        return residency.any_tenant_enforcing()
    return residency.enforcement_known(tenant) is not False


def _redact_payload(payload: dict) -> dict:
    if _withheld_for_residency(payload):
        tenant = _tenant_of(payload)
        return {**HIDDEN, **({"tenant_id": tenant} if tenant else {})}
    redacted = redact_credentials(payload)
    return redacted if isinstance(redacted, dict) else {}


def tracing_enabled() -> bool:
    return any(os.getenv(name, "").strip().lower() in {"1", "true", "yes"} for name in _TRACING_ENV_VARS)


def install_trace_redaction() -> bool:
    """Configure LangSmith to redact credentials when tracing is on. Idempotent.

    Returns True when a redacting client is installed.
    """
    global _installed
    if _installed or not tracing_enabled():
        return _installed
    from core.config import settings

    if settings.residency_enforce:
        # Residency: tracing export is an external destination with no
        # tenant attestation path; with deployment-wide enforcement it stays off.
        # Tenant-scoped enforcement is honoured per payload by _redact_payload.
        for name in _TRACING_ENV_VARS:
            os.environ.pop(name, None)
        logger.warning("langsmith_tracing_refused_residency")
        return False
    try:
        import langsmith
        from langsmith import Client
    except ImportError:
        return False
    langsmith.configure(client=Client(hide_inputs=_redact_payload, hide_outputs=_redact_payload))
    _installed = True
    logger.info("langsmith_trace_redaction_installed", keys=sorted(CREDENTIAL_KEYS))
    return True
