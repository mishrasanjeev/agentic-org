"""Tests for `_build_tool_index` connector filtering (UR-Bug-2).

Uday/Ramesh 2026-04-21 report: agents created with Gmail as their
connector ended up with `authorized_tools` populated from the full
catalogue (or left empty), not the Gmail tool list. Root cause was
that the tool-index builder didn't support a per-connector filter.
These tests pin the new ``connector_names`` filter.
"""

from __future__ import annotations

import connectors  # noqa: F401  # registers built-in connectors
from core.langgraph.tool_adapter import _build_tool_index


class TestBuildToolIndexConnectorFilter:
    def test_no_filter_returns_many_connectors(self) -> None:
        """Default behaviour — index includes tools from every
        registered connector (the previous behaviour)."""
        idx = _build_tool_index()
        distinct_connectors = {conn for conn, _ in idx.values()}
        # Must include at least gmail + a finance connector.
        assert len(distinct_connectors) > 1
        assert "gmail" in distinct_connectors

    def test_filter_restricts_to_named_connector(self) -> None:
        """The Gmail case from the bug report — passing
        ``connector_names=['gmail']`` returns only gmail tools."""
        idx = _build_tool_index(connector_names=["gmail"])
        assert idx, "gmail connector should have at least one tool"
        for _tool, (connector, _desc) in idx.items():
            assert connector == "gmail", (
                f"unexpected connector {connector!r} in gmail-only index"
            )

    def test_filter_accepts_registry_prefix(self) -> None:
        """The UI stores connector ids as ``registry-<name>``. The
        filter must tolerate that prefix so the frontend doesn't have
        to strip it before calling /tools."""
        idx_plain = _build_tool_index(connector_names=["gmail"])
        idx_prefixed = _build_tool_index(connector_names=["registry-gmail"])
        assert idx_plain.keys() == idx_prefixed.keys()

    def test_filter_is_case_insensitive(self) -> None:
        idx_lower = _build_tool_index(connector_names=["gmail"])
        idx_upper = _build_tool_index(connector_names=["GMAIL"])
        assert idx_lower.keys() == idx_upper.keys()

    def test_filter_unknown_connector_returns_empty(self) -> None:
        """A connector the user mis-typed or that isn't installed
        simply returns an empty index rather than raising. The UI
        shows 'no tools available' which is the right UX."""
        idx = _build_tool_index(connector_names=["nope-this-does-not-exist"])
        assert idx == {}

    def test_multiple_connectors_union_tools(self) -> None:
        """Selecting multiple connectors should union their tools."""
        gmail_only = set(_build_tool_index(connector_names=["gmail"]).keys())
        multi = _build_tool_index(connector_names=["gmail", "slack"])
        # Everything in gmail-only must appear in the multi-filter.
        assert gmail_only.issubset(multi.keys())
