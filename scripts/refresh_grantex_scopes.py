#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Re-register agents' Grantex scopes with the permission levels enforcement understands.

Agents registered before this fix carry ``tool:{connector}:execute:{tool}``
scopes. ``grantex.enforce`` reads the third segment as a permission level
(``read < write < delete < admin``), so ``execute`` grants nothing and a grant
delegated for such an agent denies every call. This script recomputes each
registered agent's scopes from its authorized tools (the same mapping new
registrations and ``PATCH /agents/{id}`` now use), updates the agent on Grantex
and stores the new scopes in ``config.grantex.grantex_scopes`` - that key only,
so a concurrent change to the rest of the agent's config is kept. Deleted
agents are skipped. Scopes are de-duplicated; an agent whose tools need more
than 100 distinct scopes is reported (``scope_limit_exceeded``) and left
unchanged.

    python scripts/refresh_grantex_scopes.py --tenant <tenant uuid>            # report only
    python scripts/refresh_grantex_scopes.py --tenant <tenant uuid> --apply
    python scripts/refresh_grantex_scopes.py --all-tenants --apply

Grantex is updated before the database, so a failed database write leaves the
agent with its new scopes on Grantex and the old ones stored (reported as
``storage_failed``; the run continues with the next agent); re-running is
idempotent. Output is one JSON line per agent (ids, counts, outcome; no
tokens or keys) and a summary. The exit status is 1 when any agent failed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class ScopeRefresh:
    agent_id: str
    grantex_agent_id: str
    before: list[str]
    after: list[str]
    # unchanged | would_update | updated | not_registered
    # | grantex_failed | storage_failed | scope_limit_exceeded | connector_lookup_failed
    outcome: str
    error: str = ""

    def as_json(self) -> str:
        line: dict[str, Any] = {
            "agent_id": self.agent_id,
            "grantex_agent_id": self.grantex_agent_id,
            "outcome": self.outcome,
            "scopes_before": len(self.before),
            "scopes_after": len(self.after),
            "execute_scopes_before": sum(1 for s in self.before if ":execute:" in s),
        }
        if self.error:
            line["error"] = self.error
        return json.dumps(line, sort_keys=True)


FAILED_OUTCOMES = frozenset({"grantex_failed", "storage_failed", "scope_limit_exceeded", "connector_lookup_failed"})


async def refresh_agent_scopes(
    *,
    agent_id: str,
    domain: str,
    authorized_tools: list[str],
    config: dict[str, Any],
    connector_names: list[str] | None,
    grantex_client: Any,
    apply: bool,
    persist: Callable[[list[str]], Awaitable[None]],
) -> ScopeRefresh:
    """Recompute one agent's scopes and, with ``apply``, push them to Grantex then storage.

    ``persist`` stores the new scope list as ``config.grantex.grantex_scopes``.
    """
    from auth.grantex_registration import (
        ScopeLimitExceededError,
        _tools_to_scopes,
        bounded_scopes,
        update_agent_scopes,
    )

    grantex_cfg = dict(config.get("grantex") or {})
    grantex_agent_id = str(grantex_cfg.get("grantex_agent_id") or "")
    before = [s for s in grantex_cfg.get("grantex_scopes") or [] if isinstance(s, str)]
    if not grantex_agent_id:
        return ScopeRefresh(agent_id, "", before, before, "not_registered")

    try:
        after = bounded_scopes(_tools_to_scopes(list(authorized_tools or []), domain, connector_names=connector_names))
    except ScopeLimitExceededError as exc:
        return ScopeRefresh(agent_id, grantex_agent_id, before, before, "scope_limit_exceeded", str(exc))
    if sorted(after) == sorted(before):
        return ScopeRefresh(agent_id, grantex_agent_id, before, after, "unchanged")
    if not apply:
        return ScopeRefresh(agent_id, grantex_agent_id, before, after, "would_update")
    try:
        await asyncio.to_thread(update_agent_scopes, grantex_client, grantex_agent_id, after)
    # enterprise-gate: broad-except-ok reason=grantex-update-failure-is-reported-and-storage-is-not-changed
    except Exception as exc:
        logger.error("grantex_scope_refresh_failed", agent_id=agent_id, error_type=type(exc).__name__)
        return ScopeRefresh(agent_id, grantex_agent_id, before, after, "grantex_failed")
    try:
        await persist(after)
    # enterprise-gate: broad-except-ok reason=storage-failure-is-reported-per-agent-and-the-backfill-continues
    except Exception as exc:
        logger.error("grantex_scope_refresh_storage_failed", agent_id=agent_id, error_type=type(exc).__name__)
        return ScopeRefresh(
            agent_id, grantex_agent_id, before, after, "storage_failed", f"storage write failed: {type(exc).__name__}"
        )
    return ScopeRefresh(agent_id, grantex_agent_id, before, after, "updated")


