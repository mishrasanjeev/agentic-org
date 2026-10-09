# SPDX-License-Identifier: Apache-2.0
"""Transaction intelligence, part 1: records, entity aggregation, the detectors, findings and disposition, routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.dml import Insert

from core.config import settings
from core.txn import aggregate, detectors, findings, records
from core.txn.records import TxnError
from core.workbench import console

TENANT = uuid.uuid4()
T0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def rec(
    ref: str,
    account: str,
    direction: str,
    amount: float,
    *,
    hours: float = 0.0,
    channel: str = "transfer",
    branch: str | None = None,
    counterparty: str | None = None,
    customer: str | None = "C1",
    description: str = "",
) -> dict:
    return {
        "record_ref": ref,
        "account": account,
        "customer_ref": customer,
        "counterparty": counterparty,
        "counterparty_name": None,
        "direction": direction,
        "amount": amount,
        "currency": "INR",
        "channel": channel,
        "branch": branch,
        "booked_at": (T0 + timedelta(hours=hours)).isoformat(),
        "description": description,
        "source": "api",
        "attributes": {},
    }


STRUCTURED = [
    rec("s1", "A1", "credit", 400_000, hours=0, channel="cash", branch="Pune"),
    rec("s2", "A1", "credit", 350_000, hours=30, channel="cash", branch="Mumbai"),
    rec("s3", "A1", "credit", 300_000, hours=70, channel="cash", branch="Pune"),
    rec("s4", "A1", "debit", 50_000, hours=80, channel="upi", counterparty="M1"),
]
PASSTHROUGH = [
    rec("p1", "A2", "credit", 1_000_000, hours=0, counterparty="X1"),
    rec("p2", "A2", "debit", 600_000, hours=5, counterparty="Y1"),
    rec("p3", "A2", "debit", 350_000, hours=20, counterparty="Y2"),
    rec("p4", "A2", "debit", 10_000, hours=100, counterparty="Y3"),
]


class TestRecords:
    def test_records_are_checked_normalised_and_given_a_reference(self):
        item = records.check_record(
            {
                "account": " A1 ",
                "direction": "Credit",
                "amount": "1500.5",
                "booked_at": "2026-09-01T10:00:00Z",
                "description": "NEFT from X",
            }
        )
        assert (
            item["account"] == "A1"
            and item["direction"] == "credit"
            and item["amount"] == 1500.5
            and item["channel"] == "transfer"
        )
        assert (
            item["record_ref"].startswith("r-") and item["currency"] == "INR" and item["booked_at"].tzinfo is not None
        )
        assert (
            records.check_record(
                {
                    "account": "A",
                    "direction": "debit",
                    "amount": 1,
                    "booked_at": "03/09/2026",
                    "description": "CASH DEP",
                }
            )["channel"]
            == "cash"
        )
        assert (
            records.check_record(
                {"account": "A", "direction": "debit", "amount": 1, "booked_at": "2026-09-03", "channel": "UPI"}
            )["channel"]
            == "upi"
        )
        for bad in (
            {"direction": "credit", "amount": 1, "booked_at": "2026-09-01"},
            {"account": "A", "direction": "sideways", "amount": 1, "booked_at": "2026-09-01"},
            {"account": "A", "direction": "credit", "amount": -1, "booked_at": "2026-09-01"},
            {"account": "A", "direction": "credit", "amount": 1, "booked_at": "someday"},
            "x",
        ):
            with pytest.raises(TxnError) as info:
                records.check_record(bad)
            assert info.value.status == 422
        assert (
            records.channel_of("CHQ 12345") == "cheque"
            and records.channel_of("POS purchase") == "card"
            and records.channel_of("??") == "other"
        )

    def test_a_statement_becomes_records_on_its_account(self):
        document = {
            "index": 0,
            "document_type": "bank_statement",
            "fields": [
                {"name": "account_number", "value": "XXXX1234"},
                {"name": "name", "value": "Ravi"},
                {"name": "opening_balance", "value": "1,000"},
            ],
            "tables": [
                {
                    "header": ["Date", "Description", "Debit", "Credit", "Balance"],
                    "rows": [
                        ["01-09-2026", "CASH DEP", "", "500", "1,500"],
                        ["02-09-2026", "NEFT to X", "200", "", "1,300"],
                    ],
                }
            ],
        }
        out = records.records_from_statement(document, source="statement:d1")
        assert [(r["direction"], r["amount"], r["channel"]) for r in (records.check_record(r) for r in out)] == [
            ("credit", 500.0, "cash"),
            ("debit", 200.0, "transfer"),
        ]
        assert (
            out[0]["account"] == "XXXX1234" and out[0]["customer_ref"] == "Ravi" and out[0]["source"] == "statement:d1"
        )
        with pytest.raises(TxnError) as info:
            records.records_from_statement({"fields": [], "tables": []}, source="x")
        assert info.value.code == "account_unknown"

    def test_identical_statement_lines_are_distinct_movements(self):
        header = ["Date", "Description", "Debit", "Credit", "Balance"]
        row = ["01-09-2026", "CASH DEP", "", "500", "1,500"]
        document = {
            "index": 0,
            "document_type": "bank_statement",
            "fields": [{"name": "account_number", "value": "X1"}],
            "tables": [{"header": header, "rows": [row, row]}],
        }
        refs = [
            records.check_record(r)["record_ref"]
            for r in records.records_from_statement(document, source="statement:d1")
        ]
        assert len(refs) == 2 and len(set(refs)) == 2  # the row number tells two identical lines apart
        same = {"account": "A", "direction": "credit", "amount": 1, "booked_at": "2026-09-01", "description": "x"}
        assert records.check_record(same)["record_ref"] == records.check_record(dict(same))["record_ref"]
        assert (
            records.check_record(same)["record_ref"] != records.check_record(dict(same, source="other"))["record_ref"]
        )


class TestDetectors:
    def test_structuring_needs_several_cash_deposits_under_the_threshold_within_the_window(self):
        found = detectors.structuring(
            STRUCTURED,
            detectors.Thresholds(structuring_threshold=1_000_000, structuring_window_days=7, structuring_min_count=3),
        )
        assert len(found) == 1
        finding = found[0]
        assert finding["kind"] == "structuring" and finding["entity_ref"] == "A1" and finding["severity"] == "high"
        assert (
            finding["record_refs"] == ["s1", "s2", "s3"]
            and finding["facts"]["branches"] == ["Mumbai", "Pune"]
            and finding["facts"]["total"] == 1_050_000
        )
        assert "across 2 branches" in finding["summary"] and len(finding["fingerprint"]) == 32
        assert (
            detectors.structuring(
                STRUCTURED, detectors.Thresholds(structuring_threshold=1_000_000, structuring_window_days=1)
            )
            == []
        )
        assert (
            detectors.structuring(STRUCTURED, detectors.Thresholds(structuring_threshold=300_000)) == []
        )  # 400k is not under the threshold
        assert detectors.structuring(STRUCTURED, detectors.Thresholds(structuring_min_count=4)) == []
        same_branch = [dict(r, branch="Pune") for r in STRUCTURED]
        assert detectors.structuring(same_branch)[0]["severity"] == "medium"

    def test_pass_through_needs_most_of_an_inflow_to_leave_within_the_window(self):
        found = detectors.pass_through(
            PASSTHROUGH,
            detectors.Thresholds(passthrough_window_hours=48, passthrough_ratio=0.8, passthrough_min_amount=100_000),
        )
        assert len(found) == 1
        finding = found[0]
        assert (
            finding["kind"] == "pass_through"
            and finding["record_refs"] == ["p1", "p2", "p3"]
            and finding["severity"] == "high"
        )
        assert (
            finding["facts"]["ratio"] == 0.95
            and finding["facts"]["from"] == "X1"
            and finding["facts"]["to"] == ["Y1", "Y2"]
            and finding["facts"]["hours"] == 20.0
        )
        assert detectors.pass_through(PASSTHROUGH, detectors.Thresholds(passthrough_ratio=0.99)) == []
        assert detectors.pass_through(PASSTHROUGH, detectors.Thresholds(passthrough_window_hours=4)) == []
        assert detectors.pass_through(PASSTHROUGH, detectors.Thresholds(passthrough_min_amount=2_000_000)) == []
        slow = [dict(r) for r in PASSTHROUGH]
        slow[2]["booked_at"] = (T0 + timedelta(hours=40)).isoformat()
        assert detectors.pass_through(slow)[0]["severity"] == "medium"
        assert [f["kind"] for f in detectors.run_all(STRUCTURED + PASSTHROUGH)] == ["structuring", "pass_through"]
        assert detectors.run_all(STRUCTURED + PASSTHROUGH, kinds=["pass_through"])[0]["kind"] == "pass_through"
        assert detectors.fingerprint("x", "A", ["b", "a"]) == detectors.fingerprint("x", "A", ["a", "b"])


class TestAggregation:
    def test_an_entity_view_sums_splits_and_names_the_counterparties(self):
        rows = STRUCTURED + PASSTHROUGH
        view = aggregate.entity_view(rows, "account", "A1", [{"entity_ref": "A1", "kind": "structuring", "facts": {}}])
        assert view["records"] == 4 and view["totals"] == {
            "in": 1_050_000.0,
            "out": 50_000.0,
            "credits": 3,
            "debits": 1,
            "net": 1_000_000.0,
        }
        assert view["by_channel"]["cash"]["in"] == 1_050_000.0 and view["by_branch"]["Pune"]["in"] == 700_000.0
        assert (
            view["counterparties"][0]["name"] in ("unknown", "M1")
            and view["cash_share"] == 1.0
            and len(view["findings"]) == 1
        )
        assert [s["date"] for s in view["series"]] == ["2026-09-01", "2026-09-02", "2026-09-04"]
        customer = aggregate.entity_view(rows, "customer", "C1")
        assert customer["records"] == 8 and customer["accounts"] == ["A1", "A2"]
        counterparty = aggregate.entity_view(rows, "counterparty", "Y1")
        assert counterparty["records"] == 1 and counterparty["counterparties"][0]["name"] == "A2"
        empty = aggregate.entity_view(rows, "account", "nothing")
        assert empty["records"] == 0 and empty["first_at"] is None and empty["cash_share"] == 0.0

    def test_entities_are_listed_with_their_volume(self):
        found = aggregate.entities(STRUCTURED + PASSTHROUGH)
        kinds = {(e["kind"], e["ref"]) for e in found}
        assert ("account", "A1") in kinds and ("customer", "C1") in kinds and ("counterparty", "Y2") in kinds
        accounts = aggregate.entities(STRUCTURED + PASSTHROUGH, kind="account")
        assert [e["ref"] for e in accounts] == ["A2", "A1"] or [e["ref"] for e in accounts] == ["A1", "A2"]
        assert (
            aggregate.entities(STRUCTURED, query="a1")[0]["ref"] == "A1"
            and aggregate.entities(STRUCTURED, query="zzz") == []
        )


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
        self.statements: list[str] = []

    async def execute(self, statement):
        text = str(statement)
        self.statements.append(text)
        if isinstance(statement, Insert):
            from core.models.txn_finding import TxnFinding
            from core.models.txn_record import TxnRecord

            model, key = (
                (TxnRecord, "record_ref") if statement.table.name == "txn_records" else (TxnFinding, "fingerprint")
            )
            params = statement.compile().params
            inserted = []
            index = 0
            while f"{key}_m{index}" in params:
                values = {
                    column.name: params[f"{column.name}_m{index}"]
                    for column in model.__table__.columns
                    if f"{column.name}_m{index}" in params
                }
                if not any(
                    row.tenant_id == values["tenant_id"] and getattr(row, key) == values[key]
                    for row in self.rows
                    if row.__tablename__ == model.__tablename__
                ):
                    self.add(model(**values))
                    inserted.append(values[key])
                index += 1
            return _Result(inserted)
        table = statement.get_final_froms()[0].name
        if text.startswith("SELECT txn_records.record_ref") or text.startswith("SELECT txn_findings.fingerprint"):
            attr = "record_ref" if "record_ref" in text[:40] else "fingerprint"
            return _Result([getattr(r, attr) for r in self.rows if r.__tablename__ == table])
        return _Result([r for r in self.rows if r.__tablename__ == table])

    def add(self, row):
        row.id = row.id or uuid.uuid4()
        self.rows.append(row)

    async def flush(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _use(monkeypatch, session):
    import core.database

    monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: session)


class TestStore:
    @pytest.mark.asyncio
    async def test_ingest_keeps_each_reference_once(self, monkeypatch):
        session = _Session()
        _use(monkeypatch, session)
        first = await records.ingest(TENANT, STRUCTURED, source="core")
        assert (
            first == {"received": 4, "kept": 4, "skipped": 0, "accounts": ["A1"]} and session.rows[0].source == "core"
        )
        again = await records.ingest(TENANT, STRUCTURED + [dict(STRUCTURED[0])])
        assert again["kept"] == 0 and again["skipped"] == 5
        with pytest.raises(TxnError):
            await records.ingest(TENANT, [])
        listed = await records.list_records(TENANT, account="A1", since=T0 - timedelta(days=1))
        assert (
            len(listed) == 4 and listed[0]["record_ref"] == "s1" and "txn_records.account =" in session.statements[-1]
        )
        await records.list_records(TENANT, counterparty="Ravi Traders")
        assert "txn_records.counterparty_name =" in session.statements[-1]  # a counterparty known only by name

    @pytest.mark.asyncio
    async def test_ingest_counts_and_provenance_follow_inserted_refs_not_attempted_rows(self, monkeypatch):
        from core.lineage import provenance

        session = _Session()
        session.execute = AsyncMock(return_value=_Result(["new"]))
        _use(monkeypatch, session)
        noted = AsyncMock()
        monkeypatch.setattr(provenance, "on_records", noted)
        existing = dict(STRUCTURED[0], record_ref="already-kept")
        fresh = dict(STRUCTURED[0], record_ref="new", source="statement:synthetic")
        duplicate = dict(fresh, amount=1, source="not-kept")
        out = await records.ingest(TENANT, [existing, fresh, duplicate])
        assert out == {"received": 3, "kept": 1, "skipped": 2, "accounts": ["A1"]}
        session.execute.assert_awaited_once()
        statement = session.execute.call_args.args[0]
        assert "ON CONFLICT (tenant_id, record_ref) DO NOTHING RETURNING txn_records.record_ref" in str(statement)
        params = statement.compile().params
        assert params["tenant_id_m0"] == params["tenant_id_m1"] == TENANT
        assert params["record_ref_m0"] == "already-kept" and params["record_ref_m1"] == "new"
        assert params["amount_m1"] == fresh["amount"]
        noted.assert_awaited_once_with(TENANT, source="statement:synthetic", records=[records.check_record(fresh)])

    @pytest.mark.asyncio
    async def test_invalid_duplicate_is_validated_before_opening_a_session(self, monkeypatch):
        import core.database
        from core.lineage import provenance

        opened = AsyncMock()
        noted = AsyncMock()
        monkeypatch.setattr(core.database, "get_tenant_session", opened)
        monkeypatch.setattr(provenance, "on_records", noted)
        with pytest.raises(TxnError) as refused:
            await records.ingest(TENANT, [STRUCTURED[0], dict(STRUCTURED[0], amount=-1)])
        assert refused.value.status == 422 and refused.value.code == "record_invalid"
        opened.assert_not_called()
        noted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_provenance_is_recorded_when_commit_fails(self, monkeypatch):
        from core.lineage import provenance

        class FailedCommitSession(_Session):
            async def __aexit__(self, *args):
                raise RuntimeError("synthetic commit failure")

        _use(monkeypatch, FailedCommitSession())
        noted = AsyncMock()
        monkeypatch.setattr(provenance, "on_records", noted)
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            await records.ingest(TENANT, STRUCTURED)
        noted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_detect_keeps_new_findings_once_and_a_person_dispositions_them(self, monkeypatch):
        session = _Session()
        _use(monkeypatch, session)
        monkeypatch.setattr(records, "list_records", AsyncMock(return_value=STRUCTURED + PASSTHROUGH))
        monkeypatch.setattr(console, "effective", AsyncMock(return_value={"txn.structuring_threshold": 1_000_000}))
        ran = await findings.detect(TENANT)
        assert ran["new"] == 2 and ran["known"] == 0 and ran["thresholds"]["structuring_threshold"] == 1_000_000.0
        again = await findings.detect(TENANT, kinds=["structuring"])
        assert again["new"] == 0 and again["known"] == 1
        rows = [r for r in session.rows if r.__tablename__ == "txn_findings"]
        assert len(rows) == 2 and rows[0].status == "open"
        listed = await findings.list_findings(TENANT)
        assert len(listed) == 2
        detail = await findings.get_finding(TENANT, rows[0].id, with_records=False)
        assert detail["kind"] == "structuring" and detail["record_refs"] == ["s1", "s2", "s3"]
        with pytest.raises(TxnError) as info:
            await findings.disposition(TENANT, rows[0].id, outcome="dismiss", notes="  ", user_id="u1")
        assert info.value.code == "reason_required"
        done = await findings.disposition(
            TENANT, rows[0].id, outcome="escalate", notes="opening a case", user_id="u1", case_ref="KYB-9"
        )
        assert done["status"] == "escalated" and done["case_ref"] == "KYB-9" and done["disposition"]["by"] == "u1"
        with pytest.raises(TxnError) as info:
            await findings.disposition(TENANT, rows[0].id, outcome="confirm", notes="", user_id="u1")
        assert info.value.code == "decided"
        with pytest.raises(TxnError):
            await findings.disposition(TENANT, rows[0].id, outcome="magic", notes="", user_id="u1")
        with pytest.raises(TxnError) as info:
            await findings.detect(TENANT, kinds=["nothing"])
        assert info.value.code == "kind_unknown"
        session.rows = []
        assert await findings.get_finding(TENANT, uuid.uuid4()) is None
        with pytest.raises(TxnError) as info:
            await findings.disposition(TENANT, uuid.uuid4(), outcome="confirm", notes="", user_id="u1")
        assert info.value.status == 404

    @pytest.mark.asyncio
    async def test_detect_reports_only_findings_actually_inserted_and_keeps_first_duplicate(self, monkeypatch):
        detected = detectors.run_all(STRUCTURED + PASSTHROUGH, detectors.Thresholds())
        duplicate = dict(detected[1], summary="Ignored duplicate")
        session = _Session()
        session.execute = AsyncMock(return_value=_Result([detected[1]["fingerprint"]]))
        _use(monkeypatch, session)
        monkeypatch.setattr(records, "list_records", AsyncMock(return_value=STRUCTURED + PASSTHROUGH))
        monkeypatch.setattr(findings, "thresholds_for", AsyncMock(return_value=detectors.Thresholds()))
        monkeypatch.setattr(detectors, "run_all", lambda *args, **kwargs: [*detected, duplicate])
        out = await findings.detect(TENANT)
        assert out["new"] == 1 and out["known"] == 2 and out["findings"] == [detected[1]]
        session.execute.assert_awaited_once()
        statement = session.execute.call_args.args[0]
        assert "ON CONFLICT (tenant_id, fingerprint) DO NOTHING RETURNING txn_findings.fingerprint" in str(statement)
        params = statement.compile().params
        expected = sorted(detected, key=lambda item: item["fingerprint"])
        for index, item in enumerate(expected):
            assert params[f"tenant_id_m{index}"] == TENANT
            assert params[f"fingerprint_m{index}"] == item["fingerprint"]
            assert params[f"summary_m{index}"] == item["summary"]
        assert "fingerprint_m2" not in params

    @pytest.mark.asyncio
    async def test_thresholds_come_from_the_console_with_defaults(self, monkeypatch):
        monkeypatch.setattr(
            console,
            "effective",
            AsyncMock(return_value={"txn.passthrough_ratio": 0.9, "txn.structuring_window_days": 3}),
        )
        found = await findings.thresholds_for(TENANT)
        assert (
            found.passthrough_ratio == 0.9
            and found.structuring_window_days == 3
            and found.structuring_threshold == 1_000_000.0
        )
        monkeypatch.setattr(settings, "transaction_intelligence_enabled", True)
        assert console.check("txn.structuring_threshold", 500_000) == 500_000.0
        with pytest.raises(console.ConsoleError):
            console.check("txn.passthrough_ratio", 2)
        monkeypatch.setattr(settings, "transaction_intelligence_enabled", False)
        assert not [k for k in console.definitions() if k.startswith("txn.")]

    @pytest.mark.asyncio
    async def test_an_entity_is_read_with_its_findings(self, monkeypatch):
        monkeypatch.setattr(records, "list_records", AsyncMock(return_value=STRUCTURED))
        monkeypatch.setattr(
            findings,
            "list_findings",
            AsyncMock(return_value=[{"entity_ref": "A1", "kind": "structuring", "facts": {}}]),
        )
        view = await findings.entity(TENANT, "account", "A1")
        assert view["records"] == 4 and view["findings"][0]["kind"] == "structuring"
        assert records.list_records.call_args.kwargs["account"] == "A1"
        await findings.entity(TENANT, "customer", "C1")
        assert records.list_records.call_args.kwargs["customer_ref"] == "C1"
        await findings.entity(TENANT, "counterparty", "Y1")
        assert records.list_records.call_args.kwargs["counterparty"] == "Y1"
        with pytest.raises(TxnError):
            await findings.entity(TENANT, "planet", "earth")

    @pytest.mark.asyncio
    async def test_a_kept_statement_is_imported(self, monkeypatch):
        from core.idp import store

        document = {
            "documents": [
                {
                    "index": 0,
                    "document_type": "bank_statement",
                    "fields": [{"name": "account_number", "value": "XXXX1234"}],
                    "tables": [
                        {
                            "header": ["Date", "Description", "Debit", "Credit", "Balance"],
                            "rows": [["01-09-2026", "CASH DEP", "", "500", "1,500"]],
                        }
                    ],
                },
                {"index": 1, "document_type": "salary_slip", "fields": [], "tables": []},
            ]
        }
        monkeypatch.setattr(store, "get_document", AsyncMock(return_value=document))
        monkeypatch.setattr(
            records,
            "ingest",
            AsyncMock(return_value={"received": 1, "kept": 1, "skipped": 0, "accounts": ["XXXX1234"]}),
        )
        document_id = uuid.uuid4()
        out = await records.import_document(TENANT, document_id)
        assert (
            out["kept"] == 1
            and out["statements"] == 1
            and records.ingest.call_args.args[1][0]["source"] == f"statement:{document_id}"
        )
        monkeypatch.setattr(
            store,
            "get_document",
            AsyncMock(return_value={"documents": [{"document_type": "salary_slip", "fields": [], "tables": []}]}),
        )
        with pytest.raises(TxnError) as info:
            await records.import_document(TENANT, document_id)
        assert info.value.code == "no_statement"
        monkeypatch.setattr(store, "get_document", AsyncMock(return_value=None))
        with pytest.raises(TxnError) as info:
            await records.import_document(TENANT, document_id)
        assert info.value.status == 404

    @pytest.mark.asyncio
    async def test_a_long_statement_is_imported_in_batches(self, monkeypatch):
        from core.idp import store

        header = ["Date", "Description", "Debit", "Credit", "Balance"]
        rows = [["01-09-2026", f"CASH DEP {i}", "", "500", "1,500"] for i in range(1200)]
        document = {
            "documents": [
                {
                    "index": 0,
                    "document_type": "bank_statement",
                    "fields": [{"name": "account_number", "value": "X1"}],
                    "tables": [{"header": header, "rows": rows}],
                }
            ]
        }
        monkeypatch.setattr(store, "get_document", AsyncMock(return_value=document))
        monkeypatch.setattr(
            records,
            "ingest",
            AsyncMock(
                side_effect=lambda tenant, batch, **kw: {
                    "received": len(batch),
                    "kept": len(batch),
                    "skipped": 0,
                    "accounts": ["X1"],
                }
            ),
        )
        out = await records.import_document(TENANT, uuid.uuid4())
        assert (
            out["received"] == 1200
            and out["kept"] == 1200
            and out["accounts"] == ["X1"]
            and records.ingest.call_count == 3
        )


class TestRoutes:
    @pytest.mark.asyncio
    async def test_status_answers_off_and_the_rest_is_not_found(self, monkeypatch):
        from api.v1 import txn as api

        monkeypatch.setattr(settings, "transaction_intelligence_enabled", False)
        answer = await api.status(tenant_id=str(TENANT))
        assert answer["enabled"] is False and answer["detectors"] == ["structuring", "pass_through"]
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        for call in (
            api.ingest_records(api.RecordsIn(records=[{"account": "A"}]), tenant_id=str(TENANT)),
            api.list_records(
                account=None, customer_ref=None, counterparty=None, since_days=10, limit=10, tenant_id=str(TENANT)
            ),
            api.import_document(uuid.uuid4(), tenant_id=str(TENANT)),
            api.list_entities(kind=None, q="", since_days=10, tenant_id=str(TENANT)),
            api.get_entity("account", "A1", since_days=10, tenant_id=str(TENANT)),
            api.detect(None, tenant_id=str(TENANT)),
            api.list_findings(status=None, kind=None, entity_ref=None, limit=10, tenant_id=str(TENANT)),
            api.get_finding(uuid.uuid4(), tenant_id=str(TENANT)),
            api.disposition(uuid.uuid4(), api.DispositionIn(outcome="confirm"), request, tenant_id=str(TENANT)),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_the_routes_serve_the_stores(self, monkeypatch):
        from api.v1 import txn as api

        monkeypatch.setattr(settings, "transaction_intelligence_enabled", True)
        monkeypatch.setattr(
            findings, "thresholds_for", AsyncMock(return_value=detectors.Thresholds(passthrough_ratio=0.9))
        )
        monkeypatch.setattr(records, "ingest", AsyncMock(return_value={"kept": 2}))
        monkeypatch.setattr(records, "list_records", AsyncMock(return_value=STRUCTURED))
        monkeypatch.setattr(records, "import_document", AsyncMock(return_value={"kept": 1}))
        monkeypatch.setattr(findings, "entity", AsyncMock(return_value={"kind": "account", "ref": "A1"}))
        monkeypatch.setattr(findings, "detect", AsyncMock(return_value={"new": 1, "findings": []}))
        monkeypatch.setattr(findings, "list_findings", AsyncMock(return_value=[{"id": "f"}]))
        monkeypatch.setattr(findings, "get_finding", AsyncMock(return_value={"id": "f"}))
        monkeypatch.setattr(findings, "disposition", AsyncMock(return_value={"id": "f", "status": "confirmed"}))
        request = SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}))
        assert (await api.status(tenant_id=str(TENANT)))["thresholds"]["passthrough_ratio"] == 0.9
        assert (
            await api.ingest_records(api.RecordsIn(records=[{"account": "A"}], source="core"), tenant_id=str(TENANT))
        )["kept"] == 2
        assert records.ingest.call_args.kwargs["source"] == "core"
        listed = await api.list_records(
            account="A1", customer_ref=None, counterparty=None, since_days=30, limit=10, tenant_id=str(TENANT)
        )
        assert listed["total"] == 4 and records.list_records.call_args.kwargs["account"] == "A1"
        assert (await api.import_document(uuid.uuid4(), tenant_id=str(TENANT)))["kept"] == 1
        entities = await api.list_entities(kind="account", q="", since_days=30, tenant_id=str(TENANT))
        assert entities["total"] == 1 and entities["entities"][0]["ref"] == "A1"
        with pytest.raises(HTTPException) as info:
            await api.list_entities(kind="planet", q="", since_days=30, tenant_id=str(TENANT))
        assert info.value.status_code == 422
        assert (await api.get_entity("account", "A1", since_days=30, tenant_id=str(TENANT)))["ref"] == "A1"
        assert (await api.detect(api.DetectIn(account="A1", since_days=30), tenant_id=str(TENANT)))["new"] == 1
        assert findings.detect.call_args.kwargs["account"] == "A1"
        assert (await api.list_findings(status="open", kind=None, entity_ref=None, limit=10, tenant_id=str(TENANT)))[
            "total"
        ] == 1
        with pytest.raises(HTTPException) as info:
            await api.list_findings(status="lost", kind=None, entity_ref=None, limit=10, tenant_id=str(TENANT))
        assert info.value.status_code == 422
        assert (await api.get_finding(uuid.uuid4(), tenant_id=str(TENANT)))["id"] == "f"
        out = await api.disposition(
            uuid.uuid4(), api.DispositionIn(outcome="confirm", notes="n"), request, tenant_id=str(TENANT)
        )
        assert out["status"] == "confirmed" and findings.disposition.call_args.kwargs["user_id"] == "u1"
        monkeypatch.setattr(findings, "get_finding", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as info:
            await api.get_finding(uuid.uuid4(), tenant_id=str(TENANT))
        assert info.value.status_code == 404
        monkeypatch.setattr(records, "ingest", AsyncMock(side_effect=TxnError(422, "record_invalid", "no")))
        with pytest.raises(HTTPException) as info:
            await api.ingest_records(api.RecordsIn(records=[{"account": "A"}]), tenant_id=str(TENANT))
        assert info.value.status_code == 422 and info.value.detail["error"] == "record_invalid"

    def test_the_txn_routes_map_onto_enforced_scopes(self):
        from api.route_enforcement import SCOPE_FAMILIES, required_scopes_for

        assert SCOPE_FAMILIES["txn"] == ("audit:read", "approvals:write")
        assert required_scopes_for("txn.findings.sensitive.write", "POST") == ("approvals:write",)
        assert required_scopes_for("txn.records.sensitive.read", "GET") == ("audit:read",)

    @pytest.mark.asyncio
    async def test_a_machine_caller_may_not_disposition(self, monkeypatch):
        from api.v1 import txn as api

        monkeypatch.setattr(settings, "transaction_intelligence_enabled", True)
        monkeypatch.setattr(findings, "disposition", AsyncMock(return_value={"id": "f", "status": "confirmed"}))
        machine = SimpleNamespace(
            state=SimpleNamespace(claims={"sub": "apikey:k1"}, auth_mode="api_key", scopes=["approvals:write"])
        )
        with pytest.raises(HTTPException) as info:
            await api.disposition(uuid.uuid4(), api.DispositionIn(outcome="confirm"), machine, tenant_id=str(TENANT))
        assert info.value.status_code == 403 and info.value.detail["error"] == "human_required"
        assert findings.disposition.call_count == 0
        person = SimpleNamespace(
            state=SimpleNamespace(
                claims={"agenticorg:user_id": str(uuid.uuid4()), "sub": "u1"}, auth_mode="session", scopes=[]
            )
        )
        out = await api.disposition(uuid.uuid4(), api.DispositionIn(outcome="confirm"), person, tenant_id=str(TENANT))
        assert out["status"] == "confirmed"
