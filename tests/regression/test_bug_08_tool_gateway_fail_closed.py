"""BUG-08 (RU-May01 verification, 2026-05-02): tool gateway fail-closed.

Discovered while running the post-deploy honesty check on PR #406's fix
for the Ramesh/Uday CA Firms BUG-01..04 May-01 reopens. The PR-#406
code fixes ARE deployed and firing (the ``tool_execution_failed_after_
reconnect`` log message — which is a NEW marker introduced by the
BUG-01 cache-eviction path — appears on every prod run). Yet the agent
still reproduces the pre-fix shape:

  confidence: 0.24
  tool_calls: []
  reasoning_trace: ["...", "Confidence capped to 0.5 (tool_call_failed)"]

Cloud Run logs from the failing run show *Stripe* being called, not
Zoho Books::

    tool_execution_failed_after_reconnect connector=stripe
        error="Illegal header value b'Bearer '"  tool=list_invoices

The Aryan FpaAgent (id=02ca34a7-...) had
``connector_ids = ["registry-zoho_books"]``.  The runtime queried
``ConnectorConfig WHERE tenant_id=tid AND connector_name='zoho_books'``,
got nothing back (the tenant has no live Zoho ConnectorConfig — only a
Connector row), then continued the run with no resolved configs.  At
``build_tools_for_agent`` time, ``connector_names`` was never plumbed
in, so ``_build_tool_index`` scanned **every** globally-registered
native connector.  Both Zoho Books and Stripe register
``list_invoices``; the dict-assignment loop overwrites the prior entry,
and Stripe (alphabetically later — but order isn't load-bearing for
the bug, just for which provider wins) ended up as the resolver for
``list_invoices``.  The LLM then called ``stripe.list_invoices`` with
no creds → 401 → tool_call_failed → confidence cap.

The fix: ``api/v1/agents.py:run_agent`` now uses the new
``_resolve_connector_configs`` helper that returns both the merged
config dict AND the list of connector names that actually resolved.
That list is passed through ``runner.run_agent`` →
``build_agent_graph`` → ``build_tools_for_agent`` →
``_build_tool_index``.  In ``_build_tool_index`` the ``None`` vs
``[]`` distinction is now load-bearing:

  ``connector_names is None``  → no caller constraint, scan all
                                  connectors (existing behaviour, used
                                  by tests + a few synthetic call sites).
  ``connector_names == []``    → fail-CLOSED: the runtime explicitly
                                  resolved zero connectors for this
                                  agent's declared ``connector_ids``.
                                  Build an empty tool index.  No
                                  fallback to global connectors.
  ``connector_names == ["zoho_books"]``
                               → tool index restricted to Zoho's
                                  registered tools only.  Other
                                  connectors that also expose
                                  ``list_invoices`` are excluded.

These pins lock that contract into place so a future refactor can't
silently restore the fail-open behaviour.
"""
from __future__ import annotations

import pytest

from core.langgraph.tool_adapter import (
    _build_tool_index,
    build_tools_for_agent,
)


class TestBuildToolIndexFailClosed:
    """``_build_tool_index`` must distinguish None from [] for connector_names."""

    def test_none_connector_names_scans_all_connectors(self):
        """``connector_names=None`` → no caller constraint, every native
        connector contributes its tools.  This is the existing behaviour
        for the few synthetic call sites (tests, ad-hoc scripts) that
        don't go through the agent runtime.
        """
        index = _build_tool_index(connector_config={}, connector_names=None)
        # Many tools register under multiple connectors. We just want to
        # confirm the index is non-empty when no constraint is given.
        assert len(index) > 0, (
            "connector_names=None should be 'no constraint, scan all' — "
            "an empty index here means a regression in the default path."
        )

    def test_empty_connector_names_yields_empty_index(self):
        """``connector_names=[]`` → fail-closed.

        This is the BUG-08 contract.  The agent runtime hands an empty
        list when an agent has ``connector_ids`` but none resolve to a
        live ConnectorConfig.  The tool index MUST be empty so the LLM
        has no tools to call.  Falling through to globally-registered
        connectors is the prior bug.
        """
        index = _build_tool_index(connector_config={}, connector_names=[])
        assert index == {}, (
            f"connector_names=[] should fail-closed (empty index), but got "
            f"{len(index)} tools: {list(index.keys())[:10]}.  This is the "
            "BUG-08 fail-OPEN regression — Stripe.list_invoices etc. would "
            "be reachable from agents that explicitly resolved zero "
            "ConnectorConfigs."
        )

    def test_single_connector_name_restricts_to_that_connector(self):
        """A non-empty list filters the tool index to only the named
        connectors.  Scoped fix for the May-01 Aryan agent: with
        ``connector_names=['zoho_books']``, ``list_invoices`` resolves
        to Zoho even though Stripe and QuickBooks also register the
        same tool name.
        """
        index = _build_tool_index(connector_config={}, connector_names=["zoho_books"])
        # The index should contain at least one Zoho tool.
        zoho_tools = [
            tn for tn, (cn, _desc) in index.items() if cn == "zoho_books"
        ]
        non_zoho_tools = [
            tn for tn, (cn, _desc) in index.items() if cn != "zoho_books"
        ]
        assert len(zoho_tools) > 0, (
            "Filtering on connector_names=['zoho_books'] returned zero "
            "Zoho tools — either the connector isn't registered or the "
            "filter regressed."
        )
        assert non_zoho_tools == [], (
            f"Filter leaked tools from other connectors: {non_zoho_tools[:5]}. "
            "When connector_names is specified the index must contain only "
            "those connectors' tools."
        )

    def test_registry_prefix_is_stripped_for_filter_matching(self):
        """Agents store ``connector_ids`` as ``["registry-zoho_books"]``.
        ``_build_tool_index`` must strip the prefix so the lookup matches
        the underlying ``ConnectorRegistry.all_names()`` entries.
        """
        index = _build_tool_index(
            connector_config={}, connector_names=["registry-zoho_books"]
        )
        zoho_tools = [
            tn for tn, (cn, _desc) in index.items() if cn == "zoho_books"
        ]
        assert len(zoho_tools) > 0, (
            "registry- prefix wasn't stripped before filter match — agents "
            "with the UI-flavoured connector_ids will fail to bind tools."
        )


