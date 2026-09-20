# SPDX-License-Identifier: Apache-2.0
"""Business Onboarding Underwriter reference agent (PRD A-7).

Investigates a business application through a :class:`VerificationProvider` and hands off a
cited ``underwriting_memo`` for a human decision. It never approves, declines, closes or files.
See ``docs/agents/business-underwriter.md``.
"""

from __future__ import annotations

from core.agents.business_underwriter.agent import (
    AGENT_NAME,
    AGENT_VERSION,
    TOOL_SET,
    UnderwriterConfig,
    UnderwriterDependencies,
    UnderwritingOutcome,
    run_underwriter,
)

__all__ = [
    "AGENT_NAME",
    "AGENT_VERSION",
    "TOOL_SET",
    "UnderwriterConfig",
    "UnderwriterDependencies",
    "UnderwritingOutcome",
    "run_underwriter",
]
