# SPDX-License-Identifier: Apache-2.0
"""Enforce what the committed alert rules must satisfy, without needing Prometheus installed.

``promtool`` checks that a rule file parses and that its expressions do what a fixture says. It
cannot check the two things this deployment gets wrong:

* **A raw counter read as if it were a level.** Every instance exports its own counters and
  instances scale to zero, so a counter's value depends on which instances are alive. Only
  ``rate()``/``increase()`` over a window means anything. Gauges are levels and may be read
  directly.
* **An alert reading an instrument nobody declared.** ``observability/alert_contract.py`` lists
  what each alert may read, and a unit test proves those instruments are still emitted. A rule
  that reads something outside its declaration is outside that protection.

It also requires every alert to carry a ``for``, a severity, a summary, a description and a
runbook, and requires the dashboard to read only declared instruments too, so the panel an
operator opens next to a firing alert cannot be the one panel nobody is testing.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "monitoring" / "prometheus" / "agenticorg-alerts.yml"
TESTS = ROOT / "monitoring" / "prometheus" / "agenticorg-alerts.test.yml"
DASHBOARD = ROOT / "monitoring" / "dashboards" / "agenticorg-governance.json"
TERRAFORM = ROOT / "infra" / "terraform" / "monitoring" / "alerts.tf"

METRIC = re.compile(r"\bagenticorg_[a-z0-9_]+")
RANGE_FUNCTION = re.compile(r"\b(rate|irate|increase|delta|idelta)\s*\(")


def _families(expr: str) -> set[str]:
    """Metric families an expression reads, with histogram suffixes folded back onto the family."""
    from observability.alert_contract import HISTOGRAM_SUFFIXES

    found: set[str] = set()
    for name in METRIC.findall(expr):
        for suffix in HISTOGRAM_SUFFIXES:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        found.add(name)
    return found


def _counter_reads_are_windowed(expr: str) -> list[str]:
    """Every ``_total`` selector has to sit inside a range function."""
    problems: list[str] = []
    for match in re.finditer(r"\bagenticorg_[a-z0-9_]*_total\b", expr):
        prefix = expr[: match.start()]
        opened = prefix.count("(") - prefix.count(")")
        if opened <= 0 or not RANGE_FUNCTION.search(prefix):
            problems.append(match.group(0))
    return problems


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from observability.alert_contract import ALERT_INSTRUMENTS

    failures: list[str] = []
    document: dict[str, Any] = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    rules = [rule for group in document.get("groups", []) for rule in group.get("rules", [])]
    names = [str(rule.get("alert", "")) for rule in rules]

    declared = set(ALERT_INSTRUMENTS)
    if set(names) != declared:
        for missing in sorted(declared - set(names)):
            failures.append(f"{missing}: declared in alert_contract.py but not defined in {RULES.name}")
        for extra in sorted(set(names) - declared):
            failures.append(f"{extra}: defined in {RULES.name} but not declared in alert_contract.py")

    for rule in rules:
        name = str(rule.get("alert", "<unnamed>"))
        expr = str(rule.get("expr", ""))
        allowed = set(ALERT_INSTRUMENTS.get(name, ()))
        for family in sorted(_families(expr) - allowed):
            failures.append(f"{name}: reads {family}, which is not declared for it in alert_contract.py")
        for counter in _counter_reads_are_windowed(expr):
            failures.append(
                f"{name}: reads the counter {counter} directly. Instances scale to zero, so a "
                "counter's value is an accident of which instances are alive - wrap it in "
                "rate() or increase()."
            )
        if not str(rule.get("for", "")).strip():
            failures.append(f"{name}: has no `for`, so a single evaluation - or a deploy - fires it")
        annotations = rule.get("annotations") or {}
        for required in ("summary", "description", "runbook"):
            if not str(annotations.get(required, "")).strip():
                failures.append(f"{name}: has no {required} annotation")
        if not str((rule.get("labels") or {}).get("severity", "")).strip():
            failures.append(f"{name}: has no severity label")

    tested = set()
    for case in (yaml.safe_load(TESTS.read_text(encoding="utf-8")) or {}).get("tests", []):
        for check in case.get("alert_rule_test", []):
            tested.add(str(check.get("alertname", "")))
    # Not every alert needs a fixture, but an alert nobody has ever seen fire is a guess.
    if not tested:
        failures.append(f"{TESTS.name}: contains no alert_rule_test cases")

    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    every_instrument = {name for names in ALERT_INSTRUMENTS.values() for name in names}
    for tile in dashboard.get("mosaicLayout", {}).get("tiles", []):
        title = tile.get("widget", {}).get("title", "<untitled>")
        for dataset in tile.get("widget", {}).get("xyChart", {}).get("dataSets", []):
            query = str(dataset.get("prometheusQuery", ""))
            for family in sorted(_families(query) - every_instrument):
                failures.append(f"dashboard panel {title!r}: reads undeclared {family}")

    terraform = TERRAFORM.read_text(encoding="utf-8")
    if "yamldecode" not in terraform or RULES.name not in terraform:
        failures.append(
            f"{TERRAFORM.name}: must build its policies from {RULES.name}. Inlining PromQL there "
            "creates a second definition that drifts from the tested one."
        )

    if failures:
        print("::error::Alert definitions are not safe to deploy.", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(f"Alert definitions OK: {len(rules)} alerts, {len(tested)} covered by promtool fixtures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
