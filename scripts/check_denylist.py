#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Refuse changes that name a denylisted third-party vendor.

The plain terms are not stored in the repository. ``config/denylist.sha256``
holds a random salt and the salted SHA-256 of each normalised term, which keeps
the names out of the tree, diffs and search results. The hashes are not a
secret: the salt is committed next to them, so anyone can test a guessed name.

``scan`` looks at everything a pull request adds: the added lines of the diff,
every binary file it adds or changes, the changed file paths, the branch name,
the commit messages and any extra text files (the CI job passes the pull
request title and body).

Text is split into lines on "\\n" alone, as git splits a diff; nothing else
(not "\\r", "\\f" or U+2028) ends a line. A line that is not UTF-8 is also read
without its NUL bytes and, if it holds no other control bytes, as Windows-1252
(see ``line_texts``). Every file the change adds or changes is read by object
id. One in UTF-16 or UTF-32 (with a byte-order mark, or text with NUL bytes
where they fall in that encoding) is decoded and checked line by line, whether
git diffs it as text or calls it binary. Any other binary file - one git's diff calls
binary, judged with the base commit's attributes so a change cannot reclassify
its own files - is checked line by line when it is text apart from its NUL
bytes and a few odd bytes, and otherwise for the runs of readable text inside
it, where video and image metadata keep names; there, a term shorter than five
characters counts only as a whole word beside other words, as random bytes
spell short terms by chance inside other words (see ``_RUN_MIN_CHARS`` and
``Report.check_file``). ``audit`` reads every file the same way. This needs
git 2.40 or later (``--attr-source``).

Each input is split into word tokens - on punctuation, whitespace, digit
boundaries and camelCase - and every run of consecutive tokens, up to ``max-tokens`` plus a
little headroom for inputs that split a term into more pieces than the list
did, is joined without separators, lower-cased and hashed. ``Acme Verify``,
``acme_verify``, ``AcmeVerify`` and ``ACME-VERIFY`` therefore all match the term
``acme verify``. A term glued to the end of a single longer word
(``myacmeverify``) is caught too: every tail of each word is also tried.
A match is reported by where it is (file and line, commit, branch or text file
and word number) rather than by quoting the matched words.

Fails closed (exit 2) when git fails (a file the change adds cannot be read, for
example), a ref does not resolve, the hash file is
missing or malformed, or an extra text file is unreadable or not UTF-8. Exit 1 means a term was found; 0 means none.

    python scripts/check_denylist.py scan --base origin/main --head HEAD
    python scripts/check_denylist.py audit
    python scripts/check_denylist.py audit connectors docs
    python scripts/check_denylist.py build --terms-file /path/outside/the/repo/terms.txt

``audit`` applies the same matching to every tracked file, or to the tracked
files under the paths it is given, to locate mentions that predate the check.
Paths that match no tracked file fail closed.

``scan`` and ``audit`` also warn about house terminology (``HOUSE_TERMS``: a
kill switch is an operator override, white-label is issuer-branded, and so on).
Those words are not secret, so they are listed and printed in plain text, and a
warning never changes the exit code.

