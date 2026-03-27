"""Route tasks to the most capable agent instance."""

from __future__ import annotations

import logging
import uuid
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# Maps business domains to their executive-level role labels.
DOMAIN_TO_ROLE = {
    "finance": "cfo",
    "hr": "chro",
    "marketing": "cmo",
    "ops": "coo",
    "backoffice": "admin",
}

# Statuses that indicate an agent cannot accept escalated work.
_INACTIVE_STATUSES = frozenset({"paused", "retired"})


class TaskRouter:
    """Route workflow tasks to specific agent instances.

    When multiple agents share the same agent_type, resolves the best
    instance using routing_filter, specialization, and availability.
    """

    async def route(
        self, workflow_run_id, step_id, step_index, total_steps, task, context
    ):
        agent_type = task.get("agent", task.get("agent_type", ""))
        target_agent_id = task.get("agent_id")
        routing_context = task.get("routing_context", {})

        return {
            "message_id": f"msg_{uuid.uuid4().hex[:12]}",
            "workflow_run_id": workflow_run_id,
            "step_id": step_id,
            "step_index": step_index,
            "total_steps": total_steps,
            "target_agent_type": agent_type,
            "target_agent_id": target_agent_id,
            "routing_context": routing_context,
            "task": task,
            "context": context,
        }

    @staticmethod
    async def resolve_agent_instance(
        tenant_id: UUID,
        agent_type: str,
        routing_context: dict[str, Any],
        session,
    ) -> UUID | None:
        """Find the best agent instance when multiple share a type.

        Resolution order:
        1. Exact routing_filter match (e.g., region=APAC)
        2. Specialization keyword match
        3. First active agent of that type (fallback)
        """
        from sqlalchemy import select

        from core.models.agent import Agent

        query = (
            select(Agent)
            .where(
                Agent.tenant_id == tenant_id,
                Agent.agent_type == agent_type,
                Agent.status == "active",
            )
            .order_by(Agent.created_at.asc())
        )
        result = await session.execute(query)
        candidates = result.scalars().all()

        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0].id

        # Try routing_filter match
        if routing_context:
            for agent in candidates:
                if agent.routing_filter:
                    if all(
                        agent.routing_filter.get(k) == v
                        for k, v in routing_context.items()
                        if k in agent.routing_filter
                    ):
                        return agent.id

        # Try specialization keyword match
        task_description = routing_context.get("description", "").lower()
        if task_description:
            for agent in candidates:
                if agent.specialization and agent.specialization.lower() in task_description:
                    return agent.id

        # Fallback: first active agent
        return candidates[0].id

    # ------------------------------------------------------------------
    # Escalation logic
    # ------------------------------------------------------------------

    @staticmethod
    async def escalate(
        agent_id: UUID, session, max_depth: int = 5
    ) -> dict:
        """Walk the parent-agent chain to find an escalation target.

        Resolution order:
        1. Walk *parent_agent_id* up to ``max_depth`` hops.
           - Skip parents whose status is paused/retired.
           - Abort immediately if a cycle is detected.
        2. If the chain is exhausted without finding an active parent,
           fall back to the **domain head** (root active agent in the
           same domain, i.e. ``parent_agent_id IS NULL``).
        3. If no domain head exists, return ``None`` with
           ``escalation_type="human"`` so the caller can route to a
           human operator.

        Returns a dict with keys:
            escalated_to   – target agent UUID or None (human)
            escalation_type – "parent_agent" | "domain_head" | "human"
            chain          – list of agent-ID strings walked (audit trail)
            reason         – human-readable explanation
        """
        from core.models.agent import Agent

        chain: list[str] = []
        visited: set[UUID] = set()

        current = await session.get(Agent, agent_id)
        if current is None:
            return {
                "escalated_to": None,
                "escalation_type": "human",
                "chain": chain,
                "reason": f"Starting agent {agent_id} not found",
            }

        origin_tenant_id = current.tenant_id
        origin_domain = current.domain
        visited.add(current.id)
        chain.append(str(current.id))

        # --- 1. Parent-chain walk ---
        depth = 0
        cursor = current
        while depth < max_depth and cursor.parent_agent_id is not None:
            parent_id = cursor.parent_agent_id

            # Cycle detection
            if parent_id in visited:
                logger.warning(
                    "Escalation cycle detected: %s already visited (chain=%s)",
                    parent_id,
                    chain,
                )
                break

            parent = await session.get(Agent, parent_id)
            if parent is None:
                logger.warning(
                    "Parent agent %s referenced by %s does not exist",
                    parent_id,
                    cursor.id,
                )
                break

            visited.add(parent.id)
            chain.append(str(parent.id))
            depth += 1

            # Skip inactive parents — keep walking
            if parent.status in _INACTIVE_STATUSES:
                logger.info(
                    "Skipping inactive parent %s (status=%s)",
                    parent.id,
                    parent.status,
                )
                cursor = parent
                continue

            # Found an active parent
            if parent.status == "active":
                return {
                    "escalated_to": parent.id,
                    "escalation_type": "parent_agent",
                    "chain": chain,
                    "reason": (
                        f"Escalated to active parent {parent.id} "
                        f"after {depth} hop(s)"
                    ),
                }

            # Any other non-active status — keep walking
            cursor = parent

        # --- 2. Domain-head fallback ---
        domain_head_id = await TaskRouter.resolve_domain_head(
            origin_tenant_id, origin_domain, session
        )
        if domain_head_id is not None and domain_head_id not in visited:
            chain.append(str(domain_head_id))
            return {
                "escalated_to": domain_head_id,
                "escalation_type": "domain_head",
                "chain": chain,
                "reason": (
                    f"Parent chain exhausted after {depth} hop(s); "
                    f"fell back to domain head {domain_head_id} "
                    f"(domain={origin_domain})"
                ),
            }

        # --- 3. Human fallback ---
        return {
            "escalated_to": None,
            "escalation_type": "human",
            "chain": chain,
            "reason": (
                f"No active parent or domain head found for "
                f"agent {agent_id} (domain={origin_domain}); "
                "escalating to human operator"
            ),
        }

    @staticmethod
    async def resolve_domain_head(
        tenant_id: UUID, domain: str, session
    ) -> UUID | None:
        """Find the domain-head (root) agent for a given tenant + domain.

        A domain head is defined as an active agent with no parent
        (``parent_agent_id IS NULL``) in the specified domain.  When
        multiple matches exist the oldest (by ``created_at``) wins.
        """
        from sqlalchemy import select

        from core.models.agent import Agent

        query = (
            select(Agent.id)
            .where(
                Agent.tenant_id == tenant_id,
                Agent.domain == domain,
                Agent.parent_agent_id.is_(None),
                Agent.status == "active",
            )
            .order_by(Agent.created_at.asc())
            .limit(1)
        )
        result = await session.execute(query)
        row = result.scalar_one_or_none()
        return row

    @staticmethod
    async def escalate_to_parent(
        agent_id: UUID, session
    ) -> UUID | None:
        """Backward-compatible wrapper around :meth:`escalate`.

        Returns the target agent UUID or ``None`` (human escalation).
        """
        result = await TaskRouter.escalate(agent_id, session)
        return result["escalated_to"]
