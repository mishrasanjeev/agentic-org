"""UI <-> API contract pins from the 2026-09-13 enterprise bug sweep.

The React pages were sending fields the Pydantic request models silently
dropped (``extra="ignore"``), so the UI looked successful while nothing was
persisted. These tests pin the request-model additions and the onboard
handler's field copy so the two halves cannot drift apart again.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]


# ── A2 / A5: AgentUpdate.prompt_amendments + max_retries ─────────────────


def test_agent_update_accepts_prompt_amendments_and_max_retries() -> None:
    from core.schemas.api import AgentUpdate

    body = AgentUpdate(prompt_amendments=["Always cite the ledger"], max_retries=5)
    data = body.model_dump(exclude_unset=True)
    assert data == {"prompt_amendments": ["Always cite the ledger"], "max_retries": 5}


def test_agent_update_rejects_out_of_range_max_retries() -> None:
    from core.schemas.api import AgentUpdate

    with pytest.raises(ValidationError):
        AgentUpdate(max_retries=-1)
    with pytest.raises(ValidationError):
        AgentUpdate(max_retries=21)


def test_update_agent_handler_persists_new_fields() -> None:
    """The handler must copy the new fields onto the ORM row, not just accept them."""
    src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert 'if "max_retries" in update_data' in src
    assert "agent.max_retries = int(update_data[\"max_retries\"])" in src
    assert 'if "prompt_amendments" in update_data' in src
    assert "agent.prompt_amendments = " in src


# ── A4: PromptTab sends change_reason (the model field), not prompt_change_reason


def test_agent_update_change_reason_field_name() -> None:
    from core.schemas.api import AgentUpdate

    assert "change_reason" in AgentUpdate.model_fields
    assert "prompt_change_reason" not in AgentUpdate.model_fields


# ── B1: AgentCreate.llm_routing persisted into llm_config["routing"] ──────


def test_agent_create_accepts_llm_routing_modes() -> None:
    from core.schemas.api import AgentCreate

    base = {"name": "x", "agent_type": "bookkeeper", "domain": "finance", "system_prompt_text": "hi"}
    for mode in ("auto", "tier1", "tier2", "tier3", "disabled"):
        body = AgentCreate(**base, llm_routing=mode)
        assert body.llm_routing == mode
    assert AgentCreate(**base).llm_routing is None
    with pytest.raises(ValidationError):
        AgentCreate(**base, llm_routing="turbo")


def test_create_agent_persists_routing_into_llm_config() -> None:
    src = (ROOT / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert '"routing": body.llm_routing' in src


# ── Q: CompanyOnboard field coverage ────────────────────────────────────


def test_company_onboard_accepts_wizard_fields() -> None:
    from api.v1.companies import CompanyOnboard

    body = CompanyOnboard(
        name="Acme Traders",
        pan="ABCDE1234F",
        signatory_email="cfo@acme.example",
        dsc_serial="DSC-123",
        dsc_expiry="2027-03-31",
        bank_branch="MG Road",
        tally_config={"bridge_url": "http://bridge:9000", "bridge_id": "b1", "company_name": "Acme"},
        fy_start_month="04",
        fy_end_month="03",
    )
    assert body.bank_branch == "MG Road"
    assert body.tally_config == {
        "bridge_url": "http://bridge:9000",
        "bridge_id": "b1",
        "company_name": "Acme",
    }
    assert body.fy_start_month == "04"
    assert body.fy_end_month == "03"


def test_company_onboard_rejects_bad_dates_and_months() -> None:
    from api.v1.companies import CompanyOnboard

    with pytest.raises(ValidationError):
        CompanyOnboard(name="x", pan="ABCDE1234F", dsc_expiry="31/03/2027")
    with pytest.raises(ValidationError):
        CompanyOnboard(name="x", pan="ABCDE1234F", fy_start_month="13")
    # Empty string is treated as "not provided" for dsc_expiry.
    assert CompanyOnboard(name="x", pan="ABCDE1234F", dsc_expiry="").dsc_expiry is None


def _company_ctor_kwargs() -> set[str]:
    """Keyword names passed to ``Company(...)`` inside ``onboard_company``."""
    tree = ast.parse((ROOT / "api" / "v1" / "companies.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "onboard_company":
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "Company"
                ):
                    return {kw.arg for kw in sub.keywords if kw.arg}
    raise AssertionError("Company(...) constructor not found in onboard_company")


def test_onboard_handler_copies_every_accepted_field() -> None:
    kwargs = _company_ctor_kwargs()
    for field in (
        "signatory_email",
        "dsc_serial",
        "dsc_expiry",
        "bank_branch",
        "tally_config",
        "fy_start_month",
        "fy_end_month",
    ):
        assert field in kwargs, f"onboard_company drops CompanyOnboard.{field}"


def test_company_model_has_no_dsc_holder_column() -> None:
    """The wizard's DSC Holder input was removed because nothing stores it."""
    from core.models.company import Company

    assert not hasattr(Company, "dsc_holder")
    ui = (ROOT / "ui" / "src" / "pages" / "CompanyOnboard.tsx").read_text(encoding="utf-8")
    assert "dsc_holder" not in ui
    assert "bank_branch: form.branch" in ui
