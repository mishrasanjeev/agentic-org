from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


def test_legacy_company_brand_is_not_reintroduced() -> None:
    forbidden = ("edu" + "matica").lower()
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()

    offenders: list[str] = []
    for rel_path in tracked:
        path = ROOT / rel_path
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if forbidden in path.read_text(encoding="utf-8", errors="ignore").lower():
            offenders.append(rel_path)

    assert offenders == []
