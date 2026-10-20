"""Grantex Commerce connector for AgenticOrg internal sandbox."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any
from urllib.parse import quote

import httpx

from connectors.framework.base_connector import BaseConnector
from core.commerce.agent_request_signing import (
    AgentSigningError,
    AgentSigningKey,
    load_agent_signing_key,
    new_nonce,
    sign_agent_request,
    tap_tag,
    with_purchase_body_objects,
)
from core.commerce.sales_guardrails import normalize_grantex_error, validate_payment_action
from core.config import grantex_base_url_for_env, settings

TOOL_ALIAS_TO_GRANTEX: dict[str, str] = {
    "merchant_get_profile": "merchant.get_profile",
    "catalog_search": "catalog.search",
    "catalog_get_item": "catalog.get_item",
    "inventory_check": "inventory.check",
    "cart_create": "cart.create",
    "payment_create_intent": "payment.create_intent",
    "checkout_create": "checkout.create",
    "payment_get_status": "payment.get_status",
}

REST_CONSENT_ENDPOINTS: dict[str, str] = {
    "consent_request": "/v1/commerce/passports/consent-requests",
    "consent_exchange": "/v1/commerce/passports/exchange",
}

REST_READ_ONLY_ENDPOINTS: dict[str, str] = {
    "buyer_discovery_preview": "/v1/commerce/merchants/{merchant_id}/agenticorg-buyer-discovery-preview",
}

IDEMPOTENT_TOOL_ALIASES = frozenset({"cart_create", "payment_create_intent", "checkout_create"})
# Signed with the TAP payer tag (agent-payer-auth); every other call is browsing (G48).
PAYMENT_TOOL_ALIASES = frozenset({"payment_create_intent", "checkout_create"})
# Caller inputs that shape the TAP body objects (G49); never forwarded as tool arguments.
BODY_OBJECT_PARAMS = ("grantex_consent", "tap_consumer_id_token")


class GrantexCommerceConnector(BaseConnector):
    """Connector exposing safe AgenticOrg aliases for Grantex Commerce tools."""

    name = "grantex_commerce"
    category = "commerce"
    auth_type = "grantex_bearer"
    # Per-environment Grantex origin (bug sheet 2026-09-14 #13) unless an
    # explicit commerce override is set.
    base_url = os.getenv("GRANTEX_COMMERCE_BASE_URL") or grantex_base_url_for_env()
    rate_limit_rpm = 600
    timeout_ms = 10000

    @staticmethod
    def _strip_bearer_prefix(value: str) -> str:
        token = value.strip()
        if token.lower().startswith("bearer "):
            return token[7:].strip()
        return token

    def _register_tools(self) -> None:
        self._tool_registry = {
            "merchant_get_profile": self.merchant_get_profile,
            "catalog_search": self.catalog_search,
            "catalog_get_item": self.catalog_get_item,
            "inventory_check": self.inventory_check,
            "cart_create": self.cart_create,
            "consent_request": self.consent_request,
            "consent_exchange": self.consent_exchange,
            "buyer_discovery_preview": self.buyer_discovery_preview,
            "payment_create_intent": self.payment_create_intent,
            "checkout_create": self.checkout_create,
            "payment_get_status": self.payment_get_status,
        }

    def _credential_token(self) -> str:
        for key in ("bearer_token", "agent_assertion", "access_token", "token", "api_key"):
            value = str(self.config.get(key, "") or "").strip()
            if value:
                return self._strip_bearer_prefix(value)

        for env_key in ("GRANTEX_COMMERCE_BEARER_TOKEN", "GRANTEX_AGENT_ASSERTION", "GRANTEX_API_KEY"):
            value = str(os.getenv(env_key, "") or "").strip()
            if value:
                return self._strip_bearer_prefix(value)

        return self._strip_bearer_prefix(str(self._get_secret("bearer_token") or ""))

    def _has_credentials(self) -> bool:
        return bool(self._credential_token())

    async def _authenticate(self) -> None:
        token = self._credential_token()
        self._auth_headers = {"Accept": "application/json"}
        if token:
            self._auth_headers["Authorization"] = f"Bearer {token}"

    async def health_check(self) -> dict[str, Any]:
        if not self._client:
            return {"status": "not_connected"}
        try:
            response = await self._client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": f"agenticorg-{uuid.uuid4().hex}", "method": "tools/list"},
            )
        except httpx.HTTPError:
            return {"status": "unhealthy", "error": "grantex_transport_error"}
        if response.status_code < 400:
            return {"status": "healthy", "http_status": response.status_code}
        return {"status": "unhealthy", "http_status": response.status_code}

    async def merchant_get_profile(self, **params: Any) -> dict[str, Any]:
        """Get a merchant commerce profile through Grantex Commerce."""
        return await self._call_mcp_alias("merchant_get_profile", params)

    async def catalog_search(self, **params: Any) -> dict[str, Any]:
        """Search merchant catalog through Grantex Commerce."""
        return await self._call_mcp_alias("catalog_search", params)

    async def catalog_get_item(self, **params: Any) -> dict[str, Any]:
        """Get a catalog item through Grantex Commerce."""
        return await self._call_mcp_alias("catalog_get_item", params)

    async def inventory_check(self, **params: Any) -> dict[str, Any]:
        """Check inventory through Grantex Commerce."""
        return await self._call_mcp_alias("inventory_check", params)

    async def cart_create(self, **params: Any) -> dict[str, Any]:
        """Create a Grantex Commerce cart draft."""
        missing = self._require_idempotency_key("cart_create", params)
        if missing:
            return missing
        return await self._call_mcp_alias("cart_create", params)

    async def consent_request(self, **params: Any) -> dict[str, Any]:
        """Create a Grantex Commerce consent request."""
        return await self._post_rest(REST_CONSENT_ENDPOINTS["consent_request"], params)

    async def consent_exchange(self, **params: Any) -> dict[str, Any]:
        """Exchange granted consent for a Grantex-minted Commerce Passport."""
        return await self._post_rest(REST_CONSENT_ENDPOINTS["consent_exchange"], params)

    async def buyer_discovery_preview(self, **params: Any) -> dict[str, Any]:
        """Read Grantex AgenticOrg buyer-agent discovery preview evidence."""
        merchant_id = str(params.get("merchant_id", "") or "").strip()
        if not merchant_id:
            return {
                "error": "merchant_id_required",
                "message": "merchant_id is required for buyer discovery preview.",
                "retryable": False,
                "refusal": True,
            }
        path = REST_READ_ONLY_ENDPOINTS["buyer_discovery_preview"].format(
            merchant_id=quote(merchant_id, safe="")
        )
        return await self._get_rest(path)

    async def payment_create_intent(self, **params: Any) -> dict[str, Any]:
        """Create a payment intent through Grantex Commerce only."""
        missing = self._require_idempotency_key("payment_create_intent", params)
        if missing:
            return missing
        guardrail = validate_payment_action("payment_create_intent", params)
        if not guardrail.get("allowed"):
            return guardrail
        return await self._call_mcp_alias("payment_create_intent", params)

    async def checkout_create(self, **params: Any) -> dict[str, Any]:
        """Create a checkout handoff through Grantex Commerce only."""
        missing = self._require_idempotency_key("checkout_create", params)
        if missing:
            return missing
        guardrail = validate_payment_action("checkout_create", params)
        if not guardrail.get("allowed"):
            return guardrail
        return await self._call_mcp_alias("checkout_create", params)

    async def payment_get_status(self, **params: Any) -> dict[str, Any]:
        """Poll payment status through Grantex Commerce only."""
        guardrail = validate_payment_action("payment_get_status", params)
        if not guardrail.get("allowed"):
            return guardrail
        return await self._call_mcp_alias("payment_get_status", params)

    def _require_idempotency_key(self, alias: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if alias not in IDEMPOTENT_TOOL_ALIASES:
            return None
        if str(params.get("idempotency_key", "") or "").strip():
            return None
        return {
            "error": "idempotency_key_required",
            "message": f"idempotency_key is required for {alias}.",
            "retryable": False,
            "refusal": False,
        }

    async def _signing_key(self) -> AgentSigningKey | None:
        """The tenant agent key (decrypted once per connector instance), or None while signing is off."""
        if not settings.httpsig_sign_outbound:
            return None
        if getattr(self, "_agent_signing_key", None) is None:
            key = await load_agent_signing_key(self.config)
            if key is None:
                raise AgentSigningError("outbound signing is on but no agent signing key is configured")
            self._agent_signing_key = key
        return self._agent_signing_key

    async def _send_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        purpose: str,
        body_objects: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Send one request, signed under the TAP profile with the tenant agent key when signing is on."""
        if not self._client:
            raise RuntimeError("Connector not connected")
        key = await self._signing_key()
        if key is None:
            if payload is None:
                return await self._client.request(method, path)
            return await self._client.request(method, path, json=payload)
        nonce = new_nonce()
        if payload is not None and body_objects and body_objects.get("grantex_consent"):
            payload = with_purchase_body_objects(
                payload,
                key=key,
                nonce=nonce,
                consent=body_objects["grantex_consent"],
                consumer_id_token=body_objects.get("tap_consumer_id_token") or None,
            )
        content = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        request = self._client.build_request(method, path, content=content or None, headers=headers)
        request.headers.update(
            sign_agent_request(
                method=method, url=str(request.url), body=content, headers=headers, key=key,
                tag=tap_tag(purpose), nonce=nonce,
            )
        )
        return await self._client.send(request)

    @staticmethod
    def _signing_refusal(exc: AgentSigningError) -> dict[str, Any]:
        return {
            "error": "agent_request_signing_failed",
            "message": str(exc),
            "retryable": False,
            "refusal": True,
        }

    async def _call_mcp_alias(self, alias: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError("Connector not connected")
        grantex_name = TOOL_ALIAS_TO_GRANTEX[alias]
        body_objects = {name: params.pop(name) for name in BODY_OBJECT_PARAMS if name in params}
        payload = {
            "jsonrpc": "2.0",
            "id": f"agenticorg-{uuid.uuid4().hex}",
            "method": "tools/call",
            "params": {
                "name": grantex_name,
                "arguments": params,
            },
        }
        try:
            response = await self._send_json(
                "POST", "/mcp", payload,
                purpose="payment" if alias in PAYMENT_TOOL_ALIASES else "browse",
                body_objects=body_objects if alias in PAYMENT_TOOL_ALIASES else None,
            )
        except AgentSigningError as exc:
            return self._signing_refusal(exc)
        except httpx.HTTPError:
            return {
                "error": "grantex_transport_error",
                "message": "Grantex Commerce is unreachable.",
                "retryable": True,
                "refusal": False,
            }
        return self._parse_mcp_response(response)

    async def _post_rest(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError("Connector not connected")
        try:
            response = await self._send_json("POST", path, params, purpose="browse")
        except AgentSigningError as exc:
            return self._signing_refusal(exc)
        except httpx.HTTPError:
            return {
                "error": "grantex_transport_error",
                "message": "Grantex Commerce is unreachable.",
                "retryable": True,
                "refusal": False,
            }

        payload = self._response_json(response)
        if response.status_code >= 400:
            return normalize_grantex_error(payload, response.status_code)
        return payload if isinstance(payload, dict) else {"data": payload}

    async def _get_rest(self, path: str) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError("Connector not connected")
        try:
            response = await self._send_json("GET", path, None, purpose="browse")
        except AgentSigningError as exc:
            return self._signing_refusal(exc)
        except httpx.HTTPError:
            return {
                "error": "grantex_transport_error",
                "message": "Grantex Commerce is unreachable.",
                "retryable": True,
                "refusal": False,
            }

        payload = self._response_json(response)
        if response.status_code >= 400:
            return normalize_grantex_error(payload, response.status_code)
        return payload if isinstance(payload, dict) else {"data": payload}

    def _parse_mcp_response(self, response: httpx.Response) -> dict[str, Any]:
        payload = self._response_json(response)
        if response.status_code >= 400:
            return normalize_grantex_error(payload, response.status_code)
        if not isinstance(payload, dict):
            return {"error": "invalid_grantex_response", "message": "Grantex Commerce returned an invalid response."}

        if isinstance(payload.get("error"), dict):
            return normalize_grantex_error(payload, response.status_code)

        result = payload.get("result")
        if not isinstance(result, dict):
            return {"data": result}

        parsed_content = self._parse_content(result.get("content"))
        if result.get("isError"):
            return normalize_grantex_error(parsed_content or result, response.status_code)
        if parsed_content is not None:
            return parsed_content if isinstance(parsed_content, dict) else {"data": parsed_content}
        return result

    @staticmethod
    def _response_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            return {}

    @staticmethod
    def _parse_content(content: Any) -> Any:
        if not isinstance(content, list) or not content:
            return None
        first = content[0]
        if not isinstance(first, dict):
            return None
        text = first.get("text")
        if not isinstance(text, str):
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"content": text}
