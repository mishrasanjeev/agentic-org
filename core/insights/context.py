# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Which entry point started an agent run, for its run fact: a context variable, no I/O.

An entry point (chat, A2A, MCP, voice, a channel, an approval resume, the
debugging console, the sales pipeline) wraps its call into the runner in
``with entrypoint("chat"):``; the run-fact writer reads it with
``get_entrypoint``. A run started outside every such block is an ``agents``
run (``POST /agents/{id}/run``). The value is a fixed code, never request data.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar

current_entrypoint: ContextVar[str | None] = ContextVar("agenticorg_insights_entrypoint", default=None)


@contextlib.contextmanager
def entrypoint(name: str) -> Iterator[None]:
    """Mark the runs started inside this block as started by ``name``; the previous value is restored after."""
    token = current_entrypoint.set(str(name or "") or None)
    try:
        yield
    finally:
        current_entrypoint.reset(token)


def get_entrypoint(default: str = "agents") -> str:
    """The entry point bound for the current run, else ``default``."""
    return current_entrypoint.get() or default
