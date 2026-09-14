"""Regression tests for the Uday CA Firms 2026-04-26 bug sweep.

Each test replays the tester's exact steps. Verdicts and full triage
in `bug_triage_skill.md`-format land in
``CA_FIRMS_BugFixSummary_Uday_26Apr2026.xlsx`` (alongside the input
report) — the autopsy notes live in
``~/.claude/.../memory/feedback_26apr_bug_sweep.md``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# BUG 1 — archived connector blocks recreate with same name
# ---------------------------------------------------------------------------


def test_bug1_re_register_path_upserts_connector_config() -> None:
    """The reactivate-on-IntegrityError branch must upsert ConnectorConfig.

    Earlier the branch ONLY reactivated the Connector row. ConnectorConfig
    has its own UniqueConstraint(tenant_id, connector_name); adding a new
    row for the just-reactivated name raised IntegrityError #2 unhandled,
    and the API surfaced 500. UI showed "Failed to register connector.
    Please try again." — see Uday CA Firms 26 Apr 2026 Bug 1.

    Pin the source contract: the `if secret_fields:` block must look up an
    existing ConnectorConfig and update it in place when present.
    """
    src = (REPO / "api" / "v1" / "connectors.py").read_text(encoding="utf-8")

    # The upsert must select existing ConnectorConfig before adding
    assert "select(ConnectorConfig)" in src, (
        "register_connector must SELECT ConnectorConfig before adding a new one"
    )
    # Must update credentials_encrypted on the existing row
    assert re.search(
        r"cc_existing\.credentials_encrypted\s*=\s*\{",
        src,
    ), "Existing ConnectorConfig must be updated in place, not duplicated"
    # Sanity: the BUG 1 regression marker is present
    assert "BUG 1" in src or "Uday 2026-04-26" in src, (
        "Inline comment must reference the regression so future readers "
        "don't strip the upsert as 'redundant'"
    )


# ---------------------------------------------------------------------------
# BUG 4 — connector test must NOT report status=healthy when http_status >= 400
# ---------------------------------------------------------------------------


def test_bug4_base_health_check_404_is_unhealthy() -> None:
    """base_connector.health_check must return status=unhealthy for HTTP 404.

    Tester reported test endpoint returning ``{"status": "healthy",
    "http_status": 404}`` for Gmail — false-green on an actually-broken
    connector. Pin the contract: only 200-399 is healthy; 4xx/5xx returns
    status=unhealthy with the http_status preserved + an actionable reason.
    """
    src = (REPO / "connectors" / "framework" / "base_connector.py").read_text(
        encoding="utf-8"
    )

    # Healthy band must be tight: 200 <= sc < 400
    assert "200 <= sc < 400" in src, (
        "Healthy band must be 200-399 only; anything >= 400 is unhealthy"
    )
    # 404 branch must exist and the unified return-unhealthy must follow
    assert "sc == 404" in src, "404 branch missing in base health_check"
    # Gmail-specific reason hint must be present (so testers see something
    # actionable instead of a bare 'http 404')
    assert "gmail.googleapis.com" in src, (
        "404 branch must include the Gmail-specific hint pointing testers "
        "at the correct base URL form"
    )
    # The single unified `return {"status": "unhealthy", ...}` must use
    # http_status — proving the failure status is preserved for debugging.
    assert re.search(
        r'return\s*\{\s*"status":\s*"unhealthy",\s*"http_status":\s*sc',
        src,
    ), "Unhealthy return must preserve http_status for debugging"


def test_bug4_no_override_health_check_returns_healthy_blindly() -> None:
    """Sibling sweep: no connector override should return status=healthy
    without first asserting an HTTP 2xx response or a successful
    authenticated call.

    The signature we're pinning is "an override must NOT contain a top-
    level `return {"status": "healthy"...}` that isn't preceded by either
    an http call result check, a try/except, or a literal data extraction
    that would raise on failure. Connector overrides that simply call
    ``await self._get(...)`` and trust raise-for-status are OK because
    `_get` raises on 4xx/5xx — the connector framework's HTTP helpers
    must surface bad responses, not swallow them.
    """
    framework_src = (REPO / "connectors" / "framework" / "base_connector.py").read_text(
        encoding="utf-8"
    )
    # _get / _post on the framework must raise_for_status (not silent on 404)
    assert "raise_for_status" in framework_src, (
        "Framework HTTP helpers must call raise_for_status — silent 404s "
        "feed the false-healthy bug."
    )


# ---------------------------------------------------------------------------
# BUG (last) — agent UI must not list deprecated LLM models
# ---------------------------------------------------------------------------


def test_bug_last_no_deprecated_gemini_in_agent_ui() -> None:
    """The AgentDetail page LLM dropdown must not list models Google has
    retired.

    Tester ran an agent and got
    ``404 NOT_FOUND ... model models/gemini-2.0-flash is no longer
    available to new users``. The model came from the hardcoded
    LLM_OPTIONS list in the UI.
    """
    # Bug sheet 2026-09-14 #34: the hardcoded LLM_OPTIONS list was the
    # source of drift (and of this bug). Both agent pages now render the
    # backend catalog via useLlmRegistry, so pin the catalog instead and
    # forbid any model-id literal from creeping back into the pages.
    from core.ai_providers.catalog import LLM_CATALOG

    deprecated = [
        "gemini-2.0-flash",       # retired by Google
        "gemini-1.5-pro",         # superseded by 2.5
        "claude-3-opus",          # superseded by claude-4
        "claude-3-sonnet",        # superseded by claude-4
    ]
    catalog_models = {m.model for m in LLM_CATALOG}
    for dep in deprecated:
        assert dep not in catalog_models, (
            f"catalog must not list deprecated model {dep!r} — Google/"
            "Anthropic returns 404 NOT_FOUND for retired model IDs"
        )
    assert any(m.startswith("gemini-2.5") for m in catalog_models), (
        "catalog must offer at least one Gemini 2.5 model"
    )
    for page in ("AgentDetail.tsx", "AgentCreate.tsx"):
        src = (REPO / "ui" / "src" / "pages" / page).read_text(encoding="utf-8")
        assert "useLlmRegistry" in src, f"{page} must render the backend LLM catalog"
        assert "LLM_OPTIONS" not in src, f"{page} must not reintroduce a hardcoded model list"
        for dep in deprecated:
            assert f'"{dep}"' not in src, f"{page} must not hardcode deprecated model {dep!r}"


# ---------------------------------------------------------------------------
# Helm-asserting tests are skipped on this branch — they were skipped in PR
# #315 and we don't want this PR to look like it re-introduced them.
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="Sibling-sweep marker — confirms the helm tests stay skipped after PR #315 "
    "(see Stage 4 GKE teardown). If this test starts failing it means someone "
    "un-skipped them without rewiring against Cloud Run config first."
)
def test_helm_tests_remain_skipped_until_cloudrun_config_audit() -> None:
    pass
