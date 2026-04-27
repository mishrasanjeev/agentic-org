"""Unit tests for scripts/qa_matrix.py — the manual ↔ automated TC mapping.

Pin the contracts that, if regressed, would silently undercount TCs
or break the burndown ratchet from Foundation #1.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import qa_matrix as qm  # noqa: E402

# ──────────────────────────────────────────────────────────────────────
# Parser
# ──────────────────────────────────────────────────────────────────────


def test_parse_plan_picks_up_single_segment_namespaces(tmp_path: Path) -> None:
    plan = tmp_path / "plan.md"
    plan.write_text(
        dedent(
            """
            ## Module 1: Landing
            ### TC-LP-001: Lands without error
            **Steps:** ...

            ### TC-LP-002: Has SEO tags
            ...
            """
        ).strip(),
        encoding="utf-8",
    )
    tcs = qm.parse_plan(plan)
    assert [t.id for t in tcs] == ["TC-LP-001", "TC-LP-002"]
    assert tcs[0].title == "Lands without error"
    assert tcs[0].module == "Module 1: Landing"


def test_parse_plan_picks_up_multi_segment_namespaces(tmp_path: Path) -> None:
    """``TC-ORG-CHART-001`` must parse — multi-segment namespaces are common."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        dedent(
            """
            ## Module 4: Agent Fleet
            ### TC-ORG-CHART-001: Create agent with parent
            ### TC-AGENT-WIZARD-FORK-007: Fork existing agent
            """
        ).strip(),
        encoding="utf-8",
    )
    tcs = qm.parse_plan(plan)
    assert {t.id for t in tcs} == {
        "TC-ORG-CHART-001",
        "TC-AGENT-WIZARD-FORK-007",
    }


def test_parse_plan_default_module_when_no_heading_seen(tmp_path: Path) -> None:
    plan = tmp_path / "plan.md"
    plan.write_text(
        "### TC-XX-001: Test before any module heading\n", encoding="utf-8"
    )
    tcs = qm.parse_plan(plan)
    assert tcs[0].module == "Module 0: Unscoped"


def test_parse_plan_missing_file_exits(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="plan not found"):
        qm.parse_plan(tmp_path / "nope.md")


# ──────────────────────────────────────────────────────────────────────
# Reference scanner
# ──────────────────────────────────────────────────────────────────────


def test_reference_index_finds_tc_ids_across_test_trees(tmp_path: Path) -> None:
    repo = tmp_path
    tests = repo / "tests"
    e2e = repo / "ui" / "e2e"
    tests.mkdir(parents=True)
    e2e.mkdir(parents=True)

    (tests / "test_a.py").write_text(
        '"""Tests TC-LP-001 and TC-AUTH-005."""\n', encoding="utf-8"
    )
    (e2e / "landing.spec.ts").write_text(
        "// covers TC-LP-001\n", encoding="utf-8"
    )
    (tests / "ignore.txt").write_text("TC-LP-001 should not match", encoding="utf-8")

    # Patch the REPO base so relative paths come out right.
    saved_repo = qm.REPO
    qm.REPO = repo
    try:
        index = qm._build_reference_index((tests, e2e))
    finally:
        qm.REPO = saved_repo

    assert set(index["TC-LP-001"]) == {"tests/test_a.py", "ui/e2e/landing.spec.ts"}
    assert index["TC-AUTH-005"] == ["tests/test_a.py"]
    # .txt extension is filtered out
    assert all(not p.endswith(".txt") for refs in index.values() for p in refs)


# ──────────────────────────────────────────────────────────────────────
# generate / check round-trip
# ──────────────────────────────────────────────────────────────────────


def test_generate_writes_matrix_with_all_tcs(tmp_path: Path) -> None:
    plan = tmp_path / "plan.md"
    plan.write_text(
        dedent(
            """
            ## Module 1: Landing
            ### TC-LP-001: First
            ### TC-LP-002: Second
            """
        ).strip(),
        encoding="utf-8",
    )
    matrix = tmp_path / "matrix.yml"
    rc = qm.generate(plan_path=plan, matrix_path=matrix, test_trees=())
    assert rc == 0
    data = yaml.safe_load(matrix.read_text(encoding="utf-8"))
    assert data["total_tcs"] == 2
    assert {e["id"] for e in data["entries"]} == {"TC-LP-001", "TC-LP-002"}
    assert all(e["status"] == "unknown" for e in data["entries"])


