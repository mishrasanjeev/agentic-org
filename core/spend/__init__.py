# SPDX-License-Identifier: Apache-2.0
"""AI spend intelligence: reference data, rupee pricing, usage records and their attribution.

The package is import-light on purpose: importing it reads one setting and
nothing else, so a guarded call site costs a bool read while the feature is
off. Behind ``spend_intelligence_enabled`` (default off).

``note`` is the one entry point for call sites outside ``record_model_call``
(direct model calls; embeddings, OCR pages, speech minutes and priced tool
calls); ``drain`` stops this process's usage writer at shutdown and hands what
is left to a worker.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from core.config import settings

_log = logging.getLogger(__name__)
_WRITER_MODULE = "core.spend.writer"


def enabled() -> bool:
    """Whether spend intelligence is on for this deployment."""
    return bool(getattr(settings, "spend_intelligence_enabled", False))


def note(kind: str, tenant_id: object, /, **raw: object) -> None:
    """Meter usage a call site saw; never raises, returns at once while off.

    ``raw`` carries the objects the site already holds (a response, a model
    object); the handler derives every quantity (``core/spend/metering.py``).
    """
    if not enabled():
        return
    try:
        from core.spend import metering

        metering.handle(kind, tenant_id, raw)
    # enterprise-gate: broad-except-ok reason=spend-note-failure-is-logged-and-counted-the-caller-proceeds
    except Exception as exc:
        _log.warning("spend_note_failed kind=%s error_type=%s", str(kind)[:32], type(exc).__name__)
        try:
            from core.spend.metering import KIND_USAGE_TYPES
            from observability import metrics

            usage_type = KIND_USAGE_TYPES.get(str(kind), "llm_tokens")
            metrics.spend_usage_write_failures_total.labels(usage_type=usage_type, reason="hook_error").inc()
        # enterprise-gate: broad-except-ok reason=metrics-outage-degrades-to-a-logged-note-failure
        except Exception:
            _log.debug("spend_note_failure_not_counted")


def drain_blocking(timeout: float = 5.0) -> int:
    """Stop this process's usage writer and spill what is left; 0 when it never started."""
    module = sys.modules.get(_WRITER_MODULE)
    if module is None:
        return 0
    return int(module.drain_blocking(timeout))


async def drain(timeout: float = 5.0) -> int:
    """``drain_blocking`` on a worker thread (for the API lifespan); 0 at once when the writer never started."""
    if _WRITER_MODULE not in sys.modules:
        return 0
    return await asyncio.to_thread(drain_blocking, timeout)
