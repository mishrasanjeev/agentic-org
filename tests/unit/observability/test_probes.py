# SPDX-License-Identifier: Apache-2.0
"""The two probes that stand behind the metrics export.

They are tools rather than libraries - CI runs them as processes and reads their exit codes - but
a tool that has quietly stopped proving anything is worse than no tool, so they are exercised here
as well: the multiprocess probe must still report success, and the gate probe must still catch
every mutation and leave the working tree exactly as it found it.
"""

from __future__ import annotations

import importlib.util
import socket
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_the_multiprocess_probe_still_proves_aggregation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """On a platform that forks this runs the real thing; elsewhere it reports that it cannot."""
    import prometheus_client.values as prometheus_values

    # Belt and braces with the probe's own restore: a leaked multiprocess value class breaks every
    # later test that creates an instrument, a long way from here.
    monkeypatch.setattr(prometheus_values, "ValueClass", prometheus_values.ValueClass)
    monkeypatch.setenv("METRICS_PORT", str(_free_port()))
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))

    assert _load("probe_metrics_multiprocess").main() == 0


def test_the_multiprocess_probe_reads_a_metrics_body() -> None:
    probe = _load("probe_metrics_multiprocess")
    body = "\n".join(
        [
            'agenticorg_mp_probe_total{who="parent"} 5.0',
            'agenticorg_mp_probe_total{who="child3"} 3.0',
            'agenticorg_other_total{who="parent"} 99.0',
        ]
    )
    assert probe._sum(body, "agenticorg_mp_probe_total{") == 8.0


def test_the_gate_probe_catches_every_mutation_and_restores_the_tree() -> None:
    """If a mutation stopped being caught, the gate it guards has a hole in it."""
    probe = _load("probe_alert_gate")
    before = {path: path.read_text(encoding="utf-8") for path, _ in probe.CASES.values()}

    assert probe.main() == 0

    for path, original in before.items():
        assert path.read_text(encoding="utf-8") == original, f"{path} was left mutated"


def test_the_gate_probe_mutations_are_not_no_ops() -> None:
    """A mutation that no longer matches the file would pass silently as 'caught'."""
    probe = _load("probe_alert_gate")
    for label, (path, mutate) in probe.CASES.items():
        original = path.read_text(encoding="utf-8")
        assert mutate(original) != original, f"the mutation for {label!r} no longer applies"
