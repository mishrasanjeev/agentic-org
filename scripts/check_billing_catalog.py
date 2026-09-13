"""Fail closed when billing offer, checkout, invoice, and runtime maps drift."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.billing.catalog import PUBLIC_PLAN_CATALOG, plan_price_minor  # noqa: E402
from core.billing.invoice_generator import PLAN_MONTHLY_FEE  # noqa: E402
from core.billing.limits import TIERS  # noqa: E402
from core.billing.pinelabs_client import PLAN_AMOUNT_INR  # noqa: E402
from core.billing.stripe_client import PLAN_AMOUNT_USD  # noqa: E402


# Plausible INR-per-USD band for list prices. The Pro plan shipped for months
# at USD 2.00 vs INR 9,999 (a leftover live-checkout test price from PR #613);
# a ~5000:1 ratio must fail this gate.
FX_BAND_INR_PER_USD = (Decimal("60"), Decimal("140"))


def fx_band_issues() -> list[str]:
    issues: list[str] = []
    low, high = FX_BAND_INR_PER_USD
    for plan in PUBLIC_PLAN_CATALOG.plans:
        usd = plan_price_minor(plan.plan_id, "USD")
        inr = plan_price_minor(plan.plan_id, "INR")
        if usd == 0 and inr == 0:
            continue
        if usd == 0 or inr == 0:
            issues.append(f"{plan.plan_id}: one currency is free while the other is paid")
            continue
        ratio = Decimal(inr) / Decimal(usd)
        if not (low <= ratio <= high):
            issues.append(
                f"{plan.plan_id}: INR/USD list-price ratio {ratio:.1f} outside "
                f"plausible FX band {low}-{high} (USD {usd / 100:.2f} vs INR {inr / 100:,.2f})"
            )
    return issues


def catalog_consistency_issues() -> list[str]:
    issues: list[str] = fx_band_issues()
    for plan in PUBLIC_PLAN_CATALOG.plans:
        expected_limits = {
            "agent_count": -1 if plan.limits.agent_count is None else plan.limits.agent_count,
            "agent_runs": -1 if plan.limits.agent_runs is None else plan.limits.agent_runs,
            "storage_bytes": -1 if plan.limits.storage_bytes is None else plan.limits.storage_bytes,
        }
        if TIERS.get(plan.plan_id) != expected_limits:
            issues.append(f"{plan.plan_id}: runtime TIERS projection drift")

        usd_minor = plan_price_minor(plan.plan_id, "USD")
        invoice_amount = PLAN_MONTHLY_FEE.get(plan.plan_id)
        if invoice_amount != Decimal(usd_minor) / Decimal(100):
            issues.append(f"{plan.plan_id}: invoice USD amount drift")

        if plan.checkout_mode == "hosted":
            if PLAN_AMOUNT_USD.get(plan.plan_id) != usd_minor:
                issues.append(f"{plan.plan_id}: Stripe fallback USD amount drift")
            if PLAN_AMOUNT_INR.get(plan.plan_id) != plan_price_minor(plan.plan_id, "INR"):
                issues.append(f"{plan.plan_id}: PineLabs INR amount drift")
    return issues


def main() -> int:
    issues = catalog_consistency_issues()
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}")
        return 1
    print(
        f"Billing catalog {PUBLIC_PLAN_CATALOG.catalog_version}: "
        f"PASS ({PUBLIC_PLAN_CATALOG.plan_count} plans)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
