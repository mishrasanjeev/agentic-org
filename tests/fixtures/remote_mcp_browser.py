# SPDX-License-Identifier: Apache-2.0
"""Local Docker browser fixture. Never a production entrypoint.

Runs the real API/auth/DB and an official SDK server with synthetic tools.
Only outbound MCP DNS routing is replaced to reach the loopback test server.
"""

import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount

if (
    os.environ.get("AGENTICORG_ENV") != "development"
    or urlsplit(os.environ.get("AGENTICORG_DB_URL", "")).hostname != "postgres"
):
    raise RuntimeError("This fixture requires the isolated local Docker development database")

from api.main import app as api_app  # noqa: E402
from core import remote_mcp_transport as transport  # noqa: E402

TOKEN = "synthetic-browser-mcp-token"
server = FastMCP("Synthetic browser MCP")


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def gnani_transcribe(text: str) -> dict:
    """Echo synthetic text to verify the tool path; this does not perform STT."""
    return {"text": text}


@server.tool(annotations=ToolAnnotations(readOnlyHint=False))
def gnani_voice_reply(text: str) -> dict:
    """Synthetic write tool. No telephony or TTS provider is connected."""
    return {"text": text}


class Bearer:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and dict(scope["headers"]).get(b"authorization") != f"Bearer {TOKEN}".encode():
            await JSONResponse({"error": "Unauthorized"}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)


provider = server.streamable_http_app()
provider.add_middleware(Bearer)


async def loopback(request):
    if request.url.host != "tools.example.test":
        raise ValueError("Browser fixture only accepts the synthetic MCP hostname")
    request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=8000, path="/provider/mcp")
    request.headers["host"] = "127.0.0.1:8000"


def client(token):
    return transport._BoundedClient(
        headers={"Authorization": f"Bearer {token}"}, event_hooks={"request": [loopback]}, trust_env=False, timeout=10
    )


transport._client = client


@asynccontextmanager
async def lifespan(app):
    async with server.session_manager.run():
        yield


app = Starlette(routes=[Mount("/provider", app=provider), Mount("/", app=api_app)], lifespan=lifespan)
