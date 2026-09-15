#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Per-tenant report of tool calls grant enforcement denied or would deny.

Reads the API's JSON log lines — raw stdout lines, or Cloud Logging entries
that wrap them in ``jsonPayload`` (``gcloud logging read --format=json`` or
one entry per line) — and counts ``grant_enforcement_would_deny`` and
``grant_enforcement_denied`` events per tenant, by reason and by
(connector, tool, reason, agent type). Use it during the warn-mode soak to
decide whether a tenant can move to deny; see
``docs/operations/grant-enforcement.md``.

    python scripts/grant_enforcement_report.py api.log
    gcloud logging read 'jsonPayload.event=~"^grant_enforcement_"' --freshness=7d \\
        --format=json | python scripts/grant_enforcement_report.py --format json -

Lines that are not JSON or not one of the two events are skipped and counted.
The report never contains a grant token: the events do not carry one.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, TextIO

EVENTS = {"grant_enforcement_would_deny": "would_deny", "grant_enforcement_denied": "denied"}


@dataclass
class TenantReport:
    would_deny: int = 0
    denied: int = 0
    by_reason: Counter[tuple[str, str]] = field(default_factory=Counter)
    by_call: Counter[tuple[str, str, str, str, str]] = field(default_factory=Counter)
    first_seen: str = ""
    last_seen: str = ""

    def add(self, outcome: str, event: dict[str, Any]) -> None:
        reason = str(event.get("reason") or "")
        if outcome == "would_deny":
            self.would_deny += 1
        else:
            self.denied += 1
        self.by_reason[(outcome, reason)] += 1
        self.by_call[
            (
                outcome,
                reason,
                str(event.get("connector") or ""),
                str(event.get("tool") or ""),
                str(event.get("agent_type") or ""),
            )
        ] += 1
        stamp = str(event.get("timestamp") or "")
        if stamp:
            self.first_seen = min(self.first_seen, stamp) if self.first_seen else stamp
            self.last_seen = max(self.last_seen, stamp)


def _entries(stream: TextIO) -> Iterator[Any]:
    """Yield parsed JSON values: one per line, or the items of a JSON array."""
    text = stream.read()
    stripped = text.lstrip()
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            yield from parsed
            return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            yield None


def build_report(entries: Iterable[Any], *, tenant: str | None = None) -> tuple[dict[str, TenantReport], int]:
    """Aggregate enforcement events per tenant. Returns (reports, skipped)."""
    reports: dict[str, TenantReport] = defaultdict(TenantReport)
    skipped = 0
    for entry in entries:
        event = entry.get("jsonPayload", entry) if isinstance(entry, dict) else None
        outcome = EVENTS.get(str(event.get("event"))) if isinstance(event, dict) else None
        if outcome is None or not isinstance(event, dict):
            skipped += 1
            continue
        tenant_id = str(event.get("tenant_id") or "") or "(no tenant)"
        if tenant and tenant_id != tenant:
            continue
        if not event.get("timestamp") and isinstance(entry, dict) and entry.get("timestamp"):
            event = {**event, "timestamp": entry["timestamp"]}
        reports[tenant_id].add(outcome, event)
    return dict(reports), skipped


def to_json(reports: dict[str, TenantReport], skipped: int) -> dict[str, Any]:
    return {
        "tenants": {
            tenant_id: {
                "would_deny": report.would_deny,
                "denied": report.denied,
                "first_seen": report.first_seen,
                "last_seen": report.last_seen,
                "by_reason": [
                    {"outcome": outcome, "reason": reason, "count": count}
                    for (outcome, reason), count in report.by_reason.most_common()
                ],
                "by_call": [
                    {
                        "outcome": outcome,
                        "reason": reason,
                        "connector": connector,
                        "tool": tool,
                        "agent_type": agent_type,
                        "count": count,
                    }
                    for (outcome, reason, connector, tool, agent_type), count in report.by_call.most_common()
                ],
            }
            for tenant_id, report in sorted(reports.items())
        },
        "skipped_lines": skipped,
    }


def to_text(reports: dict[str, TenantReport], skipped: int, *, top: int) -> str:
    lines: list[str] = []
    if not reports:
        lines.append("No grant enforcement events found.")
    for tenant_id, report in sorted(reports.items()):
        lines.append(f"tenant {tenant_id}: would_deny={report.would_deny} denied={report.denied}")
        if report.first_seen:
            lines.append(f"  seen {report.first_seen} .. {report.last_seen}")
        for (outcome, reason), count in report.by_reason.most_common():
            lines.append(f"  {outcome:<10} {reason:<24} {count}")
        lines.append("  top calls:")
        for (outcome, reason, connector, tool, agent_type), count in report.by_call.most_common(top):
            lines.append(f"    {count:>6}  {outcome}  {reason}  {connector}.{tool}  agent_type={agent_type or '-'}")
    lines.append(f"skipped lines: {skipped}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", default=["-"], help="log files, or - for stdin (default)")
    parser.add_argument("--tenant", help="only this tenant id")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--top", type=int, default=20, help="calls listed per tenant in text output")
    args = parser.parse_args(argv)

    def _all_entries() -> Iterator[Any]:
        for path in args.paths:
            if path == "-":
                yield from _entries(sys.stdin)
            else:
                with open(path, encoding="utf-8") as handle:
                    yield from _entries(handle)

    reports, skipped = build_report(_all_entries(), tenant=args.tenant)
    if args.format == "json":
        print(json.dumps(to_json(reports, skipped), indent=2, sort_keys=True))
    else:
        print(to_text(reports, skipped, top=args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