def test_generate_preserves_manual_status_across_runs(tmp_path: Path) -> None:
    """Hand-edits to status/notes must not be trampled on regenerate."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        dedent(
            """
            ## Module 1: Landing
            ### TC-LP-001: First
            ### TC-LP-002: Second
            """
        ).strip(),
        encoding="utf-8",
    )
    matrix = tmp_path / "matrix.yml"
    qm.generate(plan_path=plan, matrix_path=matrix, test_trees=())
    # Operator hand-edits one TC.
    data = yaml.safe_load(matrix.read_text(encoding="utf-8"))
    for e in data["entries"]:
        if e["id"] == "TC-LP-001":
            e["status"] = "manual_only"
            e["notes"] = "Visual smoke — not automatable"
    matrix.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    # Regenerate.
    qm.generate(plan_path=plan, matrix_path=matrix, test_trees=())
    data2 = yaml.safe_load(matrix.read_text(encoding="utf-8"))
    by_id = {e["id"]: e for e in data2["entries"]}
    assert by_id["TC-LP-001"]["status"] == "manual_only"
    assert by_id["TC-LP-001"]["notes"] == "Visual smoke — not automatable"
    # The other TC stayed unknown.
    assert by_id["TC-LP-002"]["status"] == "unknown"


def test_generate_auto_classifies_when_references_found(tmp_path: Path) -> None:
    plan = tmp_path / "plan.md"
    plan.write_text(
        "## Module 1: Landing\n### TC-LP-001: First\n", encoding="utf-8"
    )
    matrix = tmp_path / "matrix.yml"
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_landing.py").write_text(
        "# TC-LP-001 covered by test_landing\n", encoding="utf-8"
    )
    saved_repo = qm.REPO
    qm.REPO = tmp_path
    try:
        qm.generate(plan_path=plan, matrix_path=matrix, test_trees=(tests,))
    finally:
        qm.REPO = saved_repo
    data = yaml.safe_load(matrix.read_text(encoding="utf-8"))
    assert data["entries"][0]["status"] == "automated"
    assert data["entries"][0]["references"] == ["tests/test_landing.py"]


# ──────────────────────────────────────────────────────────────────────
# check exit codes
# ──────────────────────────────────────────────────────────────────────


def test_check_default_is_informational_with_unknowns(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.yml"
    matrix.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "total_tcs": 1,
                "entries": [
                    {
                        "id": "TC-LP-001",
                        "module": "Module 1",
                        "title": "x",
                        "status": "unknown",
                        "references": [],
                        "notes": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    captured = io.StringIO()
    sys.stdout = captured
    try:
        rc = qm.check(matrix_path=matrix)
    finally:
        sys.stdout = sys.__stdout__
    assert rc == 0
    assert "INFO" in captured.getvalue()


def test_check_enforce_unknown_returns_1(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.yml"
    matrix.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "total_tcs": 1,
                "entries": [
                    {
                        "id": "TC-LP-001",
                        "module": "Module 1",
                        "title": "x",
                        "status": "unknown",
                        "references": [],
                        "notes": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    captured = io.StringIO()
    sys.stdout = captured
    try:
        rc = qm.check(matrix_path=matrix, enforce_unknown=True)
    finally:
        sys.stdout = sys.__stdout__
    assert rc == 1
    assert "FAIL" in captured.getvalue()


def test_check_strict_returns_1_on_needs_automation(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.yml"
    matrix.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "total_tcs": 1,
                "entries": [
                    {
                        "id": "TC-LP-001",
                        "module": "Module 1",
                        "title": "x",
                        "status": "needs-automation",
                        "references": [],
                        "notes": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    captured = io.StringIO()
    sys.stdout = captured
    try:
        rc = qm.check(matrix_path=matrix, strict=True)
    finally:
        sys.stdout = sys.__stdout__
    assert rc == 1


def test_check_passes_when_all_classified(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.yml"
    matrix.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "total_tcs": 1,
                "entries": [
                    {
                        "id": "TC-LP-001",
                        "module": "Module 1",
                        "title": "x",
                        "status": "automated",
                        "references": ["tests/test_landing.py"],
                        "notes": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    rc = qm.check(matrix_path=matrix, enforce_unknown=True, strict=True)
    assert rc == 0


def test_check_missing_file_returns_2(tmp_path: Path) -> None:
    rc = qm.check(matrix_path=tmp_path / "nope.yml")
    assert rc == 2


def test_check_malformed_returns_2(tmp_path: Path) -> None:
    matrix = tmp_path / "bad.yml"
    matrix.write_text("just a string", encoding="utf-8")
    rc = qm.check(matrix_path=matrix)
    assert rc == 2


def test_check_json_emits_machine_readable_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    matrix = tmp_path / "matrix.yml"
    matrix.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "total_tcs": 2,
                "entries": [
                    {"id": "TC-LP-001", "module": "Module 1", "title": "x",
                     "status": "automated", "references": [], "notes": ""},
                    {"id": "TC-LP-002", "module": "Module 1", "title": "y",
                     "status": "unknown", "references": [], "notes": ""},
                ],
            }
        ),
        encoding="utf-8",
    )
    rc = qm.check(matrix_path=matrix, output_json=True)
    assert rc == 0
    out = capsys.readouterr().out
    import json as _json

    payload = _json.loads(out)
    assert payload["total"] == 2
    assert payload["buckets"]["automated"] == 1
    assert payload["buckets"]["unknown"] == 1
    assert payload["unknown_ids"] == ["TC-LP-002"]


# ──────────────────────────────────────────────────────────────────────
# CLI surface
# ──────────────────────────────────────────────────────────────────────


def test_cli_help_documents_subcommands() -> None:
    captured = io.StringIO()
    sys.stdout = captured
    try:
        with pytest.raises(SystemExit) as exc:
            qm.main(["--help"])
    finally:
        sys.stdout = sys.__stdout__
    assert exc.value.code == 0
    out = captured.getvalue()
    assert "generate" in out
    assert "check" in out


def test_cli_check_help_documents_flags() -> None:
    captured = io.StringIO()
    sys.stdout = captured
    try:
        with pytest.raises(SystemExit) as exc:
            qm.main(["check", "--help"])
    finally:
        sys.stdout = sys.__stdout__
    assert exc.value.code == 0
    out = captured.getvalue()
    for token in ("--enforce-unknown", "--strict", "--json"):
        assert token in out
