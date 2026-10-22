# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""UCP buyer connector: an AgenticOrg agent shops a public UCP merchant and stops before payment.

The connector reads the merchant's business profile (``/.well-known/ucp``), takes the REST
endpoint of ``dev.ucp.shopping`` at UCP 2026-04-08, and calls the REST binding:

* ``catalog_search`` and ``catalog_lookup`` (public; no API key is sent);
* ``checkout_create``, ``checkout_get`` and ``checkout_update`` (the merchant-issued platform
  key in ``X-API-Key``, scopes ``ucp:read`` / ``ucp:write``).

A UCP checkout session is a priced draft; only Complete executes payment. This connector has
no Complete, Cancel or payment tool, refuses payment members in a checkout body, and never
builds a URL other than the five operations above, so it cannot move money.

``sandbox_checkout_create`` and ``sandbox_checkout_update`` are the same two checkout writes,
allowed only at merchant hosts the operator lists in ``AGENTICORG_UCP_BUYER_SANDBOX_HOSTS``
(exact host names, comma separated, empty by default). They exist so an agent can demonstrate a
checkout at a synthetic demo shop where governance would otherwise contain the write; at any
other host they refuse before a request is built.

Every request goes through the connector's guarded client (``BaseConnector``): https only,
a public address checked before each send (``core.security.egress.validate_public_url_async``),
DNS pinned by ``build_pinned_async_transport``, no redirects, a timeout, and a bounded body.
The REST endpoint must be on the profile URL's origin, so the API key is never sent to a host
the merchant profile URL does not name.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()

UCP_VERSION = "2026-04-08"
SHOPPING_SERVICE = "dev.ucp.shopping"
CATALOG_SEARCH_CAPABILITY = "dev.ucp.shopping.catalog.search"
CATALOG_LOOKUP_CAPABILITY = "dev.ucp.shopping.catalog.lookup"
CHECKOUT_CAPABILITY = "dev.ucp.shopping.checkout"
PROFILE_PATH_SUFFIX = "/.well-known/ucp"
PROFILE_BODY_LIMIT = 256 * 1024
RESPONSE_BODY_LIMIT = 1024 * 1024
PROFILE_CACHE_SECONDS = 300.0
MAX_LOOKUP_IDS = 50
MAX_PAGE_SIZE = 50
MAX_LINE_ITEMS = 50
MAX_ID_LENGTH = 512
MAX_QUANTITY = 2**53 - 1
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
# A checkout id becomes one path segment; nothing that could add a segment or a query.
_CHECKOUT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:~-]{0,255}$")
# Members a full-replacement update may carry. Payment members (``payment``, ``ap2``) and the
# vendor member are refused: this connector never hands a payment credential to a merchant.
UPDATE_MEMBERS = frozenset({"id", "line_items", "buyer", "context", "fulfillment"})
PAYMENT_MEMBERS = frozenset({"payment", "ap2", "payment_data", "ai.agenticorg.commerce"})

PAYMENT_NOTE = (
    "Payment and checkout completion are not available to this agent. The buyer completes the "
    "purchase with the merchant (continue_url, when present)."
)
SANDBOX_HOSTS_ENV = "AGENTICORG_UCP_BUYER_SANDBOX_HOSTS"
AMOUNT_NOTE = "Amounts are integer minor units of the currency (for example 89900 INR is 899.00 INR)."


@dataclass(frozen=True)
class _MerchantProfile:
    endpoint: str
    capabilities: frozenset[str]


