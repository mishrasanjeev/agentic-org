# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Whether yesterday's daily rollup is in, for the late-data banner on the landing views.

Every closed local day gets a rollup state row from 02:00 local (empty days
too; see ``core.tasks.insights_tasks.closed_days_to_roll``). From
``LATE_AFTER`` local, a missing row for yesterday, one never rolled or one
rolled before the day closed means the daily figures are late.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

# The rollup sweep runs every 15 minutes from 02:00 local; an hour of grace before the banner shows.
LATE_AFTER = time(3, 0)


async def rollup_late(session: object, tenant_id: uuid.UUID, zone: ZoneInfo, now: datetime) -> bool:
    from sqlalchemy import select

    from core.insights.metrics import closed_at
    from core.models.insights import InsightsRollupState as S

    local_now = now.astimezone(zone)
    if local_now.time() < LATE_AFTER:
        return False
    yesterday = local_now.date() - timedelta(days=1)
    rolled_at = (
        await session.execute(  # type: ignore[attr-defined]
            select(S.rolled_at).where(S.tenant_id == tenant_id, S.day == yesterday)
        )
    ).scalar_one_or_none()
    return rolled_at is None or rolled_at < closed_at(yesterday, zone)
