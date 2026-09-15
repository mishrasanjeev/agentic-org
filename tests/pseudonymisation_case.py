# SPDX-License-Identifier: Apache-2.0
"""A fixture case for pseudonymisation tests.

Every value is invented or reserved: names from no real person, a date of
birth of 1900-01-01, ``example.com`` addresses, SSN area 000, ITIN serial
0000, an EIN prefix the IRS never assigns, the HMRC example National
Insurance prefix ``QQ``, Companies House ``00000001``, and an IBAN and VAT
number built on all-zero bank and registry codes with computed check digits.
"""

from __future__ import annotations

from typing import Any

TENANT_ID = "00000000-0000-4000-8000-00000000f005"
OTHER_TENANT_ID = "00000000-0000-4000-8000-00000000f006"
# A case id a client puts in its request. It must never select a pseudonym map.
CASE_ID = "case-f5-0001"


def iban(country: str, bban: str) -> str:
    numeric = "".join(str(int(char, 36)) for char in bban + country + "00")
    return f"{country}{98 - int(numeric) % 97:02d}{bban}"


APPLICANT_NAME = "Quinta Placeholder"
DIRECTOR_NAME = "Orrin Examplesson"
DATE_OF_BIRTH = "1900-01-01"
ADDRESS_LINE = "1 Example Street, Sampletown"
EMAIL = "applicant@example.com"
SSN = "000-00-0001"
ITIN = "900-70-0000"
EIN = "00-0000001"
NINO = "QQ 12 34 56 C"
COMPANY_NUMBER = "00000001"
IBAN = iban("DE", "000000000000000001")
VAT = "DE000000011"
SYSTEM_PROMPT_EMAIL = "escalations@example.org"

RAW_VALUES: tuple[str, ...] = (
    APPLICANT_NAME,
    DIRECTOR_NAME,
    DATE_OF_BIRTH,
    ADDRESS_LINE,
    EMAIL,
    SSN,
    ITIN,
    EIN,
    NINO,
    COMPANY_NUMBER,
    IBAN,
    VAT,
    SYSTEM_PROMPT_EMAIL,
)


def task_input() -> dict[str, Any]:
    return {
        "action": "screen_applicant",
        "inputs": {
            "case_id": CASE_ID,
            "applicant": {
                "full_name": APPLICANT_NAME,
                "dob": DATE_OF_BIRTH,
                "address": {"line1": ADDRESS_LINE, "country": "GB"},
                "email": EMAIL,
            },
            "notes": (
                f"{APPLICANT_NAME} gave SSN {SSN} and ITIN {ITIN}. Employer EIN {EIN}. "
                f"National Insurance number {NINO}. Director {DIRECTOR_NAME}, full_name on file. "
                f"Company number {COMPANY_NUMBER}; pays from {IBAN}; VAT {VAT}."
            ),
            "director": {"full_name": DIRECTOR_NAME},
        },
    }


def system_prompt() -> str:
    return f"You screen applicants. Escalate anything unusual to {SYSTEM_PROMPT_EMAIL}."
