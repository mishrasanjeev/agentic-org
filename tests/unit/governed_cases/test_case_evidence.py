# SPDX-License-Identifier: Apache-2.0
"""The evidence sink: records built from a run's record and posted to the Grantex evidence
service; the grant id carried from the authorizer through the gateway; the sink off by
default and never failing a case."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import jsonschema
import pytest
from grantex.evidence._schema import load_schema

from core.cases import evidence as case_evidence
from core.cases.evidence import (
    EvidenceServiceError,
    GrantexEvidenceService,
    evidence_time,
    index_for,
    record_run_evidence,
    records_for_run,
)
from core.tool_gateway.provider_gateway import ToolCallRecord, ToolDecision

RUN = "tenant:t1:case:case_000000000000000000000001:underwriter:abc"
GRANT = "grnt_01DEMO"
STARTED = "2026-10-01T10:00:00.123456+00:00"


def _call(
    sequence: int, tool: str, outcome: str, record_ids: tuple[str, ...] = (), grant_id: str = GRANT, reason: str = ""
) -> dict[str, Any]:
    return ToolCallRecord(
        sequence=sequence,
        tool=tool,
        capability="registry_lookup",
        provider="mock",
        outcome=outcome,
        reason=reason,
        input_sha256="sha256:" + "1" * 64,
        output_sha256=None if outcome in ("denied", "error", "not_available") else "sha256:" + "2" * 64,
        record_ids=record_ids,
        started_at=STARTED,
        grant_id=grant_id,
    ).to_dict()


def _run_record(**overrides: Any) -> dict[str, Any]:
    record = {
        "agent": "business_underwriter",
        "agent_version": "1.0.0",
        "run_id": RUN,
        "status": "completed",
        "failure_reason": "",
        "prompt": {"version": "3", "text": "redacted"},
        "model_id": "scripted",
        "policy_result": {
            "policy_id": "gb-onboarding",
            "policy_version": "2026.1",
            "policy_hash": "sha256:" + "a" * 64,
            "tier": "medium",
            "score": 40,
            "reasons": [
                {
                    "rule_id": "owner-missing",
                    "tier": "medium",
                    "score": 40,
                    "reason": "x",
                    "indeterminate": False,
                    "unresolved_paths": [],
                }
            ],
            "fired_rules": ["owner-missing"],
            "inputs": {"registry.status": "active", "owners.count": 0, "owners.list": ["a", "b"]},
        },
        "tool_calls": [
            _call(1, "registry.lookup", "ok", ("mock:registry:1",)),
            _call(2, "screening.screen", "ok", ("mock:screening:hit-1",)),
            _call(3, "ownership.graph", "denied", reason="tool_not_granted"),
            _call(4, "documents.fetch", "error"),
        ],
    }
    record.update(overrides)
    return record


MEMO = {
    "recommendation": {"proposed": "refer", "basis": "policy_result", "requires_human_decision": True},
    "sections": [
        {
            "section_id": "registry",
            "status": "complete",
            "evidence": [{"provider": "mock", "record_id": "mock:registry:1", "field": "status"}],
        },
        {
            "section_id": "ownership",
            "status": "partial",
            "evidence": [{"provider": "mock", "record_id": "mock:registry:1", "field": "owners"}],
        },
        {"section_id": "documents", "status": "error", "evidence": []},
        {
            "section_id": "orphan",
            "status": "complete",
            "evidence": [{"provider": "mock", "record_id": "never-retrieved"}],
        },
    ],
}


def _validate(record: dict[str, Any]) -> None:
    """The record's data against the service's schema definition for its type."""
    schema = load_schema()
    names = {
        "run_context": "runContext",
        "tool_call": "toolCall",
        "policy_evaluation": "policyEvaluation",
        "recommendation": "recommendation",
        "disposition": "disposition",
    }
    definition = {"$ref": f"#/$defs/{names[record['type']]}", "$defs": schema["$defs"]}
    jsonschema.validate(record["data"], definition)
    assert evidence_time(record["at"]) == record["at"]


# ── timestamps ──────────────────────────────────────────────────────────────


def test_evidence_time_is_utc_with_three_fraction_digits() -> None:
    assert evidence_time(STARTED) == "2026-10-01T10:00:00.123Z"
    assert evidence_time("2026-10-01T12:30:00+02:00") == "2026-10-01T10:30:00.000Z"
    assert evidence_time(datetime(2026, 10, 1, 1, 2, 3, 999_999, tzinfo=UTC)) == "2026-10-01T01:02:03.999Z"
    assert evidence_time(None, fallback=datetime(2026, 1, 1, tzinfo=UTC)) == "2026-01-01T00:00:00.000Z"


# ── record builders ─────────────────────────────────────────────────────────


def test_records_for_a_run_are_schema_valid_and_in_order() -> None:
    records = records_for_run(_run_record(), purpose="aml.cdd.onboarding", memo=MEMO)
    assert [r["type"] for r in records] == [
        "run_context",
        "tool_call",
        "tool_call",
        "tool_call",
        "tool_call",
        "policy_evaluation",
        "recommendation",
    ]
    for record in records:
        _validate(record)

    context = records[0]["data"]
    assert context["run_id"] == RUN and context["agent_id"] == "business_underwriter"
    assert context["policies"] == [{"id": "gb-onboarding", "version": "2026.1", "digest": "sha256:" + "a" * 64}]
    assert context["prompts"][0]["id"] == "business_underwriter.prompt" and context["prompts"][0]["version"] == "3"

    allowed = records[1]["data"]
    assert allowed == {
        "call_id": f"{RUN}:1",
        "connector": "mock",
        "provider": "mock",
        "tool": "registry.lookup",
        "grant_id": GRANT,
        "purpose": "aml.cdd.onboarding",
        "run_id": RUN,
        "started_at": "2026-10-01T10:00:00.123Z",
        "input_hash": "sha256:" + "1" * 64,
        "output_hash": "sha256:" + "2" * 64,
        "outcome": "allowed",
        "upstream_records": [{"record_id": "mock:registry:1", "retrieved_at": "2026-10-01T10:00:00.123Z"}],
    }
    denied = records[3]["data"]
    assert denied["outcome"] == "denied" and denied["denial"] == {"reason": "tool_not_granted"}
    assert denied["output_hash"] is None and denied["upstream_records"] == []
    errored = records[4]["data"]
    assert errored["outcome"] == "error" and "denial" not in errored and errored["output_hash"] is None

    evaluation = records[5]["data"]
    assert (
        evaluation["evaluation_id"] == f"{RUN}:policy" and evaluation["tier"] == "medium" and evaluation["score"] == 40
    )
    assert evaluation["fired_rules"] == [{"rule_id": "owner-missing", "reason_code": "owner-missing", "tier": "medium"}]
    # Policy inputs have no per-path provenance: recorded with their value, marked unsourced.
    assert all(i["unsourced"] is True and i["evidence"] == [] for i in evaluation["inputs"])
    assert {i["path"]: i["value"] for i in evaluation["inputs"]} == {
        "registry.status": "active",
        "owners.count": 0,
        "owners.list": '["a","b"]',
    }

    recommendation = records[6]["data"]
    assert recommendation["outcome"] == "refer" and recommendation["evaluation_ids"] == [f"{RUN}:policy"]
    by_section = {s["section"]: s for s in recommendation["sections"]}
    # Citations resolve to the call that retrieved the record, with its retrieved_at.
    assert by_section["registry"]["status"] == "complete"
    assert by_section["registry"]["evidence"] == [
        {
            "call_id": f"{RUN}:1",
            "provider": "mock",
            "record_id": "mock:registry:1",
            "retrieved_at": "2026-10-01T10:00:00.123Z",
            "field": "status",
        }
    ]
    assert by_section["ownership"]["status"] == "issues_found"
    assert by_section["documents"]["status"] == "not_available" and by_section["documents"]["evidence"] == []
    # A citation of a record no call retrieved cannot be sourced: the section is not available.
    assert by_section["orphan"]["status"] == "not_available"


def test_a_denied_call_before_any_grant_uses_the_runs_grant_and_calls_without_one_are_skipped() -> None:
    record = _run_record(
        tool_calls=[
            _call(1, "x", "denied", grant_id="", reason="tool_not_in_agent_tool_set"),
            _call(2, "registry.lookup", "ok", ("r",)),
        ]
    )
    calls = [r for r in records_for_run(record, purpose="p") if r["type"] == "tool_call"]
    assert [c["data"]["grant_id"] for c in calls] == [GRANT, GRANT]
    none = _run_record(tool_calls=[_call(1, "x", "ok", ("r",), grant_id="")], policy_result=None)
    assert [r["type"] for r in records_for_run(none, purpose="p")] == ["run_context"]


def test_a_disposition_cites_the_screening_call_of_an_earlier_run() -> None:
    underwriter = _run_record()
    disposition_run = {
        "agent": "screening_disposition",
        "agent_version": "1.0.0",
        "run_id": RUN.replace("underwriter", "disposition"),
        "status": "completed",
        "hit_id": "hit-1",
        "prompt": {"version": "1"},
        "model_id": "scripted",
        "tool_calls": [_call(1, "screening.rescreen", "ok", ("mock:screening:hit-1",))],
    }
    disposition = {
        "disposition_id": "dsp-1",
        "hit_id": "hit-1",
        "proposed_outcome": "false_positive",
        "confidence_band": "high",
        "rationale": "Names differ.",
        "evidence": [{"provider": "mock", "record_id": "mock:screening:hit-1", "field": "name"}],
        "comparisons": [
            {
                "identifier": "name",
                "result": "partial_match",
                "evidence": [{"provider": "mock", "record_id": "mock:screening:hit-1", "field": "name"}],
            },
            {"identifier": "date_of_birth", "result": "not_comparable", "evidence": []},
            {"identifier": "shoe_size", "result": "match", "evidence": []},
        ],
    }
    records = records_for_run(disposition_run, purpose="p", prior_records=[underwriter], disposition=disposition)
    assert [r["type"] for r in records] == ["run_context", "tool_call", "disposition"]
    for record in records:
        _validate(record)
    data = records[2]["data"]
    # The hit was first retrieved by the underwriter run's screening call.
    assert data["hit"]["call_id"] == f"{RUN}:2" and data["hit"]["record_id"] == "mock:screening:hit-1"
    assert [(c["identifier"], c["result"]) for c in data["comparisons"]] == [
        ("name", "partial"),
        ("date_of_birth", "not_available"),
    ]
    assert data["outcome"] == "false_positive" and data["confidence_band"] == "high"


def test_a_disposition_whose_hit_no_call_retrieved_is_not_recorded() -> None:
    run = {
        "agent": "screening_disposition",
        "agent_version": "1",
        "run_id": "r",
        "status": "completed",
        "tool_calls": [],
    }
    disposition = {
        "disposition_id": "d",
        "proposed_outcome": "true_match",
        "confidence_band": "low",
        "rationale": "",
        "evidence": [{"provider": "mock", "record_id": "nope"}],
        "comparisons": [],
    }
    assert [r["type"] for r in records_for_run(run, purpose="p", disposition=disposition)] == ["run_context"]


def test_index_resolves_the_first_retrieval_of_a_record() -> None:
    index = index_for([_run_record(tool_calls=[_call(1, "a", "ok", ("rec",)), _call(2, "b", "ok", ("rec",))])])
    assert index.resolve({"provider": "mock", "record_id": "rec"})["call_id"] == f"{RUN}:1"
    assert index.resolve({"provider": "other", "record_id": "rec"}) is None


# ── the grant id travels from the authorizer through the gateway ────────────


def test_tool_decision_and_record_carry_the_grant_id() -> None:
    assert ToolDecision(allowed=True).grant_id == ""
    assert ToolDecision(allowed=True, grant_id="grnt_1").grant_id == "grnt_1"
    assert _call(1, "t", "ok")["grant_id"] == GRANT


# ── the service client ──────────────────────────────────────────────────────


class FakeEvidenceService:
    """The evidence service's records and export routes, as the demo and the runtime use them."""

    def __init__(self, *, refuse: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.refuse = refuse

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.url.path, body))
        assert request.headers["authorization"] == "Bearer test-only-key"
        if self.refuse is not None:
            return httpx.Response(self.refuse["status"], json=self.refuse["body"])
        if request.url.path.endswith("/records"):
            return httpx.Response(
                201,
                json={
                    "case_id": "c",
                    "records": [
                        {"audit_entry_id": f"alog_{i}", "hash": "h" * 64, "duplicate": False}
                        for i, _ in enumerate(body["records"])
                    ],
                },
            )
        if request.url.path.endswith("/export"):
            return httpx.Response(
                200,
                content=b'{"format":"grantex-evidence-package"}',
                headers={
                    "grantex-evidence-root": "sha256:" + "f" * 64,
                    "grantex-evidence-anchor": "e" * 64,
                    "content-type": "application/json",
                },
            )
        return httpx.Response(404, json={"code": "NOT_FOUND"})

    def service(self) -> GrantexEvidenceService:
        transport = httpx.MockTransport(self.handle)
        return GrantexEvidenceService(
            base_url="http://grantex.test",
            api_key="test-only-key",
            client_factory=lambda: httpx.AsyncClient(transport=transport, base_url="http://grantex.test"),
        )


