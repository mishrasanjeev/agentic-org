# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""A tenant's reporting settings: the time zone its days are counted in, its fiscal year and its currency.

Stored in ``tenants.settings["reporting"]`` and changed through
``PUT /config/reporting``. Until a tenant sets them they follow its data
region: an ``IN`` tenant reports in ``Asia/Kolkata``, with an April fiscal
year, in INR; every other tenant in UTC, with a January fiscal year, in USD.
A stored value that is no longer valid (an unknown zone, say) falls back to
the region default for that field, so a report never fails on it.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = structlog.get_logger()

REPORTING_SETTINGS_KEY = "reporting"
Currency = Literal["INR", "USD"]
CURRENCIES: tuple[str, ...] = ("INR", "USD")
MAX_TIMEZONE_LENGTH = 64


def _valid_zone(value: str) -> bool:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False
    return True


class ReportingSettings(BaseModel):
    """How a tenant's reports count days and money."""

    model_config = {"extra": "forbid"}

    timezone: str = Field(..., min_length=1, max_length=MAX_TIMEZONE_LENGTH)
    fiscal_year_start_month: int = Field(..., ge=1, le=12)
    currency: Currency

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        value = value.strip()
        if not _valid_zone(value):
            raise ValueError("timezone must be an IANA time zone name such as Asia/Kolkata or UTC")
        return value

    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def defaults_for_region(data_region: str | None) -> ReportingSettings:
    """The reporting settings a tenant in ``data_region`` starts with."""
    if (data_region or "").strip().upper() == "IN":
        return ReportingSettings(timezone="Asia/Kolkata", fiscal_year_start_month=4, currency="INR")
    return ReportingSettings(timezone="UTC", fiscal_year_start_month=1, currency="USD")


def settings_from_tenant(tenant_settings: dict[str, Any] | None, data_region: str | None) -> ReportingSettings:
    """The stored reporting settings over the region defaults, field by field; invalid stored fields are ignored."""
    defaults = defaults_for_region(data_region)
    stored = (tenant_settings or {}).get(REPORTING_SETTINGS_KEY)
    if not isinstance(stored, dict):
        return defaults
    merged = defaults.model_dump()
    for field in ReportingSettings.model_fields:
        if field not in stored:
            continue
        candidate = {**merged, field: stored[field]}
        try:
            ReportingSettings(**candidate)
        except (ValidationError, TypeError):
            logger.warning("reporting_settings_field_ignored", field=field)
            continue
        merged = candidate
    return ReportingSettings(**merged)


async def load_reporting_settings(session: Any, tenant_id: uuid.UUID | str) -> ReportingSettings:
    """The tenant's reporting settings, read in ``session`` (a tenant session for ``tenant_id``)."""
    from sqlalchemy import select

    from core.models.tenant import Tenant

    tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    row = (await session.execute(select(Tenant.settings, Tenant.data_region).where(Tenant.id == tid))).first()
    if row is None:
        return defaults_for_region(None)
    return settings_from_tenant(row[0], row[1])


def store_reporting_settings(tenant_settings: dict[str, Any] | None, value: ReportingSettings) -> dict[str, Any]:
    """A copy of ``tenant_settings`` with the reporting settings replaced by ``value``."""
    updated = dict(tenant_settings or {})
    updated[REPORTING_SETTINGS_KEY] = value.model_dump()
    return updated
