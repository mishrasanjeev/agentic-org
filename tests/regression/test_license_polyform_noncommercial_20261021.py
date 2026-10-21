# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Relicensing the platform to PolyForm Noncommercial 1.0.0 (2026-10-21).

Guards the change from Apache-2.0: the licence text and notices, the SPDX header of every
platform file, the SDK and MCP server carve-out (which stays Apache-2.0), package metadata,
the vendored third-party licences, and the wording that tells existing users about earlier
versions without claiming the earlier grant is revoked.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LICENSE_ID = "PolyForm-Noncommercial-1.0.0"
SDK_LICENSE_ID = "Apache-2.0"
SDK_DIRS = ("sdk", "sdk-ts", "mcp-server")
OWNER = "Orchestrum Technologies LLP"
CONTACT = "sanjeev@orchestrum.in"
APACHE_HEADER = "SPDX-License-" + "Identifier: Apache-2.0"
EARLIER = "remain available under the Apache License 2.0 they were released with"
TAKEN_BACK = re.compile(r"revok|withdr[ae]w|terminat|cancel|no longer (?:valid|available)", re.I)
SDK_FREE = re.compile(r"Apache License 2\.0[^.]{0,80}free for any use[^.]{0,20}commercial", re.I)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _flat(relative: str) -> str:
    return " ".join(_read(relative).split())


