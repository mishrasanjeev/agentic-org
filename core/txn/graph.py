# SPDX-License-Identifier: Apache-2.0
"""Fund-flow graphs: where an entity's money came from and went to, expanded across hops, with the paths that carry the most.

The graph starts at an account, a customer or a counterparty and takes
every record booked against it: each counterparty becomes a node and
each direction of flow between two nodes an edge carrying the total,
the count, the first and last movement and the channels. A node found
at one hop is expanded at the next from its own records, up to the hops
asked for and a bound on nodes, so the graph never runs away. The paths
list follows the money outward from the root along the heaviest edges.
The export carries the graph, the records behind every edge and the
findings on every node, as JSON or as CSV rows, for the case file.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable

from core.txn import aggregate, records

MAX_HOPS = 4
MAX_NODES = 200
MAX_PATHS = 10
MAX_EXPORT_RECORDS = 5000


def _when(record: dict[str, Any]) -> datetime:
    value = record["booked_at"]
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _other(record: dict[str, Any], node: str) -> str | None:
    """The node at the other end of a record from ``node``: the counterparty of an account, the account of a counterparty."""
    account = str(record.get("account") or "")
    counterparty = str(record.get("counterparty") or record.get("counterparty_name") or "")
    if node == account:
        return counterparty or None
    if node == counterparty:
        return account or None
    return None


def _edge_key(record: dict[str, Any], node: str, other: str) -> tuple[str, str]:
    """(from, to) for the record seen from ``node``: a credit on an account flows other -> account."""
    account = str(record.get("account") or "")
    if record.get("direction") == "credit":
        return (other, account) if node == account else (node, other)
    return (account, other) if node == account else (other, node)


async def expand(
    tenant_id: uuid.UUID,
    kind: str,
    ref: str,
    *,
    hops: int = 2,
    min_amount: float = 0.0,
    since_days: int = 365,
    fetch: Callable[[str], Awaitable[list[dict[str, Any]]]] | None = None,
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The fund-flow graph around an entity, expanded hop by hop from each node's own records."""
    if kind not in aggregate.ENTITY_KINDS:
        raise records.TxnError(422, "kind_unknown", f"kind is one of {', '.join(aggregate.ENTITY_KINDS)}")
    hops = max(1, min(hops, MAX_HOPS))
    since = records.window_start(since_days)

    async def default_fetch(node: str) -> list[dict[str, Any]]:
        return await records.list_records(tenant_id, counterparty=node, since=since, limit=records.MAX_LIST)

    fetch = fetch or default_fetch
    if kind == "customer":
        own = await records.list_records(tenant_id, customer_ref=ref, since=since, limit=records.MAX_LIST)
        roots = sorted({str(r["account"]) for r in own})
    else:
        roots = [ref]
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    edge_records: dict[tuple[str, str], list[str]] = {}
    seen_records: set[str] = set()
    frontier = list(roots)
    for node in roots:
        nodes[node] = {"id": node, "kind": "account", "label": node, "hop": 0, "in": 0.0, "out": 0.0, "records": 0, "root": True}
    truncated = False
    for hop in range(1, hops + 1):
        next_frontier: list[str] = []
        for node in frontier:
            for record in await fetch(node):
                amount = float(record["amount"])
                if amount < min_amount or record["record_ref"] in seen_records:
                    continue
                other = _other(record, node)
                if not other:
                    continue
                seen_records.add(str(record["record_ref"]))
                if other not in nodes:
                    if len(nodes) >= MAX_NODES:
                        truncated = True
                        continue
                    nodes[other] = {
                        "id": other,
                        "kind": "account" if other == str(record.get("account")) else "counterparty",
                        "label": other,
                        "hop": hop,
                        "in": 0.0,
                        "out": 0.0,
                        "records": 0,
                    }
                    next_frontier.append(other)
                source, target = _edge_key(record, node, other)
                nodes[target]["in"] = round(nodes[target]["in"] + amount, 2)
                nodes[source]["out"] = round(nodes[source]["out"] + amount, 2)
                nodes[node]["records"] += 1
                if other != node:
                    nodes[other]["records"] += 1
                edge = edges.setdefault(
                    (source, target),
                    {"from": source, "to": target, "amount": 0.0, "count": 0, "first_at": None, "last_at": None, "channels": []},
                )
                edge["amount"] = round(edge["amount"] + amount, 2)
                edge["count"] += 1
                when = _when(record).isoformat()
                edge["first_at"] = min(edge["first_at"] or when, when)
                edge["last_at"] = max(edge["last_at"] or when, when)
                channel = str(record.get("channel") or "other")
                if channel not in edge["channels"]:
                    edge["channels"].append(channel)
                edge_records.setdefault((source, target), []).append(str(record["record_ref"]))
        frontier = next_frontier
        if not frontier:
            break
    by_ref = {f.get("entity_ref"): [] for f in (findings or [])}
    for finding in findings or []:
        by_ref.setdefault(finding.get("entity_ref"), []).append({"id": finding.get("id"), "kind": finding.get("kind"), "severity": finding.get("severity"), "status": finding.get("status")})
    for node in nodes.values():
        node["findings"] = by_ref.get(node["id"], [])
    edge_list = sorted(edges.values(), key=lambda e: -e["amount"])
    return {
        "root": {"kind": kind, "ref": ref, "accounts": roots},
        "hops": hops,
        "nodes": sorted(nodes.values(), key=lambda n: (n["hop"], -(n["in"] + n["out"]))),
        "edges": edge_list,
        "paths": paths_from(roots, edge_list),
        "edge_records": {f"{k[0]}->{k[1]}": v for k, v in edge_records.items()},
        "truncated": truncated,
        "totals": {"nodes": len(nodes), "edges": len(edge_list), "records": len(seen_records)},
    }


