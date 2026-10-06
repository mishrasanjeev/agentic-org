# SPDX-License-Identifier: Apache-2.0
"""Bounded MCP Streamable HTTP client using the official SDK."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from core.security.egress import build_pinned_async_transport, validate_public_url

MCP_SCHEMA = "mcp:streamable-http:v1"
MAX_TOOLS = 100
MAX_BYTES = 1_048_576
NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


class RemoteMCPError(ValueError):
    """A stable, secret-free error suitable for the operator."""


def validate_endpoint(url: str) -> None:
    validate_public_url(url, require_dns=False)
    parsed = urlsplit(url)
    if (parsed.hostname or "").endswith(".internal"):
        raise RemoteMCPError("Internal MCP endpoints are not permitted")
    if parsed.query or parsed.fragment:
        raise RemoteMCPError("MCP endpoints must not contain query credentials or fragments")


class _LimitedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream) -> None:
        self.stream = stream

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > MAX_BYTES:
                raise RemoteMCPError("MCP response exceeded the size limit")
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


class _BoundedClient(httpx.AsyncClient):
    async def send(self, request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
        validate_endpoint(str(request.url))
        request.headers["Accept-Encoding"] = "identity"
        streamed = kwargs.get("stream", False)
        kwargs["stream"] = True
        kwargs["follow_redirects"] = False
        response = await super().send(request, *args, **kwargs)
        # Count decoded bytes without exposing the process to decompression bombs.
        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
            await response.aclose()
            raise RemoteMCPError("MCP servers must honor Accept-Encoding: identity")
        response.stream = _LimitedStream(response.stream)
        if not streamed:
            try:
                await response.aread()
            finally:
                await response.aclose()
        return response


def _client(token: str) -> httpx.AsyncClient:
    # MCP 2025-11-25 transports: pinned DNS, verified TLS, no redirects or proxies.
    return _BoundedClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(20.0, connect=5.0),
        transport=build_pinned_async_transport(require_dns=True),
        follow_redirects=False,
        trust_env=False,
    )


@asynccontextmanager
async def _session(url: str, token: str):
    validate_endpoint(url)
    if not token or any(c in token for c in "\r\n"):
        raise RemoteMCPError("A valid MCP bearer token is required")
    try:
        async with asyncio.timeout(30):
            async with _client(token) as client:
                async with streamable_http_client(url, http_client=client) as (read, write, _):
                    async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session:
                        initialized = await session.initialize()
                        if initialized.capabilities.tools is None:
                            raise RemoteMCPError("The MCP server does not advertise tools")
                        yield session
    except RemoteMCPError:
        raise
    # SDK task groups can wrap transport failures. Never return their raw URL/header/body.
    # enterprise-gate: broad-except-ok reason=mcp-sdk-boundary-fails-closed-with-secret-free-error
    except Exception as exc:
        safe = _safe_error(exc)
        if safe is not None:
            raise safe from None
        raise RemoteMCPError(
            "MCP connection failed: check the HTTPS endpoint, bearer token, server availability and protocol support"
        ) from None


def _safe_error(exc: BaseException) -> RemoteMCPError | None:
    if isinstance(exc, RemoteMCPError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            safe = _safe_error(child)
            if safe is not None:
                return safe
    return None


def _check_schema(root: dict) -> None:
    # JSON Schema is remote input. Bound expansions and reject recursive or regex
    # programs rather than evaluating unbounded work on the API event loop.
    count = 0

    def walk(value: Any, ancestors: set[int], depth: int) -> None:
        nonlocal count
        count += 1
        if depth > 20 or count > 2000:
            raise RemoteMCPError("MCP input schema complexity exceeds the limit")
        if not isinstance(value, dict | list):
            return
        if id(value) in ancestors:
            raise RemoteMCPError("Recursive MCP schemas are not supported")
        parents = ancestors | {id(value)}
        if isinstance(value, dict):
            if "$dynamicRef" in value or "$recursiveRef" in value:
                raise RemoteMCPError("Dynamic MCP schema references are not supported")
            if isinstance(value.get("pattern"), str) or "patternProperties" in value:
                raise RemoteMCPError("Remote regex schemas are not supported; use bounded types or enums")
            if isinstance(value.get("$id"), str) and not value["$id"].startswith("#"):
                raise RemoteMCPError("External schema identifiers are not supported")
            if "$ref" in value:
                ref = value["$ref"]
                if not isinstance(ref, str) or (ref != "#" and not ref.startswith("#/")):
                    raise RemoteMCPError("Only local JSON pointer schema references are supported")
                target: Any = root
                try:
                    for segment in unquote(ref[2:]).split("/") if ref != "#" else []:
                        segment = segment.replace("~1", "/").replace("~0", "~")
                        target = target[int(segment)] if isinstance(target, list) else target[segment]
                except (KeyError, IndexError, TypeError, ValueError):
                    raise RemoteMCPError("MCP schema reference cannot be resolved") from None
                walk(target, parents, depth + 1)
            for child in value.values():
                walk(child, parents, depth + 1)
        else:
            for child in value:
                walk(child, parents, depth + 1)

    walk(root, set(), 0)


def normalize_tool(connector: str, tool: dict[str, Any]) -> dict[str, Any]:
    name = tool.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name) or "__" in name or len(f"{connector}__{name}") > 64:
        raise RemoteMCPError("Tool names must be unambiguous and fit the 64-character connector-qualified limit")
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise RemoteMCPError("Every MCP tool needs an object input schema")
    if len(json.dumps(schema)) > 32_768:
        raise RemoteMCPError("MCP input schema exceeds the size limit")
    _check_schema(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise RemoteMCPError("Invalid MCP input schema") from None
    description = str(tool.get("description") or name)[:1024]
    annotations = tool.get("annotations") or {}
    # Server hints describe intent, never grant authority. An owner reviews read access.
    read_hint = isinstance(annotations, dict) and annotations.get("readOnlyHint") is True
    identity = {"name": name, "description": description, "inputSchema": schema, "read_only_hint": read_hint}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**identity, "schema_hash": fingerprint, "permission": "write"}


async def _list(session: ClientSession, connector: str) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursors: set[str] = set()
    cursor = None
    for _ in range(10):
        page = await session.list_tools(cursor=cursor)
        for item in page.tools:
            normalized = normalize_tool(connector, item.model_dump(by_alias=True, exclude_none=True))
            if normalized["name"] in seen or len(tools) >= MAX_TOOLS:
                raise RemoteMCPError("MCP catalog has duplicate tools or exceeds the tool limit")
            seen.add(normalized["name"])
            tools.append(normalized)
        cursor = page.nextCursor
        if not cursor:
            return tools
        if cursor in cursors:
            break
        cursors.add(cursor)
    raise RemoteMCPError("MCP catalog pagination did not terminate")


async def discover(url: str, token: str, connector: str) -> list[dict[str, Any]]:
    async with _session(url, token) as session:
        result = await _list(session, connector)
        if token in json.dumps(result):
            raise RemoteMCPError("MCP catalog contained authentication material and was withheld")
        return result


async def call(url: str, token: str, connector: str, descriptor: dict[str, Any], arguments: dict[str, Any]) -> dict:
    if len(json.dumps(arguments)) > 65_536:
        raise RemoteMCPError("MCP arguments exceed the size limit")
    validator = Draft202012Validator(descriptor["inputSchema"])
    if not validator.is_valid(arguments):
        raise RemoteMCPError("Arguments do not match the discovered tool schema")
    async with _session(url, token) as session:
        live = {tool["name"]: tool for tool in await _list(session, connector)}
        current = live.get(descriptor["name"])
        if current is None or current["schema_hash"] != descriptor["schema_hash"]:
            raise RemoteMCPError("MCP tool changed or disappeared; refresh discovery and review its permissions")
        # One attempt only: a lost response must not duplicate an external side effect.
        result = await session.call_tool(descriptor["name"], arguments)
        if result.isError:
            raise RemoteMCPError("The MCP server reported a tool error; inspect the server using its request log")
        payload = result.model_dump(by_alias=True, exclude_none=True)
        encoded = json.dumps(payload)
        if token in encoded:
            raise RemoteMCPError("MCP response contained authentication material and was withheld")
        if len(encoded) > MAX_BYTES:
            raise RemoteMCPError("MCP tool result exceeded the size limit")
        return payload
