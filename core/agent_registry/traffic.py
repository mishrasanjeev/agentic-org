# SPDX-License-Identifier: Apache-2.0
"""Traffic allocation between two published agents, and its one-action rollback.

An agent may carry a split in its configuration (``config["traffic_split"]``):
another agent of the tenant and a percentage. While
``AGENTICORG_AGENT_TRAFFIC_SPLIT_ENABLED`` is on, that share of the runs
asked of the agent through the agents API are served by the other agent
instead, and the response says which agent served the run. The choice is
made from the run's correlation id, so a retry of the same run lands on the
same agent and the share is reproducible; without one it is random.

The other agent must be active and in the same tenant when the split is set;
at run time an inactive target is skipped and the run stays on the agent
asked for, with the reason logged, so a split never sends work to an agent
that is not in production. Removing the split (``DELETE``) is the rollback:
one action, and every run returns to the agent asked for.

Off, a stored split is kept and reported and no run is redirected.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

SPLIT_KEY = "traffic_split"


class TrafficError(ValueError):
    """The split cannot be used."""


def enabled() -> bool:
    return bool(settings.agent_traffic_split_enabled)


def parse_split(raw: Any, *, own_id: uuid.UUID) -> dict[str, Any]:
    """A split as it is stored: the other agent's id and a whole-number percentage, 1 to 100."""
    if not isinstance(raw, dict):
        raise TrafficError("the split must be an object")
    unknown = sorted(set(raw) - {"to_agent_id", "percent"})
    if unknown:
        raise TrafficError(f"unknown split keys: {', '.join(unknown)}")
    try:
        target = uuid.UUID(str(raw.get("to_agent_id") or ""))
    except ValueError:
        raise TrafficError("to_agent_id must be an agent id") from None
    if target == own_id:
        raise TrafficError("an agent cannot split traffic to itself")
    percent = raw.get("percent")
    if isinstance(percent, bool) or not isinstance(percent, int) or not 1 <= percent <= 100:
        raise TrafficError("percent is a whole number between 1 and 100")
    return {"to_agent_id": str(target), "percent": percent}


def declared(agent: Any) -> dict[str, Any] | None:
    split = (getattr(agent, "config", None) or {}).get(SPLIT_KEY)
    return dict(split) if isinstance(split, dict) and split else None


def bucket(correlation_id: str | None) -> int:
    """A number from 0 to 99 for the run: from its correlation id, or random."""
    if correlation_id:
        digest = hashlib.sha256(str(correlation_id).encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") % 100
    return secrets.randbelow(100)


def chooses_target(split: dict[str, Any], correlation_id: str | None = None, *, draw: int | None = None) -> bool:
    """Whether the run's draw falls in the target's share.

    A run makes one draw (``bucket``); callers that decide in two steps pass
    the same ``draw`` to both, so a random draw is never made twice.
    """
    number = bucket(correlation_id) if draw is None else int(draw)
    return number < int(split["percent"])


def choose(
    agent: Any, correlation_id: str | None, load_target: Any, *, draw: int | None = None
) -> tuple[Any, str | None]:
    """The agent that serves the run and, when it is not the one asked for, why it was chosen.

    ``load_target`` returns the target agent row for an id, or None. A target
    that is missing or not active is skipped and the run stays on ``agent``.
    Nothing here is awaited: callers load the target before choosing, and pass
    the ``draw`` they loaded it on.
    """
    if not enabled():
        return agent, None
    split = declared(agent)
    if split is None or not chooses_target(split, correlation_id, draw=draw):
        return agent, None
    target = load_target(uuid.UUID(split["to_agent_id"]))
    if target is None or str(getattr(target, "status", "")) != "active":
        logger.warning(
            "agent_traffic_split_target_skipped",
            agent_id=str(agent.id),
            reason="missing" if target is None else "not_active",
        )
        return agent, None
    return target, f"traffic_split:{split['percent']}"
