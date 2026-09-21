# SPDX-License-Identifier: Apache-2.0
"""The conventions the committed alert rules have to satisfy, and the checker that enforces them."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _checker():
    spec = importlib.util.spec_from_file_location("check_alert_rules", ROOT / "scripts" / "check_alert_rules.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_committed_rules_pass_the_checker() -> None:
    assert _checker().main() == 0


def test_a_counter_read_as_a_level_is_refused() -> None:
    """Instances scale to zero, so a counter's value is an accident of which ones are alive."""
    checker = _checker()
    assert checker._counter_reads_are_windowed("agenticorg_provider_calls_total > 5") == [
        "agenticorg_provider_calls_total"
    ]
    assert checker._counter_reads_are_windowed("sum(rate(agenticorg_provider_calls_total[5m])) > 5") == []


def test_histogram_series_are_folded_back_onto_their_family() -> None:
    """``..._bucket`` and ``..._count`` are the same instrument as far as the contract goes."""
    checker = _checker()
    assert checker._families('agenticorg_case_decision_dwell_seconds_bucket{le="15"}') == {
        "agenticorg_case_decision_dwell_seconds"
    }
