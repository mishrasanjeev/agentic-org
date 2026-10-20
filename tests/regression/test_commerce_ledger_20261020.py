# SPDX-License-Identifier: Apache-2.0
"""Phase 2 commerce ledger (G9, G10, G12): signed ledger rows, enforced audit_event, no human legs.

Before this change commerce routes wrote no audit row and ``route_meta(audit_event=...)``
was metadata only; a write route could return success with nothing on the ledger.
"""

from __future__ import annotations

import ast
import inspect
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api.route_enforcement import enforce_route_metadata, is_commerce_write_route
from api.route_metadata import ROUTE_METADATA_ATTR, route_meta
from core.commerce import ledger
from core.commerce.ledger import CommerceLedgerError, commerce_payload_hash, record_commerce_event
from core.config import settings
from core.models.audit import AuditLog
from core.tool_gateway.audit_logger import verify_audit_row

TENANT = "11111111-1111-4111-8111-111111111111"


class _Session:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)


# ---------------------------------------------------------------------------
# record_commerce_event (G9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ledger_row_is_signed_and_carries_the_rfc8785_payload_hash() -> None:
    session = _Session()
    payload = {"b": 2, "a": [1, "x"], "when": "2026-10-20T00:00:00Z"}
    entry = await record_commerce_event(
        session, tenant_id=TENANT, event="commerce.runtime.purchase.prepare", outcome="success",
        merchant_id="merchant.example", seller_agent_id="seller-01", buyer_agent_id="shopper-01", payload=payload,
    )
    [row] = session.added
    assert isinstance(row, AuditLog)
    assert row.tenant_id == uuid.UUID(TENANT)
    assert row.resource_type == "commerce_ledger"
    assert row.event_type == row.action == "commerce.runtime.purchase.prepare"
    assert row.details["ledger"] == {
        "version": "commerce-ledger-v1",
        "event": "commerce.runtime.purchase.prepare",
        "merchant_id": "merchant.example",
        "seller_agent_id": "seller-01",
        "buyer_agent_id": "shopper-01",
        "payload_hash": entry.payload_hash,
        "payload_canonicalization": "RFC8785-SHA256",
    }
    # RFC 8785: key order and whitespace do not change the hash.
    assert entry.payload_hash == commerce_payload_hash({"a": [1, "x"], "when": "2026-10-20T00:00:00Z", "b": 2})
    assert verify_audit_row(row)
    row.outcome = "refused"
    assert not verify_audit_row(row)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"tenant_id": "tenant-a"}, "tenant UUID"),
        ({"event": "tool.shopify"}, "commerce"),
        ({"outcome": "maybe"}, "outcome"),
        ({"payload_hash": "ABC"}, "payload_hash"),
    ],
)
async def test_ledger_refuses_bad_rows_so_the_write_fails(kwargs: dict, message: str) -> None:
    arguments = {
        "tenant_id": TENANT, "event": "commerce.runtime.purchase.prepare", "outcome": "success",
        "merchant_id": None, "seller_agent_id": None, "buyer_agent_id": None, "payload": {},
    }
    with pytest.raises(CommerceLedgerError, match=message):
        await record_commerce_event(_Session(), **{**arguments, **kwargs})


@pytest.mark.asyncio
async def test_ledger_rows_are_listed_in_chain_order_for_export() -> None:
    row = AuditLog(
        id=uuid.uuid4(), tenant_id=uuid.UUID(TENANT), event_type="commerce.arbiter.decision", actor_type="system",
        actor_id="system", action="commerce.arbiter.decision", outcome="allow", resource_id="arbdec_1",
        details={"ledger": {"payload_hash": "a" * 64}}, signature="sig", chain_seq=3, chain_prev="p",
        chain_hash="h", created_at=None, sealed_at=None,
    )
    view = ledger.ledger_view(row)
    assert view["chain_seq"] == 3 and view["ledger"]["payload_hash"] == "a" * 64

    class _Scalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _ReadSession:
        def __init__(self):
            self.calls = 0

        async def scalars(self, _statement):
            self.calls += 1
            return _Scalars([row] if self.calls == 1 else [])

    rows = await ledger.list_commerce_events(_ReadSession(), TENANT, limit=10)
    assert rows == [row]


# ---------------------------------------------------------------------------
# audit_event enforcement (G9)
# ---------------------------------------------------------------------------


