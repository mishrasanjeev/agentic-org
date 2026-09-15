"""Wrap existing BaseConnector tools as LangChain tools.

Each connector tool becomes a LangChain @tool function that:
1. Validates the agent's Grantex grant has the required scope
2. Debits the Grantex budget for payment operations
3. Executes the tool via the existing connector framework
4. Logs to the Grantex audit trail
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, get_type_hints

import httpx
import structlog
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, TypeAdapter, create_model

from auth.grant_enforcement import EnforcementMode, GrantCallContext
from auth.run_grants import RunGrant, check_run_grant
from connectors.framework.base_connector import BaseConnector
from connectors.registry import ConnectorRegistry
from core.config import is_strict_runtime_env, settings
from core.governance.action_policy import (
    ActionContext,
    ActionDomain,
    CapabilityAuthorization,
    database_feature_flag_resolver,
    evaluate_action,
)
from core.pii.pseudonymiser import PseudonymisationError, PseudonymSession, refusal

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
            "message": (f"{connector_name}.{tool_name} could not reach the upstream API ({type(exc).__name__})."),
            "connector": connector_name,
            "tool": tool_name,
            "error_class": type(exc).__name__,
        }
    detail = _sanitize_error_text(exc)
    return {
        "error": "connector_tool_execution_failed",
        "message": (f"{connector_name}.{tool_name} failed: {type(exc).__name__}" + (f": {detail}" if detail else "")),
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

    Bug sheet #14 (2026-09-14): ``connector.tool`` is the spelling the
    agent-creation UI and several packs persist, so it is accepted as an
    equivalent of ``connector:tool``. No registered connector or tool name
    contains a ``.`` (verified against the live registry), so the split is
    unambiguous.
    """
    raw = str(tool_ref or "").strip()
    if raw.startswith("tool:"):
        return None, raw
    qualified = raw.replace(".", ":", 1) if ":" not in raw and "." in raw else raw
    if ":" not in qualified:
        return None, raw
    connector_name, tool_name = qualified.split(":", 1)
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
    if isinstance(nested, dict):
        # Bug sheet #15 (2026-09-14): with a real args_schema LangChain adds
        # the handler defaults next to a legacy ``kwargs`` wrapper, so the
        # single-key check above no longer matches. The wrapped values are
        # what the model actually sent, so they win over those defaults.
        return {**{k: v for k, v in kwargs.items() if k != "kwargs"}, **nested}
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
        logger.warning("connector_connect_failed", connector=connector_name, error=str(exc))
        return None
    return instance


async def _execute_connector_tool(
    connector_name: str,
    tool_name: str,
    params: dict[str, Any],
    config: dict[str, Any] | None = None,
    *,
    tenant_id: str | None = None,
    company_id: str | None = None,
    domain: ActionDomain | str | None = None,
    capability_authorization: CapabilityAuthorization | None = None,
) -> dict[str, Any]:
    """Execute a connector tool and return the result.

    On transport errors (stale cached client), evict the cache entry,
    rebuild the connector once, and retry. Closes RU-May01-BUG-01
    where every cached connector failed silently after the server
    had been running long enough for the upstream HTTP keep-alive to
    expire.
    """
    # This is the connector dispatch boundary used by LangGraph and workflow
    # connector steps. Evaluate policy before registry lookup, cache access,
    # connection setup, retry, or any provider side effect.
    if is_strict_runtime_env(settings.env) or tenant_id is not None or company_id is not None or domain is not None:
        decision = await evaluate_action(
            f"{connector_name}:{tool_name}",
            context=ActionContext(
                tenant_id=tenant_id,
                company_id=company_id,
                domain=domain,
                runtime_env=settings.env,
            ),
            capability_authorization=capability_authorization,
            feature_flags=database_feature_flag_resolver,
        )
        if not decision.dispatch_allowed:
            governance = decision.to_dict()
            logger.warning(
                "connector_action_contained",
                connector=connector_name,
                tool=tool_name,
                reason=decision.reason,
                governance=governance,
            )
            return {
                "error": "action_contained",
                "message": f"Connector action contained: {decision.reason}",
                "governance": governance,
            }

    connector_cls = ConnectorRegistry.get(connector_name)
    if not connector_cls:
        return {"error": f"Connector '{connector_name}' not found in registry"}

    config_fingerprint = json.dumps(config or {}, sort_keys=True)
    cache_key = (
        f"{tenant_id or '_global'}:{company_id or '_global'}:"
        f"{connector_name}:{config_fingerprint}"
    )
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


