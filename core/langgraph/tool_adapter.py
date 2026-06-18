"""Wrap existing BaseConnector tools as LangChain tools.

Each connector tool becomes a LangChain @tool function that:
1. Validates the agent's Grantex grant has the required scope
2. Debits the Grantex budget for payment operations
3. Executes the tool via the existing connector framework
4. Logs to the Grantex audit trail
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
import structlog
from langchain_core.tools import StructuredTool

from connectors.framework.base_connector import BaseConnector
from connectors.registry import ConnectorRegistry

logger = structlog.get_logger()

# Cache connector instances to avoid re-creating on every tool call.
#
# RU-May01-BUG-01: the cache holds long-lived ``httpx.AsyncClient``
# instances. After hours of idle, the underlying TCP connection is
# closed by the remote (keep-alive timeout / network reset) but the
# client object is still in this dict. The next tool call hits the
# stale client and raises ``httpx.LocalProtocolError`` —
# ``Illegal header value`` / ``Server disconnected without sending a
# response``. The fix below evicts on transport errors and retries
# once with a fresh instance.
_CONNECTOR_CACHE_MAX_SIZE = 128
# enterprise-gate: process-local-ok reason=bounded-local-connector-client-cache
_connector_cache: dict[str, BaseConnector] = {}

# Transport-level errors that indicate the cached client is no longer
# usable. NOT 4xx/5xx — those are real server responses and must
# surface to the caller. These are kept narrow on purpose: anything
# wider (e.g. catching every httpx exception) would mask real
# upstream-service errors as "stale cache".
_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    httpx.LocalProtocolError,
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.PoolTimeout,
)

_SECRETISH_RE = re.compile(
    r"(?i)\b(authorization|bearer|api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token)"
    r"\b\s*[:=]\s*['\"]?([A-Za-z0-9._\-]{8,})"
)


def _sanitize_error_text(value: Any) -> str:
    text = str(value or "").strip()
    text = _SECRETISH_RE.sub(r"\1=[redacted]", text)
    return text[:300]


def _safe_response_json(response: httpx.Response | None) -> Any:
    if response is None:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _provider_error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("message", "error_description", "detail", "reason"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _sanitize_error_text(value)
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            return _sanitize_error_text(error)
        if isinstance(error, dict):
            nested = _provider_error_message(error)
            if nested:
                return nested
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            for item in errors:
                nested = _provider_error_message(item)
                if nested:
                    return nested
        code = payload.get("code")
        if code not in (None, ""):
            return _sanitize_error_text(code)
    if isinstance(payload, list):
        for item in payload:
            nested = _provider_error_message(item)
            if nested:
                return nested
    return ""


def _classify_http_error(status_code: int, provider_message: str) -> str:
    lowered = provider_message.lower()
    if status_code == 401:
        if "expired" in lowered:
            return "expired_token"
        if "invalid" in lowered or "malformed" in lowered:
            return "invalid_access_token"
        return "authentication_failed"
    if status_code == 403:
        return "missing_permissions"
    if status_code == 400:
        if "payload" in lowered or "validation" in lowered or "required" in lowered:
            return "invalid_payload"
        return "api_validation_failed"
    if status_code == 404:
        return "invalid_endpoint_or_resource"
    if status_code == 429:
        return "rate_limited"
    if 500 <= status_code:
        return "upstream_server_error"
    return "upstream_http_error"


def _connector_exception_payload(
    exc: BaseException,
    *,
    connector_name: str,
    tool_name: str,
) -> dict[str, Any]:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        status_code = response.status_code
        payload = _safe_response_json(response)
        provider_message = _provider_error_message(payload)
        if not provider_message:
            provider_message = _sanitize_error_text(response.reason_phrase)
        code = _classify_http_error(status_code, provider_message)
        return {
            "error": code,
            "message": (
                f"Upstream {connector_name} API returned HTTP {status_code}"
                + (f": {provider_message}" if provider_message else ".")
            ),
            "http_status": status_code,
            "connector": connector_name,
            "tool": tool_name,
            "error_class": type(exc).__name__,
        }
    if isinstance(exc, httpx.TimeoutException):
        return {
            "error": "upstream_timeout",
            "message": f"{connector_name}.{tool_name} timed out while calling the upstream API.",
            "connector": connector_name,
            "tool": tool_name,
            "error_class": type(exc).__name__,
        }
    if isinstance(exc, httpx.RequestError):
        return {
            "error": "upstream_connection_error",
            "message": (
                f"{connector_name}.{tool_name} could not reach the upstream API "
                f"({type(exc).__name__})."
            ),
            "connector": connector_name,
            "tool": tool_name,
            "error_class": type(exc).__name__,
        }
    detail = _sanitize_error_text(exc)
    return {
        "error": "connector_tool_execution_failed",
        "message": (
            f"{connector_name}.{tool_name} failed: {type(exc).__name__}"
            + (f": {detail}" if detail else "")
        ),
        "connector": connector_name,
        "tool": tool_name,
        "error_class": type(exc).__name__,
    }


def _canonical_connector_name(connector_name: str) -> str:
    return connector_name.removeprefix("registry-").strip().lower()


def _split_connector_tool_ref(tool_ref: str) -> tuple[str | None, str]:
    """Return (connector, tool) for connector-qualified tool references.

    CA pack tools intentionally use ``connector:tool`` because bare tool
    names like ``get_trial_balance`` exist on both Tally and Zoho Books.
    ``tool:connector:execute:resource`` is a Grantex scope, not an
    authorized-tool reference, so it is left untouched.
    """
    raw = str(tool_ref or "").strip()
    if raw.startswith("tool:"):
        return None, raw
    if ":" not in raw:
        return None, raw
    connector_name, tool_name = raw.split(":", 1)
    connector_name = _canonical_connector_name(connector_name)
    tool_name = tool_name.strip()
    if not connector_name or not tool_name or ":" in tool_name:
        return None, raw
    return connector_name, tool_name


def _llm_safe_tool_name(connector_name: str | None, tool_name: str) -> str:
    """Map connector-qualified names to provider-safe function names."""
    if not connector_name:
        return tool_name
    return f"{connector_name}__{tool_name}"[:64]


def _actual_tool_name(tool_ref: str) -> str:
    connector_name, tool_name = _split_connector_tool_ref(tool_ref)
    if connector_name:
        return tool_name
    raw = str(tool_ref or "").strip()
    if "__" in raw:
        maybe_connector, maybe_tool = raw.split("__", 1)
        if ConnectorRegistry.get(_canonical_connector_name(maybe_connector)):
            return maybe_tool
    return raw


def _store_connector_cache(cache_key: str, instance: BaseConnector) -> None:
    if cache_key not in _connector_cache and len(_connector_cache) >= _CONNECTOR_CACHE_MAX_SIZE:
        oldest_key = next(iter(_connector_cache))
        _connector_cache.pop(oldest_key, None)
    _connector_cache[cache_key] = instance


def _flatten_structured_tool_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Unwrap LangChain's ``**kwargs`` schema artifact for var-kw tools.

    Connector methods are registered as ``method(**params)``. LangChain's
    schema inference can expose that signature as one argument named
    ``kwargs`` and then call the wrapper with ``{"kwargs": {...}}``.
    Passing that through unchanged sends a bogus ``kwargs`` query param to
    upstream APIs. Flatten the single-field wrapper at the LangGraph
    boundary so connectors receive the params they declared.
    """
    nested = kwargs.get("kwargs")
    if len(kwargs) == 1 and isinstance(nested, dict):
        return dict(nested)
    return kwargs


