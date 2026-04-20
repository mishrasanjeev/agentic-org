"""Unit tests for ABM input validators introduced with PR-D of the
2026-04-20 QA sweep (TC_012/013/018/019/020).

Pure-function tests — no DB, no fixtures. The CSV upload endpoint
itself is exercised in the integration suite under AGENTICORG_DB_URL.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.v1.abm import (
    AccountCreate,
    _validate_domain,
    _validate_safe_name,
    _validate_safe_text,
)


class TestValidateDomain:
    @pytest.mark.parametrize(
        "domain",
        [
            "example.com",
            "acme.co.uk",
            "sub.example.org",
            "a.com",
            "wipro.in",
            "xn--9q8h.tw",  # punycode IDN
            "ACME.COM",     # upper-case gets lowercased
        ],
    )
    def test_valid_domains_pass(self, domain: str) -> None:
        assert _validate_domain(domain) == domain.strip().lower()

    @pytest.mark.parametrize(
        "domain",
        [
            "wipro",              # TC_018 — no TLD
            "",
            ".com",
            "just.",
            "http://example.com", # scheme must not be present
            "foo bar.com",        # whitespace
            "foo@bar.com",         # @ forbidden
            "-foo.com",           # leading hyphen
            "foo-.com",           # trailing hyphen
            "a." + "b" * 64 + ".com",  # overlong label
        ],
    )
    def test_invalid_domains_rejected(self, domain: str) -> None:
        with pytest.raises(ValueError):
            _validate_domain(domain)


class TestValidateSafeName:
    @pytest.mark.parametrize(
        "name",
        [
            "Acme Corp",
            "A. O. Smith",
            "S&P Global",
            "Wipro Limited (Technologies)",
            "Infosys-BPM",
            "O'Brien Consulting",
        ],
    )
    def test_typical_company_names_pass(self, name: str) -> None:
        assert _validate_safe_name(name) == name.strip()

    @pytest.mark.parametrize(
        "bad",
        [
            "Infosys@123",       # @
            "IT@@Services",
            "Tier#1",            # #
            "50Cr-100Cr$$",      # $
            "Acme Corp <script>",  # < >
            "Acme | Corp",        # |
            "Acme; DROP TABLE",   # ;
        ],
    )
    def test_injection_like_names_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError):
            _validate_safe_name(bad)


class TestValidateSafeText:
    @pytest.mark.parametrize(
        "value",
        [
            "IT Services",
            "50Cr-100Cr",
            "100M USD",
            "Finance/Banking",  # slash allowed
            "",                  # empty is fine; required-ness is separate
        ],
    )
    def test_typical_values_pass(self, value: str) -> None:
        assert _validate_safe_text(value, "industry") == value.strip()

    @pytest.mark.parametrize(
        "value",
        [
            "50Cr-100Cr$$",  # $
            "Banking@HQ",    # @
            "IT #1",         # #
            "back\\slash",   # backslash
            "pipe | sep",    # pipe
        ],
    )
    def test_injection_like_values_rejected(self, value: str) -> None:
        with pytest.raises(ValueError):
            _validate_safe_text(value, "industry")


class TestAccountCreatePydantic:
    def test_happy_path_accepts_clean_payload(self) -> None:
        acct = AccountCreate(
            company_name="Acme Corp",
            domain="acme.com",
            industry="IT Services",
            revenue="50Cr-100Cr",
            tier="1",
        )
        assert acct.domain == "acme.com"
        assert acct.company_name == "Acme Corp"

    def test_invalid_domain_raises_422_class(self) -> None:
        with pytest.raises(ValidationError):
            AccountCreate(company_name="Acme", domain="wipro")

    def test_injection_in_company_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AccountCreate(company_name="Acme@123", domain="acme.com")

    def test_empty_company_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AccountCreate(company_name="", domain="acme.com")

    def test_unknown_tier_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AccountCreate(
                company_name="Acme",
                domain="acme.com",
                tier="99",
            )
