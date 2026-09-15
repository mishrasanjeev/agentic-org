# SPDX-License-Identifier: Apache-2.0
"""Vendor-name denylist check (scripts/check_denylist.py).

Uses its own salt and invented terms; the committed hash list is only checked
for shape.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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
    real_git = dl._git

    def failing_diff(repo_path: Path, *args: str) -> str:
        if args[0] == "diff":
            raise dl.DenylistError("git diff failed: simulated")
        return real_git(repo_path, *args)

    monkeypatch.setattr(dl, "_git", failing_diff)
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


def test_committed_denylist_is_well_formed_and_holds_no_plaintext() -> None:
    path = REPO_ROOT / "config" / "denylist.sha256"
    loaded = dl.load(path)
    assert len(loaded.hashes) >= 50
    assert len(loaded.salt) >= 16
    for line in path.read_text(encoding="utf-8").splitlines():
        assert line.startswith(("#", "format:", "salt:", "max-tokens:")) or len(line) == 64
