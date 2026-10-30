# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Vendor-name denylist check (scripts/check_denylist.py).

Uses its own salt and invented terms; the committed hash list is only checked
for shape.
"""

from __future__ import annotations

import random
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import check_denylist as dl

TEST_SALT = bytes.fromhex("7e57" * 16)
TEST_TERMS = ["acme verify", "Globex Screening Hub", "initechkyb"]
REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> str:
    argv = ["git", "-C", str(repo), *args]
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout.strip()  # noqa: S603, S607


@pytest.fixture()
def denylist() -> dl.Denylist:
    text, _ = dl.render(TEST_TERMS, TEST_SALT)
    return dl.Denylist(
        salt=TEST_SALT,
        max_tokens=3,
        hashes=frozenset(line for line in text.splitlines() if len(line) == 64),
    )


@pytest.fixture()
def hash_file(tmp_path: Path) -> Path:
    path = tmp_path / "hashes" / "denylist.sha256"
    path.parent.mkdir()
    path.write_text(dl.render(TEST_TERMS, TEST_SALT)[0], encoding="utf-8")
    return path


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "ci@example.com")
    _git(root, "config", "user.name", "ci")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "existing.py").write_text("legacy = 'AcmeVerify'  # predates the change\nkeep = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-q", "-b", "feat/neutral-provider")
    return root


def _commit(repo: Path, files: dict[str, str], message: str = "change") -> None:
    for name, body in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


def _scan(repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, str, str]:
    argv = ["--repo", str(repo), "--hash-file", str(hash_file), "scan", "--base", "main", "--head", "HEAD", *extra]
    code = dl.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ── Matching ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "acme verify",
        "AcmeVerify",
        "acme_verify",
        "ACME-VERIFY",
        "use the acmeverify client",
        "class AcmeVerifyProvider:",
        "Ácme Vérify",
        "globex screening hub",
        "GlobexScreeningHub",
        "initech-kyb",
        "INITECH_KYB_TOKEN",
    ],
)
def test_spelling_variants_of_a_term_match(denylist: dl.Denylist, text: str) -> None:
    assert denylist.matches(text)


@pytest.mark.parametrize(
    "text",
    [
        "acme",
        "verify the acme_kyb example",
        "globex screening",
        "provider = mock",
        'integrity "sha512-Q2x0AcmeVerify9z8y7x6w5v4u3t2s1r0qAbCdEfGhIjKlMnOp=="',
    ],
)
def test_unrelated_text_and_encoded_data_do_not_match(denylist: dl.Denylist, text: str) -> None:
    assert denylist.matches(text) == []


def test_match_reports_token_position(denylist: dl.Denylist) -> None:
    assert denylist.matches("configure the Acme Verify adapter") == [2]


def _single(term: str) -> dl.Denylist:
    """A denylist built exactly as ``build`` would, from one invented term."""
    return dl.load_text(dl.render([term], TEST_SALT)[0])


@pytest.mark.parametrize("text", ["myacmeverify", "use_theacmeverify", "INITECHKYB-free", "legacyinitechkyb"])
def test_term_glued_to_the_end_of_a_word_matches(denylist: dl.Denylist, text: str) -> None:
    assert denylist.matches(text)


def test_word_tails_are_not_joined_to_the_following_words() -> None:
    walls = _single("stone wall")
    assert walls.matches("gemstonewall")
    assert walls.matches("stone wall")
    assert walls.matches("gemstone wall") == [], "a tail joined across a word boundary would be a chance match"


def test_window_has_headroom_for_terms_split_into_more_tokens() -> None:
    one_token = _single("initechkyb")
    assert one_token.max_tokens == 1
    assert one_token.matches("Ini-Tech-Kyb")  # three tokens: the longest term plus the headroom
    assert one_token.matches("I-ni-Tech-Kyb") == []  # four tokens: beyond the headroom


def test_long_identifiers_with_digits_are_still_checked(denylist: dl.Denylist) -> None:
    identifier = "AcmeVerify2024ClientConfigurationSettingsHandler"
    assert len(identifier) >= 40
    assert not dl.looks_encoded(identifier)
    assert denylist.matches(f"client = {identifier}()")
    assert denylist.matches("acmeverify20240915clientconfigurationsettings")


@pytest.mark.parametrize(
    ("run", "encoded"),
    [
        ("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08", True),
        ("Q2x0AcmeVerify9z8y7x6w5v4u3t2s1r0qAbCdEfGhIjKlMnOp==", True),
        ("AcmeVerify2024ClientConfigurationSettingsHandler", False),
        ("abcdefabcdefabcdefabcdefabcdefabcdef", False),
        ("configurationsettingsforthedevelopmentstackv2", False),
    ],
)
def test_encoded_data_detection(run: str, encoded: bool) -> None:
    assert dl.looks_encoded(run) is encoded


# ── Scanning a change ───────────────────────────────────────────────────────


def test_clean_change_passes(repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, {"providers/mock.py": "PROVIDER = 'acme_kyb'\n"}, "feat: add the mock provider")
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 0
    assert "no denylisted terms" in out


def test_added_line_is_reported_by_location_without_echoing_the_term(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit(repo, {"providers/client.py": "x = 1\nbase_url = 'https://acmeverify.example.com'\n"})
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    assert "providers/client.py:2" in out
    assert "verify" not in out.lower()


def test_file_path_is_checked(repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, {"providers/globex_screening_hub/__init__.py": ""})
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    assert "file path providers/" in out


def test_commit_message_is_checked(repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, {"providers/mock.py": "ok = True\n"}, "feat: port the InitechKyb adapter")
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    assert "message line 1" in out


def test_branch_name_is_checked(repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _git(repo, "checkout", "-q", "-b", "feat/acme-verify-adapter")
    _commit(repo, {"providers/mock.py": "ok = True\n"})
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    assert "branch name" in out


def test_explicit_branch_name_overrides_checkout(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit(repo, {"providers/mock.py": "ok = True\n"})
    code, out, _ = _scan(repo, hash_file, capsys, "--branch", "feat/globex-screening-hub")
    assert code == 1
    assert "branch name" in out


def test_added_lines_that_look_like_diff_headers_are_checked(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "notes.md").write_text("intro\n-- old heading\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "notes")
    base = _git(repo, "rev-parse", "HEAD")
    # In the unified diff these become "--- old heading" and "+++ acme verify":
    # a removed and an added line, not a file header.
    (repo / "notes.md").write_text("intro\n++ acme verify\n")
    _git(repo, "commit", "-qam", "change")
    code = dl.main(["--repo", str(repo), "--hash-file", str(hash_file), "scan", "--base", base, "--head", "HEAD"])
    out = capsys.readouterr().out
    assert code == 1
    assert "notes.md:2" in out


def _commit_bytes(repo: Path, files: dict[str, bytes], message: str = "add media") -> None:
    for name, body in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


# Bytes that, diffed as text, give a line starting "@@" that is no hunk header.
_VIDEO_LIKE = b"\x00\x00\x00\x18ftypmp42\x00\x00\n@@\x9c\x01\xff\xfe junk\n+\x00\x01\x02\n" * 50


def test_a_binary_file_is_read_for_its_text_not_diffed_as_lines(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit_bytes(repo, {"ui/public/media/clip.mp4": _VIDEO_LIKE, "ui/public/media/poster.jpg": b"\xff\xd8\x00" * 40})
    code, out, err = _scan(repo, hash_file, capsys)
    assert code == 0, err
    assert "no denylisted terms" in out


def test_a_term_in_a_binary_files_metadata_is_found(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tagged = _VIDEO_LIKE + b"\x00\x00\x00\x20\xa9too encoded by AcmeVerify Studio\x00" + _VIDEO_LIKE
    _commit_bytes(repo, {"ui/public/media/clip.mp4": tagged})
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    assert "ui/public/media/clip.mp4 (binary, readable text" in out
    assert "verify" not in out.lower()


def test_a_deleted_binary_file_adds_nothing(repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "old.bin").write_bytes(b"\x00 tagged by AcmeVerify \x00" * 4)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "old binary")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "old.bin").unlink()
    _git(repo, "commit", "-qam", "remove it")
    code = dl.main(["--repo", str(repo), "--hash-file", str(hash_file), "scan", "--base", base, "--head", "HEAD"])
    assert code == 0, capsys.readouterr().err


def _scan_from(repo: Path, hash_file: Path, base: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = dl.main(["--repo", str(repo), "--hash-file", str(hash_file), "scan", "--base", base, "--head", "HEAD"])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_a_new_mode_alone_on_a_binary_file_adds_nothing(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Git's patch shows no "Binary files ... differ" line for a mode change, though --numstat counts the file.
    _commit_bytes(repo, {"tools/run.bin": b"\x00 tagged by AcmeVerify \x00" * 4}, "tool")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "tools" / "other.bin").write_bytes(b"\x00\x01clean\x00")
    _git(repo, "add", "tools/other.bin")
    _git(repo, "update-index", "--chmod=+x", "tools/run.bin")
    _git(repo, "commit", "-qm", "make the tool executable")
    assert "100755" in _git(repo, "ls-tree", "HEAD", "tools/run.bin")
    code, out, err = _scan_from(repo, hash_file, base, capsys)
    assert code == 0, err
    assert "no denylisted terms" in out


def _commit_entries(repo: Path, entries: dict[str, bytes], message: str = "add media") -> None:
    """Commit files at the top of the tree with git plumbing, for names no checkout can hold (":clip.mp4")."""
    lines = [line for line in _git(repo, "ls-tree", "HEAD").splitlines() if line]
    for name, body in entries.items():
        argv = ["git", "-C", str(repo), "hash-object", "-w", "--stdin"]
        blob = subprocess.run(argv, input=body, check=True, capture_output=True).stdout.decode().strip()  # noqa: S603
        lines.append(f"100644 blob {blob}\t{name}")
    argv = ["git", "-C", str(repo), "mktree"]
    listing = ("\n".join(lines) + "\n").encode()
    tree = subprocess.run(argv, input=listing, check=True, capture_output=True).stdout.decode().strip()  # noqa: S603
    _git(repo, "update-ref", "HEAD", _git(repo, "commit-tree", tree, "-p", "HEAD", "-m", message))


# What str.splitlines() breaks a line at but git does not: \r, \v, \f, \x1c-\x1e, U+0085, U+2028, U+2029.
_SPLITLINES_ONLY = [
    "\r",
    "\x0b",
    "\x0c",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x85",
    "\N{LINE SEPARATOR}",
    "\N{PARAGRAPH SEPARATOR}",
]


def test_bytes_that_split_before_at_at_neither_crash_the_scan_nor_hide_text(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Regression: the scan split the diff with str.splitlines(), so a "\r" or
    # "\f" just before "@@" started a "line" that was refused as a hunk header.
    # The video (diffed as text before binary files were left out of the diff)
    # and the text file (still diffed) each made the scan exit 2.
    video = b"\x00\x00\x00\x18ftypmp42\x00\x00" + b"".join(bytes([sep]) + b"@@\x9c junk" for sep in b"\r\x0c\x1e" * 20)
    _commit_bytes(repo, {"ui/public/media/clip.mp4": video, "docs/notes.md": b"intro\x0c@@ not a hunk\x0b@@ y\nok\n"})
    code, out, err = _scan(repo, hash_file, capsys)
    assert code == 0, err
    assert "no denylisted terms" in out


@pytest.mark.parametrize("separator", _SPLITLINES_ONLY)
def test_a_term_after_a_character_that_is_no_line_break_to_git_is_found(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str], separator: str
) -> None:
    _commit_bytes(repo, {"docs/notes.md": f"intro{separator}Powered by AcmeVerify\n".encode()})
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    assert "docs/notes.md:1 (word" in out


def test_a_file_with_carriage_return_line_ends_is_checked_past_its_first_line(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit_bytes(repo, {"data/vendors.csv": b"id,name\r1,ok\r2,AcmeVerify\r"})
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    assert "data/vendors.csv:1 (word" in out


def test_a_diff_header_inside_an_added_line_does_not_end_the_hunk(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit_bytes(repo, {"docs/notes.md": b"intro\x0cdiff --git a/x b/x\n+++ b/x\nAcmeVerify\n"})
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    assert "docs/notes.md:3 (word 1)" in out


def test_a_directory_that_replaces_a_binary_file_of_the_same_name_is_checked(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit_bytes(repo, {"ui/logo.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" * 20}, "logo")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "ui" / "logo.png").unlink()
    _commit(repo, {"ui/logo.png/notes.md": "intro\nPowered by AcmeVerify\n"}, "notes")
    code, out, _ = _scan_from(repo, hash_file, base, capsys)
    assert code == 1
    assert "ui/logo.png/notes.md:2 (word 3)" in out


def test_a_binary_file_whose_name_starts_with_a_colon_is_read(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Git reads ":clip.mp4" given as a path argument as pathspec magic, so the
    # file must be read by object id, never by name.
    tagged = _VIDEO_LIKE + b"\x00\x00\x00\x20\xa9too encoded by AcmeVerify Studio\x00" + _VIDEO_LIKE
    _commit_entries(repo, {":clip.mp4": tagged})
    code, out, err = _scan(repo, hash_file, capsys)
    assert code == 1, err
    assert ":clip.mp4 (binary, readable text at byte" in out
    assert "verify" not in out.lower()


@pytest.mark.parametrize(
    "line",
    [b"vendor\tAcme\tVerify", "vendor Acme\N{NO-BREAK SPACE}Verify".encode(), "vendor Ácme Vérify".encode()],
)
def test_a_text_file_with_one_nul_byte_is_still_checked_as_text(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str], line: bytes
) -> None:
    _commit_bytes(repo, {"docs/vendors.md": b"intro\x00\n" + line + b"\n"})
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    assert "docs/vendors.md:2 (binary, read as text) (word 2)" in out


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le"])
def test_utf16_and_utf32_text_files_are_checked_line_by_line(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str], encoding: str
) -> None:
    text = "Release notes\nBuilt with Ácme\tVérify\n"
    _commit_bytes(repo, {"docs/notes.txt": text.encode(encoding)})
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    assert f"docs/notes.txt:2 (binary, read as {encoding[:6].upper()}) (word 3)" in out


def test_a_gitattributes_rule_added_by_the_change_does_not_reclassify_files(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The committed bytes of existing.py plus one line: "binary" turns end-of-line
    # conversion off, so a checkout's line ends would otherwise change every line.
    existing = subprocess.run(["git", "-C", str(repo), "show", "HEAD:existing.py"], capture_output=True, check=True)  # noqa: S603, S607
    files = {
        ".gitattributes": b"*.py binary\n*.md -diff\n",
        "providers/client.py": b"x = 1\nvendor = 'Acme\tVerify'\n",
        "existing.py": existing.stdout + b"more = 2\n",
    }
    _commit_bytes(repo, files, "change")
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    # Still text files (the base commit has no such rule): only added lines are
    # checked, as lines of the diff, so the old mention in existing.py is not.
    assert "providers/client.py:2 (word 2)" in out
    assert "existing.py" not in out
    assert "binary" not in out


def test_many_binary_files_scan_without_growing_any_command_line(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    frames = {f"media/frame-{i:04d}.bin": b"\x00\x01frame\x00" + bytes([i % 200 + 32]) * 64 for i in range(400)}
    frames["media/frame-0399.bin"] = b"\x00\x01\xff encoded by AcmeVerify Studio \x00"
    _commit_bytes(repo, frames)
    argvs: list[list[str]] = []

    class RecordingPopen(subprocess.Popen):  # records every command line the scan runs
        def __init__(self, argv: list[str], *args: object, **kwargs: object) -> None:
            argvs.append(list(argv))
            super().__init__(argv, *args, **kwargs)

    monkeypatch.setattr(dl.subprocess, "Popen", RecordingPopen)
    code, out, err = _scan(repo, hash_file, capsys)
    monkeypatch.undo()
    assert code == 1, err
    assert out.count("media/frame-") == 1
    assert "media/frame-0399.bin (binary, readable text at byte 3) (word 3)" in out
    assert argvs and all("frame-" not in arg for argv in argvs for arg in argv)
    assert max(len(" ".join(argv)) for argv in argvs) < 1_000


def _mp4_atom(kind: bytes, body: bytes) -> bytes:
    return (8 + len(body)).to_bytes(4, "big") + kind + body


def _mp4(title: bytes, payload_bytes: int = 2 * 1024 * 1024) -> bytes:
    """An MP4-shaped file: a title and an encoder tag in its metadata, and pseudo-random (compressed-like) media."""
    data = b"\x00\x00\x00\x01\x00\x00\x00\x00"  # a text value: type 1, default locale
    tags = _mp4_atom(b"\xa9nam", _mp4_atom(b"data", data + title))
    tags += _mp4_atom(b"\xa9too", _mp4_atom(b"data", data + b"Lavf60.16.100"))
    meta = _mp4_atom(b"meta", b"\x00\x00\x00\x00" + _mp4_atom(b"ilst", tags))
    media = _mp4_atom(b"mdat", random.Random(20261030).randbytes(payload_bytes))  # noqa: S311 - test data
    return _mp4_atom(b"ftyp", b"isom\x00\x00\x02\x00isomiso2avc1mp41") + media + _mp4_atom(b"moov", meta)


def test_a_realistic_video_passes_with_the_committed_list(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit_bytes(repo, {"ui/public/media/walkthrough.mp4": _mp4(b"Order walkthrough")})
    code, out, err = _scan(repo, REPO_ROOT / "config" / "denylist.sha256", capsys)
    assert code == 0, out + err


@pytest.mark.parametrize("title", [b"AcmeVerify", b"Globex Screening Hub", "Ácme Vérify".encode(), b"initech_kyb"])
def test_a_term_as_a_videos_whole_title_is_found(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str], title: bytes
) -> None:
    _commit_bytes(repo, {"ui/public/media/walkthrough.mp4": _mp4(title, payload_bytes=64 * 1024)})
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    assert "ui/public/media/walkthrough.mp4 (binary, readable text at byte" in out


def test_a_five_character_term_alone_in_metadata_is_found(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    short = tmp_path / "short.sha256"
    short.write_text(dl.render(["zorbx"], TEST_SALT)[0], encoding="utf-8")
    assert dl._RUN_MIN_CHARS == 5
    _commit_bytes(repo, {"ui/public/media/walkthrough.mp4": _mp4(b"Zorbx", payload_bytes=64 * 1024)})
    code, out, _ = _scan(repo, short, capsys)
    assert code == 1
    assert "walkthrough.mp4 (binary, readable text at byte" in out


def test_readable_runs_read_utf8_utf16_and_utf32_text_in_binary_data() -> None:
    accented = "Ácme Vérify"
    data = (
        b"\x00\x01"
        + accented.encode()
        + b"\x00\xff"
        + accented.encode("utf-16-le")
        + b"\x01\x02"
        + accented.encode("utf-16-be")
        + b"\x03"
        + accented.encode("utf-32-le")
        + b"\x05\x06\x07ok\x08"
    )
    runs = list(dl.readable_runs(data))
    assert [text for _, text in runs] == [accented] * 4
    assert [offset for offset, _ in runs] == sorted(offset for offset, _ in runs)
    assert data[runs[0][0] :].startswith(accented.encode())


def test_a_term_in_utf16_metadata_of_a_binary_file_is_found(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    resource = b"\x00\x00\x01\x00" + "CompanyName\x00Acme Verify Ltd\x00".encode("utf-16-le") + b"\xff\x00\x00\x01"
    _commit_bytes(repo, {"tools/setup.exe": b"MZ\x90\x00\x03\x00\x00\x00" * 64 + resource})
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    assert "tools/setup.exe (binary, readable text at byte" in out


def test_a_line_in_a_legacy_8_bit_encoding_keeps_its_accents(denylist: dl.Denylist) -> None:
    raw = "vendor Ácme Vérify".encode("cp1252")
    assert not denylist.matches(raw.decode("utf-8", "replace"))
    assert any(denylist.matches(text) for text in dl.line_texts(raw))
    assert len(dl.line_texts(b"\x01\xc1cme")) == 1  # control bytes: binary noise, not 8-bit text


def test_a_record_separator_in_a_commit_message_hides_nothing(
    repo: Path, hash_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "providers").mkdir()
    (repo / "providers" / "mock.py").write_text("ok = True\n")
    _git(repo, "add", "-A")
    message = tmp_path / "message.txt"
    message.write_bytes(b"feat: tidy up\x1eport the AcmeVerify adapter\n")
    _git(repo, "commit", "-q", "-F", str(message))
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    assert "message line 1 (word" in out


def test_a_path_with_a_space_is_reported_by_its_name(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit(repo, {"docs/release notes.md": "intro\nPowered by AcmeVerify\n"})
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    assert "  docs/release notes.md:2 (word 3)" in out


def test_non_utf8_text_file_fails_closed(
    repo: Path, hash_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit(repo, {"providers/mock.py": "ok = True\n"})
    pr_text = tmp_path / "pull-request.txt"
    pr_text.write_bytes(b"Title\n\xff\xfe not utf-8\n")
    code, _, err = _scan(repo, hash_file, capsys, "--text-file", str(pr_text))
    assert code == 2
    assert "cannot read" in err


def test_extra_text_file_is_checked(
    repo: Path, hash_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit(repo, {"providers/mock.py": "ok = True\n"})
    pr_text = tmp_path / "pull-request.txt"
    pr_text.write_text("Adds a provider\n\nModelled on the Acme Verify API.\n", encoding="utf-8")
    code, out, _ = _scan(repo, hash_file, capsys, "--text-file", str(pr_text))
    assert code == 1
    assert "pull-request.txt line 3" in out


def test_removed_and_untouched_lines_are_not_reported(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "existing.py").write_text("keep = 1\n")
    _git(repo, "commit", "-qam", "refactor: drop the legacy name")
    code, _, _ = _scan(repo, hash_file, capsys)
    assert code == 0


# ── Failing closed ──────────────────────────────────────────────────────────


def test_unresolvable_base_fails_closed(repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = dl.main(["--repo", str(repo), "--hash-file", str(hash_file), "scan", "--base", "no-such-ref"])
    assert code == 2
    assert "cannot resolve ref 'no-such-ref'" in capsys.readouterr().err


def test_not_a_git_repository_fails_closed(
    tmp_path: Path, hash_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    # Stop git from discovering a repository above the temporary directory.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    code = dl.main(["--repo", str(plain), "--hash-file", str(hash_file), "scan", "--base", "main", "--branch", "x"])
    assert code == 2
    assert "check_denylist:" in capsys.readouterr().err


def test_git_failure_after_refs_resolve_fails_closed(
    repo: Path, hash_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit(repo, {"providers/mock.py": "ok = True\n"})
    real_git = dl._git_bytes

    def failing_diff(repo_path: Path, *args: str, **options: str | None) -> bytes:
        if args[0] == "diff":
            raise dl.DenylistError("git diff failed: simulated")
        return real_git(repo_path, *args, **options)

    monkeypatch.setattr(dl, "_git_bytes", failing_diff)
    code, _, err = _scan(repo, hash_file, capsys)
    assert code == 2
    assert "simulated" in err


def test_missing_hash_file_fails_closed(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = _scan(repo, tmp_path / "absent.sha256", capsys)
    assert code == 2
    assert "cannot read hash file" in err


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda t: t.replace("format: 1", "format: 9"), "expected 'format: 1'"),
        (lambda t: "\n".join(line for line in t.splitlines() if not line.startswith("salt:")), "'salt'"),
        (lambda t: t.replace("max-tokens: 3", "max-tokens: 0"), "'max-tokens' must be between"),
        (lambda t: t.replace("max-tokens: 3", "max-tokens: many"), "'max-tokens' must be an integer"),
        (lambda t: t + "acme verify\n", "not a field or a lower-case SHA-256"),
        (lambda t: "\n".join(line for line in t.splitlines() if len(line) != 64), "contains no hashes"),
    ],
)
def test_malformed_hash_file_fails_closed(tmp_path: Path, hash_file: Path, mutate, reason: str) -> None:
    hash_file.write_text(mutate(hash_file.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(dl.DenylistError, match=reason):
        dl.load(hash_file)


# ── Building and the committed list ─────────────────────────────────────────


def test_build_writes_only_salt_and_digests(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    terms = tmp_path / "private-terms.txt"
    terms.write_text("# invented\nacme verify\nGlobex Screening Hub\n\n", encoding="utf-8")
    out_file = repo / "config" / "denylist.sha256"
    code = dl.main(["--repo", str(repo), "--hash-file", str(out_file), "build", "--terms-file", str(terms)])
    out = capsys.readouterr().out
    assert code == 0
    assert "with 2 terms" in out
    assert "acme" not in out.lower() and "globex" not in out.lower()
    written = out_file.read_text(encoding="utf-8").lower()
    assert "acme" not in written and "globex" not in written
    loaded = dl.load(out_file)
    assert len(loaded.hashes) == 2
    assert loaded.max_tokens == 3
    assert loaded.matches("AcmeVerify")


def test_build_keep_salt_reuses_the_existing_salt(repo: Path, tmp_path: Path) -> None:
    terms = tmp_path / "private-terms.txt"
    terms.write_text("acme verify\n", encoding="utf-8")
    out_file = repo / "denylist.sha256"
    base = ["--repo", str(repo), "--hash-file", str(out_file), "build", "--terms-file", str(terms)]
    assert dl.main(base) == 0
    first = dl.load(out_file).salt
    assert dl.main([*base, "--keep-salt"]) == 0
    assert dl.load(out_file).salt == first
    assert dl.main(base) == 0
    assert dl.load(out_file).salt != first


def test_build_refuses_a_terms_file_inside_the_repository(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    terms = repo / "terms.txt"
    terms.write_text("acme verify\n", encoding="utf-8")
    out_file = repo / "denylist.sha256"
    code = dl.main(["--repo", str(repo), "--hash-file", str(out_file), "build", "--terms-file", str(terms)])
    assert code == 2
    assert "outside the repository" in capsys.readouterr().err
    assert not out_file.exists()


def test_audit_reports_mentions_that_predate_the_check(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = dl.main(["--repo", str(repo), "--hash-file", str(hash_file), "audit"])
    out = capsys.readouterr().out
    assert code == 1
    assert "existing.py:1" in out
    assert "verify" not in out.lower()


def test_audit_can_be_limited_to_paths(repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, {"clean/ok.py": "ok = True\n"})
    base = ["--repo", str(repo), "--hash-file", str(hash_file), "audit"]
    assert dl.main([*base, "clean"]) == 0
    assert dl.main([*base, "existing.py"]) == 1
    assert "existing.py:1" in capsys.readouterr().out


def test_audit_of_paths_that_match_no_tracked_file_fails_closed(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = dl.main(["--repo", str(repo), "--hash-file", str(hash_file), "audit", "no/such/dir"])
    assert code == 2
    assert "no tracked file" in capsys.readouterr().err


def test_audit_reads_files_with_nul_bytes_utf16_text_and_binary_metadata(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The audit used to skip every file holding a NUL byte.
    tagged = _VIDEO_LIKE + b"\x00\x00\x00\x20\xa9too encoded by AcmeVerify Studio\x00" + _VIDEO_LIKE
    files = {
        "media/clip.mp4": tagged,
        "media/clean.mp4": _VIDEO_LIKE,
        "docs/vendors.md": b"intro\x00\nvendor\tAcme\tVerify\n",
        "docs/notes.txt": "Built with Ácme\tVérify\n".encode("utf-16"),
        "docs/split.md": "intro\N{LINE SEPARATOR}Acme\x0cVerify\n".encode(),
    }
    _commit_bytes(repo, files)
    code = dl.main(["--repo", str(repo), "--hash-file", str(hash_file), "audit", "media", "docs"])
    out = capsys.readouterr().out
    assert code == 1
    assert "media/clip.mp4 (binary, readable text at byte" in out
    assert "media/clean.mp4" not in out
    assert "docs/vendors.md:2 (binary, read as text) (word 2)" in out
    assert "docs/notes.txt:1 (binary, read as UTF-16) (word 3)" in out
    assert "docs/split.md:1 (word 2)" in out
    assert "verify" not in out.lower()


# ── House terminology (warnings) ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "preferred"),
    [
        ("pull the kill switch", "operator override"),
        ("KillSwitch()", "operator override"),
        ("kill_switch_enabled = True", "operator override"),
        ("killswitches", "operator override"),
        ("a white-label console", "issuer-branded"),
        ("WhiteLabelTheme", "issuer-branded"),
        ("white-labelled portal", "issuer-branded"),
        ("anomaly detection", "irregularity"),
        ("flag anomalies", "irregularity"),
        ("ANOMALOUS_SPEND", "irregularity"),
        ("our trust provider", "accredited issuer"),
        ("TrustProviders", "accredited issuer"),
        ("verification partners", "accredited issuer"),
        ("store the verification result", "attestation"),
        ("verificationResults", "attestation"),
    ],
)
def test_house_terms_match_spelling_variants(text: str, preferred: str) -> None:
    assert [match.preferred for match in dl.house_terms(text)] == [preferred]


@pytest.mark.parametrize(
    "text",
    [
        "kill the worker, then switch regions",
        "label the white box",
        "results of the verification",
        "the provider we trust",
        "the registry directory",
        "a consumer group",
        "software name and version",
    ],
)
def test_ordinary_words_are_not_house_terms(text: str) -> None:
    assert dl.house_terms(text) == []


def test_house_term_match_reports_word_position_and_preferred_term() -> None:
    assert dl.house_terms("stop it with the kill switch") == [dl.TermMatch(4, "kill switch", "operator override")]


def test_terminology_terms_warn_but_do_not_fail(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit(
        repo,
        {"docs/ops.md": "intro\nUse the kill switch to stop agents.\nA white-label console.\n"},
        "docs: record the verification result",
    )
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 0
    assert "no denylisted terms" in out
    assert "docs/ops.md:2 (word 3)" in out and "operator override" in out
    assert "docs/ops.md:3 (word 2)" in out and "issuer-branded" in out
    assert "message line 1" in out and "attestation" in out


def test_terminology_warnings_leave_a_failing_result_failing(
    repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _commit(repo, {"providers/client.py": "# anomaly score\nclient = 'AcmeVerify'\n"})
    code, out, _ = _scan(repo, hash_file, capsys)
    assert code == 1
    assert "providers/client.py:2 (word 2)" in out
    assert "providers/client.py:1 (word 1)" in out and "irregularity" in out
    assert "verify" not in out.lower()


def test_audit_prints_terminology_warnings(repo: Path, hash_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, {"notes.md": "Ask the trust provider.\n"})
    base = ["--repo", str(repo), "--hash-file", str(hash_file), "audit"]
    assert dl.main([*base, "notes.md"]) == 0
    out = capsys.readouterr().out
    assert "notes.md:1 (word 3)" in out and "accredited issuer" in out
    assert dl.main(base) == 1  # existing.py still names a denylisted term
    assert "accredited issuer" in capsys.readouterr().out


# ── The committed tree and CI ───────────────────────────────────────────────

# Where the audit used to fail: the screening connector that named its vendor,
# its generator entry and the references to it. The whole tree is audited by
# the Vendor Denylist workflow's audit job, which takes about a minute: too
# long for the unit suite.
PATHS_THAT_NAMED_A_VENDOR = ("connectors", "scripts", "core/langgraph", "core/agents/prompts", "manifests")


def test_audit_passes_on_the_paths_that_named_a_vendor(capsys: pytest.CaptureFixture[str]) -> None:
    code = dl.main(["--repo", str(REPO_ROOT), "audit", *PATHS_THAT_NAMED_A_VENDOR])
    out = capsys.readouterr().out
    assert code == 0, out


def test_ci_audits_the_whole_tree_on_pull_requests_and_main() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "vendor-denylist.yml").read_text("utf-8"))
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on` key as true
    assert "pull_request" in triggers
    assert "main" in triggers["push"]["branches"]
    audit_steps = [
        (job, step)
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if re.fullmatch(r"python scripts/check_denylist\.py audit", step.get("run", "").strip())
    ]
    assert audit_steps, "no job runs the whole-tree audit"
    for job, step in audit_steps:
        assert not job.get("continue-on-error") and not step.get("continue-on-error")


def test_committed_denylist_is_well_formed_and_holds_no_plaintext() -> None:
    path = REPO_ROOT / "config" / "denylist.sha256"
    loaded = dl.load(path)
    assert len(loaded.hashes) >= 50
    assert len(loaded.salt) >= 16
    for line in path.read_text(encoding="utf-8").splitlines():
        assert line.startswith(("#", "format:", "salt:", "max-tokens:")) or len(line) == 64
