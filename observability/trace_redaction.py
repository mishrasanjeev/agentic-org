# SPDX-License-Identifier: Apache-2.0
"""Keep grant tokens out of LangSmith traces of agent graph state.

``AgentState.grant_token`` carries the run's Grantex grant (a credential). When
LangSmith tracing is switched on through the environment
(``LANGSMITH_TRACING`` / ``LANGCHAIN_TRACING_V2``), LangChain records every
graph node's inputs and outputs - the whole state. ``install_trace_redaction``
configures the process-wide LangSmith client to replace any value under a
credential key with ``[redacted]`` before a run is sent. With tracing off it
does nothing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import structlog

logger = structlog.get_logger()

REDACTED = "[redacted]"
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


def _redact_payload(payload: dict) -> dict:
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
    try:
        import langsmith
        from langsmith import Client
    except ImportError:
        return False
    langsmith.configure(client=Client(hide_inputs=_redact_payload, hide_outputs=_redact_payload))
    _installed = True
    logger.info("langsmith_trace_redaction_installed", keys=sorted(CREDENTIAL_KEYS))
    return True
