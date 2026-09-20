# SPDX-License-Identifier: Apache-2.0
"""Governed business cases: lifecycle, persistence, agent runtime and human decisions (PRD A-8).

See ``docs/governance/case-lifecycle.md``.
"""

from __future__ import annotations

from core.cases.states import TERMINAL, TRANSITIONS, CaseError, CaseState, check_transition

__all__ = ["TERMINAL", "TRANSITIONS", "CaseError", "CaseState", "check_transition"]
