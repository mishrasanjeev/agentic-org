# SPDX-License-Identifier: Apache-2.0
"""Environment policy shared by the hermetic Redis guard and its tests."""

from __future__ import annotations

import os
from collections.abc import Mapping


def declared_redis_url(environ: Mapping[str, str] = os.environ) -> str | None:
    """Return a non-empty explicit Redis URL, if this run selected one."""
    return environ.get("AGENTICORG_REDIS_URL") or environ.get("REDIS_URL") or None
