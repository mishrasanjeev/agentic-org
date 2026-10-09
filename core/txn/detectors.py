# SPDX-License-Identifier: Apache-2.0
"""Detectors over transaction records: structuring and pass-through, each finding with the rows that support it.

Structuring: cash deposits each below the reporting threshold, several of
them within a window on one account, together at or above the threshold;
spread across branches, the finding is graver. Pass-through: money that
arrives and leaves an account within a short window, the outflow a large
share of the inflow, which is how an account is used as a conduit. Both
are deterministic functions over the records, so a finding is the same
on every run and every row behind it is named.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

KINDS: tuple[str, ...] = ("structuring", "pass_through")


@dataclass(frozen=True)
class Thresholds:
    structuring_threshold: float = 1_000_000.0  # the reporting threshold deposits are kept under
    structuring_window_days: int = 7
    structuring_min_count: int = 3
    passthrough_window_hours: int = 48
    passthrough_ratio: float = 0.8
    passthrough_min_amount: float = 100_000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "structuring_threshold": self.structuring_threshold,
            "structuring_window_days": self.structuring_window_days,
            "structuring_min_count": self.structuring_min_count,
            "passthrough_window_hours": self.passthrough_window_hours,
            "passthrough_ratio": self.passthrough_ratio,
            "passthrough_min_amount": self.passthrough_min_amount,
        }


def _when(record: dict[str, Any]) -> datetime:
    value = record["booked_at"]
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def fingerprint(kind: str, entity_ref: str, record_refs: list[str]) -> str:
    seed = "|".join([kind, entity_ref, *sorted(record_refs)])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _by_account(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        out.setdefault(str(record["account"]), []).append(record)
    for items in out.values():
        items.sort(key=_when)
    return out


def structuring(records: list[dict[str, Any]], thresholds: Thresholds | None = None) -> list[dict[str, Any]]:
    """Cash deposits under the threshold that together reach it within the window, per account."""
    t = thresholds or Thresholds()
    findings: list[dict[str, Any]] = []
    window = timedelta(days=t.structuring_window_days)
    for account, items in _by_account(records).items():
        deposits = [
            r
            for r in items
            if r.get("direction") == "credit"
            and r.get("channel") == "cash"
            and 0 < float(r["amount"]) < t.structuring_threshold
        ]
        index = 0
        while index < len(deposits):
            start = _when(deposits[index])
            group = [r for r in deposits[index:] if _when(r) - start <= window]
            total = sum(float(r["amount"]) for r in group)
            if len(group) >= t.structuring_min_count and total >= t.structuring_threshold:
                branches = sorted({str(r.get("branch") or "") for r in group if r.get("branch")})
                refs = [str(r["record_ref"]) for r in group]
                severity = "high" if len(branches) >= 2 or total >= 2 * t.structuring_threshold else "medium"
                findings.append(
                    {
                        "kind": "structuring",
                        "entity_kind": "account",
                        "entity_ref": account,
                        "severity": severity,
                        "summary": (
                            f"{len(group)} cash deposits under {t.structuring_threshold:,.0f} on account {account} "
                            f"between {start.date().isoformat()} and {_when(group[-1]).date().isoformat()} "
                            f"total {total:,.0f}" + (f" across {len(branches)} branches" if len(branches) >= 2 else "")
                        ),
                        "facts": {
                            "count": len(group),
                            "total": round(total, 2),
                            "threshold": t.structuring_threshold,
                            "window_days": t.structuring_window_days,
                            "window_start": start.isoformat(),
                            "window_end": _when(group[-1]).isoformat(),
                            "branches": branches,
                            "largest": max(float(r["amount"]) for r in group),
                        },
                        "record_refs": refs,
                        "fingerprint": fingerprint("structuring", account, refs),
                    }
                )
                index += len(group)
            else:
                index += 1
    return findings


def pass_through(records: list[dict[str, Any]], thresholds: Thresholds | None = None) -> list[dict[str, Any]]:
    """Inflows followed within the window by outflows that take most of them away, per account."""
    t = thresholds or Thresholds()
    findings: list[dict[str, Any]] = []
    window = timedelta(hours=t.passthrough_window_hours)
    used: set[str] = set()
    for account, items in _by_account(records).items():
        for position, credit in enumerate(items):
            if credit.get("direction") != "credit" or float(credit["amount"]) < t.passthrough_min_amount:
                continue
            if credit["record_ref"] in used:
                continue
            arrived = _when(credit)
            debits = [
                r
                for r in items[position + 1 :]
                if r.get("direction") == "debit"
                and r["record_ref"] not in used
                and timedelta(0) <= _when(r) - arrived <= window
            ]
            outflow = sum(float(r["amount"]) for r in debits)
            inflow = float(credit["amount"])
            if not debits or outflow < t.passthrough_ratio * inflow:
                continue
            ratio = min(outflow / inflow, 9.99)
            hours = (_when(debits[-1]) - arrived).total_seconds() / 3600.0
            refs = [str(credit["record_ref"])] + [str(r["record_ref"]) for r in debits]
            used.update(refs)
            severity = "high" if ratio >= 0.95 and hours <= 24 else "medium"
            findings.append(
                {
                    "kind": "pass_through",
                    "entity_kind": "account",
                    "entity_ref": account,
                    "severity": severity,
                    "summary": (
                        f"{inflow:,.0f} arrived on account {account} on {arrived.date().isoformat()} and "
                        f"{outflow:,.0f} left within {hours:.0f} hours through {len(debits)} payment(s)"
                    ),
                    "facts": {
                        "inflow": round(inflow, 2),
                        "outflow": round(outflow, 2),
                        "ratio": round(ratio, 3),
                        "hours": round(hours, 1),
                        "window_hours": t.passthrough_window_hours,
                        "from": credit.get("counterparty") or credit.get("counterparty_name") or "unknown",
                        "to": sorted(
                            {str(r.get("counterparty") or r.get("counterparty_name") or "unknown") for r in debits}
                        ),
                        "payments": len(debits),
                    },
                    "record_refs": refs,
                    "fingerprint": fingerprint("pass_through", account, refs),
                }
            )
    return findings


def run_all(
    records: list[dict[str, Any]], thresholds: Thresholds | None = None, *, kinds: tuple[str, ...] | list[str] = KINDS
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if "structuring" in kinds:
        out.extend(structuring(records, thresholds))
    if "pass_through" in kinds:
        out.extend(pass_through(records, thresholds))
    return out
