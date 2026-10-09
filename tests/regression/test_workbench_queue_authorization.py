# SPDX-License-Identifier: Apache-2.0
"""Queue visibility must not grant write authority over the backing resource."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from api.route_enforcement import _check_scope, required_scopes_for
from api.route_metadata import ROUTE_METADATA_ATTR
from api.v1 import approvals, txn, workbench_queue
from core.config import settings
from core.content import drafts
from core.idp import store
from core.rbac import ROLE_SCOPES
from core.txn import findings
from core.workbench import assignments, queue

TENANT = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
USER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ITEM = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
ACTION_KINDS = ("approval", "document", "draft", "finding")


@pytest.fixture
def boundary(monkeypatch):
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)
    monkeypatch.setattr(settings, "transaction_intelligence_enabled", True)
    monkeypatch.setattr(settings, "idp_enabled", True)
    monkeypatch.setattr(settings, "content_services_enabled", True)
    monkeypatch.setattr(settings, "route_enforcement_mode", "enforce")
    principal = {
        "role": "auditor",
        "scopes": list(ROLE_SCOPES["auditor"]),
        "auth_mode": "legacy",
        "user_id": USER,
        "sub": USER,
    }
    monkeypatch.setattr(assignments, "assigned_to", AsyncMock(return_value={"investigator", "review_officer"}))
    edits = AsyncMock(return_value={"id": str(ITEM)})
    approval_edits = AsyncMock(return_value={"review_edits": []})
    monkeypatch.setattr(queue, "apply_edits", edits)
    monkeypatch.setattr(queue, "record_approval_edits", approval_edits)
    decisions = {
        "approval": AsyncMock(return_value={"status": "decided"}),
        "document": AsyncMock(return_value={"status": "approved"}),
        "draft": AsyncMock(return_value={"status": "approved"}),
        "finding": AsyncMock(return_value={"status": "dismissed"}),
    }
    monkeypatch.setattr(approvals, "decide", decisions["approval"])
    monkeypatch.setattr(store, "decide", decisions["document"])
    monkeypatch.setattr(drafts, "decide", decisions["draft"])
    monkeypatch.setattr(findings, "disposition", decisions["finding"])

    async def enforce_scopes(request: Request):
        endpoint = request.scope["route"].endpoint
        _check_scope(request, getattr(endpoint, ROUTE_METADATA_ATTR))

    app = FastAPI(dependencies=[Depends(enforce_scopes)])

    @app.middleware("http")
    async def authenticated_context(request: Request, call_next):
        # The middleware double supplies verified server context, never request-body claims.
        request.state.tenant_id = str(TENANT)
        request.state.scopes = principal["scopes"]
        request.state.auth_mode = principal["auth_mode"]
        request.state.claims = {"role": principal["role"], "sub": principal["sub"]}
        if principal["user_id"]:
            request.state.claims["agenticorg:user_id"] = principal["user_id"]
        return await call_next(request)

    app.include_router(workbench_queue.router, prefix="/api/v1")
    app.include_router(txn.router, prefix="/api/v1")
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, principal=principal, edits=edits, approval_edits=approval_edits, decisions=decisions
        )


def _decide(boundary, kind, *, decision="approve", edits=False):
    return boundary.client.post(
        f"/api/v1/workbench/queue/{kind}/{ITEM}/decide",
        json={
            "decision": decision,
            "notes": "Review outcome",
            "edits": [{"name": "title", "value": "Changed before approval"}] if edits else [],
        },
    )


def _no_mutations(boundary):
    boundary.edits.assert_not_awaited()
    boundary.approval_edits.assert_not_awaited()
    for decision in boundary.decisions.values():
        decision.assert_not_awaited()


def test_every_actionable_queue_kind_matches_its_owning_route_write_scope():
    from api.v1 import content, idp_review

    handlers = {
        "approval": approvals.decide,
        "document": idp_review.decide_document,
        "draft": content.decide_draft,
        "finding": txn.disposition,
    }
    assert set(queue.KINDS) == set(handlers) | {"case"}
    for handler in handlers.values():
        metadata = getattr(handler, ROUTE_METADATA_ATTR)
        assert required_scopes_for(metadata["scope"], "POST") == ("approvals:write",)


@pytest.mark.parametrize("decision,outcome", [("approve", "confirm"), ("reject", "dismiss")])
def test_auditor_cannot_bypass_direct_finding_authorization_via_queue(boundary, decision, outcome):
    assert "finding" in queue.kinds_for("auditor", {"investigator"})
    direct = boundary.client.post(
        f"/api/v1/txn/findings/{ITEM}/disposition", json={"outcome": outcome, "notes": "Review outcome"}
    )
    assert direct.status_code == 403
    boundary.decisions["finding"].assert_not_awaited()
    response = _decide(boundary, "finding", decision=decision)
    assert response.status_code == 403, response.text
    assert "approvals:write" in response.text
    _no_mutations(boundary)


@pytest.mark.parametrize("kind", ACTION_KINDS)
@pytest.mark.parametrize(
    "scopes",
    [
        None,
        [],
        ["audit:read", "approvals:read"],
        ["approvals:write:extra"],
        ["agenticorg:administration:read"],
        "approvals:write",
        '["approvals:write"]',
    ],
)
def test_queue_visibility_and_read_scopes_never_allow_edits_or_decisions(boundary, kind, scopes):
    boundary.principal.update(role="cfo", scopes=scopes)
    assert kind in queue.kinds_for("cfo", {"investigator", "review_officer"})
    response = _decide(boundary, kind, edits=True)
    assert response.status_code == 403, response.text
    _no_mutations(boundary)


@pytest.mark.parametrize("kind,family", [("approval", "approvals"), ("document", "documents"), ("finding", "txn")])
@pytest.mark.parametrize("scopes", [["approvals:write"], ["approvals.write"], ["agenticorg:admin"]])
def test_authorized_queue_actions_keep_the_owning_resource_write_contract(boundary, kind, family, scopes):
    assert required_scopes_for(f"{family}.sensitive.write", "POST") == ("approvals:write",)
    boundary.principal.update(role="cfo", scopes=scopes)
    response = _decide(boundary, kind, edits=kind != "finding")
    assert response.status_code == 200, response.text
    boundary.edits.assert_awaited_once()
    assert boundary.edits.call_args.args[:3] == (TENANT, kind, str(ITEM))
    assert boundary.edits.call_args.kwargs["user_id"] == USER
    boundary.decisions[kind].assert_awaited_once()
    assert boundary.decisions[kind].call_args.args[0] == (ITEM if kind == "approval" else TENANT)
    for other, decision in boundary.decisions.items():
        if other != kind:
            decision.assert_not_awaited()


@pytest.mark.parametrize("scopes,status", [(["approvals:write"], 403), (["agenticorg:admin"], 200)])
def test_draft_keeps_its_additional_administrator_requirement(boundary, scopes, status):
    assert required_scopes_for("content.drafts.sensitive.write", "POST") == ("approvals:write",)
    boundary.principal.update(role="cfo", scopes=scopes)
    response = _decide(boundary, "draft", edits=True)
    assert response.status_code == status, response.text
    if status == 403:
        _no_mutations(boundary)
    else:
        boundary.edits.assert_awaited_once()
        boundary.decisions["draft"].assert_awaited_once()


@pytest.mark.parametrize("auth_mode,user_id", [("api_key", USER), ("grantex", USER), ("legacy", None)])
def test_finding_requires_a_person_before_any_edit_even_with_write_scope(boundary, auth_mode, user_id):
    boundary.principal.update(role="cfo", scopes=["approvals:write"], auth_mode=auth_mode, user_id=user_id)
    if user_id is None:
        boundary.principal["sub"] = ""
    response = _decide(boundary, "finding")
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error"] == "human_required"
    _no_mutations(boundary)


def test_audit_read_still_allows_finding_inspection_not_decision(boundary, monkeypatch):
    read = AsyncMock(return_value={"kind": "finding", "item": {"id": str(ITEM)}})
    monkeypatch.setattr(queue, "get_item", read)
    response = boundary.client.get(f"/api/v1/workbench/queue/finding/{ITEM}")
    assert response.status_code == 200
    read.assert_awaited_once()
    assert _decide(boundary, "finding").status_code == 403
    _no_mutations(boundary)


def test_log_only_route_enforcement_does_not_allow_queue_mutations(boundary, monkeypatch):
    monkeypatch.setattr(settings, "route_enforcement_mode", "log")
    response = _decide(boundary, "finding")
    assert response.status_code == 403, response.text
    _no_mutations(boundary)


@pytest.mark.asyncio
async def test_direct_handler_requires_verified_state_scope_not_role_or_claim_fallback(boundary):
    claims = {"role": "cfo", "agenticorg:user_id": USER, "grantex:scopes": ["approvals:write"]}
    request = SimpleNamespace(state=SimpleNamespace(claims=claims, scopes=[], auth_mode="legacy"))
    with pytest.raises(HTTPException) as refused:
        await workbench_queue.decide(
            "finding",
            str(ITEM),
            workbench_queue.DecisionIn(decision="approve"),
            BackgroundTasks(),
            request,
            role="cfo",
            tenant_id=str(TENANT),
            user_claims=claims,
            user_domains=None,
        )
    assert refused.value.status_code == 403
    _no_mutations(boundary)


@pytest.mark.parametrize("kind,status", [("case", 422), ("unknown", 404)])
def test_non_actionable_queue_kinds_never_reach_edits_or_stores(boundary, kind, status):
    boundary.principal.update(role="admin", scopes=["agenticorg:admin"])
    assert _decide(boundary, kind).status_code == status
    _no_mutations(boundary)
