#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Re-register agents' Grantex scopes with the permission levels enforcement understands.

Agents registered before this fix carry ``tool:{connector}:execute:{tool}``
scopes. ``grantex.enforce`` reads the third segment as a permission level
(``read < write < delete < admin``), so ``execute`` grants nothing and a grant
delegated for such an agent denies every call. This script recomputes each
registered agent's scopes from its authorized tools (the same mapping new
registrations and ``PATCH /agents/{id}`` now use), updates the agent on Grantex
and stores the new scopes in ``config.grantex.grantex_scopes``.

    python scripts/refresh_grantex_scopes.py --tenant <tenant uuid>            # report only
    python scripts/refresh_grantex_scopes.py --tenant <tenant uuid> --apply
    python scripts/refresh_grantex_scopes.py --all-tenants --apply

Grantex is updated before the database, so a failed database write leaves the
agent with its new scopes on Grantex and the old ones stored; re-running is
idempotent. Output is one JSON line per agent (ids, counts, outcome; no
tokens or keys) and a summary.
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
    outcome: str  # unchanged | would_update | updated | grantex_failed | not_registered

    def as_json(self) -> str:
        return json.dumps(
            {
                "agent_id": self.agent_id,
                "grantex_agent_id": self.grantex_agent_id,
                "outcome": self.outcome,
                "scopes_before": len(self.before),
                "scopes_after": len(self.after),
                "execute_scopes_before": sum(1 for s in self.before if ":execute:" in s),
            },
            sort_keys=True,
        )


async def refresh_agent_scopes(
    *,
    agent_id: str,
    domain: str,
    authorized_tools: list[str],
    config: dict[str, Any],
    connector_names: list[str] | None,
    grantex_client: Any,
    apply: bool,
    persist: Callable[[dict[str, Any]], Awaitable[None]],
) -> ScopeRefresh:
    """Recompute one agent's scopes and, with ``apply``, push them to Grantex then storage."""
    from auth.grantex_registration import _tools_to_scopes

    grantex_cfg = dict(config.get("grantex") or {})
    grantex_agent_id = str(grantex_cfg.get("grantex_agent_id") or "")
    before = [s for s in grantex_cfg.get("grantex_scopes") or [] if isinstance(s, str)]
    if not grantex_agent_id:
        return ScopeRefresh(agent_id, "", before, before, "not_registered")

    after = _tools_to_scopes(list(authorized_tools or []), domain, connector_names=connector_names)
    if sorted(after) == sorted(before):
        return ScopeRefresh(agent_id, grantex_agent_id, before, after, "unchanged")
    if not apply:
        return ScopeRefresh(agent_id, grantex_agent_id, before, after, "would_update")
    try:
        await asyncio.to_thread(grantex_client.agents.update, grantex_agent_id, scopes=after)
    # enterprise-gate: broad-except-ok reason=grantex-update-failure-is-reported-and-storage-is-not-changed
    except Exception as exc:
        logger.error("grantex_scope_refresh_failed", agent_id=agent_id, error_type=type(exc).__name__)
        return ScopeRefresh(agent_id, grantex_agent_id, before, after, "grantex_failed")
    await persist({**config, "grantex": {**grantex_cfg, "grantex_scopes": after}})
    return ScopeRefresh(agent_id, grantex_agent_id, before, after, "updated")


async def _tenant_ids(all_tenants: bool, tenants: list[str]) -> list[uuid.UUID]:
    if not all_tenants:
        return [uuid.UUID(t) for t in tenants]
    from sqlalchemy import text

    from core.database import engine

    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT id FROM tenants ORDER BY id"))).fetchall()
    return [row[0] if isinstance(row[0], uuid.UUID) else uuid.UUID(str(row[0])) for row in rows]


async def run(args: argparse.Namespace) -> int:
    from sqlalchemy import select, update

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
            agents = (await session.execute(select(Agent).where(Agent.tenant_id == tid))).scalars().all()
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
                _, resolved = await _resolve_connector_configs(
                    tenant_id=str(tid), connector_ids=connector_ids, company_id=company_id
                )
                names = resolved or None

            async def _persist(
                new_config: dict[str, Any], _aid: uuid.UUID = agent_id, _tid: uuid.UUID = tid
            ) -> None:
                async with get_tenant_session(_tid) as write:
                    await write.execute(
                        update(Agent).where(Agent.id == _aid, Agent.tenant_id == _tid).values(config=new_config)
                    )

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
    return 1 if totals.get("grantex_failed") else 0


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
