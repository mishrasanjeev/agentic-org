# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""scripts/seed_e2e_demo_agents.py finds the seeded agents on every page of the agents list."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seed_e2e_demo_agents.py"


def _seed_module():
    spec = importlib.util.spec_from_file_location("seed_e2e_demo_agents", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(monkeypatch: pytest.MonkeyPatch, agents: list[dict[str, Any]]) -> list[tuple[str, str, dict[str, str]]]:
    calls: list[tuple[str, str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append((request.method, request.url.path, params))
        path = request.url.path
        if path == "/api/v1/companies":
            return httpx.Response(200, json={"items": [{"id": "company-1"}]})
        if path.startswith("/api/v1/approvals"):
            return httpx.Response(200, json={"items": [{"id": "approval-1"}], "total": 1})
        if request.method == "GET" and path == "/api/v1/agents":
            page, per_page = int(params["page"]), int(params["per_page"])
            # The API caps a page at 100 whatever the caller asks for.
            per_page = min(per_page, 100)
            return httpx.Response(200, json={"items": agents[(page - 1) * per_page : page * per_page]})
        if request.method == "PATCH":
            return httpx.Response(200, json={})
        if request.method == "POST" and path == "/api/v1/agents":
            return httpx.Response(201, json={"id": "created"})
        return httpx.Response(200, json={"items": []})

    seed = _seed_module()
    real_client = httpx.Client
    monkeypatch.setattr(
        seed.httpx, "Client", lambda *a, **k: real_client(*a, transport=httpx.MockTransport(handler), **k)
    )
    monkeypatch.setattr(sys, "argv", ["seed", "--base-url", "https://example.test", "--token", "synthetic-token"])
    assert seed.main() == 0
    return calls


def test_seeded_agents_past_page_one_are_refreshed_not_recreated(monkeypatch) -> None:
    # A leftover agent of the same type comes first; the seeded ones are on page 3.
    agents = [
        {"id": f"leftover-{i}", "agent_type": "ap_processor" if i == 5 else "other", "employee_name": f"x {i}"}
        for i in range(228)
    ]
    agents += [
        {"id": "seeded-ap", "agent_type": "ap_processor", "employee_name": "E2E AP Processor", "status": "shadow"},
        {"id": "seeded-ta", "agent_type": "talent_acquisition", "employee_name": "E2E Talent Acquisition"},
    ]

    calls = _run(monkeypatch, agents)

    pages = [c[2]["page"] for c in calls if c[0] == "GET" and c[1] == "/api/v1/agents"]
    assert pages == ["1", "2", "3"]
    patched = [c[1] for c in calls if c[0] == "PATCH"]
    assert "/api/v1/agents/seeded-ap" in patched and "/api/v1/agents/seeded-ta" in patched
    assert "/api/v1/agents/leftover-5" not in patched
    created = [c for c in calls if c[0] == "POST" and c[1] == "/api/v1/agents"]
    assert len(created) == 1  # only the type that truly has no agent


def test_without_a_seeded_agent_the_first_live_one_of_the_type_is_used(monkeypatch) -> None:
    agents = [
        {"id": "retired-ap", "agent_type": "ap_processor", "employee_name": "old", "status": "retired"},
        {"id": "live-ap", "agent_type": "ap_processor", "employee_name": "renamed", "status": "shadow"},
    ]

    calls = _run(monkeypatch, agents)

    patched = [c[1] for c in calls if c[0] == "PATCH"]
    assert "/api/v1/agents/live-ap" in patched
    assert "/api/v1/agents/retired-ap" not in patched
