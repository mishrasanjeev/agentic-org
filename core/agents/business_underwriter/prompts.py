# SPDX-License-Identifier: Apache-2.0
"""Versioned prompts for the Business Onboarding Underwriter.

A prompt is identified by ``(prompt_id, version)`` and bound by the SHA-256 of its exact bytes.
Both are recorded in the case record and memo provenance so the evidence package names the prompt
that produced every model-written sentence. Changing a prompt's text means a new version file;
:func:`load_prompt` refuses a file whose content no longer matches the digest pinned here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass(frozen=True, slots=True)
class PromptRecord:
    prompt_id: str
    version: str
    sha256: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"prompt_id": self.prompt_id, "version": self.version, "sha256": self.sha256}


class PromptIntegrityError(RuntimeError):
    """A versioned prompt file is missing or its content changed without a new version."""


#: (prompt_id, version) -> pinned digest of the file ``prompts/<name>-<version>.txt``.
PINNED: dict[tuple[str, str], str] = {
    (
        "business_underwriter.narrative",
        "1.0.0",
    ): "sha256:3f3728c0c783719804f835087f53b9a2046a1782ca922a2814cd6b0f418fdc68",
}
NARRATIVE = ("business_underwriter.narrative", "1.0.0")


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def load_versioned_prompt(
    directory: Path, pinned: dict[tuple[str, str], str], prompt_id: str, version: str
) -> PromptRecord:
    """Load ``<directory>/<last part of prompt_id>-<version>.txt`` and check it against its pinned digest."""
    digest_pinned = pinned.get((prompt_id, version))
    if digest_pinned is None:
        raise PromptIntegrityError(f"prompt_unknown: {prompt_id}@{version}")
    path = directory / f"{prompt_id.rsplit('.', 1)[-1]}-{version}.txt"
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PromptIntegrityError(f"prompt_unreadable: {prompt_id}@{version}") from exc
    digest = _digest(data)
    if digest != digest_pinned:
        raise PromptIntegrityError(f"prompt_digest_mismatch: {prompt_id}@{version}")
    return PromptRecord(prompt_id=prompt_id, version=version, sha256=digest, text=data.decode("utf-8"))


def load_prompt(prompt_id: str, version: str) -> PromptRecord:
    return load_versioned_prompt(PROMPTS_DIR, PINNED, prompt_id, version)