def paths_from(roots: list[str], edges: list[dict[str, Any]], *, limit: int = MAX_PATHS, depth: int = MAX_HOPS) -> list[dict[str, Any]]:
    """The heaviest outward chains of money from the roots, each a list of hops with the amount carried."""
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        outgoing.setdefault(edge["from"], []).append(edge)
    found: list[dict[str, Any]] = []

    def walk(node: str, chain: list[dict[str, Any]], visited: set[str]) -> None:
        if len(chain) >= depth:
            return
        for edge in sorted(outgoing.get(node, []), key=lambda e: -e["amount"])[:5]:
            if edge["to"] in visited:
                continue
            step = chain + [{"from": edge["from"], "to": edge["to"], "amount": edge["amount"]}]
            found.append({"hops": [s["to"] for s in step], "start": step[0]["from"], "carried": min(s["amount"] for s in step), "steps": step})
            walk(edge["to"], step, visited | {edge["to"]})

    for root in roots:
        walk(root, [], {root})
    found.sort(key=lambda p: (-len(p["hops"]), -p["carried"]))
    return found[:limit]


def export_rows(graph: dict[str, Any], records_by_ref: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per record behind an edge, in CSV order: hop, from, to, then the record."""
    hops = {n["id"]: n["hop"] for n in graph.get("nodes", [])}
    rows: list[dict[str, Any]] = []
    for key, refs in graph.get("edge_records", {}).items():
        source, target = key.split("->", 1)
        for ref in refs:
            record = records_by_ref.get(ref, {})
            rows.append(
                {
                    "hop": max(hops.get(source, 0), hops.get(target, 0)),
                    "from": source,
                    "to": target,
                    "record_ref": ref,
                    "booked_at": record.get("booked_at"),
                    "amount": record.get("amount"),
                    "direction": record.get("direction"),
                    "channel": record.get("channel"),
                    "branch": record.get("branch"),
                    "description": record.get("description"),
                }
            )
    rows.sort(key=lambda r: (r["hop"], str(r["booked_at"] or "")))
    return rows


def to_csv(rows: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=["hop", "from", "to", "record_ref", "booked_at", "amount", "direction", "channel", "branch", "description"])
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return out.getvalue()


async def export(tenant_id: uuid.UUID, kind: str, ref: str, *, hops: int = 2, since_days: int = 365, findings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The graph with the records behind every edge and the findings on every node, for the case file."""
    graph = await expand(tenant_id, kind, ref, hops=hops, since_days=since_days, findings=findings)
    refs = [ref for refs in graph["edge_records"].values() for ref in refs][:MAX_EXPORT_RECORDS]
    rows_by_ref: dict[str, dict[str, Any]] = {}
    if refs:
        for record in await records.list_by_refs(tenant_id, refs):
            rows_by_ref[record["record_ref"]] = record
    rows = export_rows(graph, rows_by_ref)
    return {"graph": graph, "records": rows, "findings": findings or [], "csv": to_csv(rows)}
