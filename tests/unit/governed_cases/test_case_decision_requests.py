# SPDX-License-Identifier: Apache-2.0
"""Decision requests and grant consumption (PRD G-3), without a database or a network.

Covers what the platform is responsible for: what the approver is shown, the provisional Grantex
endpoints and their refusals, the verifier that consumes grants for the exact action and case
version, and the fake service the rest of the suite drives (four eyes, same approver refused,
single use).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from core.cases.decision_requests import (
    DecisionServiceError,
    GrantexDecisionGrantService,
    ServiceDecisionVerifier,
    case_action,
    policy_score_for_approval,
    render_memo_for_approval,
)
from core.models.governed_case import GovernedCase
from core.test_doubles.fake_decision_grants import FakeDecisionGrantService

POLICY = {
    "schema_version": "1.0.0",
    "policy": {"policy_id": "business_onboarding_uk", "version": "1.2.0", "example": True, "reviewed_by": None},
    "inputs_digest": "sha256:" + "0" * 64,
    "score": 40,
    "tier": "medium",
    "reasons": [
        {
            "rule_id": "ownership_reconciled",
            "tier": "medium",
            "reason": "Declared owners do not reconcile with the ownership graph",
            "score": 40,
            "inputs": {"ownership.missing_owners": 1},
        }
    ],
}

MEMO = {
    "memo_id": "memo_0001",
    "recommendation": {"proposed": "refer", "basis": "policy_result", "requires_human_decision": True},
    "sections": [
        {
            "section_id": "ownership",
            "status": "complete",
            "findings": [
                {
                    "code": "missing_owner",
                    "severity": "medium",
                    "statement": "A declared owner is absent.",
                    "evidence": [{}],
                }
            ],
            "evidence": [{}],
        },
        {
            "section_id": "web_presence",
            "status": "not_available",
            "not_available_reason": "capability_not_supported",
            "findings": [],
            "evidence": [],
        },
    ],
    "missing_items": [{"item": "owner_evidence", "reason": "Ownership evidence is needed."}],
    "policy_result": POLICY,
}


async def _always_enabled(_tenant_id: Any) -> bool:
    return True


def case_row(**overrides: Any) -> GovernedCase:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "case_ref": "case_" + "a" * 24,
        "purpose": "aml.cdd.onboarding",
        "state": "awaiting_decision",
        "provider": "mock",
        "policy_id": "business_onboarding_uk",
        "application": {"legal_name": "Marlpit Orchard Example Ltd", "jurisdiction": "GB"},
        "subject": {"provider": "mock", "provider_ref": "mock-gb-00000001", "jurisdiction": "GB"},
        "memo": MEMO,
        "policy_result": POLICY,
        "screening_dispositions": [],
        "version": 3,
    }
    values.update(overrides)
    return GovernedCase(**values)


# --- what the approver reads ----------------------------------------------------------------------


def test_the_approval_memo_states_the_action_the_policy_and_every_section() -> None:
    text = render_memo_for_approval(case_row(), "decline", "The applicant withdrew two owners.")
    assert "Requested decision: decline" in text
    assert "Marlpit Orchard Example Ltd" in text
    assert "tier medium" in text and "business_onboarding_uk" in text
    assert "shipped example" in text
    assert "The applicant withdrew two owners." in text
    assert "ownership: complete" in text and "web_presence: not_available (capability_not_supported)" in text
    assert "missing_owner: A declared owner is absent." in text
    assert "owner_evidence" in text
    assert "No agent may approve, decline, close or file this case." in text


def test_the_approval_memo_reports_each_screening_disposition_and_its_review() -> None:
    case = case_row(
        screening_dispositions=[
            {"hit_id": "hit-1", "proposed_outcome": "false_positive", "review": None},
            {
                "hit_id": "hit-2",
                "proposed_outcome": "true_match",
                "review": {"action": "accepted", "final_outcome": "true_match", "analyst_id": "user:a"},
            },
        ]
    )
    text = render_memo_for_approval(case, "approve")
    assert "hit hit-1: proposed false_positive; awaiting analyst review" in text
    assert "hit hit-2: proposed true_match; accepted as true_match by user:a" in text


def test_a_policy_result_too_large_for_the_issuer_is_trimmed_but_keeps_its_identity() -> None:
    reasons = [
        {"rule_id": f"rule_{n}", "tier": "medium", "reason": "x" * 400, "score": 1, "inputs": {"a.b": n}}
        for n in range(200)
    ]
    trimmed = policy_score_for_approval(case_row(policy_result={**POLICY, "reasons": reasons}))
    assert trimmed["truncated"] is True
    assert trimmed["policy"] == POLICY["policy"] and trimmed["inputs_digest"] == POLICY["inputs_digest"]
    assert len(json.dumps(trimmed, separators=(",", ":")).encode()) <= 30_000
    assert 0 < len(trimmed["reasons"]) < 200
    assert trimmed["omitted_reasons"] == 200 - len(trimmed["reasons"])
    assert trimmed["score"] == 40 and trimmed["tier"] == "medium"


# --- the provisional Grantex endpoints -------------------------------------------------------------


def service(handler: Any) -> GrantexDecisionGrantService:
    transport = httpx.MockTransport(handler)
    return GrantexDecisionGrantService(
        base_url="https://auth.grantex.invalid",
        api_key="test-only-key",
        client_factory=lambda: httpx.AsyncClient(transport=transport, base_url="https://auth.grantex.invalid"),
    )


async def test_creating_a_request_registers_the_case_version_and_returns_the_approval_page() -> None:
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        seen.append((request.method, request.url.path, body))
        assert request.headers["authorization"] == "Bearer test-only-key"
        if request.method == "PUT":
            return httpx.Response(200, json={"caseId": body, "caseVersion": "3"})
        return httpx.Response(
            201,
            json={
                "requestId": "dr_1",
                "status": "pending",
                "action": body["action"],
                "actionHash": "sha256:" + "1" * 64,
                "caseVersion": "3",
                "approvalsRequired": 2,
                "approvals": [],
                "expiresAt": "2026-09-21T10:00:00Z",
                "approvalPage": "https://auth.grantex.invalid/decisions/dr_1",
            },
        )

    view = await service(handler).create_request(
        action={"case_id": "case_1", "action": "case_decision", "decision": "decline", "subject": "mock:x"},
        case_version="3",
        memo="memo text",
        policy_score=POLICY,
        four_eyes_on=("decline",),
    )
    assert [(m, p) for m, p, _ in seen] == [("PUT", "/v1/decisions/cases/case_1"), ("POST", "/v1/decisions/requests")]
    assert seen[1][2]["caseVersion"] == "3" and seen[1][2]["fourEyesOn"] == ["decline"]
    assert seen[1][2]["memo"]["content"] == "memo text"
    assert view.request_id == "dr_1" and view.approvals_required == 2
    assert view.approval_page == "https://auth.grantex.invalid/decisions/dr_1"
    assert view.grants_ready is False


async def test_a_request_status_reports_each_approval_and_the_dwell_the_issuer_measured() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "requestId": "dr_1",
                "status": "pending",
                "action": {"case_id": "case_1", "action": "case_decision", "decision": "approve", "subject": "mock:x"},
                "actionHash": "sha256:" + "1" * 64,
                "caseVersion": "3",
                "approvalsRequired": 2,
                "approvals": [
                    {
                        "jti": "j1",
                        "sub": "user:9f:alice",
                        "approverAuth": "sso+webauthn",
                        "dwellMs": 61250,
                        "dwellSource": "server",
                        "position": 1,
                        "issuedAt": "2026-09-20T10:00:00Z",
                        "consumedAt": None,
                    }
                ],
                "expiresAt": "2026-09-21T10:00:00Z",
                "approvalPage": "https://auth.grantex.invalid/decisions/dr_1",
            },
        )

    view = await service(handler).get_request("dr_1")
    assert view.approvals_received == 1 and view.approvals_required == 2 and view.grants_ready is False
    assert view.approvals[0].dwell_ms == 61250 and view.approvals[0].dwell_source == "server"
    assert view.as_dict()["approvals"][0]["approver"] == "user:9f:alice"


@pytest.mark.parametrize(
    ("status", "payload", "reason", "detail"),
    [
        (404, {"code": "DECISION_GRANTS_DISABLED"}, "decision_service_disabled", "DECISION_GRANTS_DISABLED"),
        # An unknown or aged-out request is not the service being switched off.
        (404, {"code": "NOT_FOUND"}, "decision_request_not_found", "NOT_FOUND"),
        (401, {"code": "UNAUTHORIZED"}, "decision_service_unauthorised", "UNAUTHORIZED"),
        (409, {"reason": "decision_invalid", "subReason": "case_changed"}, "decision_invalid", "case_changed"),
        (500, {"code": "INTERNAL"}, "decision_service_refused", "INTERNAL"),
    ],
)
async def test_every_issuer_refusal_becomes_a_reason_code(
    status: int, payload: dict[str, Any], reason: str, detail: str
) -> None:
    with pytest.raises(DecisionServiceError) as refused:
        await service(lambda _r: httpx.Response(status, json=payload)).get_request("dr_1")
    assert (refused.value.reason, refused.value.detail) == (reason, detail)


def _status_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "requestId": "dr_1",
        "status": "pending",
        "action": {"case_id": "case_1", "action": "case_decision", "decision": "decline", "subject": "mock:x"},
        "actionHash": "sha256:" + "1" * 64,
        "caseVersion": "3",
        "approvalsRequired": 2,
        "approvals": [],
        "expiresAt": "2026-09-21T10:00:00Z",
    }
    payload.update(overrides)
    return {k: v for k, v in payload.items() if v is not _ABSENT}


_ABSENT = object()


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"approvalsRequired": _ABSENT}, id="approvals_required_missing"),
        pytest.param({"approvalsRequired": 0}, id="approvals_required_zero"),
        pytest.param({"approvalsRequired": "2"}, id="approvals_required_not_a_number"),
        pytest.param({"actionHash": _ABSENT}, id="action_hash_missing"),
        pytest.param({"action": _ABSENT}, id="action_missing"),
        pytest.param({"caseVersion": _ABSENT}, id="case_version_missing"),
        pytest.param({"status": _ABSENT}, id="status_missing"),
        pytest.param({"requestId": ""}, id="request_id_empty"),
        pytest.param({"expiresAt": _ABSENT}, id="expires_at_missing"),
    ],
)
async def test_a_status_answer_missing_a_field_the_screen_states_is_refused(overrides: dict[str, Any]) -> None:
    """A renamed or absent field must not become a default: four eyes would read as one approver."""
    with pytest.raises(DecisionServiceError) as refused:
        await service(lambda _r: httpx.Response(200, json=_status_payload(**overrides))).get_request("dr_1")
    assert refused.value.reason == "decision_service_response_invalid"


@pytest.mark.parametrize(
    "approval",
    [
        pytest.param(
            {"approverAuth": "sso+webauthn", "dwellSource": "server", "position": 1, "issuedAt": "x"}, id="no_sub"
        ),
        pytest.param({"sub": "user:a", "dwellSource": "server", "position": 1, "issuedAt": "x"}, id="no_auth"),
        pytest.param({"sub": "user:a", "approverAuth": "sso", "position": 1, "issuedAt": "x"}, id="no_dwell_source"),
        pytest.param(
            {"sub": "user:a", "approverAuth": "sso", "dwellSource": "server", "issuedAt": "x"}, id="no_position"
        ),
        pytest.param(
            {
                "sub": "user:a",
                "approverAuth": "sso",
                "dwellSource": "server",
                "position": 1,
                "issuedAt": "x",
                "dwellMs": "61250",
            },
            id="dwell_not_a_duration",
        ),
        pytest.param("not-an-object", id="not_an_object"),
    ],
)
async def test_an_approval_missing_what_the_screen_asserts_is_refused(approval: Any) -> None:
    with pytest.raises(DecisionServiceError) as refused:
        await service(lambda _r: httpx.Response(200, json=_status_payload(approvals=[approval]))).get_request("dr_1")
    assert refused.value.reason == "decision_service_response_invalid"


async def test_a_created_request_without_an_approval_page_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200, json={"caseVersion": "3"})
        return httpx.Response(201, json=_status_payload())

    with pytest.raises(DecisionServiceError) as refused:
        await service(handler).create_request(
            action={"case_id": "case_1", "action": "case_decision", "decision": "decline", "subject": "mock:x"},
            case_version="3",
            memo="memo",
            policy_score=POLICY,
            four_eyes_on=("decline",),
        )
    assert refused.value.reason == "decision_service_response_invalid"


async def test_an_unreachable_issuer_fails_closed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(DecisionServiceError) as refused:
        await service(handler).grants("dr_1")
    assert refused.value.reason == "decision_service_unavailable"


async def test_consumption_returns_the_approvers_and_refuses_an_answer_without_one() -> None:
    consumed = await service(
        lambda _r: httpx.Response(
            200,
            json={
                "consumed": True,
                "requestId": "dr_1",
                "jtis": ["j1", "j2"],
                "approvers": [{"sub": "user:a", "jti": "j1"}, {"sub": "user:b", "jti": "j2"}],
                "actionHash": "sha256:" + "1" * 64,
            },
        )
    ).consume(grants=["g1", "g2"], action={"case_id": "case_1"}, case_version="3")
    assert consumed.approvers == (("user:a", "j1"), ("user:b", "j2"))

    with pytest.raises(DecisionServiceError) as refused:
        await service(lambda _r: httpx.Response(200, json={"consumed": True, "requestId": "dr_1"})).consume(
            grants=["g1"], action={}, case_version="3"
        )
    assert refused.value.reason == "decision_service_response_invalid"


async def test_a_consumption_without_a_grant_id_per_approver_is_refused() -> None:
    """The case records which single-use grant was spent; an approver without one is not a record."""
    with pytest.raises(DecisionServiceError) as refused:
        await service(
            lambda _r: httpx.Response(
                200,
                json={
                    "consumed": True,
                    "requestId": "dr_1",
                    # Two approvers and one grant id: nothing says which grant
                    # the second approver spent, so neither is recorded.
                    "jtis": ["j1"],
                    "approvers": [{"sub": "user:a"}, {"sub": "user:b"}],
                },
            )
        ).consume(grants=["g1", "g2"], action={"case_id": "case_1"}, case_version="3")
    assert refused.value.reason == "decision_service_response_invalid"

    with pytest.raises(DecisionServiceError) as no_sub:
        await service(
            lambda _r: httpx.Response(
                200, json={"consumed": True, "requestId": "dr_1", "jtis": ["j1"], "approvers": [{"jti": "j1"}]}
            )
        ).consume(grants=["g1"], action={"case_id": "case_1"}, case_version="3")
    assert no_sub.value.reason == "decision_service_response_invalid"


async def test_an_approver_takes_its_grant_id_from_a_matching_jtis_list() -> None:
    """An issuer that lists the grant ids separately, in the same order, is still usable."""
    consumed = await service(
        lambda _r: httpx.Response(
            200,
            json={
                "consumed": True,
                "requestId": "dr_1",
                "jtis": ["j1", "j2"],
                "approvers": [{"sub": "user:a"}, {"sub": "user:b"}],
            },
        )
    ).consume(grants=["g1", "g2"], action={"case_id": "case_1"}, case_version="3")
    assert consumed.approvers == (("user:a", "j1"), ("user:b", "j2"))


# --- the verifier ----------------------------------------------------------------------------------


async def test_a_decision_without_grants_is_refused_with_decision_required() -> None:
    verifier = ServiceDecisionVerifier(FakeDecisionGrantService())
    check = await verifier.verify(tenant_id="t", case=case_row(), outcome="approve", grants=[])
    assert (check.allowed, check.reason) == (False, "decision_required")


async def test_the_verifier_consumes_the_grants_for_the_exact_action_and_case_version() -> None:
    fake = FakeDecisionGrantService()
    case = case_row()
    action = case_action(case, "approve")
    assert action["extra"] == {"tenant": str(case.tenant_id)}
    view = await fake.create_request(
        action=action, case_version=str(case.version), memo="memo", policy_score=POLICY, four_eyes_on=()
    )
    fake.approve(view.request_id, "user:9f:alice")
    grants = await fake.grants(view.request_id)

    check = await ServiceDecisionVerifier(fake).verify(tenant_id="t", case=case, outcome="approve", grants=grants)
    # The grant id is recorded, never the token.
    assert check.allowed is True and check.approvers == (("user:9f:alice", "jti-dr_00000001-1"),)
    assert grants[0] not in [grant_id for _, grant_id in check.approvers]


async def test_a_case_that_changed_since_the_approval_is_refused() -> None:
    fake = FakeDecisionGrantService()
    case = case_row()
    view = await fake.create_request(
        action=case_action(case, "approve"),
        case_version=str(case.version),
        memo="memo",
        policy_score=POLICY,
        four_eyes_on=(),
    )
    fake.approve(view.request_id, "user:9f:alice")
    grants = await fake.grants(view.request_id)
    case.version = case.version + 1

    check = await ServiceDecisionVerifier(fake).verify(tenant_id="t", case=case, outcome="approve", grants=grants)
    assert (check.allowed, check.reason) == (False, "case_changed")


async def test_grants_approved_for_another_outcome_do_not_decide_this_one() -> None:
    fake = FakeDecisionGrantService()
    case = case_row()
    view = await fake.create_request(
        action=case_action(case, "approve"),
        case_version=str(case.version),
        memo="memo",
        policy_score=POLICY,
        four_eyes_on=(),
    )
    fake.approve(view.request_id, "user:9f:alice")
    grants = await fake.grants(view.request_id)

    check = await ServiceDecisionVerifier(fake).verify(tenant_id="t", case=case, outcome="decline", grants=grants)
    assert (check.allowed, check.reason) == (False, "action_mismatch")


async def test_a_grant_for_another_tenants_case_of_the_same_reference_is_refused() -> None:
    fake = FakeDecisionGrantService()
    case = case_row()
    other_tenant = case_row(case_ref=case.case_ref, tenant_id=uuid.uuid4())
    view = await fake.create_request(
        action=case_action(case, "approve"),
        case_version=str(case.version),
        memo="memo",
        policy_score=POLICY,
        four_eyes_on=(),
    )
    fake.approve(view.request_id, "user:9f:alice")
    grants = await fake.grants(view.request_id)

    check = await ServiceDecisionVerifier(fake).verify(
        tenant_id="t", case=other_tenant, outcome="approve", grants=grants
    )
    assert (check.allowed, check.reason) == (False, "action_mismatch")


async def test_a_consumed_decision_records_grant_ids_and_never_a_token() -> None:
    fake = FakeDecisionGrantService()
    case = case_row()
    view = await fake.create_request(
        action=case_action(case, "decline"),
        case_version=str(case.version),
        memo="memo",
        policy_score=POLICY,
        four_eyes_on=("decline",),
    )
    fake.approve(view.request_id, "user:9f:alice")
    fake.approve(view.request_id, "user:9f:bob")
    grants = await fake.grants(view.request_id)

    check = await ServiceDecisionVerifier(fake).verify(tenant_id="t", case=case, outcome="decline", grants=grants)
    assert check.allowed is True
    recorded = [grant_id for _, grant_id in check.approvers]
    assert recorded == ["jti-dr_00000001-1", "jti-dr_00000001-2"]
    for token in grants:
        assert token not in recorded


async def test_a_new_case_version_supersedes_an_open_request_at_the_issuer() -> None:
    """The issuer's own protection: grants for a case that moved on are revoked, not left live."""
    fake = FakeDecisionGrantService()
    case = case_row()
    view = await fake.create_request(
        action=case_action(case, "approve"),
        case_version=str(case.version),
        memo="memo",
        policy_score=POLICY,
        four_eyes_on=(),
    )
    fake.approve(view.request_id, "user:9f:alice")
    assert await fake.grants(view.request_id) != []

    await fake.set_case_version(case.case_ref, str(case.version + 1))

    after = await fake.get_request(view.request_id)
    assert after.status == "superseded"
    assert await fake.grants(view.request_id) == []


