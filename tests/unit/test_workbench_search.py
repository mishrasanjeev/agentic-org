# SPDX-License-Identifier: Apache-2.0
"""Workbench search: the query grammar, who may search what, hits of every kind, facets and the route."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.workbench import assignments, search

TENANT = uuid.uuid4()
NOW = datetime.now(UTC)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _Session:
    def __init__(self, rows_by_table):
        self.rows_by_table = rows_by_table
        self.statements: list[str] = []

    async def execute(self, statement):
        table = statement.get_final_froms()[0].name
        self.statements.append(str(statement.compile(compile_kwargs={"literal_binds": False})))
        return _Result(self.rows_by_table.get(table, []))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _use(monkeypatch, session):
    import core.database

    monkeypatch.setattr(core.database, "get_tenant_session", lambda tenant_id: session)


def _case(**kw):
    base = {
        "id": uuid.uuid4(),
        "case_ref": "KYB-1",
        "purpose": "onboarding",
        "provider": "registry",
        "state": "awaiting_decision",
        "subject": {"provider": "registry", "provider_ref": "R-9", "name": "Example Traders"},
        "parties": [{"name": "Ravi Kumar", "role": "director"}, {"name": "Asha Rao"}],
        "updated_at": NOW,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _document(**kw):
    base = {
        "id": uuid.uuid4(),
        "filename": "statement.pdf",
        "status": "review",
        "result": {
            "documents": [
                {
                    "index": 0,
                    "document_type": "bank_statement",
                    "fields": [
                        {"name": "name", "value": "Ravi Kumar"},
                        {"name": "account_number", "value": "XXXX1234"},
                        {"name": "ifsc", "value": "ABCD0123456"},
                    ],
                    "extra_fields": [],
                },
                {
                    "index": 1,
                    "document_type": "salary_slip",
                    "fields": [
                        {"name": "employee_name", "value": "Asha Rao"},
                        {"name": "account_number", "value": "9876"},
                    ],
                },
            ]
        },
        "corrections": {"1": {"account_number": {"value": "9999", "by": "r"}}},
        "updated_at": NOW,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _company(**kw):
    base = {
        "id": uuid.uuid4(),
        "name": "Ravi Traders",
        "pan": "ABCDE1234F",
        "gstin": None,
        "cin": None,
        "industry": "retail",
        "state_code": "27",
        "registered_address": "Pune",
        "signatory_name": "Ravi Kumar",
        "is_active": True,
        "updated_at": NOW,
    }
    base.update(kw)
    return SimpleNamespace(**base)


class TestQuery:
    def test_words_phrases_and_exclusions(self):
        assert search.parse_query('ravi "bank statement" -salary') == (["ravi", "bank statement"], ["salary"])
        assert search.parse_query("a  -b") == ([], [])  # terms shorter than two characters are ignored
        assert search.parse_query("") == ([], [])
        with pytest.raises(search.SearchError) as info:
            search.parse_query(" ".join(f"word{i}" for i in range(9)))
        assert info.value.status == 422

    def test_filters_are_checked_per_kind(self):
        assert search.check_filters("case", {"state": "decided", "nothing": ["x"], "purpose": ["onboarding", " "]}) == {
            "state": ["decided"],
            "purpose": ["onboarding"],
        }
        assert search.check_filters("document", {"document_type": ["bank_statement"]}) == {
            "document_type": ["bank_statement"]
        }
        with pytest.raises(search.SearchError):
            search.check_filters("case", {"state": ["x"] * 21})
        assert search.check_filters("customer", {"active": ["True"]}) == {"active": ["true"]}
        with pytest.raises(search.SearchError) as info:
            search.check_filters("customer", {"active": ["yes"]})
        assert info.value.code == "filter_invalid"

    def test_who_may_search_what(self):
        assert search.kinds_for("admin") == ["case", "document", "customer", "account"]
        assert search.kinds_for("cfo") == ["case", "document", "customer", "account"]
        assert search.kinds_for("domain_lead") == ["case", "document", "account"]  # no companies page
        assert search.kinds_for("auditor") == ["customer"]  # investigator shows only the audit trail to an auditor
        assert search.kinds_for("analyst") == []
        assert search.kinds_for("auditor", {"review_officer"}) == ["customer"]  # the tabs still name the roles


class TestHits:
    @pytest.mark.asyncio
    async def test_every_kind_is_searched_with_the_terms_and_facets_counted(self, monkeypatch):
        session = _Session({"governed_cases": [_case()], "idp_documents": [_document()], "companies": [_company()]})
        _use(monkeypatch, session)
        found = await search.search(TENANT, q="ravi -salary", kinds=list(search.KINDS), limit=10)
        assert found["query"] == {"must": ["ravi"], "must_not": ["salary"]}
        assert found["counts"] == {"case": 1, "document": 1, "customer": 1, "account": 2}
        kinds = [h["kind"] for h in found["hits"]]
        assert kinds == ["case", "document", "account", "account", "customer"]
        case = found["hits"][0]
        assert case["path"] == "/dashboard/approvals/cases/KYB-1" and case["parties"] == ["Ravi Kumar", "Asha Rao"]
        assert case["subject_ref"] == "R-9" and "Ravi" in case["snippet"]
        document = found["hits"][1]
        assert document["facets"] == {"status": "review", "document_type": ["bank_statement", "salary_slip"]}
        accounts = [h for h in found["hits"] if h["kind"] == "account"]
        assert accounts[0]["title"] == "account number XXXX1234" and "Ravi Kumar" in accounts[0]["subtitle"]
        assert accounts[1]["title"] == "ifsc ABCD0123456"  # the salary slip's account is excluded by -salary
        assert found["facets"]["case"]["state"] == {"awaiting_decision": 1}
        assert found["facets"]["document"]["document_type"] == {"bank_statement": 1, "salary_slip": 1}
        assert found["facets"]["customer"] == {
            "industry": {"retail": 1},
            "state_code": {"27": 1},
            "active": {"true": 1},
        }
        for text in session.statements:
            assert "LIKE lower(" in text  # every term must match
        assert (
            sum("NOT (" in text for text in session.statements) == len(session.statements) - 1
        )  # accounts judge exclusions per account
        assert sum("companies" in text for text in session.statements) == 1

    @pytest.mark.asyncio
    async def test_corrections_win_and_filters_narrow(self, monkeypatch):
        session = _Session({"idp_documents": [_document()]})
        _use(monkeypatch, session)
        found = await search.search(
            TENANT, q="9999", kinds=["account"], filters={"document_type": ["salary_slip"]}, limit=10
        )
        assert [h["title"] for h in found["hits"]] == ["account number 9999"]  # the corrected value, not the read one
        assert found["hits"][0]["subtitle"].startswith("Asha Rao")
        found = await search.search(TENANT, q="", kinds=["document"], filters={"document_type": ["passport"]}, limit=10)
        assert found["hits"] == [] and found["counts"] == {"document": 0}
        found = await search.search(TENANT, q="", kinds=["document"], filters={"status": ["review"]}, limit=10)
        assert found["counts"] == {"document": 1} and "idp_documents.status IN" in session.statements[-1]
        await search.search(TENANT, q="", kinds=["document"], filters={"document_type": ["bank_statement"]}, limit=10)
        assert "idp_documents.result @>" in session.statements[-1]  # narrowed in the query, before the row limit

    @pytest.mark.asyncio
    async def test_an_excluded_term_drops_only_the_account_it_names(self, monkeypatch):
        session = _Session({"idp_documents": [_document()]})
        _use(monkeypatch, session)
        found = await search.search(TENANT, q="account -salary", kinds=["account", "document"], limit=10)
        accounts = [h["title"] for h in found["hits"] if h["kind"] == "account"]
        assert accounts == ["account number XXXX1234"]  # the salary slip account is dropped, the statement account kept
        account_statement = next(t for t in session.statements if "NOT (" not in t)
        assert "LIKE lower(" in account_statement  # the account rows are fetched without the exclusion

    @pytest.mark.asyncio
    async def test_case_and_customer_filters_go_into_the_query(self, monkeypatch):
        session = _Session({"governed_cases": [], "companies": []})
        _use(monkeypatch, session)
        await search.search(
            TENANT,
            q="ravi",
            kinds=["case", "customer"],
            filters={"state": ["decided"], "active": ["false"], "industry": ["retail"]},
            limit=5,
        )
        assert "governed_cases.state IN" in session.statements[0]
        assert "companies.is_active IN" in session.statements[1] and "companies.industry IN" in session.statements[1]

    @pytest.mark.asyncio
    async def test_an_empty_query_without_filters_is_refused(self, monkeypatch):
        _use(monkeypatch, _Session({}))
        with pytest.raises(search.SearchError) as info:
            await search.search(TENANT, q="a", kinds=["case"], limit=5)
        assert info.value.code == "query_empty"

    def test_snippets_centre_on_the_first_term(self):
        text = "x" * 100 + " Ravi Kumar " + "y" * 200
        snippet = search._snippet(text, ["ravi"])
        assert snippet.startswith("…") and "Ravi Kumar" in snippet and snippet.endswith("…")
        assert search._snippet("short", []) == "short"


class TestRoute:
    def _request(self, **params):
        class Params(dict):
            def getlist(self, name):
                value = self.get(name)
                return list(value) if isinstance(value, list) else ([value] if value else [])

        return SimpleNamespace(state=SimpleNamespace(claims={"agenticorg:user_id": "u1"}), query_params=Params(params))

    @pytest.mark.asyncio
    async def test_the_route_is_not_found_while_off(self, monkeypatch):
        from api.v1 import workbench_search as api

        monkeypatch.setattr(settings, "workbench_v2_enabled", False)
        with pytest.raises(HTTPException) as info:
            await api.search_workbench(
                self._request(), q="ravi", kind=None, limit=10, role="cfo", tenant_id=str(TENANT)
            )
        assert info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_the_route_narrows_to_the_callers_kinds_and_passes_the_filters(self, monkeypatch):
        from api.v1 import workbench_search as api

        monkeypatch.setattr(settings, "workbench_v2_enabled", True)
        monkeypatch.setattr(assignments, "assigned_to", AsyncMock(return_value=set()))
        monkeypatch.setattr(
            search,
            "search",
            AsyncMock(return_value={"hits": [], "counts": {}, "facets": {}, "total": 0, "query": {}, "kinds": []}),
        )
        request = self._request(state=["awaiting_decision"], document_type="bank_statement")
        found = await api.search_workbench(
            request, q="ravi", kind=["case", "customer"], limit=10, role="domain_lead", tenant_id=str(TENANT)
        )
        assert found["allowed_kinds"] == ["case", "document", "account"]
        assert found["filters"] == {"state": ["awaiting_decision"], "document_type": ["bank_statement"]}
        call = search.search.call_args
        assert call.kwargs["kinds"] == ["case"] and call.kwargs["q"] == "ravi"
        with pytest.raises(HTTPException) as info:
            await api.search_workbench(request, q="ravi", kind=["nothing"], limit=10, role="cfo", tenant_id=str(TENANT))
        assert info.value.status_code == 422
        monkeypatch.setattr(search, "search", AsyncMock(side_effect=search.SearchError(422, "query_empty", "no")))
        with pytest.raises(HTTPException) as info:
            await api.search_workbench(self._request(), q="", kind=None, limit=10, role="cfo", tenant_id=str(TENANT))
        assert info.value.detail["error"] == "query_empty"
