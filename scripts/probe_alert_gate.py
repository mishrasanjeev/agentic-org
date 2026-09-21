# SPDX-License-Identifier: Apache-2.0
"""Mutate the alert definitions and check that the gate notices (a tool, not a CI step).

``scripts/check_alert_rules.py`` is only worth having if it fails when it should. This breaks the
definitions in each of the ways they have actually been broken, runs the checker, and reports
whether each was caught - then restores every file it touched.

It writes to the working tree, so run it on a clean tree and check ``git status`` afterwards::

    python scripts/probe_alert_gate.py

Adapted from the probe written during the independent review of the metrics export.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "monitoring" / "prometheus" / "agenticorg-alerts.yml"
TERRAFORM = ROOT / "infra" / "terraform" / "monitoring" / "alerts.tf"

Mutation = tuple[Path, Callable[[str], str]]

CASES: dict[str, Mutation] = {
    "a `for` the conversion cannot parse": (
        RULES,
        lambda s: s.replace("        for: 5m\n", "        for: 7w\n", 1),
    ),
    "terraform passes the raw Prometheus duration through": (
        TERRAFORM,
        lambda s: s.replace(
            "duration            = local.duration_seconds[each.key]",
            "duration            = each.value.for",
        ),
    ),
    "terraform loses the conversion entirely": (
        TERRAFORM,
        lambda s: s.replace("duration_seconds", "some_other_local"),
    ),
    "a counter read as a level": (
        RULES,
        lambda s: s.replace(
            'expr: sum(increase(agenticorg_budget_cap_events_total{outcome="exhausted"}[2h])) > 0',
            'expr: agenticorg_budget_cap_events_total{outcome="exhausted"} > 0',
        ),
    ),
    "an alert with no sustain window": (
        RULES,
        lambda s: s.replace("        for: 10m\n", "        for: \n", 1),
    ),
}


def _run() -> tuple[int, str]:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "check_alert_rules.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout + result.stderr


def main() -> int:
    code, output = _run()
    print(f"baseline: exit={code}  {output.strip().splitlines()[-1] if output.strip() else ''}")
    if code != 0:
        print("the definitions do not pass as they are; fix that before probing", file=sys.stderr)
        return 1

    missed: list[str] = []
    for label, (path, mutate) in CASES.items():
        original = path.read_text(encoding="utf-8")
        try:
            mutated = mutate(original)
            if mutated == original:
                print(f"  !! {label}: the mutation did not change anything - the probe is stale")
                missed.append(label)
                continue
            path.write_text(mutated, encoding="utf-8", newline="\n")
            code, output = _run()
            first = next((line.strip() for line in output.splitlines() if line.strip().startswith("-")), "")
            print(f"  {'caught' if code else 'NOT CAUGHT':11s} {label}\n{'':13s}{first[:110]}")
            if code == 0:
                missed.append(label)
        finally:
            path.write_text(original, encoding="utf-8", newline="\n")

    if missed:
        print(f"\n{len(missed)} mutation(s) the gate does not catch.", file=sys.stderr)
    return 1 if missed else 0


if __name__ == "__main__":
    raise SystemExit(main())
