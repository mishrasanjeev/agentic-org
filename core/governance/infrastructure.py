# SPDX-License-Identifier: Apache-2.0
"""Infrastructure attestations for the compliance report.

The hosting platform, not application code, satisfies the infrastructure items
of the BFSI baseline (``docs/bfsi/deployment-reference.md``): multi-zone
regions, HSM-backed keys, a web application firewall, immutable backups and so
on. The platform cannot verify them itself; an operator does, and records the
outcome in a JSON file named by ``AGENTICORG_INFRASTRUCTURE_ATTESTATIONS_FILE``::

    {
      "attestations": [
        {"id": "INF-01", "status": "verified", "verified_at": "2026-09-30",
         "verified_by": "platform-ops", "evidence_ref": "change CHG-1182"},
        {"id": "DATA-02", "status": "not_applicable", "evidence_ref": "no analytics exports"}
      ]
    }

The compliance report lists every control with the recorded status, or
``not_verified`` when nothing is recorded, so a reviewer sees exactly what has
and has not been attested. The report never raises: an unreadable file makes the
section ``unavailable`` with the reason.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

STATUSES: tuple[str, ...] = ("verified", "not_verified", "not_applicable")

# The infrastructure items of the baseline, in deployment-reference order.
CONTROLS: tuple[tuple[str, str], ...] = (
    ("INF-01", "Multi-zone architecture"),
    ("INF-02", "Native disaster recovery"),
    ("INF-03", "AI accelerators"),
    ("INF-04", "Managed Kubernetes"),
    ("INF-05", "Managed serverless containers"),
    ("SEC-01", "Hardware security module"),
    ("SEC-02", "Key management and BYOK"),
    ("SEC-03", "Secrets management"),
    ("SEC-04", "Web application firewall"),
    ("SEC-05", "Managed DDoS protection"),
    ("SEC-06", "Security posture management"),
    ("SEC-07", "Threat detection"),
    ("SEC-08", "SIEM and security lake"),
    ("SEC-09", "Identity and access management"),
    ("DATA-01", "Scalable object storage"),
    ("DATA-02", "Serverless SQL query engine"),
    ("DATA-03", "Managed lakehouse"),
    ("DATA-04", "Managed streaming"),
    ("DATA-05", "Managed serverless ETL"),
    ("DATA-06", "Data catalogue and lineage"),
    ("DATA-07", "Managed search engine"),
    ("DATA-08", "Managed relational databases"),
    ("AIINF-13", "Parameter-efficient fine-tuning"),
    ("NET-01", "Private service endpoints"),
    ("NET-02", "Enterprise API gateway"),
    ("NET-03", "Managed secure file transfer"),
    ("NET-04", "Managed service mesh"),
    ("OPS-03", "Infrastructure as code"),
    ("OPS-05", "Fault injection and resilience testing"),
    ("OPS-06", "Managed CI/CD"),
    ("OPS-07", "Immutable backup (WORM)"),
    ("OPS-08", "Confidential compute"),
    ("GOV-01", "Published per-service SLAs"),
    ("GOV-02", "Proven in-country track record"),
    ("GOV-03", "24x7 enterprise support"),
    ("FE-08", "Governed API exposure"),
)

_KNOWN = {control_id for control_id, _ in CONTROLS}


def _read_attestations(path: str) -> dict[str, dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = raw.get("attestations") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise ValueError("attestations file must hold an object with an 'attestations' list")
    out: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise ValueError(f"attestations[{index}] must be an object with an 'id'")
        status = str(entry.get("status") or "not_verified")
        if status not in STATUSES:
            raise ValueError(f"attestations[{index}].status must be one of {', '.join(STATUSES)}")
        out[entry["id"].strip().upper()] = {
            "status": status,
            "verified_at": entry.get("verified_at"),
            "verified_by": entry.get("verified_by"),
            "evidence_ref": entry.get("evidence_ref"),
        }
    return out


def report_section() -> dict[str, Any]:
    """The ``infrastructure_controls`` section of the compliance report; never raises."""
    path = settings.infrastructure_attestations_file
    section: dict[str, Any] = {"control_id": "INFRA-1", "status": "collected", "source": path or None}
    recorded: dict[str, dict[str, Any]] = {}
    if path:
        try:
            recorded = _read_attestations(path)
        # enterprise-gate: broad-except-ok reason=unreadable-attestation-file-reports-the-section-unavailable-never-fails-the-package
        except Exception as exc:
            logger.warning("infrastructure_attestations_unreadable", error_type=type(exc).__name__)
            section["status"] = "unavailable"
            section["reason"] = f"{type(exc).__name__}: {exc}"
    controls = []
    for control_id, title in CONTROLS:
        entry = recorded.get(control_id, {})
        controls.append(
            {
                "id": control_id,
                "title": title,
                "status": entry.get("status", "not_verified"),
                "verified_at": entry.get("verified_at"),
                "verified_by": entry.get("verified_by"),
                "evidence_ref": entry.get("evidence_ref"),
            }
        )
    section["controls"] = controls
    section["unknown_ids"] = sorted(set(recorded) - _KNOWN)
    section["summary"] = {status: sum(1 for c in controls if c["status"] == status) for status in STATUSES}
    return section
