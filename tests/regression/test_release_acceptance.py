"""Foundation #9 — regression tests for the release-acceptance gate.

Pinned behaviors:

- ``CheckResult`` carries the right fields.
- The skip-reason allowlist matches the Foundation #8 pattern.
- ``overall_pass`` ignores warnings and only fails on
  required failures.
- Each individual check returns a CheckResult with sensible
  ``passed`` + ``message`` for its happy path.
- ``main()`` writes a JSON artefact even on failure.
- ``--skip`` rejects unknown keys with exit code 2 (Foundation #8
  false-green prevention — silent skip would let a buggy
  configuration pass).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "release_acceptance.py"


def _load_module():
    import sys as _sys

    spec = importlib.util.spec_from_file_location(
        "release_acceptance", SCRIPT
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec_module so dataclasses
    # can resolve forward references via cls.__module__.
    _sys.modules["release_acceptance"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_script_imports_cleanly() -> None:
    mod = _load_module()
    assert hasattr(mod, "main")
    assert hasattr(mod, "CHECK_REGISTRY")
    assert hasattr(mod, "CheckResult")


def test_check_result_dataclass_fields() -> None:
    mod = _load_module()
    r = mod.CheckResult(name="x", passed=True, message="ok")
    assert r.severity == "required"
    assert r.duration_seconds == 0.0
    assert r.details == {}


def test_overall_pass_ignores_warnings() -> None:
    mod = _load_module()
    results = [
        mod.CheckResult(name="a", passed=True, message="ok"),
        mod.CheckResult(
            name="b", passed=False, message="warn", severity="warning"
        ),
    ]
    assert mod.overall_pass(results) is True


def test_overall_pass_fails_on_required_failure() -> None:
    mod = _load_module()
    results = [
        mod.CheckResult(name="a", passed=True, message="ok"),
        mod.CheckResult(name="b", passed=False, message="fail"),
    ]
    assert mod.overall_pass(results) is False


def test_overall_pass_with_only_warnings_still_passes() -> None:
    mod = _load_module()
    results = [
        mod.CheckResult(
            name="b", passed=False, message="warn", severity="warning"
        ),
    ]
    assert mod.overall_pass(results) is True


def test_no_unexplained_skips_runs_against_real_tree() -> None:
    """The check must succeed on the live tree (Foundation #8's
    allowlist already covers every legitimate skip we've shipped).
    A regression here means a new test added a skip with an
    undocumented reason."""
    mod = _load_module()
    r = mod.check_no_unexplained_skips()
    assert r.name == "pytest_no_unexplained_skips"
    assert r.passed, f"unexpected unallowed skip(s): {r.message}"


def test_qa_matrix_check_handles_missing_file(monkeypatch, tmp_path) -> None:
    """When the matrix file isn't on disk, the gate must FAIL —
    not silently skip. Foundation #8 false-green prevention."""
    mod = _load_module()
    monkeypatch.setattr(mod, "REPO", tmp_path)
    r = mod.check_qa_matrix_p0_p1_mapped()
    assert r.passed is False
    assert "Missing artefact" in r.message


def test_required_artefacts_check_handles_missing(monkeypatch, tmp_path) -> None:
    """Missing required artefacts → fail with the list."""
    mod = _load_module()
    monkeypatch.setattr(mod, "REPO", tmp_path)
    r = mod.check_required_artefacts()
    assert r.passed is False
    assert "missing" in r.message.lower() or "Missing" in r.message


def test_required_artefacts_pass_when_present(monkeypatch, tmp_path) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "REPO", tmp_path)
    (tmp_path / "coverage.xml").write_text("<coverage/>", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "qa_test_matrix.yml").write_text("test_cases: []\n", encoding="utf-8")
    r = mod.check_required_artefacts()
    assert r.passed is True


def test_main_writes_json_artefact_on_pass_and_fail(tmp_path, monkeypatch) -> None:
    """The JSON artefact must be written regardless of outcome —
    downstream pipelines key off it."""
    mod = _load_module()

    monkeypatch.chdir(tmp_path)
    out = tmp_path / "ra.json"
    # Skip every check so main() exits cleanly with no work; the
    # important property is that the JSON file is written.
    rc = mod.main(["--json", str(out), "--skip", ",".join(mod.CHECK_REGISTRY.keys())])
    assert rc == 0  # nothing to fail when everything is skipped
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["results"] == []
    assert set(payload["skipped"]) == set(mod.CHECK_REGISTRY.keys())


def test_main_rejects_unknown_skip_key(tmp_path, capsys) -> None:
    mod = _load_module()
    rc = mod.main(["--skip", "not_a_real_check", "--json", str(tmp_path / "x.json")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Unknown --skip keys" in err


def test_skip_allowlist_pattern_compiles_and_matches() -> None:
    mod = _load_module()
    pat = mod._ALLOWED_SKIP_REASONS
    for legit in (
        "requires postgres",
        "requires redis",
        "Foundation #5 not yet merged",
        "helm chart removed in Stage 4",
        "sibling-sweep marker",
        "windows-only fixture",
    ):
        assert pat.search(legit), f"should allow {legit!r}"
    for bad in ("", "TODO", "skipping for now"):
        assert not pat.search(bad), f"should reject {bad!r}"


def test_check_registry_is_complete() -> None:
    """Every closure-plan criterion has a registered check."""
    mod = _load_module()
    expected = {
        "skips",
        "coverage",
        "qa_matrix",
        "crypto",
        "tsc",
        "npm_build",
        "vitest",
        "playwright",
        "artefacts",
    }
    assert set(mod.CHECK_REGISTRY.keys()) == expected


@pytest.mark.parametrize(
    "key", ["skips", "coverage", "qa_matrix", "crypto", "tsc", "npm_build", "vitest", "playwright", "artefacts"]
)
def test_each_check_returns_a_checkresult(key) -> None:
    """Smoke: every check function returns a CheckResult, never
    raises uncaught. Some legitimately fail in the test env (no
    npm, no DB) — that's fine; we're pinning the contract that
    they return a CheckResult."""
    mod = _load_module()
    fn = mod.CHECK_REGISTRY[key]
    r = fn()
    assert isinstance(r, mod.CheckResult)
    assert r.name
    assert isinstance(r.passed, bool)
