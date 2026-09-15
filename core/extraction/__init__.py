# SPDX-License-Identifier: Apache-2.0
"""Untrusted content extraction: websites, registry documents and applicant uploads.

Content from these sources is attacker-controlled. It is parsed in a sandboxed
worker process that returns typed, constrained fields only; excerpts are stored
separately and cited by ``excerpt_ref``; and the context builder and model-call
guard keep every untrusted string out of model context. See
``docs/security/untrusted-content.md``.
"""

from __future__ import annotations

from core.extraction.context import (
    LEAK_REASON,
    UntrustedContentLeakError,
    UntrustedContextError,
    UntrustedTextRegistry,
    build_model_context,
)
from core.extraction.excerpts import Excerpt, ExcerptStore, InMemoryExcerptStore, excerpt_ref
from core.extraction.sandbox import ExtractionResult, extract, probe_isolation
from core.extraction.schema import (
    CONTENT_TYPES,
    FIELDS,
    ExtractionFailure,
    SourceKind,
    untrusted_text_fields,
)

__all__ = [
    "CONTENT_TYPES",
    "FIELDS",
    "LEAK_REASON",
    "Excerpt",
    "ExcerptStore",
    "ExtractionFailure",
    "ExtractionResult",
    "InMemoryExcerptStore",
    "SourceKind",
    "UntrustedContentLeakError",
    "UntrustedContextError",
    "UntrustedTextRegistry",
    "build_model_context",
    "excerpt_ref",
    "extract",
    "probe_isolation",
    "untrusted_text_fields",
]