def _authorized_tool_refs(authorized_tools: list[str]) -> set[tuple[str | None, str]]:
    """Normalise ``authorized_tools`` entries to ``(connector | None, tool)`` pairs.

    Accepts every spelling the product uses: bare ``list_contacts``,
    ``hubspot:list_contacts`` / ``hubspot.list_contacts`` /
    ``hubspot__list_contacts`` and Grantex scopes
    ``tool:hubspot:<perm>:list_contacts``.
    """
    refs: set[tuple[str | None, str]] = set()
    for raw in authorized_tools or []:
        parsed = _parse_authorized_tool_ref(raw)
        if parsed is not None:
            refs.add(parsed)
    return refs


def _parse_authorized_tool_ref(raw: Any) -> tuple[str | None, str] | None:
    """Normalise one ``authorized_tools`` entry to ``(connector | None, tool)``.

    Bug sheet #14 (2026-09-14): this is the single normaliser for every
    spelling the product persists — bare ``send_email``,
    ``gmail:send_email`` / ``gmail.send_email`` / ``gmail__send_email`` and
    Grantex scopes ``tool:gmail:<perm>:send_email`` — so ``is_tool_authorized``
    and ``build_tools_for_agent`` can never disagree about what a ref means.
    Returns ``None`` for empty refs and malformed Grantex scopes.
    """
    ref = str(raw or "").strip()
    if not ref:
        return None
    if ref.startswith("tool:"):
        parts = ref.split(":")
        if len(parts) >= 4 and parts[1] and parts[3]:
            return _canonical_connector_name(parts[1]), parts[3]
        return None
    connector_hint, tool_name = _split_connector_tool_ref(ref)
    if connector_hint:
        return connector_hint, tool_name
    if "__" in ref:
        maybe_connector, maybe_tool = ref.split("__", 1)
        if maybe_tool and ConnectorRegistry.get(_canonical_connector_name(maybe_connector)):
            return _canonical_connector_name(maybe_connector), maybe_tool
    return None, ref


def _connector_tool_handlers(
    connector_name: str,
    connector_config: dict[str, Any] | None,
) -> dict[str, Callable[..., Any]]:
    """Return ``tool_name -> bound handler`` for a registered connector.

    Same ``__new__`` + ``_register_tools`` trick ``_build_tool_index`` uses:
    no ``connect()``/network, only the registry. ``{}`` when the connector
    is unknown or its registration raises, so callers fall back to an
    open schema instead of failing the whole tool build.
    """
    if connector_name == "composio":
        # The Composio meta-connector discovers tools over the network in
        # ``_register_tools``; its tools keep the open schema.
        return {}
    connector_cls = ConnectorRegistry.get(connector_name)
    if not connector_cls:
        return {}
    instance = connector_cls.__new__(connector_cls)
    instance.config = connector_config or {}
    instance._tool_registry = {}
    try:
        instance._register_tools()
    # enterprise-gate: broad-except-ok reason=connector-tool-schema-derivation-falls-back-to-open-schema
    except Exception:  # noqa: BLE001
        return {}
    return dict(instance._tool_registry)


def _handler_param_names(handler: Callable[..., Any] | None) -> tuple[set[str], bool]:
    """Return ``(keyword-able parameter names, accepts **kwargs)`` for a handler.

    Bound methods already exclude ``self``. An unreadable signature is
    treated as ``**kwargs`` (pass everything through) so a connector that
    wraps its handlers in something ``inspect`` cannot see keeps working.
    """
    if handler is None:
        return set(), True
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return set(), True
    names: set[str] = set()
    var_kw = False
    for param in signature.parameters.values():
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            var_kw = True
        elif param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            names.add(param.name)
    return names, var_kw


