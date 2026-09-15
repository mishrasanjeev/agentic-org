#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Refuse changes that name a denylisted third-party vendor.

The plain terms are not stored in the repository. ``config/denylist.sha256``
holds a random salt and the salted SHA-256 of each normalised term, which keeps
the names out of the tree, diffs and search results. The hashes are not a
secret: the salt is committed next to them, so anyone can test a guessed name.

``scan`` looks at everything a pull request adds: the added lines of the diff,
the changed file paths, the branch name, the commit messages and any extra
text files (the CI job passes the pull request title and body). Each input is
split into word tokens - on punctuation, whitespace, digit boundaries and
camelCase - and every run of consecutive tokens, up to ``max-tokens`` plus a
little headroom for inputs that split a term into more pieces than the list
did, is joined without separators, lower-cased and hashed. ``Acme Verify``,
``acme_verify``, ``AcmeVerify`` and ``ACME-VERIFY`` therefore all match the term
``acme verify``. A term glued to the end of a single longer word
(``myacmeverify``) is caught too: every tail of each word is also tried.
A match is reported by where it is (file and line, commit, branch or text file
and word number) rather than by quoting the matched words.

Fails closed (exit 2) when git fails, a ref does not resolve, the hash file is
missing or malformed, or an extra text file is unreadable or not UTF-8. Exit 1 means a term was found; 0 means none.

    python scripts/check_denylist.py scan --base origin/main --head HEAD
    python scripts/check_denylist.py audit
    python scripts/check_denylist.py build --terms-file /path/outside/the/repo/terms.txt

``audit`` applies the same matching to every tracked file, to locate mentions
that predate the check.

