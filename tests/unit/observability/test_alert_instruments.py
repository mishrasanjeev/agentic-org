# SPDX-License-Identifier: Apache-2.0
"""Every instrument a committed alert reads must still be emitted (FINDINGS A-56).

A-56 was instruments nobody could read. The failure that replaces it is subtler and looks
healthier: an alert whose metric quietly stops being emitted in a refactor never fires, and an
alert that never fires is indistinguishable from a system that is behaving.

So this asserts two things for every declared instrument: it is registered, and somewhere in the
codebase a value is actually recorded on it. The second check reads the syntax tree, not the text,
so a mention in a comment or a docstring does not count as a writer - which is exactly how a
removal would look mid-refactor.

What it does *not* do is require the writer to live outside the module that defines the
instrument: in this codebase every one of them is defined next to the code that records it, which
is the right place for it. The check is about a value being recorded at all.
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
    """A defined instrument nobody records a value on exports a zero for ever and alerts on nothing."""
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
    """Modules that record a value on the instrument, found in the syntax tree.

    Deliberately not a text search: a commented-out ``instrument.labels(...).inc()`` reads exactly
    like a live one, and a refactor that removes the last real call usually leaves one behind.
    """
    found: set[str] = set()
    for directory in SOURCE_DIRS:
        for path in (ROOT / directory).rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:  # pragma: no cover - a file this suite does not own
                continue
            if _records_value(tree, variable):
                found.add(str(path.relative_to(ROOT)))
    return found


def _records_value(tree: ast.AST, variable: str) -> bool:
    """True when the tree contains a call that records a value on ``variable``."""
    recording = {"inc", "observe", "set", "dec"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in recording:
            continue
        target = node.func.value
        # instrument.inc()  /  instrument.labels(...).inc()  /  module.instrument.labels(...).inc()
        if isinstance(target, ast.Call) and isinstance(target.func, ast.Attribute):
            target = target.func.value
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
        if name == variable:
            return True
    return False


def test_a_commented_out_call_is_not_a_writer() -> None:
    """The removal this test exists to catch leaves the old call behind as a comment."""
    live = ast.parse("probe.labels(x='1').inc()")
    commented = ast.parse("# probe.labels(x='1').inc()\nprobe = 1\n")
    assert _records_value(live, "probe")
    assert not _records_value(commented, "probe")


def test_reading_an_instrument_is_not_writing_to_it() -> None:
    """``labels()`` alone returns a child; it records nothing."""
    assert not _records_value(ast.parse("value = probe.labels(x='1')"), "probe")
    assert _records_value(ast.parse("probe.labels(x='1').observe(2)"), "probe")


def test_every_declared_alert_has_at_least_one_instrument() -> None:
    for alert, instruments in ALERT_INSTRUMENTS.items():
        assert instruments, f"{alert} declares no instrument"
