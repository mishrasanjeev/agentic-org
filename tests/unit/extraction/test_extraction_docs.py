# SPDX-License-Identifier: Apache-2.0
"""docs/security/untrusted-content.md stays in step with the code it documents."""

from __future__ import annotations

import re
from pathlib import Path

from core.extraction import CONTENT_TYPES, FIELDS, ExtractionFailure, SourceKind

DOC = Path(__file__).resolve().parents[3] / "docs" / "security" / "untrusted-content.md"


def _doc() -> str:
    return DOC.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_every_failure_reason_is_documented() -> None:
    text = _doc()
    assert [reason.value for reason in ExtractionFailure if f"`{reason.value}`" not in text] == []


def test_the_field_table_matches_the_schema() -> None:
    text = _doc()
    for kind in SourceKind:
        row = re.search(rf"^\| `{kind.value}` \| (.+?) \| (.+?) \|$", text, re.MULTILINE)
        assert row is not None, kind
        assert re.findall(r"`([^`]+)`", row.group(1)) == list(CONTENT_TYPES[kind])
        assert re.findall(r"`([^`]+)`", row.group(2)) == list(FIELDS[kind])


def test_documented_api_names_exist() -> None:
    import core.extraction as extraction
    from core.langgraph.agent_graph import build_agent_graph

    for name in ("InMemoryExcerptStore", "SourceKind", "UntrustedTextRegistry", "extract", "probe_isolation"):
        assert hasattr(extraction, name)
    assert "context_guard" in build_agent_graph.__code__.co_varnames
