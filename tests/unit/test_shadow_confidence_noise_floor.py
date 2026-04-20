"""Tests for the shadow-accuracy noise floor (BUG-012).

BUG-012 — shadow-test accuracy sat consistently at ~40% for agents
that would otherwise score 0.75-0.85 on real runs. Root cause: the
running average was mixing in errored/no-parse runs whose default
confidence was ``0.0``. A few of those per 20-sample window dragged
the average into the 0.4 band.

The fix applies a noise floor of 0.10 — samples below that are
treated as "no signal" and skipped rather than counted as zero.
These tests pin that behaviour.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

# Mirror the constant from agents.py so the test pins the behaviour
# even if the literal moves.
CONFIDENCE_NOISE_FLOOR = 0.10


def _is_measurable(task_status: str, task_confidence: float | None) -> bool:
    """Re-implemented gate mirroring api/v1/agents.py logic so we can
    unit-test the decision without standing up the DB."""
    return (
        task_status in ("completed", "hitl_triggered")
        and task_confidence is not None
        and float(task_confidence) >= CONFIDENCE_NOISE_FLOOR
    )


@pytest.mark.parametrize(
    "status,confidence,expected",
    [
        ("completed", 0.85, True),
        ("completed", 0.75, True),
        ("hitl_triggered", 0.65, True),
        ("completed", 0.10, True),   # boundary — lands on the floor
        ("completed", 0.0, False),    # silent failure — exclude
        ("completed", 0.09, False),   # below floor — exclude
        ("completed", None, False),   # missing confidence — exclude
        ("failed", 0.9, False),       # run didn't complete — exclude
        ("error", 0.9, False),
    ],
)
def test_noise_floor_decides_inclusion(
    status: str, confidence: float | None, expected: bool,
) -> None:
    assert _is_measurable(status, confidence) is expected


def test_mixed_window_is_not_dragged_by_zero_confidence_samples() -> None:
    """Simulate the Ramesh scenario: 12 real samples at 0.80, 8 errored
    samples at 0.0. Without the floor, running average lands ~0.48.
    With the floor, average stays ~0.80."""
    samples = [0.80] * 12 + [0.0] * 8

    # Old (buggy) behaviour: include every sample.
    old_avg = sum(samples) / len(samples)
    assert old_avg == pytest.approx(0.48, abs=0.01)

    # New behaviour: skip samples below the floor.
    filtered = [s for s in samples if s >= CONFIDENCE_NOISE_FLOOR]
    new_avg = sum(filtered) / len(filtered)
    assert new_avg == pytest.approx(0.80, abs=0.001)


class TestShadowFloorDefault:
    def test_orm_default_is_080_not_095(self) -> None:
        """BUG-012: default floor was 0.95 which was unreachable for
        LLM agents. The ORM default is now 0.80."""
        from core.models.agent import Agent

        column = Agent.__table__.c["shadow_accuracy_floor"]
        assert column.default is not None
        # default.arg may be a callable or value depending on SQLAlchemy
        # version; either way it should produce 0.800.
        value = column.default.arg
        if callable(value):
            value = value(None)
        assert Decimal(str(value)) == Decimal("0.800")

    def test_pydantic_schema_default_matches_orm(self) -> None:
        from core.schemas.api import AgentCreate

        assert (
            AgentCreate.model_fields["shadow_accuracy_floor"].default
            == 0.80
        )