# --- the fake service's own rules --------------------------------------------------------------------


async def test_four_eyes_needs_two_different_approvers_and_refuses_the_first_one_twice() -> None:
    fake = FakeDecisionGrantService()
    view = await fake.create_request(
        action={"case_id": "case_1", "action": "case_decision", "decision": "decline", "subject": "mock:x"},
        case_version="3",
        memo="memo",
        policy_score=POLICY,
        four_eyes_on=("decline",),
    )
    assert view.approvals_required == 2
    fake.approve(view.request_id, "user:9f:alice")
    assert await fake.grants(view.request_id) == []

    with pytest.raises(DecisionServiceError) as refused:
        fake.approve(view.request_id, "user:9f:alice")
    assert refused.value.detail == "same_approver"

    after = fake.approve(view.request_id, "user:9f:bob")
    assert after.status == "approved" and after.grants_ready is True
    assert [a.approver for a in after.approvals] == ["user:9f:alice", "user:9f:bob"]
    assert len(await fake.grants(view.request_id)) == 2


async def test_grants_are_single_use() -> None:
    fake = FakeDecisionGrantService()
    action = {"case_id": "case_1", "action": "case_decision", "decision": "approve", "subject": "mock:x"}
    view = await fake.create_request(
        action=action, case_version="3", memo="memo", policy_score=POLICY, four_eyes_on=()
    )
    fake.approve(view.request_id, "user:9f:alice")
    grants = await fake.grants(view.request_id)
    await fake.consume(grants=grants, action=action, case_version="3")

    with pytest.raises(DecisionServiceError) as refused:
        await fake.consume(grants=grants, action=action, case_version="3")
    assert refused.value.detail == "consumed"
    assert await fake.grants(view.request_id) == []