_OPEN_TOOL_ARGS_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": True}


def _tool_args_schema(handler: Callable[..., Any] | None, tool_name: str) -> type[BaseModel] | dict[str, Any]:
    """Derive the LLM-facing argument schema from the connector handler.

    Bug sheet #15 (2026-09-14): ``StructuredTool.from_function`` on the
    ``**kwargs`` wrapper exposed one opaque ``kwargs`` object, so the model
    guessed parameter names and top-level keys were silently dropped
    before the connector saw them. Named handler parameters
    (``send_email(to, subject, body, cc, bcc)``) become typed, defaulted
    Pydantic fields (``self`` is already bound away); an annotation that is
    unresolvable or has no JSON schema becomes ``Any`` for that field only.
    Handlers that only accept ``**params`` carry no names in their
    signature, so they get an open object schema (the docstring, which
    lists the params, is the tool description) and every key the model
    sends reaches the handler.

    Extra keys are allowed at the schema so ``build_tools_for_agent`` can
    drop-and-log them against the real signature instead of Pydantic
    discarding them silently.
    """
    names, var_kw = _handler_param_names(handler)
    if not names:
        if var_kw:
            return dict(_OPEN_TOOL_ARGS_SCHEMA)
        return create_model(tool_name, __config__=ConfigDict(extra="allow"))
    fields: dict[str, Any] = {}
    for param in inspect.signature(handler).parameters.values():  # type: ignore[arg-type]
        if param.name not in names:
            continue
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[param.name] = (_schemable_annotation(handler, param.annotation), default)
    try:
        model = create_model(tool_name, __config__=ConfigDict(extra="allow"), **fields)
        model.model_json_schema()  # fail here, not later inside bind_tools
    # enterprise-gate: broad-except-ok reason=unschemable-handler-signature-falls-back-to-open-schema
    except Exception as exc:  # noqa: BLE001
        logger.warning("tool_args_schema_fallback", tool=tool_name, error_type=type(exc).__name__)
        return dict(_OPEN_TOOL_ARGS_SCHEMA)
    return model


def _schemable_annotation(handler: Callable[..., Any] | None, annotation: Any) -> Any:
    """Resolve one parameter annotation, or ``Any`` when it cannot be schema'd.

    Connector modules use ``from __future__ import annotations`` so
    annotations arrive as strings; they are resolved against the handler's
    module globals one parameter at a time so a single unknown type does not
    erase the typing of every other field.
    """
    if annotation is inspect.Parameter.empty:
        return Any
    if isinstance(annotation, str):
        func = getattr(handler, "__func__", handler)
        probe = SimpleNamespace(
            __annotations__={"value": annotation},
            __globals__=getattr(func, "__globals__", {}),
        )
        try:
            annotation = get_type_hints(probe)["value"]
        # enterprise-gate: broad-except-ok reason=unresolvable-annotation-degrades-to-untyped-field
        except Exception:  # noqa: BLE001
            return Any
    try:
        TypeAdapter(annotation).json_schema()
    # enterprise-gate: broad-except-ok reason=non-json-schema-annotation-degrades-to-untyped-field
    except Exception:  # noqa: BLE001
        return Any
    return annotation


def _drop_unknown_handler_params(
    params: dict[str, Any],
    allowed: set[str],
    accepts_var_kw: bool,
    *,
    connector_name: str,
    tool_name: str,
) -> dict[str, Any]:
    """Drop keys the handler cannot accept instead of letting it TypeError.

    Only key names are logged — never values, which may carry live PII.
    """
    if accepts_var_kw:
        return params
    unknown = sorted(k for k in params if k not in allowed)
    if not unknown:
        return params
    logger.warning(
        "tool_args_unknown_keys_dropped",
        connector=connector_name,
        tool=tool_name,
        dropped=unknown,
    )
    return {k: v for k, v in params.items() if k in allowed}


