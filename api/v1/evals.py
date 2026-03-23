"""Evaluation scorecard endpoints — public, no auth required."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

_SCORECARD_PATH = Path("scorecard.json")


def _load_scorecard() -> dict:
    """Load the most recent scorecard from disk."""
    if not _SCORECARD_PATH.exists():
        raise HTTPException(status_code=404, detail="Scorecard not found. Run `python -m evals.runner` first.")
    with open(_SCORECARD_PATH, encoding="utf-8") as f:
        return json.load(f)


@router.get("/evals")
async def get_evals():
    """Return the full evaluation scorecard."""
    return _load_scorecard()


@router.get("/evals/agent/{agent_type}")
async def get_agent_evals(agent_type: str):
    """Return scores for a single agent type."""
    scorecard = _load_scorecard()

    agent_agg = scorecard.get("agent_aggregates", {}).get(agent_type)
    if not agent_agg:
        raise HTTPException(status_code=404, detail=f"No evaluation data for agent: {agent_type}")

    case_results = [c for c in scorecard.get("case_results", []) if c["agent_type"] == agent_type]

    return {
        "agent_type": agent_type,
        "aggregate": agent_agg,
        "cases": case_results,
    }
