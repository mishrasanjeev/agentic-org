# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Validating a lead and its consents, from an API call, a signed webhook or a CSV import.

A lead names a business in the United States or Canada, at least one way to
reach a person there, and the consents that person gave. A consent records
the channel, the exact wording they agreed to, how and where it was captured,
when, and whether it was captured in writing. Nothing is inferred: a CSV
import carries consent only through the importer's attestation of the wording
and method used to collect it.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from core.onboarding_desk import contacts, rules

MAX_NAME = 200
MAX_WORDING = 2000
MIN_WORDING = 10
MAX_SOURCE_REF = 512
MAX_EXTERNAL_REF = 128
MAX_LEADS_PER_WEBHOOK = 100
MAX_CSV_BYTES = 1024 * 1024
MAX_CSV_ROWS = 2000
METHODS: tuple[str, ...] = (
    "web_form",
    "crm_import",
    "csv_attestation",
    "inbound_message",
    "recorded_call",
    "paper_form",
)
LOCALES: tuple[str, ...] = ("en", "fr")
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
CSV_COLUMNS: tuple[str, ...] = (
    "business_name",
    "contact_name",
    "email",
    "phone",
    "country",
    "region",
    "locale",
    "merchant_type",
    "external_ref",
    "consent_channels",
    "consent_captured_at",
    "consent_source_ref",
)


class LeadError(ValueError):
    """A lead or consent that cannot be accepted; ``field`` names what is wrong."""

    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(message)
        self.field = field_name
        self.message = message


@dataclass(frozen=True)
class ConsentInput:
    channel: str
    wording: str
    method: str
    captured_at: datetime
    source_ref: str = ""
    written: bool = False
    expires_at: datetime | None = None


@dataclass(frozen=True)
class LeadInput:
    business_name: str
    country: str
    region: str
    locale: str
    merchant_type: str
    contact_name: str = ""
    email: str | None = None
    phone: str | None = None
    external_ref: str | None = None
    consents: tuple[ConsentInput, ...] = field(default_factory=tuple)


def _text(raw: dict[str, Any], name: str, *, limit: int, required: bool = False) -> str:
    value = raw.get(name)
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise LeadError(name, f"{name} is required")
    if len(text) > limit:
        raise LeadError(name, f"{name} is longer than {limit} characters")
    return text


def _instant(value: Any, name: str) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise LeadError(name, f"{name} must be an ISO 8601 instant") from exc
    if moment.tzinfo is None:
        raise LeadError(name, f"{name} must include a time zone")
    return moment.astimezone(UTC)


def consent_from(raw: dict[str, Any], *, now: datetime) -> ConsentInput:
    channel = _text(raw, "channel", limit=16, required=True).lower()
    if channel not in rules.OUTREACH_CHANNELS:
        raise LeadError("channel", f"channel must be one of {list(rules.OUTREACH_CHANNELS)}")
    wording = _text(raw, "wording", limit=MAX_WORDING, required=True)
    if len(wording) < MIN_WORDING:
        raise LeadError("wording", "wording must be the text the person agreed to")
    method = _text(raw, "method", limit=32, required=True)
    if method not in METHODS:
        raise LeadError("method", f"method must be one of {list(METHODS)}")
    captured_at = _instant(raw.get("captured_at"), "captured_at")
    if captured_at > now + timedelta(minutes=5):
        raise LeadError("captured_at", "captured_at is in the future")
    written = raw.get("written", False)
    if not isinstance(written, bool):
        raise LeadError("written", "written must be true or false")
    expires_at = _instant(raw["expires_at"], "expires_at") if raw.get("expires_at") else None
    if expires_at is not None and expires_at <= captured_at:
        raise LeadError("expires_at", "expires_at must be after captured_at")
    return ConsentInput(
        channel=channel,
        wording=wording,
        method=method,
        captured_at=captured_at,
        source_ref=_text(raw, "source_ref", limit=MAX_SOURCE_REF),
        written=written,
        expires_at=expires_at,
    )