def test_the_client_posts_in_batches_of_100_and_exports() -> None:
    fake = FakeEvidenceService()
    service = fake.service()
    records = [{"type": "tool_call", "at": "2026-10-01T00:00:00.000Z", "data": {"n": i}} for i in range(250)]
    receipts = asyncio.run(service.record("case_x", records))
    assert len(receipts) == 250
    paths = [p for p, _ in fake.calls]
    assert paths == ["/v1/evidence/cases/case_x/records"] * 3
    assert [len(b["records"]) for _, b in fake.calls] == [100, 100, 50]
    exported = asyncio.run(service.export("case_x"))
    assert exported.root == "sha256:" + "f" * 64 and exported.anchor_hash == "e" * 64
    assert fake.calls[-1] == ("/v1/evidence/cases/case_x/export", {"disclose": [], "sign": True})


def test_a_refusal_keeps_the_services_code_and_field_path() -> None:
    fake = FakeEvidenceService(
        refuse={
            "status": 422,
            "body": {
                "code": "EVIDENCE_REFERENCE_INVALID",
                "message": "no such grant for this developer",
                "field_path": "records[0].data.grant_id",
            },
        }
    )
    with pytest.raises(EvidenceServiceError) as info:
        asyncio.run(fake.service().record("c", [{"type": "tool_call", "at": "x", "data": {}}]))
    assert info.value.reason == "EVIDENCE_REFERENCE_INVALID" and info.value.status == 422
    assert "records[0].data.grant_id" in info.value.detail


