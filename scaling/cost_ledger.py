"""Cost ledger -- track per-agent costs and enforce budgets.

Persistence strategy
--------------------
- **Redis (immediate):** Every ``record()`` call writes to Redis sorted sets
  and hash maps for real-time budget queries.
- **Database (async batch):** Records are buffered in memory and flushed to
  the database periodically or when the buffer reaches a threshold.

Budget enforcement
------------------
- ``check_budget()`` queries Redis for today's and this month's totals.
- ``should_pause()`` returns ``True`` when either daily or monthly cap is
  exceeded, and emits an ``AGENT_COST_CAP_EXCEEDED`` event.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Cost record
# ---------------------------------------------------------------------------


@dataclass
class CostRecord:
    agent_id: str
    tokens: int
    cost_usd: Decimal
    model: str
    tenant_id: str
    timestamp: float  # epoch seconds


# ---------------------------------------------------------------------------
# CostLedger
# ---------------------------------------------------------------------------


class CostLedger:
    """Track per-agent LLM costs and enforce daily/monthly budgets.

    When a real Redis connection is provided (via ``redis`` parameter), data is
    persisted there.  Otherwise an in-memory fallback is used so the class
    remains functional in tests and local development.
    """

    # Redis key templates
    _DAILY_COST_KEY = "agenticorg:cost:daily:{agent_id}:{date}"
    _DAILY_TOKENS_KEY = "agenticorg:cost:daily_tokens:{agent_id}:{date}"
    _MONTHLY_COST_KEY = "agenticorg:cost:monthly:{agent_id}:{month}"
    _MONTHLY_TOKENS_KEY = "agenticorg:cost:monthly_tokens:{agent_id}:{month}"
    _DAILY_TASKS_KEY = "agenticorg:cost:daily_tasks:{agent_id}:{date}"

    # Flush settings
    _FLUSH_BUFFER_SIZE = 50
    _FLUSH_INTERVAL_SECONDS = 30.0

    def __init__(
        self,
        redis: Any | None = None,
        db_session_factory: Any | None = None,
        event_emitter: Any | None = None,
    ) -> None:
        self._redis = redis
        self._db_session_factory = db_session_factory
        self._event_emitter = event_emitter

        # In-memory fallback stores (used when Redis is unavailable)
        self._mem_daily_cost: dict[str, Decimal] = {}  # key: agent_id:date
        self._mem_daily_tokens: dict[str, int] = {}
        self._mem_monthly_cost: dict[str, Decimal] = {}
        self._mem_monthly_tokens: dict[str, int] = {}
        self._mem_daily_tasks: dict[str, int] = {}

        # DB flush buffer
        self._buffer: list[CostRecord] = []
        self._last_flush: float = time.monotonic()

    # ------------------------------------------------------------------
    # record()
    # ------------------------------------------------------------------

    async def record(
        self,
        agent_id: str,
        tokens: int,
        cost_usd: float,
        *,
        model: str = "",
        tenant_id: str = "",
    ) -> None:
        """Record a cost entry for an agent.

        Writes immediately to Redis (or in-memory fallback) and buffers
        for async DB persistence.
        """
        now = datetime.now(UTC)
        today = now.strftime("%Y-%m-%d")
        month = now.strftime("%Y-%m")
        cost = Decimal(str(cost_usd))

        record = CostRecord(
            agent_id=agent_id,
            tokens=tokens,
            cost_usd=cost,
            model=model,
            tenant_id=tenant_id,
            timestamp=now.timestamp(),
        )

        # --- Redis (immediate) ---
        if self._redis is not None:
            await self._write_redis(agent_id, today, month, tokens, cost)
        else:
            self._write_memory(agent_id, today, month, tokens, cost)

        logger.info(
            "cost_record",
            agent_id=agent_id,
            tokens=tokens,
            cost=float(cost),
            model=model,
        )

        # --- DB buffer ---
        self._buffer.append(record)
        if len(self._buffer) >= self._FLUSH_BUFFER_SIZE or self._flush_due():
            await self._flush_to_db()

    async def _write_redis(
        self,
        agent_id: str,
        today: str,
        month: str,
        tokens: int,
        cost: Decimal,
    ) -> None:
        """Increment Redis counters atomically via pipeline."""
        pipe = self._redis.pipeline()

        daily_cost_key = self._DAILY_COST_KEY.format(agent_id=agent_id, date=today)
        daily_tokens_key = self._DAILY_TOKENS_KEY.format(agent_id=agent_id, date=today)
        monthly_cost_key = self._MONTHLY_COST_KEY.format(agent_id=agent_id, month=month)
        monthly_tokens_key = self._MONTHLY_TOKENS_KEY.format(agent_id=agent_id, month=month)
        daily_tasks_key = self._DAILY_TASKS_KEY.format(agent_id=agent_id, date=today)

        pipe.incrbyfloat(daily_cost_key, float(cost))
        pipe.incrby(daily_tokens_key, tokens)
        pipe.incrbyfloat(monthly_cost_key, float(cost))
        pipe.incrby(monthly_tokens_key, tokens)
        pipe.incr(daily_tasks_key)

        # Expire daily keys after 48 hours, monthly after 35 days
        pipe.expire(daily_cost_key, 48 * 3600)
        pipe.expire(daily_tokens_key, 48 * 3600)
        pipe.expire(daily_tasks_key, 48 * 3600)
        pipe.expire(monthly_cost_key, 35 * 86400)
        pipe.expire(monthly_tokens_key, 35 * 86400)

        await pipe.execute()

    def _write_memory(
        self,
        agent_id: str,
        today: str,
        month: str,
        tokens: int,
        cost: Decimal,
    ) -> None:
        """In-memory fallback when Redis is unavailable."""
        daily_key = f"{agent_id}:{today}"
        monthly_key = f"{agent_id}:{month}"

        self._mem_daily_cost[daily_key] = self._mem_daily_cost.get(daily_key, Decimal("0")) + cost
        self._mem_daily_tokens[daily_key] = self._mem_daily_tokens.get(daily_key, 0) + tokens
        self._mem_daily_tasks[daily_key] = self._mem_daily_tasks.get(daily_key, 0) + 1
        self._mem_monthly_cost[monthly_key] = (
            self._mem_monthly_cost.get(monthly_key, Decimal("0")) + cost
        )
        self._mem_monthly_tokens[monthly_key] = (
            self._mem_monthly_tokens.get(monthly_key, 0) + tokens
        )

    # ------------------------------------------------------------------
    # check_budget()
    # ------------------------------------------------------------------

    async def check_budget(
        self,
        agent_id: str,
        daily_budget: float,
        monthly_cap: float,
    ) -> dict[str, Any]:
        """Query actual spend for today and current month.

        Returns
        -------
        dict with keys:
            within_budget: bool
            daily_cost: float
            monthly_cost: float
            daily_budget: float
            monthly_cap: float
            budget_pct_used: float  (of monthly cap)
            daily_pct_used: float   (of daily budget)
            warnings: list[str]
        """
        now = datetime.now(UTC)
        today = now.strftime("%Y-%m-%d")
        month = now.strftime("%Y-%m")

        daily_cost = await self._get_cost(agent_id, today, scope="daily")
        monthly_cost = await self._get_cost(agent_id, month, scope="monthly")

        daily_pct = (float(daily_cost) / daily_budget * 100) if daily_budget > 0 else 0.0
        monthly_pct = (float(monthly_cost) / monthly_cap * 100) if monthly_cap > 0 else 0.0

        warnings: list[str] = []
        within_budget = True

        if daily_budget > 0 and float(daily_cost) >= daily_budget:
            warnings.append(f"Daily budget exceeded: ${daily_cost:.2f} >= ${daily_budget:.2f}")
            within_budget = False
        elif daily_budget > 0 and daily_pct >= 80:
            warnings.append(
                f"Daily budget at {daily_pct:.1f}%: ${daily_cost:.2f} / ${daily_budget:.2f}"
            )

        if monthly_cap > 0 and float(monthly_cost) >= monthly_cap:
            warnings.append(f"Monthly cap exceeded: ${monthly_cost:.2f} >= ${monthly_cap:.2f}")
            within_budget = False
        elif monthly_cap > 0 and monthly_pct >= 80:
            warnings.append(
                f"Monthly budget at {monthly_pct:.1f}%: ${monthly_cost:.2f} / ${monthly_cap:.2f}"
            )

        return {
            "within_budget": within_budget,
            "daily_cost": float(daily_cost),
            "monthly_cost": float(monthly_cost),
            "daily_budget": daily_budget,
            "monthly_cap": monthly_cap,
            "budget_pct_used": monthly_pct,
            "daily_pct_used": daily_pct,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # should_pause()
    # ------------------------------------------------------------------

    async def should_pause(
        self,
        agent_id: str,
        daily_budget: float = 0.0,
        monthly_cap: float = 0.0,
    ) -> bool:
        """Return True if the agent should be paused due to budget exceedance.

        Checks both daily and monthly caps.  Emits an event and logs when
        a pause is recommended.
        """
        now = datetime.now(UTC)
        today = now.strftime("%Y-%m-%d")
        month = now.strftime("%Y-%m")

        daily_cost = await self._get_cost(agent_id, today, scope="daily")
        monthly_cost = await self._get_cost(agent_id, month, scope="monthly")

        daily_exceeded = daily_budget > 0 and float(daily_cost) >= daily_budget
        monthly_exceeded = monthly_cap > 0 and float(monthly_cost) >= monthly_cap

        if daily_exceeded or monthly_exceeded:
            reason = "daily" if daily_exceeded else "monthly"
            cap_value = daily_budget if daily_exceeded else monthly_cap
            current_value = float(daily_cost) if daily_exceeded else float(monthly_cost)

            logger.warning(
                "agent_budget_exceeded",
                agent_id=agent_id,
                reason=reason,
                current=current_value,
                cap=cap_value,
            )

            await self._emit_budget_exceeded_event(
                agent_id=agent_id,
                reason=reason,
                current_cost=current_value,
                cap=cap_value,
            )
            return True

        return False

    # ------------------------------------------------------------------
    # get_daily_summary()
    # ------------------------------------------------------------------

    async def get_daily_summary(
        self,
        agent_id: str,
        day: str | None = None,
    ) -> dict[str, Any]:
        """Return token_count, cost_usd, task_count for a given day.

        Parameters
        ----------
        day:
            Date string ``YYYY-MM-DD``.  Defaults to today (UTC).
        """
        day = day or datetime.now(UTC).strftime("%Y-%m-%d")

        cost = await self._get_cost(agent_id, day, scope="daily")
        tokens = await self._get_tokens(agent_id, day, scope="daily")
        tasks = await self._get_tasks(agent_id, day)

        return {
            "agent_id": agent_id,
            "date": day,
            "token_count": tokens,
            "cost_usd": float(cost),
            "task_count": tasks,
        }

    # ------------------------------------------------------------------
    # get_monthly_summary()
    # ------------------------------------------------------------------

    async def get_monthly_summary(
        self,
        agent_id: str,
        month: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate cost and token data for the current (or specified) month.

        Parameters
        ----------
        month:
            Month string ``YYYY-MM``.  Defaults to current month (UTC).
        """
        month = month or datetime.now(UTC).strftime("%Y-%m")

        cost = await self._get_cost(agent_id, month, scope="monthly")
        tokens = await self._get_tokens(agent_id, month, scope="monthly")

        # Compute daily average
        today = datetime.now(UTC)
        day_of_month = today.day if month == today.strftime("%Y-%m") else 30
        avg_daily_cost = float(cost) / max(day_of_month, 1)

        return {
            "agent_id": agent_id,
            "month": month,
            "token_count": tokens,
            "cost_usd": float(cost),
            "avg_daily_cost": avg_daily_cost,
            "days_tracked": day_of_month,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_cost(self, agent_id: str, period: str, *, scope: str) -> Decimal:
        """Fetch cost from Redis or in-memory store."""
        if self._redis is not None:
            if scope == "daily":
                key = self._DAILY_COST_KEY.format(agent_id=agent_id, date=period)
            else:
                key = self._MONTHLY_COST_KEY.format(agent_id=agent_id, month=period)
            val = await self._redis.get(key)
            return Decimal(val.decode()) if val else Decimal("0")

        mem_key = f"{agent_id}:{period}"
        if scope == "daily":
            return self._mem_daily_cost.get(mem_key, Decimal("0"))
        return self._mem_monthly_cost.get(mem_key, Decimal("0"))

    async def _get_tokens(self, agent_id: str, period: str, *, scope: str) -> int:
        """Fetch token count from Redis or in-memory store."""
        if self._redis is not None:
            if scope == "daily":
                key = self._DAILY_TOKENS_KEY.format(agent_id=agent_id, date=period)
            else:
                key = self._MONTHLY_TOKENS_KEY.format(agent_id=agent_id, month=period)
            val = await self._redis.get(key)
            return int(val) if val else 0

        mem_key = f"{agent_id}:{period}"
        if scope == "daily":
            return self._mem_daily_tokens.get(mem_key, 0)
        return self._mem_monthly_tokens.get(mem_key, 0)

    async def _get_tasks(self, agent_id: str, day: str) -> int:
        """Fetch task count from Redis or in-memory store."""
        if self._redis is not None:
            key = self._DAILY_TASKS_KEY.format(agent_id=agent_id, date=day)
            val = await self._redis.get(key)
            return int(val) if val else 0

        mem_key = f"{agent_id}:{day}"
        return self._mem_daily_tasks.get(mem_key, 0)

    def _flush_due(self) -> bool:
        return (time.monotonic() - self._last_flush) >= self._FLUSH_INTERVAL_SECONDS

    async def _flush_to_db(self) -> None:
        """Persist buffered records to the database."""
        if not self._buffer:
            return

        records = self._buffer.copy()
        self._buffer.clear()
        self._last_flush = time.monotonic()

        if self._db_session_factory is None:
            logger.debug("cost_flush_skipped_no_db", count=len(records))
            return

        try:
            async with self._db_session_factory() as session:
                for rec in records:
                    await session.execute(
                        # Using raw SQL for flexibility -- the ORM model can be
                        # substituted once the schema is finalised.
                        __import__("sqlalchemy").text(
                            "INSERT INTO cost_records "
                            "(agent_id, tokens, cost_usd, model, tenant_id, recorded_at) "
                            "VALUES (:agent_id, :tokens, :cost_usd, :model, :tenant_id, :recorded_at)"
                        ),
                        {
                            "agent_id": rec.agent_id,
                            "tokens": rec.tokens,
                            "cost_usd": float(rec.cost_usd),
                            "model": rec.model,
                            "tenant_id": rec.tenant_id,
                            "recorded_at": datetime.fromtimestamp(rec.timestamp, tz=UTC),
                        },
                    )
                await session.commit()
                logger.info("cost_flush_complete", count=len(records))
        except Exception as exc:
            logger.error("cost_flush_failed", error=str(exc), count=len(records))
            # Re-queue records that failed to flush
            self._buffer.extend(records)

    async def _emit_budget_exceeded_event(
        self,
        agent_id: str,
        reason: str,
        current_cost: float,
        cap: float,
    ) -> None:
        """Emit a platform event when a budget cap is exceeded."""
        if self._event_emitter is None:
            logger.info(
                "budget_exceeded_event",
                agent_id=agent_id,
                reason=reason,
                current_cost=current_cost,
                cap=cap,
            )
            return

        try:
            await self._event_emitter(
                event_type="agenticorg.agent.cost_cap_exceeded",
                payload={
                    "agent_id": agent_id,
                    "reason": reason,
                    "current_cost": current_cost,
                    "cap": cap,
                    "recommendation": "pause",
                },
            )
        except Exception as exc:
            logger.error("budget_event_emit_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def flush(self) -> None:
        """Force-flush any buffered records to the database."""
        await self._flush_to_db()
