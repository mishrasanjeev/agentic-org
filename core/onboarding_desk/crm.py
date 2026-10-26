# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Lead intake from the partner's CRM: HubSpot or Salesforce contacts, through the tenant's own connector.

A tenant administrator names a CRM lead source, the connector, which contacts
to take (one field equal to one value, such as a list membership or a lead
status), which fields record consent per channel, the field that holds when
consent was captured, and an attestation of the wording people agreed to. A
sync pulls up to 200 matching contacts and turns each into a lead, idempotently
by its CRM id, so syncing again never duplicates a lead.

Consent is never assumed. A channel's consent is recorded only when its consent
field is true and the capture time field holds a time; the wording and method
come from the attestation, as for a CSV import. A contact with no consent field
set arrives as a lead that cannot be contacted until consent is recorded.
Opted-out contacts arrive opted out, as every lead does.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from core.onboarding_desk import leads, rules, store
from core.onboarding_desk.store import DeskError

logger = structlog.get_logger()

PROVIDERS: tuple[str, ...] = ("hubspot", "salesforce")
MAX_SYNC = 200
_CONNECTOR_RE = re.compile(r"^registry-[a-z][a-z0-9_]{1,63}$")
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
#: Where each provider keeps the fields a lead needs.
FIELDS: dict[str, dict[str, str]] = {
    "hubspot": {
        "id": "id",
        "first_name": "firstname",
        "last_name": "lastname",
        "email": "email",
        "phone": "phone",
        "company": "company",
        "region": "state",
        "country": "country",
    },
    "salesforce": {
        "id": "Id",
        "first_name": "FirstName",
        "last_name": "LastName",
        "email": "Email",
        "phone": "Phone",
        "company": "Account.Name",
        "region": "MailingState",
        "country": "MailingCountry",
    },
}
_COUNTRIES = {"US": "US", "USA": "US", "UNITED STATES": "US", "UNITED STATES OF AMERICA": "US", "CA": "CA",
              "CAN": "CA", "CANADA": "CA"}  # fmt: skip
_REGION_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA", "COLORADO": "CO",
    "CONNECTICUT": "CT", "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA",
    "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY",
    "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND",
    "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT",
    "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "ALBERTA": "AB", "BRITISH COLUMBIA": "BC", "MANITOBA": "MB", "NEW BRUNSWICK": "NB",
    "NEWFOUNDLAND AND LABRADOR": "NL", "NORTHWEST TERRITORIES": "NT", "NOVA SCOTIA": "NS", "NUNAVUT": "NU",
    "ONTARIO": "ON", "PRINCE EDWARD ISLAND": "PE", "QUEBEC": "QC", "QUÉBEC": "QC", "SASKATCHEWAN": "SK",
    "YUKON": "YT",
}  # fmt: skip
_TRUE = frozenset({"true", "yes", "y", "1", "opted_in", "subscribed", "granted"})


@dataclass(frozen=True)
class CrmSettings:
    source_id: str
    provider: str
    connector: str
    filter_field: str
    filter_value: str
    captured_at_field: str
    attestation: leads.Attestation
    consent_fields: dict[str, str] = field(default_factory=dict)
    merchant_type: str = "general"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "provider": self.provider,
            "connector": self.connector,
            "filter_field": self.filter_field,
            "filter_value": self.filter_value,
            "captured_at_field": self.captured_at_field,
            "consent_fields": dict(self.consent_fields),
            "attestation": {
                "wording": self.attestation.wording,
                "method": self.attestation.method,
                "written": self.attestation.written,
            },
            "merchant_type": self.merchant_type,
        }


def _field(raw: dict[str, Any], name: str) -> str:
    value = str(raw.get(name) or "").strip()
    if not _FIELD_RE.match(value):
        raise DeskError(422, "crm_settings_invalid", f"{name} must be a CRM field name")
    return value


