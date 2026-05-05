"""Regression tests for the Uday CA Firms 2026-04-26 PM bug sweep.

Triage matrix in
``CA_FIRMS_BugFixSummary_Uday_26Apr2026_PM.xlsx`` (alongside the input).
Brutal autopsy in
``~/.claude/.../memory/feedback_26apr_pm_bug_sweep.md``.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# BUG 1 — duplicate "Client Secret" field on connector update form
# ---------------------------------------------------------------------------


def test_bug1_no_duplicate_client_secret_for_oauth2() -> None:
    """When authType is oauth2, the Edit form must render exactly one
    "Client Secret" input.

    Before this PR: the OAuth2 panel had an explicit Client Secret
    input, AND the generic ``authTypeLabel(authType)`` helper rendered
    a SECOND identically-labelled input below (because
    ``authTypeLabel("oauth2") === "Client Secret"``). Two visually
    identical fields with different state bindings — the explicit
    field's value won at save time, but the duplicate was a real UX
    bug (admins didn't know which was which).

    Pin the contract: the generic helper field must be conditional on
    ``authType !== "oauth2"``.
    """
    src = (REPO / "ui" / "src" / "pages" / "ConnectorDetail.tsx").read_text(
        encoding="utf-8"
    )

    # The generic helper field must be guarded
    assert 'authType !== "oauth2"' in src, (
        "Generic credential helper field must be skipped when the "
        "OAuth2-specific Client Secret/authorization block is in play "
        "— otherwise both inputs render with the same 'Client Secret' "
        "label."
    )
    # The explicit OAuth2 Client Secret input must still exist (the
    # OAuth2 panel needs it to mint access tokens at runtime).
    assert "oauth2ClientSecret" in src
    assert 'placeholder="Enter new client secret' in src


# ---------------------------------------------------------------------------
# BUG 2 — UI shows "Connection test failed" when backend says healthy
# ---------------------------------------------------------------------------


def test_bug2_connection_test_reads_health_status_not_data_success() -> None:
    """``handleTestConnection`` must dispatch on ``data.health.status``,
    not on a non-existent ``data.success`` field.

    The ``POST /connectors/{id}/test`` backend response shape is
    ``{tested: bool, name: str, health: {status: str, http_status?: int,
    reason?: str}, error?: str}``. Reading ``data.success`` is always
    ``undefined`` → ``error`` branch → tester sees "Connection test
    failed" even when the connector returned ``status: "healthy"``.
    Bug reported by Uday CA Firms 2026-04-26.
    """
    src = (REPO / "ui" / "src" / "pages" / "ConnectorDetail.tsx").read_text(
        encoding="utf-8"
    )

    # Confirm the handler exists
    assert "async function handleTestConnection" in src

    # The fix: branch on data.health.status — the real backend shape.
    assert "data?.health?.status" in src, (
        "Test-connection result handler must read data?.health?.status "
        "(the real response shape from POST /connectors/{id}/test)"
    )
    # The misleading data.success branch must be gone from this file
    # (the only place it appeared was the buggy handleTestConnection).
    assert "data.success" not in src, (
        "data.success was a fictional field — the previous handler "
        "always saw it as undefined and showed 'Connection test failed' "
        "even on healthy responses"
    )


# ---------------------------------------------------------------------------
# BUG (major) part B — Generate Test Samples must default to 1 + allow Stop
# ---------------------------------------------------------------------------


def test_bug_major_b_shadow_sample_batch_is_user_controlled() -> None:
    """The Shadow tab's "Generate Test Sample(s)" button must:

    - default the per-click batch to 1 (NOT auto-run 10)
    - expose an input for batch size (1–10)
    - render a "Stop" button while a batch is in flight
    - check a stop signal between samples in the loop

    The previous behaviour was a hardcoded
    ``Math.min(gap > 0 ? gap : 1, 10)`` calculation that auto-batched
    up to 10 sequential samples per click. Tester reported it as
    'no manual control'.
    """
    src = (REPO / "ui" / "src" / "pages" / "AgentDetail.tsx").read_text(
        encoding="utf-8"
    )

    # batchSize state with default 1
    assert re.search(
        r"useState\(1\)[^\n]*\n[\s\S]*?// Stop signal",
        src,
    ) or "useState(1)" in src, (
        "batchSize state must exist and default to 1"
    )
    # stopRequestedRef
    assert "stopRequestedRef" in src, "Stop signal ref must exist"
    # input for batch size
    assert re.search(
        r'aria-label="Samples per click"',
        src,
    ), "Batch-size number input must have aria-label='Samples per click'"
    # Stop button
    assert re.search(
        r">\s*Stop\s*</Button>",
        src,
    ), "Stop button must render while generating"
    # Loop must check stopRequestedRef
    assert "stopRequestedRef.current" in src, (
        "The generate loop must read the stop signal between samples"
    )


# ---------------------------------------------------------------------------
# BUG (major) part A — shadow accuracy <40% — STILL Unverified (carryover)
# ---------------------------------------------------------------------------
#
# This contract has not changed since yesterday's sheet (Uday 2026-04-25).
# Without a specific failing agent_id + 5+ shadow run records (msg_*),
# root cause cannot be isolated — see the Unverified verdict in the
# previous summary xlsx and feedback_26apr_bug_sweep.md.
#
# Not asserted in code today. Documented here as a sweep marker.
