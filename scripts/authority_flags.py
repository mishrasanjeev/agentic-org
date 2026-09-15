#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Manage authority feature flags (platform operators only).

Authority flags - ``grants.enforce_closed.warn`` / ``.deny`` and the other keys
in ``core.feature_flags.RESERVED_FLAG_KEYS`` - decide what agents may do, so the
tenant feature-flag API refuses them. Operators with database access set them
here, either globally or for one tenant:

    python scripts/authority_flags.py set grants.enforce_closed.warn --tenant <tenant uuid> --operator <name>
    python scripts/authority_flags.py set grants.enforce_closed.deny --global --operator <name>
    python scripts/authority_flags.py clear grants.enforce_closed.deny --tenant <tenant uuid> --operator <name>
    python scripts/authority_flags.py list --tenant <tenant uuid>

``set`` enables the flag at 100% rollout (``--rollout`` to change); ``clear``
deletes the row and says so when there was none. ``set`` and ``clear`` require
``--operator`` and write a signed ``feature_flag.authority_changed`` audit row
(tenant rows under that tenant, global rows under the nil tenant) with the
operator, the command and the row before and after, in the same transaction as
the change. Connects through the application's tenant session
(``AGENTICORG_DB_URL``). The process flag cache expires within 30 seconds, so
running API and worker processes pick the change up without a restart.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, select

from core.feature_flags import is_reserved_flag_key
from core.models.feature_flag import FeatureFlag

logger = structlog.get_logger()

# Global rows (tenant_id NULL) are read and written under the nil tenant,
# the same context the feature-flag evaluator uses for them.
GLOBAL_CONTEXT_TENANT = uuid.UUID(int=0)
AUDIT_EVENT = "feature_flag.authority_changed"


def _row_state(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"enabled": bool(row.enabled), "rollout_percentage": int(row.rollout_percentage)}


def _audit_row(
    *,
    tenant: uuid.UUID,
    operator: str,
    command: str,
    flag_key: str,
    scope: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    outcome: str,
) -> Any:
    from core.config import settings
    from core.models.audit import AuditLog
    from core.tool_gateway.audit_logger import sign_audit_record

    entry: dict[str, Any] = {
        "tenant_id": tenant,
        "event_type": AUDIT_EVENT,
        "actor_type": "operator",
        "actor_id": operator,
        "agent_id": None,
        "workflow_run_id": None,
        "resource_type": "feature_flag",
        "resource_id": flag_key,
        "action": command,
        "outcome": outcome,
        "details": {"flag_key": flag_key, "scope": scope, "before": before, "after": after},
        "trace_id": "",
        "created_at": datetime.now(UTC),
    }
    entry["signature"] = sign_audit_record(entry, settings.secret_key.encode())
    return AuditLog(**entry)


async def run(args: argparse.Namespace) -> int:
    from core.database import get_tenant_session

    tenant_id = uuid.UUID(args.tenant) if args.tenant else None
    context_tenant = tenant_id or GLOBAL_CONTEXT_TENANT
    scope_clause = FeatureFlag.tenant_id == tenant_id if tenant_id else FeatureFlag.tenant_id.is_(None)
    scope_label = str(tenant_id) if tenant_id else "global"

    if args.command != "list":
        if not is_reserved_flag_key(args.flag_key):
            print(f"refusing: {args.flag_key!r} is not an authority flag", file=sys.stderr)
            return 2
        if not (args.operator or "").strip():
            print("refusing: --operator is required to change an authority flag", file=sys.stderr)
            return 2

    async with get_tenant_session(context_tenant) as session:
        if args.command == "list":
            rows = (await session.execute(select(FeatureFlag).where(scope_clause))).scalars().all()
            for row in sorted(rows, key=lambda r: r.flag_key):
                if is_reserved_flag_key(row.flag_key):
                    print(f"{scope_label} {row.flag_key} enabled={row.enabled} rollout={row.rollout_percentage}")
            return 0

        existing = (
            await session.execute(select(FeatureFlag).where(scope_clause, FeatureFlag.flag_key == args.flag_key))
        ).scalar_one_or_none()
        before = _row_state(existing)
        outcome = "applied"
        after: dict[str, Any] | None = None
        if args.command == "set":
            if existing is None:
                session.add(
                    FeatureFlag(
                        tenant_id=tenant_id,
                        flag_key=args.flag_key,
                        enabled=True,
                        rollout_percentage=args.rollout,
                        description=args.description,
                    )
                )
            else:
                existing.enabled = True
                existing.rollout_percentage = args.rollout
                if args.description:
                    existing.description = args.description
            after = {"enabled": True, "rollout_percentage": int(args.rollout)}
        elif existing is None:
            outcome = "not_found"
        else:
            await session.execute(delete(FeatureFlag).where(scope_clause, FeatureFlag.flag_key == args.flag_key))
        session.add(
            _audit_row(
                tenant=context_tenant,
                operator=args.operator.strip(),
                command=args.command,
                flag_key=args.flag_key,
                scope=scope_label,
                before=before,
                after=after,
                outcome=outcome,
            )
        )

    logger.info(
        "authority_flag_changed",
        command=args.command,
        flag_key=args.flag_key,
        scope=scope_label,
        operator=args.operator,
        outcome=outcome,
    )
    if outcome == "not_found":
        print(f"clear {args.flag_key} ({scope_label}): no row existed, nothing changed")
    else:
        print(f"{args.command} {args.flag_key} ({scope_label})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("set", "clear", "list"):
        cmd = sub.add_parser(name)
        if name != "list":
            cmd.add_argument("flag_key")
            cmd.add_argument("--operator", required=True, help="who is making the change (recorded in the audit log)")
        scope = cmd.add_mutually_exclusive_group(required=True)
        scope.add_argument("--tenant", help="tenant uuid")
        scope.add_argument("--global", dest="global_scope", action="store_true", help="the global row")
        if name == "set":
            cmd.add_argument("--rollout", type=int, default=100, choices=range(0, 101), metavar="0-100")
            cmd.add_argument("--description", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
