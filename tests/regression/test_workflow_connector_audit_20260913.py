"""Regression tests for the 2026-09-13 workflow / connector / bridge audit.

Each test replays a confirmed finding (failing before the fix, passing after):

1.  ``BaseConnector`` write helpers tolerate ``204 No Content`` / empty bodies.
2.  Cancellation is not last-writer-wins: stale checkpoints are rejected, the
    engine re-reads status between steps, and DB sync never downgrades a
    terminal run status.
3.  ``route_to_bridge`` idempotent replay returns the stored outcome and never
    publishes ``post_xml`` a second time.
4.  Tally bridge reconnects after a graceful server close.
5.  Salesforce (and swept siblings) re-authenticate once on 401.
6.  A paused sub-workflow fails closed (``sub_workflow_pause_unsupported``).
7.  ``resume_from_hitl`` keeps an agent step's pre-approval output.
8.  Wait/event resume tasks retry (not noop) when they land before the
    ``waiting_*`` checkpoint is persisted.
9.  Agent actions ``process``/``generate``/``draft`` are not retried as read-only.
10. Zoho per-call ``organization_id`` never re-points the cached connector.
11. GSTN bulk e-way bill submission keeps partial results on a row failure.
12. HITL push notifications carry a real approval id (flush before push).
13. HubSpot list tools forward ``after`` and emit ``next_after``.
15. Grantex ``enforce`` runs off the event loop.
16. Resume/timeout Celery tasks re-raise so Celery retries; checkpoint-gap
    returns become ``Retry``.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from auth.run_grants import NO_RUN_GRANT_FOR_TESTS
from workflows.engine import WorkflowEngine
from workflows.state_store import (
    STATE_VERSION_KEY,
    InMemoryWorkflowStateRepository,
    StaleStateError,
    WorkflowStateStore,
)

TENANT_A = "11111111-1111-1111-1111-111111111111"


def _engine() -> tuple[WorkflowEngine, InMemoryWorkflowStateRepository]:
    repo = InMemoryWorkflowStateRepository()
    return WorkflowEngine(WorkflowStateStore(repository=repo, redis=None)), repo


def _http_response(status_code: int = 200, *, content: bytes = b"", json_data: Any = None) -> httpx.Response:
    request = httpx.Request("PUT", "https://api.example.invalid/x")
    if json_data is not None:
        return httpx.Response(status_code, json=json_data, request=request)
    return httpx.Response(status_code, content=content, request=request)


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    response = _http_response(status_code, json_data={"error": "x"})
    return httpx.HTTPStatusError(str(status_code), request=response.request, response=response)


# ---------------------------------------------------------------------------
# 1. 204 / empty-body writes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_base_connector_write_helpers_tolerate_no_content() -> None:
    from connectors.hr.zoom import ZoomConnector

    connector = ZoomConnector({"client_id": "a", "client_secret": "b", "account_id": "c"})
    connector._client = MagicMock()
    connector._client.patch = AsyncMock(return_value=_http_response(204))
    connector._client.delete = AsyncMock(return_value=_http_response(200, content=b""))
    connector._client.put = AsyncMock(return_value=_http_response(200, json_data={"id": "1"}))
    connector._client.post = AsyncMock(return_value=_http_response(204))
    connector._client.get = AsyncMock(return_value=_http_response(200, content=b"not json"))

    assert await connector._patch("/x", {"a": 1}) == {"status": "ok", "http_status": 204}
    assert await connector._delete("/x") == {"status": "ok", "http_status": 200}
    assert await connector._put("/x", {"a": 1}) == {"id": "1"}
    assert await connector._post("/x", {"a": 1}) == {"status": "ok", "http_status": 204}
    assert await connector._get("/x") == {"status": "ok", "http_status": 200}


# ---------------------------------------------------------------------------
# 2. Cancellation is not last-writer-wins
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_state_store_rejects_stale_save_after_concurrent_cancel() -> None:
    repo = InMemoryWorkflowStateRepository()
    store = WorkflowStateStore(repository=repo, redis=None)
    await store.save({"id": "run-occ", "status": "running", "step_results": {}})

    worker_view = await store.load("run-occ")
    canceller_view = await store.load("run-occ")

    canceller_view["status"] = "cancelled"
    await store.save(canceller_view, actor="workflow_engine.cancel")

    worker_view["step_results"]["s1"] = {"status": "completed", "output": {}}
    with pytest.raises(StaleStateError):
        await store.save(worker_view, actor="workflow_engine", step_id="s1")

    assert repo.states["run-occ"]["state"]["status"] == "cancelled"
    assert STATE_VERSION_KEY not in repo.states["run-occ"]["state"]
    # The winning writer's in-memory copy stays current and can keep saving.
    canceller_view["cancelled_at"] = "now"
    await store.save(canceller_view, actor="workflow_engine.cancel")


@pytest.mark.asyncio
async def test_engine_in_flight_step_cannot_overwrite_cancel() -> None:
    engine, repo = _engine()
    definition = {
        "name": "cancel-race",
        "steps": [
            {"id": "s1", "type": "agent", "agent": "x"},
            {"id": "s2", "type": "agent", "agent": "x", "depends_on": ["s1"]},
        ],
    }
    executed: list[str] = []

    async def cancel_while_running(step: dict, state: dict) -> dict[str, Any]:
        executed.append(step["id"])
        if step["id"] == "s1":
            # Operator hits POST /cancel while s1 is still executing.
            await engine.cancel(state["id"])
        return {"step_id": step["id"], "type": "agent", "status": "completed", "output": {}}

    with patch("workflows.engine.execute_step", side_effect=cancel_while_running):
        run_id = await engine.start_run(definition)
        result = await engine.execute(run_id)

    assert result["status"] == "cancelled"
    assert repo.states[run_id]["state"]["status"] == "cancelled"
    assert executed == ["s1"]


@pytest.mark.asyncio
async def test_engine_rechecks_status_between_steps() -> None:
    engine, repo = _engine()
    store = engine.state_store
    definition = {
        "name": "cancel-between",
        "steps": [
            {"id": "s1", "type": "agent", "agent": "x"},
            {"id": "s2", "type": "agent", "agent": "x", "depends_on": ["s1"]},
        ],
    }
    executed: list[str] = []
    original_save = store.save
    cancelled = False

    async def save_then_cancel(state: dict, **kwargs: Any) -> None:
        nonlocal cancelled
        await original_save(state, **kwargs)
        if (
            not cancelled
            and kwargs.get("step_id") == "s1"
            and (kwargs.get("metadata") or {}).get("event") == "step_checkpointed"
        ):
            cancelled = True
            await engine.cancel(state["id"])

    async def fake_step(step: dict, state: dict) -> dict[str, Any]:
        executed.append(step["id"])
        return {"step_id": step["id"], "type": "agent", "status": "completed", "output": {}}

    store.save = save_then_cancel  # type: ignore[method-assign]
    with patch("workflows.engine.execute_step", side_effect=fake_step):
        run_id = await engine.start_run(definition)
        result = await engine.execute(run_id)

    assert executed == ["s1"]
    assert result["status"] == "cancelled"
    assert repo.states[run_id]["state"]["status"] == "cancelled"


class _FakeSession:
    def __init__(self, db_run: Any, agent_id: Any = None) -> None:
        self.db_run = db_run
        self.agent_id = agent_id
        self.events: list[str] = []
        self.added: list[Any] = []

    async def execute(self, _stmt: Any) -> Any:
        result = MagicMock()
        result.scalar_one_or_none.return_value = self.db_run if self.db_run is not None else self.agent_id
        return result

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        self.events.append("add")

    async def flush(self) -> None:
        self.events.append("flush")


def _tenant_session_patch(session: _FakeSession):
    @contextlib.asynccontextmanager
    async def _fake(*_args: Any, **_kwargs: Any):
        yield session

    return patch("core.database.get_tenant_session", _fake)


@pytest.mark.asyncio
async def test_run_sync_never_downgrades_terminal_db_status() -> None:
    from workflows.run_sync import sync_engine_state_to_workflow_run

    db_run = SimpleNamespace(
        status="cancelled", steps_completed=0, steps_total=2, result=None, error=None, completed_at=None
    )
    session = _FakeSession(db_run)
    state = {"id": "eng-1", "status": "completed", "step_results": {}, "steps_total": 2, "definition": {"steps": []}}

    with _tenant_session_patch(session), patch("workflows.run_sync.record_ab_outcome_if_terminal", AsyncMock()):
        await sync_engine_state_to_workflow_run(
            tenant_id=uuid.UUID(TENANT_A),
            workflow_run_id=uuid.uuid4(),
            engine_run_id="eng-1",
            state=state,
        )

    assert db_run.status == "cancelled"
    assert db_run.result is None


# ---------------------------------------------------------------------------
# 3. Bridge idempotent replay
# ---------------------------------------------------------------------------
@pytest.fixture
def bridge_state():
    from bridge import server_handler
    from bridge.state import (
        InMemoryBridgeBroker,
        InMemoryBridgeStateRepository,
        configure_bridge_state_for_tests,
        reset_bridge_state_for_tests,
    )

    repo = InMemoryBridgeStateRepository()
    broker = InMemoryBridgeBroker()
    configure_bridge_state_for_tests(repository=repo, broker=broker)
    server_handler._active_bridges.clear()
    server_handler._pending_requests.clear()
    try:
        yield repo, broker
    finally:
        server_handler._active_bridges.clear()
        server_handler._pending_requests.clear()
        reset_bridge_state_for_tests()


async def _connect_bridge(repo: Any, bridge_id: str = "bridge-1") -> None:
    await repo.connect_session(
        bridge_id=bridge_id,
        tenant_id=TENANT_A,
        connector_type="tally",
        tally_healthy=True,
        owner="pod-a",
        process_id=1,
    )


@pytest.mark.asyncio
async def test_route_to_bridge_replay_returns_stored_result_without_republishing(bridge_state) -> None:
    from bridge.server_handler import route_to_bridge

    repo, broker = bridge_state
    await _connect_bridge(repo)
    publishes: list[dict] = []

    async def tally_posts_voucher(message: dict) -> None:
        publishes.append(message)
        result = {"type": "response", "request_id": message["request_id"], "status": "ok", "xml_response": "<VCH/>"}
        await repo.mark_responded(request_id=message["request_id"], result=result, response_metadata={})
        await broker.publish_response(message["request_id"], result)

    await broker.subscribe_requests("bridge-1", tally_posts_voucher)

    first = await route_to_bridge(
        "bridge-1", "<ENVELOPE/>", timeout=1, tenant_id=TENANT_A, idempotency_key="voucher-42"
    )
    # Workflow retry replays the same voucher post.
    second = await route_to_bridge(
        "bridge-1", "<ENVELOPE/>", timeout=1, tenant_id=TENANT_A, idempotency_key="voucher-42"
    )

    assert first["status"] == "ok"
    assert second == first
    assert len(publishes) == 1


@pytest.mark.asyncio
async def test_route_to_bridge_replay_of_failed_request_raises_stored_failure(bridge_state) -> None:
    from bridge.server_handler import route_to_bridge
    from bridge.state import BridgeRouteError, payload_hash

    repo, broker = bridge_state
    await _connect_bridge(repo)
    await repo.create_request(
        request_id="req-failed",
        bridge_id="bridge-1",
        tenant_id=TENANT_A,
        connector_type="tally",
        method="post_xml",
        payload_hash_value=payload_hash("<ENVELOPE/>"),
        timeout_seconds=5,
        idempotency_key="voucher-failed",
    )
    await repo.mark_failed(
        request_id="req-failed", code="bridge_disconnected", message="Bridge bridge-1 is disconnected"
    )
    publishes: list[dict] = []

    async def record(message: dict) -> None:
        publishes.append(message)

    await broker.subscribe_requests("bridge-1", record)

    with pytest.raises(BridgeRouteError) as exc:
        await route_to_bridge(
            "bridge-1", "<ENVELOPE/>", timeout=1, tenant_id=TENANT_A, idempotency_key="voucher-failed"
        )

    assert exc.value.code == "bridge_disconnected"
    assert publishes == []


@pytest.mark.asyncio
async def test_route_to_bridge_replay_of_in_flight_request_waits_without_republishing(bridge_state) -> None:
    from bridge.server_handler import route_to_bridge
    from bridge.state import payload_hash

    repo, broker = bridge_state
    await _connect_bridge(repo)
    original = await repo.create_request(
        request_id="req-inflight",
        bridge_id="bridge-1",
        tenant_id=TENANT_A,
        connector_type="tally",
        method="post_xml",
        payload_hash_value=payload_hash("<ENVELOPE/>"),
        timeout_seconds=5,
        idempotency_key="voucher-inflight",
    )
    await repo.mark_sent(request_id=original.request_id)
    publishes: list[dict] = []

    async def record(message: dict) -> None:
        publishes.append(message)

    await broker.subscribe_requests("bridge-1", record)

    async def original_pod_finishes() -> None:
        await asyncio.sleep(0.05)
        result = {"type": "response", "request_id": "req-inflight", "status": "ok", "xml_response": "<VCH/>"}
        await repo.mark_responded(request_id="req-inflight", result=result, response_metadata={})
        await broker.publish_response("req-inflight", result)

    finisher = asyncio.create_task(original_pod_finishes())
    result = await route_to_bridge(
        "bridge-1", "<ENVELOPE/>", timeout=2, tenant_id=TENANT_A, idempotency_key="voucher-inflight"
    )
    await finisher

    assert result["status"] == "ok"
    assert result["request_id"] == "req-inflight"
    assert publishes == []


# ---------------------------------------------------------------------------
# 4. Tally bridge graceful close
# ---------------------------------------------------------------------------
def _tally_bridge():
    from bridge.tally_bridge import TallyBridge

    return TallyBridge(cloud_url="wss://cloud.example.invalid/ws", bridge_id="b1", bridge_token="t")


@pytest.mark.asyncio
async def test_tally_bridge_connection_loops_return_when_socket_closes_gracefully(monkeypatch) -> None:
    bridge = _tally_bridge()
    bridge._running = True
    bridge._ws = object()

    async def message_loop_ends_on_1000() -> None:
        return None

    async def forever() -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(bridge, "_message_loop", message_loop_ends_on_1000)
    monkeypatch.setattr(bridge, "_heartbeat_loop", forever)
    monkeypatch.setattr(bridge, "_health_check_loop", forever)

    await asyncio.wait_for(bridge._run_connection_loops(), timeout=2)


@pytest.mark.asyncio
async def test_tally_bridge_health_loop_exits_when_disconnected() -> None:
    bridge = _tally_bridge()
    bridge._running = True
    bridge._ws = None
    await asyncio.wait_for(bridge._health_check_loop(), timeout=1)


@pytest.mark.asyncio
async def test_tally_bridge_start_reconnects_after_graceful_close(monkeypatch) -> None:
    bridge = _tally_bridge()
    connects = 0

    async def fake_connect() -> None:
        nonlocal connects
        connects += 1

    async def loops_end_gracefully() -> None:
        return None

    async def backoff_then_stop() -> None:
        if connects >= 2:
            bridge._running = False

    monkeypatch.setattr(bridge, "_connect", fake_connect)
    monkeypatch.setattr(bridge, "_run_connection_loops", loops_end_gracefully)
    monkeypatch.setattr(bridge, "_backoff_before_reconnect", backoff_then_stop)

    await asyncio.wait_for(bridge.start(), timeout=2)
    assert connects == 2


# ---------------------------------------------------------------------------
# 5. 401 re-authentication
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_salesforce_execute_tool_reauthenticates_once_on_401() -> None:
    from connectors.marketing.salesforce import SalesforceConnector

    connector = SalesforceConnector(
        {
            "client_id": "a",
            "client_secret": "b",
            "instance_url": "https://acme.my.salesforce.com",
            "access_token": "old",
        }
    )
    attempts: list[int] = []

    async def query(**_params: Any) -> dict[str, Any]:
        attempts.append(1)
        if len(attempts) == 1:
            raise _status_error(401)
        return {"records": []}

    connector._tool_registry["query"] = query
    connector._authenticate = AsyncMock()  # type: ignore[method-assign]
    connector._rebuild_http_client = AsyncMock()  # type: ignore[method-assign]

    assert await connector.execute_tool("query", {"soql": "SELECT Id FROM Account"}) == {"records": []}
    connector._authenticate.assert_awaited_once()
    connector._rebuild_http_client.assert_awaited_once()

    async def forbidden(**_params: Any) -> dict[str, Any]:
        raise _status_error(403)

    connector._tool_registry["query"] = forbidden
    with pytest.raises(httpx.HTTPStatusError):
        await connector.execute_tool("query", {})


def test_swept_dynamic_auth_connectors_override_execute_tool_for_401() -> None:
    from connectors.finance.gstn import GstnConnector
    from connectors.framework.base_connector import BaseConnector
    from connectors.hr.linkedin_talent import LinkedinTalentConnector
    from connectors.hr.zoom import ZoomConnector
    from connectors.marketing.brandwatch import BrandwatchConnector
    from connectors.marketing.linkedin_ads import LinkedinAdsConnector
    from connectors.marketing.salesforce import SalesforceConnector
    from connectors.ops.servicenow import ServicenowConnector

    for cls in (
        SalesforceConnector,
        GstnConnector,
        ZoomConnector,
        LinkedinTalentConnector,
        BrandwatchConnector,
        LinkedinAdsConnector,
        ServicenowConnector,
    ):
        assert cls.execute_tool is not BaseConnector.execute_tool, cls.__name__


# ---------------------------------------------------------------------------
# 6. Paused sub-workflow fails closed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sub_workflow_pause_fails_closed_instead_of_stranding_parent() -> None:
    from workflows.step_types import execute_step

    result = await execute_step(
        {
            "id": "nested",
            "type": "sub_workflow",
            "definition": {"name": "child", "steps": [{"id": "approve", "type": "human_in_loop"}]},
        },
        {"id": "parent-run"},
    )

    assert result["status"] == "failed"
    assert result["code"] == "sub_workflow_pause_unsupported"
    assert "sub_workflow_pause_unsupported" in result["error"]
    assert result["sub_run_id"]


# ---------------------------------------------------------------------------
# 7. HITL resume keeps the agent's pre-approval output
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_resume_from_hitl_merges_decision_into_agent_output() -> None:
    engine, repo = _engine()
    definition = {
        "name": "draft-then-send",
        "steps": [
            {"id": "draft", "type": "agent", "agent": "x"},
            {"id": "gate", "type": "human_in_loop", "depends_on": ["draft"]},
            {"id": "send", "type": "agent", "agent": "x", "depends_on": ["gate"]},
        ],
    }

    async def fake_step(step: dict, state: dict) -> dict[str, Any]:
        if step["id"] == "draft":
            return {
                "step_id": "draft",
                "type": "agent",
                "status": "waiting_hitl",
                "output": {"subject": "Q3 offer", "body": "Hello"},
                "confidence": 0.42,
            }
        if step["type"] == "human_in_loop":
            from workflows import step_types

            return await step_types._execute_hitl(step, state)
        return {"step_id": step["id"], "type": "agent", "status": "completed", "output": {"sent": True}}

    with patch("workflows.engine.execute_step", side_effect=fake_step):
        run_id = await engine.start_run(definition)
        assert (await engine.execute(run_id))["status"] == "waiting_hitl"
        decision = {"decision": "approve", "confidence": 1.0, "notes": "ship it"}
        first = await engine.resume_from_hitl(run_id, decision)
        assert first["status"] == "waiting_hitl"  # the dedicated gate step
        final = await engine.resume_from_hitl(run_id, {"decision": "approve"})

    state = repo.states[run_id]["state"]
    draft = state["step_results"]["draft"]
    assert draft["status"] == "completed"
    assert draft["output"]["subject"] == "Q3 offer"
    assert draft["output"]["hitl_decision"] == decision
    assert draft["confidence"] == 0.42
    # A dedicated human_in_loop step still records the decision as its output.
    assert state["step_results"]["gate"]["output"] == {"decision": "approve"}
    assert final["status"] == "completed"
    assert state["step_results"]["send"]["status"] == "completed"


# ---------------------------------------------------------------------------
# 8. Resume before the waiting checkpoint is persisted
# ---------------------------------------------------------------------------
def _running_state_without_checkpoint(run_id: str) -> dict:
    return {
        "id": run_id,
        "status": "running",
        "definition": {
            "steps": [
                {"id": "wait-1", "type": "wait_for_event"},
                {"id": "after", "type": "agent", "agent": "x", "depends_on": ["wait-1"]},
            ]
        },
        "step_results": {},
        "steps_completed": 0,
    }


@pytest.mark.asyncio
async def test_resume_wait_task_retries_when_checkpoint_not_yet_persisted() -> None:
    from core.tasks import workflow_tasks

    repo = InMemoryWorkflowStateRepository()
    store = WorkflowStateStore(repository=repo, redis=None)
    store.init = AsyncMock()  # type: ignore[method-assign]
    store.close = AsyncMock()  # type: ignore[method-assign]
    await store.save(_running_state_without_checkpoint("run-gap"))

    with patch.object(workflow_tasks, "_state_store", return_value=store):
        result = await workflow_tasks._resume_workflow_wait_async("run-gap", "wait-1")

    assert result["status"] == "retry"
    assert repo.states["run-gap"]["state"]["status"] == "running"
    assert "wait-1" not in repo.states["run-gap"]["state"]["step_results"]


@pytest.mark.asyncio
async def test_timeout_event_task_retries_when_checkpoint_not_yet_persisted() -> None:
    from core.tasks import workflow_tasks
    from workflows.event_waits import InMemoryWorkflowEventWaitRepository, WorkflowEventWaitStore

    repo = InMemoryWorkflowStateRepository()
    store = WorkflowStateStore(repository=repo, redis=None)
    store.init = AsyncMock()  # type: ignore[method-assign]
    store.close = AsyncMock()  # type: ignore[method-assign]
    await store.save(_running_state_without_checkpoint("run-gap-timeout"))
    event_store = WorkflowEventWaitStore(repository=InMemoryWorkflowEventWaitRepository(), redis=None)
    await event_store.register(engine_run_id="run-gap-timeout", step_id="wait-1", event_type="email.opened")

    with (
        patch.object(workflow_tasks, "_state_store", return_value=store),
        patch.object(workflow_tasks, "_event_wait_store", return_value=event_store),
    ):
        result = await workflow_tasks._timeout_workflow_event_async("run-gap-timeout", "wait-1")

    assert result["status"] == "retry"
    assert repo.states["run-gap-timeout"]["state"]["status"] == "running"


def test_resume_wait_task_raises_celery_retry_on_checkpoint_gap() -> None:
    from celery.exceptions import Retry

    from core.tasks import workflow_tasks

    with patch.object(workflow_tasks, "run_async", return_value={"status": "retry", "reason": "gap"}):
        with pytest.raises(Retry):
            workflow_tasks.resume_workflow_wait("run-gap", "wait-1")


# ---------------------------------------------------------------------------
# 9. Retry classification for default agent actions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("action", ["process", "generate", "draft", "generate_invoice", "process_payment"])
def test_agent_default_actions_are_not_retried_as_read_only(action: str) -> None:
    step = {"id": "a", "type": "agent", "agent": "x", "action": action}
    assert WorkflowEngine._step_is_retryable(step) is False
    assert WorkflowEngine._step_is_retryable({**step, "idempotency_key": "k1"}) is True
    # Steps with no action at all default to ``process`` and must not be retried.
    assert WorkflowEngine._step_is_retryable({"id": "a", "type": "agent", "agent": "x"}) is False


def test_read_only_agent_actions_still_retry() -> None:
    assert WorkflowEngine._step_is_retryable({"id": "a", "type": "agent", "agent": "x", "action": "summarize"}) is True
    assert (
        WorkflowEngine._step_is_retryable({"id": "a", "type": "agent", "agent": "x", "action": "fetch_report"}) is True
    )


# ---------------------------------------------------------------------------
# 10. Zoho per-call org does not re-point the cached connector
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_zoho_explicit_org_scopes_only_that_call() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector({"access_token": "fake", "organization_id": "cfg-org"})
    connector._client = MagicMock()
    connector._client.get = AsyncMock(return_value=_http_response(200, json_data={"contacts": []}))

    await connector.list_vendors(organization_id="60072428145")
    await connector.list_vendors()

    first, second = connector._client.get.call_args_list
    assert first.kwargs["params"]["organization_id"] == "60072428145"
    assert second.kwargs["params"]["organization_id"] == "cfg-org"
    assert connector._org_id == "cfg-org"
    assert connector.config["organization_id"] == "cfg-org"


# ---------------------------------------------------------------------------
# 11. GSTN bulk e-way bills keep partial results
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_gstn_bulk_submit_keeps_generated_rows_when_a_row_fails() -> None:
    from connectors.finance.gstn import GstnConnector

    connector = GstnConnector({"gspappid": "a", "gspappsecret": "b", "gstin": "27AAAAA0000A1Z5"})
    connector.generate_eway_bill = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"ewayBillNo": "1", "payload": {"client_reference": "INV-1"}},
            httpx.ConnectError("gstn unreachable"),
            {"ewayBillNo": "3", "payload": {"client_reference": "INV-3"}},
        ]
    )
    rows = [{"document_number": f"INV-{i}"} for i in (1, 2, 3)]

    result = await connector.bulk_generate_eway_bills(invoices=rows, submit=True)

    assert result["status"] == "completed_with_errors"
    assert result["summary"] == {"input_rows": 3, "generated": 2, "failed": 1, "submitted_to_gstn": True}
    assert [row["row_number"] for row in result["generated"]] == [1, 3]
    assert result["failed"][0]["row_number"] == 2
    assert "gstn unreachable" not in result["failed"][0]["error"]


# ---------------------------------------------------------------------------
# 12. HITL push carries a real approval id
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_sync_flushes_hitl_row_before_push_notification() -> None:
    from workflows.run_sync import sync_engine_state_to_workflow_run

    db_run = SimpleNamespace(
        status="running", steps_completed=0, steps_total=1, result=None, error=None, completed_at=None
    )
    session = _FakeSession(db_run)
    step_row = SimpleNamespace(status="waiting_hitl", agent_id=uuid.uuid4())
    push = AsyncMock(side_effect=lambda *_a, **_k: session.events.append("push"))
    state = {
        "id": "eng-hitl",
        "status": "waiting_hitl",
        "waiting_step_id": "approve",
        "step_results": {"approve": {"status": "waiting_hitl", "output": {}}},
        "steps_total": 1,
        "definition": {"steps": [{"id": "approve", "type": "human_in_loop"}]},
    }

    with (
        _tenant_session_patch(session),
        patch("api.v1.workflows._upsert_step_execution", AsyncMock(return_value=(step_row, True))),
        patch("workflows.run_sync.schedule_hitl_timeout", return_value=True),
        patch("core.push.sender.notify_approval_created", push),
        patch("workflows.run_sync.record_ab_outcome_if_terminal", AsyncMock()),
    ):
        await sync_engine_state_to_workflow_run(
            tenant_id=uuid.UUID(TENANT_A),
            workflow_run_id=uuid.uuid4(),
            engine_run_id="eng-hitl",
            state=state,
        )

    assert session.events == ["add", "flush", "push"]
    push.assert_awaited_once()


# ---------------------------------------------------------------------------
# 13. HubSpot pagination cursor
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hubspot_list_tools_forward_after_and_emit_next_cursor() -> None:
    from connectors.marketing.hubspot import HubspotConnector

    connector = HubspotConnector({"access_token": "fake"})
    connector._client = MagicMock()
    connector._client.get = AsyncMock(
        return_value=_http_response(
            200,
            json_data={
                "results": [{"id": "1", "properties": {"email": "a@x.io"}}],
                "paging": {"next": {"after": "cursor-2"}},
            },
        )
    )

    page = await connector.list_contacts(after="cursor-1", limit=1)
    assert connector._client.get.call_args.kwargs["params"]["after"] == "cursor-1"
    assert page["has_more"] is True
    assert page["next_after"] == "cursor-2"

    for tool, key in (
        ("list_deals", "deals"),
        ("list_companies", "companies"),
        ("list_tasks", "tasks"),
        ("list_notes", "notes"),
    ):
        result = await getattr(connector, tool)(after="c")
        assert connector._client.get.call_args.kwargs["params"]["after"] == "c", tool
        assert result["next_after"] == "cursor-2", tool
        assert key in result

    connector._client.get = AsyncMock(return_value=_http_response(200, json_data={"results": []}))
    last = await connector.list_deals()
    assert "after" not in connector._client.get.call_args.kwargs["params"]
    assert last["has_more"] is False and last["next_after"] is None


# ---------------------------------------------------------------------------
# 15. Grantex enforce off the event loop
# ---------------------------------------------------------------------------
class _RecordingGrantex:
    def __init__(self) -> None:
        self.thread: threading.Thread | None = None

    def enforce(self, **_kwargs: Any) -> Any:
        self.thread = threading.current_thread()
        return SimpleNamespace(allowed=False, reason="not granted")


@pytest.mark.asyncio
async def test_execute_agent_tool_runs_grant_enforce_off_event_loop() -> None:
    from core.langgraph.tool_adapter import execute_agent_tool

    grantex = _RecordingGrantex()
    with patch("core.langgraph.grantex_auth.get_grantex_client", return_value=grantex):
        result = await execute_agent_tool(
            "hubspot",
            "list_contacts",
            {},
            tenant_id=TENANT_A,
            company_id=None,
            domain=None,
            authorized_tools=["hubspot.list_contacts"],
            grant_token="grant-token",
            run_grant=NO_RUN_GRANT_FOR_TESTS,
        )

    assert result["error"]["code"] == "E1007"
    assert grantex.thread is not None and grantex.thread is not threading.main_thread()


@pytest.mark.asyncio
async def test_tool_gateway_runs_grant_enforce_off_event_loop() -> None:
    from core.tool_gateway.gateway import ToolGateway

    grantex = _RecordingGrantex()
    with patch("core.langgraph.grantex_auth.get_grantex_client", return_value=grantex):
        result = await ToolGateway().execute(
            tenant_id=TENANT_A,
            agent_id="agent-1",
            agent_scopes=[],
            connector_name="hubspot",
            tool_name="list_contacts",
            params={},
            grant_token="grant-token",
            run_grant=NO_RUN_GRANT_FOR_TESTS,
        )

    assert result["error"]["code"] == "E1007"
    assert grantex.thread is not None and grantex.thread is not threading.main_thread()


# ---------------------------------------------------------------------------
# 16. Celery tasks re-raise so Celery retries
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("task_name", ["resume_workflow_wait", "timeout_workflow_event", "timeout_workflow_hitl"])
def test_workflow_tasks_autoretry_instead_of_swallowing(task_name: str) -> None:
    from core.tasks import workflow_tasks

    task = getattr(workflow_tasks, task_name)
    assert task.autoretry_for == (Exception,)
    assert task.retry_backoff is True
    assert task.retry_kwargs == {"max_retries": 5}

    with patch.object(workflow_tasks, "run_async", side_effect=RuntimeError("db unavailable")):
        with pytest.raises(RuntimeError):
            task("run-x", "step-x")
