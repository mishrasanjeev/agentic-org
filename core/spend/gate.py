# SPDX-License-Identifier: Apache-2.0
"""The Gate 1 status of a month: attributed spend and provider reconciliation, both strict.

Gate 1, the exit criterion of Phase 1, is met for a month when both hold:

* **Attribution** (the reporting month, ``spend_reporting_timezone``): the
  INR amount attributed to a business unit, department, team or cost centre
  is at least 98% of the month's INR amount (attribution to a ``group`` node
  is reported as ``group_share`` and does not count, so a catch-all mapping
  to the root cannot meet it); no record is unpriced (``UNPRICED_MAX``), none
  is unconverted and no FX conversion is pending, because those records have
  no INR amount and would otherwise sit outside both sides of the share. With
  no priced spend there is no share and the measure is not met.
* **Reconciliation** (each provider's own billing month): every provider
  with a current invoice for the month, and every provider with tenant-billed
  priced usage in it, has a reconciliation that is current (not superseded),
  not stale and ``within_tolerance``. In-house serving, platform storage and
  providers with only GPU or storage usage have no provider invoice and are
  left out. A provider invoiced with nothing metered is listed (a broken
  hook, a name mismatch, usage on a key the tenant did not pay for), and so is
  a provider metered with no invoice. An accepted run is reported as an
  accepted exception and does not meet the gate.

Displayed shares are rounded toward zero and variances away from it; every
verdict uses the exact values, so a displayed figure never reads as meeting a
threshold the exact value misses.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Context, Decimal, localcontext
from typing import Any

from sqlalchemy import or_, select

from core.spend import clock, invoices, reconcile, rollups, vocab

ATTRIBUTED_TARGET = Decimal("0.98")
UNPRICED_MAX = 0  # records; owner decision O4
NO_PROVIDER_INVOICE_USAGE = frozenset({"gpu_hours", "storage"})
_ZERO = Decimal("0")


def _ratio(part: Decimal, whole: Decimal) -> Decimal | None:
    if whole == 0:
        return None
    with localcontext(Context(prec=38)):
        return part / whole


def attribution_of(period: Mapping[str, Any]) -> dict[str, Any]:
    """The exact attribution figures of a coverage period (``rollups.coverage``'s ``period``)."""
    amount = Decimal(period["amount_inr"]) if period.get("amount_inr") is not None else _ZERO
    countable = Decimal(period.get("countable_amount_inr") or "0")
    records = int(period.get("records") or 0)
    return {
        "share": _ratio(countable, amount),
        "count_share": _ratio(Decimal(int(period.get("countable_records") or 0)), Decimal(records)),
        "unpriced_count": int(period.get("unpriced_count") or 0),
        "unconverted_count": int(period.get("unconverted_count") or 0),
        "fx_pending_count": int(period.get("fx_pending_count") or 0),
    }


def verdict(
    attribution: Mapping[str, Any],
    providers: Sequence[Mapping[str, Any]],
    missing: Sequence[str],
    stale: Sequence[str],
) -> dict[str, Any]:
    """Both measures and the gate from exact figures (pure)."""
    reasons: list[str] = []
    share = attribution.get("share")
    if share is None:
        reasons.append("no_priced_usage")
    elif share < ATTRIBUTED_TARGET:
        reasons.append("below_target")
    if int(attribution.get("unpriced_count") or 0) > UNPRICED_MAX:
        reasons.append("unpriced_usage")
    if int(attribution.get("unconverted_count") or 0):
        reasons.append("unconverted_usage")
    if int(attribution.get("fx_pending_count") or 0):
        reasons.append("fx_pending")
    attribution_met = not reasons
    accepted = sorted(str(p["provider"]) for p in providers if p.get("status") == "accepted")
    reconciled = all(
        p.get("reconciliation_id") is not None and not p.get("stale") and p.get("status") == "within_tolerance"
        for p in providers
    )
    reconciliation_met = reconciled and not missing and not stale
    return {
        "attribution_met": attribution_met,
        "attribution_reasons": reasons,
        "reconciliation_met": reconciliation_met,
        "accepted_exceptions": accepted,
        "gate_met": attribution_met and reconciliation_met,
    }


def providers_to_reconcile(invoiced: Iterable[str], metered: Iterable[tuple[str, str]]) -> list[tuple[str, bool, bool]]:
    """``(provider, invoiced, metered)`` for every provider the month must reconcile (pure).

    ``metered`` holds ``(provider, usage_type)`` of tenant-billed priced usage.
    """
    excluded = set(vocab.IN_HOUSE_PROVIDERS) | {vocab.STORAGE_PROVIDER}
    usage: dict[str, set[str]] = {}
    for provider, usage_type in metered:
        usage.setdefault(provider, set()).add(usage_type)
    metered_set = {p for p, types in usage.items() if p not in excluded and not types <= NO_PROVIDER_INVOICE_USAGE}
    invoiced_set = {p for p in invoiced if p not in excluded}
    return [(p, p in invoiced_set, p in metered_set) for p in sorted(invoiced_set | metered_set)]


def _provider_entry(provider: str, invoiced: bool, metered: bool, run: Any, stale: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "provider": provider,
        "billing_timezone": clock.billing_zone(provider).key,
        "invoiced": invoiced,
        "metered": metered,
        "reconciliation_id": None,
        "currency": None,
        "invoice_amount": None,
        "stored_amount": None,
        "repriced_amount": None,
        "stored_variance_pct": None,
        "repriced_variance_pct": None,
        "status": None,
        "stale": False,
        "retroactive": [],
        "platform_billed_amount_inr": None,
        "unknown_account_records": 0,
    }
    if run is None:
        return entry
    entry.update(
        reconciliation_id=str(run.id),
        currency=str(run.currency).strip(),
        invoice_amount=vocab.dec_str(run.invoice_amount),
        stored_amount=vocab.dec_str(run.stored_amount),
        repriced_amount=vocab.dec_str(run.repriced_amount),
        stored_variance_pct=vocab.dec_str(run.stored_variance_pct),
        repriced_variance_pct=vocab.dec_str(run.repriced_variance_pct),
        status=run.status,
        stale=stale,
        retroactive=list(run.retroactive or []),
        platform_billed_amount_inr=vocab.dec_str(run.platform_billed_amount_inr),
        unknown_account_records=int(run.unknown_account_records or 0),
    )
    return entry


async def status(tenant_id: uuid.UUID, period: str, *, now: datetime) -> dict[str, Any]:
    """The Gate 1 status of ``period`` (``YYYY-MM``)."""
    from core.database import get_tenant_session
    from core.models.spend_invoice import SpendInvoice
    from core.models.spend_usage import SpendUsageRollup as U
    from core.spend import ledgers

    p0, p1 = invoices.parse_period(period)
    coverage = await rollups.coverage(tenant_id, start=p0, end=p1 - timedelta(days=1), now=now)
    tally = coverage["period"]
    exact = attribution_of(tally)
    entries: list[dict[str, Any]] = []
    missing: list[str] = []
    stale: list[str] = []
    async with get_tenant_session(tenant_id) as session:
        invoiced = (
            await session.execute(
                select(SpendInvoice.provider)
                .where(
                    SpendInvoice.tenant_id == tenant_id,
                    SpendInvoice.period_start == p0,
                    SpendInvoice.status == "current",
                )
                .group_by(SpendInvoice.provider)
            )
        ).all()
        metered = (
            await session.execute(
                select(U.provider, U.usage_type)
                .where(
                    U.tenant_id == tenant_id,
                    U.billing_date >= p0,
                    U.billing_date < p1,
                    or_(U.billing_account.is_(None), U.billing_account == reconcile.TENANT_BILLED),
                    U.amount > 0,
                )
                .group_by(U.provider, U.usage_type)
            )
        ).all()
        for provider, was_invoiced, was_metered in providers_to_reconcile(
            (row[0] for row in invoiced), ((row[0], row[1]) for row in metered)
        ):
            run = await reconcile.latest_run(session, tenant_id, provider, p0)
            is_stale = False
            if run is None:
                missing.append(provider)
            else:
                is_stale = await reconcile.is_stale(session, tenant_id, run)
                if is_stale:
                    stale.append(provider)
            entries.append(_provider_entry(provider, was_invoiced, was_metered, run, is_stale))
    outcome = verdict(exact, entries, missing, stale)
    return {
        "period": invoices.period_text(p0),
        "reporting_timezone": coverage["reporting_timezone"],
        "computed_at": now.isoformat(),
        "attribution": {
            "attributed_share": tally["attributed_share"],
            "group_share": tally["group_share"],
            "attributed_count_share": rollups.share_text(exact["count_share"]),
            "records": tally["records"],
            "amount_inr": tally["amount_inr"],
            "attributed_amount_inr": tally["countable_amount_inr"],
            "by_path": coverage["by_path"],
            "by_reason": coverage["by_reason"],
            "unpriced_count": tally["unpriced_count"],
            "unpriced_quantity": tally["unpriced_quantity"],
            "unconverted_count": tally["unconverted_count"],
            "unconverted_amount": tally["unconverted_amount"],
            "fx_pending_count": tally["fx_pending_count"],
            "gaps": tally["gaps"],
            "target": format(ATTRIBUTED_TARGET, "f"),
            "met": outcome["attribution_met"],
            "reasons": outcome["attribution_reasons"],
        },
        "reconciliation": {
            "providers": entries,
            "missing_reconciliations": missing,
            "stale_reconciliations": stale,
            "accepted_exceptions": outcome["accepted_exceptions"],
            "tolerance_pct": format(reconcile.TOLERANCE_PCT, "f"),
            "met": outcome["reconciliation_met"],
        },
        "backfill_source": ledgers.backfill_source(),
        "gate_met": outcome["gate_met"],
    }
