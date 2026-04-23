"""Regression tests for the 23-Apr-2026 QA sweep.

Pins the contracts the Aishwarya + Uday bug sheets asked for:
- TC_002: Knowledge Base search uses extractApiError, not a canned
  "API offline" message; a zero-result response shows a dedicated
  no-results state.
- TC_003 + TC_004: Schemas page exposes name/version/description form
  fields and an explicit Save/Create button wired to
  POST/PUT /schemas.
- TC_005: Connectors page no longer positions the three action
  buttons absolutely on top of the card content.
- TC_006: connector test endpoint falls back to the per-company GSTN
  Credential Vault for the GSTN connector.
- TC_007: connector test surfaces specific error hints (401/SSL/DNS/
  timeout/token-expired) instead of "Connector test failed".
- TC_008: basic auth uses username + password keys end-to-end; the
  Edit screen renders a Username input alongside Password.
- Uday Bug 1/2: shadow tab target defaults to 10 with a Samples
  generated / Promotion target split; loop emits per-sample status.
- Uday connector-delete: POST /connectors reactivates the
  soft-deleted twin instead of 409.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# TC_002 — Knowledge Base search surfaces real errors + no-results state
# ---------------------------------------------------------------------------


def test_tc_002_knowledge_base_uses_extract_api_error() -> None:
    src = _read("ui/src/pages/KnowledgeBase.tsx")
    assert "extractApiError" in src, (
        "KnowledgeBase must import extractApiError; previously the "
        "catch block showed a blanket 'API offline' message"
    )
    # The old rendered fallback (arg to setSearchResults) must be gone.
    # Code comments that document the history are fine to keep.
    assert 'setSearchResults(["(Search unavailable' not in src, (
        "The old canned 'Search unavailable — API offline' array item "
        "must be removed — it misled testers into filing bugs against "
        "a working search"
    )
    # The new error-state bucket must be rendered
    assert "setSearchError" in src
    assert "data-testid=\"kb-search-error\"" in src


# ---------------------------------------------------------------------------
# TC_003 + TC_004 — Schema create + edit have Save/Create buttons
# ---------------------------------------------------------------------------


def test_tc_003_tc_004_schema_form_has_save_button() -> None:
    src = _read("ui/src/pages/Schemas.tsx")
    assert "handleSaveSchema" in src, (
        "Schemas page must wire a save handler for both Create and Edit"
    )
    assert "data-testid=\"schema-save-button\"" in src
    # Name + version + description inputs must exist so the payload
    # matches SchemaCreate on the backend
    assert "id=\"schema-name\"" in src
    assert "id=\"schema-version\"" in src
    assert "id=\"schema-description\"" in src
    # Must POST for new and PUT for edit
    assert "api.post(\"/schemas\"" in src
    assert "api.put(" in src and "/schemas/" in src


# ---------------------------------------------------------------------------
# TC_005 — Connectors page action buttons no longer absolutely positioned
# ---------------------------------------------------------------------------


def test_tc_005_connectors_no_absolute_action_buttons() -> None:
    src = _read("ui/src/pages/Connectors.tsx")
    # The old structure wrapped the ConnectorCard in a relative
    # container with an `absolute bottom-3 right-3` button bar — that
    # is what caused the overlap. The replacement uses a flex column.
    assert "absolute bottom-3 right-3" not in src, (
        "Action buttons must no longer use absolute positioning over "
        "the card content (TC_005 overlap fix)"
    )
    # Responsive grid: at least one breakpoint below lg
    assert re.search(r"grid-cols-1\s+sm:grid-cols-2\s+lg:grid-cols-3", src)


# ---------------------------------------------------------------------------
# TC_006 — connector test bridges GSTN Credential Vault
# ---------------------------------------------------------------------------


def test_tc_006_connector_test_bridges_gstn_vault() -> None:
    src = _read("api/v1/connectors.py")
    assert "GSTNCredential" in src, (
        "test_connector must try the per-company GSTN Credential Vault "
        "for the GSTN connector when no ConnectorConfig rows exist"
    )
    assert "password_encrypted" in src
    assert "is_active" in src


# ---------------------------------------------------------------------------
# TC_007 — connector test emits specific error hints
# ---------------------------------------------------------------------------


def test_tc_007_connector_test_has_specific_error_hints() -> None:
    src = _read("api/v1/connectors.py")
    # The route must discriminate between common failure classes
    # rather than returning a single "Connector test failed" blob.
    for needle in ("Invalid credentials", "SSL", "DNS", "timed out", "token"):
        assert needle.lower() in src.lower(), (
            f"connector test error hints must include a {needle!r} branch"
        )
    # The generic "Connector test failed" string is no longer the only
    # value the route can return — we allow it nowhere except inside a
    # comment describing history.
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == "Connector test failed":
                raise AssertionError(
                    "The generic 'Connector test failed' literal must be "
                    "removed in favor of discriminated hints (TC_007)"
                )


# ---------------------------------------------------------------------------
# TC_008 — basic auth field parity between Create and Edit
# ---------------------------------------------------------------------------


def test_tc_008_basic_auth_uses_username_password_keys() -> None:
    """Create + Edit + buildBasicAuthConfig use the same key names."""
    create_src = _read("ui/src/pages/ConnectorCreate.tsx")
    consts_src = _read("ui/src/lib/connector-constants.ts")
    detail_src = _read("ui/src/pages/ConnectorDetail.tsx")

    # Create now uses username + password (not api_key + api_secret)
    assert 'key: "username"' in create_src
    assert 'key: "password"' in create_src
    assert 'key: "api_key", label: "Username"' not in create_src, (
        "Create form must not route Username into api_key anymore"
    )

    # connector-constants exposes a basic-auth helper
    assert "buildBasicAuthConfig" in consts_src
    assert "username" in consts_src and "password" in consts_src

    # Edit screen renders an explicit Username input for basic auth
    assert "buildBasicAuthConfig" in detail_src
    assert "basicUsername" in detail_src
    assert re.search(r"authType\s*===\s*\"basic\"", detail_src), (
        "Edit screen must branch on basic auth to render the Username "
        "input alongside Password"
    )


# ---------------------------------------------------------------------------
# Uday connector delete — reregistration revives soft-deleted twin
# ---------------------------------------------------------------------------


def test_uday_connector_soft_delete_reregistration() -> None:
    src = _read("api/v1/connectors.py")
    # The POST /connectors route must check for an existing
    # soft-deleted row with the same name and reactivate it in place.
    assert re.search(r"existing\.status\s*=\s*\"active\"", src), (
        "POST /connectors must reactivate a soft-deleted twin instead "
        "of 409-ing on the same name"
    )
    # List must filter out deleted entries by default
    assert "!= \"deleted\"" in src or "!= 'deleted'" in src


# ---------------------------------------------------------------------------
# Uday Bug 1 + 2 — shadow tab defaults, display split, sequential loop
# ---------------------------------------------------------------------------


def test_uday_shadow_tab_defaults_and_display() -> None:
    agent_detail = _read("ui/src/pages/AgentDetail.tsx")
    # Target default is 10 (not 20) in the UI fallback
    assert "?? 10" in agent_detail
    # Display shows both the generated count AND the target
    assert "Samples generated" in agent_detail
    assert "Promotion target" in agent_detail
    # Loop emits a per-sample status line so testers see sequential progress
    assert "Generating sample" in agent_detail
    assert "sequential_index" in agent_detail

    # Backend default dropped from 20 to 10
    agent_model = _read("core/models/agent.py")
    assert "shadow_min_samples" in agent_model
    # The whole mapped_column(...) expression lives on a single line.
    # Extract that line and assert both the column name and default.
    mapped_col_lines = [
        line
        for line in agent_model.splitlines()
        if "shadow_min_samples" in line and "mapped_column" in line
    ]
    assert mapped_col_lines, "shadow_min_samples mapped_column line not found"
    assert any("default=10" in line for line in mapped_col_lines), (
        "shadow_min_samples ORM default must be 10 per Uday 2026-04-23 "
        "Bug 1 — existing rows keep their admin-set value"
    )
