# SPDX-License-Identifier: Apache-2.0
"""An in-memory stand-in for the async Redis calls core/commerce/artifact_freshness.py makes."""

from __future__ import annotations

import fnmatch
import time
from typing import Any


class FakeRedis:
    """The few async Redis calls the module makes, with key expiry on the wall clock."""

    def __init__(self) -> None:
        self.values: dict[str, tuple[Any, float | None]] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    def _live(self, key: str) -> bool:
        entry = self.values.get(key)
        if entry is None:
            return False
        if entry[1] is not None and entry[1] <= time.time():
            del self.values[key]
            return False
        return True

    async def set(self, key: str, value: Any, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and self._live(key):
            return False
        self.values[key] = (value, time.time() + ex if ex else None)
        return True

    async def get(self, key: str) -> Any:
        return self.values[key][0] if self._live(key) else None

    async def exists(self, *keys: str) -> int:
        return sum(1 for key in keys if self._live(key))

    async def delete(self, key: str) -> int:
        return 1 if self.values.pop(key, None) is not None else 0

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def zremrangebyscore(self, key: str, low: Any, high: float) -> int:
        members = self.zsets.get(key, {})
        doomed = [m for m, score in members.items() if score <= high]
        for member in doomed:
            del members[member]
        return len(doomed)

    async def zrangebyscore(self, key: str, low: float, high: Any, *, start: int = 0, num: int = 100) -> list[str]:
        members = self.zsets.get(key, {})
        return [m for m, score in sorted(members.items(), key=lambda i: i[1]) if score >= low][start : start + num]

    def keys(self, pattern: str) -> list[str]:
        return [key for key in list(self.values) if fnmatch.fnmatch(key, pattern) and self._live(key)]
