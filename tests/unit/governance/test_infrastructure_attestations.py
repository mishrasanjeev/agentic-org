# SPDX-License-Identifier: Apache-2.0
"""The compliance report's infrastructure attestation section."""

from __future__ import annotations

import json
from pathlib import Path

from core.governance import infrastructure as infra

ROOT = Path(__file__).resolve().parents[3]


def test_without_a_file_every_control_is_not_verified(monkeypatch):
    monkeypatch.setattr(infra.settings, "infrastructure_attestations_file", None)
    section = infra.report_section()
    assert section["status"] == "collected" and section["source"] is None
    assert len(section["controls"]) == len(infra.CONTROLS)
    assert {c["status"] for c in section["controls"]} == {"not_verified"}
    assert section["summary"]["not_verified"] == len(infra.CONTROLS)


def test_recorded_attestations_are_merged_in_order(monkeypatch, tmp_path):
    path = tmp_path / "attestations.json"
    path.write_text(
        json.dumps(
            {
                "attestations": [
                    {
                        "id": "inf-01",
                        "status": "verified",
                        "verified_at": "2026-09-30",
                        "verified_by": "platform-ops",
                        "evidence_ref": "CHG-1182",
                    },
                    {"id": "DATA-02", "status": "not_applicable", "evidence_ref": "no analytics exports"},
                    {"id": "XYZ-99", "status": "verified"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(infra.settings, "infrastructure_attestations_file", str(path))
    section = infra.report_section()
    assert section["status"] == "collected" and section["source"] == str(path)
    by_id = {c["id"]: c for c in section["controls"]}
    assert by_id["INF-01"]["status"] == "verified" and by_id["INF-01"]["verified_by"] == "platform-ops"
    assert by_id["DATA-02"]["status"] == "not_applicable"
    assert by_id["SEC-01"]["status"] == "not_verified"
    assert section["unknown_ids"] == ["XYZ-99"]
    assert section["summary"] == {"verified": 1, "not_verified": len(infra.CONTROLS) - 2, "not_applicable": 1}
    assert [c["id"] for c in section["controls"]] == [cid for cid, _ in infra.CONTROLS]


def test_an_unreadable_or_invalid_file_reports_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(infra.settings, "infrastructure_attestations_file", str(tmp_path / "missing.json"))
    section = infra.report_section()
    assert section["status"] == "unavailable" and "FileNotFoundError" in section["reason"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"attestations": [{"id": "INF-01", "status": "maybe"}]}), encoding="utf-8")
    monkeypatch.setattr(infra.settings, "infrastructure_attestations_file", str(bad))
    section = infra.report_section()
    assert section["status"] == "unavailable" and "status must be one of" in section["reason"]
    assert {c["status"] for c in section["controls"]} == {"not_verified"}


def test_controls_match_the_deployment_reference():
    doc = (ROOT / "docs" / "bfsi" / "deployment-reference.md").read_text(encoding="utf-8")
    for control_id, _ in infra.CONTROLS:
        assert f"| {control_id} |" in doc, control_id


def test_compliance_report_carries_the_section_and_does_not_claim_mtls_by_default():
    src = (ROOT / "api" / "v1" / "compliance.py").read_text(encoding="utf-8")
    assert '"infrastructure_controls": infrastructure.report_section()' in src
    assert 'os.getenv("AGENTICORG_MTLS", "false")' in src
