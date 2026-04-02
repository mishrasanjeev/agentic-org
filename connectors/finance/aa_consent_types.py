"""Pydantic models for the Account Aggregator consent flow."""

from __future__ import annotations

from enum import Enum, StrEnum

from pydantic import BaseModel


class ConsentStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class FIType(StrEnum):
    """Financial Information types per RBI AA framework."""

    DEPOSIT = "DEPOSIT"
    TERM_DEPOSIT = "TERM_DEPOSIT"
    RECURRING_DEPOSIT = "RECURRING_DEPOSIT"
    SIP = "SIP"
    MUTUAL_FUNDS = "MUTUAL_FUNDS"
    ETF = "ETF"
    BONDS = "BONDS"
    DEBENTURES = "DEBENTURES"
    SHARES = "SHARES"
    INSURANCE_POLICIES = "INSURANCE_POLICIES"
    NPS = "NPS"
    GOVT_SECURITIES = "GOVT_SECURITIES"
    EQUITIES = "EQUITIES"


class PurposeCode(int, Enum):
    """RBI-defined purpose codes for data access."""

    WEALTH_MANAGEMENT = 101
    CUSTOMER_SPENDING_PATTERNS = 102
    ACCOUNT_AGGREGATION = 103
    LENDING = 104
    INSURANCE = 105


class ConsentRequest(BaseModel):
    """Input for creating an AA consent request."""

    customer_vua: str  # e.g. user@finvu
    fi_types: list[FIType]
    purpose_code: PurposeCode
    from_date: str  # YYYY-MM-DD
    to_date: str
    fetch_type: str = "ONETIME"  # ONETIME or PERIODIC
    consent_mode: str = "VIEW"  # VIEW, STORE, QUERY, STREAM
    data_life_unit: str = "MONTH"
    data_life_value: int = 3
    frequency_unit: str = "MONTH"
    frequency_value: int = 1


class ConsentArtifact(BaseModel):
    """Signed consent object returned by the AA."""

    consent_id: str
    consent_handle: str
    status: ConsentStatus
    customer_vua: str
    fi_types: list[str]
    purpose_code: int
    from_date: str
    to_date: str
    created_at: str
    signed_consent: str = ""  # Digital signature
    consent_expiry: str = ""


class FIDataSession(BaseModel):
    """Session for fetching financial data."""

    session_id: str
    consent_id: str
    status: str  # ACTIVE, COMPLETED, EXPIRED, FAILED
    created_at: str
    data_ranges: list[dict] = []


class AACallbackPayload(BaseModel):
    """Webhook payload from Finvu on consent status change."""

    consent_handle: str
    consent_id: str = ""
    consent_status: ConsentStatus
    timestamp: str
    aa_id: str = "finvu-aa"
    customer_vua: str = ""
    error_code: str = ""
    error_msg: str = ""
