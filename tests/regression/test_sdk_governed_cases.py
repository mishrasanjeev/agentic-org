# SPDX-License-Identifier: Apache-2.0
"""The client exposes machine-safe case calls without a human-decision shortcut."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from typing import Any

import httpx
import pytest


def _sdk() -> Any:
    path = pathlib.Path(__file__).resolve().parents[2] / "sdk" / "agenticorg" / "client.py"
    spec = importlib.util.spec_from_file_location("_repo_cases_sdk", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["_repo_cases_sdk"] = module
    spec.loader.exec_module(module)
    return module


def test_case_client_contract_and_denials() -> None:
    sdk = _sdk()
    case_ref = "case_" + "a" * 24
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == "Bearer test-api-key"
        path = request.url.path
        if path == "/api/v1/governed-cases" and request.method == "POST":
            assert json.loads(request.content) == {
                "application": {"legal_name": "Example Ltd", "jurisdiction": "GB"},
                "purpose": "aml.cdd.onboarding",
                "policy_id": "uk_onboarding",
            }
            return httpx.Response(201, json={"case_ref": case_ref, "state": "submitted"})
        if path == "/api/v1/governed-cases" and request.method == "GET":
            if request.url.params.get("state") == "denied":
                return httpx.Response(403, json={"error": {"reason": "governed_cases_disabled"}})
            assert dict(request.url.params) == {"state": "submitted", "limit": "10"}
            return httpx.Response(200, json={"cases": [{"case_ref": case_ref}]})
        if path == f"/api/v1/governed-cases/{case_ref}" and request.method == "GET":
            return httpx.Response(200, json={"case": {"case_id": case_ref}})
        if path == f"/api/v1/governed-cases/{case_ref}/investigate":
            return httpx.Response(202, json={"status": "investigation_scheduled"})
        return httpx.Response(403, json={"error": {"reason": "governed_cases_disabled"}})

    with sdk.AgenticOrg(api_key="test-api-key", base_url="https://example.test") as client:
        client._http.close()
        client._http = httpx.Client(
            base_url="https://example.test",
            transport=httpx.MockTransport(handler),
            headers=client._build_headers(),
        )
        client.cases._http = client._http
        assert client.cases.submit(
            {"legal_name": "Example Ltd", "jurisdiction": "GB"},
            purpose="aml.cdd.onboarding",
            policy_id="uk_onboarding",
        )["case_ref"] == case_ref
        assert client.cases.list(state="submitted", limit=10)[0]["case_ref"] == case_ref
        assert client.cases.get(case_ref)["case"]["case_id"] == case_ref
        assert client.cases.investigate(case_ref)["status"] == "investigation_scheduled"
        assert not hasattr(client.cases, "decide")
        assert not hasattr(client.cases, "withdraw")
        with pytest.raises(ValueError):
            client.cases.get("../decision")
        with pytest.raises(ValueError):
            client.cases.list(limit=201)
        with pytest.raises(httpx.HTTPStatusError) as denied:
            client.cases.list(state="denied")
        assert denied.value.response.status_code == 403
        assert len(seen) == 5