def lead_from(raw: dict[str, Any], *, now: datetime | None = None) -> LeadInput:
    """A validated lead, or ``LeadError`` naming the first field that is wrong."""
    if not isinstance(raw, dict):
        raise LeadError("lead", "each lead must be an object")
    moment = now or datetime.now(UTC)
    business = _text(raw, "business_name", limit=MAX_NAME, required=True)
    country = _text(raw, "country", limit=2, required=True).upper()
    if country not in rules.COUNTRIES:
        raise LeadError("country", f"country must be one of {list(rules.COUNTRIES)}")
    region = _text(raw, "region", limit=2, required=True).upper()
    if region not in rules.REGIONS[country]:
        raise LeadError("region", f"region is not a {country} state or province code")
    locale = _text(raw, "locale", limit=2).lower() or rules.default_locale(country, region)
    if locale not in LOCALES:
        raise LeadError("locale", f"locale must be one of {list(LOCALES)}")
    merchant_type = _text(raw, "merchant_type", limit=64).lower() or "general"
    if not _SLUG_RE.match(merchant_type):
        raise LeadError("merchant_type", "merchant_type must be a lower-case slug")
    try:
        email = contacts.normalise_email(raw.get("email"))
    except contacts.ContactError as exc:
        raise LeadError("email", str(exc)) from exc
    try:
        phone = contacts.normalise_phone(raw.get("phone"))
    except contacts.ContactError as exc:
        raise LeadError("phone", str(exc)) from exc
    if not email and not phone:
        raise LeadError("email", "a lead needs an email or a phone number")
    external_ref = _text(raw, "external_ref", limit=MAX_EXTERNAL_REF) or None
    raw_consents = raw.get("consents") or []
    if not isinstance(raw_consents, list) or len(raw_consents) > len(rules.OUTREACH_CHANNELS) * 2:
        raise LeadError("consents", "consents must be a short list")
    consents = tuple(consent_from(c if isinstance(c, dict) else {}, now=moment) for c in raw_consents)
    for consent in consents:
        if consent.channel == "email" and not email:
            raise LeadError("consents", "an email consent needs an email address")
        if consent.channel in rules.TIMED_CHANNELS and not phone:
            raise LeadError("consents", f"a {consent.channel} consent needs a phone number")
    return LeadInput(
        business_name=business,
        country=country,
        region=region,
        locale=locale,
        merchant_type=merchant_type,
        contact_name=_text(raw, "contact_name", limit=MAX_NAME),
        email=email,
        phone=phone,
        external_ref=external_ref,
        consents=consents,
    )


def leads_from_webhook(body: Any, *, now: datetime | None = None) -> list[LeadInput]:
    """One lead or ``{"leads": [...]}`` up to ``MAX_LEADS_PER_WEBHOOK``."""
    items = body.get("leads") if isinstance(body, dict) and "leads" in body else [body]
    if not isinstance(items, list) or not items:
        raise LeadError("leads", "the body must be a lead or a non-empty leads list")
    if len(items) > MAX_LEADS_PER_WEBHOOK:
        raise LeadError("leads", f"at most {MAX_LEADS_PER_WEBHOOK} leads per call")
    return [lead_from(item, now=now) for item in items]


@dataclass(frozen=True)
class Attestation:
    """What the importer attests about how the leads in a CSV gave their consent."""

    wording: str
    method: str = "csv_attestation"
    written: bool = False


def attestation_from(raw: dict[str, Any]) -> Attestation:
    wording = _text(raw, "wording", limit=MAX_WORDING, required=True)
    if len(wording) < MIN_WORDING:
        raise LeadError("wording", "wording must be the text the people agreed to")
    method = _text(raw, "method", limit=32) or "csv_attestation"
    if method not in METHODS:
        raise LeadError("method", f"method must be one of {list(METHODS)}")
    written = raw.get("written", False)
    if not isinstance(written, bool):
        raise LeadError("written", "written must be true or false")
    return Attestation(wording, method, written)


@dataclass(frozen=True)
class CsvResult:
    leads: list[LeadInput]
    errors: list[dict[str, Any]]


def leads_from_csv(data: bytes, attestation: Attestation | None, *, now: datetime | None = None) -> CsvResult:
    """Leads from a CSV with the ``CSV_COLUMNS`` header; rows that fail are reported, not imported.

    A row's ``consent_channels`` (separated by ``|`` or ``;``) become consents
    only with an attestation of the wording and method; without one, the rows
    are imported with no consent and cannot be contacted.
    """
    if len(data) > MAX_CSV_BYTES:
        raise LeadError("file", f"the file is larger than {MAX_CSV_BYTES // 1024} KB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LeadError("file", "the file must be UTF-8 text") from exc
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in ("business_name", "country", "region") if c not in header]
    if missing:
        raise LeadError("file", f"the header is missing {', '.join(missing)}")
    unknown = [h for h in header if h not in CSV_COLUMNS]
    if unknown:
        raise LeadError("file", f"unknown columns: {', '.join(unknown[:5])}")
    moment = now or datetime.now(UTC)
    leads: list[LeadInput] = []
    errors: list[dict[str, Any]] = []
    for index, row in enumerate(reader, start=2):
        if index - 1 > MAX_CSV_ROWS:
            raise LeadError("file", f"at most {MAX_CSV_ROWS} rows per file")
        cleaned = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        channels = [c.strip().lower() for c in re.split(r"[|;]", cleaned.pop("consent_channels", "")) if c.strip()]
        captured = cleaned.pop("consent_captured_at", "")
        source_ref = cleaned.pop("consent_source_ref", "")
        if channels and attestation is not None:
            cleaned["consents"] = [
                {
                    "channel": channel,
                    "wording": attestation.wording,
                    "method": attestation.method,
                    "written": attestation.written,
                    "captured_at": captured,
                    "source_ref": source_ref,
                }
                for channel in channels
            ]
        try:
            leads.append(lead_from({k: v for k, v in cleaned.items() if v != "" or k == "consents"}, now=moment))
        except LeadError as exc:
            errors.append({"row": index, "field": exc.field, "error": exc.message})
    return CsvResult(leads, errors)
