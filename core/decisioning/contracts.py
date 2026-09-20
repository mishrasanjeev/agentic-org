# SPDX-License-Identifier: Apache-2.0

"""Contracts shared by semantic decision providers and AgenticOrg callers.

Decision providers advise the runtime. They do not authenticate callers,
authorize tools, mutate state, or replace deterministic policy checks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

QuestionType = Literal["choice", "score", "noul"]


class DecisionProviderError(RuntimeError):
    """Safe, provider-neutral failure from a decision provider."""


@dataclass(frozen=True)
class DecisionQuestion:
    """One atomic typed question accepted by System One providers."""

    type: QuestionType
    instructions: str
    criteria: Mapping[str, str] | Sequence[str] | None = None

    def to_payload(self) -> dict[str, Any]:
        instructions = self.instructions.strip()
        if not instructions:
            raise ValueError("decision question instructions must not be empty")
        if self.type not in {"choice", "score", "noul"}:
            raise ValueError("unsupported decision question type")

        payload: dict[str, Any] = {
            "type": self.type,
            "instructions": instructions,
        }
        if self.type == "choice":
            if not isinstance(self.criteria, Mapping) or not self.criteria:
                raise ValueError("choice questions require a non-empty criteria mapping")
            payload["criteria"] = {
                str(key): str(value) for key, value in self.criteria.items()
            }
        elif self.type == "score":
            if (
                not isinstance(self.criteria, Sequence)
                or isinstance(self.criteria, (str, bytes))
                or len(self.criteria) < 2
            ):
                raise ValueError("score questions require at least two criteria labels")
            payload["criteria"] = [str(item) for item in self.criteria]
        elif self.criteria is not None:
            raise ValueError("noul questions do not accept criteria")
        return payload


@dataclass(frozen=True)
class DecisionRequest:
    """Provider request with explicit purpose and tenant context."""

    state: str | Mapping[str, Any]
    questions: Mapping[str, DecisionQuestion]
    purpose: str
    model: str = "jev-latest"
    tenant_id: str | None = None
    trace_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        if not self.questions:
            raise ValueError("decision request must contain at least one question")
        purpose = self.purpose.strip()
        if not purpose:
            raise ValueError("decision request purpose must not be empty")
        state: str | dict[str, Any]
        if isinstance(self.state, Mapping):
            state = dict(self.state)
        elif isinstance(self.state, str) and self.state.strip():
            state = self.state
        else:
            raise ValueError("decision request state must be non-empty text or an object")
        return {
            "state": state,
            "model": self.model.strip() or "jev-latest",
            "questions": {
                name: question.to_payload()
                for name, question in self.questions.items()
            },
        }


@dataclass(frozen=True)
class DecisionAnswer:
    """One typed provider answer preserved without converting it to prose."""

    type: str
    value: Any
    confidence: float | None = None
    probabilities: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionResponse:
    """Provider response plus non-sensitive execution metadata."""

    provider: str
    model: str
    answers: Mapping[str, DecisionAnswer]
    usage: Mapping[str, int] = field(default_factory=dict)
    latency_ms: float | None = None

    @property
    def minimum_confidence(self) -> float | None:
        confidences = [
            answer.confidence
            for answer in self.answers.values()
            if answer.confidence is not None
        ]
        return min(confidences) if confidences else None


class DecisionProvider(Protocol):
    """Provider seam used by future shadow and routing integrations."""

    async def decide(self, request: DecisionRequest) -> DecisionResponse:
        """Return a typed advisory decision for *request*."""
