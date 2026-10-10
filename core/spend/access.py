# SPDX-License-Identifier: Apache-2.0
"""Who reads what in spend intelligence.

Commercial reads (rate cards, commitments, the price quote, and later
invoices, reconciliations and the gate) are for a human administrator or
auditor only: a person whose domains are unrestricted. Machine credentials
can hold ``audit:read`` through agent grants and are refused here.

Usage reads apply the existing agent visibility rule (``core/ownership.py``)
to everyone but administrators: a record of a personal agent is visible to
its owner and to administrators, a record of a shared agent within the
caller's domains, a record with no agent to every reader. The initiating
user's id is shown to administrators and auditors only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, or_, select

from core.ownership import Caller, agent_visibility_clause
from core.spend.errors import SpendError


@dataclass(frozen=True)
class ReadView:
    agent_clause: ColumnElement[bool] | None  # None = no agent filter
    show_user_ids: bool
    commercial: bool


def is_commercial_reader(caller: Caller) -> bool:
    """A human administrator or auditor: a person whose domains are unrestricted."""
    return caller.is_human and caller.domains is None


def require_commercial(caller: Caller) -> None:
    """403 ``commercial_read_refused`` for anyone but a human administrator or auditor."""
    if not is_commercial_reader(caller):
        raise SpendError(
            403, "commercial_read_refused", "rate cards, commitments and prices are for an administrator or auditor"
        )


def read_view(caller: Caller) -> ReadView:
    """The filter and redactions a caller's usage reads get."""
    from core.models.agent import Agent

    trusted = is_commercial_reader(caller)
    return ReadView(
        agent_clause=None if caller.is_admin else agent_visibility_clause(Agent, caller),
        show_user_ids=trusted,
        commercial=trusted,
    )


def usage_filter(view: ReadView, record_table: Any, agent_table: Any) -> ColumnElement[bool]:
    """Records with no agent, or with an agent of the record's tenant that ``view`` may see.

    ``record_table`` and ``agent_table`` are tables (or ORM classes' ``__table__``)
    carrying ``agent_id`` / ``tenant_id`` and ``id`` / ``tenant_id``. An agent id
    whose row is gone is visible to administrators only.
    """
    from sqlalchemy import true

    if view.agent_clause is None:
        return true()
    visible = select(agent_table.c.id).where(agent_table.c.tenant_id == record_table.c.tenant_id, view.agent_clause)
    return or_(record_table.c.agent_id.is_(None), record_table.c.agent_id.in_(visible))


def redact_user(view: ReadView, user_id: Any) -> str | None:
    """The initiating user's id for a reader allowed to see it, else ``None``."""
    if not view.show_user_ids or user_id is None:
        return None
    return str(user_id)
