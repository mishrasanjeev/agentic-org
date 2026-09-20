# SPDX-License-Identifier: Apache-2.0
"""Record an analyst's review of a proposed screening disposition.

The agent only proposes. :func:`apply_review` is what the approvals console (or an operator's own
system) calls when a human accepts or overrides a proposal; it never runs inside the agent. It
records the analyst identity - which the caller takes from the authenticated session, never from
the request body - the final outcome and, for an override, a free-text reason. A review is written
once: a reviewed disposition cannot be reviewed again through this path.

Recording a review does not close the hit in any system. Closing a hit is the analyst's action in
the operator's system of record.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.domain_schemas import DomainSchemaError, validate

Outcome = Literal["true_match", "false_positive", "insufficient_information"]
MAX_REASON_CHARS = 4000


class DispositionReviewError(ValueError):
    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


class DispositionReviewRequest(BaseModel):
    """What an analyst submits. Identity and time are added by the server."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["accepted", "overridden"]
    final_outcome: Outcome
    reason: str | None = Field(default=None, max_length=MAX_REASON_CHARS)


def apply_review(
    disposition: Mapping[str, Any],
    request: DispositionReviewRequest | Mapping[str, Any],
    *,
    analyst_id: str,
    reviewed_at: datetime,
) -> dict[str, Any]:
    """Return a copy of ``disposition`` with its review recorded, validated against the schema.

    Refused (``DispositionReviewError.reason``): ``disposition_invalid``, ``already_reviewed``,
    ``request_invalid``, ``analyst_invalid`` (empty, or an agent identity), ``reviewed_at_naive``,
    ``accepted_outcome_differs`` (accepting must keep the proposed outcome),
    ``override_outcome_unchanged`` and ``override_reason_required``.
    """
    try:
        validate("screening_disposition", disposition)
    except DomainSchemaError as exc:
        raise DispositionReviewError("disposition_invalid", "; ".join(exc.errors[:3])) from exc
    if disposition.get("review") is not None:
        raise DispositionReviewError("already_reviewed")
    try:
        review = (
            request
            if isinstance(request, DispositionReviewRequest)
            else DispositionReviewRequest.model_validate(request)
        )
    except ValidationError as exc:
        raise DispositionReviewError("request_invalid", str(exc.errors()[0].get("msg", ""))) from exc
    analyst = analyst_id.strip() if isinstance(analyst_id, str) else ""
    if not analyst or analyst.startswith("agent:") or len(analyst) > 256:
        raise DispositionReviewError("analyst_invalid")
    if reviewed_at.tzinfo is None:
        raise DispositionReviewError("reviewed_at_naive")

    proposed = disposition["proposed_outcome"]
    reason = (review.reason or "").strip() or None
    if review.action == "accepted" and review.final_outcome != proposed:
        raise DispositionReviewError("accepted_outcome_differs")
    if review.action == "overridden":
        if review.final_outcome == proposed:
            raise DispositionReviewError("override_outcome_unchanged")
        if reason is None:
            raise DispositionReviewError("override_reason_required")

    reviewed = dict(disposition)
    reviewed["review"] = {
        "action": review.action,
        "final_outcome": review.final_outcome,
        "analyst_id": analyst,
        "reviewed_at": reviewed_at.isoformat(),
        "reason": reason,
    }
    validate("screening_disposition", reviewed)
    return reviewed
