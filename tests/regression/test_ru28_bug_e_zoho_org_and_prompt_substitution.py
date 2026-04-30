"""RU28-Bug-E regression pins.

Original symptom (gunjan@gmail.com on 2026-04-29):
    POST /agents/{id}/run on the Zoho Books FPA agent returned
    confidence: 0.4, tool_calls: [], runtime: "langgraph". The LLM
    refused with: "I cannot fulfill this request. The available tools
    do not have the capability to fetch organization details such as
    company name and organization ID."

Investigation (2026-04-30) revealed three independent issues and
fixed all three in a single PR:

1. **Missing get_organization tool** — the agent's user-supplied
   system_prompt_text literally said "fetch my organization details
   including company name and organization ID", but the Zoho Books
   connector exposed 7 tools and none returned org details. The
   LLM was correctly stuck. Added `get_organization`.

2. **Template substitution typo across 28 agent modules** —
   `core/langgraph/agents/*.py` had `template.replace("{{}" + key + "}}", val)`
   which builds the search string `"{{}org_name}}"` instead of
   `"{{org_name}}"`. So the substitution never matched and the
   LLM saw literal `{{org_name}}` placeholders in its system
   prompt. Mass fix to all 28 sites; canonical version in
   `api/v1/agents.py:1635` was already correct.

3. **`_AGENT_TYPE_DEFAULT_TOOLS` drift for fpa_agent** — three
   sites defined the default tool list with different contents:
   `api/v1/agents.py:69-76` (canonical, 6 tools),
   `core/agent_generator.py:198-201` (4 tools, missing
   get_profit_loss + get_trial_balance), and
   `core/langgraph/agents/fpa_agent.py:11-16` (same drift).
   Same drift class as PR #386 Bug-D. Synced all three.

These pins ensure none of the three regress.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────────
# Pin #1 — Zoho Books exposes get_organization as a registered tool
# ─────────────────────────────────────────────────────────────────


def test_zoho_books_registers_get_organization_tool() -> None:
    """get_organization must live in the connector's tool registry so
    the LangGraph tool_adapter binds it for any agent whose
    authorized_tools include it. Without this, a user prompt asking
    for org details has no tool that can satisfy it and the LLM
    refuses (the original RU28-Bug-E symptom).
    """
    from connectors.finance.zoho_books import ZohoBooksConnector

    instance = ZohoBooksConnector(config={"organization_id": "test-org"})
    assert "get_organization" in instance._tool_registry, (
        "get_organization missing from Zoho Books tool registry — "
        "agents asking for org details will be stuck again."
    )
    handler = instance._tool_registry["get_organization"]
    assert callable(handler)
    # Must be an async coroutine function (every other Zoho tool is)
    assert inspect.iscoroutinefunction(handler)


@pytest.mark.asyncio
async def test_zoho_books_get_organization_routes_to_organizations_endpoint(
    monkeypatch,
) -> None:
    """End-to-end: calling the tool hits GET /organizations on the
    Zoho API and returns the organizations list. Uses fake_connectors
    seam (Foundation #7 PR-D) so no real network."""
    from connectors.finance.zoho_books import ZohoBooksConnector
    from core.test_doubles import fake_connectors

    fake_connectors.register(
        method="GET",
        url_contains="/organizations",
        status=200,
        json={
            "organizations": [
                {
                    "organization_id": "60001234567",
                    "name": "TechSolutions India Pvt Ltd",
                    "currency_code": "INR",
                }
            ]
        },
    )

    conn = ZohoBooksConnector(
        config={
            "organization_id": "60001234567",
            "access_token": "fake-token",
        }
    )
    await conn.connect()
    try:
        result = await conn.get_organization()
    finally:
        await conn.disconnect()

    assert result == {
        "organizations": [
            {
                "organization_id": "60001234567",
                "name": "TechSolutions India Pvt Ltd",
                "currency_code": "INR",
            }
        ]
    }


# ─────────────────────────────────────────────────────────────────
# Pin #2 — Every agent module's load_prompt actually substitutes
# ─────────────────────────────────────────────────────────────────


def _list_agent_modules() -> list[str]:
    """Return every agent module under core/langgraph/agents that
    defines load_prompt. Discovered dynamically so a 29th agent
    automatically inherits the regression pin."""
    agents_dir = Path(__file__).parent.parent.parent / "core" / "langgraph" / "agents"
    modules: list[str] = []
    for p in sorted(agents_dir.glob("*.py")):
        if p.name in ("__init__.py", "base_agent.py"):
            continue
        try:
            mod = importlib.import_module(f"core.langgraph.agents.{p.stem}")
        except Exception:  # noqa: BLE001, S112 — skip non-importable agent modules during discovery
            continue
        if hasattr(mod, "load_prompt"):
            modules.append(p.stem)
    return modules


@pytest.mark.parametrize("agent_module", _list_agent_modules())
def test_agent_load_prompt_substitutes_jinja_style_placeholders(
    agent_module: str, tmp_path, monkeypatch
) -> None:
    """The pre-fix code had ``template.replace("{{}" + key + "}}", val)``
    which builds the search string ``{{}key}}`` and never matches the
    actual ``{{key}}`` placeholder in the prompt files. We pin every
    agent module's load_prompt: given a prompt template containing
    ``{{org_name}}`` and a substitution dict, the output must contain
    the substituted value and must NOT contain the literal
    ``{{org_name}}`` placeholder.

    Auto-discovers all agent modules with load_prompt so this pin
    keeps biting if a 29th file lands with the typo.
    """
    mod = importlib.import_module(f"core.langgraph.agents.{agent_module}")

    # Point the agent module at a controlled prompt file containing
    # a known placeholder. We monkeypatch the PROMPTS_DIR plus the
    # specific filename the module reads. Most modules name the
    # prompt after the agent_module itself.
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    # Modules pick the filename based on their own module name —
    # write a dummy prompt at every plausible path so we don't have
    # to know each module's wiring detail.
    template_body = "Hello {{org_name}}, you are the {{role}} agent."
    for stem in {agent_module, agent_module.replace("_agent", ""), "agent"}:
        (prompt_dir / f"{stem}.prompt.txt").write_text(template_body)

    monkeypatch.setattr(mod, "PROMPTS_DIR", str(prompt_dir), raising=True)

    rendered = mod.load_prompt({"org_name": "Acme Corp", "role": "FP&A"})

    assert "Acme Corp" in rendered, (
        f"{agent_module}.load_prompt failed to substitute {{{{org_name}}}}"
    )
    assert "FP&A" in rendered, (
        f"{agent_module}.load_prompt failed to substitute {{{{role}}}}"
    )
    assert "{{org_name}}" not in rendered, (
        f"{agent_module}.load_prompt left {{{{org_name}}}} unsubstituted "
        "— the {{}}-key-{{}} typo regressed"
    )
    assert "{{role}}" not in rendered, (
        f"{agent_module}.load_prompt left {{{{role}}}} unsubstituted "
        "— the {{}}-key-{{}} typo regressed"
    )


# ─────────────────────────────────────────────────────────────────
# Pin #3 — _AGENT_TYPE_DEFAULT_TOOLS for fpa_agent is consistent
# ─────────────────────────────────────────────────────────────────


def test_fpa_agent_default_tools_match_across_definition_sites() -> None:
    """fpa_agent default tools live in three places. Drift produces
    silently-narrower agents on whichever creation path uses the
    diverged dict (same drift class as PR #386 Bug-D). Pin all three
    against the canonical api/v1/agents.py list.
    """
    from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS as API_DEFAULTS  # noqa: N811
    from core.agent_generator import (  # noqa: N811
        _AGENT_TYPE_DEFAULT_TOOLS as GENERATOR_DEFAULTS,
    )
    from core.langgraph.agents.fpa_agent import DEFAULT_TOOLS as RUNTIME_FPA_TOOLS  # noqa: N811

    canonical = set(API_DEFAULTS["fpa_agent"])
    assert canonical == set(GENERATOR_DEFAULTS["fpa_agent"]), (
        "core/agent_generator.py:_AGENT_TYPE_DEFAULT_TOOLS['fpa_agent'] "
        "drifted from api/v1/agents.py — newly-created fpa_agents will "
        "miss tools their handlers expect."
    )
    assert canonical == set(RUNTIME_FPA_TOOLS), (
        "core/langgraph/agents/fpa_agent.py:DEFAULT_TOOLS drifted from "
        "api/v1/agents.py — REST /run path binds a narrower toolset."
    )
    # Plus the specific tools that were missing from the drifted
    # versions before this fix — they MUST be present:
    for required in ("get_profit_loss", "get_trial_balance"):
        assert required in RUNTIME_FPA_TOOLS, (
            f"{required} missing from runtime DEFAULT_TOOLS — that was "
            "exactly the gap RU28-Bug-E investigation surfaced."
        )
        assert required in GENERATOR_DEFAULTS["fpa_agent"], (
            f"{required} missing from agent_generator DEFAULT_TOOLS"
        )
