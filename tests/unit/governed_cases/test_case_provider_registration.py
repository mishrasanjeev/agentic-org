# SPDX-License-Identifier: Apache-2.0
"""Governed-case agent registration must use the verification provider's manifest."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from auth import grantex_registration as registration
from core.config import settings
from core.tool_gateway.provider_gateway import READ_TOOLS

MANIFESTS = Path(__file__).resolve().parents[3] / "manifests"


def test_case_role_registration_uses_provider_read_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "case_provider", "mock")
    monkeypatch.setenv("GRANTEX_MANIFESTS_DIR", str(MANIFESTS))
    captured: dict[str, Any] = {}

    def register(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(id="ag_case", did="did:example:case")

    client = SimpleNamespace(agents=SimpleNamespace(register=register))
    monkeypatch.setattr(registration, "_get_grantex_client", lambda: client)
    result = registration.register_agent(
        "Underwriter", "business_underwriter", "compliance", list(READ_TOOLS)
    )
    assert result is not None and result["grantex_agent_id"] == "ag_case"
    assert set(captured["scopes"]) == {f"tool:mock:read:{tool}" for tool in READ_TOOLS}
    assert result["grantex_scopes"] == captured["scopes"]


@pytest.mark.parametrize("tools", [[], ["unknown_tool"], ["resolve_business", "delete_business"]])
def test_case_role_registration_refuses_undeclared_tools(
    monkeypatch: pytest.MonkeyPatch, tools: list[str]
) -> None:
    monkeypatch.setattr(settings, "case_provider", "mock")
    monkeypatch.setenv("GRANTEX_MANIFESTS_DIR", str(MANIFESTS))

    def unexpected(**kwargs: Any) -> Any:
        raise AssertionError("invalid scopes must never reach registration")

    monkeypatch.setattr(
        registration, "_get_grantex_client", lambda: SimpleNamespace(agents=SimpleNamespace(register=unexpected))
    )
    assert registration.register_agent("Underwriter", "business_underwriter", "compliance", tools) is None


def test_case_role_registration_refuses_missing_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "case_provider", "mock")
    monkeypatch.setenv("GRANTEX_MANIFESTS_DIR", str(tmp_path))

    def unexpected(**kwargs: Any) -> Any:
        raise AssertionError("a missing manifest must never reach registration")

    monkeypatch.setattr(
        registration, "_get_grantex_client", lambda: SimpleNamespace(agents=SimpleNamespace(register=unexpected))
    )
    assert registration.register_agent("Underwriter", "screening_disposition", "compliance", ["screen_person"]) is None


def test_case_role_registration_refuses_wrong_manifest_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    from grantex import ToolManifest

    monkeypatch.setattr(settings, "case_provider", "mock")
    monkeypatch.setenv("GRANTEX_MANIFESTS_DIR", str(MANIFESTS))
    monkeypatch.setattr(
        ToolManifest,
        "from_file",
        lambda filename: SimpleNamespace(connector="other", tools={"resolve_business": "read"}),
    )
    with pytest.raises(ValueError, match="does not match"):
        registration._case_provider_scopes(["resolve_business"])


def test_custom_provider_uses_its_own_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    from grantex import ToolManifest

    monkeypatch.setattr(settings, "case_provider", "acme_kyb")
    monkeypatch.setenv("GRANTEX_MANIFESTS_DIR", str(MANIFESTS))
    seen: list[str] = []

    def load(filename: str) -> Any:
        seen.append(filename)
        return SimpleNamespace(connector="acme_kyb", tools={"resolve_business": "read"})

    monkeypatch.setattr(ToolManifest, "from_file", load)
    assert registration._case_provider_scopes(["resolve_business"]) == [
        "tool:acme_kyb:read:resolve_business"
    ]
    assert seen == [str(MANIFESTS / "acme_kyb.json")]


def test_provider_name_cannot_escape_manifest_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "case_provider", "../other")
    with pytest.raises(ValueError, match="invalid case provider"):
        registration._case_provider_scopes(["resolve_business"])
