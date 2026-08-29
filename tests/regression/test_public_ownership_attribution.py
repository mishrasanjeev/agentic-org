"""Regression guards for public AgenticOrg ownership and contact attribution."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OWNER = "Orchestrum Technologies LLP"
INVENTOR_OWNER = "Sanjeev Kumar"
PRIMARY_EMAIL = "sanjeev@orchestrum.in"
SECONDARY_EMAIL = "mishra.sanjeev@gmail.com"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_canonical_ownership_documents_are_consistent() -> None:
    paths = (
        ROOT / "README.md",
        ROOT / "NOTICE",
        ROOT / "docs" / "README.md",
        ROOT / "docs" / "OWNERSHIP.md",
        ROOT / "docs" / "PRODUCT_STATUS.md",
        ROOT / "docs" / "privacy-policy.md",
        ROOT / "docs" / "terms-of-service.md",
        ROOT / "docs" / "oacp" / "README.md",
        ROOT / "sdk" / "README.md",
        ROOT / "sdk-ts" / "README.md",
        ROOT / "mcp-server" / "README.md",
    )
    for path in paths:
        text = _read(path)
        normalized = " ".join(text.split())
        assert OWNER in normalized, f"missing product owner in {path.relative_to(ROOT)}"
        assert INVENTOR_OWNER in normalized, f"missing inventor/owner in {path.relative_to(ROOT)}"

    ownership = _read(ROOT / "docs" / "OWNERSHIP.md")
    assert PRIMARY_EMAIL in ownership
    assert SECONDARY_EMAIL in ownership


def test_published_package_metadata_names_the_inventor_and_company() -> None:
    root_project = _read(ROOT / "pyproject.toml")
    sdk_project = _read(ROOT / "sdk" / "pyproject.toml")
    for project in (root_project, sdk_project):
        assert f'name = "{INVENTOR_OWNER}"' in project
        assert f'name = "{OWNER}"' in project
        assert f'email = "{PRIMARY_EMAIL}"' in project

    for path in (ROOT / "sdk-ts" / "package.json", ROOT / "mcp-server" / "package.json"):
        metadata = json.loads(_read(path))
        assert metadata["author"] == {"name": INVENTOR_OWNER, "email": PRIMARY_EMAIL}
        assert metadata["maintainers"] == [{"name": OWNER, "email": PRIMARY_EMAIL}]


def test_public_site_metadata_names_the_legal_owner_and_contacts() -> None:
    manifest = json.loads(_read(ROOT / "ui" / "src" / "content" / "publicSite.json"))
    assert manifest["site"]["name"] == "AgenticOrg"
    assert manifest["site"]["legalName"] == OWNER
    assert manifest["site"]["inventorOwner"] == INVENTOR_OWNER
    assert manifest["site"]["email"] == PRIMARY_EMAIL
    assert manifest["site"]["secondaryEmail"] == SECONDARY_EMAIL


def test_answer_engine_document_preserves_mailto_contacts() -> None:
    short = _read(ROOT / "ui" / "public" / "llms.txt")
    full = _read(ROOT / "ui" / "public" / "llms-full.txt")
    for value in (OWNER, INVENTOR_OWNER, PRIMARY_EMAIL, SECONDARY_EMAIL):
        assert value in short
    assert f"mailto:{PRIMARY_EMAIL}" in full
    assert f"mailto:{SECONDARY_EMAIL}" in full
    assert "/blob/main/mailto:" not in full


def test_every_public_page_footer_uses_the_canonical_ownership_component() -> None:
    page_root = ROOT / "ui" / "src" / "pages"
    footer_pages = [path for path in page_root.rglob("*.tsx") if "<footer" in _read(path)]
    assert footer_pages
    for path in footer_pages:
        assert "ProductOwnership" in _read(path), (
            f"public footer must use canonical ownership disclosure: {path.relative_to(ROOT)}"
        )


def test_current_public_surfaces_do_not_restore_the_retired_contact() -> None:
    paths = (
        ROOT / "README.md",
        ROOT / "CODE_OF_CONDUCT.md",
        ROOT / "SECURITY.md",
        ROOT / "ui" / "src",
        ROOT / "ui" / "public",
        ROOT / "docs" / "README.md",
        ROOT / "docs" / "OWNERSHIP.md",
        ROOT / "docs" / "PRODUCT_STATUS.md",
        ROOT / "docs" / "privacy-policy.md",
        ROOT / "docs" / "terms-of-service.md",
        ROOT / "docs" / "DPDP_ACT.md",
        ROOT / "docs" / "GDPR.md",
        ROOT / "docs" / "HIPAA.md",
        ROOT / "docs" / "SLA.md",
        ROOT / "docs" / "VULNERABILITY_DISCLOSURE.md",
    )
    retired = "sanjeev@agenticorg.ai"
    for path in paths:
        if path.is_dir():
            files = [item for item in path.rglob("*") if item.is_file()]
        else:
            files = [path]
        for file_path in files:
            if file_path.suffix.lower() not in {".md", ".ts", ".tsx", ".json", ".txt"}:
                continue
            assert retired not in _read(file_path), (
                f"retired public contact found in {file_path.relative_to(ROOT)}"
            )
