"""Agent lifecycle state machine."""
from __future__ import annotations

import structlog

logger = structlog.get_logger()

VALID_TRANSITIONS = {
    "draft": ["shadow"],
    "shadow": ["review_ready", "shadow_failing"],
    "shadow_failing": ["shadow"],
    "review_ready": ["staging", "shadow"],
    "staging": ["production_ready", "shadow"],
    "production_ready": ["active", "staging"],
    "active": ["paused", "deprecated"],
    "paused": ["active", "deprecated"],
    "deprecated": ["deleted"],
}

class LifecycleManager:
    def can_transition(self, current: str, target: str) -> bool:
        return target in VALID_TRANSITIONS.get(current, [])

    async def transition(self, agent_id: str, current: str, target: str, triggered_by: str = "system", reason: str = "") -> dict:
        if not self.can_transition(current, target):
            raise ValueError(f"Invalid transition: {current} -> {target}")
        logger.info("lifecycle_transition", agent_id=agent_id, from_s=current, to_s=target, by=triggered_by)
        return {"agent_id": agent_id, "from_status": current, "to_status": target, "triggered_by": triggered_by}

    async def check_shadow_promotion(self, agent_id: str, sample_count: int, accuracy: float, min_samples: int, accuracy_floor: float) -> str | None:
        if sample_count < min_samples:
            return None
        if accuracy >= accuracy_floor:
            return "review_ready"
        return "shadow_failing"
