# SPDX-License-Identifier: Apache-2.0
"""Keep the generic capability inventory and its readable view in agreement."""

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BFSI = ROOT / "docs" / "bfsi"
LABELS = {"covered": "Covered", "partial": "Partial", "none": "Gap"}


def _inventory():
    return json.loads((BFSI / "capability-baseline.json").read_text(encoding="utf-8"))["capabilities"]


def test_all_capabilities_have_unique_ids_and_existing_agenticorg_evidence():
    capabilities = _inventory()
    assert len(capabilities) == 205
    assert len({item["id"] for item in capabilities}) == len(capabilities)
    assert Counter(item["kind"] for item in capabilities) == {
        "mandatory": 5,
        "technical": 50,
        "functional": 150,
    }
    for item in capabilities:
        assert item["status"] in LABELS
        assert item["agenticorg"]["status"] in LABELS
        assert item["grantex"]["status"] in LABELS
        for path in item["agenticorg"]["evidence"]:
            assert not Path(path).is_absolute(), item["id"]
            resolved = (ROOT / path).resolve()
            assert resolved.is_relative_to(ROOT), (item["id"], path)
            assert resolved.exists(), (item["id"], path)


def test_readable_matrix_matches_machine_status_and_counts():
    capabilities = _inventory()
    matrix = (BFSI / "coverage-matrix.md").read_text(encoding="utf-8")
    readme = (BFSI / "README.md").read_text(encoding="utf-8")
    rows = {}
    for line in matrix.splitlines():
        columns = [cell.strip() for cell in line.split("|")[1:-1]]
        if len(columns) == 6 and columns[0] in {item["id"] for item in capabilities}:
            assert columns[0] not in rows
            rows[columns[0]] = columns
    assert len(rows) == len(capabilities)
    for item in capabilities:
        row = rows[item["id"]]
        assert row[2] == LABELS[item["status"]], item["id"]
        assert row[3].startswith(f"**{LABELS[item['agenticorg']['status']]}**"), item["id"]
        assert row[4].startswith(f"**{LABELS[item['grantex']['status']]}**"), item["id"]
        assert row[5] == item["group"], item["id"]

    for label, subset in (
        ("Baseline conditions", [item for item in capabilities if item["kind"] == "mandatory"]),
        ("Technical capabilities", [item for item in capabilities if item["kind"] == "technical"]),
        ("Functional capabilities", [item for item in capabilities if item["kind"] == "functional"]),
        ("Total", capabilities),
    ):
        counts = Counter(item["status"] for item in subset)
        expected = (
            f"| {label} | {len(subset)} | {counts['covered']} | "
            f"{counts['partial']} | {counts['none']} |"
        )
        assert expected in matrix
        assert expected in readme


def test_functional_gap_register_matches_inventory():
    capabilities = _inventory()
    page = (BFSI / "functional-coverage.md").read_text(encoding="utf-8")
    gap_section = page.split("## Exact gap register\n", 1)[1].split("\n## ", 1)[0]
    listed = re.findall(r"`([A-Z]+-\d{2})`", gap_section)
    expected = {item["id"] for item in capabilities if item["kind"] == "functional" and item["status"] == "none"}
    assert len(listed) == len(set(listed))
    assert set(listed) == expected
