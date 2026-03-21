"""HPA integration for auto-scaling.

Supports three scaling signals:
1. Queue depth  -- scale when pending tasks exceed threshold
2. CPU usage    -- scale when average CPU exceeds target
3. Schedule     -- time-of-day / day-of-week pre-scaling

The decision engine merges all signals and picks the highest recommended
replica count, clamped to [min_replicas, max_replicas].
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Schedule rule
# ---------------------------------------------------------------------------


class ScheduleRule:
    """A time-based scaling rule.

    Parameters
    ----------
    days:
        ISO weekday numbers (1=Mon .. 7=Sun) when this rule is active.
    start_hour:
        Hour (0-23, UTC) when the rule begins.
    end_hour:
        Hour (0-23, UTC) when the rule ends.
    min_replicas:
        Minimum replicas while the rule is active.
    """

    def __init__(
        self,
        days: list[int],
        start_hour: int,
        end_hour: int,
        min_replicas: int,
    ) -> None:
        self.days = days
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.min_replicas = min_replicas

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        if now.isoweekday() not in self.days:
            return False
        if self.start_hour <= self.end_hour:
            return self.start_hour <= now.hour < self.end_hour
        # Wrap-around (e.g. 22 -> 06)
        return now.hour >= self.start_hour or now.hour < self.end_hour


# ---------------------------------------------------------------------------
# HPAIntegration
# ---------------------------------------------------------------------------


class HPAIntegration:
    """Horizontal Pod Autoscaler decision engine.

    Merges queue-depth, CPU, and schedule signals into a single scaling
    recommendation.
    """

    def __init__(
        self,
        *,
        schedule_rules: list[ScheduleRule] | None = None,
        cooldown_seconds: float = 120.0,
    ) -> None:
        self._schedule_rules = schedule_rules or []
        self._cooldown_seconds = cooldown_seconds
        self._last_scale_action: dict[str, float] = {}  # agent_type -> epoch

    # ------------------------------------------------------------------
    # Individual signal evaluators
    # ------------------------------------------------------------------

    def _queue_depth_replicas(
        self,
        queue_depth: int,
        current_replicas: int,
        config: dict[str, Any],
    ) -> int:
        """Compute desired replicas from queue depth.

        Scale-up when queue_depth > scale_up_threshold.
        Scale-down when queue_depth < scale_down_threshold.
        """
        scale_up_threshold = config.get("scale_up_threshold", 30)
        scale_down_threshold = config.get("scale_down_threshold", 5)
        tasks_per_replica = config.get("tasks_per_replica", 10)

        if queue_depth > scale_up_threshold:
            # Size to clear the queue at `tasks_per_replica` per pod
            desired = math.ceil(queue_depth / max(tasks_per_replica, 1))
            return max(desired, current_replicas + 1)

        if queue_depth < scale_down_threshold and current_replicas > 1:
            desired = max(math.ceil(queue_depth / max(tasks_per_replica, 1)), 1)
            return min(desired, current_replicas - 1)

        return current_replicas

    def _cpu_replicas(
        self,
        cpu_usage_pct: float,
        current_replicas: int,
        config: dict[str, Any],
    ) -> int:
        """Compute desired replicas from average CPU utilisation.

        Uses the standard HPA formula:
            desired = ceil(current * (current_metric / target_metric))
        """
        target_cpu = config.get("target_cpu_pct", 60.0)
        if target_cpu <= 0:
            return current_replicas

        ratio = cpu_usage_pct / target_cpu
        desired = math.ceil(current_replicas * ratio)
        return max(desired, 1)

    def _schedule_replicas(self, now: datetime | None = None) -> int:
        """Return the highest min_replicas from active schedule rules."""
        now = now or datetime.now(UTC)
        active_mins = [rule.min_replicas for rule in self._schedule_rules if rule.is_active(now)]
        return max(active_mins) if active_mins else 0

    # ------------------------------------------------------------------
    # Combined check
    # ------------------------------------------------------------------

    async def check_scaling(
        self,
        agent_type: str,
        queue_depth: int,
        config: dict[str, Any],
        *,
        cpu_usage_pct: float | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Evaluate all scaling signals and return a recommendation.

        Parameters
        ----------
        agent_type:
            Identifier for the agent pool being scaled.
        queue_depth:
            Number of pending tasks in the agent's work queue.
        config:
            Dict containing:
                - current_replicas (int, default 1)
                - min_replicas (int, default 1)
                - max_replicas (int, default 5)
                - scale_up_threshold (int, default 30)
                - scale_down_threshold (int, default 5)
                - tasks_per_replica (int, default 10)
                - target_cpu_pct (float, default 60.0)
        cpu_usage_pct:
            Average CPU utilisation across current replicas (0-100).
            When *None*, the CPU signal is skipped.
        now:
            Override the current time (for testing schedule rules).

        Returns
        -------
        dict with keys:
            action:           "scale_up" | "scale_down" | "no_change"
            replicas:         recommended replica count
            current_replicas: previous replica count
            signals:          per-signal breakdown
            reason:           human-readable explanation
        """
        current = config.get("current_replicas", 1)
        min_r = config.get("min_replicas", 1)
        max_r = config.get("max_replicas", 5)

        signals: dict[str, dict[str, Any]] = {}

        # 1. Queue depth
        q_desired = self._queue_depth_replicas(queue_depth, current, config)
        signals["queue_depth"] = {
            "desired_replicas": q_desired,
            "queue_depth": queue_depth,
        }

        # 2. CPU (optional)
        cpu_desired = current
        if cpu_usage_pct is not None:
            cpu_desired = self._cpu_replicas(cpu_usage_pct, current, config)
            signals["cpu"] = {
                "desired_replicas": cpu_desired,
                "cpu_usage_pct": cpu_usage_pct,
                "target_cpu_pct": config.get("target_cpu_pct", 60.0),
            }

        # 3. Schedule
        sched_min = self._schedule_replicas(now)
        if sched_min > 0:
            signals["schedule"] = {
                "desired_replicas": sched_min,
                "active_schedule_min": sched_min,
            }

        # Merge: take the maximum recommendation, then clamp
        desired = max(q_desired, cpu_desired, sched_min)
        desired = max(min_r, min(desired, max_r))

        # Determine action
        if desired > current:
            action = "scale_up"
            reason = self._build_reason("scale_up", current, desired, signals)
        elif desired < current:
            action = "scale_down"
            reason = self._build_reason("scale_down", current, desired, signals)
        else:
            action = "no_change"
            reason = f"Replicas stable at {current}"

        # Cooldown check: prevent flapping
        if action != "no_change" and not self._check_cooldown(agent_type):
            logger.info(
                "scaling_cooldown",
                agent_type=agent_type,
                blocked_action=action,
                desired=desired,
            )
            return {
                "action": "no_change",
                "replicas": current,
                "current_replicas": current,
                "signals": signals,
                "reason": f"Scaling action blocked by cooldown ({self._cooldown_seconds}s)",
            }

        if action != "no_change":
            import time

            self._last_scale_action[agent_type] = time.monotonic()
            logger.info(
                action,
                agent_type=agent_type,
                from_r=current,
                to_r=desired,
                signals=list(signals.keys()),
            )

        return {
            "action": action,
            "replicas": desired,
            "current_replicas": current,
            "signals": signals,
            "reason": reason,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_cooldown(self, agent_type: str) -> bool:
        """Return True if enough time has passed since the last scale action."""
        import time

        last = self._last_scale_action.get(agent_type)
        if last is None:
            return True
        return (time.monotonic() - last) >= self._cooldown_seconds

    @staticmethod
    def _build_reason(
        action: str,
        current: int,
        desired: int,
        signals: dict[str, dict[str, Any]],
    ) -> str:
        parts = [f"{action}: {current} -> {desired} replicas"]
        for name, data in signals.items():
            sig_desired = data.get("desired_replicas", "?")
            parts.append(f"  {name}: wants {sig_desired}")
        return "; ".join(parts)

    # ------------------------------------------------------------------
    # Schedule management
    # ------------------------------------------------------------------

    def add_schedule_rule(self, rule: ScheduleRule) -> None:
        """Add a schedule-based scaling rule at runtime."""
        self._schedule_rules.append(rule)
        logger.info(
            "schedule_rule_added",
            days=rule.days,
            start=rule.start_hour,
            end=rule.end_hour,
            min_replicas=rule.min_replicas,
        )

    def clear_schedule_rules(self) -> None:
        """Remove all schedule rules."""
        self._schedule_rules.clear()
