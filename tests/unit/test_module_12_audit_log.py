"""Foundation #6 — Module 12 Audit Log.

Source-pin tests for TC-AUDIT-001 through TC-AUDIT-006 from
``docs/qa_test_matrix.yml``.

The audit log is the compliance backbone — every other safety
property in the platform leans on the assumption that audit
entries are tamper-evident, queryable by event_type +
date range, paginated, and exportable. These pins guard:

- The /audit endpoint contract (params, RBAC, ILIKE semantics).
- The page-size cap (so a request can't ask for 10k rows and
  blow the API memory budget).
- The append-only DB trigger that refuses UPDATE/DELETE on
  audit_log rows.
- The UI export contract (CSV + evidence-JSON filenames + mime).
- The auditor-role bypass of the domain filter (auditor sees
  cross-domain entries, all other roles get domain-scoped
  results).
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-AUDIT-001 — Audit log page loads (endpoint contract)
# ─────────────────────────────────────────────────────────────────


def test_tc_audit_001_get_endpoint_returns_paginated_response() -> None:
    src = (REPO / "api" / "v1" / "audit.py").read_text(encoding="utf-8")
    assert '@router.get("/audit", response_model=PaginatedResponse)' in src
    # The handler must accept the documented filter params.
    for param in ("event_type", "agent_id", "company_id",
                  "date_from", "date_to", "page", "per_page"):
        assert param in src, f"audit endpoint missing parameter: {param}"


def test_tc_audit_001_audit_to_dict_includes_required_fields() -> None:
    """Every audit row dict must carry the fields the UI renders.
    A field rename here silently empties the table."""
    src = (REPO / "api" / "v1" / "audit.py").read_text(encoding="utf-8")
    for field in (
        '"id"', '"event_type"', '"actor_type"', '"actor_id"',
        '"agent_id"', '"resource_type"', '"resource_id"',
        '"action"', '"outcome"', '"created_at"',
    ):
        assert field in src, f"_audit_to_dict missing key {field}"


# ─────────────────────────────────────────────────────────────────
# TC-AUDIT-002 — Filter by event type
# ─────────────────────────────────────────────────────────────────


def test_tc_audit_002_event_type_uses_ilike_substring_pattern() -> None:
    """event_type filter is a CASE-INSENSITIVE substring match
    (ILIKE '%pattern%') — not equality. If this is changed to
    equality, "auth" would stop matching "auth.login" / "auth.logout"
    and the audit page filter UX silently breaks."""
    src = (REPO / "api" / "v1" / "audit.py").read_text(encoding="utf-8")
    assert "AuditLog.event_type.ilike(pattern)" in src
    assert 'pattern = f"%{event_type}%"' in src


# ─────────────────────────────────────────────────────────────────
# TC-AUDIT-003 — Pagination
# ─────────────────────────────────────────────────────────────────


def test_tc_audit_003_per_page_capped_at_100() -> None:
    """per_page is clamped to [1, 100]. Without the cap a request
    can ask for 10k rows and OOM the API process."""
    src = (REPO / "api" / "v1" / "audit.py").read_text(encoding="utf-8")
    assert "per_page = min(max(per_page, 1), 100)" in src


def test_tc_audit_003_page_one_minimum_enforced() -> None:
    """page < 1 is a 422, not silently coerced. Foundation #8
    false-green: silent coercion would let the UI think it
    received page 1's rows when it asked for page 0."""
    src = (REPO / "api" / "v1" / "audit.py").read_text(encoding="utf-8")
    assert 'raise HTTPException(422, "page must be >= 1")' in src


def test_tc_audit_003_results_ordered_desc_by_created_at() -> None:
    """Newest-first ordering is the contract — UI shows recent
    activity at the top. Reversing this would silently rotate
    the page contents under existing dashboards."""
    src = (REPO / "api" / "v1" / "audit.py").read_text(encoding="utf-8")
    assert "order_by(AuditLog.created_at.desc())" in src


# ─────────────────────────────────────────────────────────────────
# TC-AUDIT-004 — Export as CSV
# ─────────────────────────────────────────────────────────────────


def test_tc_audit_004_csv_export_uses_blob_and_text_csv_mime() -> None:
    """The UI builds the CSV client-side via Blob with mime
    'text/csv' and downloads as 'audit-log-{date}.csv'. Server
    is NOT involved — this is intentional so an auditor
    downloading the page they're viewing always gets the rows
    they actually see, not a re-query that might race with new
    inserts."""
    src = (REPO / "ui" / "src" / "pages" / "Audit.tsx").read_text(
        encoding="utf-8"
    )
    assert 'new Blob([csv], { type: "text/csv" })' in src
    assert 'audit-log-${datestamp}.csv' in src


# ─────────────────────────────────────────────────────────────────
# TC-AUDIT-005 — Export evidence package (JSON)
# ─────────────────────────────────────────────────────────────────


def test_tc_audit_005_evidence_json_export_filename_is_pinned() -> None:
    """The evidence-package JSON export uses filename
    'audit-evidence-{date}.json' — this naming is referenced in
    SOC-2 evidence-collection runbooks; renaming silently
    breaks the auditor handoff."""
    src = (REPO / "ui" / "src" / "pages" / "Audit.tsx").read_text(
        encoding="utf-8"
    )
    assert "audit-evidence-${datestamp}.json" in src


# ─────────────────────────────────────────────────────────────────
# TC-AUDIT-006 — Auditor role: read-only access (DB-level + RBAC)
# ─────────────────────────────────────────────────────────────────


def test_tc_audit_006_auditor_bypasses_domain_filter() -> None:
    """Auditor sees cross-domain entries; every other role gets
    domain-scoped results. The check is ``user_role != 'auditor'``;
    if the inversion drops, auditors silently lose visibility on
    cross-domain entries — a real compliance regression."""
    src = (REPO / "api" / "v1" / "audit.py").read_text(encoding="utf-8")
    assert 'user_role != "auditor"' in src


def test_tc_audit_006_audit_log_is_db_level_append_only() -> None:
    """The DB has a trigger that rejects UPDATE/DELETE on
    audit_log. Even if a route bug or an auditor session somehow
    tried to mutate a row, postgres rejects with
    ERRCODE='insufficient_privilege'. This is the deepest
    defense — never let it disappear."""
    src = (REPO / "core" / "database.py").read_text(encoding="utf-8")
    assert "audit_log_reject_mutation" in src
    assert "BEFORE UPDATE OR DELETE ON audit_log" in src
    assert "audit_log is append-only" in src


def test_tc_audit_006_compliance_evidence_endpoint_is_read_only() -> None:
    """Cross-pin: the /compliance/evidence-package endpoint that
    auditors read is a GET (not POST) — it cannot mutate
    state. Pin the verb so a future refactor can't promote it
    to POST without code review."""
    src = (REPO / "api" / "v1" / "compliance.py").read_text(encoding="utf-8")
    assert '@router.get("/compliance/evidence-package")' in src