def is_tool_authorized(authorized_tools: list[str], connector_name: str, tool_name: str) -> bool:
    """Return True when ``connector.tool`` is covered by ``authorized_tools``.

    Connector-qualified entries must match both halves. A bare tool name
    matches only when the shared tool index resolves it to this connector,
    so ``list_invoices`` authorized for Zoho never unlocks Stripe.
    """
    connector_name = _canonical_connector_name(connector_name)
    tool_name = str(tool_name or "").strip()
    if not connector_name or not tool_name:
        return False
    refs = _authorized_tool_refs(authorized_tools)
    if (connector_name, tool_name) in refs:
        return True
    if (None, tool_name) not in refs:
        return False
    index = _build_tool_index(connector_names=[connector_name], include_connector_aliases=True)
    match = index.get(tool_name)
    return bool(match and match[0] == connector_name)


async def load_connector_config(
    connector_name: str,
    tenant_id: str | None,
    company_id: str | None,
) -> dict[str, Any] | None:
    """Load the encrypted per-company connector config for a tool call.

    Returns ``None`` when no config row exists so callers fail closed
    instead of constructing a provider with empty credentials.
    """
    if not connector_name or not tenant_id or not company_id:
        return None
    import json as _json
    import uuid as _uuid

    try:
        tenant_uuid = _uuid.UUID(str(tenant_id))
        company_uuid = _uuid.UUID(str(company_id))
    except (TypeError, ValueError):
        return None

    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.connector_config import ConnectorConfig

    async with get_tenant_session(tenant_uuid, company_uuid) as session:
        result = await session.execute(
            select(ConnectorConfig).where(
                ConnectorConfig.tenant_id == tenant_uuid,
                ConnectorConfig.company_id == company_uuid,
                ConnectorConfig.connector_name == connector_name,
            )
        )
        row = result.scalar_one_or_none()
    if row is None:
        return None

    config = dict(row.config or {})
    creds = row.credentials_encrypted or {}
    if isinstance(creds, str):
        creds = _json.loads(creds)
    if isinstance(creds, dict) and "_encrypted" in creds:
        from core.crypto import decrypt_for_tenant

        # KMS-backed decrypt is synchronous (gRPC); keep it off the event loop.
        creds = _json.loads(await asyncio.to_thread(decrypt_for_tenant, creds["_encrypted"]))
    if isinstance(creds, dict):
        config.update(creds)
    return config


