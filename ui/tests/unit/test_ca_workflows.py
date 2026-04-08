"""Tests for CA workflow YAML definitions."""
from __future__ import annotations

from pathlib import Path

import yaml

EXAMPLES = Path(__file__).resolve().parents[2] / "workflows" / "examples"

def _load(name):
    p = EXAMPLES / name
    assert p.exists(), f"Missing: {p}"
    return yaml.safe_load(p.read_text())

class TestGSTR:
    def test_parses(self):
        assert _load("gstr_filing_monthly.yaml")["name"]

    def test_finance(self):
        assert _load("gstr_filing_monthly.yaml")["domain"] == "finance"

    def test_schedule(self):
        assert _load("gstr_filing_monthly.yaml")["trigger"] == "schedule"

    def test_company_scoped(self):
        assert _load("gstr_filing_monthly.yaml").get("company_scoped") is True

    def test_partner_review(self):
        ids = [s["id"] for s in _load("gstr_filing_monthly.yaml")["steps"]]
        assert "partner_review" in ids

class TestTDS:
    def test_parses(self):
        assert _load("tds_quarterly_filing.yaml")["name"]

    def test_company_scoped(self):
        assert _load("tds_quarterly_filing.yaml").get("company_scoped") is True

    def test_partner_review(self):
        ids = [s["id"] for s in _load("tds_quarterly_filing.yaml")["steps"]]
        assert "partner_review" in ids

class TestBankRecon:
    def test_parses(self):
        assert _load("bank_recon_daily.yaml")["name"]

    def test_company_scoped(self):
        assert _load("bank_recon_daily.yaml").get("company_scoped") is True

    def test_auto_match(self):
        ids = [s["id"] for s in _load("bank_recon_daily.yaml")["steps"]]
        assert "auto_match" in ids
