# SPDX-License-Identifier: Apache-2.0
"""Keep untrusted source text out of model context.

``UntrustedTextRegistry`` remembers, per case, every free-text string that came
from an untrusted source: extractor string fields and excerpts, and any other
source the caller registers (for example screening aliases returned by a
provider). ``build_model_context`` renders a case's evidence for a model using
only numbers, booleans and short enum-like tokens; every other string, and
every registered string, is replaced by ``{"untrusted_ref": "<path>"}``. The
rendered text is then checked, and ``guard_messages`` checks every message
before a model call (``build_agent_graph(context_guard=...)``). A match raises
:class:`UntrustedContentLeakError`: the call fails closed rather than sending
attacker text to the model.

Matching ignores case, whitespace, punctuation and JSON escaping, and also
looks for any copied run of 40 or more letters and digits from a long
passage, so a reformatted or partial copy is still caught.

Registered strings shorter than 8 letters and digits, and strings that are
exactly a schema vocabulary value, are not searched for. The guard matches
substrings of the whole prompt, so a short needle such as ``Retail`` or
``Ltd`` would match ordinary words in system prompts and fail legitimate runs.
Such strings are still always replaced by ``build_model_context`` (exact
match), and a string that short cannot carry more than a single word; the
guard is the backstop for longer text that reaches a prompt by another route.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from core.extraction.schema import VOCABULARY

MIN_SEARCH_CHARS = 8
WINDOW_CHARS = 32
WINDOW_STEP = 8
LEAK_REASON = "untrusted_content_in_model_context"

# Strings a model may see verbatim: short snake_case identifiers (enum values)
# and ISO dates. No ``:``, ``.``, ``/`` or spaces, so a value cannot be shaped
# like a role marker, a dotted instruction or a sentence.
_SAFE_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_JSON_ESCAPE_RE = re.compile(r"\\(u[0-9a-fA-F]{4}|[\"\\/bfnrt])")
_SIMPLE_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": " ", "f": " ", "n": " ", "r": " ", "t": " "}


class UntrustedContentLeakError(RuntimeError):
    """Untrusted source text was about to reach a model."""

    def __init__(self, where: str, fingerprints: Sequence[str]) -> None:
        self.reason = LEAK_REASON
        self.where = where
        self.fingerprints = tuple(fingerprints)
        # The message names fingerprints, never the text itself.
        super().__init__(f"{LEAK_REASON} in {where}: {len(self.fingerprints)} match(es) {list(self.fingerprints)}")


class UntrustedContextError(ValueError):
    """Evidence cannot be rendered for a model without guessing what is safe."""


def _unescape(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if token.startswith("u"):
            return chr(int(token[1:], 16))
        return _SIMPLE_ESCAPES[token]

    return _JSON_ESCAPE_RE.sub(replace, text)


def _fold(text: str) -> str:
    """Letters and digits only, case-folded, after NFKC and JSON unescaping."""
    text = unicodedata.normalize("NFKC", _unescape(text)).casefold()
    return "".join(ch for ch in text if ch.isalnum())


_VOCABULARY_FOLDED = frozenset(_fold(word) for word in VOCABULARY)


def _fingerprint(folded: str) -> str:
    return hashlib.sha256(folded.encode("utf-8")).hexdigest()[:12]


class UntrustedTextRegistry:
    """Per-case record of untrusted strings, used to keep them out of model context."""

    def __init__(self) -> None:
        self._exact: set[str] = set()
        self._needles: dict[str, str] = {}

    def register(self, text: str) -> None:
        if not isinstance(text, str):
            raise TypeError("only strings can be registered as untrusted text")
        folded = _fold(text)
        if not folded:
            return
        self._exact.add(folded)
        if len(folded) < MIN_SEARCH_CHARS or folded in _VOCABULARY_FOLDED:
            return
        fingerprint = _fingerprint(folded)
        if len(folded) <= WINDOW_CHARS:
            self._needles.setdefault(folded, fingerprint)
            return
        for start in range(0, len(folded) - WINDOW_CHARS + 1, WINDOW_STEP):
            self._needles.setdefault(folded[start : start + WINDOW_CHARS], fingerprint)
        self._needles.setdefault(folded[-WINDOW_CHARS:], fingerprint)

    def register_all(self, texts: Iterable[str]) -> None:
        for text in texts:
            self.register(text)

    def is_untrusted(self, value: str) -> bool:
        return _fold(value) in self._exact

    def __len__(self) -> int:
        return len(self._exact)

    def find(self, text: str) -> tuple[str, ...]:
        """Fingerprints of registered strings that appear in ``text``."""
        haystack = _fold(text)
        return tuple(sorted({fp for needle, fp in self._needles.items() if needle in haystack}))

    def assert_absent(self, text: str, *, where: str) -> None:
        found = self.find(text)
        if found:
            raise UntrustedContentLeakError(where, found)

    def guard_messages(self, messages: Sequence[Any]) -> None:
        """Raise if any message about to be sent to a model contains untrusted text.

        Pass as ``build_agent_graph(context_guard=registry.guard_messages)``.
        """
        for index, message in enumerate(messages):
            role = getattr(message, "type", type(message).__name__)
            for part in _message_texts(message):
                self.assert_absent(part, where=f"message[{index}] ({role})")


def _message_texts(message: Any) -> list[str]:
    texts: list[str] = []
    content = getattr(message, "content", message)
    if isinstance(content, str):
        texts.append(content)
    else:
        texts.append(json.dumps(content, ensure_ascii=False, default=str))
    for call in getattr(message, "tool_calls", None) or []:
        texts.append(json.dumps(call, ensure_ascii=False, default=str, sort_keys=True))
    extra = getattr(message, "additional_kwargs", None)
    if extra:
        texts.append(json.dumps(extra, ensure_ascii=False, default=str, sort_keys=True))
    return texts


def _render(value: Any, path: str, untrusted: UntrustedTextRegistry) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UntrustedContextError(f"{path}: non-finite number")
        return value
    if isinstance(value, str):
        if untrusted.is_untrusted(value) or not (_SAFE_TOKEN_RE.fullmatch(value) or _ISO_DATE_RE.fullmatch(value)):
            return {"untrusted_ref": path}
        return value
    if isinstance(value, Mapping):
        rendered: dict[str, Any] = {}
        for key in sorted(value, key=str):
            if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
                raise UntrustedContextError(f"{path}: key is not a lower-case snake_case identifier")
            rendered[key] = _render(value[key], f"{path}.{key}" if path else key, untrusted)
        return rendered
    if isinstance(value, list | tuple):
        return [_render(item, f"{path}[{index}]", untrusted) for index, item in enumerate(value)]
    raise UntrustedContextError(f"{path}: {type(value).__name__} cannot be rendered for a model")


def build_model_context(evidence: Mapping[str, Any], *, untrusted: UntrustedTextRegistry) -> str:
    """Render case evidence as JSON a model may see, with untrusted text replaced by references.

    Numbers, booleans, ``null``, ISO dates and enum-like tokens (a lower-case
    letter then up to 31 lower-case letters, digits or underscores) are kept;
    any other string,
    and any string registered as untrusted, becomes
    ``{"untrusted_ref": "<dotted.path>"}``. Keys must be snake_case
    identifiers. The result is checked against the registry before it is
    returned.
    """
    if not isinstance(evidence, Mapping):
        raise UntrustedContextError("evidence must be a mapping")
    text = json.dumps(_render(evidence, "", untrusted), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    untrusted.assert_absent(text, where="case context")
    return text
