from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMERCE_CODE = [
    ROOT / "connectors" / "commerce" / "grantex_commerce.py",
    ROOT / "core" / "commerce" / "sales_guardrails.py",
    ROOT / "core" / "commerce" / "staging_evidence.py",
    ROOT / "core" / "commerce" / "staging_runtime.py",
    ROOT / "core" / "langgraph" / "agents" / "commerce_sales_agent.py",
]

BANNED_IMPORT_FRAGMENTS = {
    "stripe",
    "pinelabs_plural",
    "plural",
    "pine",
    "provider_credentials",
    "core.billing",
}

BANNED_CALL_FRAGMENTS = {
    "stripeconnector",
    "pinelabspluralconnector",
    "create_payment_link",
    "provider_credentials",
    "credential_payload",
    "client_secret",
    "webhook_secret",
}


def test_commerce_code_does_not_import_provider_connectors_or_credentials() -> None:
    for path in COMMERCE_CODE:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.Import):
                module = " ".join(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
            if module:
                lowered = module.lower()
                assert not any(fragment in lowered for fragment in BANNED_IMPORT_FRAGMENTS), path


def test_commerce_code_does_not_call_provider_or_credential_paths() -> None:
    for path in COMMERCE_CODE:
        lowered = path.read_text().lower()
        assert not any(fragment in lowered for fragment in BANNED_CALL_FRAGMENTS), path


def test_commerce_agent_default_toolset_is_grantex_only() -> None:
    from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS, _DOMAIN_DEFAULT_TOOLS

    tools = _AGENT_TYPE_DEFAULT_TOOLS["commerce_sales_agent"]
    domain_tools = _DOMAIN_DEFAULT_TOOLS["commerce"]

    assert tools == domain_tools
    assert tools
    assert all(tool.startswith("grantex_commerce:") for tool in tools)
    assert all("stripe" not in tool.lower() for tool in tools)
    assert all("plural" not in tool.lower() for tool in tools)
    assert all("pine" not in tool.lower() for tool in tools)


def test_payment_status_polling_goes_through_grantex_only() -> None:
    from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

    tools = _AGENT_TYPE_DEFAULT_TOOLS["commerce_sales_agent"]

    assert "grantex_commerce:payment_get_status" in tools
    assert "payment_get_status" not in tools
    assert not any("check_order_status" == tool for tool in tools)

