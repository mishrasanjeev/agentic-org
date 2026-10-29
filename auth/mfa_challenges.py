# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Versioned pending sign-ins, bound to a durable user security generation."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_GENERATION = (1 << 63) - 1


class MfaChallengeClaims(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(strict=True, ge=1, le=1)
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    method: Literal["password", "google"]
    generation: int = Field(strict=True, ge=0, le=MAX_GENERATION)


def challenge_generation(value: object) -> int:
    """Never bind a challenge to missing, corrupt or unbounded persisted state."""
    if type(value) is not int or not 0 <= value <= MAX_GENERATION:
        raise ValueError("Invalid MFA security generation")
    return value
