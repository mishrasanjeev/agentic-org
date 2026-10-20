# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The autonomy ladder: how far the agent may go for one task, for one customer.

Five levels, set per task class and per customer, promotable and demotable:

- L0 inform: cited answers from the bank's data; a read never needs more.
- L1 recommend: a suggestion with its "why this"; nothing is prepared.
- L2 prepare: the action is prepared and waits for one tap (the default
  ceiling for a transaction until the bank raises it).
- L3 execute with approval: executes after the bank's step-up of the specific
  action (a signed approval bound to the action).
- L4 execute within mandate: no per-action approval inside the caps,
  counterparties and window the customer set; outside them the action falls
  back to a step-up.

A mandate is the customer's standing instruction and the agent's envelope.
Caps are checked here before an action is prepared and again by the bank's
rail when it executes; never inside the model. The deployment ceiling
(``AGENTICORG_CUSTOMER_AGENT_MAX_AUTONOMY``, 2 by default) bounds every level.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from core.config import settings
from core.conversation.intents import INTENTS, Intent

LEVEL_NAMES: dict[int, str] = {
    0: "inform",
    1: "recommend",
    2: "prepare",
    3: "execute_with_approval",
    4: "execute_within_mandate",
}
READ_CLASS = "read"
CONVERSATION_CLASS = "conversation"
REQUIRES_NONE = "none"
REQUIRES_CONFIRM = "confirm"
REQUIRES_STEP_UP = "step_up"
PERIODS: dict[str, timedelta] = {"day": timedelta(days=1), "week": timedelta(days=7), "month": timedelta(days=30)}


def ceiling() -> int:
    """The highest level this deployment allows any customer."""
    try:
        return max(0, min(4, int(settings.customer_agent_max_autonomy)))
    except (TypeError, ValueError):
        return 2


def task_class(intent: Intent | str | None) -> str:
    """The task class an intent belongs to: ``read`` for enquiries, the intent's own name for a transaction."""
    resolved = INTENTS.get(intent) if isinstance(intent, str) else intent
    if resolved is None:
        return CONVERSATION_CLASS
    if resolved.risk == "transact":
        return resolved.name
    if resolved.risk == "read":
        return READ_CLASS
    return CONVERSATION_CLASS


def default_level(klass: str) -> int:
    """The level a customer has without a mandate: reads always run, transactions prepare."""
    if klass in (READ_CLASS, CONVERSATION_CLASS):
        return 0
    return min(2, ceiling())


def mandate_active(mandate: Any, now: datetime | None = None) -> bool:
    if mandate is None or str(getattr(mandate, "status", "")) != "active":
        return False
    moment = now or datetime.now(UTC)
    expires = getattr(mandate, "expires_at", None)
    if expires is not None and expires <= moment:
        return False
    window = getattr(mandate, "window", None) or {}
    start, end = window.get("start_hour"), window.get("end_hour")
    if isinstance(start, int) and isinstance(end, int) and not (start <= moment.hour < end):
        return False
    return True


@dataclass(frozen=True)
class Decision:
    """What the ladder says about one action: the level, whether it may proceed, and what it still needs."""

    task_class: str
    level: int
    allowed: bool
    requires: str
    reason: str
    cap_detail: str = ""

    @property
    def level_name(self) -> str:
        return LEVEL_NAMES.get(self.level, str(self.level))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_class": self.task_class,
            "level": self.level,
            "level_name": self.level_name,
            "allowed": self.allowed,
            "requires": self.requires,
            "reason": self.reason,
            "cap_detail": self.cap_detail,
        }


def _amount(slots: dict[str, Any]) -> float | None:
    value = slots.get("amount")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _within_caps(mandate: Any, slots: dict[str, Any], spent_in_period: float) -> tuple[bool, str]:
    amount = _amount(slots)
    per_action = getattr(mandate, "per_action_cap", None)
    if per_action is not None and amount is not None and amount > float(per_action):
        return False, "cap_exceeded:per_action"
    period_cap = getattr(mandate, "period_cap", None)
    if period_cap is not None and amount is not None and spent_in_period + amount > float(period_cap):
        return False, "cap_exceeded:period"
    counterparties = [str(c).strip().lower() for c in (getattr(mandate, "counterparties", None) or [])]
    payee = str(slots.get("payee") or slots.get("biller") or "").strip().lower()
    if counterparties and payee and payee not in counterparties:
        return False, "purpose_not_allowed:counterparty"
    return True, "within_mandate"