async def _tenant_ids(all_tenants: bool, tenants: list[str]) -> list[uuid.UUID]:
    if not all_tenants:
        return [uuid.UUID(t) for t in tenants]
    from sqlalchemy import text

    from core.database import engine

    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT id FROM tenants ORDER BY id"))).fetchall()
    return [row[0] if isinstance(row[0], uuid.UUID) else uuid.UUID(str(row[0])) for row in rows]


class AgentNotUpdatedError(RuntimeError):
    """The scope write matched no row (the agent was deleted or lost its Grantex config meanwhile)."""


def scope_update_statement(tenant_id: uuid.UUID, agent_id: uuid.UUID, scopes: list[str]) -> Any:
    """``UPDATE agents`` setting only ``config.grantex.grantex_scopes`` on a live, registered agent."""
    from sqlalchemy import Text, bindparam, func, literal, true, update
    from sqlalchemy.dialects.postgresql import ARRAY, JSONB

    from core.models.agent import Agent

    return (
        update(Agent)
        .where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant_id,
            Agent.status != "deleted",
            Agent.config.has_key("grantex"),
        )
        .values(
            config=func.jsonb_set(
                Agent.config,
                literal(["grantex", "grantex_scopes"], ARRAY(Text)),
                bindparam("grantex_scopes", list(scopes), type_=JSONB),
                true(),
            )
        )
    )


async def run(args: argparse.Namespace) -> int:
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    from api.v1.agents import _resolve_connector_configs
    from auth.grantex_registration import _get_grantex_client
    from core.database import get_tenant_session
    from core.models.agent import Agent

    client = _get_grantex_client()
    if client is None:
        print("GRANTEX_API_KEY is not configured", file=sys.stderr)
        return 2

    totals: dict[str, int] = {}
    for tid in await _tenant_ids(args.all_tenants, args.tenant or []):
        async with get_tenant_session(tid) as session:
            agents = (
                (await session.execute(select(Agent).where(Agent.tenant_id == tid, Agent.status != "deleted")))
                .scalars()
                .all()
            )
            rows = [
                (
                    a.id,
                    a.domain,
                    list(a.authorized_tools or []),
                    dict(a.config or {}),
                    list(a.connector_ids or []),
                    a.company_id,
                )
                for a in agents
            ]
        for agent_id, domain, tools, config, connector_ids, company_id in rows:
            names: list[str] | None = None
            if connector_ids:
                try:
                    _, resolved = await _resolve_connector_configs(
                        tenant_id=str(tid), connector_ids=connector_ids, company_id=company_id
                    )
                except (RuntimeError, SQLAlchemyError, TypeError, ValueError) as exc:
                    grantex_cfg = config.get("grantex") or {}
                    stored = [s for s in grantex_cfg.get("grantex_scopes") or [] if isinstance(s, str)]
                    failed = ScopeRefresh(
                        str(agent_id),
                        str(grantex_cfg.get("grantex_agent_id") or ""),
                        stored,
                        stored,
                        "connector_lookup_failed",
                        f"connector bindings unreadable: {type(exc).__name__}",
                    )
                    totals[failed.outcome] = totals.get(failed.outcome, 0) + 1
                    print(failed.as_json())
                    continue
                names = resolved or None

            async def _persist(scopes: list[str], _aid: uuid.UUID = agent_id, _tid: uuid.UUID = tid) -> None:
                async with get_tenant_session(_tid) as write:
                    written = await write.execute(scope_update_statement(_tid, _aid, scopes))
                    if written.rowcount != 1:
                        raise AgentNotUpdatedError("no live registered agent row matched")

            result = await refresh_agent_scopes(
                agent_id=str(agent_id),
                domain=domain,
                authorized_tools=tools,
                config=config,
                connector_names=names,
                grantex_client=client,
                apply=args.apply,
                persist=_persist,
            )
            totals[result.outcome] = totals.get(result.outcome, 0) + 1
            print(result.as_json())
    print(json.dumps({"summary": totals, "applied": bool(args.apply)}, sort_keys=True))
    return 1 if any(totals.get(outcome) for outcome in FAILED_OUTCOMES) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--tenant", action="append", help="tenant uuid (repeatable)")
    scope.add_argument("--all-tenants", action="store_true")
    parser.add_argument("--apply", action="store_true", help="update Grantex and storage (default: report only)")
    return parser


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
