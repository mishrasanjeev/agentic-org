# SPDX-License-Identifier: Apache-2.0
"""Document processing, part 4: statement line items with running-balance checks, and version comparison."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.idp import compare, statements, store

TENANT = uuid.uuid4()
HEADER = ["Date", "Description", "Debit", "Credit", "Balance"]
ROWS = [
    ["02/09/2026", "Salary credit", "", "45,000", "57,500"],
    ["05/09/2026", "Card payment", "2,300", "", "55,200"],
    ["", "at Example Mart", "", "", ""],
    ["09/09/2026", "Transfer to Ravi", "5,000", "", "50,200"],
    ["12/09/2026", "ECS RTN insufficient funds", "500", "", "49,700"],
    ["15/10/2026", "Interest", "", "100", "49,900"],
]


def _statement(rows=ROWS, opening="12500.00", closing="49900.00"):
    return {
        "index": 0,
        "document_type": "bank_statement",
        "fields": [{"name": "opening_balance", "value": opening}, {"name": "closing_balance", "value": closing}],
        "tables": [{"header": HEADER, "rows": rows}],
    }


class TestStatements:
    def test_amounts_and_headers_are_read(self):
        assert statements.parse_amount("45,000") == 45000.0 and statements.parse_amount("₹ 1,200.50") == 1200.5
        assert statements.parse_amount("(300)") == -300.0 and statements.parse_amount("300 Dr") == -300.0
        assert statements.parse_amount("Salary") is None and statements.parse_amount("") is None
        assert statements.map_header(["Txn Date", "Narration", "Withdrawal", "Deposit", "Balance", "Chq No"]) == {
            "date": 0,
            "description": 1,
            "debit": 2,
            "credit": 3,
            "balance": 4,
            "reference": 5,
        }
        assert statements.rows_to_transactions(["a", "b"], [["1", "2"]]) == []

    def test_rows_become_transactions_with_continuations_flags_and_a_checked_running_balance(self):
        result = statements.analyse(_statement())
        rows = result["transactions"]
        assert len(rows) == 5 and rows[1]["description"] == "Card payment at Example Mart"
        assert rows[0]["flags"] == ["salary_credit"] and "returned_or_bounced" in rows[3]["flags"]
        assert (
            all(r["consistent"] for r in rows[:4])
            and rows[4]["consistent"] is False
            and "balance_break" in rows[4]["flags"]
        )
        summary = result["summary"]
        assert summary["balance_breaks"] == 1 and summary["consistent"] is False and summary["closing_matches"] is True
        assert summary["total_credits"] == 45100.0 and summary["total_debits"] == 7800.0
        assert (
            summary["months"] == {"2026-09": 4, "2026-10": 1}
            and summary["first_date"] == "2026-09-02"
            and summary["span_days"] == 44
        )
        assert summary["salary_credits"] == [{"date": "2026-09-02", "amount": 45000.0, "description": "Salary credit"}]
        assert (
            summary["returned_or_bounced"] == 1
            and summary["minimum_balance"] == 49700.0
            and result["tables_used"] == [0]
        )

    def test_a_consistent_statement_and_one_without_an_opening_balance(self):
        good = statements.analyse(
            _statement(rows=ROWS[:4] + [["12/09/2026", "Interest", "", "100", "50,300"]], closing="50300")
        )
        assert good["summary"]["consistent"] is True and good["summary"]["balance_breaks"] == 0
        no_opening = statements.analyse(_statement(opening="", closing=""))
        assert no_opening["transactions"][0]["consistent"] is None and no_opening["summary"]["closing_matches"] is None
        assert (
            statements.analyse({"index": 1, "document_type": "bank_statement", "fields": [], "tables": []})["summary"][
                "transactions"
            ]
            == 0
        )

    def test_signed_debit_cells_are_debited_as_magnitudes(self):
        rows = [
            ["02/09/2026", "Card payment", "100 Dr", "", "900"],
            ["03/09/2026", "Transfer", "(200)", "", "700"],
            ["04/09/2026", "Fee", "-50", "", "650"],
        ]
        result = statements.analyse(_statement(rows=rows, opening="1000", closing="650"))
        assert [t["debit"] for t in result["transactions"]] == [100.0, 200.0, 50.0]
        assert [t["expected_balance"] for t in result["transactions"]] == [900.0, 700.0, 650.0]
        summary = result["summary"]
        assert summary["balance_breaks"] == 0 and summary["total_debits"] == 350.0 and summary["consistent"] is True

    def test_flags_are_recomputed_after_continuation_text_is_joined(self):
        rows = [
            ["02/09/2026", "Cheque 123", "500", "", "9,500"],
            ["", "RETURNED insufficient funds", "", "", ""],
            ["05/09/2026", "NEFT from Example Ltd", "", "40,000", "49,500"],
            ["", "SALARY SEP 2026", "", "", ""],
        ]
        result = statements.analyse(_statement(rows=rows, opening="10000", closing="49500"))
        first, second = result["transactions"]
        assert first["description"] == "Cheque 123 RETURNED insufficient funds"
        assert first["flags"] == ["returned_or_bounced"] and second["flags"] == ["salary_credit"]
        summary = result["summary"]
        assert summary["returned_or_bounced"] == 1 and [s["amount"] for s in summary["salary_credits"]] == [40000.0]

    def test_a_statement_with_no_checked_rows_is_indeterminate(self):
        empty = statements.analyse({"index": 1, "document_type": "bank_statement", "fields": [], "tables": []})
        assert empty["summary"]["consistent"] is None and empty["summary"]["rows_checked"] == 0
        single = statements.analyse(_statement(rows=ROWS[:1], opening="", closing=""))
        assert single["transactions"][0]["consistent"] is None
        assert single["summary"]["consistent"] is None and single["summary"]["rows_checked"] == 0
        no_opening = statements.analyse(_statement(rows=ROWS[:4], opening="", closing=""))
        assert no_opening["summary"]["rows_checked"] == 2 and no_opening["summary"]["consistent"] is True
        mismatch = statements.analyse(_statement(rows=ROWS[:1], opening="", closing="1"))
        assert mismatch["summary"]["closing_matches"] is False and mismatch["summary"]["consistent"] is False


def _detail(doc_id, fields, lines, rows):
    return {
        "id": doc_id,
        "pages_detail": [{"number": 1, "lines": [{"text": t} for t in lines]}],
        "documents": [
            {
                "index": 0,
                "document_type": "salary_slip",
                "pages": [1],
                "fields": [
                    {
                        "name": k,
                        "value": v,
                        "page": 1,
                        "bbox": [1, 1, 2, 2],
                        "kind": "amount" if k == "net_pay" else "text",
                        "required": True,
                    }
                    for k, v in fields.items()
                ],
                "extra_fields": [],
                "tables": [{"header": ["a"], "rows": rows}],
            }
        ],
    }


class TestCompare:
    def test_fields_pages_and_tables_are_compared(self):
        before = _detail(
            "a",
            {"employee_name": "A. Example", "net_pay": "41,250", "employer": "Example Ltd"},
            ["Salary slip", "Net pay 41,250"],
            [["x", "1"], ["y", "2"]],
        )
        after = _detail(
            "b",
            {"employee_name": "A Example", "net_pay": "43000", "pay_period": "Oct 2026"},
            ["Salary slip", "Net pay 43,000", "Revised"],
            [["x", "1"], ["y", "3"]],
        )
        result = compare.compare(before, after)
        assert result["comparable"] and result["same_type"] and result["identical"] is False
        fields = result["fields"]
        assert [f["name"] for f in fields["changed"]] == ["net_pay"] and fields["changed"][0]["before"] == "41,250"
        assert [f["name"] for f in fields["added"]] == ["pay_period"] and [f["name"] for f in fields["removed"]] == [
            "employer"
        ]
        assert fields["unchanged"] == ["employee_name"]
        page = result["pages"][0]
        assert (
            page["removed"] == ["Net pay 41,250"]
            and page["added"] == ["Net pay 43,000", "Revised"]
            and 0 < page["similarity"] < 1
        )
        assert result["tables"][0]["rows_removed"] == ["y | 2"] and result["tables"][0]["rows_added"] == ["y | 3"]
        assert result["summary"] == {
            "fields_changed": 1,
            "fields_added": 1,
            "fields_removed": 1,
            "pages_changed": 1,
            "tables_changed": 1,
        }
        same = compare.compare(before, before)
        assert same["identical"] is True and same["summary"]["fields_changed"] == 0
        assert compare.compare(before, after, document_index=3)["comparable"] is False

    def test_a_table_on_one_side_only_returns_its_rows(self, monkeypatch):
        before = {"tables": [{"header": ["a"], "rows": [["x", "1"], ["y", "2"]]}]}
        after: dict = {"tables": []}
        [removed] = compare.compare_tables(before, after)
        assert removed["present_after"] is False and removed["changed"] is True
        assert removed["rows_removed"] == ["x | 1", "y | 2"] and removed["rows_added"] == []
        [added] = compare.compare_tables(after, before)
        assert added["present_before"] is False and added["rows_added"] == ["x | 1", "y | 2"]
        assert added["rows_removed"] == []
        monkeypatch.setattr(compare, "MAX_LINE_CHANGES", 1)
        [limited] = compare.compare_tables(before, after)
        assert limited["rows_removed"] == ["x | 1"]


class TestRoutes:
    @pytest.mark.asyncio
    async def test_statement_and_compare_routes(self, monkeypatch):
        from api.v1 import idp_statements as api

        monkeypatch.setattr(settings, "idp_enabled", True)
        statement_detail = {
            "id": "s",
            "pages_detail": [],
            "documents": [_statement(), {"index": 1, "document_type": "invoice", "fields": [], "tables": []}],
        }
        monkeypatch.setattr(store, "get_document", AsyncMock(return_value=statement_detail))
        answer = await api.statement_lines(uuid.uuid4(), document_index=0, tenant_id=str(TENANT))
        assert answer["summary"]["transactions"] == 5
        with pytest.raises(HTTPException) as info:
            await api.statement_lines(uuid.uuid4(), document_index=1, tenant_id=str(TENANT))
        assert info.value.status_code == 422
        with pytest.raises(HTTPException) as info:
            await api.statement_lines(uuid.uuid4(), document_index=7, tenant_id=str(TENANT))
        assert info.value.status_code == 404
        compared = await api.compare_documents(uuid.uuid4(), uuid.uuid4(), document_index=0, tenant_id=str(TENANT))
        assert compared["identical"] is True and compared["before"] != compared["after"]
        monkeypatch.setattr(store, "get_document", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as info:
            await api.compare_documents(uuid.uuid4(), uuid.uuid4(), document_index=0, tenant_id=str(TENANT))
        assert info.value.status_code == 404
        monkeypatch.setattr(settings, "idp_enabled", False)
        with pytest.raises(HTTPException) as info:
            await api.statement_lines(uuid.uuid4(), document_index=0, tenant_id=str(TENANT))
        assert info.value.status_code == 404
