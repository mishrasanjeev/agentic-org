from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_python_sdk_release_version_matches_cli_package_metadata() -> None:
    pyproject = tomllib.loads((ROOT / "sdk" / "pyproject.toml").read_text(encoding="utf-8"))
    init_text = (ROOT / "sdk" / "agenticorg" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)

    assert match
    assert pyproject["project"]["name"] == "agenticorg"
    assert pyproject["project"]["version"] == "0.3.0"
    assert match.group(1) == pyproject["project"]["version"]
    assert pyproject["project"]["scripts"]["agenticorg"] == "agenticorg.cli:main"


def test_root_package_exposes_direct_cli_and_sdk_package() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    release_docs = (ROOT / "docs" / "release-sdks.md").read_text(encoding="utf-8")

    assert pyproject["project"]["name"] == "agenticorg"
    assert pyproject["project"]["scripts"]["agenticorg"] == "agenticorg.cli:main"
    assert pyproject["project"]["scripts"]["agenticorg-bridge"] == "bridge.cli:main"
    assert "sdk/agenticorg" in pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert "do not upload the root/full-platform wheel to PyPI" in release_docs
    assert "Do not run `twine upload` from the repository root" in release_docs


def test_typescript_sdk_release_metadata_uses_published_package_name() -> None:
    package_json = _read_json("sdk-ts/package.json")
    package_lock = _read_json("sdk-ts/package-lock.json")
    source = (ROOT / "sdk-ts" / "src" / "index.ts").read_text(encoding="utf-8")

    assert package_json["name"] == "agenticorg-sdk"
    assert package_json["version"] == "0.3.0"
    assert package_lock["version"] == package_json["version"]
    assert package_lock["packages"][""]["version"] == package_json["version"]
    assert 'from "agenticorg-sdk"' in source
    assert "@agenticorg/sdk" not in source


def test_mcp_server_release_metadata_is_lockstep_and_runtime_truthful() -> None:
    package_json = _read_json("mcp-server/package.json")
    package_lock = _read_json("mcp-server/package-lock.json")
    server_json = _read_json("mcp-server/server.json")
    readme = (ROOT / "mcp-server" / "README.md").read_text(encoding="utf-8")

    assert package_json["version"] == "4.0.5"
    assert package_lock["version"] == package_json["version"]
    assert package_lock["packages"][""]["version"] == package_json["version"]
    assert server_json["version"] == package_json["version"]
    assert server_json["packages"][0]["version"] == package_json["version"]
    assert len(server_json["description"]) <= 100

    expected_tools = {
        "list_agents",
        "run_agent",
        "get_agent_details",
        "create_agent_from_sop",
        "deploy_agent",
        "list_connectors",
        "list_mcp_tools",
        "discover_agents_a2a",
        "get_agent_card",
        "seller.list_products",
        "seller.search_products",
        "seller.get_product_facts",
        "seller.get_offer_snapshot",
        "seller.get_inventory_snapshot",
        "seller.ask_product_question",
    }
    assert {tool["name"] for tool in server_json["tools"]} == expected_tools

    removed_or_unimplemented_tools = {
        "call_connector_tool",
        "create_agent",
        "list_workflows",
        "run_workflow",
        "search_knowledge_base",
    }
    assert removed_or_unimplemented_tools.isdisjoint({tool["name"] for tool in server_json["tools"]})
    assert "call_connector_tool" not in readme
