# SPDX-License-Identifier: Apache-2.0
"""HTTP discovery -> encrypted DB -> agent save -> governed MCP execution."""

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from core.database import get_tenant_session
from core.models.connector_config import ConnectorConfig
from tests.unit.test_remote_mcp import (  # noqa: F401 - shared real TCP server fixture
    TOKEN,
    URL,
    mcp_server,
    remote_grant_client,  # noqa: F401 - real signed grant fixture
)


@pytest.mark.asyncio
async def test_remote_mcp_persisted_registration_agent_and_revocation(
    client,
    auth_headers,
    tenant_id,
    make_auth_headers,
    mcp_server,  # noqa: F811
    remote_grant_client,  # noqa: F811
    monkeypatch,
):
    from api.v1.agents import _resolve_connector_configs
    from auth.grant_enforcement import EnforcementMode
    from auth.run_grants import NO_RUN_GRANT_FOR_TESTS, RunGrant
    from core.langgraph import grantex_auth
    from core.langgraph.tool_adapter import build_tools_for_agent, execute_agent_tool
    from core.models.agent import Agent
    from core.models.company import Company
    from core.remote_mcp import CATALOG_KEY

    name = f"mcp_test_{uuid4().hex[:10]}"
    path = "/api/v1/connectors/mcp"
    bad = await client.post(path, headers=auth_headers, json={"name": name, "url": URL, "access_token": "bad"})
    assert bad.status_code == 422, bad.text
    result = await client.post(path, headers=auth_headers, json={"name": name, "url": URL, "access_token": TOKEN})
    assert result.status_code == 201, result.text
    assert TOKEN not in result.text
    data = result.json()
    cid = data["id"]
    tools = data["tools"]
    ref = f"{name}__gnani_transcribe"
    write_ref = f"{name}__gnani_voice_reply"
    assert {t["permission"] for t in tools} == {"write"}
    assert (
        await client.post(path, headers=auth_headers, json={"name": name, "url": URL, "access_token": TOKEN})
    ).status_code == 409
    other = make_auth_headers(tenant_id=str(uuid4()))
    assert (await client.get(path, headers=other)).json()["items"] == []
    assert (await client.post(f"{path}/{cid}/refresh", headers=other, json={})).status_code == 404
    assert (
        await client.post(f"{path}/{cid}/probe", headers=auth_headers, json={"tool": "gnani_voice_reply"})
    ).status_code == 422
    hashes = {t["name"]: t["schema_hash"] for t in tools}
    review = await client.put(
        f"{path}/{cid}/permissions",
        headers=auth_headers,
        json={
            "schema_hashes": hashes,
            "read_only_tools": ["gnani_transcribe"],
        },
    )
    assert review.status_code == 200, review.text
    tested = await client.post(
        f"{path}/{cid}/probe", headers=auth_headers, json={"tool": "gnani_transcribe", "arguments": {"text": "first"}}
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()["tested"] is True
    async with get_tenant_session(UUID(tenant_id)) as session:
        config = (
            await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == UUID(tenant_id),
                    ConnectorConfig.connector_name == name,
                )
            )
        ).scalar_one()
        assert TOKEN not in json.dumps(config.credentials_encrypted)
        assert config.credentials_encrypted.get("_encrypted")
        company = Company(tenant_id=UUID(tenant_id), name=f"MCP test {uuid4().hex[:8]}", pan="TESTONLY01")
        session.add(company)
        await session.flush()
        company_id = str(company.id)

    payload = {
        "name": name,
        "agent_type": "support_triage",
        "domain": "ops",
        "company_id": company_id,
        "connector_ids": [name],
        "authorized_tools": [ref, write_ref],
        "system_prompt": "support_triage",
    }
    saved = await client.post("/api/v1/agents", headers=auth_headers, json=payload)
    assert saved.status_code in (200, 201), saved.text
    aid = saved.json()["agent_id"]
    reload = await client.get(f"/api/v1/agents/{aid}", headers=auth_headers)
    assert reload.status_code == 200
    assert reload.json()["authorized_tools"] == [ref, write_ref]
    changed = await client.patch(f"/api/v1/agents/{aid}", headers=auth_headers, json={"authorized_tools": [ref]})
    assert changed.status_code == 200, changed.text
    mismatch = await client.patch(
        f"/api/v1/agents/{aid}", headers=auth_headers, json={"authorized_tools": ["mcp_other__gnani_transcribe"]}
    )
    assert mismatch.status_code == 422, mismatch.text
    invalid_replace = await client.put(
        f"/api/v1/agents/{aid}",
        headers=auth_headers,
        json={**payload, "authorized_tools": ["mcp_other__gnani_transcribe"]},
    )
    assert invalid_replace.status_code == 422, invalid_replace.text

    config, names = await _resolve_connector_configs(tenant_id, [name], {}, company_id=company_id)
    assert TOKEN not in json.dumps(config)
    assert name in config[CATALOG_KEY]
    graph_tools = build_tools_for_agent(
        [ref], config, names, tenant_id=tenant_id, company_id=company_id, domain="ops", agent_id=aid
    )
    response = await graph_tools[0].ainvoke({"text": "graph"})
    assert "error" not in response, response
    assert json.loads(response["content"][0]["text"])["text"] == "graph"
    monkeypatch.setattr(grantex_auth, "get_grantex_client", lambda: remote_grant_client.client)
    runtime_grant = RunGrant(
        mode=EnforcementMode.DENY,
        token=remote_grant_client.signed(f"tool:{name}:write:gnani_transcribe"),
        source="supplied",
    )
    runtime = await execute_agent_tool(
        name,
        "gnani_transcribe",
        {"text": "runtime"},
        tenant_id=tenant_id,
        company_id=company_id,
        domain="ops",
        authorized_tools=[ref],
        agent_id=aid,
        run_grant=runtime_grant,
    )
    assert "error" not in runtime, runtime
    assert mcp_server.calls == ["first", "graph", "runtime"]
    denied_grant = await execute_agent_tool(
        name,
        "gnani_transcribe",
        {"text": "must not dispatch"},
        tenant_id=tenant_id,
        company_id=company_id,
        domain="ops",
        authorized_tools=[ref],
        agent_id=aid,
        run_grant=RunGrant(mode=EnforcementMode.DENY, token=remote_grant_client.signed(), source="supplied"),
    )
    assert denied_grant["error"]["code"] == "E1007"
    assert len(mcp_server.calls) == 3
    from core.tool_gateway.gateway import ToolGateway

    gateway = ToolGateway()
    gateway_result = await gateway.execute(
        tenant_id, aid, [], name, "gnani_transcribe", {"text": "gateway"},
        company_id=company_id, domain="ops", run_grant=runtime_grant,
    )
    assert "error" not in gateway_result, gateway_result
    assert json.loads(gateway_result["content"][0]["text"])["text"] == "gateway"
    denied_gateway = await gateway.execute(
        tenant_id, aid, [], name, "gnani_transcribe", {"text": "must not dispatch"},
        company_id=company_id, domain="ops",
        run_grant=RunGrant(mode=EnforcementMode.DENY, token=remote_grant_client.signed(), source="supplied"),
    )
    assert denied_gateway["error"]["code"] == "E1007"
    assert len(mcp_server.calls) == 4
    await client.patch(f"/api/v1/agents/{aid}", headers=auth_headers, json={"authorized_tools": [ref, write_ref]})
    blocked = await execute_agent_tool(
        name,
        "gnani_voice_reply",
        {"text": "do not send"},
        tenant_id=tenant_id,
        company_id=company_id,
        domain="ops",
        authorized_tools=[write_ref],
        agent_id=aid,
        run_grant=NO_RUN_GRANT_FOR_TESTS,
    )
    assert blocked["error"] == "action_contained"
    assert len(mcp_server.calls) == 4
    # Simulates another worker: calls re-read persisted links rather than process caches.
    async with get_tenant_session(UUID(tenant_id)) as session:
        agent = await session.get(Agent, UUID(aid))
        agent.connector_ids = []
    denied = await graph_tools[0].ainvoke({"text": "must not call"})
    assert denied["error"] == "remote_mcp_unavailable"
    assert len(mcp_server.calls) == 4
    archived = await client.delete(f"/api/v1/connectors/{cid}", headers=auth_headers)
    assert archived.status_code in (200, 204)
    assert not any(item["id"] == cid for item in (await client.get(path, headers=auth_headers)).json()["items"])