def _ledger_app() -> FastAPI:
    app = FastAPI(dependencies=[Depends(enforce_route_metadata, scope="function")])
    meta = {"auth_required": False, "tenant_required": False, "audit_event": "commerce.test.write"}

    @app.post("/api/v1/commerce/test/recorded")
    @route_meta(**meta)
    async def recorded() -> dict:
        await record_commerce_event(
            _Session(), tenant_id=TENANT, event="commerce.test.write", outcome="success",
            merchant_id=None, seller_agent_id=None, buyer_agent_id=None, payload={"ok": True},
        )
        return {"ok": True}

    @app.post("/api/v1/commerce/test/unrecorded")
    @route_meta(**meta)
    async def unrecorded() -> dict:
        return {"ok": True}

    @app.post("/api/v1/commerce/test/other-event")
    @route_meta(**meta)
    async def other_event() -> dict:
        await record_commerce_event(
            _Session(), tenant_id=TENANT, event="commerce.test.something_else", outcome="success",
            merchant_id=None, seller_agent_id=None, buyer_agent_id=None, payload={},
        )
        return {"ok": True}

    @app.post("/api/v1/commerce/test/raises")
    @route_meta(**meta)
    async def raises() -> dict:
        raise HTTPException(404, "not found")

    @app.get("/api/v1/commerce/test/read")
    @route_meta(**meta)
    async def read() -> dict:
        return {"ok": True}

    @app.post("/api/v1/agents/test/not-commerce")
    @route_meta(**meta)
    async def not_commerce() -> dict:
        return {"ok": True}

    return app


def test_write_route_that_recorded_its_event_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(settings, "route_enforcement_mode", "enforce")
    client = TestClient(_ledger_app(), raise_server_exceptions=False)
    assert client.post("/api/v1/commerce/test/recorded").status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/commerce/test/unrecorded", "/api/v1/commerce/test/other-event"])
def test_write_route_without_its_event_is_a_server_error_in_enforce_mode(monkeypatch, path: str) -> None:
    monkeypatch.setattr(settings, "route_enforcement_mode", "enforce")
    client = TestClient(_ledger_app(), raise_server_exceptions=False)
    response = client.post(path)
    assert response.status_code == 500
    assert response.json()["detail"] == "commerce_ledger_event_missing"


def test_log_mode_logs_the_missing_event_and_lets_the_response_through(monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "route_enforcement_mode", "log")
    client = TestClient(_ledger_app(), raise_server_exceptions=False)
    with caplog.at_level("ERROR", logger="agenticorg.route_enforcement"):
        response = client.post("/api/v1/commerce/test/unrecorded")
    assert response.status_code == 200
    assert any(record.getMessage() == "commerce_ledger_event_missing" for record in caplog.records)


def test_reads_errors_and_non_commerce_routes_are_not_checked(monkeypatch) -> None:
    monkeypatch.setattr(settings, "route_enforcement_mode", "enforce")
    client = TestClient(_ledger_app(), raise_server_exceptions=False)
    assert client.post("/api/v1/commerce/test/raises").status_code == 404
    assert client.get("/api/v1/commerce/test/read").status_code == 200
    assert client.post("/api/v1/agents/test/not-commerce").status_code == 200


def test_main_app_runs_the_ledger_check_before_the_response() -> None:
    from api.main import app

    [dependency] = [d for d in app.router.dependencies if d.dependency is enforce_route_metadata]
    assert dependency.scope == "function"


def test_every_commerce_write_route_declares_its_ledger_event() -> None:
    """Walk the app's route table: each commerce write route names the event it writes."""
    from api.main import app

    writes: dict[str, str] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            if not is_commerce_write_route(route.path, method):
                continue
            meta = getattr(route.endpoint, ROUTE_METADATA_ATTR, None) or {}
            event = meta.get("audit_event")
            assert isinstance(event, str) and event.startswith(("commerce.", "a2a.")), (method, route.path)
            assert len(event) <= 100
            writes[f"{method} {route.path}"] = event
    assert "POST /api/v1/commerce/runtime/purchase/prepare" in writes
    assert "POST /api/v1/commerce/runtime/pos/offline/handoffs" in writes
    assert "PUT /api/v1/commerce/runtime/merchant-configs/{merchant_id}" in writes
    assert "POST /api/v1/a2a/message:send" in writes
    assert "POST /api/v1/commerce/runtime/ledger/decisions/{decision_id}/operator-decisions" in writes
    assert "DELETE /api/v1/a2a/commerce/buyer-access/{access_id}" in writes
    assert len(writes) >= 21