async def execute_agent_tool(
    connector_name: str,
    tool_name: str,
    params: dict[str, Any],
    *,
    tenant_id: str | None,
    company_id: str | None,
    domain: ActionDomain | str | None,
    authorized_tools: list[str],
    grant_token: str | None = None,
    capability_authorization: CapabilityAuthorization | None = None,
    run_grant: RunGrant | None = None,
    agent_id: str = "",
    runtime: str = "base_agent",
    agent_type: str = "",
    pseudonymiser: PseudonymSession | None = None,
) -> dict[str, Any]:
    """Governed tool dispatch for ``BaseAgent`` callers without a ToolGateway.

    Same path LangGraph agents take: ``authorized_tools`` membership,
    Grantex ``enforce`` when a grant token is present, tenant/company scoped
    connector config, then ``_execute_connector_tool`` (which applies the
    action policy). Every denial is an explicit ``{"error": ...}`` payload;
    the agent runtime turns those into a failed step.

    With ``pseudonymiser`` the model's pseudonymised arguments are restored
    first; a call whose pseudonyms cannot all be restored is refused.

    PRD F-1: with a ``run_grant`` in ``warn`` or ``deny`` the grant check
    replaces the legacy ``grant_token`` check (``auth/grant_enforcement.py``).
    """
    if pseudonymiser is not None:
        try:
            params = await pseudonymiser.restore_arguments(params)
        except PseudonymisationError as exc:
            await _audit_pseudonym_refusal(tenant_id, connector_name, tool_name, exc.reason)
            return refusal(exc)
    connector_name = _canonical_connector_name(connector_name)
    if not is_tool_authorized(authorized_tools, connector_name, tool_name):
        logger.warning(
            "agent_tool_scope_denied",
            connector=connector_name,
            tool=tool_name,
            reason="not_in_authorized_tools",
        )
        return {
            "error": {
                "code": "E1007",
                "message": f"scope_denied: {connector_name}.{tool_name} is not in the agent's authorized_tools",
            }
        }

    if run_grant is not None and run_grant.mode is not EnforcementMode.OFF:
        amount = params.get("amount")
        check = await check_run_grant(
            run_grant,
            connector=connector_name,
            tool=tool_name,
            amount=amount if isinstance(amount, int | float) and not isinstance(amount, bool) else None,
            context=GrantCallContext(
                tenant_id=str(tenant_id or ""),
                agent_id=agent_id,
                agent_type=agent_type,
                runtime=runtime,
                grant_source=run_grant.source,
            ),
        )
        if not check.dispatch_allowed and check.denial is not None:
            return {
                "error": {
                    "code": "E1007",
                    "message": f"grant_denied: {check.denial.reason.value}",
                    "reason": check.denial.reason.value,
                    "sub_reason": check.denial.sub_reason,
                }
            }
    elif grant_token:
        from core.langgraph.grantex_auth import get_grantex_client

        # ``enforce`` verifies the grant JWT against Grantex's JWKS with a
        # synchronous HTTPS fetch; run it off the event loop.
        enforcement = await asyncio.to_thread(
            get_grantex_client().enforce,
            grant_token=grant_token,
            connector=connector_name,
            tool=tool_name,
            amount=params.get("amount") if isinstance(params.get("amount"), int | float) else None,
        )
        if not enforcement.allowed:
            logger.warning(
                "agent_tool_grant_denied",
                connector=connector_name,
                tool=tool_name,
                reason=enforcement.reason,
            )
            return {"error": {"code": "E1007", "message": f"scope_denied: {enforcement.reason}"}}

    if not company_id and is_strict_runtime_env(settings.env):
        return {
            "error": {
                "code": "E1010",
                "message": "company_scope_required: tool calls need a company-scoped agent in this runtime",
            }
        }

    config = await load_connector_config(connector_name, tenant_id, company_id)
    if config is None and company_id:
        return {
            "error": {
                "code": "E1005",
                "message": f"Connector not configured: {connector_name} has no connector config for this company",
            }
        }

    return await _execute_connector_tool(
        connector_name,
        tool_name,
        params,
        config,
        tenant_id=tenant_id,
        company_id=company_id,
        domain=domain,
        capability_authorization=capability_authorization,
    )


async def _audit_pseudonym_refusal(tenant_id: str | None, connector_name: str, tool_name: str, reason: str) -> None:
    """Audit a tool call refused because its pseudonyms could not be restored, as ``ToolGateway`` does.

    The audit row is written in the tenant's RLS context; a write failure is
    logged by ``AuditLogger`` and never turns the refusal into a dispatch.
    """
    import uuid as _uuid

    from core.database import get_tenant_session
    from core.tool_gateway.audit_logger import AuditLogger

    session_factory = None
    if tenant_id:
        try:
            tid = _uuid.UUID(str(tenant_id))
        except ValueError:
            tid = None
        if tid is not None:

            def session_factory() -> Any:
                return get_tenant_session(tid)

    await AuditLogger(session_factory).log(
        tenant_id=str(tenant_id or ""),
        tool_name=tool_name,
        action="pseudonym_restore_failed",
        outcome="blocked",
        details={"reason": reason, "connector": connector_name},
    )


def _deanonymize_value(value: Any, token_map: dict[str, str]) -> Any:
    """Restore PII tokens in ``value`` recursively (strings inside dicts/lists)."""
    if not token_map:
        return value
    if isinstance(value, str):
        from core.pii.deanonymizer import deanonymize

        return deanonymize(value, token_map)
    if isinstance(value, dict):
        return {k: _deanonymize_value(v, token_map) for k, v in value.items()}
    if isinstance(value, list):
        return [_deanonymize_value(v, token_map) for v in value]
    return value


