# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Relicensing to PolyForm Noncommercial 1.0.0 (2026-10-21).

Guards the change from Apache-2.0: the licence text and notices, every first-party SPDX
header, package metadata, the vendored third-party licences, and the wording that tells
existing users about earlier versions without claiming the earlier grant is revoked.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LICENSE_ID = "PolyForm-Noncommercial-1.0.0"
OWNER = "Orchestrum Technologies LLP"
CONTACT = "sanjeev@orchestrum.in"
RETIRED_HEADER = "SPDX-License-" + "Identifier: Apache-2.0"
EARLIER = "remain available under the Apache License 2.0 they were released with"
TAKEN_BACK = re.compile(r"revok|withdr[ae]w|terminat|cancel|no longer (?:valid|available)", re.I)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _flat(relative: str) -> str:
    return " ".join(_read(relative).split())


def test_license_is_the_official_polyform_text_with_required_notices() -> None:
    text = _read("LICENSE").replace("\r\n", "\n")
    lines = text.splitlines()
    assert lines[0] == f"Required Notice: Copyright 2026 {OWNER} (https://agenticorg.ai)"
    assert lines[1].startswith("Required Notice: Commercial use of this software requires a prior written agreement")
    assert CONTACT in lines[1]
    body = text.split("\n\n", 1)[1]
    assert body.startswith("# PolyForm Noncommercial License 1.0.0\n\n<https://polyformproject.org/licenses/noncommercial/1.0.0>")
    for heading in (
        "Acceptance", "Copyright License", "Distribution License", "Notices", "Changes and New Works License",
        "Patent License", "Noncommercial Purposes", "Personal Uses", "Noncommercial Organizations", "Fair Use",
        "No Other Rights", "Patent Defense", "Violations", "No Liability", "Definitions",
    ):
        assert f"\n## {heading}\n" in body, heading
    assert "within 32 days of receiving notice" in body
    assert "Apache" not in text


def test_package_copies_of_license_match_the_root_license() -> None:
    root = _read("LICENSE")
    for package in ("sdk", "sdk-ts", "mcp-server"):
        assert _read(f"{package}/LICENSE") == root, package


def test_no_first_party_file_keeps_the_retired_spdx_identifier() -> None:
    argv = ["git", "-C", str(ROOT), "grep", "-l", "-F", RETIRED_HEADER]
    result = subprocess.run(  # noqa: S603 - fixed git argv
        argv,  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    assert result.stdout.split() == []


def test_licence_header_check_requires_the_new_identifier() -> None:
    from scripts import check_license_headers

    assert check_license_headers.HEADER == f"SPDX-License-Identifier: {LICENSE_ID}"


def test_package_metadata_declares_the_new_licence() -> None:
    for manifest in ("pyproject.toml", "sdk/pyproject.toml"):
        project = tomllib.loads(_read(manifest))["project"]
        assert project["license"] == LICENSE_ID, manifest
        classifiers = project["classifiers"]
        assert not any("Apache" in item or "OSI Approved" in item for item in classifiers), manifest
    for manifest in ("ui/package.json", "sdk-ts/package.json", "mcp-server/package.json"):
        assert json.loads(_read(manifest))["license"] == LICENSE_ID, manifest
    for lock in ("ui/package-lock.json", "sdk-ts/package-lock.json", "mcp-server/package-lock.json"):
        assert json.loads(_read(lock))["packages"][""]["license"] == LICENSE_ID, lock


def test_vendored_third_party_material_keeps_its_apache_licence() -> None:
    apache = _read("LICENSES/Apache-2.0.txt")
    assert "Apache License" in apache
    assert "Version 2.0, January 2004" in apache
    assert "END OF TERMS AND CONDITIONS" in apache
    scope = json.loads(_read("licensing/scope.json"))
    assert scope["license_id"] == LICENSE_ID
    assert scope["effective_date"] == "2026-10-21"
    for entry in scope["third_party"]:
        assert (ROOT / entry["path"]).is_dir(), entry["path"]
        assert entry["license_id"] == "Apache-2.0"
    for readme in ("tests/fixtures/ucp/README.md", "core/commerce/acp/schemas/2026-04-17/README.md"):
        assert "LICENSES/Apache-2.0.txt" in _read(readme), readme
    notice = _flat("NOTICE")
    assert "tests/fixtures/ucp/" in notice
    assert "core/commerce/acp/schemas/" in notice


def test_commercial_licence_and_trademarks_state_the_owner_decision() -> None:
    commercial = _flat("COMMERCIAL-LICENSE.md")
    assert "Only self use is free." in commercial
    assert f"written agreement with {OWNER}" in commercial
    for use in ("Internal business use", "Hosting", "Resale", "Embedding", "Consulting delivery"):
        assert f"**{use}**" in commercial, use
    assert CONTACT in commercial
    trademarks = _flat("TRADEMARKS.md")
    names = '"AgenticOrg" and "Grantex", and the AgenticOrg and Grantex names and logos,'
    assert f"{names} are trademarks of **{OWNER}**" in trademarks
    assert "including versions published before 21 October 2026" in trademarks
    assert "prior written approval" in trademarks


def test_public_statements_name_the_change_date_and_earlier_versions_factually() -> None:
    surfaces = (
        "README.md",
        "NOTICE",
        "LICENSING.md",
        "COMMERCIAL-LICENSE.md",
        "docs/OWNERSHIP.md",
        "docs/terms-of-service.md",
        "docs/user-guide/licensing.md",
        "sdk/README.md",
        "sdk-ts/README.md",
        "mcp-server/README.md",
    )
    for surface in surfaces:
        text = _flat(surface)
        assert "PolyForm Noncommercial" in text, surface
        assert "21 October 2026" in text, surface
        assert EARLIER in text, surface
        # The Apache-2.0 grant on copies already released cannot be taken back; no sentence about
        # it may say otherwise.
        for sentence in re.split(r"(?<=[.;])\s+", text):
            if "Apache" in sentence:
                assert not TAKEN_BACK.search(sentence), (surface, sentence)


def test_readme_licence_section_sits_near_the_top() -> None:
    readme = _read("README.md")
    headings = [line for line in readme.splitlines() if line.startswith("## ")]
    assert headings[0] == "## Licence"
    section = _flat("README.md").split("## Licence", 1)[1].split("## ", 1)[0]
    assert "**AgenticOrg is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE)." in section
    for target in ("COMMERCIAL-LICENSE.md", "TRADEMARKS.md", f"mailto:{CONTACT}"):
        assert target in section, target


def test_contributing_has_a_contributor_licence_for_commercial_licensing() -> None:
    contributing = _flat("CONTRIBUTING.md")
    assert "## Contributor licence" in _read("CONTRIBUTING.md")
    assert "You keep the copyright in your contribution." in contributing
    assert "including commercial licence agreements" in contributing
    assert "licensed under the Apache License 2.0." not in contributing


def test_public_site_sources_no_longer_claim_open_source_or_apache() -> None:
    stale = re.compile(r"open[- ]source (?:enterprise|platform)|Apache-2\.0 (?:enterprise|licensed)", re.I)
    site_sources = (
        "ui/index.html",
        "ui/src/content/publicSite.json",
        "ui/public/llms.txt",
        "ui/scripts/generate-llms.mjs",
    )
    for surface in site_sources:
        assert not stale.search(_read(surface)), surface
    assert '"license": "https://polyformproject.org/licenses/noncommercial/1.0.0"' in _read("ui/index.html")
    assert "Apache" not in _read("ui/public/og-image.svg")
