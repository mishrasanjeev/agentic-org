# SPDX-License-Identifier: Apache-2.0
"""Real SDK server/client round trips plus tenant-authority regression checks."""

import asyncio
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse

from core import remote_mcp as service
from core import remote_mcp_transport as transport

TOKEN = "synthetic-mcp-test-token"
URL = "https://tools.example.test/mcp"


@pytest_asyncio.fixture(loop_scope="function")
async def mcp_server(monkeypatch):
    from sse_starlette.sse import AppStatus

    monkeypatch.setattr(AppStatus, "should_exit", False)
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_CONNECTORS", "0")
    calls = []
    auth = SimpleNamespace(token=TOKEN)
    server = FastMCP("Local regression tools", json_response=False)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def gnani_transcribe(text: str) -> dict:
        """Return synthetic text for a protocol-only test, not real speech recognition."""
        calls.append(text)
        return {"text": text}

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
    def gnani_voice_reply(text: str) -> str:
        """Synthetic write tool that must never be dispatched by a read probe."""
        calls.append("WRITE")
        return text

    app = server.streamable_http_app()

    class Bearer:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if (
                scope["type"] == "http"
                and dict(scope["headers"]).get(b"authorization") != f"Bearer {auth.token}".encode()
            ):
                await JSONResponse({"error": "Unauthorized"}, status_code=401)(scope, receive, send)
                return
            await self.app(scope, receive, send)

    app.add_middleware(Bearer)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    process = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    task = asyncio.create_task(process.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not process.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)

        async def local_url(request):
            # Test-only routing: production DNS/HTTPS restrictions remain intact.
            request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=port)
            request.headers["host"] = f"127.0.0.1:{port}"

        def client(token):
            return transport._BoundedClient(
                headers={"Authorization": f"Bearer {token}"},
                event_hooks={"request": [local_url]},
                trust_env=False,
                timeout=5,
            )

        monkeypatch.setattr(transport, "_client", client)
        yield SimpleNamespace(server=server, calls=calls, auth=auth)
    finally:
        process.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


@pytest.mark.asyncio
async def test_real_initialize_discovery_schema_and_call(mcp_server):
    tools = await transport.discover(URL, TOKEN, "mcp_voice")
    assert {t["name"] for t in tools} == {"gnani_transcribe", "gnani_voice_reply"}
    assert all(t["permission"] == "write" for t in tools)
    reviewed = service.review_tools(tools, ["gnani_transcribe"])
    read = next(t for t in reviewed if t["name"] == "gnani_transcribe")
    assert read["permission"] == "read"
    result = await transport.call(URL, TOKEN, "mcp_voice", read, {"text": "synthetic sample"})
    assert json.loads(result["content"][0]["text"]) == {"text": "synthetic sample"}
    assert mcp_server.calls == ["synthetic sample"]
    with pytest.raises(transport.RemoteMCPError, match="Arguments"):
        await transport.call(URL, TOKEN, "mcp_voice", read, {"wrong": 1})
    assert mcp_server.calls == ["synthetic sample"]


@pytest.mark.asyncio
async def test_bad_auth_and_changed_tool_never_dispatch(mcp_server):
    with pytest.raises(transport.RemoteMCPError, match="connection failed"):
        await transport.discover(URL, "invalid-token", "mcp_voice")
    tools = await transport.discover(URL, TOKEN, "mcp_voice")
    read = next(t for t in tools if t["name"] == "gnani_transcribe")
    mcp_server.server.remove_tool("gnani_transcribe")
    with pytest.raises(transport.RemoteMCPError, match="changed or disappeared"):
        await transport.call(URL, TOKEN, "mcp_voice", read, {"text": "do not dispatch"})
    assert not mcp_server.calls


@pytest.mark.asyncio
async def test_response_credential_material_is_withheld(mcp_server):
    read = (await transport.discover(URL, TOKEN, "mcp_voice"))[0]
    with pytest.raises(transport.RemoteMCPError, match="authentication material"):
        await transport.call(URL, TOKEN, "mcp_voice", read, {"text": TOKEN})


@pytest.mark.parametrize(
    "url",
    [
        "http://tools.example.test/mcp",
        "https://127.0.0.1/mcp",
        "https://localhost/mcp",
        "https://user:password@tools.example.test/mcp",
        "https://tools.example.test/mcp?token=private",
        "https://tools.example.test/mcp#fragment",
        "https://metadata.google.internal/mcp",
    ],
)
def test_unsafe_endpoints_rejected(url):
    with pytest.raises(ValueError):
        transport.validate_endpoint(url)


