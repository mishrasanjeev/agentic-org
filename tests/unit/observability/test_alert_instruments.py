# SPDX-License-Identifier: Apache-2.0
"""Every instrument a committed alert reads must still be emitted (FINDINGS A-56).

A-56 was instruments nobody could read. The failure that replaces it is subtler and looks
healthier: an alert whose metric quietly stops being emitted in a refactor never fires, and an
alert that never fires is indistinguishable from a system that is behaving. ``agenticorg_agent_budget_pct``
is the proof that this happens here - it is defined in ``observability/metrics.py`` and nothing in
the repository has ever set it.

So this asserts two things for every declared instrument: it is registered, and some module other
than the one that defines it actually writes to it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from observability.alert_contract import ALERT_INSTRUMENTS, REQUIRED_INSTRUMENTS

ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIRS = ("api", "auth", "core", "observability", "scripts", "workflows", "connectors")


def _registered_names() -> set[str]:
    names: set[str] = set()
    for metric in REGISTRY.collect():
        names.add(metric.name)
        for sample in metric.samples:
            names.add(sample.name)
    return names


@pytest.fixture(scope="module", autouse=True)
def _import_the_emitters() -> None:
    """Instruments register on import, so the modules that define them have to be imported."""
    import core.cases.decision_requests  # noqa: F401
    import core.cases.excerpts  # noqa: F401
    import core.cases.push  # noqa: F401
    import core.cases.states  # noqa: F401
    import core.tool_gateway.provider_gateway  # noqa: F401
    import observability.metrics  # noqa: F401


@pytest.mark.parametrize("instrument", REQUIRED_INSTRUMENTS)
def test_an_instrument_an_alert_reads_is_registered(instrument: str) -> None:
    registered = _registered_names()
    # prometheus_client reports a counter family without its ``_total`` suffix and a histogram
    # family by its base name, so accept the family as well as the series.
    family = instrument[: -len("_total")] if instrument.endswith("_total") else instrument
    assert {instrument, family, f"{instrument}_count"} & registered, (
        f"{instrument} is read by an alert in monitoring/prometheus/agenticorg-alerts.yml but is "
        "no longer registered. Restore it, or change the alert and its declaration together."
    )


@pytest.mark.parametrize("instrument", REQUIRED_INSTRUMENTS)
def test_an_instrument_an_alert_reads_is_still_written_to(instrument: str) -> None:
    """A defined instrument nobody increments exports a zero for ever and alerts on nothing."""
    variable = _defining_variable(instrument)
    writers = sorted(_writers(variable))
    assert writers, (
        f"{instrument} ({variable}) is registered but nothing in the codebase records a value for "
        "it, so the alert that reads it can never fire."
    )


def _defining_variable(instrument: str) -> str:
    """The Python name the instrument is bound to at its definition site."""
    for directory in SOURCE_DIRS:
        for path in (ROOT / directory).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if f'"{instrument}"' not in text:
                continue
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                    continue
                args = node.value.args
                if args and isinstance(args[0], ast.Constant) and args[0].value == instrument:
                    target = node.targets[0]
                    if isinstance(target, ast.Name):
                        return target.id
    raise AssertionError(f"{instrument} is not defined anywhere under {SOURCE_DIRS}")


def _writers(variable: str) -> set[str]:
    """Modules that record a value on the instrument, excluding the one that defines it."""
    found: set[str] = set()
    for directory in SOURCE_DIRS:
        for path in (ROOT / directory).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for call in (f"{variable}.labels(", f"{variable}.inc(", f"{variable}.observe(", f"{variable}.set("):
                if call in text:
                    found.add(str(path.relative_to(ROOT)))
    return found


def test_every_declared_alert_has_at_least_one_instrument() -> None:
    for alert, instruments in ALERT_INSTRUMENTS.items():
        assert instruments, f"{alert} declares no instrument"