async def test_a_second_request_for_the_same_action_and_version_reuses_the_open_one() -> None:
    fake = FakeDecisionGrantService()
    action = {"case_id": "case_1", "action": "case_decision", "decision": "approve", "subject": "mock:x"}
    first = await fake.create_request(action=action, case_version="3", memo="m", policy_score=POLICY, four_eyes_on=())
    second = await fake.create_request(action=action, case_version="3", memo="m", policy_score=POLICY, four_eyes_on=())
    assert first.request_id == second.request_id
    other_version = await fake.create_request(
        action=action, case_version="4", memo="m", policy_score=POLICY, four_eyes_on=()
    )
    assert other_version.request_id != first.request_id


async def test_an_unreachable_issuer_is_counted_when_a_case_version_is_announced() -> None:
    """A persistently unreachable issuer leaves stale requests live at its end; that has to show."""
    from prometheus_client import REGISTRY

    from core.cases.runtime import CaseRuntime, announce_case_version

    fake = FakeDecisionGrantService()
    fake.fail_with = DecisionServiceError("decision_service_unavailable", "ConnectError")
    runtime = CaseRuntime(decision_service=lambda: fake, flag=_always_enabled)

    def counter(result: str) -> float:
        value = REGISTRY.get_sample_value(
            "agenticorg_case_version_announcements_total", {"result": result}
        )
        return float(value or 0.0)

    failed_before, ok_before = counter("failed"), counter("registered")
    await announce_case_version(runtime, "case_" + "a" * 24, 4, only_if=True)
    assert counter("failed") == failed_before + 1

    await announce_case_version(runtime, "case_" + "a" * 24, 4, only_if=True)
    assert counter("registered") == ok_before + 1

    # A case nobody asked a decision about never calls the issuer at all.
    await announce_case_version(runtime, "case_" + "a" * 24, 5, only_if=False)
    assert counter("registered") == ok_before + 1