def test_the_service_needs_a_url_and_a_key() -> None:
    with pytest.raises(EvidenceServiceError) as info:
        GrantexEvidenceService(base_url="", api_key="k")
    assert info.value.reason == "evidence_service_not_configured"


# ── the runtime hook ────────────────────────────────────────────────────────


def test_record_run_evidence_is_off_by_default_and_posts_when_on() -> None:
    assert asyncio.run(record_run_evidence(lambda: None, case_ref="c", purpose="p", run_record=_run_record())) is None
    fake = FakeEvidenceService()
    summary = asyncio.run(
        record_run_evidence(
            fake.service, case_ref="case_1", purpose="aml.cdd.onboarding", run_record=_run_record(), memo=MEMO
        )
    )
    assert summary["records"] == 7 and len(summary["receipts"]) == 7
    assert fake.calls[0][0] == "/v1/evidence/cases/case_1/records"
    assert [r["type"] for r in fake.calls[0][1]["records"]][0] == "run_context"


def test_a_failing_sink_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    refusing = FakeEvidenceService(
        refuse={"status": 403, "body": {"code": "FEATURE_DISABLED", "message": "Evidence export is not enabled"}}
    )
    assert (
        asyncio.run(record_run_evidence(refusing.service, case_ref="c", purpose="p", run_record=_run_record())) is None
    )

    def broken() -> GrantexEvidenceService:
        raise EvidenceServiceError("evidence_service_not_configured", "GRANTEX_BASE_URL", status=503)

    assert asyncio.run(record_run_evidence(broken, case_ref="c", purpose="p", run_record=_run_record())) is None


def test_evidence_service_factory_follows_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(case_evidence.settings, "case_evidence_service", "off")
    assert case_evidence.evidence_service() is None
    monkeypatch.setattr(case_evidence.settings, "case_evidence_service", "grantex")
    monkeypatch.delenv("GRANTEX_BASE_URL", raising=False)
    monkeypatch.setattr(case_evidence.external_keys, "grantex_api_key", "k")
    with pytest.raises(EvidenceServiceError) as info:
        case_evidence.evidence_service()
    assert info.value.reason == "evidence_service_not_configured"
    monkeypatch.setenv("GRANTEX_BASE_URL", "http://grantex:3001")
    monkeypatch.setenv("GRANTEX_API_KEY", "sandbox-key")
    service = case_evidence.evidence_service()
    assert isinstance(service, GrantexEvidenceService) and service.base_url == "http://grantex:3001"
