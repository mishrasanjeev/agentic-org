"""Resolve conflicts between agent outputs."""
from __future__ import annotations
from typing import Any

class ConflictResolver:
    def resolve(self, results) -> dict[str, Any]:
        if len(results) < 2:
            return {"action": "no_conflict", "output": results[0].output if results else {}}
        outputs = [r.output for r in results]
        if outputs[0] == outputs[1]:
            return {"action": "no_conflict", "output": outputs[0]}
        # Factual conflict: surface both, escalate
        return {
            "action": "escalate",
            "reason": "factual_conflict",
            "outputs": outputs,
            "recommendation": "conservative",
        }
