# SPDX-License-Identifier: Apache-2.0
"""AI spend intelligence: reference data, rupee pricing, usage records and their attribution.

The package is import-light on purpose: importing it reads one setting and
nothing else, so a guarded call site costs a bool read while the feature is
off. Behind ``spend_intelligence_enabled`` (default off).

``note`` is the one entry point for call sites outside ``record_model_call``
(direct model calls today, non-token metering later); ``prewarm`` loads the
metering code at process start while the feature is on; ``drain`` stops this
process's usage writer at shutdown and hands what is left to a worker.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys

from core.config import settings

_log = logging.getLogger(__name__)
_WRITER_MODULE = "core.spend.writer"
# What the model-call hook and ``note`` import on first use, in dependency order.
_HOOK_MODULES = (
    "observability.metrics",
    "core.finops.attribution",  # vocab.label
    "core.spend.tokens",
    "core.spend.clock",
    "core.spend.meter",
    _WRITER_MODULE,
    "core.spend.metering",
)


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
            from observability import metrics

            metrics.spend_usage_write_failures_total.labels(usage_type="llm_tokens", reason="hook_error").inc()
        # enterprise-gate: broad-except-ok reason=metrics-outage-degrades-to-a-logged-note-failure
        except Exception:
            _log.debug("spend_note_failure_not_counted")


def prewarm() -> bool:
    """Load the metering code now, at process start, so the first metered call does not pay for it.

    Does nothing at all while off. Never raises: a failure is logged and the
    first metered call imports the code itself, as it would without this. A
    process that drained its writer (an earlier API lifespan) may start one
    again. Returns whether the code is loaded.
    """
    if not enabled():
        return False
    try:
        for name in _HOOK_MODULES:
            importlib.import_module(name)
        sys.modules[_WRITER_MODULE].reopen()
    # enterprise-gate: broad-except-ok reason=spend-prewarm-failure-is-logged-the-first-call-imports-lazily
    except Exception as exc:
        _log.warning("spend_prewarm_failed error_type=%s", type(exc).__name__)
        return False
    return True


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
