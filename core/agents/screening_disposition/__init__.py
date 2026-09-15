# SPDX-License-Identifier: Apache-2.0
"""Screening Disposition reference agent (PRD A-7).

Proposes a disposition for one screening hit - per-identifier comparisons, cited evidence, a
rationale, a proposed outcome and a confidence band - for an analyst to accept or override. It
never closes a hit. See ``docs/agents/screening-disposition.md``.
"""

from __future__ import annotations

from core.agents.screening_disposition.agent import (
    AGENT_NAME,
    AGENT_VERSION,
    TOOL_SET,
    DispositionConfig,
    DispositionDependencies,
    DispositionOutcome,
    run_screening_disposition,
)
from core.agents.screening_disposition.review import DispositionReviewError, DispositionReviewRequest, apply_review

__all__ = [
    "AGENT_NAME",
    "AGENT_VERSION",
    "TOOL_SET",
    "DispositionConfig",
    "DispositionDependencies",
    "DispositionOutcome",
    "DispositionReviewError",
    "DispositionReviewRequest",
    "apply_review",
    "run_screening_disposition",
]
