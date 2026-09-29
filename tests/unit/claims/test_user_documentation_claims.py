# SPDX-License-Identifier: Apache-2.0
"""Keep published guide text within the existing public-claims boundary."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.claims import scan_surfaces

ROOT = Path(__file__).resolve().parents[3]
REVIEWED_AT = datetime(2026, 9, 29, tzinfo=UTC)


@pytest.mark.parametrize("surface", ["ui/public/llms.txt", "ui/public/llms-full.txt"])
def test_published_manual_passes_public_claim_governance(surface: str) -> None:
    report = scan_surfaces(
        ROOT,
        ROOT / "config/public_claim_registry.json",
        paths=[surface],
        now=REVIEWED_AT,
    )
    assert report.valid, report.issues


def test_preflight_enforces_claims_after_building_public_assets() -> None:
    script = (ROOT / "scripts/preflight.sh").read_text(encoding="utf-8")
    build = 'run_step "ui build"'
    claims = 'run_step "public claims"          python scripts/lint_public_claims.py'
    assert claims in script
    assert script.index(claims) > script.index(build)


def test_preflight_checks_tracked_llm_artifacts_before_public_claims() -> None:
    script = (ROOT / "scripts/preflight.sh").read_text(encoding="utf-8")
    build = 'run_step "ui build"'
    tracked = 'run_step "ui tracked LLM artifacts" ui_llms_artifact_sync'
    claims = 'run_step "public claims"'
    assert 'node scripts/generate-llms.mjs --check' in script
    assert script.index(build) < script.index(tracked) < script.index(claims)