def _redact_text_with_token_map(text: str, redactor: Any, token_map: dict[str, str]) -> str:
    """Mask PII in ``text`` reusing existing tokens; new findings extend ``token_map``.

    Known original values are swapped back to their existing tokens first so a
    result echoing the input email gets the same ``<EMAIL_ADDRESS_1>`` the LLM
    already knows. Fresh entities get tokens that never collide with the map.
    """
    if not text:
        return text
    for token, original in sorted(token_map.items(), key=lambda item: -len(item[1])):
        if original:
            text = text.replace(original, token)
    redacted, new_tokens = redactor.redact(text)
    if not new_tokens:
        return redacted
    counters: dict[str, int] = {}
    for token in token_map:
        match = re.match(r"^<([A-Z_]+?)_(\d+)>$", token)
        if match:
            counters[match.group(1)] = max(counters.get(match.group(1), 0), int(match.group(2)))
    for token, original in new_tokens.items():
        if token in token_map:
            match = re.match(r"^<([A-Z_]+?)_(\d+)>$", token)
            etype = match.group(1) if match else "PII"
            counters[etype] = counters.get(etype, 0) + 1
            fresh = f"<{etype}_{counters[etype]}>"
            redacted = redacted.replace(token, fresh)
            token_map[fresh] = original
        else:
            token_map[token] = original
    return redacted


def _redact_value(value: Any, redactor: Any, token_map: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _redact_text_with_token_map(value, redactor, token_map)
    if isinstance(value, dict):
        return {k: _redact_value(v, redactor, token_map) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v, redactor, token_map) for v in value]
    return value