def crm_settings_from(raw: Any) -> CrmSettings:
    if not isinstance(raw, dict):
        raise DeskError(422, "crm_settings_invalid", "CRM settings must be an object")
    allowed = {"source_id", "provider", "connector", "filter_field", "filter_value", "captured_at_field",
               "consent_fields", "attestation", "merchant_type"}  # fmt: skip
    if set(raw) - allowed:
        raise DeskError(422, "crm_settings_invalid", f"CRM settings may only set {sorted(allowed)}")
    try:
        source_id = str(uuid.UUID(str(raw.get("source_id") or "")))
    except ValueError as exc:
        raise DeskError(422, "crm_settings_invalid", "source_id must be a CRM lead source id") from exc
    provider = str(raw.get("provider") or "")
    if provider not in PROVIDERS:
        raise DeskError(422, "crm_settings_invalid", f"provider must be one of {list(PROVIDERS)}")
    connector = str(raw.get("connector") or "")
    if not _CONNECTOR_RE.match(connector):
        raise DeskError(
            422, "crm_settings_invalid", "connector must be a configured connector id like registry-hubspot"
        )
    value = str(raw.get("filter_value") or "").strip()
    if not value or len(value) > 200:
        raise DeskError(422, "crm_settings_invalid", "filter_value must be between 1 and 200 characters")
    consent_raw = raw.get("consent_fields") or {}
    if not isinstance(consent_raw, dict) or not consent_raw or set(consent_raw) - set(rules.OUTREACH_CHANNELS):
        raise DeskError(
            422, "crm_settings_invalid", f"consent_fields maps channels {list(rules.OUTREACH_CHANNELS)} to fields"
        )
    consent_fields = {channel: _field(consent_raw, channel) for channel in consent_raw}
    attestation_raw = raw.get("attestation")
    if not isinstance(attestation_raw, dict):
        raise DeskError(422, "crm_settings_invalid", "attestation must state the wording people agreed to")
    try:
        attestation = leads.attestation_from({"method": "crm_import", **attestation_raw})
    except leads.LeadError as exc:
        raise DeskError(422, "crm_settings_invalid", f"attestation.{exc.field}: {exc.message}") from exc
    merchant_type = str(raw.get("merchant_type") or "general")
    if not re.match(r"^[a-z][a-z0-9_]{0,63}$", merchant_type):
        raise DeskError(422, "crm_settings_invalid", "merchant_type must be a lower-case slug")
    return CrmSettings(
        source_id=source_id,
        provider=provider,
        connector=connector,
        filter_field=_field(raw, "filter_field"),
        filter_value=value,
        captured_at_field=_field(raw, "captured_at_field"),
        attestation=attestation,
        consent_fields=consent_fields,
        merchant_type=merchant_type,
    )


async def get_crm_settings(tenant_id: uuid.UUID) -> CrmSettings | None:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingPolicy

    async with store._session(tenant_id) as session:
        row = (
            await session.execute(select(OnboardingPolicy).where(OnboardingPolicy.tenant_id == tenant_id))
        ).scalar_one_or_none()
    raw = (row.settings or {}).get("crm") if row else None
    return crm_settings_from(raw) if raw else None


