"""Cost ledger — track per-agent costs and enforce budgets."""
from __future__ import annotations
from decimal import Decimal
import structlog
logger = structlog.get_logger()

class CostLedger:
    async def record(self, agent_id: str, tokens: int, cost_usd: float) -> None:
        logger.info("cost_record", agent_id=agent_id, tokens=tokens, cost=cost_usd)

    async def check_budget(self, agent_id: str, daily_budget: int, monthly_cap: float) -> dict:
        return {"within_budget": True, "budget_pct_used": 0.0}

    async def should_pause(self, agent_id: str, monthly_cap: float, current_cost: float) -> bool:
        return current_cost >= monthly_cap