def test_every_declared_commerce_write_event_is_written_by_its_handler() -> None:
    """Each commerce write handler (or a helper it calls) names its declared event in a ledger call."""
    from api.main import app
    from api.v1 import (
        a2a_interop,
        commerce_acp,
        commerce_ledger,
        commerce_runtime,
        commerce_ucp,
        commerce_ucp_publishing,
    )
    from core.commerce.acp import checkout as acp_checkout
    from core.commerce.ucp import handlers as ucp_handlers

    sources = (
        inspect.getsource(commerce_runtime)
        + inspect.getsource(a2a_interop)
        + inspect.getsource(commerce_ledger)
        + inspect.getsource(commerce_ucp_publishing)
        + inspect.getsource(commerce_ucp)
        + inspect.getsource(ucp_handlers)
        + inspect.getsource(commerce_acp)
        + inspect.getsource(acp_checkout)
    )
    for route in app.routes:
        if not isinstance(route, APIRoute) or not any(is_commerce_write_route(route.path, m) for m in route.methods):
            continue
        event = getattr(route.endpoint, ROUTE_METADATA_ATTR)["audit_event"]
        # The declaration plus at least one ledger use of the same name.
        assert sources.count(f'"{event}"') >= 2, event


# ---------------------------------------------------------------------------
# No human legs (G12)
# ---------------------------------------------------------------------------


def _decision(outcome: str = "refuse") -> Any:
    from datetime import UTC, datetime

    from core.commerce.arbiter import ArbiterContext, decide

    return decide(
        "pos_handoff",
        ArbiterContext(
            tenant_id=TENANT, merchant_id="merchant.example", seller_agent_id="seller-01", buyer_agent_id=None,
            now=datetime(2026, 10, 20, tzinfo=UTC), payload={"x": 1}, amount_minor_units=None, currency=None,
            quantity=1,
        ),
    )


@pytest.mark.asyncio
async def test_escalation_off_never_touches_hitl_queue(monkeypatch) -> None:
    from core.commerce import arbiter_store
    from core.models import hitl

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("hitl_queue was touched with escalate_to_human off")

    monkeypatch.setattr(hitl, "HITLQueue", _forbidden)
    result = await arbiter_store.escalate_refusal(
        tenant_id=TENANT, decision=_decision(), escalate_to_human=False, review_agent_id=str(uuid.uuid4())
    )
    assert result is None


@pytest.mark.asyncio
async def test_escalation_on_queues_a_review_copy_and_returns_at_once(monkeypatch) -> None:
    from core import database
    from core.commerce import arbiter_store

    added: list[Any] = []
    agent_id = uuid.uuid4()

    class _Result:
        def scalar_one_or_none(self):
            return agent_id

    class _Session:
        async def execute(self, _statement):
            return _Result()

        def add(self, row):
            added.append(row)

    @asynccontextmanager
    async def _tenant_session(_tenant):
        yield _Session()

    monkeypatch.setattr(database, "get_tenant_session", _tenant_session)
    decision = _decision()
    result = await arbiter_store.escalate_refusal(
        tenant_id=TENANT, decision=decision, escalate_to_human=True, review_agent_id=str(agent_id)
    )
    assert result["queued"] is True
    [item, ledger_row] = added
    assert result["hitl_ref"] == str(item.id)
    assert item.trigger_type == "commerce_arbiter_refusal"
    assert item.context["outcome_binding"] is False
    assert item.context["decision"]["outcome"] == "refuse"
    assert ledger_row.event_type == "commerce.arbiter.decision.escalated"
    # The decision itself is unchanged: escalation is a copy for review.
    assert decision.outcome == "refuse"


@pytest.mark.asyncio
async def test_escalation_without_a_review_agent_reports_and_does_not_raise() -> None:
    from core.commerce import arbiter_store

    result = await arbiter_store.escalate_refusal(
        tenant_id=TENANT, decision=_decision(), escalate_to_human=True, review_agent_id=None
    )
    assert result == {"queued": False, "reason": "review_agent_missing", "hitl_ref": None}


