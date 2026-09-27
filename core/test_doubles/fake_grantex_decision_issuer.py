# SPDX-License-Identifier: Apache-2.0
"""The Grantex auth service's decision-grant endpoints over HTTP, for tests (PRD G-3).

:class:`FakeDecisionIssuer` answers ``httpx.MockTransport`` requests the way the auth service
answers a platform (Grantex ``spec/decision-grant.md`` sections 4 and 6, and its OpenAPI document),
in both states of the issuer's ``DECISION_GRANT_AGENT_BINDING``:

- off, ``GET /v1/decisions/requests/{id}`` carries ``decisionGrants`` (the tokens) once the request
  is fully approved and its grants are usable, and ``POST /v1/decisions/consume`` records the agent
  without comparing it;
- on, the status carries ``decisionGrantsReady`` and never the tokens, and the grants of a request
  that names an agent are consumed only when the caller names that agent and grant
  (``wrong_agent``, 403).

In both states ``POST /v1/decisions/requests/{id}/consume`` consumes a request that names no agent
by its id, and ``POST /v1/decisions/requests/{id}/grants`` releases a request's grants only to a
grant token of the agent it names. Approvals arrive only through :meth:`FakeDecisionIssuer.approve`,
which stands in for a person on the approval page. Every call is kept in
:attr:`FakeDecisionIssuer.calls`, so a test can say what the platform asked for and what it never
asked for.

It is a test double, never a production path: nothing in ``core`` or ``api`` constructs it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

ISSUER_ORIGIN = "https://auth.grantex.invalid"
_REQUEST_PATH = re.compile(r"^/v1/decisions/requests/([^/]+)(?:/(grants|consume|cancel))?$")
_CASE_PATH = re.compile(r"^/v1/decisions/cases/([^/]+)$")


def _canonical(action: Mapping[str, Any]) -> str:
    return json.dumps(dict(action), sort_keys=True, separators=(",", ":"))


def _code(status: int) -> str:
    return {400: "BAD_REQUEST", 404: "NOT_FOUND", 410: "DECISION_EXPIRED"}.get(status, "DECISION_INVALID")


class _RefusedError(Exception):
    def __init__(self, status: int, sub_reason: str) -> None:
        super().__init__(sub_reason)
        self.status = status
        self.sub_reason = sub_reason


@dataclass
class _Grant:
    jti: str
    sub: str
    position: int
    token: str
    dwell_ms: int
    consumed_at: str | None = None
    revoked_at: str | None = None
    revoked_reason: str | None = None


@dataclass
class _Request:
    request_id: str
    action: dict[str, Any]
    case_version: str
    approvals_required: int
    agent_id: str | None = None
    grant_id: str | None = None
    status: str = "pending"
    grants: list[_Grant] = field(default_factory=list)


@dataclass
class FakeDecisionIssuer:
    """Decision requests, approvals and grants, answered over HTTP as the auth service does."""

    #: The issuer's ``DECISION_GRANT_AGENT_BINDING``.
    binding: bool = False
    api_key: str = "test-only-key"
    #: The live grant token of the agent (``agent_id``, ``grant_id``) a bound request names.
    agent_grant_tokens: dict[str, tuple[str, str]] = field(default_factory=dict)
    requests: dict[str, _Request] = field(default_factory=dict)
    case_versions: dict[str, str] = field(default_factory=dict)
    #: ``(method, path, body)`` for every call, in order.
    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    _sequence: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client_factory(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport(), base_url=ISSUER_ORIGIN)

    # ── the approver's side (the approval page) ──────────────────────────────────────────────

    def approve(self, request_id: str, sub: str, *, dwell_ms: int = 61_250) -> None:
        """A person approving on the approval page: four eyes, one grant per approval."""
        request = self.requests[request_id]
        if request.status != "pending":
            raise ValueError(f"request is {request.status}")
        if any(g.sub == sub for g in request.grants):
            raise ValueError("same_approver")
        position = len(request.grants) + 1
        request.grants.append(
            _Grant(
                jti=f"dgnt_{request_id.removeprefix('dreq_')}{position}",
                sub=sub,
                position=position,
                token=f"decision+jwt.{request_id}.{position}.signature-not-a-real-token",
                dwell_ms=dwell_ms,
            )
        )
        if len(request.grants) >= request.approvals_required:
            request.status = "approved"

    def open_request(
        self, action: Mapping[str, Any], case_version: str, *, agent_id: str | None, grant_id: str | None
    ) -> str:
        """A request someone else made with the same developer key, for the agent it names."""
        self.case_versions[str(action["case_id"])] = case_version
        return self._create(dict(action), case_version, 1, agent_id, grant_id).request_id

    def tokens(self, request_id: str) -> list[str]:
        return [g.token for g in self.requests[request_id].grants]

    # ── the platform's side ──────────────────────────────────────────────────────────────────

    def handle(self, request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = json.loads(request.content) if request.content else {}
        path = request.url.path
        self.calls.append((request.method, path, body))
        if request.headers.get("authorization") != f"Bearer {self.api_key}":
            return httpx.Response(401, json={"message": "Invalid API key", "code": "UNAUTHORIZED"})
        try:
            return self._route(request.method, path, body)
        except _RefusedError as refused:
            return httpx.Response(
                refused.status,
                json={
                    "message": refused.sub_reason,
                    "code": _code(refused.status),
                    "reason": "decision_invalid",
                    "subReason": refused.sub_reason,
                    "requestId": "req-test",
                },
            )

    def _route(self, method: str, path: str, body: dict[str, Any]) -> httpx.Response:
        case = _CASE_PATH.match(path)
        if method == "PUT" and case:
            return self._set_case_version(case.group(1), str(body["caseVersion"]))
        if method == "POST" and path == "/v1/decisions/requests":
            return self._create_request(body)
        if method == "POST" and path == "/v1/decisions/consume":
            return self._consume_presented(body)
        match = _REQUEST_PATH.match(path)
        if match:
            request_id, verb = match.group(1), match.group(2)
            request = self.requests.get(request_id)
            if method == "GET" and verb is None:
                if request is None:
                    return httpx.Response(404, json={"message": "Decision request not found", "code": "NOT_FOUND"})
                return self._status(request)
            if method == "POST" and verb == "grants":
                return self._release(request, body)
            if method == "POST" and verb == "consume":
                if request is None:
                    raise _RefusedError(404, "unknown_grant")
                return self._consume(request, body, by_request=True)
        return httpx.Response(404, json={"message": f"Route {method}:{path} not found", "error": "Not Found"})

    def _set_case_version(self, case_id: str, case_version: str) -> httpx.Response:
        self.case_versions[case_id] = case_version
        for request in self.requests.values():
            if (
                request.action.get("case_id") == case_id
                and request.status in ("pending", "approved")
                and request.case_version != case_version
            ):
                request.status = "superseded"
                for grant in request.grants:
                    if grant.consumed_at is None:
                        grant.revoked_at, grant.revoked_reason = "2026-09-20T10:05:00Z", "case_changed"
        return httpx.Response(200, json={"caseId": case_id, "caseVersion": case_version})

    def _create(
        self, action: dict[str, Any], case_version: str, required: int, agent_id: str | None, grant_id: str | None
    ) -> _Request:
        self._sequence += 1
        request = _Request(
            request_id=f"dreq_{self._sequence:08d}",
            action=action,
            case_version=case_version,
            approvals_required=required,
            agent_id=agent_id,
            grant_id=grant_id,
        )
        self.requests[request.request_id] = request
        return request

    def _create_request(self, body: dict[str, Any]) -> httpx.Response:
        action = dict(body["action"])
        case_version = str(body["caseVersion"])
        if self.case_versions.get(str(action["case_id"])) not in (None, case_version):
            raise _RefusedError(409, "case_changed")
        agent_id, grant_id = body.get("agentId"), body.get("grantId")
        required = 2 if action.get("decision") in (body.get("fourEyesOn") or []) else 1
        for existing in self.requests.values():
            if (
                existing.status in ("pending", "approved")
                and _canonical(existing.action) == _canonical(action)
                and existing.case_version == case_version
            ):
                if self.binding and (existing.agent_id, existing.grant_id) != (agent_id, grant_id):
                    raise _RefusedError(409, "wrong_agent")
                return httpx.Response(200, json={**self._answer(existing), "created": False})
        request = self._create(action, case_version, required, agent_id, grant_id)
        return httpx.Response(201, json={**self._answer(request), "created": True})

    def _answer(self, request: _Request) -> dict[str, Any]:
        return {**self._response(request, ready=self._ready(request) if self.binding else None),
                "approvalPage": f"{ISSUER_ORIGIN}/decisions/{request.request_id}"}  # fmt: skip

    def _status(self, request: _Request) -> httpx.Response:
        ready = self._ready(request)
        if self.binding:
            return httpx.Response(200, json=self._response(request, ready=ready))
        tokens = [g.token for g in request.grants] if ready else None
        return httpx.Response(200, json=self._response(request, tokens=tokens))

    def _release(self, request: _Request | None, body: dict[str, Any]) -> httpx.Response:
        token = body.get("grantToken")
        if not isinstance(token, str) or not token:
            return httpx.Response(400, json={"message": "grantToken is required", "code": "BAD_REQUEST"})
        if request is None:
            return httpx.Response(404, json={"message": "Decision request not found", "code": "NOT_FOUND"})
        holder = self.agent_grant_tokens.get(token)
        if holder is None or (request.agent_id is None and request.grant_id is None):
            raise _RefusedError(403, "wrong_agent")
        if (request.agent_id is not None and holder[0] != request.agent_id) or (
            request.grant_id is not None and holder[1] != request.grant_id
        ):
            raise _RefusedError(403, "wrong_agent")
        ready = self._ready(request)
        tokens = [g.token for g in request.grants] if ready else None
        return httpx.Response(200, json=self._response(request, ready=ready, tokens=tokens))

    def _consume_presented(self, body: dict[str, Any]) -> httpx.Response:
        tokens = body.get("decisionGrants")
        if not isinstance(tokens, list) or not 1 <= len(tokens) <= 2:
            raise _RefusedError(400, "malformed")
        presented = [
            (request, grant)
            for token in tokens
            for request in self.requests.values()
            for grant in request.grants
            if grant.token == token
        ]
        if len(presented) != len(tokens):
            raise _RefusedError(409, "unknown_grant")
        if len({request.request_id for request, _ in presented}) != 1:
            raise _RefusedError(409, "action_mismatch")
        request = presented[0][0]
        if self.binding and (request.agent_id is not None or request.grant_id is not None):
            named_agent = body.get("agentId") or body.get("agentDid")
            if (request.agent_id is not None and named_agent != request.agent_id) or (
                request.grant_id is not None and body.get("grantId") != request.grant_id
            ):
                raise _RefusedError(403, "wrong_agent")
        return self._consume(request, body, by_request=False, presented=[g for _, g in presented])

    def _consume(
        self, request: _Request, body: dict[str, Any], *, by_request: bool, presented: list[_Grant] | None = None
    ) -> httpx.Response:
        action, case_version = body.get("action"), body.get("caseVersion")
        if not isinstance(action, dict) or not isinstance(case_version, str) or not case_version:
            raise _RefusedError(400, "malformed")
        # A decision an agent asked for is spent only with the grants that agent presents.
        if by_request and (request.agent_id is not None or request.grant_id is not None):
            raise _RefusedError(403, "wrong_agent")
        grants = request.grants if by_request else list(presented or [])
        if by_request and not grants:
            closed = {"superseded": "case_changed", "cancelled": "revoked"}
            raise _RefusedError(409, closed.get(request.status, "unknown_grant"))
        if action.get("case_id") != request.action.get("case_id"):
            raise _RefusedError(409, "wrong_case")
        if _canonical(action) != _canonical(request.action):
            raise _RefusedError(409, "action_mismatch")
        if any(g.consumed_at for g in grants):
            raise _RefusedError(409, "consumed")
        if any(g.revoked_at for g in grants):
            raise _RefusedError(409, "case_changed" if grants[0].revoked_reason == "case_changed" else "revoked")
        current = self.case_versions.get(str(request.action["case_id"]))
        if case_version != request.case_version or current != request.case_version:
            raise _RefusedError(409, "case_changed")
        if len(grants) < request.approvals_required:
            raise _RefusedError(409, "four_eyes_incomplete")
        for grant in grants:
            grant.consumed_at = "2026-09-20T10:10:00Z"
        request.status = "consumed"
        return httpx.Response(
            200,
            json={
                "consumed": True,
                "requestId": request.request_id,
                "jtis": [g.jti for g in grants],
                "approvers": [
                    {
                        "sub": g.sub,
                        "approver_auth": "sso+webauthn",
                        "dwell_ms": g.dwell_ms,
                        "dwell_source": "server",
                        "jti": g.jti,
                    }
                    for g in sorted(grants, key=lambda g: g.position)
                ],
                "actionHash": "sha256:" + "1" * 64,
            },
        )

    @staticmethod
    def _ready(request: _Request) -> bool:
        return (
            request.status == "approved"
            and len(request.grants) == request.approvals_required
            and all(g.consumed_at is None and g.revoked_at is None for g in request.grants)
        )

    @staticmethod
    def _response(request: _Request, *, ready: bool | None = None, tokens: list[str] | None = None) -> dict[str, Any]:
        answer: dict[str, Any] = {
            "requestId": request.request_id,
            "status": request.status,
            "action": request.action,
            "actionHash": "sha256:" + "1" * 64,
            "connector": "governed_cases",
            "caseVersion": request.case_version,
            "approvalsRequired": request.approvals_required,
            "approvalsReceived": len(request.grants),
            "agentId": request.agent_id,
            "grantId": request.grant_id,
            "expiresAt": "2026-09-21T10:00:00.000Z",
            "createdAt": "2026-09-20T09:00:00.000Z",
            "approvals": [
                {
                    "jti": g.jti,
                    "sub": g.sub,
                    "approverAuth": "sso+webauthn",
                    "dwellMs": g.dwell_ms,
                    "dwellSource": "server",
                    "position": g.position,
                    "issuedAt": "2026-09-20T10:00:00.000Z",
                    "expiresAt": "2026-09-21T10:00:00.000Z",
                    "consumedAt": g.consumed_at,
                    "revokedAt": g.revoked_at,
                    "revokedReason": g.revoked_reason,
                }
                for g in request.grants
            ],
        }
        if ready is not None:
            answer["decisionGrantsReady"] = ready
        if tokens is not None:
            answer["decisionGrants"] = tokens
        return answer
