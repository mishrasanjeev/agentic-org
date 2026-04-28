"""Hermetic fake Celery (Foundation #7 PR-E).

Two related capabilities:

1. **Eager mode**: when ``AGENTICORG_TEST_FAKE_CELERY=1`` is set,
   ``core.tasks.celery_app:app`` flips on
   ``task_always_eager=True`` + ``task_eager_propagates=True``
   so every ``.delay(...)`` / ``.apply_async(...)`` runs
   synchronously in the calling process. No Redis, no worker,
   no broker connection.

2. **Invocation capture**: a Celery signal handler records every
   task that runs (name + args + kwargs) so tests can assert
   "the X task was enqueued with these arguments" without
   inspecting Redis. Captures fire whether or not the task body
   is mocked elsewhere.

Inspection::

    from core.test_doubles import fake_celery

    fake_celery.invocations()           # list of CapturedTask
    fake_celery.last()                  # most-recent
    fake_celery.count()                 # total
    fake_celery.find(task_name="...")   # filter by name
    fake_celery.reset()                 # clear

The autouse fixture in tests/conftest.py calls ``reset()`` before
each test so captures don't leak.

Why eager mode AND capture (instead of just one): eager runs the
real task body — that's what unit tests want when they need to
assert side-effects. Capture lets tests that DON'T want the body
to run (because it has its own deps) still assert "the task was
scheduled".

Disabling eager mode for one test that needs a real worker
round-trip:

    def test_real_celery(monkeypatch):
        monkeypatch.delenv("AGENTICORG_TEST_FAKE_CELERY", raising=False)
        # ... requires a running redis + worker — integration only ...
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CapturedTask:
    """One captured task invocation."""

    name: str
    args: tuple = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


_INVOCATIONS: list[CapturedTask] = []


def is_active() -> bool:
    """True iff ``AGENTICORG_TEST_FAKE_CELERY`` is truthy."""
    return os.getenv("AGENTICORG_TEST_FAKE_CELERY", "").lower() in (
        "1",
        "true",
        "yes",
    )


def _record(name: str, args: tuple = (), kwargs: dict[str, Any] | None = None) -> None:
    """Internal: append one invocation. Called from the celery
    signal handler in ``core.tasks.celery_app``."""
    _INVOCATIONS.append(
        CapturedTask(name=name, args=tuple(args or ()), kwargs=dict(kwargs or {}))
    )


def invocations() -> list[CapturedTask]:
    """Return a copy of every captured task, oldest first."""
    return list(_INVOCATIONS)


def last() -> CapturedTask | None:
    """Return the most-recent captured task, or None if empty."""
    return _INVOCATIONS[-1] if _INVOCATIONS else None


def count() -> int:
    """Total tasks captured since the last reset."""
    return len(_INVOCATIONS)


def find(*, task_name: str) -> list[CapturedTask]:
    """Return all captured tasks whose ``name`` equals ``task_name``."""
    return [t for t in _INVOCATIONS if t.name == task_name]


def reset() -> None:
    """Clear the invocation list. Call from a test fixture to keep
    captures isolated between cases."""
    _INVOCATIONS.clear()