def _git_grep(*args: str) -> list[str]:
    argv = ["git", "-C", str(ROOT), "grep", "-l", *args]
    result = subprocess.run(  # noqa: S603 - fixed git argv
        argv,  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    return result.stdout.split()


def test_license_is_the_official_polyform_text_with_required_notices() -> None:
    text = _read("LICENSE").replace("\r\n", "\n")
    lines = text.splitlines()
    assert lines[0] == f"Required Notice: Copyright 2026 {OWNER} (https://agenticorg.ai)"
    assert lines[1].startswith("Required Notice: Commercial use of this software requires a prior written agreement")
    assert CONTACT in lines[1]
    assert lines[2].startswith("Required Notice: The sdk/, sdk-ts/ and mcp-server/ directories are not covered")
    assert "Apache License 2.0" in lines[2]
    body = text.split("\n\n", 1)[1]
    assert body.startswith("# PolyForm Noncommercial License 1.0.0\n\n<https://polyformproject.org/licenses/noncommercial/1.0.0>")
    for heading in (
        "Acceptance", "Copyright License", "Distribution License", "Notices", "Changes and New Works License",
        "Patent License", "Noncommercial Purposes", "Personal Uses", "Noncommercial Organizations", "Fair Use",
        "No Other Rights", "Patent Defense", "Violations", "No Liability", "Definitions",
    ):
        assert f"\n## {heading}\n" in body, heading
    assert "within 32 days of receiving notice" in body
    assert "Apache" not in body


def test_sdk_and_mcp_server_packages_stay_apache() -> None:
    apache = _read("LICENSES/Apache-2.0.txt")
    for package in SDK_DIRS:
        assert _read(f"{package}/LICENSE") == apache, package
    sdk_project = tomllib.loads(_read("sdk/pyproject.toml"))["project"]
    assert sdk_project["license"] == SDK_LICENSE_ID
    assert "License :: OSI Approved :: Apache Software License" in sdk_project["classifiers"]
    for package in ("sdk-ts", "mcp-server"):
        assert json.loads(_read(f"{package}/package.json"))["license"] == SDK_LICENSE_ID, package
        assert json.loads(_read(f"{package}/package-lock.json"))["packages"][""]["license"] == SDK_LICENSE_ID
    for package in SDK_DIRS:
        readme = _flat(f"{package}/README.md")
        assert "Apache License 2.0" in readme, package
        assert "open source and free for any use, including commercial use" in readme, package
        assert "PolyForm" in readme, package  # names the separate platform licence


def test_spdx_headers_follow_the_directory_licence() -> None:
    # A header is a comment line that starts with the SPDX tag; Markdown examples are not headers.
    apache_header = r"^\s*(#|//|/\*)\s*" + re.escape(APACHE_HEADER)
    platform_header = r"^\s*(#|//|/\*)\s*SPDX-License-Identifier: " + re.escape(LICENSE_ID)
    outside_sdks = [f":(exclude){package}/" for package in SDK_DIRS] + [":(exclude)*.md"]
    assert _git_grep("-E", apache_header, "--", ".", *outside_sdks) == []
    assert _git_grep("-E", platform_header, "--", *SDK_DIRS) == []
    assert set(_git_grep("-E", apache_header, "--", *SDK_DIRS)) >= {"mcp-server/src/ucp.ts"}


def test_licence_header_check_requires_the_directory_identifier() -> None:
    from scripts import check_license_headers as check

    assert check.HEADER == f"SPDX-License-Identifier: {LICENSE_ID}"
    assert check.SDK_HEADER == f"SPDX-License-Identifier: {SDK_LICENSE_ID}"
    assert check.required_header("core/x.py") == check.HEADER
    for package in SDK_DIRS:
        assert check.required_header(f"{package}/src/x.ts") == check.SDK_HEADER


def test_platform_package_metadata_declares_the_new_licence() -> None:
    project = tomllib.loads(_read("pyproject.toml"))["project"]
    # The root wheel also carries the Apache-2.0 Python SDK and vendored schemas.
    assert project["license"] == f"{LICENSE_ID} AND Apache-2.0"
    assert "LICENSES/Apache-2.0.txt" in project["license-files"]
    assert not any("OSI Approved" in item for item in project["classifiers"])
    assert json.loads(_read("ui/package.json"))["license"] == LICENSE_ID
    assert json.loads(_read("ui/package-lock.json"))["packages"][""]["license"] == LICENSE_ID


def test_vendored_third_party_material_keeps_its_apache_licence() -> None:
    apache = _read("LICENSES/Apache-2.0.txt")
    assert "Apache License" in apache
    assert "Version 2.0, January 2004" in apache
    assert "END OF TERMS AND CONDITIONS" in apache
    scope = json.loads(_read("licensing/scope.json"))
    assert scope["license_id"] == LICENSE_ID
    assert scope["effective_date"] == "2026-10-21"
    assert [entry["path"] for entry in scope["apache_2_0_packages"]] == [f"{package}/" for package in SDK_DIRS]
    for entry in scope["third_party"] + scope["apache_2_0_packages"]:
        assert (ROOT / entry["path"]).is_dir(), entry["path"]
        assert entry["license_id"] == "Apache-2.0"
        assert "Apache License" in _read(entry["license_text"])
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
    assert "**SDK carve-out:**" in commercial
    trademarks = _flat("TRADEMARKS.md")
    names = '"AgenticOrg" and "Grantex", and the AgenticOrg and Grantex names and logos,'
    assert f"{names} are trademarks of **{OWNER}**" in trademarks
    assert "including versions published before 21 October 2026" in trademarks
    assert "prior written approval" in trademarks


def test_public_statements_name_the_split_change_date_and_earlier_versions_factually() -> None:
    surfaces = (
        "README.md",
        "NOTICE",
        "LICENSING.md",
        "COMMERCIAL-LICENSE.md",
        "docs/OWNERSHIP.md",
        "docs/terms-of-service.md",
        "docs/user-guide/licensing.md",
    )
    for surface in surfaces:
        text = _flat(surface)
        assert "PolyForm Noncommercial" in text, surface
        assert "21 October 2026" in text, surface
        assert EARLIER in text, surface
        assert "sdk" in text.lower() and "MCP server" in text.replace("mcp-server", "MCP server"), surface
        assert SDK_FREE.search(text) or "free for any use" in text, surface
        # The Apache-2.0 grant on copies already released cannot be taken back; no sentence about
        # it may say otherwise.
        for sentence in re.split(r"(?<=[.;])\s+", text):
            if "Apache" in sentence:
                assert not TAKEN_BACK.search(sentence), (surface, sentence)


def test_licence_scope_table_maps_directories_to_licences() -> None:
    for surface in ("README.md", "LICENSING.md", "COMMERCIAL-LICENSE.md"):
        text = _read(surface)
        assert "| Directory | Licence |" in text, surface
        for package in SDK_DIRS:
            assert re.search(rf"\| `{package}/`[^|]*\| Apache License 2\.0", text), (surface, package)
        assert re.search(r"\| Everything else: the AgenticOrg platform[^|]*\| PolyForm Noncommercial", text), surface


def test_readme_licence_section_sits_near_the_top() -> None:
    readme = _read("README.md")
    headings = [line for line in readme.splitlines() if line.startswith("## ")]
    assert headings[0] == "## Licence"
    section = _flat("README.md").split("## Licence", 1)[1].split("## ", 1)[0]
    lead = "**The AgenticOrg platform is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE)."
    assert lead in section
    assert "are open source under the Apache License 2.0, free for any use including commercial.**" in section
    for target in ("COMMERCIAL-LICENSE.md", "TRADEMARKS.md", f"mailto:{CONTACT}"):
        assert target in section, target


def test_contributing_has_a_contributor_licence_for_commercial_licensing() -> None:
    contributing = _flat("CONTRIBUTING.md")
    assert "## Contributor licence" in _read("CONTRIBUTING.md")
    assert "You keep the copyright in your contribution." in contributing
    assert "including commercial licence agreements" in contributing
    sdk_terms = "Contributions to `sdk/`, `sdk-ts/` and `mcp-server/` are also licensed to everyone under the"
    assert sdk_terms in contributing


def test_public_site_sources_state_the_split() -> None:
    stale = re.compile(r"open[- ]source (?:enterprise|platform)|Apache-2\.0 (?:enterprise|licensed)", re.I)
    site_sources = (
        "ui/index.html",
        "ui/src/content/publicSite.json",
        "ui/public/llms.txt",
        "ui/scripts/generate-llms.mjs",
    )
    for surface in site_sources:
        assert not stale.search(_read(surface)), surface
    llms = _read("ui/public/llms.txt")
    assert "- License: AgenticOrg platform: PolyForm Noncommercial License 1.0.0." in llms
    assert "MCP server are open source under the Apache License 2.0" in llms
    assert '"license": "https://polyformproject.org/licenses/noncommercial/1.0.0"' in _read("ui/index.html")
    assert "Apache" not in _read("ui/public/og-image.svg")
    footer = _read("ui/src/components/ProductOwnership.tsx")
    assert "SDKs and MCP server: Apache-2.0, free for any use." in footer
