# SPDX-License-Identifier: Apache-2.0
"""AI spend intelligence: reference data, rupee pricing and (in later parts) usage records.

The package is import-light on purpose: importing it reads one setting and
nothing else, so a guarded call site costs a bool read while the feature is
off. Behind ``spend_intelligence_enabled`` (default off).
"""

from __future__ import annotations

from core.config import settings


def enabled() -> bool:
    """Whether spend intelligence is on for this deployment."""
    return bool(getattr(settings, "spend_intelligence_enabled", False))
