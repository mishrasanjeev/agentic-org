"""Regression pins for Uday CA-Firms 2026-05-17 promotion reopen (bug 1).

Tester steps replayed:
  1. Register a Zoho Books connector for the CA firm tenant.
  2. Open /dashboard/agents/{id} for a CA pack agent (e.g. TDS Compliance),
     generate 10 shadow test samples.
  3. Click Promote.

Actual result (bug): HTTP 409 ``connector_not_ready_for_activation`` with
``income_tax_india`` and ``tally`` reported ``missing_connector_config`` —
even though the CA pack is documented to run on a Zoho-Books-only tenant.

Expected result: the agent promotes, because Zoho Books (the only
*required* connector) is healthy. ``income_tax_india`` / ``tally`` are
optional capability and stay fail-closed at runtime tool dispatch.

These tests exercise the real code path (``promote_agent`` →
``_required_connector_ids_for_agent`` → ``_assert_connectors_ready_for_
activation``) with the tester's exact already-provisioned agent shape,
and pin that the fail-closed gate is preserved for the required connector.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

TENANT_ID = uuid.UUID("49ca24aa-c6e7-4124-91af-059023295da4")

# The connector_ids an already-provisioned CA TDS agent carries from the
# pre-fix installer: every connector any authorized tool references.
PROVISIONED_BROAD_CONNECTOR_IDS = [
    "registry-zoho_books",
    "registry-income_tax_india",
    "registry-tally",
]


def _ca_tds_agent(connector_ids=None, config=None):
    """An agent row shaped exactly like the tester's provisioned TDS agent."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        agent_type="tds_compliance_agent",
        connector_ids=(
            list(PROVISIONED_BROAD_CONNECTOR_IDS)
            if connector_ids is None
            else connector_ids
        ),
        config=(
            {"pack_install": {"pack_name": "ca-firm"}}
            if config is None
            else config
        ),
    )


# ── _required_connector_ids_for_agent ────────────────────────────────────────


def test_required_subset_narrows_ca_pack_agent_to_zoho_only() -> None:
    """Tester's exact agent → activation gates on Zoho Books only."""
    from api.v1.agents import _required_connector_ids_for_agent

    agent = _ca_tds_agent()
    assert _required_connector_ids_for_agent(agent) == ["registry-zoho_books"]


def test_required_subset_self_heals_without_config_backfill() -> None:
    """Already-provisioned agent whose persisted config still has the old
    broad required list is healed live from the static pack definition."""
    from api.v1.agents import _required_connector_ids_for_agent

    agent = _ca_tds_agent(
        config={
            "pack_install": {"pack_name": "ca-firm"},
            # stale pre-fix persisted value
            "required_connector_ids": list(PROVISIONED_BROAD_CONNECTOR_IDS),
        }
    )
    assert _required_connector_ids_for_agent(agent) == ["registry-zoho_books"]


def test_required_subset_fails_closed_for_hand_built_agent() -> None:
    """Non-pack agents keep the original behaviour: gate on every linked
    connector (no silent relaxation)."""
    from api.v1.agents import _required_connector_ids_for_agent

    agent = _ca_tds_agent(config={})  # no pack_install
    assert _required_connector_ids_for_agent(agent) == list(
        PROVISIONED_BROAD_CONNECTOR_IDS
    )


def test_required_subset_fails_closed_when_pack_declares_nothing() -> None:
    """A pack agent whose type the pack does not declare required_connectors
    for falls back to the full linked set via the persisted value."""
    from api.v1.agents import _required_connector_ids_for_agent

    agent = _ca_tds_agent(
        config={
            "pack_install": {"pack_name": "ca-firm"},
            "required_connector_ids": list(PROVISIONED_BROAD_CONNECTOR_IDS),
        },
    )
    agent.agent_type = "agent_type_not_in_pack"
    assert _required_connector_ids_for_agent(agent) == list(
        PROVISIONED_BROAD_CONNECTOR_IDS
    )


# ── installer required-connector derivation ──────────────────────────────────


