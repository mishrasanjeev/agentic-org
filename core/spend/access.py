# SPDX-License-Identifier: Apache-2.0
"""Who reads what in spend intelligence.

Commercial reads (rate cards, commitments, the price quote, invoices,
reconciliations and the gate) are for a human administrator or
auditor only: a person whose domains are unrestricted. Machine credentials
can hold ``audit:read`` through agent grants and are refused here. The
audit rows of commercial writes, which carry the same values, are hidden
from the general audit read for the same callers.

Usage reads apply the existing agent visibility rule (``core/ownership.py``)
to everyone but administrators: a record of a personal agent is visible to
its owner and to administrators, a record of a shared agent within the
caller's domains, a record with no agent to every reader. The initiating
user's id is shown to administrators and auditors only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, not_, or_, select

from core.ownership import Caller, agent_visibility_clause
from core.spend.errors import SpendError

# Audit rows of commercial writes carry the values themselves (prices, tiers,
# committed amounts, overage prices, invoice totals, reconciliation figures and
# acceptance reasons) in ``details``; the general audit read hides them from
# anyone the commercial routes refuse. Each event-type prefix names the model
# whose rows those audit rows describe: the application never deletes those
# rows (invoices and runs are superseded, never removed) and their audit rows
# commit with them, so a tenant with none of them has no commercial audit rows.
# Item acceptances and carried-over acceptances are ``spend.reconciliations.*``
# events and describe rows of their run's table.
COMMERCIAL_AUDIT_SOURCES = (
    ("spend.rate_cards.", "SpendRateCard"),
    ("spend.commitments.", "SpendCommitment"),
    ("spend.invoices.", "SpendInvoice"),
    ("spend.reconciliations.", "SpendReconciliation"),
)
COMMERCIAL_AUDIT_PREFIXES = tuple(prefix for prefix, _model in COMMERCIAL_AUDIT_SOURCES)


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
            403,
            "commercial_read_refused",
            "rate cards, commitments, prices, invoices and reconciliations are for an administrator or auditor",
        )


def commercial_audit_clause(caller: Caller, event_type: Any) -> ColumnElement[bool] | None:
    """``None`` for a commercial reader; otherwise a clause on ``event_type`` hiding commercial spend audit rows.

    ``GET /audit`` applies it, whatever ``spend_intelligence_enabled`` says
    (the rows outlive the flag), when ``commercial_rows_kept`` finds the
    tenant has such rows, so a caller refused ``GET /spend/rate-cards``
    cannot read the same prices from the rows their writes left.
    """
    if is_commercial_reader(caller):
        return None
    return and_(*(not_(event_type.startswith(prefix, autoescape=True)) for prefix in COMMERCIAL_AUDIT_PREFIXES))


async def commercial_rows_kept(session: Any, tenant_id: Any) -> bool:
    """Whether the tenant keeps any row a commercial audit row describes (one query, one index probe per table).

    False means the tenant has no commercial audit rows, so ``GET /audit``
    runs the query it ran before spend intelligence existed.
    """
    import core.models as models

    kept = [
        exists().where(getattr(models, model).tenant_id == tenant_id) for _prefix, model in COMMERCIAL_AUDIT_SOURCES
    ]
    return bool((await session.execute(select(or_(*kept)))).scalar())


def is_commercial_audit_event(event_type: Any) -> bool:
    """The same test as ``commercial_audit_clause``, on a loaded row's ``event_type``."""
    return isinstance(event_type, str) and event_type.startswith(COMMERCIAL_AUDIT_PREFIXES)


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