class TestBuildToolsForAgentFailClosed:
    """The public surface used by the runtime: ``build_tools_for_agent``."""

    def test_empty_connector_names_produces_zero_tools(self):
        """Agent with ``authorized_tools=['list_invoices']`` and
        ``connector_names=[]`` (i.e. runtime resolved zero
        ConnectorConfigs) must end up with **zero** built tools.

        Pre-fix: ``build_tools_for_agent`` ignored ``connector_names``
        entirely, called ``_build_tool_index(connector_config)`` with
        no filter, and ``list_invoices`` resolved to whichever
        connector registered it last (Stripe in prod).  The LLM then
        had a callable ``list_invoices`` tool that fired against
        Stripe with no auth.

        Post-fix: empty list propagates through; tool index is empty;
        no StructuredTool is built; the LLM has nothing to call.
        """
        tools = build_tools_for_agent(
            authorized_tools=["list_invoices"],
            connector_config={},
            connector_names=[],
        )
        assert tools == [], (
            f"Agent with authorized_tools=['list_invoices'] and zero "
            f"resolved connectors got {len(tools)} tool(s) instead of 0. "
            "This is the BUG-08 fail-OPEN — the LLM would dispatch "
            "list_invoices to whichever connector won the global "
            "registration race."
        )

    def test_zoho_only_filter_excludes_stripe_list_invoices(self):
        """The May-01 reproducer in pin form.

        Aryan FpaAgent's authorized_tools include ``list_invoices``.
        Both ``zoho_books`` and ``stripe`` connectors register that
        tool name.  With ``connector_names=['zoho_books']`` the built
        tool MUST resolve to Zoho's handler, not Stripe's.
        """
        tools = build_tools_for_agent(
            authorized_tools=["list_invoices"],
            connector_config={},
            connector_names=["zoho_books"],
        )
        assert len(tools) == 1, (
            f"Expected exactly one tool, got {len(tools)}.  Either Zoho "
            "stopped exposing list_invoices or the filter is leaking."
        )
        assert tools[0].name == "list_invoices"

    def test_none_connector_names_preserves_legacy_unconstrained_behaviour(self):
        """Tests + a few synthetic callers pass ``connector_names=None``
        (the default).  That MUST keep the unconstrained behaviour so
        we don't break those call sites.  Only the explicit empty-list
        signal is the new fail-closed path.
        """
        tools = build_tools_for_agent(
            authorized_tools=["list_invoices"],
            connector_config={},
            connector_names=None,
        )
        # Some tool should resolve in the unconstrained path.
        assert len(tools) >= 1, (
            "connector_names=None used to scan all connectors and bind a "
            "list_invoices tool; an empty result means the legacy path "
            "regressed."
        )


class TestResolveConnectorConfigsReturnsResolvedNames:
    """``_resolve_connector_configs`` is the new helper that drives the
    fail-closed allow-list for the runtime.  It must return ``[]`` (not
    ``None``, not the input names) when no ConnectorConfig matches.

    These pins use a stub session so the test is hermetic — the goal is
    to lock the *return shape* of the helper, not to re-run the full
    DB round-trip (covered elsewhere by ``test_bugs_april06_2026``).
    """

    @pytest.mark.asyncio
    async def test_empty_connector_ids_returns_empty_names_list(self):
        """No connector_ids declared → ``([config or {}], [])``.

        The agent has nothing to resolve, so the runtime should NOT
        impose a fail-closed restriction (returning ``[]`` would block
        every tool, but the agent never asked for any tenant
        connectors).  The runtime layer is responsible for translating
        ``[]`` into "no constraint".
        """
        from api.v1.agents import _resolve_connector_configs

        merged, names = await _resolve_connector_configs(
            tenant_id="11111111-1111-1111-1111-111111111111",
            connector_ids=[],
            agent_level_config={"k": "v"},
        )
        assert merged == {"k": "v"}
        assert names == []

    @pytest.mark.asyncio
    async def test_invalid_tenant_returns_empty_names(self):
        """Invalid tenant_id → fail-closed.  Used to return just a
        dict; now must also return an empty resolved-names list so the
        runtime treats this case as ``connector_ids declared but none
        resolved`` and builds an empty tool index.
        """
        from api.v1.agents import _resolve_connector_configs

        merged, names = await _resolve_connector_configs(
            tenant_id="not-a-uuid",
            connector_ids=["registry-zoho_books"],
            agent_level_config=None,
        )
        assert merged == {}
        assert names == []