def build_tools_for_agent(
    authorized_tools: list[str],
    connector_config: dict[str, Any] | None = None,
    connector_names: list[str] | None = None,
    *,
    tenant_id: str | None = None,
    company_id: str | None = None,
    domain: ActionDomain | str | None = None,
    capability_authorization: CapabilityAuthorization | None = None,
    pii_token_map: dict[str, str] | None = None,
    pseudonymiser: PseudonymSession | None = None,
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

    ``pii_token_map`` is the ``before_llm`` redaction map shared with the
    runner. The LLM only ever sees ``<EMAIL_ADDRESS_1>`` style tokens, so
    its tool arguments are de-anonymized here (recursively) right before
    the connector call and the connector result is re-masked (extending the
    same map) before it is returned to the model. Trace/audit logging only
    ever sees the masked side.

    ``pseudonymiser`` (flag ``pseudonymisation.pre_model``) replaces that
    map with the case's persistent one: arguments are restored strictly (a
    call with a pseudonym that cannot be restored is refused, never sent)
    and results are pseudonymised before they reach the model. When it is
    set ``pii_token_map`` is ignored.

    Returns a list of callable LangChain tools ready for LangGraph.
    """
    tools: list[StructuredTool] = []
    handlers_by_connector: dict[str, dict[str, Callable[..., Any]]] = {}

    # Build a reverse index. Connector-qualified aliases are included so
    # CA pack tools can keep ``zoho_books:get_trial_balance`` and
    # ``tally:get_trial_balance`` separate instead of collapsing into a
    # bare-name collision.
    tool_index = _build_tool_index(
        connector_config,
        connector_names,
        include_connector_aliases=True,
    )

    # Bug sheet #14 (2026-09-14): resolve every spelling
    # (``gmail.send_email`` / ``gmail:send_email`` / ``gmail__send_email``
    # / ``tool:gmail:<perm>:send_email``) through one normaliser so the
    # index lookup, the registered LLM-facing name and dedup all agree.
    # One (connector, tool) pair registers exactly once; it takes the
    # ``connector__tool`` name when any ref for it was connector-qualified
    # and keeps its historical bare name otherwise.
    resolved: list[tuple[str, str, str]] = []
    qualified_pairs: set[tuple[str, str]] = set()
    for tool_ref in authorized_tools:
        parsed = _parse_authorized_tool_ref(tool_ref)
        if parsed is None:
            continue
        connector_hint, actual_tool_name = parsed
        lookup_key = f"{connector_hint}:{actual_tool_name}" if connector_hint else actual_tool_name
        match = tool_index.get(lookup_key)
        if not match:
            logger.warning(
                "authorized_tool_unresolved",
                tool_ref=str(tool_ref)[:120],
                connector=connector_hint,
                tool=actual_tool_name,
                connector_names=sorted(connector_names) if connector_names is not None else None,
            )
            continue
        connector_name, description = match
        if connector_hint:
            qualified_pairs.add((connector_name, actual_tool_name))
        resolved.append((connector_name, actual_tool_name, description))

    seen: set[tuple[str, str]] = set()
    for connector_name, actual_tool_name, description in resolved:
        pair = (connector_name, actual_tool_name)
        if pair in seen:
            continue
        seen.add(pair)
        public_tool_name = (
            _llm_safe_tool_name(connector_name, actual_tool_name) if pair in qualified_pairs else actual_tool_name
        )

        if connector_name not in handlers_by_connector:
            handlers_by_connector[connector_name] = _connector_tool_handlers(connector_name, connector_config)
        handler = handlers_by_connector[connector_name].get(actual_tool_name)
        handler_params, handler_accepts_var_kw = _handler_param_names(handler)
        # The full docstring lists the params for ``**params`` handlers;
        # OpenAI caps function descriptions at 1024 characters.
        handler_doc = (inspect.getdoc(handler) or "").strip() if handler is not None else ""
        tool_description = (handler_doc or description or f"Execute {actual_tool_name} on {connector_name}")[:1024]

        # Create an async wrapper that calls the connector
        def _make_tool_fn(cn: str, tn: str, desc: str, allowed: set[str], var_kw: bool):
            async def _tool_fn(**kwargs: Any) -> dict[str, Any]:
                params = _flatten_structured_tool_kwargs(kwargs)
                params = _drop_unknown_handler_params(
                    params,
                    allowed,
                    var_kw,
                    connector_name=cn,
                    tool_name=tn,
                )
                # Live execution payloads carry the real values; masking is
                # for the model, logs and traces only.
                if pseudonymiser is not None:
                    try:
                        params = await pseudonymiser.restore_arguments(params)
                    except PseudonymisationError as exc:
                        logger.warning("tool_call_refused_pseudonym", connector=cn, tool=tn, reason=exc.reason)
                        await _audit_pseudonym_refusal(tenant_id, cn, tn, exc.reason)
                        return refusal(exc)
                elif pii_token_map:
                    params = _deanonymize_value(params, pii_token_map)
                result = await _execute_connector_tool(
                    cn,
                    tn,
                    params,
                    connector_config,
                    tenant_id=tenant_id,
                    company_id=company_id,
                    domain=domain,
                    capability_authorization=capability_authorization,
                )
                if pseudonymiser is not None:
                    return await pseudonymiser.pseudonymise_value(result)
                if pii_token_map is not None:
                    from core.pii.redactor import PIIRedactor

                    redactor = PIIRedactor()
                    if redactor.mode == "before_llm":
                        result = _redact_value(result, redactor, pii_token_map)
                return result

            _tool_fn.__name__ = tn
            _tool_fn.__doc__ = desc or f"Execute {tn} on {cn} connector"
            return _tool_fn

        tool = StructuredTool.from_function(
            coroutine=_make_tool_fn(
                connector_name,
                actual_tool_name,
                tool_description,
                handler_params,
                handler_accepts_var_kw,
            ),
            name=public_tool_name,
            description=tool_description,
            args_schema=_tool_args_schema(handler, actual_tool_name),
            # Lets the graph map ``gmail.send_email`` / ``gmail:send_email``
            # tool calls back to this registered name (bug sheet #14).
            metadata={"connector": connector_name, "tool": actual_tool_name},
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
        allowed = {n.removeprefix("registry-").strip().lower() for n in connector_names if n}

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
                # Bug sheet #14: ``connector.tool`` is a persisted spelling too.
                index[f"{connector_name}.{tool_name}"] = (connector_name, doc)
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