def test_installer_declares_zoho_only_for_every_ca_agent() -> None:
    from core.agents.packs.ca import CA_PACK
    from core.agents.packs.installer import (
        _agent_type,
        _declared_required_connector_ids,
        required_connectors_for_pack_agent,
    )

    for index, agent_cfg in enumerate(CA_PACK["agents"]):
        raw_tools = [str(t) for t in agent_cfg.get("tools", []) if t]
        # The declared subset is exactly Zoho Books for every CA agent...
        assert _declared_required_connector_ids(agent_cfg, raw_tools) == [
            "registry-zoho_books"
        ]
        # ...and the gate's authoritative lookup agrees.
        at = _agent_type(agent_cfg, index)
        assert required_connectors_for_pack_agent("ca-firm", at) == [
            "registry-zoho_books"
        ]


def test_installer_fails_closed_when_no_declaration() -> None:
    from core.agents.packs.installer import _declared_required_connector_ids

    cfg = {"tools": ["zoho_books:list_invoices", "tally:get_ledger_balance"]}
    # No required_connectors key → full linked set (fail-closed default).
    assert _declared_required_connector_ids(cfg, cfg["tools"]) == [
        "registry-zoho_books",
        "registry-tally",
    ]


def test_installer_typo_declaration_fails_closed() -> None:
    """A required_connectors value that resolves to nothing the agent links
    must NOT yield an empty (ungated) requirement."""
    from core.agents.packs.installer import _declared_required_connector_ids

    cfg = {
        "required_connectors": ["does_not_exist"],
        "tools": ["zoho_books:list_invoices", "tally:get_ledger_balance"],
    }
    assert _declared_required_connector_ids(cfg, cfg["tools"]) == [
        "registry-zoho_books",
        "registry-tally",
    ]


def test_unknown_pack_or_agent_returns_none() -> None:
    from core.agents.packs.installer import required_connectors_for_pack_agent

    assert required_connectors_for_pack_agent("no-such-pack", "x") is None
    assert required_connectors_for_pack_agent("ca-firm", "no_such_agent") is None
    assert required_connectors_for_pack_agent(None, None) is None


# ── _assert_connectors_ready_for_activation: query-aware replay ───────────────


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _ZohoOnlyTenantSession:
    """Fake session for a tenant that configured ONLY Zoho Books.

    Routes by the selected entity and (for ConnectorConfig) by the bound
    ``connector_name`` so income_tax_india / tally resolve to nothing —
    exactly the tester's tenant state.
    """

    def __init__(self):
        self.added: list = []
        self._zoho_cc = SimpleNamespace(
            id=uuid.uuid4(),
            connector_name="zoho_books",
            status="configured",
            health_status="healthy",
            auth_type="oauth2",
            credentials_encrypted={"_encrypted": "enc-blob"},
        )
        self._zoho_connector = SimpleNamespace(
            name="zoho_books", status="active"
        )

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        name = getattr(entity, "__name__", str(entity))
        if name == "ConnectorConfig":
            params = stmt.compile().params
            wants_zoho = any(
                str(v) == "zoho_books" for v in params.values()
            )
            return _Result(self._zoho_cc if wants_zoho else None)
        if name == "Connector":
            return _Result(self._zoho_connector)
        return _Result(None)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_gate_passes_on_zoho_only_tenant_for_required_subset() -> None:
    """The fix: gating on the required subset (Zoho only) → no 409."""
    from api.v1.agents import _assert_connectors_ready_for_activation

    session = _ZohoOnlyTenantSession()
    with patch(
        "core.crypto.decrypt_for_tenant",
        return_value=json.dumps({"refresh_token": "rt-present"}),
    ):
        # Must NOT raise — Zoho Books is healthy & refreshable.
        await _assert_connectors_ready_for_activation(
            session, TENANT_ID, ["registry-zoho_books"]
        )


