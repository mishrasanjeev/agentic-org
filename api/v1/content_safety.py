"""Content safety API — check text for PII, toxicity, and duplicates."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from core.content_safety.checker import check_content_safety

router = APIRouter()


class ContentSafetyConfig(BaseModel):
    check_pii: bool = True
    check_toxicity: bool = True
    check_duplicates: bool = True


class ContentSafetyRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to check for safety issues")
    config: ContentSafetyConfig = Field(default_factory=ContentSafetyConfig)


class ContentSafetyIssue(BaseModel):
    type: str
    detail: str
    severity: str


class ContentSafetyResponse(BaseModel):
    safe: bool
    issues: list[ContentSafetyIssue]
    scores: dict[str, float]


@router.post(
    "/content-safety/check",
    response_model=ContentSafetyResponse,
    summary="Check text for content safety issues",
)
async def check_safety(req: ContentSafetyRequest) -> dict[str, Any]:
    """Run content safety checks (PII, toxicity, near-duplicate) on the given text.

    Returns whether the text is safe, a list of issues found, and per-check scores.
    """
    result = await check_content_safety(
        text=req.text,
        config={
            "check_pii": req.config.check_pii,
            "check_toxicity": req.config.check_toxicity,
            "check_duplicates": req.config.check_duplicates,
        },
    )
    return result
