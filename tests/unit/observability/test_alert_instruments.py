# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
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
def test_an_instrument_an_alert_reads_is_still_written_to(instrument: str, instrument_source_index) -> None:
    """A defined instrument nobody records a value on exports a zero for ever and alerts on nothing."""
    definitions, writers_by_variable = instrument_source_index
    assert instrument in definitions, f"{instrument} is not defined anywhere under {SOURCE_DIRS}"
    variable = definitions[instrument]
    writers = sorted(writers_by_variable.get(variable, set()))
    assert writers, (
        f"{instrument} ({variable}) is registered but nothing in the codebase records a value for "
        "it, so the alert that reads it can never fire."
    )


@pytest.fixture(scope="module")
def instrument_source_index() -> tuple[dict[str, str], dict[str, set[str]]]:
    return _build_instrument_index(ROOT, SOURCE_DIRS, REQUIRED_INSTRUMENTS)


def _build_instrument_index(root, directories, required):
    """Index the same full source tree once per test run, not once per metric.

    Deliberately not a text search: a commented-out ``instrument.labels(...).inc()`` reads exactly
    like a live one, and a refactor that removes the last real call usually leaves one behind.
    """
    definitions: dict[str, str] = {}
    writers: dict[str, set[str]] = {}
    sources: list[tuple[Path, str]] = []
    trees: dict[Path, ast.Module] = {}
    for directory in directories:
        for path in (root / directory).rglob("*.py"):
            source = path.read_text(encoding="utf-8", errors="replace")
            sources.append((path, source))
            if not any(instrument in source for instrument in required):
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:  # pragma: no cover - a file this suite does not own
                continue
            trees[path] = tree
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    args = node.value.args
                    target = node.targets[0]
                    if (args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str)
                            and args[0].value in required and isinstance(target, ast.Name)):
                        definitions.setdefault(args[0].value, target.id)
    # The text is only a candidate filter. Comments/read-only mentions still
    # need an actual recording call in the AST before they count as writers.
    variables = set(definitions.values())
    for path, source in sources:
        if not any(variable in source for variable in variables):
            continue
        try:
            tree = trees[path] if path in trees else ast.parse(source)
        except SyntaxError:  # pragma: no cover - a file this suite does not own
            continue
        for node in ast.walk(tree):
            variable = _recording_variable(node)
            if variable is not None and variable in variables:
                writers.setdefault(variable, set()).add(path.relative_to(root).as_posix())
    return definitions, writers


def _recording_variable(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr not in {"inc", "observe", "set", "dec"}:
        return None
    target = node.func.value
    if isinstance(target, ast.Call) and isinstance(target.func, ast.Attribute):
        target = target.func.value
    return target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)


def _records_value(tree: ast.AST, variable: str) -> bool:
    """True when the tree contains a call that records a value on ``variable``."""
    return any(_recording_variable(node) == variable for node in ast.walk(tree))


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


def test_source_index_filters_candidates_but_requires_real_recording_calls(tmp_path):
    directory = tmp_path / "core"
    directory.mkdir()
    path = directory / "metrics.py"
    path.write_text("probe = Counter('sample_total')\n# probe.inc()\nvalue = probe.labels(x='1')\n", encoding="utf-8")
    definitions, writers = _build_instrument_index(tmp_path, ("core",), ("sample_total",))
    assert definitions == {"sample_total": "probe"}
    assert writers == {}
    path.write_text("probe = Counter('sample_total')\nprobe.labels(x='1').inc()\n", encoding="utf-8")
    assert _build_instrument_index(tmp_path, ("core",), ("sample_total",))[1] == {"probe": {"core/metrics.py"}}


def test_every_declared_alert_has_at_least_one_instrument() -> None:
    for alert, instruments in ALERT_INSTRUMENTS.items():
        assert instruments, f"{alert} declares no instrument"
