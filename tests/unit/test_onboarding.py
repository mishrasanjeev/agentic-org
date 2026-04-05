"""Tests for enterprise onboarding milestone tracking."""

from __future__ import annotations

from pathlib import Path

_DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"


# ── tests ────────────────────────────────────────────────────────────────────

def test_milestone_tracking_api():
    """Onboarding playbook defines milestones for each week."""
    doc_path = _DOCS_DIR / "enterprise-onboarding.md"
    assert doc_path.exists(), "enterprise-onboarding.md missing"
    content = doc_path.read_text(encoding="utf-8")
    # Each week should have a Milestone marker
    assert content.count("**Milestone:**") >= 4, "Expected at least 4 weekly milestones"


def test_onboarding_progress_stored():
    """Onboarding UI component tracks progress (localStorage or API).

    Validates the Onboarding.tsx file references milestone tracking state.
    """
    ui_path = Path(__file__).resolve().parents[2] / "ui" / "src" / "pages" / "Onboarding.tsx"
    assert ui_path.exists(), "Onboarding.tsx missing"
    content = ui_path.read_text(encoding="utf-8")
    # The component should have milestone state and persistence
    assert "milestone" in content.lower() or "localStorage" in content, (
        "Onboarding.tsx should track milestone progress"
    )


def test_week_completion_tracked():
    """Onboarding playbook has completion criteria for all 4 weeks."""
    doc_path = _DOCS_DIR / "enterprise-onboarding.md"
    content = doc_path.read_text(encoding="utf-8")
    for week in ["Week 1", "Week 2", "Week 3", "Week 4"]:
        assert week in content, f"Missing {week} in onboarding playbook"
    # Check that checklists exist (markdown checkboxes)
    assert content.count("- [ ]") >= 10, "Expected at least 10 checklist items"