def test_catalog_rejects_ambiguous_names_external_schemas_and_write_hint():
    with pytest.raises(transport.RemoteMCPError):
        transport.normalize_tool("mcp_voice", {"name": "other__send", "inputSchema": {"type": "object"}})
    with pytest.raises(transport.RemoteMCPError):
        transport.normalize_tool("mcp_voice", {"name": "read", "inputSchema": {"type": "object", "$ref": URL}})
    tool = transport.normalize_tool(
        "mcp_voice",
        {
            "name": "gnani_voice_reply",
            "inputSchema": {"type": "object"},
            "annotations": {"readOnlyHint": True},
        },
    )
    with pytest.raises(transport.RemoteMCPError):
        service.review_tools([tool], ["gnani_voice_reply"])


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "$ref": "#"},
        {"type": "object", "properties": {"text": {"type": "string", "pattern": "(a+)+$"}}},
        {"type": "object", "$ref": "#/$defs/missing"},
        {"type": "object", "$dynamicRef": "#node"},
    ],
)
def test_schema_programs_fail_closed(schema):
    with pytest.raises(transport.RemoteMCPError):
        transport.normalize_tool("mcp_voice", {"name": "read", "inputSchema": schema})


def test_bounded_local_definition_supported():
    schema = {"type": "object", "$defs": {"text": {"type": "string"}}, "properties": {"text": {"$ref": "#/$defs/text"}}}
    assert transport.normalize_tool("mcp_voice", {"name": "read", "inputSchema": schema})["inputSchema"] == schema


@pytest.mark.asyncio
async def test_http_redirect_and_size_limits(monkeypatch):
    requests = []

    async def respond(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://other.example.test/mcp"})

    async with transport._BoundedClient(transport=httpx.MockTransport(respond)) as client:
        response = await client.get(URL, follow_redirects=True)
        assert response.status_code == 302
        assert requests == [URL]

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 101

    monkeypatch.setattr(transport, "MAX_BYTES", 100)
    async with transport._BoundedClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream()))
    ) as client:
        with pytest.raises(transport.RemoteMCPError, match="size limit"):
            await client.get(URL)


