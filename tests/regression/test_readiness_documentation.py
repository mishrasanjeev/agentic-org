"""Regression guards for the cross-domain readiness documentation program."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
READINESS = ROOT / "docs" / "readiness"
CANONICAL_FILES = (
    READINESS / "README.md",
    READINESS / "PROGRAM_MEMORY.md",
    READINESS / "GAP_ANALYSIS.md",
    READINESS / "DOMAIN_READINESS_STANDARD.md",
    READINESS / "CAPABILITY_READINESS_REGISTER.md",
    READINESS / "BUILD_ROADMAP.md",
    READINESS / "LANDING_AND_DOCUMENTATION_BLUEPRINT.md",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_canonical_readiness_package_exists_and_is_linked() -> None:
    for path in CANONICAL_FILES:
        assert path.is_file(), f"missing canonical readiness document: {path}"

    root_readme = _read(ROOT / "README.md")
    hub = _read(READINESS / "README.md")
    for name in (
        "GAP_ANALYSIS.md",
        "DOMAIN_READINESS_STANDARD.md",
        "CAPABILITY_READINESS_REGISTER.md",
        "BUILD_ROADMAP.md",
        "LANDING_AND_DOCUMENTATION_BLUEPRINT.md",
        "PROGRAM_MEMORY.md",
    ):
        assert name in root_readme or name == "PROGRAM_MEMORY.md"
        assert name in hub


def test_local_markdown_links_resolve() -> None:
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
    failures: list[str] = []

    for path in (*CANONICAL_FILES, ROOT / "README.md", ROOT / "ROADMAP.md"):
        for raw_target in link_pattern.findall(_read(path)):
            target = raw_target.strip().strip("<>").split("#", 1)[0]
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                failures.append(f"{path.relative_to(ROOT)} -> {raw_target}")

    assert not failures, "broken local links:\n" + "\n".join(failures)


def test_mermaid_blocks_are_closed_and_have_diagram_types() -> None:
    diagrams = 0
    failures: list[str] = []

    for path in (*CANONICAL_FILES, ROOT / "README.md"):
        lines = _read(path).splitlines()
        in_mermaid = False
        body: list[str] = []
        for line_number, line in enumerate(lines, start=1):
            if line.strip() == "```mermaid":
                if in_mermaid:
                    failures.append(f"{path.name}:{line_number}: nested Mermaid fence")
                in_mermaid = True
                body = []
            elif in_mermaid and line.strip() == "```":
                diagrams += 1
                first = next((item.strip() for item in body if item.strip()), "")
                if not first.startswith(("flowchart", "sequenceDiagram", "stateDiagram")):
                    failures.append(f"{path.name}:{line_number}: missing supported diagram type")
                in_mermaid = False
            elif in_mermaid:
                body.append(line)
        if in_mermaid:
            failures.append(f"{path.name}: unclosed Mermaid fence")

    assert diagrams >= 12, f"expected a visual workflow set, found {diagrams} diagrams"
    assert not failures, "\n".join(failures)


def test_capability_register_covers_every_requested_domain() -> None:
    register = _read(READINESS / "CAPABILITY_READINESS_REGISTER.md")
    expected_minimums = {
        "PLAT": 12,
        "FIN": 12,
        "CA": 9,
        "HR": 12,
        "MKT": 14,
        "OPS": 11,
        "CBO": 12,
    }
    for prefix, minimum in expected_minimums.items():
        ids = set(re.findall(rf"\| ({prefix}-C\d{{2}}) \|", register))
        assert len(ids) >= minimum, f"{prefix} register coverage: {len(ids)} < {minimum}"

    for dimension in (
        "Internal maturity",
        "Gate result",
        "Public availability",
        "Claim treatment",
    ):
        assert dimension in register

    assert "capability id" in register.lower()
    assert "permitted claim" in register.lower()
    assert "Unassigned" in register


def test_roadmap_contains_shared_foundations_and_domain_exit_gates() -> None:
    roadmap = _read(READINESS / "BUILD_ROADMAP.md")
    required_ids = (
        "W0-08",
        "PLAT-00",
        "PLAT-04",
        "WF-03",
        "DATA-04",
        "GOV-01",
        "PRIV-01",
        "REL-02",
        "FIN-07",
        "CA-04",
        "MKT-10",
        "HR-09",
        "OPS-10",
        "CBO-09",
        "DOC-06",
        "WEB-05",
    )
    for work_package in required_ids:
        assert f"| {work_package} |" in roadmap, f"missing roadmap package {work_package}"

    for phrase in (
        "missing, expired, revoked, wrong-identity, and invalid-chain DSC",
        "provisional statutory facts",
        "Employment decision governance",
        "Customer support operations",
        "Capability readiness/evidence ledger",
        "per-capability staged delivery",
    ):
        assert phrase.lower() in roadmap.lower()


def test_root_readme_does_not_restore_known_blanket_claims() -> None:
    readme = _read(ROOT / "README.md")
    forbidden = (
        "All Real API Endpoints",
        "All endpoints real",
        "CI runs against production",
        "Runs against production on every merge to main",
        "Every agent action logged, 7-year WORM retention, HMAC signed",
        "27 production-tested templates",
        "Agent Registry (36 agents)",
        "Production-ready workflow templates",
    )
    for phrase in forbidden:
        assert phrase not in readme

    for phrase in (
        "Readiness Boundary (2026-07-13)",
        "Capability Readiness Register",
        "current workflow",
        "hard-disabled",
    ):
        assert phrase in readme


def test_program_memory_preserves_scope_and_next_checkpoint() -> None:
    memory = _read(READINESS / "PROGRAM_MEMORY.md")
    for domain in ("Marketing", "Finance", "CA", "HR", "COO", "CBO"):
        assert domain in memory
    assert "documentation and acceptance criteria come before feature construction" in memory.lower()
    assert "Next execution checkpoint" in memory
    assert "capability readiness and evidence register" in memory.lower()
