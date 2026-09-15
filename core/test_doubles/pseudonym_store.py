# SPDX-License-Identifier: Apache-2.0
"""In-memory pseudonym map store for tests.

Behaves like ``core.pii.pseudonymiser.DatabasePseudonymMapStore`` without a
database: every write is serialised and every read parsed back, so tests go
through the same map encoding as production, and a fresh
``PseudonymSession`` on the same store sees what an earlier one wrote (as a
restarted process would). ``fail_next`` makes the next read or write raise
the error the database store raises.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.pii.pseudonymiser import PseudonymisationError, PseudonymMap


class InMemoryPseudonymMapStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], str] = {}
        self.fail_next: str | None = None
        self.writes = 0

    def _maybe_fail(self) -> None:
        if self.fail_next is not None:
            reason, self.fail_next = self.fail_next, None
            raise PseudonymisationError(reason)

    async def load(self, tenant_id: str, case_id: str) -> PseudonymMap | None:
        self._maybe_fail()
        raw = self.rows.get((tenant_id, case_id))
        return PseudonymMap.from_json(raw) if raw is not None else None

    async def add(self, tenant_id: str, case_id: str, additions: Sequence[tuple[str, str]]) -> PseudonymMap:
        self._maybe_fail()
        raw = self.rows.get((tenant_id, case_id))
        pmap = PseudonymMap.from_json(raw) if raw is not None else PseudonymMap.new()
        for value, entity in additions:
            pmap.token_for(value, entity)
        self.rows[(tenant_id, case_id)] = pmap.to_json()
        self.writes += 1
        return PseudonymMap.from_json(self.rows[(tenant_id, case_id)])