``build`` rewrites the hash file from a plaintext list kept outside the
repository (one term per line, ``#`` comments allowed). It refuses a terms file
inside the working tree and prints only the number of terms.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import heapq
import itertools
import os
import re
import secrets
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter
from collections.abc import Container, Iterable, Iterator
from dataclasses import dataclass, field
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


def _normalise(text: str) -> str:
    """``text`` as tokens are read from it: accents stripped, encoded data blanked out."""
    if text.isascii():
        stripped = text  # nothing to decompose (and much faster for the many runs in binary files)
    else:
        decomposed = unicodedata.normalize("NFKD", text)
        stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _RUN_RE.sub(lambda m: " " if looks_encoded(m.group()) else m.group(), stripped)


def tokens(text: str) -> list[str]:
    """Word tokens of ``text``: accents stripped, split on case and digit boundaries."""
    return [token.lower() for token in _TOKEN_RE.findall(_normalise(text))]


# Characters a token is made of; a token with none of these on either side is a whole word.
_WORD_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
# The length of the other words a run of binary data must hold for a short term in it to count.
_PROSE_WORD_CHARS = 3
# How many such words the run must hold when no whitespace is beside the short term, as for a value in markup.
_MARKUP_OTHER_WORDS = 2


def _letter_word(token: str) -> bool:
    return len(token) >= _PROSE_WORD_CHARS and token.isalpha()


def _prose_word(text: str, start: int, end: int, *, others: int) -> bool:
    """Whether the token at ``text[start:end]`` stands as a word among other words.

    It must be a whole word (no letter or digit on either side), and ``text``
    must hold other words of ``_PROSE_WORD_CHARS`` or more letters
    (``others``): one when whitespace is right before or after it ("Encoded
    with Xyzw Studio"), and ``_MARKUP_OTHER_WORDS`` when only punctuation
    bounds it, as it does a value in markup ("<xmp:CreatorTool>Xyzw</xmp:
    CreatorTool>", "/Producer (Xyzw) /Title (Demo)"). Not "q;Xyzw/7", and
    not a value with no words beside it. See ``_RUN_MIN_CHARS`` for why.
    """
    before = text[start - 1] if start else ""
    after = text[end] if end < len(text) else ""
    if before in _WORD_CHARS or after in _WORD_CHARS:
        return False
    return others >= (1 if before.isspace() or after.isspace() else _MARKUP_OTHER_WORDS)


def candidates(text: str, max_tokens: int) -> Iterator[tuple[int, str]]:
    """Joined runs of consecutive tokens that could spell a term; yields (start index, joined).

    A run starts at a token and spans up to ``max_tokens + WINDOW_HEADROOM``
    tokens. Every tail of a token at least ``MIN_SUFFIX_CHARS`` long is also a
    candidate on its own, for a term glued to the end of a word.
    """
    return _joined(tokens(text), max_tokens)


def _joined(words: list[str], max_tokens: int) -> Iterator[tuple[int, str]]:
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

    def matches(self, text: str, *, min_chars: int = 1) -> list[int]:
        """Token positions in ``text`` where a denylisted term starts.

        A term shorter than ``min_chars`` characters counts only as a word of
        prose (``_prose_word``): never joined to its neighbours, nor as the
        tail of a longer word.
        """
        normalised = _normalise(text)
        words = [token.lower() for token in _TOKEN_RE.findall(normalised)]
        found: list[int] = []
        for start, key in _joined(words, self.max_tokens):
            if (not found or found[-1] != start) and len(key) >= min_chars and digest(self.salt, key) in self.hashes:
                found.append(start)
        if min_chars > 1 and len(words) > 1:  # a short term counts only beside another word
            hits = [
                i for i, word in enumerate(words) if len(word) < min_chars and digest(self.salt, word) in self.hashes
            ]
            if hits:  # rare: only now is it worth finding where the words are
                spans = [token.span() for token in _TOKEN_RE.finditer(normalised)]
                letter_words = sum(1 for word in words if _letter_word(word))
                short = {
                    i for i in hits if _prose_word(normalised, *spans[i], others=letter_words - _letter_word(words[i]))
                }
                found = sorted(short.union(found))
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


# ── House terminology (warnings) ────────────────────────────────────────────

# Each house term: a pattern over a line's word tokens joined by single spaces
# (the tokens the denylist hashes, so ``kill_switch``, ``KillSwitch`` and
# ``kill-switch`` all read ``kill switch``) and the term to use instead.
# ``directory`` (for the registry), ``consumer`` (for a relying party) and
# ``name`` / ``version`` (for ``software_name`` / ``software_version``) have too
# many ordinary meanings to flag and are reviewed by hand.
HOUSE_TERMS: tuple[tuple[str, str], ...] = (
    (r"kill ?switch(?:es)?", "operator override"),
    (r"white ?label(?:s|l?ed|l?ing)?", "issuer-branded"),
    (r"anomal(?:y|ies|ous|ously)", "irregularity"),
    (r"trust ?providers?", "accredited issuer"),
    (r"verification ?partners?", "accredited issuer"),
    (r"verification ?results?", "attestation"),
)
_HOUSE_TERM_RES = tuple(
    (re.compile(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])"), preferred) for pattern, preferred in HOUSE_TERMS
)
# Every house term contains one of these, so a line with none of them skips tokenising.
_HOUSE_TERM_STEMS = ("kill", "label", "anomal", "trust", "verification")


@dataclass(frozen=True)
class TermMatch:
    position: int
    found: str
    preferred: str


def house_terms(text: str) -> list[TermMatch]:
    """House terms in ``text``, with the token position where each starts and the term to use."""
    lowered = text.lower()
    if not any(stem in lowered for stem in _HOUSE_TERM_STEMS):
        return []
    joined = " ".join(tokens(text))
    matches = [
        TermMatch(joined.count(" ", 0, match.start()), match.group(), preferred)
        for pattern, preferred in _HOUSE_TERM_RES
        for match in pattern.finditer(joined)
    ]
    return sorted(matches, key=lambda match: match.position)


# ── Reading bytes as text ───────────────────────────────────────────────────
#
# Every input is split into lines on "\n" alone, which is how git splits a diff.
# str.splitlines() would also break at "\r", "\v", "\f", "\x1c"-"\x1e", U+0085,
# U+2028 and U+2029: the text after one of those would lose the diff's leading
# "+" and go unchecked, or could pose as a diff header.

# Bytes no line of text holds: C0 controls other than whitespace, and DEL.
_CONTROL_BYTES = re.compile(rb"[\x00-\x08\x0e-\x1f\x7f]")
_CONTROL_BYTE_SET = bytes([*range(0x09), *range(0x0E, 0x20), 0x7F])


def _utf8(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def line_texts(raw: bytes) -> list[str]:
    """The readings of one line of bytes that are checked; a term found by several is reported once.

    The line is read as UTF-8, each invalid byte becoming a word break. A line
    holding NUL bytes is also read with them removed (ASCII text stored as
    UTF-16 or UTF-32, or a NUL wedged inside a word). A line that is not UTF-8,
    even without its NULs, is also read that way as Windows-1252, so legacy
    8-bit text keeps its accented letters, unless it holds other control bytes:
    those mark binary data, in which Windows-1252 would find a letter in nearly
    every byte.
    """
    text = _utf8(raw)
    if text is not None and "\0" not in text:
        return [text]
    readings = [raw.decode("utf-8", "replace")]
    stripped = raw.replace(b"\0", b"")
    if len(stripped) != len(raw):
        readings.append(stripped.decode("utf-8", "replace"))
    if _utf8(stripped) is None and not _CONTROL_BYTES.search(stripped):
        readings.append(stripped.decode("cp1252", "replace"))
    return readings


# Bytes are text, once their NUL bytes are removed, when odd bytes - control
# bytes and bytes that are not part of UTF-8 - make up at most 2% of them, or
# number at most 8. Text holds almost none, so a stray control or invalid byte
# in a text file, or a few, leaves it text. Compressed data such as video,
# images and archives is close to random, and there more than half the bytes
# are odd (11% control bytes, and most bytes from 0x80 up); executables hold
# more. Text is read for terms of any length (``line_texts``), which in random
# bytes finds the committed list's terms by chance about once in 2 MB, so the
# limits keep random-looking bytes in a file read as text to about 4% of it,
# or a dozen bytes. Legacy 8-bit text with NUL bytes in it can fall on the
# binary side, as its accented letters are not UTF-8.
_TEXT_MAX_ODD_SHARE = 0.02
_TEXT_MAX_ODD = 8


def _mostly_text(data: bytes) -> bool:
    """Whether ``data`` (without NUL bytes) is text by the measure above."""
    limit = max(_TEXT_MAX_ODD, len(data) * _TEXT_MAX_ODD_SHARE)
    controls = len(data) - len(data.translate(None, _CONTROL_BYTE_SET))
    if controls > limit:
        return False  # most binary data: no need to decode it
    not_utf8 = len(data) - len(data.decode("utf-8", "ignore").encode("utf-8"))
    return controls + not_utf8 <= limit


_WIDE_BOMS = (
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)
# UTF-16 or UTF-32 text without a byte-order mark: the high bytes of an ASCII
# character are NUL, so they are NUL in at least this share of the characters,
# and ten times as often as the low byte. A decoding counts only when it is
# almost all text.
_WIDE_NUL_SHARE = 0.1
_WIDE_MAX_NON_TEXT = 0.02
# Without a byte-order mark the encoding is a guess, and a decoding counts only
# when it reads as text in some script (``_reads_as_text``): half its characters
# ASCII text, or one in a hundred whitespace, or all but one in twenty of the
# others from at most four scripts (Japanese mixes kanji, hiragana and katakana;
# Chinese has long lines with no whitespace). An array of 16- or 32-bit numbers
# below 65,536 has the NULs of such text but decodes to characters all over
# Unicode: in random arrays of numbers from ranges between 0 and 65,535, under
# 0.2% of the characters were whitespace, and the four commonest scripts held at
# most 90% of the others.
_WIDE_GUESS_MIN_ASCII = 0.5
_WIDE_GUESS_MIN_WHITESPACE = 0.01
_WIDE_GUESS_SCRIPTS = 4
_WIDE_GUESS_MIN_SCRIPT_SHARE = 0.95
_WIDE_GUESS_SAMPLE = 20_000  # the characters the script count looks at
_ASCII_TEXT = re.compile(r"[\t\n\r\x20-\x7e]")
_ANY_WHITESPACE = re.compile(r"\s")
# Characters text does not hold: controls other than whitespace, and code units that did not decode.
_NON_TEXT = re.compile(r"[\x00-\x08\x0e-\x1f\x7f-\x9f\N{REPLACEMENT CHARACTER}]")


def _wide_codecs(data: bytes) -> list[tuple[str, int]]:
    """(codec, byte-order mark length) of each UTF-16 or UTF-32 encoding ``data`` may be in."""
    for bom, codec in _WIDE_BOMS:
        if data.startswith(bom):
            return [(codec, len(bom))]
    quads = len(data) // 4
    nuls = [data[byte : 4 * quads : 4].count(0) for byte in range(4)]
    least = max(quads * _WIDE_NUL_SHARE, 1)

    def mostly_nul(high: list[int], low: list[int]) -> bool:
        return all(nuls[i] >= least and nuls[i] >= 10 * nuls[j] for i in high for j in low)

    guesses = (
        ("utf-32-le", [2, 3], [0]),
        ("utf-32-be", [0, 1], [3]),
        ("utf-16-le", [1, 3], [0, 2]),
        ("utf-16-be", [0, 2], [1, 3]),
    )
    return [(codec, 0) for codec, high, low in guesses if mostly_nul(high, low)]


def _script(ch: str) -> str:
    """The first word of a character's Unicode name ("CYRILLIC", "CJK", "HIRAGANA"); "" when it has none."""
    return unicodedata.name(ch, "").split(" ", 1)[0]


def _reads_as_text(text: str) -> bool:
    """Whether a decoding guessed without a byte-order mark reads as text in some script (see ``_WIDE_GUESS_MIN_ASCII``)."""
    if _ASCII_TEXT.subn("", text)[1] >= len(text) * _WIDE_GUESS_MIN_ASCII:
        return True
    if _ANY_WHITESPACE.subn("", text)[1] >= len(text) * _WIDE_GUESS_MIN_WHITESPACE:
        return True
    others = [ch for ch in text[:_WIDE_GUESS_SAMPLE] if ch > "\x7f"]
    scripts = Counter(_script(ch) for ch in others)
    scripts.pop("", None)  # unassigned code points
    scripts.pop("REPLACEMENT", None)  # code units that did not decode
    held = sum(count for _, count in scripts.most_common(_WIDE_GUESS_SCRIPTS))
    return held >= len(others) * _WIDE_GUESS_MIN_SCRIPT_SHARE


def _wide_text(data: bytes) -> tuple[str, str] | None:
    """(encoding name, text) when ``data`` is UTF-16 or UTF-32 text."""
    for codec, skip in _wide_codecs(data):
        text = data[skip:].decode(codec, "replace")
        if not text or _NON_TEXT.subn("", text)[1] > len(text) * _WIDE_MAX_NON_TEXT:
            continue
        if not skip and not _reads_as_text(text):
            continue
        return codec[:6].upper(), text
    return None


# Readable text inside binary data is checked in runs of at least five
# characters (readable_runs). A metadata value - a title, artist or encoder tag
# in a video, a field in image metadata - is a run of its own between binary
# bytes, so a five-letter term standing alone there is still found, and so is
# a shorter term standing as a word in a longer value ("Encoded with Xyzw
# Studio"). Compressed media is close to random bytes, and there short terms
# turn up by chance inside other words: in 3 GB of random bytes a printable run
# spelled one of the committed list's four-character terms 29 times (about
# once per 100 MB; a run minimum of eight still left one in 600 MB), while no
# term of five or more characters matched at all, and neither did anything in
# the 31 MB of videos and images in the repository's history. So in a run a
# term shorter than five characters counts only as a word among words
# (``_prose_word``): a whole word, in a run with another word of three or more
# letters when whitespace is next to it, or with two such words when only
# punctuation bounds it (a value in XMP, a PDF dictionary or JSON). 1 GB of
# random bytes held 14,179 whole four-letter words next to whitespace, which
# would spell one of the two committed four-character terms about once in
# 16 GB, and 326 that also had the other words these rules ask for (one of
# them bounded only by punctuation): about once in 700 GB. With these rules
# that 1 GB gave no chance match of any committed term. A run shorter than
# five characters is not read: a lone metadata value that short is too often a
# chance one.
_RUN_MIN_CHARS = 5
# A UTF-8 run: printable ASCII, whitespace other than "\n" (which ends a run as
# it ends a line) and any decodable character outside the C0 and C1 controls,
# so accented letters and non-ASCII spaces stay inside a run. Undecodable bytes
# are read as lone surrogates (U+DC80-U+DCFF), which end a run.
_TEXT_RUN = re.compile(r"[\t\v\f\r\x20-\x7e\xa0-\ud7ff\ue000-\U0010ffff]" + f"{{{_RUN_MIN_CHARS},}}")
# A single NUL or control byte between two words of two or more letters is read
# as the space it stands for ("Acme<NUL>Verify"), so the run goes on and the
# words are checked together. Random bytes hold that about once in 5,000
# bytes, which makes their runs about 4% longer in all.
_RUN_GLUE = re.compile(r"[\x00-\x08\x0e-\x1f\x7f](?<=[^\W\d_]{2}.)(?=[^\W\d_]{2})")
# A UTF-16 or UTF-32 run, in either byte order: ASCII characters or Latin-1
# letters two (or four) bytes apart, with NULs between them. The pattern starts
# at a NUL so the search can skip ahead quickly; the character before that
# first NUL is added back.
_WIDE_UNIT = rb"[\t\v\f\r\x20-\x7e\xa0-\xff]"
_WIDE_UNITS = frozenset(byte for byte in range(256) if re.fullmatch(_WIDE_UNIT, bytes([byte])))
_WIDE_RUNS = {
    width: re.compile(rb"(?:\x00{%d}%s){%d,}" % (width - 1, _WIDE_UNIT, _RUN_MIN_CHARS - 1)) for width in (2, 4)
}


def _utf8_runs(data: bytes) -> Iterator[tuple[int, str]]:
    # The glue byte and the space that replaces it are one byte each in UTF-8, so offsets are unchanged.
    text = _RUN_GLUE.sub(" ", data.decode("utf-8", "surrogateescape"))
    chars = offset = 0
    for match in _TEXT_RUN.finditer(text):
        offset += len(text[chars : match.start()].encode("utf-8", "surrogateescape"))
        chars = match.start()
        yield offset, match.group()


def _wide_runs(data: bytes, width: int) -> Iterator[tuple[int, str]]:
    for match in _WIDE_RUNS[width].finditer(data):
        start, end = match.span()
        start = start - 1 if start and data[start - 1] in _WIDE_UNITS else start + width - 1
        units = data[start:end:width]
        if len(units) >= _RUN_MIN_CHARS:
            yield start, units.decode("latin-1")


def readable_runs(data: bytes) -> Iterator[tuple[int, str]]:
    """(byte offset, text) of each run of readable UTF-8, UTF-16 or UTF-32 text in binary data, in byte order."""
    runs = (_utf8_runs(data), _wide_runs(data, 2), _wide_runs(data, 4))
    return heapq.merge(*runs, key=lambda run: run[0])


# ── Git inputs ──────────────────────────────────────────────────────────────


def _git_bytes(repo: Path, *args: str, attr_source: str | None = None) -> bytes:
    """Output of ``git args``; ``attr_source`` makes git read .gitattributes from that commit only."""
    options = [f"--attr-source={attr_source}"] if attr_source else []
    argv = ["git", "-C", str(repo), *options, *args]
    try:
        result = subprocess.run(argv, capture_output=True, check=False)  # noqa: S603, S607 - fixed argv, no shell
    except OSError as exc:
        raise DenylistError(f"cannot run git: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise DenylistError(f"git {args[0]} failed: {detail or f'exit {result.returncode}'}")
    return result.stdout


def _git(repo: Path, *args: str) -> str:
    return _git_bytes(repo, *args).decode("utf-8", "replace")


def _resolve(repo: Path, ref: str) -> str:
    try:
        return _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    except DenylistError as exc:
        raise DenylistError(f"cannot resolve ref {ref!r}") from exc


def read_objects(repo: Path, oids: Iterable[str], kind: str) -> Iterator[tuple[str, bytes]]:
    """(object id, content) of each object, read by id through a single ``git cat-file --batch``.

    Reading by id means no path is ever parsed as a pathspec (a name starting
    with ":" is just a name), and one process serves any number of objects, so
    no command line grows with the size of a change. An object that is missing
    or of another kind fails closed.
    """
    argv = ["git", "-C", str(repo), "cat-file", "--batch"]
    with tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)  # noqa: S603 - fixed argv, no shell
        except OSError as exc:
            raise DenylistError(f"cannot run git: {exc}") from exc
        try:
            for oid in oids:
                yield oid, _read_object(process, oid, kind)
        finally:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    with contextlib.suppress(OSError):  # git has already exited
                        stream.close()
            process.wait()
        if process.returncode != 0:
            errors.seek(0)
            detail = errors.read().decode("utf-8", "replace").strip()
            raise DenylistError(f"git cat-file failed: {detail or f'exit {process.returncode}'}")


def _read_object(process: subprocess.Popen[bytes], oid: str, kind: str) -> bytes:
    """One object from a running ``git cat-file --batch``: ask for it, then read its header and content."""
    stdin, stdout = process.stdin, process.stdout
    if stdin is None or stdout is None:
        raise DenylistError("git cat-file has no pipes")
    try:
        stdin.write(oid.encode("ascii") + b"\n")
        stdin.flush()
        header = stdout.readline().split()
        if len(header) != 3 or header[0].decode("ascii") != oid or header[1].decode("ascii") != kind:
            detail = b" ".join(header[1:]).decode("utf-8", "replace") or "no output"
            raise DenylistError(f"cannot read {kind} {oid}: {detail}")
        size = int(header[2])
        data = stdout.read(size)
        if len(data) != size or stdout.read(1) != b"\n":
            raise DenylistError(f"cannot read {kind} {oid}: the output ended early")
    except (OSError, ValueError) as exc:
        raise DenylistError(f"cannot read {kind} {oid}: {exc}") from exc
    return data


@dataclass(frozen=True)
class Finding:
    where: str
    position: int


@dataclass(frozen=True)
class TermWarning:
    where: str
    match: TermMatch


@dataclass
class Report:
    """Denylisted terms (they fail the check) and house terms (warnings only) found by a scan or audit."""

    denylist: Denylist
    findings: list[Finding] = field(default_factory=list)
    warnings: list[TermWarning] = field(default_factory=list)

    def check(self, where: str, *texts: str, warn: bool = True, min_chars: int = 1) -> None:
        """Check one or more readings of the same text; each word a term starts at is reported once."""
        matches = self.denylist.matches
        positions = sorted({position for text in texts for position in matches(text, min_chars=min_chars)})
        self.findings.extend(Finding(where, position) for position in positions)
        if warn and texts:
            self.warnings.extend(TermWarning(where, match) for match in house_terms(texts[0]))

    def check_lines(self, where: str, data: bytes, *, warn: bool = True) -> None:
        """Check bytes line by line, each line read as ``line_texts`` reads it; lines are numbered from 1."""
        text = _utf8(data)
        if text is not None and "\0" not in text:
            for number, line in enumerate(text.split("\n"), start=1):
                self.check(f"{where}:{number}", line, warn=warn)
            return
        for number, raw in enumerate(data.split(b"\n"), start=1):
            self.check(f"{where}:{number}", *line_texts(raw), warn=warn)

    def check_wide(self, where: str, data: bytes) -> bool:
        """Check ``data`` line by line as UTF-16 or UTF-32 text if it is that (``_wide_text``); whether it was.

        Git splits such a file's diff at every 0x0A byte, which also occurs
        inside characters (U+200A, a hair space, is 0A 20 in UTF-16LE), so its
        added lines say little; the decoded text is checked whole instead.
        """
        wide = _wide_text(data)
        if wide is None:
            return False
        encoding, text = wide
        label = f"binary, read as {encoding}" if b"\0" in data else f"read as {encoding}"
        for number, line in enumerate(text.split("\n"), start=1):
            readings = (line, line.replace("\0", "")) if "\0" in line else (line,)
            self.check(f"{where}:{number} ({label})", *readings)
        return True

    def check_file(self, where: str, data: bytes) -> None:
        """Check a whole file's bytes: as text if they are text, otherwise the readable runs inside them.

        UTF-16 and UTF-32 text is decoded and checked line by line
        (``check_wide``); when that finds no term, the bytes are read as below
        too, for text in another encoding or byte alignment inside them. Bytes
        without NUL are checked line by line like any added text, and so are
        bytes that are text once their NULs are removed, even with a few
        control or invalid bytes in them (``_mostly_text``). Anything else is
        binary data, and each readable run in it is checked
        (``readable_runs``), terms shorter than ``_RUN_MIN_CHARS`` characters
        only as words of prose.
        """
        found = len(self.findings)
        wide = self.check_wide(where, data)
        if len(self.findings) > found:
            return
        # The decoded lines of UTF-16 or UTF-32 text have given their warnings.
        warn = not wide
        if b"\0" not in data:
            self.check_lines(where, data, warn=warn)
            return
        if _mostly_text(data.replace(b"\0", b"")):
            for number, raw in enumerate(data.split(b"\n"), start=1):
                self.check(f"{where}:{number} (binary, read as text)", *line_texts(raw), warn=warn)
            return
        for offset, run in readable_runs(data):
            where_run = f"{where} (binary, readable text at byte {offset})"
            self.check(where_run, run, warn=warn, min_chars=_RUN_MIN_CHARS)

    def add(self, prefix: str, other: Report) -> None:
        """Take over another report's results, with ``prefix`` put before each location."""
        self.findings.extend(Finding(prefix + finding.where, finding.position) for finding in other.findings)
        self.warnings.extend(TermWarning(prefix + warning.where, warning.match) for warning in other.warnings)


# Options for every diff of the range: renames off (each path stands alone), no
# external diff or text conversion, and paths from the top of the repository
# whatever the working directory or diff.relative say.
_DIFF_OPTIONS = ("--no-renames", "--no-ext-diff", "--no-textconv", "--no-relative")
_NO_OBJECT = re.compile(r"0+")
_GITLINK_MODE = "160000"


@dataclass(frozen=True)
class Change:
    """A path the range changes: git's status letter, its new mode, its objects and whether git diffs it as binary."""

    path: bytes
    status: str
    mode: str
    old_oid: str
    oid: str
    binary: bool

    @property
    def name(self) -> str:
        return self.path.decode("utf-8", "replace")

    @property
    def new_content(self) -> bool:
        """Whether the path gets content it did not have: not deleted, and not only given a new mode."""
        return self.status != "D" and self.oid != self.old_oid


def changes(repo: Path, base: str, head: str) -> list[Change]:
    """Every path the range changes, from one ``git diff --raw --numstat -z``.

    Binary means git's own check (a ``-`` count in ``--numstat``) with the
    attributes of the base commit, so a ``.gitattributes`` added by the change
    cannot reclassify its files. The patch ``added_lines`` reads uses the same
    attributes, so it leaves out the content of these files only.
    """
    out = _git_bytes(
        repo, "diff", "--raw", "--numstat", "-z", "--no-abbrev", *_DIFF_OPTIONS, f"{base}...{head}", attr_source=base
    )
    fields = out.split(b"\0")
    raw: dict[bytes, tuple[str, str, str, str]] = {}
    binary: set[bytes] = set()
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if record.startswith(b":"):
            # ":old_mode new_mode old_oid new_oid status", then the path as a field of its own.
            meta = record[1:].decode("ascii", "replace").split()
            if len(meta) != 5 or index >= len(fields):
                raise DenylistError("unparseable git diff --raw record")
            raw[fields[index]] = (meta[4][:1], meta[1], meta[2], meta[3])
            index += 1
        elif record:
            added, deleted, path = (record.split(b"\t", 2) + [b"", b""])[:3]
            if not path:
                raise DenylistError("unparseable git diff --numstat record")
            if added == b"-" and deleted == b"-":
                binary.add(path)
    if not binary.issubset(raw):
        raise DenylistError("git diff --numstat names a binary file that --raw does not")
    found = []
    for path, (status, mode, old_oid, oid) in raw.items():
        if status != "D" and _NO_OBJECT.fullmatch(oid):
            raise DenylistError(f"git diff gives no new object for {path.decode('utf-8', 'replace')}")
        found.append(Change(path, status, mode, old_oid, oid, path in binary))
    return found


# A C-quoted path in a patch header ("b/caf\303\251.md"): git quotes names with
# special characters or bytes outside ASCII.
_C_ESCAPE = re.compile(rb"\\([0-7]{3}|.)", re.DOTALL)
_C_ESCAPES = {b"a": b"\a", b"b": b"\b", b"t": b"\t", b"n": b"\n", b"v": b"\v", b"f": b"\f", b"r": b"\r"}


def _patch_path(target: bytes) -> str:
    """The path of a "+++ " header: without the tab git adds after a name with a space, unquoted, without "b/"."""
    if target.endswith(b"\t"):
        target = target[:-1]  # a tab inside a name is always quoted, so this one is git's
    if target.startswith(b'"') and target.endswith(b'"'):
        target = _C_ESCAPE.sub(
            lambda m: bytes([int(m[1], 8)]) if len(m[1]) == 3 else _C_ESCAPES.get(m[1], m[1]), target[1:-1]
        )
    return (target[2:] if target.startswith(b"b/") else target).decode("utf-8", "replace")


# A patch header's "index <old>..<new> [mode]" line, with full object ids (--full-index).
_INDEX_LINE = re.compile(rb"index [0-9a-f]+\.\.([0-9a-f]+)(?: [0-7]+)?")


def added_lines(repo: Path, base: str, head: str, *, read_whole: Container[str]) -> Iterator[tuple[str, int, bytes]]:
    """(path, new line number, raw bytes) for every line the range adds to a text file.

    The patch is split on "\\n" only, git's own line end, so every line inside
    a hunk starts with "+", "-", " " or "\\" and nothing in a file can look like
    a header. A binary file appears only as a "Binary files ... differ" line,
    and its new object (from the "index" line) must be deleted or in
    ``read_whole``, the blobs ``Report.check_file`` reads instead: a file the
    patch leaves out must not go unread. (The reverse does not hold: --numstat
    also counts an empty file or a new mode as binary, where the patch shows
    no such line, and reading those whole costs nothing.)
    """
    diff = _git_bytes(
        repo,
        "diff",
        "--unified=0",
        "--no-color",
        "--full-index",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        *_DIFF_OPTIONS,
        f"{base}...{head}",
        attr_source=base,
    )
    path = ""
    line_no = 0
    new_oid: str | None = None
    # A file's header runs from "diff --git" to its first hunk. "+++ " is a
    # header only there, right after "--- "; inside a hunk the same prefix is
    # an added line that starts with "++" and is checked like any other.
    in_header = False
    previous = b""
    for raw in diff.split(b"\n"):
        if raw.startswith(b"diff --git "):
            in_header, path, new_oid = True, "", None
        elif in_header and raw.startswith(b"+++ ") and previous.startswith(b"--- "):
            path = _patch_path(raw[4:])
        elif in_header and raw.startswith(b"index "):
            index = _INDEX_LINE.fullmatch(raw)
            new_oid = index.group(1).decode("ascii") if index else None
        elif in_header and raw.startswith(b"Binary files "):
            if new_oid is None or not (_NO_OBJECT.fullmatch(new_oid) or new_oid in read_whole):
                detail = raw[:200].decode("utf-8", "replace")
                raise DenylistError(f"git diff shows a binary file that --numstat does not count: {detail}")
        elif raw.startswith(b"@@"):
            match = re.match(rb"@@ -\S+ \+(\d+)", raw)
            if not match:
                raise DenylistError(f"unparseable diff hunk header: {raw[:80].decode('utf-8', 'replace')}")
            if not path:
                raise DenylistError("diff hunk without a file header")
            in_header = False
            line_no = int(match.group(1))
        elif not in_header and raw.startswith(b"+"):
            yield path, line_no, raw[1:]
            line_no += 1
        previous = raw


def _declared_encoding(header: bytes) -> str | None:
    """The encoding a commit object's ``encoding`` header names, if it has one."""
    for line in header.split(b"\n"):
        if line.startswith(b"encoding "):
            return line[len(b"encoding ") :].decode("ascii", "replace").strip()
    return None


def commit_messages(repo: Path, base: str, head: str) -> Iterator[tuple[str, list[bytes]]]:
    """(short id, the versions of its message that are checked) of each commit in the range.

    A commit's ``encoding`` header (``i18n.commitEncoding``) names the encoding
    its message is stored in. The message is read as ``git log
    --encoding=UTF-8`` shows it, converted from that encoding; as Python
    decodes it, for an encoding the git build cannot convert; and as stored,
    in case the header is wrong. Each ``git log -z`` record ends with a NUL,
    which git refuses in a message; the records must name exactly the commits
    ``rev-list`` lists.
    """
    shas = _git(repo, "rev-list", f"{base}..{head}").split()
    log = _git_bytes(
        repo, "log", "-z", "--encoding=UTF-8", "--no-show-signature", "--format=%H%n%B", f"{base}..{head}", "--"
    )
    records = log.split(b"\0")
    if records[-1:] == [b""]:
        records.pop()
    shown: dict[bytes, bytes] = {}
    for record in records:
        sha_line, _, message = record.partition(b"\n")
        shown[sha_line] = message
    if len(records) != len(shas) or set(shown) != {sha.encode("ascii") for sha in shas}:
        raise DenylistError("git log and git rev-list disagree about the commits in the range")
    for sha, data in read_objects(repo, shas, "commit"):
        header, _, stored = data.partition(b"\n\n")
        versions = [shown[sha.encode("ascii")], stored]
        declared = _declared_encoding(header)
        if declared:
            with contextlib.suppress(LookupError, UnicodeError):  # not a codec Python has
                versions.append(stored.decode(declared, "replace").encode("utf-8"))
        yield sha[:12], list(dict.fromkeys(versions))


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
) -> Report:
    base_sha = _resolve(repo, base)
    head_sha = _resolve(repo, head)
    report = Report(denylist)
    check = report.check

    changed = changes(repo, base_sha, head_sha)
    for change in changed:
        check(f"file path {change.name}", *line_texts(change.path))
    # A deleted file adds nothing, nor does a new mode, and a submodule's commit is not in this repository.
    readable = [change for change in changed if change.new_content and change.mode != _GITLINK_MODE]
    whole = {change.oid for change in readable if change.binary}
    for path, line_no, raw in added_lines(repo, base_sha, head_sha, read_whole=whole):
        check(f"{path}:{line_no}", *line_texts(raw))
    # Every new blob is read once: a binary one is checked whole, and a text one
    # too if it is UTF-16 or UTF-32, whose added lines git splits at random.
    ways: dict[str, set[bool]] = {}
    for change in readable:
        ways.setdefault(change.oid, set()).add(change.binary)
    per_blob: dict[tuple[str, bool], Report] = {}
    for oid, data in read_objects(repo, ways, "blob"):
        for binary in ways[oid]:
            blob_report = per_blob[oid, binary] = Report(denylist)
            if binary:
                blob_report.check_file("", data)
            else:
                blob_report.check_wide("", data)
    for change in readable:
        report.add(change.name, per_blob[change.oid, change.binary])
    for sha, versions in commit_messages(repo, base_sha, head_sha):
        lines = itertools.zip_longest(*(version.split(b"\n") for version in versions), fillvalue=b"")
        for offset, readings in enumerate(lines, start=1):
            texts = [text for raw in dict.fromkeys(readings) for text in line_texts(raw)]
            check(f"commit {sha} message line {offset}", *texts)
    branch_name = branch if branch is not None else current_branch(repo)
    check("branch name", branch_name)
    for text_file in text_files:
        try:
            content = text_file.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise DenylistError(f"cannot read {text_file}: {exc}") from exc
        for offset, line in enumerate(content.split("\n"), start=1):
            check(f"{text_file.name} line {offset}", line)
    return report


def audit(denylist: Denylist, repo: Path, paths: Iterable[str] = ()) -> Report:
    """Check every tracked path and file at the working tree (or those under ``paths``), not just a change.

    Each file is read as ``Report.check_file`` reads an added binary file, so
    a file holding NUL bytes is still checked (as text if it is text), and
    UTF-16 or UTF-32 text is decoded first, with or without NUL bytes.
    """
    paths = list(paths)
    names = [name for name in _git_bytes(repo, "ls-files", "-z", "--", *paths).split(b"\0") if name]
    if paths and not names:
        # A mistyped path would otherwise audit nothing and pass.
        raise DenylistError(f"no tracked file under {' '.join(paths)}")
    report = Report(denylist)
    for raw_name in names:
        name = raw_name.decode("utf-8", "replace")
        report.check(f"file path {name}", *line_texts(raw_name))
        try:
            data = (repo / os.fsdecode(raw_name)).read_bytes()
        except OSError:
            continue  # tracked but absent from the working tree (sparse, deleted or a submodule)
        report.check_file(name, data)
    return report


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

    audit_p = sub.add_parser("audit", help="check every tracked file, e.g. to find existing mentions")
    audit_p.add_argument("paths", nargs="*", help="check only the tracked files under these paths")

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
            report = audit(denylist, repo, args.paths)
        else:
            report = scan(denylist, repo, args.base, args.head, branch=args.branch, text_files=args.text_file)
    except DenylistError as exc:
        print(f"check_denylist: {exc}", file=sys.stderr)
        return 2

    if report.warnings:
        print("House terminology (warnings; they do not change the result):")
        for warning in report.warnings:
            match = warning.match
            print(f'  {warning.where} (word {match.position + 1}): "{match.found}" -> {match.preferred}')
    if report.findings:
        subject = "the tracked files" if args.command == "audit" else "this change"
        print(f"A denylisted vendor name appears in {subject}. Use a provider-neutral name instead")
        print("(the mock provider is `mock`; documentation and examples use `acme_kyb`):")
        for finding in report.findings:
            print(f"  {finding.where} (word {finding.position + 1})")
        return 1
    print(f"check_denylist: no denylisted terms ({len(denylist.hashes)} terms checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
