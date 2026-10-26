# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The partner's report: the funnel, time to submission and decision, consent coverage, checks per merchant.

Everything is counted over leads created in the period, so each figure follows
the same cohort from first contact to decision. ``summarise`` is pure: it takes
the rows and the period and computes the report, which is what the tests
exercise; ``summary`` reads the rows for a tenant.

- Funnel: leads by status, applications by status, cases opened, cases by
  state, and decisions (approve and decline). Approvals and declines are only
  ever made by a person; the desk records what they decided.
- Time: the median and 90th percentile hours from lead to submitted
  application, and from submitted application to decision.
- Consent coverage: the share of leads with a current consent on any channel,
  and on each channel.
- Checks per merchant: provider calls the case agents made, per case with a
  completed run (mean and maximum), and screening results per case.
"""

from __future__ import annotations

import math
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from core.onboarding_desk import store
from core.onboarding_desk.store import DeskError

MAX_PERIOD_DAYS = 366
MAX_ROWS = 20_000
CONSENT_CHANNELS = ("sms", "email", "whatsapp", "voice")


@dataclass(frozen=True)
class Period:
    since: datetime
    until: datetime

    def to_dict(self) -> dict[str, str]:
        return {"since": self.since.isoformat(), "until": self.until.isoformat()}


def parse_time(value: str | None) -> datetime | None:
    """An ISO 8601 query time, or None when not given; ``period_invalid`` when it does not parse."""
    if value in (None, ""):
        return None
    parsed = _parse(str(value)[:40])
    if parsed is None:
        raise DeskError(422, "period_invalid", "since and until are ISO 8601 times with a zone")
    return parsed


def period_from(since: datetime | None, until: datetime | None, *, now: datetime | None = None) -> Period:
    end = until or now or datetime.now(UTC)
    start = since or end - timedelta(days=30)
    if start.tzinfo is None or end.tzinfo is None:
        raise DeskError(422, "period_invalid", "since and until need a time zone")
    if start >= end or end - start > timedelta(days=MAX_PERIOD_DAYS):
        raise DeskError(422, "period_invalid", f"since must be before until, at most {MAX_PERIOD_DAYS} days apart")
    return Period(start, end)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return round(ordered[index], 1)


def _hours(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None or end < start:
        return None
    return (end - start).total_seconds() / 3600


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _share(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def summarise(
    period: Period,
    leads: Iterable[Any],
    applications: Iterable[Any],
    consents: Iterable[Any],
    cases: Iterable[Any],
) -> dict[str, Any]:
    lead_rows = list(leads)
    lead_ids = {lead.id for lead in lead_rows}
    lead_created = {lead.id: lead.created_at for lead in lead_rows}
    app_rows = [a for a in applications if a.lead_id in lead_ids]
    case_rows = {c.case_ref: c for c in cases}
    linked = [case_rows[a.case_ref] for a in app_rows if a.case_ref and a.case_ref in case_rows]

    decisions = Counter((c.decision or {}).get("outcome") for c in linked if c.decision)
    to_submission = [h for a in app_rows if (h := _hours(lead_created.get(a.lead_id), a.submitted_at)) is not None]
    by_ref = {a.case_ref: a for a in app_rows if a.case_ref}
    to_decision = [
        h
        for c in linked
        if c.decision
        and (h := _hours(by_ref[c.case_ref].submitted_at, _parse((c.decision or {}).get("decided_at")))) is not None
    ]

    now_consent: dict[str, set[Any]] = {channel: set() for channel in CONSENT_CHANNELS}
    for consent in consents:
        if consent.lead_id not in lead_ids or consent.status != "granted":
            continue
        if consent.expires_at is not None and consent.expires_at <= period.until:
            continue
        if consent.channel in now_consent:
            now_consent[consent.channel].add(consent.lead_id)
    any_consent = set().union(*now_consent.values())

    run_cases = [c for c in linked if c.agent_records]
    calls = [sum(len((r or {}).get("tool_calls") or []) for r in c.agent_records) for c in run_cases]
    screenings = [len(c.screening_results or []) for c in run_cases]

    return {
        "period": period.to_dict(),
        "funnel": {
            "leads": len(lead_rows),
            "leads_by_status": dict(sorted(Counter(lead.status for lead in lead_rows).items())),
            "applications": len(app_rows),
            "applications_by_status": dict(sorted(Counter(a.status for a in app_rows).items())),
            "cases_opened": sum(1 for a in app_rows if a.case_ref),
            "cases_by_state": dict(sorted(Counter(c.state for c in linked).items())),
            "approved": decisions.get("approve", 0),
            "declined": decisions.get("decline", 0),
        },
        "hours_to_submission": {
            "count": len(to_submission),
            "median": _percentile(to_submission, 0.5),
            "p90": _percentile(to_submission, 0.9),
        },
        "hours_to_decision": {
            "count": len(to_decision),
            "median": _percentile(to_decision, 0.5),
            "p90": _percentile(to_decision, 0.9),
        },
        "consent_coverage": {
            "any_channel": _share(len(any_consent), len(lead_rows)),
            **{channel: _share(len(ids), len(lead_rows)) for channel, ids in now_consent.items()},
        },
        "checks_per_merchant": {
            "cases_with_checks": len(run_cases),
            "provider_calls_mean": round(sum(calls) / len(calls), 2) if calls else None,
            "provider_calls_max": max(calls) if calls else None,
            "screening_results_mean": round(sum(screenings) / len(screenings), 2) if screenings else None,
        },
        "truncated": len(lead_rows) >= MAX_ROWS,
    }


async def summary(tenant_id: uuid.UUID, period: Period) -> dict[str, Any]:
    from sqlalchemy import select

    from core.models.governed_case import GovernedCase
    from core.models.onboarding_desk import OnboardingApplication, OnboardingConsent, OnboardingLead

    async with store._session(tenant_id) as session:
        leads = list(
            (
                await session.execute(
                    select(OnboardingLead)
                    .where(
                        OnboardingLead.tenant_id == tenant_id,
                        OnboardingLead.created_at >= period.since,
                        OnboardingLead.created_at < period.until,
                    )
                    .order_by(OnboardingLead.created_at)
                    .limit(MAX_ROWS)
                )
            ).scalars()
        )
        ids = [lead.id for lead in leads]
        apps = (
            list(
                (
                    await session.execute(
                        select(OnboardingApplication).where(
                            OnboardingApplication.tenant_id == tenant_id, OnboardingApplication.lead_id.in_(ids)
                        )
                    )
                ).scalars()
            )
            if ids
            else []
        )
        consents = (
            list(
                (
                    await session.execute(
                        select(OnboardingConsent).where(
                            OnboardingConsent.tenant_id == tenant_id, OnboardingConsent.lead_id.in_(ids)
                        )
                    )
                ).scalars()
            )
            if ids
            else []
        )
        refs = [a.case_ref for a in apps if a.case_ref]
        cases = (
            list(
                (
                    await session.execute(
                        select(GovernedCase).where(GovernedCase.tenant_id == tenant_id, GovernedCase.case_ref.in_(refs))
                    )
                ).scalars()
            )
            if refs
            else []
        )
    return summarise(period, leads, apps, consents, cases)
