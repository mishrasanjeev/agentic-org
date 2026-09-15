# SPDX-License-Identifier: Apache-2.0
"""The governed case lifecycle.

::

    submitted ──► in_progress ──► awaiting_decision ──► decided
        │             │   ▲              │   │
        │             ▼   │              │   └──► withdrawn
        │           failed ──────────────┘ (re-investigation: awaiting_decision ──► in_progress)
        └──────────────────────────────────────────► withdrawn

``decided`` and ``withdrawn`` are terminal. Only a human decision backed by a verified decision
grant reaches ``decided`` (``core.cases.decisions``); no agent path does. Every transition is
recorded in ``governed_case_transitions`` and counted in
``agenticorg_governed_case_transitions_total{from_state,to_state}``.
"""

from __future__ import annotations

from enum import StrEnum

from prometheus_client import Counter


class CaseState(StrEnum):
    SUBMITTED = "submitted"
    IN_PROGRESS = "in_progress"
    AWAITING_DECISION = "awaiting_decision"
    DECIDED = "decided"
    WITHDRAWN = "withdrawn"
    FAILED = "failed"


TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    CaseState.SUBMITTED: frozenset({CaseState.IN_PROGRESS, CaseState.WITHDRAWN}),
    CaseState.IN_PROGRESS: frozenset({CaseState.AWAITING_DECISION, CaseState.FAILED}),
    CaseState.AWAITING_DECISION: frozenset({CaseState.DECIDED, CaseState.WITHDRAWN, CaseState.IN_PROGRESS}),
    CaseState.FAILED: frozenset({CaseState.IN_PROGRESS, CaseState.WITHDRAWN}),
    CaseState.DECIDED: frozenset(),
    CaseState.WITHDRAWN: frozenset(),
}
TERMINAL: frozenset[CaseState] = frozenset(state for state, targets in TRANSITIONS.items() if not targets)

case_transitions_total = Counter(
    "agenticorg_governed_case_transitions_total",
    "Governed case state transitions, by from and to state",
    ["from_state", "to_state"],
)


class CaseError(ValueError):
    """A case operation was refused. ``reason`` is a stable code; ``status`` the HTTP status to answer with."""

    def __init__(self, reason: str, detail: str = "", *, status: int = 409) -> None:
        self.reason = reason
        self.detail = detail
        self.status = status
        super().__init__(f"{reason}: {detail}" if detail else reason)


def check_transition(current: str, target: CaseState) -> CaseState:
    """Return ``current`` as a :class:`CaseState` when ``target`` may follow it; raise otherwise."""
    try:
        state = CaseState(current)
    except ValueError as exc:
        raise CaseError("case_state_unknown", current) from exc
    if target not in TRANSITIONS[state]:
        raise CaseError("transition_not_allowed", f"{state.value} -> {target.value}")
    return state