async def put_crm_settings(tenant_id: uuid.UUID, raw: Any, *, actor: str) -> CrmSettings:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingPolicy

    crm = crm_settings_from(raw)
    sources = {str(s.id): s for s in await store.list_sources(tenant_id)}
    source = sources.get(crm.source_id)
    if source is None or source.kind != "crm":
        raise DeskError(422, "crm_settings_invalid", "source_id must name a lead source of kind crm")
    async with store._session(tenant_id) as session:
        row = (
            await session.execute(select(OnboardingPolicy).where(OnboardingPolicy.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if row is None:
            session.add(OnboardingPolicy(tenant_id=tenant_id, settings={"crm": crm.to_dict()}, updated_by=actor[:128]))
        else:
            row.settings = {**(row.settings or {}), "crm": crm.to_dict()}
            row.updated_by = actor[:128]
            row.updated_at = datetime.now(UTC)
    return crm


# ── Mapping a contact to a lead ──────────────────────────────────────────────


def _get(record: dict[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def country_code(value: Any) -> str:
    return _COUNTRIES.get(" ".join(str(value or "").upper().split()), "")


def region_code(value: Any) -> str:
    text = " ".join(str(value or "").upper().split())
    return text if len(text) == 2 else _REGION_NAMES.get(text, "")


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in _TRUE


def lead_raw(record: dict[str, Any], crm: CrmSettings) -> dict[str, Any]:
    """The lead a CRM contact describes, in the shape ``leads.lead_from`` validates."""
    names = FIELDS[crm.provider]
    record_id = str(_get(record, names["id"]) or "")
    first, last = _get(record, names["first_name"]), _get(record, names["last_name"])
    contact_name = " ".join(str(part) for part in (first, last) if part)
    captured = _get(record, crm.captured_at_field)
    consents = [
        {
            "channel": channel,
            "wording": crm.attestation.wording,
            "method": crm.attestation.method,
            "written": crm.attestation.written,
            "captured_at": captured,
            "source_ref": f"{crm.provider}:{record_id}",
        }
        for channel, field_name in crm.consent_fields.items()
        if captured and _truthy(_get(record, field_name))
    ]
    return {
        "business_name": str(_get(record, names["company"]) or contact_name or "").strip(),
        "contact_name": contact_name,
        "email": _get(record, names["email"]),
        "phone": _get(record, names["phone"]),
        "country": country_code(_get(record, names["country"])),
        "region": region_code(_get(record, names["region"])),
        "merchant_type": crm.merchant_type,
        "external_ref": f"{crm.provider}:{record_id}" if record_id else None,
        "consents": consents,
    }


# ── Fetching and syncing ─────────────────────────────────────────────────────


def _soql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _wanted(crm: CrmSettings) -> list[str]:
    names = FIELDS[crm.provider]
    extra = [*crm.consent_fields.values(), crm.captured_at_field]
    return list(dict.fromkeys([*names.values(), *extra]))


async def fetch(tenant_id: uuid.UUID, crm: CrmSettings, *, limit: int = MAX_SYNC) -> list[dict[str, Any]]:
    """Matching contacts from the CRM; ``crm_unavailable`` when the connector cannot be reached."""
    from core.onboarding_desk import senders
    from core.onboarding_desk.branding import Sender

    count = max(1, min(limit, MAX_SYNC))
    instance = await senders._connector(tenant_id, Sender(crm.connector))
    if instance is None:
        raise DeskError(409, "crm_connector_not_configured", f"{crm.connector} is not configured for this tenant")
    try:
        if crm.provider == "hubspot":
            properties = [name for name in _wanted(crm) if name != "id"]
            result = await instance.search_contacts(
                property_name=crm.filter_field,
                operator="EQ",
                query=crm.filter_value,
                properties=properties,
                limit=min(count, 100),
            )
            records = result.get("contacts")
        else:
            fields = ", ".join(_wanted(crm))
            # Field names are checked against _FIELD_RE, the value is an escaped literal and the limit an int.
            soql = (
                f"SELECT {fields} FROM Contact WHERE {crm.filter_field} = {_soql_literal(crm.filter_value)} "  # noqa: S608  # nosec B608
                f"ORDER BY LastModifiedDate DESC LIMIT {count}"
            )
            result = await instance.query(q=soql)
            records = result.get("records")
    # enterprise-gate: broad-except-ok reason=connector-failure-logged-and-reported-as-crm-unavailable
    except Exception as exc:  # noqa: BLE001 - each connector raises its own transport errors
        logger.warning("onboarding_crm_fetch_failed", provider=crm.provider, error=type(exc).__name__)
        raise DeskError(502, "crm_unavailable", f"the CRM could not be read: {type(exc).__name__}") from exc
    finally:
        try:
            await instance.disconnect()
        # enterprise-gate: broad-except-ok reason=disconnect-failure-logged-and-the-sync-result-stands
        except Exception as exc:  # noqa: BLE001 - closing a client must not change the outcome
            logger.info("onboarding_crm_disconnect_failed", error=type(exc).__name__)
    if not isinstance(records, list):
        error = result.get("error") if isinstance(result, dict) else None
        raise DeskError(502, "crm_unavailable", f"the CRM refused the query: {str(error)[:200]}")
    return [r for r in records if isinstance(r, dict)][:count]


async def sync(tenant_id: uuid.UUID, *, actor: str, limit: int = MAX_SYNC) -> dict[str, Any]:
    """Pull matching contacts and store them as leads; returns what was created, already there and refused."""
    crm = await get_crm_settings(tenant_id)
    if crm is None:
        raise DeskError(409, "crm_not_configured", "set the CRM settings first")
    records = await fetch(tenant_id, crm, limit=limit)
    now = datetime.now(UTC)
    created = existing = 0
    errors: list[dict[str, Any]] = []
    for record in records:
        raw = lead_raw(record, crm)
        try:
            lead = leads.lead_from(raw, now=now)
        except leads.LeadError as exc:
            errors.append({"external_ref": raw["external_ref"], "field": exc.field, "message": exc.message})
            continue
        outcome = await store.create_lead(
            tenant_id, lead, source_id=uuid.UUID(crm.source_id), source_kind="crm", actor=actor
        )
        if outcome.created:
            created += 1
        else:
            existing += 1
    logger.info("onboarding_crm_synced", provider=crm.provider, created=created, refused=len(errors))
    return {"fetched": len(records), "created": created, "existing": existing, "rejected": len(errors),
            "errors": errors[:100]}  # fmt: skip