``build`` rewrites the hash file from a plaintext list kept outside the
repository (one term per line, ``#`` comments allowed). It refuses a terms file
inside the working tree and prints only the number of terms.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import subprocess
import sys
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

FORMAT_VERSION = "1"
DEFAULT_HASH_FILE = Path("config/denylist.sha256")
_TOKEN_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
# Candidates for encoded data (lockfile integrity hashes, digests, keys); see looks_encoded.
_RUN_RE = re.compile(r"[A-Za-z0-9+/=]{32,}")
_HEX_RE = re.compile(r"[0-9a-fA-F]{32,}")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_MAX_TOKENS_LIMIT = 8
# Extra tokens scanned beyond the longest term, for inputs that split a term
# into more tokens than the terms file did ("I-Den-Ti-Ty" for a one-token term).
WINDOW_HEADROOM = 2
# Shortest tail of a word tried as the start of a term ("my" + "acmeverify").
MIN_SUFFIX_CHARS = 4
_ENCODED_MIN_BASE64 = 40
_ENCODED_MIN_TRANSITIONS = 0.3


class DenylistError(RuntimeError):
    """The check cannot run reliably; callers must treat this as a failure."""


# ── Normalisation and hashing ───────────────────────────────────────────────


def _char_class(ch: str) -> int:
    return 0 if ch.isdigit() else 1 if ch.isupper() else 2 if ch.islower() else 3


def looks_encoded(run: str) -> bool:
    """True for hex digests and base64-like blobs; False for long identifiers.

    Hex needs 32+ hex characters with at least one digit and one letter. Base64
    needs 40+ characters mixing upper case, lower case and digits with frequent
    changes of character class, which random data has and long identifiers
    (``AcmeVerify2024ClientSettings``) do not. Containing digits alone is not
    enough to skip a run.
    """
    if _HEX_RE.fullmatch(run) and any(c.isdigit() for c in run) and any(c.isalpha() for c in run):
        return True
    if len(run) < _ENCODED_MIN_BASE64:
        return False
    if not (any(c.isupper() for c in run) and any(c.islower() for c in run) and any(c.isdigit() for c in run)):
        return False
    body = run.rstrip("=")
    changes = sum(1 for a, b in zip(body, body[1:], strict=False) if _char_class(a) != _char_class(b))
    return changes / max(len(body) - 1, 1) >= _ENCODED_MIN_TRANSITIONS


def tokens(text: str) -> list[str]:
    """Word tokens of ``text``: accents stripped, split on case and digit boundaries."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    words = _RUN_RE.sub(lambda m: " " if looks_encoded(m.group()) else m.group(), stripped)
    return [token.lower() for token in _TOKEN_RE.findall(words)]


def candidates(text: str, max_tokens: int) -> Iterator[tuple[int, str]]:
    """Joined runs of consecutive tokens that could spell a term; yields (start index, joined).

    A run starts at a token and spans up to ``max_tokens + WINDOW_HEADROOM``
    tokens. Every tail of a token at least ``MIN_SUFFIX_CHARS`` long is also a
    candidate on its own, for a term glued to the end of a word.
    """
    words = tokens(text)
    window = max_tokens + WINDOW_HEADROOM
    for start, first in enumerate(words):
        joined = first
        yield start, joined
        for end in range(start + 1, min(start + window, len(words))):
            joined += words[end]
            yield start, joined
        # A tail is tried on its own only: joining it to the next words would
        # match across ordinary word boundaries.
        for cut in range(1, len(first) - MIN_SUFFIX_CHARS + 1):
            yield start, first[cut:]


def term_key(term: str) -> str:
    return "".join(tokens(term))


def digest(salt: bytes, key: str) -> str:
    return hashlib.sha256(salt + b"\x00" + key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Denylist:
    salt: bytes
    max_tokens: int
    hashes: frozenset[str]

    def matches(self, text: str) -> list[int]:
        """Token positions in ``text`` where a denylisted term starts."""
        found: list[int] = []
        for start, key in candidates(text, self.max_tokens):
            if (not found or found[-1] != start) and digest(self.salt, key) in self.hashes:
                found.append(start)
        return found


def load(path: Path) -> Denylist:
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DenylistError(f"cannot read hash file {path}: {exc}") from exc
    return load_text(text, str(path))


def load_text(text: str, path: str = "<hash file>") -> Denylist:
    lines = text.splitlines()
    fields: dict[str, str] = {}
    hashes: set[str] = set()
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            name, _, value = line.partition(":")
            fields[name.strip()] = value.strip()
        elif _HEX64_RE.fullmatch(line):
            hashes.add(line)
        else:
            raise DenylistError(f"{path}:{number}: not a field or a lower-case SHA-256 hex digest")
    if fields.get("format") != FORMAT_VERSION:
        raise DenylistError(f"{path}: expected 'format: {FORMAT_VERSION}'")
    salt_hex = fields.get("salt", "")
    if not re.fullmatch(r"[0-9a-f]{32,}", salt_hex) or len(salt_hex) % 2:
        raise DenylistError(f"{path}: 'salt' must be at least 16 bytes of lower-case hex")
    try:
        max_tokens = int(fields.get("max-tokens", ""))
    except ValueError as exc:
        raise DenylistError(f"{path}: 'max-tokens' must be an integer") from exc
    if not 1 <= max_tokens <= _MAX_TOKENS_LIMIT:
        raise DenylistError(f"{path}: 'max-tokens' must be between 1 and {_MAX_TOKENS_LIMIT}")
    if not hashes:
        raise DenylistError(f"{path}: contains no hashes")
    return Denylist(salt=bytes.fromhex(salt_hex), max_tokens=max_tokens, hashes=frozenset(hashes))


# ── Git inputs ──────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    argv = ["git", "-C", str(repo), *args]
    try:
        result = subprocess.run(argv, capture_output=True, check=False)  # noqa: S603, S607 - fixed argv, no shell
    except OSError as exc:
        raise DenylistError(f"cannot run git: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise DenylistError(f"git {args[0]} failed: {detail or f'exit {result.returncode}'}")
    return result.stdout.decode("utf-8", "replace")


def _resolve(repo: Path, ref: str) -> str:
    try:
        return _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    except DenylistError as exc:
        raise DenylistError(f"cannot resolve ref {ref!r}") from exc


@dataclass(frozen=True)
class Finding:
    where: str
    position: int


def added_lines(repo: Path, base: str, head: str) -> Iterator[tuple[str, int, str]]:
    """(path, new line number, text) for every line the range adds."""
    diff = _git(repo, "diff", "--unified=0", "--no-color", "--no-ext-diff", "--text", f"{base}...{head}")
    path = ""
    line_no = 0
    # A file's header runs from "diff --git" to its first hunk. "+++ " is a
    # header only there, right after "--- "; inside a hunk the same prefix is
    # an added line that starts with "++" and is checked like any other.
    in_header = False
    previous = ""
    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            in_header, path = True, ""
        elif in_header and raw.startswith("+++ ") and previous.startswith("--- "):
            target = raw[4:]
            path = target[2:] if target.startswith("b/") else target
        elif raw.startswith("@@"):
            match = re.match(r"@@ -\S+ \+(\d+)", raw)
            if not match:
                raise DenylistError(f"unparseable diff hunk header: {raw[:80]}")
            if not path:
                raise DenylistError("diff hunk without a file header")
            in_header = False
            line_no = int(match.group(1))
        elif not in_header and raw.startswith("+"):
            yield path, line_no, raw[1:]
            line_no += 1
        previous = raw


def changed_paths(repo: Path, base: str, head: str) -> list[str]:
    out = _git(repo, "diff", "--name-only", "-z", "--no-renames", f"{base}...{head}")
    return [name for name in out.split("\0") if name]


def commit_messages(repo: Path, base: str, head: str) -> list[tuple[str, str]]:
    out = _git(repo, "log", "--format=%H%x00%B%x1e", f"{base}..{head}")
    messages = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, _, body = record.partition("\0")
        messages.append((sha[:12], body))
    return messages


def current_branch(repo: Path) -> str:
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


def scan(
    denylist: Denylist,
    repo: Path,
    base: str,
    head: str,
    *,
    branch: str | None,
    text_files: Iterable[Path] = (),
) -> list[Finding]:
    base_sha = _resolve(repo, base)
    head_sha = _resolve(repo, head)
    findings: list[Finding] = []

    def check(where: str, text: str) -> None:
        findings.extend(Finding(where, position) for position in denylist.matches(text))

    for path in changed_paths(repo, base_sha, head_sha):
        check(f"file path {path}", path)
    for path, line_no, text in added_lines(repo, base_sha, head_sha):
        check(f"{path}:{line_no}", text)
    for sha, body in commit_messages(repo, base_sha, head_sha):
        for offset, line in enumerate(body.splitlines(), start=1):
            check(f"commit {sha} message line {offset}", line)
    branch_name = branch if branch is not None else current_branch(repo)
    check("branch name", branch_name)
    for text_file in text_files:
        try:
            content = text_file.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise DenylistError(f"cannot read {text_file}: {exc}") from exc
        for offset, line in enumerate(content.splitlines(), start=1):
            check(f"{text_file.name} line {offset}", line)
    return findings


def audit(denylist: Denylist, repo: Path) -> list[Finding]:
    """Check every tracked path and text file at the working tree, not just a change."""
    findings: list[Finding] = []
    for name in _git(repo, "ls-files", "-z").split("\0"):
        if not name:
            continue
        findings.extend(Finding(f"file path {name}", position) for position in denylist.matches(name))
        try:
            data = (repo / name).read_bytes()
        except OSError:
            continue  # tracked but absent from the working tree (sparse or deleted)
        if b"\0" in data:
            continue  # binary
        for line_no, line in enumerate(data.decode("utf-8", "replace").splitlines(), start=1):
            findings.extend(Finding(f"{name}:{line_no}", position) for position in denylist.matches(line))
    return findings


# ── Building the hash file ──────────────────────────────────────────────────


def read_terms(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DenylistError(f"cannot read terms file: {exc}") from exc
    terms = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    if not terms:
        raise DenylistError("terms file contains no terms")
    return terms


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def render(terms: list[str], salt: bytes) -> tuple[str, int]:
    keys = {term_key(term) for term in terms}
    keys.discard("")
    if not keys:
        raise DenylistError("no term has any word characters")
    counts = [len(tokens(term)) for term in terms if term_key(term)]
    max_tokens = max(counts)
    if max_tokens > _MAX_TOKENS_LIMIT:
        raise DenylistError(f"a term has more than {_MAX_TOKENS_LIMIT} tokens")
    body = "\n".join(sorted(digest(salt, key) for key in keys))
    text = (
        "# Vendor-name denylist for scripts/check_denylist.py (see 'Vendor-neutral names' in CONTRIBUTING.md).\n"
        "# Salted SHA-256 digests of normalised terms. The plaintext list is kept outside the\n"
        "# repository and is never committed; regenerate with `check_denylist.py build`.\n"
        f"format: {FORMAT_VERSION}\n"
        f"salt: {salt.hex()}\n"
        f"max-tokens: {max_tokens}\n"
        f"{body}\n"
    )
    return text, len(keys)


def build(terms_file: Path, hash_file: Path, repo: Path, *, keep_salt: bool) -> int:
    work_tree = Path(_git(repo, "rev-parse", "--show-toplevel").strip())
    if _inside(terms_file, work_tree):
        raise DenylistError("the plaintext terms file must live outside the repository working tree")
    terms = read_terms(terms_file)
    salt = load(hash_file).salt if keep_salt else secrets.token_bytes(32)
    text, count = render(terms, salt)
    hash_file.parent.mkdir(parents=True, exist_ok=True)
    hash_file.write_text(text, encoding="utf-8", newline="\n")
    return count


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=".")
    parser.add_argument("--hash-file", type=Path, default=None, help=f"default: {DEFAULT_HASH_FILE}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="check a commit range")
    scan_p.add_argument("--base", default=os.environ.get("BASE_REF", "origin/main"))
    scan_p.add_argument("--head", default=os.environ.get("HEAD_REF", "HEAD"))
    scan_p.add_argument("--branch", default=None, help="branch name to check (default: the checked-out branch)")
    scan_p.add_argument("--text-file", type=Path, action="append", default=[], help="extra text to check")

    sub.add_parser("audit", help="check every tracked file, e.g. to find existing mentions")

    build_p = sub.add_parser("build", help="regenerate the hash file from a plaintext terms file")
    build_p.add_argument("--terms-file", type=Path, required=True)
    build_p.add_argument("--keep-salt", action="store_true", help="reuse the existing salt")

    args = parser.parse_args(argv)
    repo = Path(args.repo)
    hash_file = args.hash_file or repo / DEFAULT_HASH_FILE

    try:
        if args.command == "build":
            count = build(args.terms_file, hash_file, repo, keep_salt=args.keep_salt)
            print(f"check_denylist: wrote {hash_file} with {count} terms")
            return 0
        denylist = load(hash_file)
        if args.command == "audit":
            findings = audit(denylist, repo)
        else:
            findings = scan(denylist, repo, args.base, args.head, branch=args.branch, text_files=args.text_file)
    except DenylistError as exc:
        print(f"check_denylist: {exc}", file=sys.stderr)
        return 2

    if findings:
        subject = "the tracked files" if args.command == "audit" else "this change"
        print(f"A denylisted vendor name appears in {subject}. Use a provider-neutral name instead")
        print("(the mock provider is `mock`; documentation and examples use `acme_kyb`):")
        for finding in findings:
            print(f"  {finding.where} (word {finding.position + 1})")
        return 1
    print(f"check_denylist: no denylisted terms ({len(denylist.hashes)} terms checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
