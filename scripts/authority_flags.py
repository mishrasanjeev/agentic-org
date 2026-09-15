#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Manage authority feature flags (platform operators only).

Authority flags - ``grants.enforce_closed.warn`` / ``.deny`` and the other keys
in ``core.feature_flags.RESERVED_FLAG_KEYS`` - decide what agents may do, so the
tenant feature-flag API refuses them. Operators with database access set them
here, either globally or for one tenant:

    python scripts/authority_flags.py set grants.enforce_closed.warn --tenant <tenant uuid>
    python scripts/authority_flags.py set grants.enforce_closed.deny --global
    python scripts/authority_flags.py clear grants.enforce_closed.deny --tenant <tenant uuid>
    python scripts/authority_flags.py list --tenant <tenant uuid>

``set`` enables the flag at 100% rollout (``--rollout`` to change); ``clear``
deletes the row. Connects with ``AGENTICORG_DB_URL``. The process flag cache
expires within 30 seconds, so running API and worker processes pick the change
up without a restart. Every change is logged.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.feature_flags import is_reserved_flag_key
from core.models.feature_flag import FeatureFlag

logger = structlog.get_logger()


def _scope(tenant: str | None) -> uuid.UUID | None:
    return uuid.UUID(tenant) if tenant else None


async def run(args: argparse.Namespace, db_url: str) -> int:
    tenant_id = _scope(args.tenant)
    engine = create_async_engine(db_url)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    scope_clause = FeatureFlag.tenant_id == tenant_id if tenant_id else FeatureFlag.tenant_id.is_(None)
    scope_label = str(tenant_id) if tenant_id else "global"
    try:
        async with sessions() as session, session.begin():
            if args.command == "list":
                rows = (await session.execute(select(FeatureFlag).where(scope_clause))).scalars().all()
                for row in sorted(rows, key=lambda r: r.flag_key):
                    if is_reserved_flag_key(row.flag_key):
                        print(f"{scope_label} {row.flag_key} enabled={row.enabled} rollout={row.rollout_percentage}")
                return 0
            if not is_reserved_flag_key(args.flag_key):
                print(f"refusing: {args.flag_key!r} is not an authority flag", file=sys.stderr)
                return 2
            existing = (
                await session.execute(select(FeatureFlag).where(scope_clause, FeatureFlag.flag_key == args.flag_key))
            ).scalar_one_or_none()
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
            else:
                await session.execute(delete(FeatureFlag).where(scope_clause, FeatureFlag.flag_key == args.flag_key))
        logger.info(
            "authority_flag_changed",
            command=args.command,
            flag_key=args.flag_key,
            scope=scope_label,
            rollout=getattr(args, "rollout", None),
        )
        print(f"{args.command} {args.flag_key} ({scope_label})")
        return 0
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("set", "clear", "list"):
        cmd = sub.add_parser(name)
        if name != "list":
            cmd.add_argument("flag_key")
        scope = cmd.add_mutually_exclusive_group(required=True)
        scope.add_argument("--tenant", help="tenant uuid")
        scope.add_argument("--global", dest="global_scope", action="store_true", help="the global row")
        if name == "set":
            cmd.add_argument("--rollout", type=int, default=100, choices=range(0, 101), metavar="0-100")
            cmd.add_argument("--description", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from core.config import settings

    return asyncio.run(run(args, settings.db_url))


if __name__ == "__main__":
    raise SystemExit(main())
