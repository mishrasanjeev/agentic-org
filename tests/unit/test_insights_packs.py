# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights packs (part C): who may ask for which pack, the manifest signature, sign-off rules, the PDF text."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from core.insights import export_jobs, packs, pdf
from core.ownership import Caller


def _caller(role: str, *, admin: bool = False, machine: bool = False) -> Caller:
    return Caller(
        user_id=None if machine else uuid.uuid4(), role=role, domains=None, is_admin=admin, is_machine=machine
    )


ADMIN, CFO, AUDITOR, COO = _caller("admin", admin=True), _caller("cfo"), _caller("auditor"), _caller("coo")


def _refused(kind: str, fmt: str, params: dict, caller: Caller) -> export_jobs.ExportJobError:
    with pytest.raises(export_jobs.ExportJobError) as exc:
        export_jobs.validate(kind, fmt, params, caller)
    return exc.value


def test_who_may_ask_for_which_file() -> None:
    assert export_jobs.validate("view_pdf", "pdf", {"view": "operations"}, COO)["view"] == "operations"
    assert _refused("view_pdf", "pdf", {"view": "finance"}, COO).status == 403
    assert _refused("view_pdf", "pdf", {"view": "nope"}, ADMIN).status == 404
    assert export_jobs.validate("finance_pack", "zip", {"from": "2026-10-01"}, CFO)["view"] == "finance"
    assert _refused("finance_pack", "zip", {}, AUDITOR).error == "insights_pack_forbidden"
    clean = export_jobs.validate("evidence_pack", "zip", {"from": "2026-10-01", "to": "2026-10-31"}, AUDITOR)
    assert clean["from"] == "2026-10-01" and clean["to"] == "2026-10-31"
    assert _refused("evidence_pack", "zip", {"from": "2026-10-01", "to": "2026-10-31"}, CFO).status == 403
    assert _refused("evidence_pack", "zip", {"from": "2026-10-31", "to": "2026-10-01"}, ADMIN).error == (
        "invalid_period"
    )
    assert _refused("evidence_pack", "zip", {"from": "2025-01-01", "to": "2026-10-31"}, ADMIN).error == (
        "invalid_period"
    )
    assert _refused("evidence_pack", "zip", {"to": "2026-10-31"}, ADMIN).error == "invalid_period"
    assert _refused("evidence_pack", "pdf", {"from": "2026-10-01", "to": "2026-10-31"}, ADMIN).error == (
        "invalid_format"
    )


def test_the_manifest_is_signed_and_any_change_breaks_it() -> None:
    body = packs.manifest({"a.csv": b"x,y\n", "b.pdf": b"%PDF-"}, {"kind": "evidence_pack", "period": {"from": "x"}})
    assert body["files"]["a.csv"]["bytes"] == 4 and body["signature"]["algorithm"] == "HMAC-SHA256"
    assert packs.verify_manifest(body)
    tampered = {**body, "files": {**body["files"], "a.csv": {"sha256": "0" * 64, "bytes": 4}}}
    assert not packs.verify_manifest(tampered)
    assert not packs.verify_manifest({k: v for k, v in body.items() if k != "signature"})


def _pack(**overrides):
    base = {
        "kind": "evidence_pack",
        "status": "ready",
        "signed_at": None,
        "sha256": "f" * 64,
        "params": {
            "manifest_sha256": "a" * 64,
            "verification_status": "verified",
            "from": "2026-10-01",
            "to": "2026-10-31",
        },
    }
    return SimpleNamespace(**{**base, **overrides})


def test_sign_off_rules() -> None:
    now = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)
    signature = packs.sign(_pack(), AUDITOR, name="Meera", statement="  Reviewed   and accepted. ", now=now)
    assert signature == {
        "by": "Meera",
        "role": "auditor",
        "user_id": str(AUDITOR.user_id),
        "statement": "Reviewed and accepted.",
        "signed_at": now.isoformat(),
        "pack_sha256": "f" * 64,
        "manifest_sha256": "a" * 64,
        "verification_status": "verified",
        "period": {"from": "2026-10-01", "to": "2026-10-31"},
    }
    assert packs.sign(_pack(), ADMIN, name="", statement="ok", now=now)["role"] == "admin"
    cases = [
        (_pack(), CFO, "insights_signoff_forbidden"),
        (_pack(), _caller("auditor", machine=True), "insights_signoff_needs_a_person"),
        (_pack(kind="finance_pack"), AUDITOR, "not_an_evidence_pack"),
        (_pack(status="running"), AUDITOR, "export_not_ready"),
        (_pack(signed_at=now), AUDITOR, "already_signed"),
    ]
    for job, caller, error in cases:
        with pytest.raises(packs.SignOffError) as exc:
            packs.sign(job, caller, name="x", statement="ok", now=now)
        assert exc.value.error == error
    with pytest.raises(packs.SignOffError) as blank:
        packs.sign(_pack(), AUDITOR, name="x", statement="   ", now=now)
    assert blank.value.error == "invalid_statement"


def test_pdf_text_and_numbers() -> None:
    assert pdf.latin("₹ 5 — ok ✓") == "INR  5 - ok ?"
    assert pdf.format_value(1234567, "inr") == "INR 12,34,567"
    assert pdf.format_value(1234.5, "usd") == "USD 1,234.50"
    assert pdf.format_value(12.5, "inr") == "INR 12.50"
    assert pdf.format_value(95_500, "duration_ms") == "1 m 36 s"
    assert pdf.format_value(450, "duration_ms") == "450 ms"
    assert pdf.format_value(96.33, "percent") == "96.3%"
    assert pdf.format_value(None, "count") == "No data"
    content = pdf.view_pdf(
        title="Insights - Finance",
        organisation="Acme",
        period={"from": "2026-10-01", "to": "2026-10-31"},
        filters={"compare": "previous"},
        tiles=[
            {"key": "cost.spend", "title": "Spend", "value": 245000, "unit": "inr", "data_quality": "measured"},
            {
                "key": "cost.forecast",
                "title": "Forecast",
                "value": None,
                "data_quality": "no_data",
                "hint": "Meter it.",
            },
        ],
        charts=[
            {
                "key": "cost.by_agent",
                "title": "Spend by agent",
                "unit": "inr",
                "data_quality": "estimated",
                "rows": [{"label": f"agent {i}", "value": 1000 * i} for i in range(20)],
            },
            {"key": "cost.by_model", "title": "Spend by model", "data_quality": "no_data", "hint": "Add a rate card."},
        ],
        currency="INR",
        computed_at=datetime(2026, 11, 3, tzinfo=UTC),
    )
    assert content[:5] == b"%PDF-"
    from pypdf import PdfReader

    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages)
    for expected in ("Insights - Finance", "INR 2,45,000", "No data", "Meter it.", "estimated", "8 more rows"):
        assert expected in text, expected
