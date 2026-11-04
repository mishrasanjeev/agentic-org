# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""What the shopping network submits to outside directories, checked against their published limits.

* mcp-server/shopping/server.json - the MCP registry entry for the hosted endpoint (schema 2025-12-11:
  name ``<namespace>/<name>``, title and description of 1-100 characters, https icons of the listed
  types, a ``streamable-http`` remote), kept apart from the npm package's mcp-server/server.json.
* mcp-server/shopping/openai-plugin/ - the ChatGPT app directory package (plugin.json listing limits,
  five positive and three negative review cases naming real tools, one MCP server with no headers).
* The public pages those listings point at exist in the UI routes, and the network terms page shows
  the terms version shops accept.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.config import Settings

ROOT = Path(__file__).resolve().parents[2]
SHOPPING = ROOT / "mcp-server" / "shopping"
PLUGIN = SHOPPING / "openai-plugin"
MCP_URL = "https://mcp.agenticorg.ai/mcp"
NAME = re.compile(r"^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$")
ICON_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/svg+xml", "image/webp"}


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _network_tools() -> set[str]:
    source = (ROOT / "mcp-server" / "src" / "network.ts").read_text(encoding="utf-8")
    block = source.split("export const NETWORK_TOOLS", 1)[1].split("]);", 1)[0]
    return set(re.findall(r'"([a-z_]+)"', block))


def _ui_routes() -> set[str]:
    return set(re.findall(r'<Route path="([^"]+)"', (ROOT / "ui" / "src" / "App.tsx").read_text(encoding="utf-8")))


def test_registry_entry_for_the_hosted_endpoint() -> None:
    entry = _json(SHOPPING / "server.json")
    package = _json(ROOT / "mcp-server" / "server.json")
    assert entry["$schema"] == "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
    assert NAME.match(entry["name"]) and entry["name"] == "io.github.mishrasanjeev/agenticorg-shopping"
    assert entry["name"] != package["name"]
    assert 1 <= len(entry["title"]) <= 100 and 1 <= len(entry["description"]) <= 100
    assert entry["version"] == _json(ROOT / "mcp-server" / "package.json")["version"]
    assert entry["remotes"] == [{"type": "streamable-http", "url": MCP_URL}]
    assert "packages" not in entry
    for icon in entry["icons"]:
        assert icon["src"].startswith("https://") and len(icon["src"]) <= 255
        assert icon["mimeType"] in ICON_TYPES
    assert entry["repository"] == {
        "url": "https://github.com/mishrasanjeev/agentic-org",
        "source": "github",
        "subfolder": "mcp-server",
    }
    # The hosted server introduces itself with the same title.
    http_source = (ROOT / "mcp-server" / "src" / "http.ts").read_text(encoding="utf-8")
    assert f'title: "{entry["title"]}"' in http_source


def test_the_npm_package_entry_is_unchanged_and_still_valid_in_shape() -> None:
    package = _json(ROOT / "mcp-server" / "server.json")
    assert package["name"] == "io.github.mishrasanjeev/agenticorg"
    assert package["packages"][0]["transport"] == {"type": "stdio"}
    assert "remotes" not in package


def test_openai_plugin_listing_fits_the_directory_limits() -> None:
    plugin = _json(PLUGIN / "plugin.json")
    assert plugin["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", plugin["name"]) and len(plugin["name"]) <= 64
    listing = plugin["extensions"]["com.openai"]["interface"]
    assert len(listing["displayName"]) <= 30 and "MCP" not in listing["displayName"]
    assert len(listing["shortDescription"]) <= 30
    assert len(listing["longDescription"]) <= 4000
    assert len(listing["developerName"]) <= 80
    assert len(listing["capabilities"]) <= 20 and all(len(c) <= 120 for c in listing["capabilities"])
    prompts = listing["defaultPrompt"]
    assert len(prompts) <= 3 and len(set(prompts)) == len(prompts) and all(len(p) <= 128 for p in prompts)
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", listing["brandColor"])
    routes = _ui_routes()
    for key in ("websiteURL", "supportURL", "privacyPolicyURL", "termsOfServiceURL"):
        url = listing[key]
        assert url.startswith("https://agenticorg.ai") and len(url) <= 1024, key
        path = url.removeprefix("https://agenticorg.ai") or "/"
        assert path in routes, f"{key} points at {path}, which is not a UI route"
    for key in ("logo", "composerIcon"):
        image = PLUGIN / listing[key].removeprefix("./")
        data = image.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) <= 5 * 1024 * 1024
        width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        assert width == height >= 48


def test_openai_review_cases_name_real_tools() -> None:
    review = _json(PLUGIN / "plugin.json")["extensions"]["com.openai"]["review"]
    tools = _network_tools()
    assert tools >= {"list_shops", "search_catalog", "complete_checkout"}
    positive, negative = review["test_cases"]["positive"], review["test_cases"]["negative"]
    assert len(positive) == 5 and len(negative) == 3
    for case in positive:
        assert {"description", "prompt", "tools_triggered", "expected_behavior"} <= set(case)
        assert case["tools_triggered"] and set(case["tools_triggered"]) <= tools
    for case in negative:
        assert set(case) == {"description", "prompt"}
    assert review["commerce"] is True and review["commerce_description"]


def test_openai_mcp_config_is_one_public_server() -> None:
    config = _json(PLUGIN / "mcp.json")
    assert config["$schema"] == "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
    (server,) = config["mcpServers"].values()
    assert server == {"type": "streamable-http", "url": MCP_URL}


def test_network_terms_page_shows_the_accepted_terms_version() -> None:
    page = (ROOT / "ui" / "src" / "pages" / "legal" / "NetworkTerms.tsx").read_text(encoding="utf-8")
    default = Settings.model_fields["network_terms_version"].default
    assert f'export const NETWORK_TERMS_VERSION = "{default}";' in page
    checkbox = (ROOT / "ui" / "src" / "pages" / "SellEverywhere.tsx").read_text(encoding="utf-8")
    assert "I remain the seller and take payment myself." in " ".join(checkbox.split())
    assert "I remain the seller and take payment myself." in " ".join(page.split())
    assert "/network-terms" in _ui_routes()