@pytest.mark.asyncio
async def test_old_broad_list_reproduces_the_tester_bug() -> None:
    """Pin the root cause: the pre-fix broad connector_ids list is exactly
    what produced the tester's 409 on a Zoho-only tenant."""
    from api.v1.agents import _assert_connectors_ready_for_activation

    session = _ZohoOnlyTenantSession()
    with patch(
        "core.crypto.decrypt_for_tenant",
        return_value=json.dumps({"refresh_token": "rt-present"}),
    ):
        with pytest.raises(HTTPException) as exc:
            await _assert_connectors_ready_for_activation(
                session, TENANT_ID, list(PROVISIONED_BROAD_CONNECTOR_IDS)
            )
    detail = exc.value.detail
    assert exc.value.status_code == 409
    assert detail["error"] == "connector_not_ready_for_activation"
    blocked = {c["connector"]: c["reason"] for c in detail["connectors"]}
    assert blocked == {
        "income_tax_india": "missing_connector_config",
        "tally": "missing_connector_config",
    }


@pytest.mark.asyncio
async def test_gate_still_fails_closed_when_required_connector_missing() -> None:
    """Fail-closed preserved: if Zoho Books itself is not configured,
    promotion is still blocked (Rule 11.3 not weakened)."""
    from api.v1.agents import _assert_connectors_ready_for_activation

    class _EmptyTenantSession(_ZohoOnlyTenantSession):
        async def execute(self, stmt):
            entity = stmt.column_descriptions[0]["entity"]
            name = getattr(entity, "__name__", str(entity))
            if name == "ConnectorConfig":
                return _Result(None)  # nothing configured at all
            return _Result(None)

    with pytest.raises(HTTPException) as exc:
        await _assert_connectors_ready_for_activation(
            _EmptyTenantSession(), TENANT_ID, ["registry-zoho_books"]
        )
    assert exc.value.status_code == 409
    blocked = {c["connector"]: c["reason"] for c in exc.value.detail["connectors"]}
    assert blocked == {"zoho_books": "missing_connector_config"}


# ── promote_agent end-to-end: tester's exact click ───────────────────────────


@pytest.mark.asyncio
async def test_promote_agent_passes_required_subset_to_gate() -> None:
    """Replay the Promote click for the tester's provisioned TDS agent and
    assert the gate now receives only the required (Zoho) connector."""
    import api.v1.agents as agents_mod

    aid = uuid.uuid4()
    agent = MagicMock()
    agent.id = aid
    agent.status = "shadow"
    agent.agent_type = "tds_compliance_agent"
    agent.connector_ids = list(PROVISIONED_BROAD_CONNECTOR_IDS)
    agent.config = {"pack_install": {"pack_name": "ca-firm"}}
    agent.version = "1.0.0"
    agent.shadow_min_samples = 10
    agent.shadow_sample_count = 10  # tester generated 10 samples
    agent.shadow_accuracy_current = 0.97
    agent.shadow_accuracy_floor = 0.60

    # session.execute: 1) select(Agent) -> agent, then AgentVersion probes -> None
    results = [agent, None, None, None]

    def _scalar():
        return results.pop(0) if results else None

    exec_result = MagicMock()
    exec_result.scalar_one_or_none.side_effect = _scalar
    session = MagicMock()
    session.execute = AsyncMock(return_value=exec_result)
    session.add = MagicMock()
    session.flush = AsyncMock()

    captured: dict = {}

    async def _fake_gate(_session, _tid, connector_ids):
        captured["connector_ids"] = connector_ids  # must be narrowed

    with patch.object(
        agents_mod,
        "_assert_connectors_ready_for_activation",
        side_effect=_fake_gate,
    ), patch.object(agents_mod, "get_tenant_session") as mock_gts:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=None)
        mock_gts.return_value = ctx
        result = await agents_mod.promote_agent(
            agent_id=aid, tenant_id=str(TENANT_ID)
        )

    assert captured["connector_ids"] == ["registry-zoho_books"]
    assert result["promoted"] is True
    assert result["from"] == "shadow"
    assert result["to"] == "active"
