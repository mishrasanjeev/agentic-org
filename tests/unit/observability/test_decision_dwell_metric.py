# SPDX-License-Identifier: Apache-2.0
"""The authoritative dwell: the one measured by the approval page, not by a browser.

The rubber-stamping alert stands or falls on this series. The console's own render-to-submit
figure measures how fast a form was posted, which constrains nobody; the issuer measures the real
thing on its own page and reports it with ``dwell_source: server``. These tests hold the two
properties the alert depends on: the issuer's figure is recorded under the source it came from,
and an approval the issuer did not measure never quietly becomes a fast one.
"""

from __future__ import annotations

from prometheus_client import REGISTRY

from core.cases.decision_requests import Approval, DecisionRequestView, record_decision_dwell


def _view(*approvals: Approval) -> DecisionRequestView:
    return DecisionRequestView(
        request_id="dr_test",
        status="approved",
        approval_page="https://issuer.example.com/decisions/dr_test",
        action={"type": "governed_case.decide"},
        action_hash="a" * 64,
        case_version="4",
        approvals_required=len(approvals) or 1,
        approvals=approvals,
    )


def _count(dwell_source: str, approval_stage: str) -> float:
    return (
        REGISTRY.get_sample_value(
            "agenticorg_case_decision_dwell_seconds_count",
            {"dwell_source": dwell_source, "approval_stage": approval_stage},
        )
        or 0.0
    )


def _approval(position: int, dwell_ms: int | None, source: str) -> Approval:
    return Approval(
        approver="analyst-one",
        approver_auth="mfa",
        dwell_ms=dwell_ms,
        dwell_source=source,
        position=position,
        issued_at="2026-09-21T10:00:00Z",
    )


def test_the_issuer_measured_dwell_of_each_approval_is_recorded() -> None:
    before_first = _count("server", "first")
    before_second = _count("server", "second")

    record_decision_dwell(_view(_approval(1, 61_250, "server"), _approval(2, 44_000, "server")))

    assert _count("server", "first") == before_first + 1
    assert _count("server", "second") == before_second + 1


def test_a_dwell_the_issuer_did_not_measure_is_not_recorded_as_authoritative() -> None:
    """Otherwise a client-supplied number would answer the question 'did anyone read this?'."""
    before = _count("server", "first")

    record_decision_dwell(_view(_approval(1, 900, "client")))

    assert _count("server", "first") == before
    assert _count("client", "first") >= 1


def test_an_approval_with_no_dwell_at_all_records_nothing() -> None:
    """A missing measurement must not become a zero: zero is the alarming value."""
    before = _count("unknown", "first")

    record_decision_dwell(_view(_approval(1, None, "")))

    assert _count("unknown", "first") == before