async def _build_connector(
    connector_cls: type[BaseConnector],
    config: dict[str, Any] | None,
    connector_name: str,
) -> BaseConnector | None:
    """Instantiate + connect a fresh connector. Returns None on failure
    so callers can map to a stable error shape."""
    instance = connector_cls(config or {})
    try:
        await instance.connect()
    # enterprise-gate: broad-except-ok reason=connector-connect-boundary-returns-explicit-error
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "connector_connect_failed", connector=connector_name, error=str(exc)
        )
        return None
    return instance


async def _execute_connector_tool(
    connector_name: str,
    tool_name: str,
    params: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a connector tool and return the result.

    On transport errors (stale cached client), evict the cache entry,
    rebuild the connector once, and retry. Closes RU-May01-BUG-01
    where every cached connector failed silently after the server
    had been running long enough for the upstream HTTP keep-alive to
    expire.
    """
    connector_cls = ConnectorRegistry.get(connector_name)
    if not connector_cls:
        return {"error": f"Connector '{connector_name}' not found in registry"}

    config_fingerprint = json.dumps(config or {}, sort_keys=True)
    cache_key = f"{connector_name}:{config_fingerprint}"
    if cache_key not in _connector_cache:
        instance = await _build_connector(connector_cls, config, connector_name)
        if instance is None:
            return {"error": f"Failed to connect to {connector_name}"}
        _store_connector_cache(cache_key, instance)

    instance = _connector_cache[cache_key]
    start = time.monotonic()
    try:
        result = await instance.execute_tool(tool_name, params)
        latency = int((time.monotonic() - start) * 1000)
        logger.info(
            "tool_executed",
            connector=connector_name,
            tool=tool_name,
            latency_ms=latency,
            status="success",
        )
        return result
    except httpx.HTTPStatusError as exc:
        latency = int((time.monotonic() - start) * 1000)
        payload = _connector_exception_payload(
            exc,
            connector_name=connector_name,
            tool_name=tool_name,
        )
        logger.error(
            "tool_execution_http_failed",
            connector=connector_name,
            tool=tool_name,
            latency_ms=latency,
            status_code=payload.get("http_status"),
            error=payload.get("error"),
        )
        return payload
    except _TRANSPORT_ERRORS as transport_exc:
        # RU-May01-BUG-01: cached client is dead. Evict, rebuild, retry
        # once. If the retry still fails, return a clear error rather
        # than the generic "Tool execution failed: <type>" — operators
        # need to know the failure shape (transport vs upstream API).
        logger.warning(
            "connector_transport_error_reconnecting",
            connector=connector_name,
            tool=tool_name,
            error=str(transport_exc),
            error_type=type(transport_exc).__name__,
        )
        _connector_cache.pop(cache_key, None)

        fresh = await _build_connector(connector_cls, config, connector_name)
        if fresh is None:
            return {
                "error": (
                    f"Tool execution failed: {type(transport_exc).__name__} "
                    "and reconnect attempt also failed. The upstream service "
                    "may be unreachable."
                ),
                "error_class": "transport_reconnect_failed",
            }
        _store_connector_cache(cache_key, fresh)

        try:
            result = await fresh.execute_tool(tool_name, params)
            latency = int((time.monotonic() - start) * 1000)
            logger.info(
                "tool_executed_after_reconnect",
                connector=connector_name,
                tool=tool_name,
                latency_ms=latency,
                status="success",
            )
            return result
        # enterprise-gate: broad-except-ok reason=connector-retry-boundary-returns-explicit-error
        except Exception as retry_exc:  # noqa: BLE001
            payload = _connector_exception_payload(
                retry_exc,
                connector_name=connector_name,
                tool_name=tool_name,
            )
            logger.error(
                "tool_execution_failed_after_reconnect",
                connector=connector_name,
                tool=tool_name,
                error=payload.get("error"),
                error_type=type(retry_exc).__name__,
            )
            payload.setdefault("error_class", "retry_failed")
            return payload
    # enterprise-gate: broad-except-ok reason=connector-tool-boundary-returns-explicit-error
    except Exception as e:
        latency = int((time.monotonic() - start) * 1000)
        payload = _connector_exception_payload(
            e,
            connector_name=connector_name,
            tool_name=tool_name,
        )
        logger.error(
            "tool_execution_failed",
            connector=connector_name,
            tool=tool_name,
            latency_ms=latency,
            error=payload.get("error"),
            error_type=type(e).__name__,
        )
        return payload


def build_tools_for_agent(
    authorized_tools: list[str],
    connector_config: dict[str, Any] | None = None,
    connector_names: list[str] | None = None,
) -> list[StructuredTool]:
    """Build LangChain tools from an agent's authorized_tools list.

    Each tool name in authorized_tools (e.g., "fetch_bank_statement",
    "create_payment_intent") is matched to a connector and wrapped as a
    LangChain StructuredTool.

    ``connector_names`` (BUG-08, RU-May01 verification 2026-05-02) is
    the agent runtime's resolved connector allow-list. ``None`` means
    "no caller constraint" (used by tests and a few synthetic call
    sites); ``[]`` is the fail-closed signal — the agent had
    ``connector_ids`` but none resolved to a live ConnectorConfig, so
    the index must be empty rather than fall back to every globally
    registered connector.

    Returns a list of callable LangChain tools ready for LangGraph.
    """
    tools: list[StructuredTool] = []
    seen: set[str] = set()

    # Build a reverse index. Connector-qualified aliases are included so
    # CA pack tools can keep ``zoho_books:get_trial_balance`` and
    # ``tally:get_trial_balance`` separate instead of collapsing into a
    # bare-name collision.
    tool_index = _build_tool_index(
        connector_config,
        connector_names,
        include_connector_aliases=True,
    )

    for tool_ref in authorized_tools:
        if tool_ref in seen:
            continue
        seen.add(tool_ref)

        match = tool_index.get(tool_ref)
        if not match:
            continue

        connector_name, description = match
        connector_hint, parsed_tool_name = _split_connector_tool_ref(tool_ref)
        actual_tool_name = parsed_tool_name if connector_hint else _actual_tool_name(tool_ref)
        public_tool_name = (
            _llm_safe_tool_name(connector_name, actual_tool_name)
            if connector_hint
            else actual_tool_name
        )

        # Create an async wrapper that calls the connector
        def _make_tool_fn(cn: str, tn: str, desc: str):
            async def _tool_fn(**kwargs: Any) -> dict[str, Any]:
                params = _flatten_structured_tool_kwargs(kwargs)
                return await _execute_connector_tool(cn, tn, params, connector_config)
            _tool_fn.__name__ = tn
            _tool_fn.__doc__ = desc or f"Execute {tn} on {cn} connector"
            return _tool_fn

        tool = StructuredTool.from_function(
            coroutine=_make_tool_fn(connector_name, actual_tool_name, description),
            name=public_tool_name,
            description=description
            or f"Execute {actual_tool_name} on {connector_name}",
        )
        tools.append(tool)

    return tools


def _build_tool_index(
    connector_config: dict[str, Any] | None = None,
    connector_names: list[str] | None = None,
    *,
    include_connector_aliases: bool = False,
) -> dict[str, tuple[str, str]]:
    """Build a reverse index: tool_name -> (connector_name, description).

    Scans all registered native connectors and their tool registries,
    then appends Composio tools (with ``composio:`` prefix) from the
    ConnectorRegistry.  Native bare tool names keep their historical
    first-wins behavior; when ``include_connector_aliases`` is true,
    connector-qualified aliases are also indexed so duplicate bare
    tools can be addressed unambiguously.

    UR-Bug-2 (Uday/Ramesh 2026-04-21): when ``connector_names`` is
    provided, the index is restricted to tools registered by those
    connectors. Used by ``GET /tools?connectors=gmail`` so the agent
    creation UI can populate authorized_tools with exactly the
    connectors the user picked, instead of every tool in the product.
    """
    # BUG-08 (RU-May01 verification, 2026-05-02): the agent runtime is
    # the security boundary that decides which connectors an agent may
    # call. ``connector_names=None`` means "caller did not constrain";
    # ``connector_names=[]`` means "caller explicitly resolved zero
    # connectors for this agent". Treating those identically (the prior
    # behaviour) was a fail-OPEN: an FpaAgent with
    # connector_ids=["registry-zoho_books"] but no live Zoho
    # ConnectorConfig fell back to *every* globally-registered native
    # connector, so the LLM cheerfully called ``stripe.list_invoices``
    # with no credentials and got 401-capped. Use ``is not None`` so
    # the explicit-empty case fails closed (empty tool index → no
    # tools → LLM has nothing to invoke).
    allowed: set[str] | None = None
    if connector_names is not None:
        # Normalise — strip any "registry-" UI prefix and lowercase.
        allowed = {
            n.removeprefix("registry-").strip().lower()
            for n in connector_names
            if n
        }

    index: dict[str, tuple[str, str]] = {}

    # 1. Native connectors first
    for connector_name in ConnectorRegistry.all_names():
        # Skip the composio meta-connector; its tools are handled below
        if connector_name == "composio":
            continue
        if allowed is not None and connector_name.lower() not in allowed:
            continue

        connector_cls = ConnectorRegistry.get(connector_name)
        if not connector_cls:
            continue

        # Instantiate just to read the tool registry
        instance = connector_cls.__new__(connector_cls)
        instance.config = connector_config or {}
        instance._tool_registry = {}
        try:
            instance._register_tools()
        # enterprise-gate: broad-except-ok reason=connector-tool-index-skips-broken-registration
        except Exception:  # noqa: S112
            continue  # Skip connectors that fail to register tools

        for tool_name, handler in instance._tool_registry.items():
            doc = (handler.__doc__ or "").strip().split("\n")[0]
            if tool_name not in index:
                index[tool_name] = (connector_name, doc)
            if include_connector_aliases:
                index[f"{connector_name}:{tool_name}"] = (connector_name, doc)
                index[_llm_safe_tool_name(connector_name, tool_name)] = (
                    connector_name,
                    doc,
                )

    # 2. Composio tools (already filtered for native priority in registry)
    if allowed is None or "composio" in allowed:
        for tool_name, meta in ConnectorRegistry.get_composio_tools().items():
            if tool_name not in index:
                index[tool_name] = ("composio", meta.get("description", ""))

    return index
