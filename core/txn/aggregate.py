# SPDX-License-Identifier: Apache-2.0
"""Entity-centric aggregation: an account, a customer or a counterparty with everything booked against it in one view.

The view sums what came in and went out, counts the movements, splits
them by channel and by branch, names the counterparties that moved the
most, lays the days out as a series, and lists the findings raised on
the entity, so an investigator reads one page rather than many rows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

ENTITY_KINDS: tuple[str, ...] = ("account", "customer", "counterparty")
TOP = 10


def _when(record: dict[str, Any]) -> datetime:
    value = record["booked_at"]
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def own(records: list[dict[str, Any]], kind: str, ref: str) -> list[dict[str, Any]]:
    """The records that belong to the entity: on the account, of the customer, or with the counterparty."""
    if kind == "account":
        return [r for r in records if r.get("account") == ref]
    if kind == "customer":
        return [r for r in records if r.get("customer_ref") == ref]
    return [r for r in records if r.get("counterparty") == ref or r.get("counterparty_name") == ref]


def entity_view(
    records: list[dict[str, Any]], kind: str, ref: str, findings: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    items = sorted(own(records, kind, ref), key=_when)
    inflows = [r for r in items if r.get("direction") == "credit"]
    outflows = [r for r in items if r.get("direction") == "debit"]
    by_channel: dict[str, dict[str, float]] = {}
    by_branch: dict[str, dict[str, float]] = {}
    counterparties: dict[str, dict[str, Any]] = {}
    days: dict[str, dict[str, float]] = {}
    for record in items:
        amount = float(record["amount"])
        side = "in" if record.get("direction") == "credit" else "out"
        by_channel.setdefault(str(record.get("channel") or "other"), {"in": 0.0, "out": 0.0})[side] += amount
        by_branch.setdefault(str(record.get("branch") or "unknown"), {"in": 0.0, "out": 0.0})[side] += amount
        who = str(record.get("counterparty") or record.get("counterparty_name") or "unknown")
        if kind == "counterparty":
            who = str(record.get("account"))
        entry = counterparties.setdefault(who, {"name": who, "in": 0.0, "out": 0.0, "count": 0})
        entry[side] += amount
        entry["count"] += 1
        days.setdefault(_when(record).date().isoformat(), {"in": 0.0, "out": 0.0})[side] += amount
    accounts = sorted({str(r.get("account")) for r in items})
    customers = sorted({str(r.get("customer_ref")) for r in items if r.get("customer_ref")})
    top = sorted(counterparties.values(), key=lambda c: -(c["in"] + c["out"]))[:TOP]
    return {
        "kind": kind,
        "ref": ref,
        "records": len(items),
        "first_at": _when(items[0]).isoformat() if items else None,
        "last_at": _when(items[-1]).isoformat() if items else None,
        "totals": {
            "in": round(sum(float(r["amount"]) for r in inflows), 2),
            "out": round(sum(float(r["amount"]) for r in outflows), 2),
            "credits": len(inflows),
            "debits": len(outflows),
            "net": round(sum(float(r["amount"]) for r in inflows) - sum(float(r["amount"]) for r in outflows), 2),
        },
        "by_channel": {k: {s: round(v, 2) for s, v in sides.items()} for k, sides in by_channel.items()},
        "by_branch": {k: {s: round(v, 2) for s, v in sides.items()} for k, sides in by_branch.items()},
        "counterparties": [{**c, "in": round(c["in"], 2), "out": round(c["out"], 2)} for c in top],
        "series": [{"date": d, "in": round(v["in"], 2), "out": round(v["out"], 2)} for d, v in sorted(days.items())],
        "accounts": accounts,
        "customers": customers,
        "cash_share": round(
            by_channel.get("cash", {}).get("in", 0.0) / (sum(float(r["amount"]) for r in inflows) or 1.0), 3
        ),
        "findings": [
            f
            for f in (findings or [])
            if f.get("entity_ref") == ref
            or ref in (f.get("facts") or {}).get("to", [])
            or (f.get("facts") or {}).get("from") == ref
        ],
    }


def entities(records: list[dict[str, Any]], *, kind: str | None = None, query: str = "") -> list[dict[str, Any]]:
    """The entities present in the records with how much moved through each, newest activity first."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    needle = query.strip().lower()
    for record in records:
        candidates = [
            ("account", record.get("account")),
            ("customer", record.get("customer_ref")),
            ("counterparty", record.get("counterparty") or record.get("counterparty_name")),
        ]
        for entity_kind, ref in candidates:
            if not ref or (kind and entity_kind != kind):
                continue
            if needle and needle not in str(ref).lower():
                continue
            entry = found.setdefault(
                (entity_kind, str(ref)),
                {"kind": entity_kind, "ref": str(ref), "records": 0, "volume": 0.0, "last_at": None},
            )
            entry["records"] += 1
            entry["volume"] = round(entry["volume"] + float(record["amount"]), 2)
            when = _when(record).isoformat()
            entry["last_at"] = max(entry["last_at"] or "", when)
    return sorted(found.values(), key=lambda e: e["last_at"] or "", reverse=True)
