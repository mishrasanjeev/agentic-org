"""HPA integration for auto-scaling."""
from __future__ import annotations
import structlog
logger = structlog.get_logger()

class HPAIntegration:
    async def check_scaling(self, agent_type: str, queue_depth: int, config: dict) -> dict:
        threshold = config.get("scale_up_threshold", 30)
        max_replicas = config.get("max_replicas", 5)
        current = config.get("current_replicas", 1)
        if queue_depth > threshold and current < max_replicas:
            new_count = min(current * 2, max_replicas)
            logger.info("scale_up", agent_type=agent_type, from_r=current, to_r=new_count)
            return {"action": "scale_up", "replicas": new_count}
        return {"action": "no_change", "replicas": current}
