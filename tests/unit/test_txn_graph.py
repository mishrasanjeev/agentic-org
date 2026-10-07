# SPDX-License-Identifier: Apache-2.0
"""Transaction intelligence, part 2: the fund-flow graph across hops, its paths, the export and the routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.txn import findings, graph, records
from core.txn.records import TxnError

TENANT = uuid.uuid4()
T0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def rec(ref: str, account: str, direction: str, amount: float, counterparty: str, *, hours: float = 0.0, channel: str = "transfer") -> dict:
    return {
        "record_ref": ref,
        "account": account,
        "customer_ref": "C1" if account == "A1" else None,
        "counterparty": counterparty,
        "counterparty_name": None,
        "direction": direction,
        "amount": amount,
        "currency": "INR",
        "channel": channel,
        "branch": None,
        "booked_at": (T0 + timedelta(hours=hours)).isoformat(),
        "description": "",
        "source": "api",
        "attributes": {},
    }


# X1 -> A1 (1,000,000); A1 -> Y1 (600,000) and A1 -> Y2 (350,000); Y1 -> Z1 (500,000) is Y1's own record
BOOK = {
    "A1": [rec("p1", "A1", "credit", 1_000_000, "X1", hours=0), rec("p2", "A1", "debit", 600_000, "Y1", hours=5, channel="upi"), rec("p3", "A1", "debit", 350_000, "Y2", hours=20)],
    "Y1": [rec("p2", "A1", "debit", 600_000, "Y1", hours=5, channel="upi"), rec("q1", "Y1", "debit", 500_000, "Z1", hours=30)],
    "Y2": [rec("p3", "A1", "debit", 350_000, "Y2", hours=20)],
    "X1": [rec("p1", "A1", "credit", 1_000_000, "X1", hours=0)],
    "Z1": [rec("q1", "Y1", "debit", 500_000, "Z1", hours=30)],
}


async def fetch(node: str) -> list[dict]:
    return list(BOOK.get(node, []))


class TestGraph:
    @pytest.mark.asyncio
    async def test_the_graph_expands_across_hops_with_edges_and_paths(self):
        found = await graph.expand(TENANT, "account", "A1", hops=2, fetch=fetch, findings=[{"id": "f1", "entity_ref": "A1", "kind": "pass_through", "severity": "high", "status": "open"}])
        assert found["root"] == {"kind": "account", "ref": "A1", "accounts": ["A1"]} and found["hops"] == 2
        nodes = {n["id"]: n for n in found["nodes"]}
        assert set(nodes) == {"A1", "X1", "Y1", "Y2", "Z1"}
        assert nodes["A1"]["hop"] == 0 and nodes["A1"]["root"] is True and nodes["A1"]["findings"][0]["id"] == "f1"
        assert nodes["Y1"]["hop"] == 1 and nodes["Z1"]["hop"] == 2 and nodes["Z1"]["kind"] == "counterparty"
        assert nodes["A1"]["in"] == 1_000_000 and nodes["A1"]["out"] == 950_000 and nodes["Y1"]["in"] == 600_000 and nodes["Y1"]["out"] == 500_000
        edges = {(e["from"], e["to"]): e for e in found["edges"]}
        assert edges[("X1", "A1")]["amount"] == 1_000_000 and edges[("A1", "Y1")]["channels"] == ["upi"] and edges[("Y1", "Z1")]["count"] == 1
        assert found["edges"][0]["amount"] == 1_000_000  # heaviest first
        assert found["paths"][0]["hops"] == ["Y1", "Z1"] and found["paths"][0]["carried"] == 500_000
        assert found["edge_records"]["A1->Y1"] == ["p2"] and found["totals"] == {"nodes": 5, "edges": 4, "records": 4}
        assert found["truncated"] is False

    @pytest.mark.asyncio
    async def test_hops_and_minimum_amount_bound_the_graph(self):
        one = await graph.expand(TENANT, "account", "A1", hops=1, fetch=fetch)
        assert {n["id"] for n in one["nodes"]} == {"A1", "X1", "Y1", "Y2"}
        big = await graph.expand(TENANT, "account", "A1", hops=2, min_amount=550_000, fetch=fetch)
        assert {n["id"] for n in big["nodes"]} == {"A1", "X1", "Y1"}
        with pytest.raises(TxnError):
            await graph.expand(TENANT, "planet", "A1", fetch=fetch)
        empty = await graph.expand(TENANT, "account", "nobody", fetch=fetch)
        assert empty["nodes"][0]["id"] == "nobody" and empty["edges"] == [] and empty["paths"] == []

    @pytest.mark.asyncio
    async def test_a_customer_starts_from_their_accounts_and_the_node_bound_marks_truncation(self, monkeypatch):
        monkeypatch.setattr(records, "list_records", AsyncMock(return_value=BOOK["A1"]))
        found = await graph.expand(TENANT, "customer", "C1", hops=1, fetch=fetch)
        assert found["root"]["accounts"] == ["A1"] and records.list_records.call_args.kwargs["customer_ref"] == "C1"
        monkeypatch.setattr(graph, "MAX_NODES", 2)
        bounded = await graph.expand(TENANT, "account", "A1", hops=1, fetch=fetch)
        assert bounded["truncated"] is True and bounded["totals"]["nodes"] == 2

    def test_paths_follow_the_heaviest_outward_edges(self):
        edges = [
            {"from": "A", "to": "B", "amount": 100},
            {"from": "A", "to": "C", "amount": 300},
            {"from": "C", "to": "D", "amount": 250},
            {"from": "D", "to": "A", "amount": 50},  # a cycle back is not followed
        ]
        paths = graph.paths_from(["A"], edges)
        assert paths[0]["hops"] == ["C", "D"] and paths[0]["carried"] == 250
        assert all("A" not in p["hops"] for p in paths)
        assert graph.paths_from(["A"], []) == []

    @pytest.mark.asyncio
    async def test_the_export_carries_the_rows_behind_every_edge(self, monkeypatch):
        monkeypatch.setattr(graph, "expand", AsyncMock(return_value={"nodes": [{"id": "A1", "hop": 0}, {"id": "Y1", "hop": 1}], "edge_records": {"A1->Y1": ["p2"]}}))
        monkeypatch.setattr(records, "list_by_refs", AsyncMock(return_value=[dict(BOOK["A1"][1], booked_at=BOOK["A1"][1]["booked_at"])]))
        out = await graph.export(TENANT, "account", "A1")
        assert out["records"][0]["from"] == "A1" and out["records"][0]["to"] == "Y1" and out["records"][0]["amount"] == 600_000 and out["records"][0]["hop"] == 1
        assert out["csv"].splitlines()[0].startswith("hop,from,to,record_ref") and "p2" in out["csv"]
        assert records.list_by_refs.call_args.args[1] == ["p2"]
        assert graph.export_rows({"nodes": [], "edge_records": {}}, {}) == [] and graph.to_csv([]).startswith("hop,")


class TestRoutes:
    @pytest.mark.asyncio
    async def test_the_graph_routes(self, monkeypatch):
        from api.v1 import txn as api

        monkeypatch.setattr(settings, "transaction_intelligence_enabled", False)
        for call in (api.fund_flow("account", "A1", hops=2, min_amount=0, since_days=30, tenant_id=str(TENANT)), api.export_fund_flow("account", "A1", hops=2, since_days=30, output="json", tenant_id=str(TENANT))):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404
        monkeypatch.setattr(settings, "transaction_intelligence_enabled", True)
        monkeypatch.setattr(findings, "list_findings", AsyncMock(return_value=[{"entity_ref": "A1"}]))
        monkeypatch.setattr(graph, "expand", AsyncMock(return_value={"nodes": [], "edges": []}))
        monkeypatch.setattr(graph, "export", AsyncMock(return_value={"graph": {}, "records": [], "findings": [], "csv": "hop,from\n"}))
        out = await api.fund_flow("account", "A1", hops=3, min_amount=10, since_days=30, tenant_id=str(TENANT))
        assert out == {"nodes": [], "edges": []} and graph.expand.call_args.kwargs["hops"] == 3 and graph.expand.call_args.kwargs["findings"] == [{"entity_ref": "A1"}]
        as_json = await api.export_fund_flow("account", "A1", hops=2, since_days=30, output="json", tenant_id=str(TENANT))
        assert "csv" not in as_json and as_json["graph"] == {}
        as_csv = await api.export_fund_flow("account", "A1", hops=2, since_days=30, output="csv", tenant_id=str(TENANT))
        assert as_csv.media_type == "text/csv" and as_csv.body == b"hop,from\n" and "fund-flow-A1.csv" in as_csv.headers["content-disposition"]
        with pytest.raises(HTTPException) as info:
            await api.export_fund_flow("account", "A1", hops=2, since_days=30, output="xml", tenant_id=str(TENANT))
        assert info.value.status_code == 422
        monkeypatch.setattr(graph, "expand", AsyncMock(side_effect=TxnError(422, "kind_unknown", "no")))
        with pytest.raises(HTTPException) as info:
            await api.fund_flow("planet", "A1", hops=2, min_amount=0, since_days=30, tenant_id=str(TENANT))
        assert info.value.detail["error"] == "kind_unknown"
