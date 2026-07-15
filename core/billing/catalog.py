"""Typed, versioned source of truth for public billing plan facts.

The catalog describes an offer.  It intentionally does not claim that runtime
usage enforcement is wired end to end; that is a separate readiness gate.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

PUBLIC_PLAN_CATALOG_SCHEMA_VERSION = "agenticorg.billing-plans.v1"

CurrencyCode = Literal["USD", "INR"]
BillingInterval = Literal["month"]
CheckoutMode = Literal["none", "hosted"]


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanPrice(CatalogModel):
    currency: CurrencyCode
    amount_minor: int = Field(ge=0)
    interval: BillingInterval


class PlanLimits(CatalogModel):
    """Offer limits; ``None`` means the catalog does not set a finite cap."""

    agent_count: int | None = Field(default=None, ge=1)
    agent_runs: int | None = Field(default=None, ge=1)
    agent_runs_interval: BillingInterval
    storage_bytes: int | None = Field(default=None, ge=1)


class PublicPlan(CatalogModel):
    plan_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    display_name: str = Field(min_length=1)
    display_order: int = Field(ge=0)
    prices: tuple[PlanPrice, ...] = Field(min_length=1)
    limits: PlanLimits
    signup_available: bool
    checkout_mode: CheckoutMode

    @model_validator(mode="after")
    def unique_prices(self) -> Self:
        currencies = [price.currency for price in self.prices]
        if len(currencies) != len(set(currencies)):
            raise ValueError("a plan cannot repeat a price currency")
        if self.checkout_mode == "none" and any(price.amount_minor > 0 for price in self.prices):
            raise ValueError("a paid plan requires a checkout mode")
        return self


class PublicPlanCatalog(CatalogModel):
    schema_version: Literal["agenticorg.billing-plans.v1"] = PUBLIC_PLAN_CATALOG_SCHEMA_VERSION
    catalog_version: str = Field(min_length=1)
    complete: Literal[True] = True
    plan_count: int = Field(gt=0)
    plans: tuple[PublicPlan, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def complete_and_unique(self) -> Self:
        if self.plan_count != len(self.plans):
            raise ValueError("plan_count must match the complete plan list")
        ids = [plan.plan_id for plan in self.plans]
        if len(ids) != len(set(ids)):
            raise ValueError("plan ids must be unique")
        orders = [plan.display_order for plan in self.plans]
        if len(orders) != len(set(orders)):
            raise ValueError("plan display_order values must be unique")
        return self


PUBLIC_PLAN_CATALOG = PublicPlanCatalog(
    catalog_version="2026-07-15.1",
    plan_count=3,
    plans=(
        PublicPlan(
            plan_id="free",
            display_name="Free",
            display_order=10,
            prices=(
                PlanPrice(currency="USD", amount_minor=0, interval="month"),
                PlanPrice(currency="INR", amount_minor=0, interval="month"),
            ),
            limits=PlanLimits(
                agent_count=3,
                agent_runs=1_000,
                agent_runs_interval="month",
                storage_bytes=1 * 1024 * 1024 * 1024,
            ),
            signup_available=True,
            checkout_mode="none",
        ),
        PublicPlan(
            plan_id="pro",
            display_name="Pro",
            display_order=20,
            prices=(
                PlanPrice(currency="USD", amount_minor=2_00, interval="month"),
                PlanPrice(currency="INR", amount_minor=9_999_00, interval="month"),
            ),
            limits=PlanLimits(
                agent_count=15,
                agent_runs=10_000,
                agent_runs_interval="month",
                storage_bytes=50 * 1024 * 1024 * 1024,
            ),
            signup_available=False,
            checkout_mode="hosted",
        ),
        PublicPlan(
            plan_id="enterprise",
            display_name="Enterprise",
            display_order=30,
            prices=(
                PlanPrice(currency="USD", amount_minor=499_00, interval="month"),
                PlanPrice(currency="INR", amount_minor=49_999_00, interval="month"),
            ),
            limits=PlanLimits(
                agent_count=None,
                agent_runs=None,
                agent_runs_interval="month",
                storage_bytes=None,
            ),
            signup_available=False,
            checkout_mode="hosted",
        ),
    ),
)


def plan_by_id(plan_id: str) -> PublicPlan:
    for plan in PUBLIC_PLAN_CATALOG.plans:
        if plan.plan_id == plan_id:
            return plan
    raise KeyError(plan_id)


def plan_price_minor(plan_id: str, currency: CurrencyCode) -> int:
    plan = plan_by_id(plan_id)
    for price in plan.prices:
        if price.currency == currency:
            return price.amount_minor
    raise KeyError(f"{plan_id}:{currency}")