def test_no_commerce_decision_path_waits() -> None:
    """Neither the arbiter, the guard nor the handoff routes poll, sleep or wait for a person."""
    from api.v1 import commerce_runtime
    from core.commerce import arbiter, arbiter_store

    bodies = [
        inspect.getsource(arbiter),
        inspect.getsource(arbiter_store),
        inspect.getsource(commerce_runtime.prepare_purchase_handoff),
        inspect.getsource(commerce_runtime.create_offline_pos_handoff),
    ]
    for body in bodies:
        tree = ast.parse(body)
        calls = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        assert not any("sleep" in call or "wait_for" in call or call.endswith(".wait") for call in calls)
        assert not any(isinstance(node, ast.While) for node in ast.walk(tree))
    assert "pending" not in Path(inspect.getfile(arbiter)).read_text(encoding="utf-8").split("Outcome = ")[1][:60]


# ---------------------------------------------------------------------------
# AGENTICORG_AUDIT_CHAIN_DEFAULT_ON (G10)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_on_seals_only_commerce_tenants_while_the_global_flag_is_off(monkeypatch) -> None:
    from core.governance import audit_chain
    from core.tasks import audit_chain_tasks

    commerce_tenant, other_tenant = uuid.uuid4(), uuid.uuid4()
    sealed: list[uuid.UUID] = []

    async def _seal(tenant_id, **_kwargs):
        sealed.append(tenant_id)
        return audit_chain.SealOutcome(tenant_id=tenant_id, sealed=1, head=audit_chain.Head(), more=False)

    async def _all():
        return [commerce_tenant, other_tenant]

    async def _commerce():
        return [commerce_tenant]

    monkeypatch.setattr(audit_chain, "seal", _seal)
    monkeypatch.setattr(audit_chain_tasks, "_tenant_ids", _all)
    monkeypatch.setattr(audit_chain_tasks, "_commerce_tenant_ids", _commerce)

    monkeypatch.setattr(settings, "audit_chain_enabled", False)
    monkeypatch.setattr(settings, "audit_chain_default_on", False)
    assert (await audit_chain_tasks._seal_audit_chains_async())["enabled"] is False
    assert sealed == []

    monkeypatch.setattr(settings, "audit_chain_default_on", True)
    result = await audit_chain_tasks._seal_audit_chains_async()
    assert result["enabled"] is True and sealed == [commerce_tenant]

    sealed.clear()
    monkeypatch.setattr(settings, "audit_chain_enabled", True)
    await audit_chain_tasks._seal_audit_chains_async()
    assert sealed == [commerce_tenant, other_tenant]


def test_default_on_flag_is_off_by_default() -> None:
    from core.config import Settings

    assert Settings.model_fields["audit_chain_default_on"].default is False


def test_migration_reinstalls_the_same_seal_trigger_text() -> None:
    """v6z88 (re)installs the audit_log trigger; its function text must equal SEAL_TRIGGER_SQL."""
    import importlib.util

    from core.governance.audit_chain import SEAL_TRIGGER_SQL

    path = Path("migrations/versions/v6_z88_commerce_ledger.py")
    spec = importlib.util.spec_from_file_location("v6z88", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module._SEAL_TRIGGER_SQL == SEAL_TRIGGER_SQL
    assert module.revision == "v6z88_commerce_ledger"
    assert len(module.revision) <= 32
    source = path.read_text(encoding="utf-8")
    for required in (
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))",
        "CREATE TRIGGER audit_log_immutable",
        "commerce_arbiter_decisions_append_only",
    ):
        assert required in source
    assert set(module.OACP_APPEND_ONLY_TABLES) == {
        "oacp_operator_decision_records",
        "oacp_audit_review_manifest_records",
        "oacp_retention_disposition_decision_records",
    }


def test_status_reports_sealing_for_a_commerce_tenant_under_default_on(monkeypatch) -> None:
    import asyncio

    from core.governance import audit_chain

    class _Result:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    class _Session:
        async def execute(self, _statement):
            return _Result("config-1")

    monkeypatch.setattr(settings, "audit_chain_enabled", False)
    monkeypatch.setattr(settings, "audit_chain_default_on", True)
    assert asyncio.run(audit_chain._sealing_enabled(_Session(), uuid.UUID(TENANT))) is True
    monkeypatch.setattr(settings, "audit_chain_default_on", False)
    assert asyncio.run(audit_chain._sealing_enabled(_Session(), uuid.UUID(TENANT))) is False
