# SPDX-License-Identifier: Apache-2.0
"""Requests for more information: approved templates only, released only by a human.

The underwriter may *propose* asking the applicant for missing items. A proposal names an approved
template and item codes from the memo's missing-items list - nothing else. The request text is
rendered only from the template and its fixed item labels, so no model-written or
applicant-supplied text can reach the applicant. Rendering needs a human approval bound to the
exact proposal digest, taken at a LangGraph interrupt (:func:`build_information_request_gate`), so
a paused request survives a restart with a durable checkpointer and cannot be released by the
agent. Delivery is the case hand-off's job, not the agent's: the agent has no send tool.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class InformationRequestError(ValueError):
    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class InformationRequestTemplate:
    template_id: str
    version: str
    #: Who approved the wording for use. Templates without a reviewer are refused.
    approved_by: str
    subject: str
    introduction: str
    item_labels: Mapping[str, str]
    closing: str

    @property
    def sha256(self) -> str:
        return _digest(
            {
                "template_id": self.template_id,
                "version": self.version,
                "approved_by": self.approved_by,
                "subject": self.subject,
                "introduction": self.introduction,
                "item_labels": dict(sorted(self.item_labels.items())),
                "closing": self.closing,
            }
        )


def _digest(value: Any) -> str:
    return (
        "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    )


#: The example template shipped with the reference agent. Operators register their own reviewed wording.
ONBOARDING_MISSING_ITEMS = InformationRequestTemplate(
    template_id="onboarding_missing_items",
    version="1.0.0",
    approved_by="example-template-requires-review",
    subject="Information needed to continue your application",
    introduction="To continue reviewing your business application we need the following:",
    item_labels={
        "registry_record": "A current registration document for the business.",
        "owner_date_of_birth": "The date of birth of each individual owner.",
        "tax_identifier": "The business's federal employer identification number.",
        "ownership_information": "A description of who owns and controls the business.",
    },
    closing="Please reply through the secure channel you used to apply.",
)

APPROVED_TEMPLATES: dict[tuple[str, str], InformationRequestTemplate] = {
    (ONBOARDING_MISSING_ITEMS.template_id, ONBOARDING_MISSING_ITEMS.version): ONBOARDING_MISSING_ITEMS,
}


def _template(template_id: str, version: str) -> InformationRequestTemplate:
    template = APPROVED_TEMPLATES.get((template_id, version))
    if template is None:
        raise InformationRequestError("template_not_approved", f"{template_id}@{version}")
    if not template.approved_by.strip():
        raise InformationRequestError("template_unreviewed", f"{template_id}@{version}")
    return template


def propose(memo: Mapping[str, Any], *, template_id: str, version: str) -> dict[str, Any]:
    """A proposal from the memo's missing items. Refused unless the memo recommends requesting information."""
    template = _template(template_id, version)
    if memo.get("recommendation", {}).get("proposed") != "request_information":
        raise InformationRequestError("not_recommended", "the memo does not recommend requesting information")
    codes = [item["item"] for item in memo.get("missing_items", ())]
    items = sorted(code for code in set(codes) if code in template.item_labels)
    unsupported = sorted(code for code in set(codes) if code not in template.item_labels)
    if not items:
        raise InformationRequestError("no_template_items", "no missing item is covered by the template")
    body = {
        "case_id": memo["case_id"],
        "memo_id": memo["memo_id"],
        "template_id": template.template_id,
        "template_version": template.version,
        "template_sha256": template.sha256,
        "items": items,
    }
    return {**body, "unsupported_items": unsupported, "proposal_sha256": _digest(body)}


def render(proposal: Mapping[str, Any], approval: Mapping[str, Any]) -> dict[str, Any]:
    """The request text, only for a proposal a human approved by digest."""
    body = {
        key: proposal[key]
        for key in ("case_id", "memo_id", "template_id", "template_version", "template_sha256", "items")
    }
    digest = _digest(body)
    if proposal.get("proposal_sha256") != digest:
        raise InformationRequestError("proposal_tampered")
    if approval.get("action") != "approve":
        raise InformationRequestError("not_approved")
    approver = approval.get("approver_id")
    if not isinstance(approver, str) or not approver.strip() or approver.startswith("agent:"):
        raise InformationRequestError("approver_invalid")
    if approval.get("proposal_sha256") != digest:
        raise InformationRequestError("approval_digest_mismatch")
    template = _template(str(body["template_id"]), str(body["template_version"]))
    if template.sha256 != body["template_sha256"]:
        raise InformationRequestError("template_changed")
    lines = [template.introduction, *(f"- {template.item_labels[item]}" for item in body["items"]), template.closing]
    return {
        "case_id": body["case_id"],
        "template_id": template.template_id,
        "template_version": template.version,
        "items": list(body["items"]),
        "subject": template.subject,
        "body": "\n".join(lines),
        "approved_by": approver,
        "proposal_sha256": digest,
    }


class InformationRequestState(TypedDict, total=False):
    memo: dict[str, Any]
    template_id: str
    template_version: str
    proposal: dict[str, Any]
    request: dict[str, Any] | None
    status: str
    reason: str


def build_information_request_gate() -> StateGraph:
    """``propose -> await_approval (interrupt) -> END``. Compile with a checkpointer to pause and resume.

    Resume with ``Command(resume={"action": "approve" | "reject", "approver_id": ..., "proposal_sha256": ...})``.
    The final state has ``status`` ``approved`` with the rendered ``request``, or ``rejected`` or
    ``refused`` with a reason.
    """

    def propose_node(state: InformationRequestState) -> dict[str, Any]:
        try:
            proposal = propose(state["memo"], template_id=state["template_id"], version=state["template_version"])
        except InformationRequestError as exc:
            return {"status": "refused", "reason": exc.reason, "request": None}
        return {"proposal": proposal, "status": "awaiting_approval"}

    def await_approval(state: InformationRequestState) -> dict[str, Any]:
        if state.get("status") == "refused":
            return {}
        proposal = state["proposal"]
        decision = interrupt({"type": "information_request_approval", "proposal": proposal})
        if not isinstance(decision, Mapping) or decision.get("action") != "approve":
            return {"status": "rejected", "reason": "rejected_by_human", "request": None}
        try:
            request = render(proposal, decision)
        except InformationRequestError as exc:
            return {"status": "refused", "reason": exc.reason, "request": None}
        return {"status": "approved", "request": request}

    graph = StateGraph(InformationRequestState)
    graph.add_node("propose", propose_node)
    graph.add_node("await_approval", await_approval)
    graph.add_edge(START, "propose")
    graph.add_edge("propose", "await_approval")
    graph.add_edge("await_approval", END)
    return graph