@pytest.mark.asyncio
async def test_compressed_responses_refused_before_decompression():
    class Compressed(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            raise AssertionError("Compressed bytes must not reach the decoder")
            yield b""  # pragma: no cover - async iterator shape

        async def aclose(self):
            self.closed = True

    stream = Compressed()

    async def respond(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=stream)

    async with transport._BoundedClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(transport.RemoteMCPError, match="Accept-Encoding"):
            await client.get(URL)
    assert stream.closed


@pytest.mark.asyncio
async def test_page_cursor_cycle_and_duplicate_refused():
    from mcp.types import ListToolsResult, Tool

    tool = Tool(name="read", inputSchema={"type": "object"})
    session = SimpleNamespace(list_tools=AsyncMock(return_value=ListToolsResult(tools=[tool], nextCursor="repeat")))
    with pytest.raises(transport.RemoteMCPError):
        await transport._list(session, "mcp_voice")
    assert session.list_tools.await_count == 2


@pytest.mark.asyncio
async def test_runtime_rechecks_persisted_authority_and_schema(monkeypatch):
    descriptor = {"name": "gnani_transcribe", "schema_hash": "new", "permission": "read"}
    agent = SimpleNamespace(company_id="company", domain="ops")
    authority = AsyncMock(return_value=(SimpleNamespace(), descriptor, agent))
    monkeypatch.setattr(service, "authorized_descriptor", authority)
    outbound = AsyncMock()
    monkeypatch.setattr(service, "call", outbound)
    result = await service.execute("tenant", "agent", "mcp_voice", "gnani_transcribe", {}, expected_hash="old")
    assert result["error"] == "remote_mcp_unavailable"
    authority.assert_awaited_once()
    outbound.assert_not_awaited()
    authority.side_effect = transport.RemoteMCPError("connector unlinked")
    assert "error" in await service.execute("tenant", "agent", "mcp_voice", "gnani_transcribe", {})
    outbound.assert_not_awaited()


def test_remote_tools_are_qualified_selected_and_not_global():
    from core.langgraph.tool_adapter import _build_tool_index, build_tools_for_agent

    descriptor = transport.normalize_tool(
        "mcp_voice",
        {
            "name": "gnani_transcribe",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
        },
    )
    config = {service.CATALOG_KEY: {"mcp_voice": [descriptor]}}
    assert "gnani_transcribe" not in _build_tool_index(config, ["mcp_voice"])
    assert "mcp_voice__gnani_transcribe" in _build_tool_index(config, ["mcp_voice"])
    assert "mcp_voice__gnani_transcribe" not in _build_tool_index(config, ["gmail"])
    assert "mcp_voice__gnani_transcribe" not in _build_tool_index()
    tools = build_tools_for_agent(
        ["mcp_voice__gnani_transcribe"], connector_config=config, connector_names=["mcp_voice"]
    )
    assert [t.name for t in tools] == ["mcp_voice__gnani_transcribe"]
    assert tools[0].args_schema == descriptor["inputSchema"]


def test_remote_scopes_agree_in_registration_and_runtime():
    from auth.grantex_registration import _tools_to_scopes as registration
    from core.langgraph.grantex_auth import _tools_to_scopes as runtime

    refs = ["mcp_voice__gnani_transcribe", "mcp_voice__gnani_voice_reply"]
    expected = ["tool:mcp_voice:write:gnani_transcribe", "tool:mcp_voice:write:gnani_voice_reply"]
    assert runtime(refs) == expected
    assert all(scope in registration(refs, "ops", connector_names=["mcp_voice"]) for scope in expected)


@pytest.fixture
def remote_grant_client(monkeypatch):
    import time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from grantex import Grantex, _verify

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public.update(kid="local-test", alg="RS256")
    monkeypatch.setattr(
        _verify, "_get_jwks", lambda *args, **kwargs: SimpleNamespace(keys=[public], fetched_at=time.monotonic())
    )
    revocations = []

    def revocation(self, mode, *args):
        revocations.append(mode)
        return None

    monkeypatch.setattr(Grantex, "_revocation_denial", revocation)
    from core.config import settings

    monkeypatch.setattr(settings, "grantex_audience", "agenticorg")
    client = Grantex(api_key="synthetic-local-key", base_url="https://issuer.example.test")

    def signed(scope="tool:mcp_voice:write:gnani_transcribe", *, expired=False, key=private):
        return jwt.encode(
            {
                "iss": "https://issuer.example.test",
                "aud": "agenticorg",
                "sub": "test-principal",
                "jti": "local-id",
                "iat": int(time.time()) - 400,
                "exp": int(time.time()) + (-300 if expired else 300),
                "scope": scope,
                _verify.GRANT_CLAIM: {
                    "agent_did": "did:example:local-agent",
                    "developer_id": "local-tenant",
                    "grant_id": "local-grant",
                },
            },
            key,
            algorithm="RS256",
            headers={"kid": "local-test", "typ": "at+jwt"},
        )

    try:
        yield SimpleNamespace(client=client, signed=signed, revocations=revocations)
    finally:
        client.close()


def test_remote_manifest_preserves_real_signature_scopes_expiry_and_revocation(remote_grant_client, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from grantex import Grantex

    from auth.grant_enforcement import enforce_connector_grant

    client = remote_grant_client.client
    signed = remote_grant_client.signed

    def enforce(token):
        return enforce_connector_grant(client, connector="mcp_voice", tool="gnani_transcribe", grant_token=token)

    assert enforce(signed()).allowed
    assert remote_grant_client.revocations == ["online"]
    assert "mcp_voice" not in client._manifests
    assert not enforce_connector_grant(
        client, connector="mcp_voice", tool="gnani_transcribe", grant_token=signed(), audience="another-app"
    ).allowed
    assert not enforce(signed("tool:mcp_voice:write:gnani_voice_reply")).allowed
    assert not enforce(signed("tool:mcp_other:write:gnani_transcribe")).allowed
    assert not enforce(signed("tool:mcp_voice:read:gnani_transcribe")).allowed
    assert not enforce(signed("tool:mcp_voice:read:gnani_transcribe tool:mcp_voice:write:gnani_voice_reply")).allowed
    assert not enforce(signed(expired=True)).allowed
    different_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert not enforce(signed(key=different_key)).allowed
    monkeypatch.setattr(Grantex, "_revocation_denial", lambda *args: ("Revoked", "revoked"))
    assert not enforce(signed()).allowed
