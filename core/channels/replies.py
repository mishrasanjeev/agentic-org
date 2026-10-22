# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Bounded final-answer disclosure, separate from private execution evidence."""

import json
import math
import re
from typing import Any

_PRIVATE_KEYS = {
    "accesstoken",
    "apikey",
    "authorization",
    "credentials",
    "password",
    "secret",
    "refreshtoken",
    "privatekey",
    "rawpayload",
    "reasoningtrace",
    "toolcalls",
    "toolresults",
    "token",
    "webhooksecret",
    "rawproviderpayload",
    "rawconnectorpayload",
}
_SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+\S+|"
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"\bAIza[A-Za-z0-9_-]{30,}|\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}|"
    r"\b\d{5,}:[A-Za-z0-9_-]{20,}|"
    r"(?:api[_ -]?key|access[_ -]?token|password|secret)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def _safe(value: Any, depth: int = 0) -> bool:
    if depth > 6:
        return False
    if isinstance(value, str):
        return (
            len(value) <= 16000
            and not _SECRET.search(value)
            and not re.search(r"[\x00-\x08\x0b-\x1f\ud800-\udfff]", value)
        )
    if isinstance(value, float):
        return math.isfinite(value)
    if value is None or isinstance(value, (bool, int)):
        return True
    if isinstance(value, list):
        return len(value) <= 100 and all(_safe(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return len(value) <= 100 and all(
            isinstance(key, str)
            and re.sub(r"[^a-z]", "", key.lower()) not in _PRIVATE_KEYS
            and _safe(key, depth + 1)
            and _safe(item, depth + 1)
            for key, item in value.items()
        )
    return False


def final_answer(result: dict, credentials: dict[str, str]) -> str | None:
    """No traces, tool records, partial outputs or arbitrary result-envelope exports."""
    if result.get("status") != "completed" or result.get("error") or result.get("hitl_trigger"):
        return None
    output = result.get("output")
    if not _safe(output):
        return None
    if isinstance(output, dict) and "answer" in output:
        output = output["answer"]
    elif (
        isinstance(output, dict)
        and isinstance(output.get("raw_output"), str)
        and set(output) <= {"raw_output", "status"}
    ):
        # A plain-text agent answer, wrapped by the agent graph (agent_graph._parse_output);
        # send the text itself, not the wrapper. Anything else in the dict keeps it a JSON export.
        output = output["raw_output"]
    if output is None or output == "":
        return None
    text = output.strip() if isinstance(output, str) else json.dumps(output, ensure_ascii=False, allow_nan=False)
    if not text:
        return None
    # The channel's own protected material must never be echoed by an answer.
    if any(len(value) >= 8 and value in text for value in credentials.values() if isinstance(value, str)):
        return None
    suffix = "\n\n[Answer shortened. Open AgenticOrg for the full result.]"
    encoded = text.encode("utf-16-le")
    if len(encoded) > 3800:
        text = encoded[: 3800 - len(suffix.encode("utf-16-le"))].decode("utf-16-le", errors="ignore") + suffix
    return text
