# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Encoding guard covers the repository, not just the reported dashboard label."""

from unittest.mock import Mock

import pytest

from scripts import check_text_encoding as guard


@pytest.mark.parametrize("original", [
    "\u2014", "\u2013", "\u2018", "\u2019", "\u201c", "\u201d",
    "\u2026", "\u2022", "\u2192", "\u2190", "\u2500", "\u00e9",
    "\u00a0", "\u20b9", "\U0001f680", "\u00c5", "\u2585", "\U0001f685",
])
@pytest.mark.parametrize("codec", ["cp1252", "latin-1"])
def test_rejects_misdecoded_punctuation_accents_and_symbols(tmp_path, original, codec):
    corrupt = original.encode("utf-8").decode(codec, errors="replace")
    path = tmp_path / "component.tsx"
    path.write_text(f"First line\n{corrupt}\n", encoding="utf-8")
    failures = guard.scan_file(path)
    assert failures
    assert all(":2:" in failure for failure in failures)


@pytest.mark.parametrize("text", [
    "No approvals logged: \u2014", "Wait\u2026", "caf\u00e9 d\u00e9j\u00e0 vu",
    "Fran\u00e7ais: \u00e2ge et cr\u00e8me", "\u00c2ngela et S\u00e3o Paulo",
    "\u0939\u093f\u0928\u094d\u0926\u0940 \u0928\u0940\u0932\u0915\u092e\u0932",
    "\u4e2d\u6587 \u65e5\u672c\u8a9e", "\u0627\u0644\u0639\u0631\u0628\u064a\u0629",
    "\u20b9 100 \u20ac 50 \U0001f680", r"\u00e2\u20ac\u201d",
    "\u00c5ngstr\u00f6m \u2585 \U0001f685",
])
def test_preserves_correct_unicode_and_escaped_detection_fixtures(tmp_path, text):
    path = tmp_path / "translations.json"
    path.write_text(text, encoding="utf-8")
    assert guard.scan_file(path) == []


def test_rejects_double_encoded_text_and_replacement_characters(tmp_path):
    text = "\u2014".encode().decode("cp1252").encode().decode("cp1252", errors="replace")
    path = tmp_path / "nested.md"
    path.write_text(text + "\n\ufffd", encoding="utf-8")
    assert len(guard.scan_file(path)) >= 2


def test_invalid_utf8_is_not_silently_replaced(tmp_path):
    path = tmp_path / "page.tsx"
    path.write_bytes(b"invalid: \xff")
    assert guard.scan_file(path) == [f"{path}: invalid UTF-8"]


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_latin1_next_line_byte_cannot_split_a_corrupt_sequence(tmp_path, ending):
    corrupt = "\u2585".encode("utf-8").decode("latin-1")
    path = tmp_path / "page.tsx"
    path.write_text(ending.join(["valid", corrupt, "end"]), encoding="utf-8")
    failures = guard.scan_file(path)
    assert len(failures) == 1
    assert ":2:" in failures[0]


def test_images_are_not_decoded_as_source_text(tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(b"\x89PNG\x00\xff")
    assert guard.scan_file(path) == []


def test_missing_file_fails_closed(tmp_path, capsys):
    assert guard.main([str(tmp_path / "missing.tsx")]) == 2
    assert "could not complete" in capsys.readouterr().out


def test_cli_rejects_corruption_and_accepts_fixed_file(tmp_path):
    path = tmp_path / "page.html"
    path.write_text("\ufffd", encoding="utf-8")
    assert guard.main([str(path)]) == 1
    path.write_text("\u2014", encoding="utf-8")
    assert guard.main([str(path)]) == 0


def test_inventory_uses_nul_delimiters_for_paths_with_spaces(monkeypatch, tmp_path):
    run = Mock(return_value=Mock(stdout=b"ui/page name.tsx\0README.md\0"))
    monkeypatch.setattr(guard.subprocess, "run", run)
    assert guard.tracked_files(tmp_path) == [tmp_path / "ui/page name.tsx", tmp_path / "README.md"]
    assert run.call_args.args[0][-2:] == ["ls-files", "-z"]


def test_failed_inventory_cannot_report_success(monkeypatch):
    def fail(_root):
        raise guard.subprocess.CalledProcessError(1, ["git", "ls-files"])

    monkeypatch.setattr(guard, "tracked_files", fail)
    assert guard.main([]) == 2


@pytest.mark.parametrize("name", ["page.tsx", "api.py", "locale.json", "README.md", ".github.yml", "Makefile"])
def test_checks_every_text_surface_including_extensionless_files(tmp_path, name):
    path = tmp_path / name
    path.write_text("\u00e2\u20ac\u201d", encoding="utf-8")
    assert guard.scan_file(path)


def test_repository_text_is_clean():
    failures = [failure for path in guard.tracked_files(guard.ROOT) for failure in guard.scan_file(path)]
    assert not failures, "\n".join(failures)
