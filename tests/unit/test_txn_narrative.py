# SPDX-License-Identifier: Apache-2.0
"""Transaction intelligence, part 3: narratives from the model or the facts, the evidence package, the queue."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.content import services
from core.txn import findings, graph, narrative, records
from core.txn.records import TxnError
from core.workbench import assignments, queue

TENANT = uuid.uuid4()
FINDING = {
    "id": "f1",
    "kind": "structuring",
    "entity_kind": "account",
    "entity_ref": "A1",
    "severity": "high",
    "status": "open",
    "summary": (
        "3 cash deposits under 1,000,000 on account A1 between 2026-09-01 and 2026-09-04 total 1,050,000 "
        "across 2 branches"
    ),
    "facts": {
        "count": 3,
        "total": 1_050_000,
        "threshold": 1_000_000,
        "window_days": 7,
        "window_start": "2026-09-01T10:00:00+00:00",
        "window_end": "2026-09-04T08:00:00+00:00",
        "branches": ["Mumbai", "Pune"],
        "largest": 400_000,
    },
    "record_refs": ["s1", "s2", "s3"],
    "detected_at": "2026-09-05T00:00:00+00:00",
}
ROWS = [
    {
        "record_ref": "s1",
        "account": "A1",
        "customer_ref": None,
        "counterparty": None,
        "counterparty_name": None,
        "direction": "credit",
        "amount": 400_000,
        "channel": "cash",
        "branch": "Pune",
        "booked_at": "2026-09-01T10:00:00+00:00",
    },
    {
        "record_ref": "s2",
        "account": "A1",
        "customer_ref": None,
        "counterparty": "D1",
        "counterparty_name": None,
        "direction": "credit",
        "amount": 350_000,
        "channel": "cash",
        "branch": "Mumbai",
        "booked_at": "2026-09-02T16:00:00+00:00",
    },
    {
        "record_ref": "s3",
        "account": "A1",
        "customer_ref": None,
        "counterparty": None,
        "counterparty_name": None,
        "direction": "credit",
        "amount": 300_000,
        "channel": "cash",
        "branch": "Pune",
        "booked_at": "2026-09-04T08:00:00+00:00",
    },
]
VIEW = {
    "kind": "account",
    "ref": "A1",
    "totals": {"in": 1_050_000, "out": 50_000},
    "cash_share": 1.0,
    "customers": ["C1"],
    "by_channel": {},
    "by_branch": {},
}


class TestNarrative:
    def test_the_extractive_draft_writes_every_section_from_the_facts(self):
        text = narrative.extractive(FINDING, ROWS, VIEW)
        assert text["method"] == "extractive" and text["title"] == "Structuring on account A1"
        assert (
            "between 2026-09-01 and 2026-09-04" in text["summary"]
            and "3 movement(s)" in text["summary"]
            and "100 per cent of receipts in cash" in text["summary"]
        )
        assert (
            text["timeline"][0].startswith("2026-09-01: 400,000 in by cash at Pune, from unknown")
            and len(text["timeline"]) == 3
        )
        assert text["parties"] == ["account A1", "D1", "customer C1"]
        assert (
            text["basis"][0].startswith("3 cash deposits each under the reporting threshold of 1,000,000")
            and "across 2 branches" in text["basis"][1]
        )
        assert (
            text["recommendation"] == "escalate"
            and "no customer is linked" in text["gaps"][0]
            and "name no counterparty" in text["gaps"][1]
        )
        medium = narrative.extractive(
            {
                **FINDING,
                "severity": "medium",
                "kind": "pass_through",
                "facts": {"inflow": 100, "outflow": 90, "ratio": 0.9, "hours": 5, "from": "X", "to": ["Y"]},
            },
            [],
            None,
        )
        assert medium["recommendation"] == "confirm" and medium["basis"][0].startswith(
            "100 received from X and 90 (90 per cent) paid out within 5 hours"
        )
        other = narrative.extractive({"kind": "odd", "entity_ref": "Z", "summary": "something", "facts": {}}, [], None)
        assert other["basis"][0] == "something" and other["title"] == "Odd on account Z"

    @pytest.mark.asyncio
    async def test_the_model_draft_goes_through_the_checked_call_and_falls_back(self):
        async def complete(tenant_id, model, messages, max_tokens):
            assert "Transaction" in messages[0]["content"] or "narratives" in messages[0]["content"]
            payload = json.loads(messages[1]["content"])
            assert payload["finding"]["entity_ref"] == "A1" and len(payload["movements"]) == 3
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "title": "Cash deposits structured on A1",
                        "summary": "Three cash deposits just under the threshold.",
                        "timeline": ["day one"],
                        "parties": ["A1"],
                        "basis": ["three deposits"],
                        "recommendation": "escalate",
                        "gaps": ["identify the depositor"],
                    }
                ),
                tokens_used=200,
                model="m",
            )

        text = await narrative.draft(TENANT, FINDING, ROWS, VIEW, method="model", complete=complete)
        assert (
            text["method"] == "model"
            and text["recommendation"] == "escalate"
            and text["model"]["tokens"] == 200
            and text["gaps"] == ["identify the depositor"]
        )

        async def broken(tenant_id, model, messages, max_tokens):
            raise RuntimeError("down")

        fallback = await narrative.draft(TENANT, FINDING, ROWS, VIEW, method="auto", complete=broken)
        assert fallback["method"] == "extractive" and fallback["fallback_from"] == "model"
        with pytest.raises(services.ContentError):  # the checked call names the model failure
            await narrative.draft(TENANT, FINDING, ROWS, VIEW, method="model", complete=broken)
        with pytest.raises(ValueError):
            await narrative.draft(TENANT, FINDING, ROWS, VIEW, method="magic")

    def test_the_digest_covers_the_package_without_itself(self):
        package = {"finding": {"id": "f1"}, "records": [], "digest": "x"}
        first = narrative.digest(package)
        assert len(first) == 64 and narrative.digest({**package, "digest": "other"}) == first
        assert narrative.digest({**package, "records": [1]}) != first


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    async def execute(self, statement):
        table = statement.get_final_froms()[0].name
        return _Result([r for r in self.rows if getattr(r, "__tablename__", "") == table])

    def add(self, row):
        row.id = row.id or uuid.uuid4()
        self.rows.append(row)

    async def flush(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _finding_row(**kw):
    from core.models.txn_finding import TxnFinding

    base = {
        "tenant_id": TENANT,
        "kind": "structuring",
        "entity_kind": "account",
        "entity_ref": "A1",
        "severity": "high",
        "status": "open",
        "summary": FINDING["summary"],
        "facts": FINDING["facts"],
        "record_refs": ["s1", "s2", "s3"],
        "fingerprint": "fp1",
        "detected_at": datetime(2026, 9, 5, tzinfo=UTC),
        "disposition": {},
        "narrative": {},
        "narrative_at": None,
        "evidence_digest": None,
    }
    base.update(kw)
    row = TxnFinding(**base)
    row.id = uuid.uuid4()
    return row


def _use(monkeypatch, session):
    import core.database

    monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: session)


class TestStore:
    @pytest.mark.asyncio
    async def test_a_narrative_is_drafted_and_kept_and_the_evidence_is_packaged_with_a_digest(self, monkeypatch):
        row = _finding_row()
        session = _Session([row])
        _use(monkeypatch, session)
        monkeypatch.setattr(
            findings, "get_finding", AsyncMock(return_value={**FINDING, "id": str(row.id), "records": ROWS})
        )
        monkeypatch.setattr(findings, "entity", AsyncMock(return_value=VIEW))
        drafted = await findings.draft_narrative(TENANT, row.id, method="extractive")
        assert (
            drafted["narrative"]["method"] == "extractive"
            and drafted["narrative_at"]
            and row.narrative["recommendation"] == "escalate"
        )
        assert drafted["status"] == "open"  # the draft files nothing
        monkeypatch.setattr(
            graph,
            "export",
            AsyncMock(return_value={"graph": {"nodes": []}, "records": [{"hop": 1}], "csv": "hop,from\n"}),
        )
        package = await findings.evidence(TENANT, row.id)
        assert (
            package["finding"]["entity_ref"] == "A1"
            and "records" not in package["finding"]
            and package["records"] == ROWS
        )
        assert (
            package["entity"] == VIEW
            and package["fund_flow"]["records"] == [{"hop": 1}]
            and package["csv"] == "hop,from\n"
        )
        assert len(package["digest"]) == 64 and row.evidence_digest == package["digest"]
        assert graph.export.call_args.args[1:] == ("account", "A1")
        with pytest.raises(TxnError) as info:
            await findings.draft_narrative(TENANT, row.id, method="magic")
        assert info.value.code == "method_unknown"
        monkeypatch.setattr(findings, "get_finding", AsyncMock(return_value=None))
        with pytest.raises(TxnError) as info:
            await findings.draft_narrative(TENANT, uuid.uuid4())
        assert info.value.status == 404
        with pytest.raises(TxnError):
            await findings.evidence(TENANT, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_a_model_that_fails_when_insisted_on_is_an_explicit_error(self, monkeypatch):
        row = _finding_row()
        _use(monkeypatch, _Session([row]))
        monkeypatch.setattr(
            findings, "get_finding", AsyncMock(return_value={**FINDING, "id": str(row.id), "records": ROWS})
        )
        monkeypatch.setattr(findings, "entity", AsyncMock(return_value=VIEW))

        async def broken(tenant_id, model, messages, max_tokens):
            raise RuntimeError("down")

        with pytest.raises(TxnError) as info:
            await findings.draft_narrative(TENANT, row.id, method="model", complete=broken)
        assert info.value.code == "narrative_failed" and row.narrative == {}


class TestQueue:
    @pytest.mark.asyncio
    async def test_open_findings_join_the_review_queue_and_are_decided_through_the_store(self, monkeypatch):
        monkeypatch.setattr(settings, "transaction_intelligence_enabled", True)
        monkeypatch.setattr(settings, "idp_enabled", True)
        monkeypatch.setattr(settings, "content_services_enabled", True)
        assert queue.KINDS["finding"] == "transactions" and "finding" in queue.enabled_kinds()
        assert "finding" in queue.kinds_for("auditor") and "finding" not in queue.kinds_for("cmo")
        # the transactions tab is sensitive: the queue tab of the review officer does not open findings
        assert "finding" in queue.kinds_for("cfo") and "finding" in queue.kinds_for("coo")
        assert "finding" not in queue.kinds_for("domain_lead") and "finding" not in queue.kinds_for("developer")
        assert "finding" not in queue.kinds_for("cmo", {"investigator"})  # holding the workbench is not enough
        assert queue.sensitive_roles("transactions") == {"admin", "coo", "auditor", "cfo"}
        assert queue.sensitive_roles("approvals") is None
        monkeypatch.setattr(settings, "transaction_intelligence_enabled", False)
        assert "finding" not in queue.enabled_kinds()
        monkeypatch.setattr(settings, "transaction_intelligence_enabled", True)
        row = _finding_row(narrative={"summary": "x"})
        session = _Session([row])
        _use(monkeypatch, session)
        from core.workbench import console

        monkeypatch.setattr(console, "value", AsyncMock(return_value=[]))
        listed = await queue.list_items(TENANT, ["finding"], limit=5)
        item = listed["items"][0]
        assert (
            item["kind"] == "finding"
            and item["title"] == "Structuring on account A1"
            and item["priority"] == "high"
            and item["narrative"] is True
        )
        assert item["facts"]["count"] == 3 and "branches" not in item["facts"] and listed["counts"] == {"finding": 1}
        monkeypatch.setattr(
            findings, "get_finding", AsyncMock(return_value={**FINDING, "id": str(row.id), "records": ROWS})
        )
        detail = await queue.get_item(TENANT, "finding", str(row.id))
        assert detail["decidable"] is True and detail["editable"] == [] and detail["item"]["records"] == ROWS
        with pytest.raises(queue.QueueError):
            await queue.get_item(TENANT, "finding", "not-a-uuid")

        from api.v1 import workbench_queue as api

        monkeypatch.setattr(settings, "workbench_v2_enabled", True)
        monkeypatch.setattr(assignments, "assigned_to", AsyncMock(return_value=set()))
        monkeypatch.setattr(queue, "apply_edits", AsyncMock(return_value=None))
        monkeypatch.setattr(findings, "disposition", AsyncMock(return_value={"id": str(row.id), "status": "dismissed"}))
        auditor = SimpleNamespace(
            state=SimpleNamespace(claims={"agenticorg:user_id": "u1", "role": "auditor"}, scopes=["audit:read"])
        )
        for decision in ("approve", "reject"):
            with pytest.raises(HTTPException) as refused:
                await api.decide(
                    "finding",
                    str(row.id),
                    api.DecisionIn(decision=decision, edits=[api.EditIn(name="title", value="Not authorized")]),
                    SimpleNamespace(),
                    auditor,
                    role="auditor",
                    tenant_id=str(TENANT),
                    user_claims={},
                    user_domains=None,
                )
            assert refused.value.status_code == 403
            assert "approvals:write" in refused.value.detail["message"]
            queue.apply_edits.assert_not_awaited()
            findings.disposition.assert_not_awaited()

        request = SimpleNamespace(
            state=SimpleNamespace(claims={"agenticorg:user_id": "u1", "role": "cfo"}, scopes=["approvals:write"])
        )
        out = await api.decide(
            "finding",
            str(row.id),
            api.DecisionIn(decision="reject", notes="a known payroll run"),
            SimpleNamespace(),
            request,
            role="cfo",
            tenant_id=str(TENANT),
            user_claims={},
            user_domains=None,
        )
        assert out["outcome"]["status"] == "dismissed" and findings.disposition.call_args.kwargs["outcome"] == "dismiss"
        assert findings.disposition.call_args.kwargs["notes"] == "a known payroll run"
        await api.decide(
            "finding",
            str(row.id),
            api.DecisionIn(decision="approve"),
            SimpleNamespace(),
            request,
            role="cfo",
            tenant_id=str(TENANT),
            user_claims={},
            user_domains=None,
        )
        assert findings.disposition.call_args.kwargs["outcome"] == "confirm"
        queue.apply_edits.reset_mock()
        findings.disposition.reset_mock()
        for state in (
            SimpleNamespace(claims={"role": "cfo"}, scopes=["approvals:write"]),  # no signed-in person
            SimpleNamespace(
                claims={"agenticorg:user_id": "u1", "sub": "apikey:k1", "role": "cfo"},
                scopes=["approvals:write"],
                auth_mode="api_key",
            ),
        ):
            with pytest.raises(HTTPException) as refused:
                await api.decide(
                    "finding",
                    str(row.id),
                    api.DecisionIn(decision="approve"),
                    SimpleNamespace(),
                    SimpleNamespace(state=state),
                    role="cfo",
                    tenant_id=str(TENANT),
                    user_claims={},
                    user_domains=None,
                )
            assert refused.value.status_code == 403 and refused.value.detail["error"] == "human_required"
            queue.apply_edits.assert_not_awaited()
            findings.disposition.assert_not_awaited()
        monkeypatch.setattr(findings, "disposition", AsyncMock(side_effect=TxnError(409, "decided", "no")))
        with pytest.raises(HTTPException) as info:
            await api.decide(
                "finding",
                str(row.id),
                api.DecisionIn(decision="approve"),
                SimpleNamespace(),
                request,
                role="cfo",
                tenant_id=str(TENANT),
                user_claims={},
                user_domains=None,
            )
        assert info.value.status_code == 409


class TestRoutes:
    @pytest.mark.asyncio
    async def test_the_narrative_and_evidence_routes(self, monkeypatch):
        from api.v1 import txn as api

        monkeypatch.setattr(settings, "transaction_intelligence_enabled", False)
        for call in (
            api.draft_narrative(uuid.uuid4(), method="auto", tenant_id=str(TENANT)),
            api.finding_evidence(uuid.uuid4(), hops=2, output="json", tenant_id=str(TENANT)),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404
        monkeypatch.setattr(settings, "transaction_intelligence_enabled", True)
        monkeypatch.setattr(
            findings, "draft_narrative", AsyncMock(return_value={"id": "f", "narrative": {"method": "extractive"}})
        )
        monkeypatch.setattr(
            findings,
            "evidence",
            AsyncMock(return_value={"finding": {}, "records": [], "digest": "d", "csv": "hop,from\n"}),
        )
        assert (await api.draft_narrative(uuid.uuid4(), method="extractive", tenant_id=str(TENANT)))["narrative"][
            "method"
        ] == "extractive"
        assert findings.draft_narrative.call_args.kwargs["method"] == "extractive"
        as_json = await api.finding_evidence(uuid.uuid4(), hops=2, output="json", tenant_id=str(TENANT))
        assert as_json["digest"] == "d" and "csv" not in as_json
        as_csv = await api.finding_evidence(uuid.uuid4(), hops=2, output="csv", tenant_id=str(TENANT))
        assert as_csv.media_type == "text/csv" and b"hop,from" in as_csv.body
        with pytest.raises(HTTPException) as info:
            await api.finding_evidence(uuid.uuid4(), hops=2, output="pdf", tenant_id=str(TENANT))
        assert info.value.status_code == 422
        monkeypatch.setattr(findings, "draft_narrative", AsyncMock(side_effect=TxnError(502, "narrative_failed", "no")))
        with pytest.raises(HTTPException) as info:
            await api.draft_narrative(uuid.uuid4(), method="model", tenant_id=str(TENANT))
        assert info.value.status_code == 502
        assert records.enabled() is True
