# SPDX-License-Identifier: Apache-2.0
"""An in-memory decision service for tests (PRD G-3).

It behaves like the Grantex auth service's decision-grant API as far as the platform can see it:
a request is created for one semantic action and answers an approval page; approvals arrive only
through :meth:`approve`, which stands in for a person approving on that page; four eyes needs two
different approvers and refuses the same one twice; grants appear only when the request is fully
approved; consumption is atomic, single-use and bound to the action and the case version.

It is a test double, never a production path: nothing in ``core`` or ``api`` constructs it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from core.cases.decision_requests import (
    Approval,
    ConsumedDecision,
    DecisionRequestView,
    DecisionServiceError,
)

APPROVAL_ORIGIN = "https://auth.grantex.invalid"


def _canonical(action: Mapping[str, Any]) -> str:
    return json.dumps(dict(action), sort_keys=True, separators=(",", ":"))


@dataclass
class _Request:
    request_id: str
    action: dict[str, Any]
    case_version: str
    approvals_required: int
    memo: str
    policy_score: dict[str, Any]
    status: str = "pending"
    approvals: list[Approval] = field(default_factory=list)
    grants: list[str] = field(default_factory=list)
    #: The grant ids (``jti``), which is what a consumed decision records - never the token.
    jtis: list[str] = field(default_factory=list)
    consumed: bool = False


@dataclass
class FakeDecisionGrantService:
    """Decision requests and grants, in memory."""

    requests: dict[str, _Request] = field(default_factory=dict)
    #: Raised by the next call, to exercise the platform's fail-closed paths.
    fail_with: DecisionServiceError | None = None
    _sequence: int = 0

    # ── the platform's side ──────────────────────────────────────────────────────────────────

    async def set_case_version(self, case_id: str, case_version: str) -> None:
        """What the issuer does with a new case version: supersede what is bound to an older one."""
        self._raise_if_asked()
        for request in self.requests.values():
            if (
                str(request.action.get("case_id")) == case_id
                and request.status in ("pending", "approved")
                and request.case_version != case_version
            ):
                request.status = "superseded"
                request.grants.clear()
                request.jtis.clear()

    async def create_request(
        self,
        *,
        action: Mapping[str, Any],
        case_version: str,
        memo: str,
        policy_score: Mapping[str, Any],
        four_eyes_on: Sequence[str],
        memo_ref: str = "",
        policy_score_ref: str = "",
    ) -> DecisionRequestView:
        self._raise_if_asked()
        await self.set_case_version(str(action["case_id"]), case_version)
        for existing in self.requests.values():
            if (
                existing.status in ("pending", "approved")
                and _canonical(existing.action) == _canonical(action)
                and existing.case_version == case_version
            ):
                return self._view(existing)
        self._sequence += 1
        request = _Request(
            request_id=f"dr_{self._sequence:08d}",
            action=dict(action),
            case_version=case_version,
            approvals_required=2 if str(action.get("decision")) in set(four_eyes_on) else 1,
            memo=memo,
            policy_score=dict(policy_score),
        )
        self.requests[request.request_id] = request
        return self._view(request)

    async def get_request(self, request_id: str) -> DecisionRequestView:
        self._raise_if_asked()
        return self._view(self._get(request_id))

    async def grants(self, request_id: str) -> list[str]:
        self._raise_if_asked()
        request = self._get(request_id)
        if request.status != "approved" or request.consumed:
            return []
        return list(request.grants)

    async def consume(
        self, *, grants: Sequence[str], action: Mapping[str, Any], case_version: str
    ) -> ConsumedDecision:
        self._raise_if_asked()
        request = next((r for r in self.requests.values() if set(grants) & set(r.grants)), None)
        if request is None or not grants:
            raise DecisionServiceError("decision_invalid", "unknown_grant", status=409)
        if request.consumed:
            raise DecisionServiceError("decision_invalid", "consumed", status=409)
        if len(set(grants)) != request.approvals_required:
            raise DecisionServiceError("decision_invalid", "four_eyes_incomplete", status=409)
        if _canonical(request.action) != _canonical(action):
            raise DecisionServiceError("decision_invalid", "action_mismatch", status=409)
        if request.case_version != case_version:
            raise DecisionServiceError("decision_invalid", "case_changed", status=409)
        request.consumed = True
        request.status = "consumed"
        return ConsumedDecision(
            request_id=request.request_id,
            approvers=tuple((a.approver, jti) for a, jti in zip(request.approvals, request.jtis, strict=False)),
            action_hash=f"sha256:{abs(hash(_canonical(action))):064x}"[:71],
        )

    # ── the approver's side (the auth service's approval page) ───────────────────────────────

    def approve(self, request_id: str, approver: str, *, dwell_ms: int = 61_250) -> DecisionRequestView:
        """What the approval page does when a person approves: step-up, dwell and four eyes."""
        request = self._get(request_id)
        if request.status not in ("pending",):
            raise DecisionServiceError("decision_invalid", "closed", status=409)
        if any(a.approver == approver for a in request.approvals):
            raise DecisionServiceError("decision_invalid", "same_approver", status=409)
        request.approvals.append(
            Approval(
                approver=approver,
                approver_auth="sso+webauthn",
                dwell_ms=dwell_ms,
                dwell_source="server",
                position=len(request.approvals) + 1,
                issued_at="2026-09-20T10:00:00Z",
            )
        )
        # A token and its ``jti`` are different things: only the id is ever recorded on a case.
        position = len(request.approvals)
        request.grants.append(f"decision+jwt.{request.request_id}.{position}.signature-not-a-real-token")
        request.jtis.append(f"jti-{request.request_id}-{position}")
        if len(request.approvals) >= request.approvals_required:
            request.status = "approved"
        return self._view(request)

    def supersede(self, request_id: str) -> None:
        """What registering a new case version does to an open request."""
        self._get(request_id).status = "superseded"

    # ── internals ────────────────────────────────────────────────────────────────────────────

    def _raise_if_asked(self) -> None:
        if self.fail_with is not None:
            error, self.fail_with = self.fail_with, None
            raise error

    def _get(self, request_id: str) -> _Request:
        request = self.requests.get(request_id)
        if request is None:
            raise DecisionServiceError("decision_request_not_found", request_id, status=404)
        return request

    @staticmethod
    def _view(request: _Request) -> DecisionRequestView:
        return DecisionRequestView(
            request_id=request.request_id,
            status=request.status,
            approval_page=f"{APPROVAL_ORIGIN}/decisions/{request.request_id}",
            action=dict(request.action),
            action_hash=f"sha256:{abs(hash(_canonical(request.action))):064x}"[:71],
            case_version=request.case_version,
            approvals_required=request.approvals_required,
            approvals=tuple(request.approvals),
            expires_at="2026-09-21T10:00:00Z",
        )
