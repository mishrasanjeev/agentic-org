"""Regression coverage for the 22-May CA Firms report."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _mock_client_response(json_data=None):
    resp = MagicMock()
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    resp.status_code = 200
    return resp


def _async_response(json_data=None):
    return AsyncMock(return_value=_mock_client_response(json_data))


def test_agent_delete_is_not_a_physical_row_delete() -> None:
    src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    delete_block = src.split("# ── DELETE /agents/", 1)[1][:6000]

    assert "await session.delete(agent)" not in delete_block
    assert 'agent.status = "deleted"' in delete_block
    assert "StepExecution.agent_id == agent_id" in delete_block
    assert "LeadPipeline.assigned_agent_id == agent_id" in delete_block
    assert "ApprovalPolicy.agent_id == agent_id" in delete_block
    assert "HITLQueue.__table__.delete" in delete_block


@pytest.mark.asyncio
async def test_github_connector_reads_repository_files_and_decodes_content() -> None:
    from connectors.comms.github_connector import GithubConnector

    connector = GithubConnector({"personal_access_token": "token"})
    connector._client = MagicMock()
    encoded = base64.b64encode(b"hello world").decode("ascii")
    connector._client.get = _async_response(
        {
            "name": "README.md",
            "path": "README.md",
            "sha": "abc123",
            "encoding": "base64",
            "size": 11,
            "content": encoded,
        }
    )

    out = await connector.read_file(owner="acme", repo="app", path="README.md", ref="main")

    assert out["content"] == "hello world"
    args = connector._client.get.call_args
    assert args.args[0] == "/repos/acme/app/contents/README.md"
    assert args.kwargs["params"]["ref"] == "main"


@pytest.mark.asyncio
async def test_github_connector_can_create_branch_and_commit_multi_file_changes() -> None:
    from connectors.comms.github_connector import GithubConnector

    connector = GithubConnector({"personal_access_token": "token"})
    connector._client = MagicMock()
    connector._client.get = AsyncMock(
        side_effect=[
            _mock_client_response({"object": {"sha": "parent-sha"}}),
            _mock_client_response({"tree": {"sha": "base-tree"}}),
        ]
    )
    connector._client.post = AsyncMock(
        side_effect=[
            _mock_client_response({"sha": "blob-a"}),
            _mock_client_response({"sha": "new-tree"}),
            _mock_client_response({"sha": "new-commit"}),
        ]
    )
    connector._client.patch = _async_response({"ref": "refs/heads/work"})

    out = await connector.commit_changes(
        owner="acme",
        repo="app",
        branch="work",
        message="Update app",
        changes=[{"path": "src/app.ts", "content": "export const ok = true;"}],
    )

    assert out["status"] == "committed"
    assert out["commit_sha"] == "new-commit"
    post_paths = [call.args[0] for call in connector._client.post.call_args_list]
    assert post_paths == [
        "/repos/acme/app/git/blobs",
        "/repos/acme/app/git/trees",
        "/repos/acme/app/git/commits",
    ]
    connector._client.patch.assert_called_once()


@pytest.mark.asyncio
async def test_zoho_books_exposes_vendor_bill_expense_vendor_and_tds_journal_tools() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector({"access_token": "fake", "organization_id": "org123"})
    connector._client = MagicMock()
    connector._client.get = _async_response({"bills": [{"bill_id": "B1"}]})
    bills = await connector.list_vendor_bills(vendor_id="V1")
    assert bills["bills"][0]["bill_id"] == "B1"
    assert connector._client.get.call_args.args[0] == "/bills"
    assert connector._client.get.call_args.kwargs["params"]["organization_id"] == "org123"

    connector._client.get = _async_response({"contacts": [{"contact_id": "V1"}]})
    vendors = await connector.list_vendors(search_text="Acme")
    assert vendors["vendors"][0]["contact_id"] == "V1"
    assert connector._client.get.call_args.args[0] == "/contacts"
    assert connector._client.get.call_args.kwargs["params"]["contact_type"] == "vendor"

    connector._client.post = _async_response({"journal": {"journal_id": "J1"}})
    journal = await connector.create_tds_entry(
        date="2026-05-22",
        expense_account_id="expense",
        vendor_account_id="vendor",
        tds_payable_account_id="tds",
        amount=10000,
        tds_amount=1000,
    )
    assert journal["journal_id"] == "J1"
    body = connector._client.post.call_args.kwargs["json"]
    assert connector._client.post.call_args.args[0] == "/journals"
    assert [line["amount"] for line in body["line_items"]] == [10000.0, 9000.0, 1000.0]


@pytest.mark.asyncio
async def test_income_tax_tds_intelligence_handles_missing_pan_structurally() -> None:
    from connectors.finance.income_tax_india import IncomeTaxIndiaConnector

    connector = IncomeTaxIndiaConnector({})

    calc = await connector.calculate_tds(amount=50000, section="194C", pan_available="false")
    assert calc["rate"] == 0.20
    assert calc["tds_amount"] == 10000.0

    detected = await connector.detect_tds_applicability(
        amount=50000,
        ledger_name="Professional fees",
        pan="",
    )
    assert detected["applicable"] is True
    assert detected["section"] == "194J"
    assert detected["pan_available"] is False
