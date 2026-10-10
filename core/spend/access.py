# SPDX-License-Identifier: Apache-2.0
"""Who reads what in spend intelligence.

Commercial reads (rate cards, commitments, the price quote, invoices,
reconciliations and the gate) are for a human administrator or
auditor only: a person whose domains are unrestricted. Machine credentials
can hold ``audit:read`` through agent grants and are refused here. The
audit rows of commercial writes, which carry the same values, and of
maintenance jobs, which carry their parameters, amounts and counts, are
hidden from the general audit read for the same callers.

One read rule for usage, the rule ``GET /audit`` follows (administrators
and auditors see everything): a tenant-wide reader, that is an
administrator or a person whose domains are unrestricted (an auditor),
reads every usage record, every rollup, coverage, meter gaps, jobs and the
ledger comparison with no agent filter. Every other reader (a domain-scoped
person, a machine credential without the administrator scope) gets the
existing agent visibility rule (``core/ownership.py``) on every read that
carries an agent: the records, every rollup grouping (not only the grouping
by agent) and the ledger comparison. A record of a personal agent is
visible to its owner, a record of a shared agent within the reader's
domains, a record with no agent to every reader. Tenant-wide figures that
carry no agent to filter by (coverage, the Gate 1 attribution measure;
meter gaps; maintenance jobs, their parameters and their record counts) are
refused to those readers with 403 ``tenant_wide_read_refused``: a filtered
version would let a reader difference two views. The initiating user's id
is shown to administrators and auditors only.
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
# anyone the commercial routes refuse. So do the rows jobs write: a job's
# parameters (a correction's reason, card ids, ranges), the amounts a
# restatement or re-attribution moved per billing date and card, the INR
# totals a settlement moved per currency, and rebuild and backfill counts.
# Each event-type prefix names the model whose rows those audit rows
# describe: the application never deletes those rows (invoices and runs are
# superseded, never removed) and their audit rows commit with them
# (``spend.job.*``, ``spend.usage.*``, ``spend.fx.*`` and ``spend.rollups.*``
# rows are written only by jobs and by queueing one), so a tenant with none of
# them has no such audit rows. ``spend.fx.`` does not match the
# ``spend.fx_rates.*`` rows of FX reference data, which stay visible. Item
# acceptances and carried-over acceptances are ``spend.reconciliations.*``
# events and describe rows of their run's table.
COMMERCIAL_AUDIT_SOURCES = (
    ("spend.rate_cards.", "SpendRateCard"),
    ("spend.commitments.", "SpendCommitment"),
    ("spend.invoices.", "SpendInvoice"),
    ("spend.reconciliations.", "SpendReconciliation"),
    ("spend.job.", "SpendJob"),
    ("spend.usage.", "SpendJob"),
    ("spend.fx.", "SpendJob"),
    ("spend.rollups.", "SpendJob"),
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


def is_tenant_wide_reader(caller: Caller) -> bool:
    """An administrator or a person whose domains are unrestricted (an auditor): the readers of usage
    figures that sum every agent's usage and cannot be filtered by agent, and the same readers who read
    every usage record, rollup and ledger comparison unfiltered (``read_view``).

    Only the administrator and auditor roles have unrestricted domains, so these are the callers
    ``GET /audit`` shows every row to (``api/v1/audit.py``). A machine credential without the
    administrator scope, and every domain role, is refused.
    """
    return caller.is_admin or is_commercial_reader(caller)


def require_tenant_wide(caller: Caller) -> None:
    """403 ``tenant_wide_read_refused`` for a reader whose agent visibility or domains are restricted."""
    if not is_tenant_wide_reader(caller):
        raise SpendError(
            403,
            "tenant_wide_read_refused",
            "coverage, meter gaps and maintenance jobs sum every agent's usage: they are for an administrator or "
            "auditor",
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


def _source_model(name: str) -> Any:
    """The ORM class a ``COMMERCIAL_AUDIT_SOURCES`` entry names (any spend model the package exports)."""
    import core.models as models

    return getattr(models, name)


async def commercial_rows_kept(session: Any, tenant_id: Any) -> bool:
    """Whether the tenant keeps any row a commercial audit row describes (one query, an index probe per model).

    False means the tenant has no commercial audit rows, so ``GET /audit``
    runs the query it ran before spend intelligence existed.
    """
    names = dict.fromkeys(model for _prefix, model in COMMERCIAL_AUDIT_SOURCES)
    kept = [exists().where(_source_model(name).tenant_id == tenant_id) for name in names]
    return bool((await session.execute(select(or_(*kept)))).scalar())


def is_commercial_audit_event(event_type: Any) -> bool:
    """The same test as ``commercial_audit_clause``, on a loaded row's ``event_type``."""
    return isinstance(event_type, str) and event_type.startswith(COMMERCIAL_AUDIT_PREFIXES)


def read_view(caller: Caller) -> ReadView:
    """The filter and redactions a caller's usage reads get.

    No agent filter for a tenant-wide reader (an administrator, or a person whose domains are unrestricted:
    an auditor), the readers of coverage, gaps and jobs, so every reader of a tenant-wide figure also reads
    the records behind it; the agent visibility rule for everyone else.
    """
    from core.models.agent import Agent

    trusted = is_commercial_reader(caller)
    return ReadView(
        agent_clause=None if is_tenant_wide_reader(caller) else agent_visibility_clause(Agent, caller),
        show_user_ids=trusted,
        commercial=trusted,
    )


def usage_filter(view: ReadView, record_table: Any, agent_table: Any, *, tenant_id: Any) -> ColumnElement[bool]:
    """Records with no agent, or with an agent of ``tenant_id`` that ``view`` may see.

    ``record_table`` and ``agent_table`` are tables (or ORM classes' ``__table__``)
    carrying ``agent_id`` and ``id`` / ``tenant_id``. The subquery compares the
    agents' tenant with the bound ``tenant_id``, never with the outer table, so
    the database runs it once instead of once per record or rollup row. An
    agent id whose row is gone is visible to tenant-wide readers only.
    """
    from sqlalchemy import true

    if view.agent_clause is None:
        return true()
    visible = select(agent_table.c.id).where(agent_table.c.tenant_id == tenant_id, view.agent_clause)
    return or_(record_table.c.agent_id.is_(None), record_table.c.agent_id.in_(visible))


def redact_user(view: ReadView, user_id: Any) -> str | None:
    """The initiating user's id for a reader allowed to see it, else ``None``."""
    if not view.show_user_ids or user_id is None:
        return None
    return str(user_id)