class _ToolError(Exception):
    """A refusal or failure returned to the agent as a non-secret error dict."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, **extra: Any) -> None:
        super().__init__(message)
        self.result: dict[str, Any] = {
            "error": code,
            "message": message,
            "retryable": retryable,
            "refusal": not retryable,
            **extra,
        }


def _origin(url: httpx.URL) -> tuple[str, str, int]:
    return (url.scheme, (url.host or "").lower(), url.port or (443 if url.scheme == "https" else 80))


def _https_url(value: Any) -> httpx.URL | None:
    """An absolute https URL without credentials, query or fragment; None otherwise."""
    if not isinstance(value, str) or not value.strip() or any(ch in value for ch in '"\\ \t\r\n'):
        return None
    try:
        url = httpx.URL(value.strip())
    except (httpx.InvalidURL, ValueError, TypeError):
        return None
    if url.scheme != "https" or not url.host or url.userinfo or url.query or url.fragment:
        return None
    return url


def _text(value: Any, limit: int = 1000) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in body.get("messages") or ():
        if not isinstance(item, dict):
            continue
        message = {key: _text(item.get(key), 500) for key in ("type", "code", "content", "severity", "path")}
        out.append({key: value for key, value in message.items() if value is not None})
    return out


def _money(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {"amount": value.get("amount"), "currency": value.get("currency")}


class UcpBuyerConnector(BaseConnector):
    """Buyer-side UCP REST client: catalog search and lookup, checkout create, read and update."""

    name = "ucp_buyer"
    category = "commerce"
    auth_type = "api_key"
    base_url = ""
    rate_limit_rpm = 120
    timeout_ms = 10000

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        # The client is bound to the merchant profile's origin, never to a configured base_url.
        profile = _https_url(self.config.get("merchant_profile_url"))
        self.base_url = f"{profile.scheme}://{profile.netloc.decode('ascii')}" if profile else ""
        self._profile: tuple[float, _MerchantProfile] | None = None

    def _register_tools(self) -> None:
        self._tool_registry = {
            "catalog_search": self.catalog_search,
            "catalog_lookup": self.catalog_lookup,
            "checkout_create": self.checkout_create,
            "checkout_get": self.checkout_get,
            "checkout_update": self.checkout_update,
            "sandbox_checkout_create": self.sandbox_checkout_create,
            "sandbox_checkout_update": self.sandbox_checkout_update,
        }

    def _api_key(self) -> str:
        """The merchant-issued platform key (resolved once per instance); never logged or returned."""
        cached = getattr(self, "_resolved_api_key", None)
        if cached is None:
            cached = str(self._get_secret("api_key") or "").strip()
            self._resolved_api_key = cached
        return cached

    def _has_credentials(self) -> bool:
        return bool(self._api_key())

    async def _authenticate(self) -> None:
        # The key is added per request, only on checkout calls; catalog calls are public.
        self._auth_headers = {"Accept": "application/json"}

    async def connect(self) -> None:
        self._auth_headers = {"Accept": "application/json"}
        self._client = self._build_http_client()

    async def health_check(self) -> dict[str, Any]:
        if not self._client:
            return {"status": "not_connected"}
        try:
            profile = await self._merchant_profile()
        except _ToolError as exc:
            return {"status": "unhealthy", "error": exc.result["error"]}
        return {"status": "healthy", "checkout_offered": CHECKOUT_CAPABILITY in profile.capabilities}

    # -- tools -------------------------------------------------------------------------------

    async def catalog_search(
        self, query: str, currency: str = "INR", limit: int | None = None, cursor: str | None = None
    ) -> dict[str, Any]:
        """Search the merchant's UCP catalog (public): products with variant ids, SKUs, prices and availability."""
        try:
            if not isinstance(query, str) or not query.strip() or len(query) > 512:
                raise _ToolError("invalid_request", "query must be a non-empty string of at most 512 characters.")
            body: dict[str, Any] = {"query": query.strip(), "context": {"currency": self._currency(currency)}}
            pagination: dict[str, Any] = {}
            if limit is not None:
                if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
                    raise _ToolError("invalid_request", f"limit must be an integer from 1 to {MAX_PAGE_SIZE}.")
                pagination["limit"] = limit
            if cursor is not None:
                if not isinstance(cursor, str) or not cursor or len(cursor) > 200:
                    raise _ToolError("invalid_request", "cursor must be the opaque cursor a previous search returned.")
                pagination["cursor"] = cursor
            if pagination:
                body["pagination"] = pagination
            response = await self._call("POST", "/catalog/search", body, capability=CATALOG_SEARCH_CAPABILITY)
            return self._finish(self._catalog_result(response, include_pagination=True))
        except _ToolError as exc:
            return self._finish(exc.result)

    async def catalog_lookup(self, ids: list[str]) -> dict[str, Any]:
        """Look up catalog products by product id, variant id or SKU (public)."""
        try:
            if (
                not isinstance(ids, list)
                or not 1 <= len(ids) <= MAX_LOOKUP_IDS
                or not all(isinstance(item, str) and item.strip() and len(item) <= MAX_ID_LENGTH for item in ids)
            ):
                raise _ToolError("invalid_request", f"ids must be a list of 1 to {MAX_LOOKUP_IDS} non-empty strings.")
            body = {"ids": [item.strip() for item in ids]}
            response = await self._call("POST", "/catalog/lookup", body, capability=CATALOG_LOOKUP_CAPABILITY)
            return self._finish(self._catalog_result(response, include_pagination=False))
        except _ToolError as exc:
            return self._finish(exc.result)

    async def checkout_create(
        self, line_items: list[dict[str, Any]], buyer: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Open a UCP checkout session (a priced draft) for line items [{item_id, quantity}]; never pays."""
        try:
            body: dict[str, Any] = {"line_items": self._line_items(line_items)}
            if buyer is not None:
                body["buyer"] = self._object(buyer, "buyer")
            response = await self._call("POST", "/checkout-sessions", body, checkout=True, write=True)
            return self._finish(self._checkout_result(response))
        except _ToolError as exc:
            return self._finish(exc.result)

    async def checkout_get(self, checkout_id: str) -> dict[str, Any]:
        """Read a UCP checkout session: status, line items, totals and merchant messages."""
        try:
            path = f"/checkout-sessions/{self._checkout_id(checkout_id)}"
            response = await self._call("GET", path, None, checkout=True)
            return self._finish(self._checkout_result(response))
        except _ToolError as exc:
            return self._finish(exc.result)

    async def checkout_update(self, checkout_id: str, checkout: dict[str, Any]) -> dict[str, Any]:
        """Replace a UCP checkout session; full replacement, so send every line item, buyer and fulfillment to keep."""
        try:
            session_id = self._checkout_id(checkout_id)
            source = self._object(checkout, "checkout")
            refused = sorted(key for key in source if key in PAYMENT_MEMBERS)
            if refused:
                raise _ToolError(
                    "payment_not_available",
                    f"This agent cannot send payment members ({', '.join(refused)}). {PAYMENT_NOTE}",
                )
            unknown = sorted(key for key in source if key not in UPDATE_MEMBERS)
            if unknown:
                raise _ToolError(
                    "invalid_request",
                    f"Unsupported checkout members: {', '.join(unknown)}. "
                    f"Allowed: {', '.join(sorted(UPDATE_MEMBERS))}.",
                )
            if source.get("id") not in (None, session_id):
                raise _ToolError("invalid_request", "checkout.id does not match checkout_id.")
            body: dict[str, Any] = {"id": session_id, "line_items": self._line_items(source.get("line_items"))}
            for member in ("buyer", "context", "fulfillment"):
                if source.get(member) is not None:
                    body[member] = self._object(source[member], member)
            response = await self._call("PUT", f"/checkout-sessions/{session_id}", body, checkout=True, write=True)
            return self._finish(self._checkout_result(response))
        except _ToolError as exc:
            return self._finish(exc.result)

    async def sandbox_checkout_create(
        self, line_items: list[dict[str, Any]], buyer: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Open a checkout session at an operator-listed sandbox shop (synthetic demo merchant only); never pays."""
        try:
            self._require_sandbox_host()
        except _ToolError as exc:
            return self._finish(exc.result)
        return await self.checkout_create(line_items, buyer)

    async def sandbox_checkout_update(self, checkout_id: str, checkout: dict[str, Any]) -> dict[str, Any]:
        """Replace a checkout session at an operator-listed sandbox shop; same rules as checkout_update."""
        try:
            self._require_sandbox_host()
        except _ToolError as exc:
            return self._finish(exc.result)
        return await self.checkout_update(checkout_id, checkout)

    # -- input validation --------------------------------------------------------------------

    @staticmethod
    def _currency(value: Any) -> str:
        currency = value.strip().upper() if isinstance(value, str) else ""
        if not _CURRENCY_RE.fullmatch(currency):
            raise _ToolError("invalid_request", "currency must be an ISO 4217 code such as INR.")
        return currency

    @staticmethod
    def _object(value: Any, name: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise _ToolError("invalid_request", f"{name} must be an object.")
        return dict(value)

    @staticmethod
    def _checkout_id(value: Any) -> str:
        if not isinstance(value, str) or not _CHECKOUT_ID_RE.fullmatch(value.strip()):
            raise _ToolError("invalid_request", "checkout_id must be the id a checkout tool returned.")
        return quote(value.strip(), safe="")

    @staticmethod
    def _line_items(value: Any) -> list[dict[str, Any]]:
        """``[{item_id, quantity}]`` (or the UCP ``[{item: {id}, quantity}]``) as UCP line items."""
        if not isinstance(value, list) or not 1 <= len(value) <= MAX_LINE_ITEMS:
            raise _ToolError("invalid_request", f"line_items must be a list of 1 to {MAX_LINE_ITEMS} items.")
        out: list[dict[str, Any]] = []
        for index, entry in enumerate(value):
            if not isinstance(entry, dict):
                raise _ToolError("invalid_request", f"line_items[{index}] must be an object.")
            item = entry.get("item")
            item_id = entry.get("item_id")
            if item_id is None and isinstance(item, dict):
                item_id = item.get("id")
            quantity = entry.get("quantity", 1)
            if not isinstance(item_id, str) or not item_id.strip() or len(item_id) > MAX_ID_LENGTH:
                raise _ToolError("invalid_request", f"line_items[{index}].item_id must be a catalog variant id.")
            if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= MAX_QUANTITY:
                raise _ToolError("invalid_request", f"line_items[{index}].quantity must be a positive integer.")
            line: dict[str, Any] = {"item": {"id": item_id.strip()}, "quantity": quantity}
            line_id = entry.get("id")
            if isinstance(line_id, str) and line_id.strip():
                line["id"] = line_id.strip()
            out.append(line)
        return out

    # -- transport ---------------------------------------------------------------------------

    def _profile_url(self) -> httpx.URL:
        url = _https_url(self.config.get("merchant_profile_url"))
        if url is None or not url.path.endswith(PROFILE_PATH_SUFFIX):
            raise _ToolError(
                "merchant_profile_url_invalid",
                "merchant_profile_url must be an https URL ending in /.well-known/ucp.",
            )
        return url

    @staticmethod
    def _sandbox_hosts() -> frozenset[str]:
        raw = os.getenv(SANDBOX_HOSTS_ENV, "")
        return frozenset(host.strip().lower().rstrip(".") for host in raw.split(",") if host.strip())

    def _require_sandbox_host(self) -> None:
        """Fail closed unless the merchant profile's host is listed exactly in the sandbox allowlist."""
        host = (self._profile_url().host or "").lower().rstrip(".")
        if not host or host not in self._sandbox_hosts():
            raise _ToolError(
                "sandbox_host_not_allowed",
                "Sandbox checkout is allowed only at merchant hosts the operator lists in "
                f"{SANDBOX_HOSTS_ENV}; use checkout_create, which governance reviews.",
            )

    def _ucp_agent(self, *, required: bool) -> str | None:
        raw = self.config.get("agent_profile_url")
        if raw in (None, "") and not required:
            return None
        url = _https_url(raw)
        if url is None:
            raise _ToolError(
                "agent_profile_url_invalid",
                "agent_profile_url must be the buyer platform's public https UCP profile URL.",
            )
        # RFC 8941 sf-string; quotes, backslashes and whitespace were refused above.
        return f'profile="{url}"'

    async def _read(self, method: str, url: str, *, headers: dict[str, str], body: Any, limit: int) -> tuple[int, Any]:
        """One request through the guarded client: no redirects, bounded body, JSON or None."""
        if not self._client:
            raise _ToolError("not_connected", "The connector is not connected.", retryable=True)
        content = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        if content is not None:
            headers = {**headers, "Content-Type": "application/json"}
        try:
            async with self._client.stream(
                method, url, content=content, headers=headers, follow_redirects=False
            ) as response:
                received = bytearray()
                async for chunk in response.aiter_bytes():
                    received.extend(chunk)
                    if len(received) > limit:
                        raise _ToolError("merchant_response_too_large", "The merchant response is too large.")
                status = response.status_code
        except httpx.TimeoutException as exc:
            raise _ToolError("merchant_timeout", "The merchant did not answer in time.", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise _ToolError("merchant_unreachable", "The merchant could not be reached.", retryable=True) from exc
        except ValueError as exc:
            # The guarded client refuses a URL that is not a public https endpoint.
            raise _ToolError("merchant_url_refused", "The merchant URL is not a public https endpoint.") from exc
        try:
            payload = json.loads(bytes(received)) if received else None
        except ValueError:
            payload = None
        logger.info("ucp_buyer_request", connector=self.name, method=method, http_status=status)
        return status, payload

    async def _merchant_profile(self) -> _MerchantProfile:
        cached = self._profile
        now = time.monotonic()
        if cached is not None and cached[0] > now:
            return cached[1]
        profile_url = self._profile_url()
        status, document = await self._read(
            "GET", str(profile_url), headers={"Accept": "application/json"}, body=None, limit=PROFILE_BODY_LIMIT
        )
        if status != 200:
            raise _ToolError(
                "merchant_profile_unreachable",
                "The merchant profile could not be fetched.",
                retryable=status >= 500,
                http_status=status,
            )
        profile = self._parse_profile(profile_url, document)
        self._profile = (now + PROFILE_CACHE_SECONDS, profile)
        return profile

    @staticmethod
    def _parse_profile(profile_url: httpx.URL, document: Any) -> _MerchantProfile:
        """The ``dev.ucp.shopping`` REST endpoint at 2026-04-08 and the capabilities offered at it."""
        ucp = document.get("ucp") if isinstance(document, dict) else None
        if not isinstance(ucp, dict):
            raise _ToolError("merchant_profile_invalid", "The merchant profile has no ucp object.")
        services = ucp.get("services")
        entries = services.get(SHOPPING_SERVICE) if isinstance(services, dict) else None
        if isinstance(entries, dict):
            entries = [entries]
        endpoint = next(
            (
                entry.get("endpoint")
                for entry in entries or ()
                if isinstance(entry, dict) and entry.get("transport") == "rest" and entry.get("version") == UCP_VERSION
            ),
            None,
        )
        endpoint_url = _https_url(endpoint)
        if endpoint_url is None:
            raise _ToolError(
                "merchant_rest_service_missing",
                f"The merchant profile has no {SHOPPING_SERVICE} REST endpoint at UCP {UCP_VERSION}.",
            )
        if _origin(endpoint_url) != _origin(profile_url):
            # The API key goes to this endpoint, so it must be on the origin the operator configured.
            raise _ToolError(
                "merchant_endpoint_origin_mismatch",
                "The merchant's UCP endpoint is not on the profile URL's origin; nothing was sent.",
            )
        offered: set[str] = set()
        capabilities = ucp.get("capabilities")
        for name, items in capabilities.items() if isinstance(capabilities, dict) else ():
            listed = items if isinstance(items, list) else [items]
            if any(isinstance(item, dict) and item.get("version") == UCP_VERSION for item in listed):
                offered.add(str(name))
        return _MerchantProfile(endpoint=str(endpoint_url).rstrip("/"), capabilities=frozenset(offered))

    async def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        *,
        capability: str = CHECKOUT_CAPABILITY,
        checkout: bool = False,
        write: bool = False,
    ) -> dict[str, Any]:
        """Send one UCP REST operation and return the 2xx JSON body, or raise a mapped error."""
        api_key = ""
        if checkout:
            agent = self._ucp_agent(required=True)
            api_key = self._api_key()
            if not api_key:
                raise _ToolError("api_key_missing", "A merchant-issued platform API key is required for checkout.")
        else:
            agent = self._ucp_agent(required=False)
        profile = await self._merchant_profile()
        if capability not in profile.capabilities:
            raise _ToolError(
                "capability_not_offered",
                f"The merchant does not offer {capability} at UCP {UCP_VERSION}.",
            )
        headers = {"Accept": "application/json", "Request-Id": str(uuid.uuid4())}
        if agent:
            headers["UCP-Agent"] = agent
        if checkout:
            headers["X-API-Key"] = api_key
        if write:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        status, payload = await self._read(
            method, f"{profile.endpoint}{path}", headers=headers, body=body, limit=RESPONSE_BODY_LIMIT
        )
        if status >= 300:
            raise self._http_error(status, payload)
        if not isinstance(payload, dict):
            raise _ToolError("invalid_merchant_response", "The merchant returned a response that is not a JSON object.")
        ucp = payload.get("ucp") if isinstance(payload.get("ucp"), dict) else {}
        if ucp.get("status") == "error":
            messages = _messages(payload)
            first = messages[0] if messages else {}
            raise _ToolError(
                "merchant_refused",
                first.get("content") or "The merchant refused the request.",
                merchant_code=first.get("code"),
                messages=messages,
            )
        return payload

    @staticmethod
    def _http_error(status: int, payload: Any) -> _ToolError:
        merchant_code = merchant_message = None
        if isinstance(payload, dict):
            merchant_code = _text(payload.get("code"), 100)
            merchant_message = _text(payload.get("content"), 500)
            messages = _messages(payload)
            if messages and not merchant_code:
                merchant_code = messages[0].get("code")
                merchant_message = messages[0].get("content")
        extra = {"http_status": status, "merchant_code": merchant_code, "merchant_message": merchant_message}
        if status in (401, 403):
            return _ToolError(
                "merchant_auth_rejected",
                "The merchant rejected the platform API key (scopes ucp:read and ucp:write are needed).",
                **extra,
            )
        if status == 404:
            return _ToolError(
                "not_found",
                "Not found: the checkout session is unknown or the merchant does not serve checkout.",
                **extra,
            )
        if status == 409:
            return _ToolError("conflict", "The checkout cannot be changed in its current state.", **extra)
        if status == 429:
            return _ToolError("rate_limited", "The merchant is rate limiting requests.", retryable=True, **extra)
        if status >= 500:
            return _ToolError("merchant_error", "The merchant failed to answer.", retryable=True, **extra)
        if 300 <= status < 400:
            return _ToolError("merchant_redirect_refused", "The merchant answered with a redirect.", **extra)
        if status in (422, 424):
            return _ToolError("negotiation_failed", "The merchant could not negotiate UCP with this agent.", **extra)
        return _ToolError("invalid_request", "The merchant rejected the request.", **extra)

    # -- results -----------------------------------------------------------------------------

    @staticmethod
    def _catalog_result(payload: dict[str, Any], *, include_pagination: bool) -> dict[str, Any]:
        products: list[dict[str, Any]] = []
        for product in payload.get("products") or ():
            if not isinstance(product, dict):
                continue
            variants = []
            for variant in product.get("variants") or ():
                if not isinstance(variant, dict):
                    continue
                availability = variant.get("availability") if isinstance(variant.get("availability"), dict) else {}
                variants.append(
                    {
                        "id": variant.get("id"),
                        "sku": variant.get("sku"),
                        "title": variant.get("title"),
                        "price": _money(variant.get("price")),
                        "availability": availability.get("status"),
                    }
                )
            description = product.get("description") if isinstance(product.get("description"), dict) else {}
            products.append(
                {
                    "id": product.get("id"),
                    "title": product.get("title"),
                    "description": _text(description.get("plain"), 1000),
                    "variants": variants,
                }
            )
        result: dict[str, Any] = {"products": products, "messages": _messages(payload), "note": AMOUNT_NOTE}
        pagination = payload.get("pagination")
        if include_pagination and isinstance(pagination, dict):
            result["pagination"] = {
                key: pagination[key] for key in ("has_next_page", "cursor", "total_count") if key in pagination
            }
        return result

    @staticmethod
    def _checkout_result(payload: dict[str, Any]) -> dict[str, Any]:
        line_items = []
        for line in payload.get("line_items") or ():
            if not isinstance(line, dict):
                continue
            item = line.get("item") if isinstance(line.get("item"), dict) else {}
            line_items.append(
                {
                    "id": line.get("id"),
                    "item": {"id": item.get("id"), "title": item.get("title"), "price": item.get("price")},
                    "quantity": line.get("quantity"),
                    "totals": line.get("totals"),
                }
            )
        result: dict[str, Any] = {
            "id": payload.get("id"),
            "status": payload.get("status"),
            "currency": payload.get("currency"),
            "line_items": line_items,
            "totals": payload.get("totals"),
            "messages": _messages(payload),
        }
        for member in ("buyer", "fulfillment", "continue_url", "expires_at"):
            if payload.get(member) is not None:
                result[member] = payload[member]
        result["payment"] = "not_available"
        result["note"] = f"{PAYMENT_NOTE} {AMOUNT_NOTE}"
        return result

    def _finish(self, result: dict[str, Any]) -> dict[str, Any]:
        """Drop empty extras and make sure the API key never appears in a result."""
        result = {key: value for key, value in result.items() if value is not None}
        api_key = self._api_key()
        if api_key and api_key in json.dumps(result, default=str):
            result = json.loads(json.dumps(result, default=str).replace(api_key, "[redacted]"))
        return result
