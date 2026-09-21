# SPDX-License-Identifier: Apache-2.0
"""What each committed alert reads, declared once so it cannot silently stop being emitted.

The gap this closes is A-56's mirror image. A-56 was instruments nobody could read; the failure
that replaces it is an alert whose metric disappears in a refactor, which looks exactly like an
alert that is healthy and never fires. Every alert in ``monitoring/prometheus/agenticorg-alerts.yml``
names the instruments it depends on here; ``scripts/check_alert_rules.py`` refuses a rule that
reads anything not declared, and ``tests/unit/observability/test_alert_instruments.py`` exercises
the code paths and fails if a declared instrument is no longer in the registry.

Nothing here is a threshold. Thresholds belong in the rules file, where an operator can see and
change them without touching Python.
"""

from __future__ import annotations

from typing import Final

#: alert name -> the metric families its expression may read.
ALERT_INSTRUMENTS: Final[dict[str, tuple[str, ...]]] = {
    # PRD §10, the six.
    "AgenticOrgDenialRateSpike": ("agenticorg_grant_enforcement_denials_total",),
    "AgenticOrgDecisionDwellCollapse": ("agenticorg_case_decision_dwell_seconds",),
    "AgenticOrgSpendCapExhaustion": ("agenticorg_budget_cap_events_total",),
    "AgenticOrgProviderErrorRate": ("agenticorg_provider_calls_total",),
    "AgenticOrgPushDeadLetterGrowth": (
        "agenticorg_case_push_dead_letters_total",
        "agenticorg_case_push_dead_letter_backlog",
    ),
    "AgenticOrgChainVerificationFailure": ("agenticorg_chain_verifications_total",),
    # The companion: a dwell series that stops arriving and a dwell that collapses to nothing are
    # different incidents, and the silent one is the dangerous one.
    "AgenticOrgAuthoritativeDwellMissing": (
        "agenticorg_case_decision_dwell_seconds",
        "agenticorg_governed_case_transitions_total",
    ),
}

#: Every instrument any alert reads.
REQUIRED_INSTRUMENTS: Final[tuple[str, ...]] = tuple(
    sorted({name for names in ALERT_INSTRUMENTS.values() for name in names})
)

#: Series suffixes a histogram family exposes, so a rule may read ``..._count`` or ``..._bucket``.
HISTOGRAM_SUFFIXES: Final[tuple[str, ...]] = ("_bucket", "_count", "_sum")
