# SPDX-License-Identifier: Apache-2.0
"""Requests for more information go out only through an approved template behind a human gate."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from core.agents.business_underwriter import information_request as rfi
from core.agents.business_underwriter.information_request import (
    ONBOARDING_MISSING_ITEMS,
    InformationRequestError,
    build_information_request_gate,
    propose,
    render,
)

TEMPLATE = (ONBOARDING_MISSING_ITEMS.template_id, ONBOARDING_MISSING_ITEMS.version)


def _memo(
    proposed: str = "request_information", items: tuple[str, ...] = ("owner_date_of_birth", "screening_results")
) -> dict[str, Any]:
    return {
        "memo_id": "memo-case-1",
        "case_id": "case-1",
        "recommendation": {"proposed": proposed, "basis": "policy_result", "requires_human_decision": True},
        "missing_items": [
            {"item": item, "reason": "Model or applicant text that must never be sent."} for item in items
        ],
    }


def test_proposal_names_only_template_items_and_reports_the_rest() -> None:
    proposal = propose(_memo(), template_id=TEMPLATE[0], version=TEMPLATE[1])
    assert proposal["items"] == ["owner_date_of_birth"]
    assert proposal["unsupported_items"] == ["screening_results"]
    assert proposal["template_sha256"] == ONBOARDING_MISSING_ITEMS.sha256


@pytest.mark.parametrize(
    ("memo", "template", "reason"),
    [
        (_memo(proposed="refer"), TEMPLATE, "not_recommended"),
        (_memo(items=("screening_results",)), TEMPLATE, "no_template_items"),
        (_memo(), ("free_text", "1.0.0"), "template_not_approved"),
    ],
)
def test_proposals_are_refused_outside_their_conditions(
    memo: dict[str, Any], template: tuple[str, str], reason: str
) -> None:
    with pytest.raises(InformationRequestError) as refused:
        propose(memo, template_id=template[0], version=template[1])
    assert refused.value.reason == reason


def test_unreviewed_template_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    unreviewed = rfi.InformationRequestTemplate(**{**ONBOARDING_MISSING_ITEMS.__dict__, "approved_by": " "})
    monkeypatch.setitem(rfi.APPROVED_TEMPLATES, TEMPLATE, unreviewed)
    with pytest.raises(InformationRequestError, match="template_unreviewed"):
        propose(_memo(), template_id=TEMPLATE[0], version=TEMPLATE[1])


def test_rendered_text_comes_only_from_the_template() -> None:
    proposal = propose(_memo(), template_id=TEMPLATE[0], version=TEMPLATE[1])
    request = render(
        proposal, {"action": "approve", "approver_id": "user:analyst-a", "proposal_sha256": proposal["proposal_sha256"]}
    )
    assert "must never be sent" not in request["body"]
    assert request["body"].splitlines() == [
        ONBOARDING_MISSING_ITEMS.introduction,
        "- " + ONBOARDING_MISSING_ITEMS.item_labels["owner_date_of_birth"],
        ONBOARDING_MISSING_ITEMS.closing,
    ]
    assert request["approved_by"] == "user:analyst-a"


@pytest.mark.parametrize(
    ("change", "approval", "reason"),
    [
        ({"items": ["owner_date_of_birth", "tax_identifier"]}, {}, "proposal_tampered"),
        ({}, {"action": "reject"}, "not_approved"),
        ({}, {"approver_id": "agent:business_underwriter"}, "approver_invalid"),
        ({}, {"approver_id": ""}, "approver_invalid"),
        ({}, {"proposal_sha256": "sha256:" + "0" * 64}, "approval_digest_mismatch"),
    ],
)
def test_render_refuses_anything_but_a_human_approval_of_that_exact_proposal(change, approval, reason) -> None:
    proposal = propose(_memo(), template_id=TEMPLATE[0], version=TEMPLATE[1])
    decision = {
        "action": "approve",
        "approver_id": "user:analyst-a",
        "proposal_sha256": proposal["proposal_sha256"],
        **approval,
    }
    with pytest.raises(InformationRequestError) as refused:
        render({**proposal, **change}, decision)
    assert refused.value.reason == reason


def test_render_refuses_when_the_template_wording_changed_after_proposal(monkeypatch: pytest.MonkeyPatch) -> None:
    proposal = propose(_memo(), template_id=TEMPLATE[0], version=TEMPLATE[1])
    reworded = rfi.InformationRequestTemplate(**{**ONBOARDING_MISSING_ITEMS.__dict__, "closing": "Reply by email."})
    monkeypatch.setitem(rfi.APPROVED_TEMPLATES, TEMPLATE, reworded)
    with pytest.raises(InformationRequestError, match="template_changed"):
        render(proposal, {"action": "approve", "approver_id": "user:a", "proposal_sha256": proposal["proposal_sha256"]})


async def test_gate_pauses_for_a_human_and_releases_only_on_approval() -> None:
    graph = build_information_request_gate().compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "tenant:t1:run:rfi-1"}}
    paused = await graph.ainvoke({"memo": _memo(), "template_id": TEMPLATE[0], "template_version": TEMPLATE[1]}, config)
    [pending] = paused["__interrupt__"]
    assert pending.value["type"] == "information_request_approval"
    assert paused.get("request") is None and paused["status"] == "awaiting_approval"

    digest = pending.value["proposal"]["proposal_sha256"]
    done = await graph.ainvoke(
        Command(resume={"action": "approve", "approver_id": "user:analyst-a", "proposal_sha256": digest}), config
    )
    assert done["status"] == "approved" and done["request"]["items"] == ["owner_date_of_birth"]


async def test_gate_rejection_or_a_forged_approval_releases_nothing() -> None:
    graph = build_information_request_gate().compile(checkpointer=MemorySaver())
    for thread, decision, status in (
        ("rfi-2", {"action": "reject", "approver_id": "user:analyst-a"}, "rejected"),
        (
            "rfi-3",
            {"action": "approve", "approver_id": "agent:business_underwriter", "proposal_sha256": "x"},
            "refused",
        ),
    ):
        config = {"configurable": {"thread_id": thread}}
        await graph.ainvoke({"memo": _memo(), "template_id": TEMPLATE[0], "template_version": TEMPLATE[1]}, config)
        done = await graph.ainvoke(Command(resume=decision), config)
        assert done["status"] == status and done["request"] is None


async def test_gate_refuses_without_pausing_when_no_request_is_warranted() -> None:
    graph = build_information_request_gate().compile(checkpointer=MemorySaver())
    done = await graph.ainvoke(
        {"memo": _memo(proposed="approve"), "template_id": TEMPLATE[0], "template_version": TEMPLATE[1]},
        {"configurable": {"thread_id": "rfi-4"}},
    )
    assert done["status"] == "refused" and done["reason"] == "not_recommended" and "__interrupt__" not in done