def decide(
    intent: Intent | str | None,
    slots: dict[str, Any] | None,
    mandate: Any = None,
    *,
    spent_in_period: float = 0.0,
    now: datetime | None = None,
) -> Decision:
    """Whether the agent may act on ``intent`` with ``slots`` for a customer holding ``mandate``, and how."""
    resolved = INTENTS.get(intent) if isinstance(intent, str) else intent
    klass = task_class(resolved)
    slots = dict(slots or {})
    if resolved is None or resolved.risk != "transact":
        return Decision(klass, default_level(klass), True, REQUIRES_NONE, "an enquiry needs no mandate")
    active = mandate_active(mandate, now)
    level = int(getattr(mandate, "autonomy_level", default_level(klass))) if active else default_level(klass)
    level = max(0, min(level, ceiling()))
    if level <= 1:
        return Decision(
            klass,
            level,
            False,
            REQUIRES_NONE,
            "the agent may inform and recommend here; preparing this action needs level 2 or higher",
        )
    if not active:
        return Decision(klass, level, True, REQUIRES_CONFIRM, "no mandate: every action waits for one tap")
    within, detail = _within_caps(mandate, slots, spent_in_period)
    if level == 4 and within:
        return Decision(klass, 4, True, REQUIRES_NONE, "within the customer's mandate", detail)
    if level >= 3:
        return Decision(
            klass,
            level,
            True,
            REQUIRES_STEP_UP,
            "outside the mandate's limits" if not within else "the mandate asks for approval of each action",
            detail,
        )
    return Decision(klass, level, True, REQUIRES_CONFIRM, "the action waits for one tap", detail)


def period_start(mandate: Any, now: datetime | None = None) -> datetime:
    moment = now or datetime.now(UTC)
    return moment - PERIODS.get(str(getattr(mandate, "period", "month")), PERIODS["month"])


def validate_mandate(body: dict[str, Any]) -> dict[str, Any]:
    """Check a mandate request against the ladder; the cleaned fields or ``ValueError``."""
    klass = str(body.get("task_class") or "").strip()
    if klass not in {name for name, intent in INTENTS.items() if intent.risk == "transact"}:
        raise ValueError("task_class must be a transaction the agent knows")
    try:
        level = int(body.get("autonomy_level", 2))
    except (TypeError, ValueError) as exc:
        raise ValueError("autonomy_level must be a whole number from 0 to 4") from exc
    if not 0 <= level <= 4:
        raise ValueError("autonomy_level must be a whole number from 0 to 4")
    if level > ceiling():
        raise ValueError(f"autonomy_level {level} is above this deployment's ceiling of {ceiling()}")
    cleaned: dict[str, Any] = {"task_class": klass, "autonomy_level": level}
    for cap in ("per_action_cap", "period_cap"):
        value = body.get(cap)
        if value is not None:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{cap} must be a number") from exc
            if number <= 0:
                raise ValueError(f"{cap} must be above zero")
            cleaned[cap] = number
    if level == 4 and "per_action_cap" not in cleaned:
        raise ValueError("executing within a mandate needs a per_action_cap")
    period = str(body.get("period") or "month").lower()
    if period not in PERIODS:
        raise ValueError("period must be day, week or month")
    cleaned["period"] = period
    counterparties = body.get("counterparties") or []
    if not isinstance(counterparties, list) or len(counterparties) > 50:
        raise ValueError("counterparties must be a list of at most 50 names")
    cleaned["counterparties"] = [str(c).strip()[:80] for c in counterparties if str(c).strip()]
    window = body.get("window") or {}
    if not isinstance(window, dict):
        raise ValueError("window must be an object")
    cleaned["window"] = {k: window[k] for k in ("start_hour", "end_hour") if isinstance(window.get(k), int)}
    expires = body.get("expires_at")
    if expires:
        try:
            cleaned["expires_at"] = datetime.fromisoformat(str(expires))
        except ValueError as exc:
            raise ValueError("expires_at must be an ISO 8601 instant") from exc
    return cleaned
