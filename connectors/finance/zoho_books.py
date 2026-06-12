"""Zoho Books connector — real Zoho Books API v3 integration."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()

# Zoho API regions
_ZOHO_GLOBAL_BASE = "https://www.zohoapis.com/books/v3"
_ZOHO_IN_BASE = "https://www.zohoapis.in/books/v3"
_ZOHO_TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"
_ZOHO_IN_TOKEN_URL = "https://accounts.zoho.in/oauth/v2/token"
_ZOHO_BASE_URLS = {
    "in": _ZOHO_IN_BASE,
    "us": _ZOHO_GLOBAL_BASE,
    "eu": "https://www.zohoapis.eu/books/v3",
    "au": "https://www.zohoapis.com.au/books/v3",
    "jp": "https://www.zohoapis.jp/books/v3",
}
_ZOHO_TOKEN_URLS = {
    "in": _ZOHO_IN_TOKEN_URL,
    "us": _ZOHO_TOKEN_URL,
    "eu": "https://accounts.zoho.eu/oauth/v2/token",
    "au": "https://accounts.zoho.com.au/oauth/v2/token",
    "jp": "https://accounts.zoho.jp/oauth/v2/token",
}
_ZOHO_REGION_HOSTS = {
    "in": ("zohoapis.in", "books.zoho.in", "accounts.zoho.in"),
    "eu": ("zohoapis.eu", "accounts.zoho.eu"),
    "au": ("zohoapis.com.au", "accounts.zoho.com.au"),
    "jp": ("zohoapis.jp", "accounts.zoho.jp"),
    "us": ("zohoapis.com", "accounts.zoho.com"),
}
_ORG_ID_KEYS = ("organization_id", "org_id", "zoho_organization_id")


def _host_from_urlish(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    candidate = text if "://" in text else f"https://{text}"
    parsed = urlparse(candidate)
    return (parsed.hostname or "").rstrip(".").lower()


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _region_from_config(config: dict[str, Any]) -> str:
    region = str(config.get("region") or "").strip().lower().replace(".", "")
    aliases = {
        "india": "in",
        "in_dc": "in",
        "global": "us",
        "us_dc": "us",
        "europe": "eu",
        "eu_dc": "eu",
        "australia": "au",
        "au_dc": "au",
        "japan": "jp",
        "jp_dc": "jp",
    }
    region = aliases.get(region, region)
    if region in _ZOHO_BASE_URLS:
        return region

    for value in (config.get("base_url"), config.get("api_base_url"), config.get("token_url")):
        host = _host_from_urlish(value)
        if not host:
            continue
        for candidate_region, domains in _ZOHO_REGION_HOSTS.items():
            if any(_host_matches(host, domain) for domain in domains):
                return candidate_region
    return "us"


def _is_india_config(config: dict[str, Any]) -> bool:
    return _region_from_config(config) == "in"


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "n", "missing", "unavailable"}
    return bool(value)


def _normalise_org_id(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "none", "null"} else text


def _redact_org_id(value: Any) -> str:
    text = _normalise_org_id(value)
    if not text:
        return ""
    return f"***{text[-4:]}" if len(text) > 4 else "***"


class ZohoBooksConnector(BaseConnector):
    name = "zoho_books"
    category = "finance"
    auth_type = "oauth2"
    base_url = _ZOHO_GLOBAL_BASE
    rate_limit_rpm = 100
    tools = [
        "create_invoice",
        "list_invoices",
        "search_invoices",
        "get_invoice_by_id",
        "list_bills",
        "list_vendor_bills",
        "search_bills",
        "get_bill_by_id",
        "get_purchase_invoices",
        "list_overdue_invoices",
        "list_expense_transactions",
        "get_expense_transactions",
        "record_expense",
        "get_vendor_payables",
        "list_vendors",
        "create_vendor",
        "create_item",
        "create_bill",
        "get_vendor_details",
        "create_journal_entry",
        "create_tds_entry",
        "update_bill",
        "get_balance_sheet",
        "get_profit_loss",
        "get_ledger_balance",
        "get_trial_balance",
        "generate_gst_report",
        "calculate_tds",
        "list_chartofaccounts",
        "fetch_bank_statement",
        "check_account_balance",
        "get_transaction_list",
        "reconcile_bank",
        "reconcile_transaction",
        "get_organization",
    ]

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        region = _region_from_config(self.config)
        self.base_url = _ZOHO_BASE_URLS[region]
        self.config["region"] = region
        self.config["base_url"] = self.base_url
        self.config["token_url"] = _ZOHO_TOKEN_URLS[region]
        self._org_id: str = _normalise_org_id(self.config.get("organization_id", ""))
        if self._org_id:
            self.config["organization_id"] = self._org_id

    def _register_tools(self):
        self._tool_registry["create_invoice"] = self.create_invoice
        self._tool_registry["list_invoices"] = self.list_invoices
        self._tool_registry["search_invoices"] = self.search_invoices
        self._tool_registry["get_invoice_by_id"] = self.get_invoice_by_id
        self._tool_registry["list_bills"] = self.list_bills
        self._tool_registry["list_vendor_bills"] = self.list_vendor_bills
        self._tool_registry["search_bills"] = self.search_bills
        self._tool_registry["get_bill_by_id"] = self.get_bill_by_id
        self._tool_registry["get_purchase_invoices"] = self.get_purchase_invoices
        self._tool_registry["list_overdue_invoices"] = self.list_overdue_invoices
        self._tool_registry["list_expense_transactions"] = self.list_expense_transactions
        self._tool_registry["get_expense_transactions"] = self.get_expense_transactions
        self._tool_registry["record_expense"] = self.record_expense
        self._tool_registry["get_vendor_payables"] = self.get_vendor_payables
        self._tool_registry["list_vendors"] = self.list_vendors
        self._tool_registry["create_vendor"] = self.create_vendor
        self._tool_registry["create_item"] = self.create_item
        self._tool_registry["create_bill"] = self.create_bill
        self._tool_registry["get_vendor_details"] = self.get_vendor_details
        self._tool_registry["create_journal_entry"] = self.create_journal_entry
        self._tool_registry["create_tds_entry"] = self.create_tds_entry
        self._tool_registry["update_bill"] = self.update_bill
        self._tool_registry["get_balance_sheet"] = self.get_balance_sheet
        self._tool_registry["get_profit_loss"] = self.get_profit_loss
        self._tool_registry["get_ledger_balance"] = self.get_ledger_balance
        self._tool_registry["get_trial_balance"] = self.get_trial_balance
        self._tool_registry["generate_gst_report"] = self.generate_gst_report
        self._tool_registry["calculate_tds"] = self.calculate_tds
        self._tool_registry["list_chartofaccounts"] = self.list_chartofaccounts
        self._tool_registry["fetch_bank_statement"] = self.fetch_bank_statement
        self._tool_registry["check_account_balance"] = self.check_account_balance
        self._tool_registry["get_transaction_list"] = self.get_transaction_list
        self._tool_registry["reconcile_bank"] = self.reconcile_bank
        self._tool_registry["reconcile_transaction"] = self.reconcile_transaction
        self._tool_registry["get_organization"] = self.get_organization

    async def _authenticate(self):
        """OAuth2 refresh token flow for Zoho Books."""
        refresh_token = self._get_secret("refresh_token")
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")

        if refresh_token and client_id:
            fresh_token = await self._refresh_oauth(client_id, client_secret, refresh_token)
            if fresh_token:
                self._auth_headers = {
                    "Authorization": f"Zoho-oauthtoken {fresh_token}",
                    "Content-Type": "application/json",
                }
                return

        # Fallback: stored access_token
        access_token = self._get_secret("access_token")
        if access_token:
            self._auth_headers = {
                "Authorization": f"Zoho-oauthtoken {access_token}",
                "Content-Type": "application/json",
            }

    async def _refresh_oauth(
        self, client_id: str, client_secret: str, refresh_token: str
    ) -> str | None:
        """Exchange refresh_token for a fresh access_token via Zoho OAuth2."""
        token_url = _ZOHO_TOKEN_URLS.get(str(self.config.get("region") or "us"), _ZOHO_TOKEN_URL)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    token_url,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh_token,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                logger.info("zoho_books_token_refreshed", expires_in=data.get("expires_in"))
                return data["access_token"]
        # enterprise-gate: broad-except-ok reason=connector-oauth-refresh-falls-back-to-existing-token-or-fails-health
        except Exception as exc:
            logger.warning("zoho_books_token_refresh_failed", error_type=type(exc).__name__)
            return None

    async def connect(self) -> None:
        """Connect, then ensure ``self._org_id`` is populated.

        RU-May01-BUG-02: every Zoho Books API call (except
        ``GET /organizations``) requires ``organization_id`` as a
        query param. Historical behavior: read from config only;
        if missing, ``self._org_id = ""`` was injected into every
        URL and Zoho returned 6041 ``This Organization is not
        associated with this Zoho account``. Auto-fetch closes the
        gap so a connector created without an explicit org_id (e.g.
        a tester onboarding flow that doesn't know the value yet)
        works on first use.
        """
        await super().connect()
        if not self._org_id and self._client:
            await self._fetch_org_id_from_api()

    async def _fetch_org_id_from_api(self) -> None:
        """Pull the first organization id from ``GET /organizations``.

        Best effort — log + continue on failure so transient API
        outage doesn't break unrelated tools.
        """
        try:
            data = await self._get("/organizations")
            orgs = data.get("organizations", []) if isinstance(data, dict) else []
            if orgs:
                self._set_org_id(orgs[0].get("organization_id"))
                logger.info(
                    "zoho_books_org_id_auto_fetched",
                    org_id=_redact_org_id(self._org_id),
                    org_name=orgs[0].get("name", ""),
                )
        # enterprise-gate: broad-except-ok reason=connector-org-discovery-fallback-requires-org-before-tool-success
        except Exception as exc:  # noqa: BLE001
            logger.warning("zoho_books_org_id_auto_fetch_failed", error_type=type(exc).__name__)

    async def _ensure_org_id(self) -> None:
        """Lazy-fetch org_id immediately before tool dispatch.

        Belt-and-braces with ``connect()``: if a cached connector
        instance was created before the org_id was set, this catches
        it on the next tool call without requiring a reconnect.
        """
        if not self._org_id and self._client:
            await self._fetch_org_id_from_api()

    @staticmethod
    def _filter_params(params: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
        return {field: params[field] for field in fields if params.get(field) is not None}

    @staticmethod
    def _raise_for_zoho_error(data: Any, path: str) -> None:
        """Raise on Zoho's HTTP-200 error envelope."""
        if not isinstance(data, dict) or "code" not in data:
            return
        raw_code = data.get("code")
        try:
            code = int(raw_code or 0)
        except (TypeError, ValueError):
            code = 0 if str(raw_code).strip() in {"", "0"} else -1
        if code == 0:
            return
        message = str(data.get("message") or "Unknown Zoho Books error")
        raise RuntimeError(
            f"Zoho Books {path} error {code}: {message}. "
            "Credentials missing, expired, or not authorized. "
            "Configure Zoho Books at Dashboard -> Connectors -> Zoho Books."
        )

    @staticmethod
    def _runtime_error_from_http_status(exc: httpx.HTTPStatusError) -> RuntimeError:
        response = exc.response
        request = exc.request
        try:
            zoho_body = response.json()
        except ValueError:
            zoho_body = {}
        if isinstance(zoho_body, dict):
            zoho_code = zoho_body.get("code", "N/A")
            zoho_message = zoho_body.get("message") or response.text[:200]
        else:
            zoho_code = "N/A"
            zoho_message = response.text[:200]
        return RuntimeError(
            f"Zoho Books API error: HTTP {response.status_code} "
            f"on {request.method} {request.url}. "
            f"Zoho code: {zoho_code} - {zoho_message}"
        )

    @staticmethod
    def _normalize_organization(org: dict[str, Any]) -> dict[str, Any]:
        address = org.get("address") if isinstance(org.get("address"), dict) else {}
        return {
            **org,
            "organization_id": org.get("organization_id"),
            "name": org.get("name"),
            "gstin": org.get("gstin") or org.get("gst_no"),
            "pan_number": org.get("pan_number") or org.get("pan"),
            "tan_number": org.get("tan_number") or org.get("tan"),
            "fiscal_year_start_month": org.get("fiscal_year_start_month"),
            "currency_code": org.get("currency_code"),
            "time_zone": org.get("time_zone"),
            "address": {
                "address_line1": address.get("address_line1"),
                "city": address.get("city"),
                "state": address.get("state"),
                "zip": address.get("zip"),
                "country": address.get("country"),
            },
            "contact_name": org.get("contact_name"),
            "phone": org.get("phone"),
        }

    async def execute_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute tool with org_id pre-flight + automatic 401 retry.

        - Runs ``_ensure_org_id()`` first (RU-May01-BUG-02 lazy fallback).
        - Runs the tool. On 401, re-authenticate and retry once.
        """
        await self._ensure_org_id()
        try:
            return await super().execute_tool(tool_name, params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise self._runtime_error_from_http_status(exc) from exc
            logger.info("zoho_books_401_retry", tool=tool_name)
            await self._authenticate()
            if self._client:
                await self._client.aclose()
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_ms / 1000,
                headers=self._auth_headers,
            )
            try:
                return await super().execute_tool(tool_name, params)
            except httpx.HTTPStatusError as retry_exc:
                raise self._runtime_error_from_http_status(retry_exc) from retry_exc

    def _set_org_id(self, value: Any) -> None:
        org_id = _normalise_org_id(value)
        if org_id:
            self._org_id = org_id
            self.config["organization_id"] = org_id

    def _org_id_from_params(self, params: dict[str, Any] | None = None) -> tuple[str, str]:
        """Resolve the Zoho organization for one API call.

        QA reopened this because ``get_organization`` returned valid org ids,
        but downstream tools ignored an explicit ``organization_id`` supplied
        by the caller and kept using a stale configured/default org. Explicit
        call params must therefore win over connector state.
        """
        source_params = params or {}
        for key in _ORG_ID_KEYS:
            if key in source_params:
                org_id = _normalise_org_id(source_params.get(key))
                if org_id:
                    return org_id, key
        org_id = _normalise_org_id(self._org_id or self.config.get("organization_id"))
        return (org_id, "connector_config") if org_id else ("", "missing")

    def _copy_org_aliases(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            key: params[key]
            for key in _ORG_ID_KEYS
            if key in params and _normalise_org_id(params.get(key))
        }

    def _clean_non_org_params(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            str(k): v
            for k, v in (params or {}).items()
            if v is not None and str(k) not in _ORG_ID_KEYS
        }

    def _org_params(
        self,
        extra: dict[str, Any] | None = None,
        *,
        source_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build query params with a normalized organization_id when available."""
        params = self._clean_non_org_params(extra)
        org_id, source = self._org_id_from_params(source_params or extra)
        if org_id:
            params["organization_id"] = org_id
            self._set_org_id(org_id)
        logger.debug(
            "zoho_books_org_context_resolved",
            source=source,
            org_id=_redact_org_id(org_id),
        )
        return params

    def _unwrap(self, data: dict[str, Any], key: str | None = None) -> dict[str, Any]:
        """Unwrap Zoho response envelope: {"code": 0, "message": "success", "<key>": {...}}.

        Returns the inner object for the given key, or strips code/message from the dict.
        """
        if not isinstance(data, dict):
            return data
        if key and key in data:
            return data[key]
        # Auto-detect: return first value that is a dict or list (skip code/message)
        for k, v in data.items():
            if k not in ("code", "message") and isinstance(v, (dict, list)):
                return v
        return data

    @staticmethod
    def _looks_like_visible_reference(value: Any, prefixes: tuple[str, ...]) -> bool:
        text = str(value or "").strip().upper()
        return any(text.startswith(prefix) for prefix in prefixes)

    @staticmethod
    def _first_param(params: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = params.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""

    @staticmethod
    def _pick_exact_record(
        records: list[dict[str, Any]],
        reference: str,
        *,
        id_key: str,
        reference_keys: tuple[str, ...],
    ) -> dict[str, Any]:
        needle = str(reference or "").strip().lower()
        if not needle:
            return {}
        exact = [
            record
            for record in records
            if any(str(record.get(key, "")).strip().lower() == needle for key in reference_keys)
        ]
        if len(exact) == 1:
            return exact[0]
        if not exact and len(records) == 1:
            return records[0]
        if exact or records:
            candidates = exact if exact else records
            return {
                "error": "ambiguous_reference",
                "message": (
                    f"Reference matched {len(candidates)} records; pass the internal {id_key}."
                ),
                "candidates": [
                    {key: record.get(key) for key in (id_key, *reference_keys) if record.get(key)}
                    for record in candidates[:5]
                ],
            }
        return {}

    async def health_check(self) -> dict[str, Any]:
        """Verify connectivity by listing organizations."""
        if not self._has_credentials():
            return {
                "status": "not_configured",
                "reason": "No Zoho Books OAuth credentials configured",
            }
        try:
            data = await self._get("/organizations")
            orgs = data.get("organizations", [])
            if not orgs:
                return {
                    "status": "unhealthy",
                    "organizations": 0,
                    "error": "no_organizations",
                }
            return {"status": "healthy", "organizations": len(orgs)}
        # enterprise-gate: broad-except-ok reason=connector-health-boundary-reports-unhealthy
        except Exception as e:
            return {"status": "unhealthy", "error": type(e).__name__, "message": str(e)}

    # ── Invoices ───────────────────────────────────────────────────────

    async def create_invoice(self, **params) -> dict[str, Any]:
        """Create a new invoice in Zoho Books.

        Required: customer_id, line_items (list of dicts with item_id, rate, quantity).
        Optional: date, due_date, gst_treatment, place_of_supply.
        """
        customer_id = params.get("customer_id")
        if not customer_id:
            return {"error": "customer_id is required"}
        line_items = params.get("line_items")
        if not line_items:
            return {"error": "line_items is required"}

        body: dict[str, Any] = {
            "customer_id": customer_id,
            "line_items": line_items,
        }
        for field in ("date", "due_date", "gst_treatment", "place_of_supply"):
            if params.get(field):
                body[field] = params[field]

        data = await self._post(
            "/invoices",
            data=body,
            params=self._org_params(source_params=params),
        )
        # Inject org_id as query param via raw request override
        return self._unwrap(data, "invoice")

    async def list_invoices(self, **params) -> dict[str, Any]:
        """List invoices with optional filters.

        Optional: status, date_start, date_end, customer_id, page, search_text.
        """
        qp: dict[str, Any] = {}
        if params.get("status"):
            qp["status"] = params["status"]
        search_text = (
            params.get("search_text")
            or params.get("invoice_number")
            or params.get("invoice_no")
            or params.get("reference_number")
            or params.get("number")
        )
        if search_text:
            qp["search_text"] = search_text
        if params.get("date_start"):
            qp["date_start"] = params["date_start"]
        if params.get("date_end"):
            qp["date_end"] = params["date_end"]
        if params.get("customer_id"):
            qp["customer_id"] = params["customer_id"]
        if params.get("page"):
            qp["page"] = params["page"]

        data = await self._get("/invoices", params=self._org_params(qp, source_params=params))
        invoices = self._unwrap(data, "invoices")
        return {
            "invoices": invoices if isinstance(invoices, list) else [],
            "page_context": data.get("page_context", {}),
        }

    async def search_invoices(self, **params) -> dict[str, Any]:
        """Search invoices by visible invoice/reference number or text."""
        query = self._first_param(
            params,
            "query",
            "search_text",
            "invoice_number",
            "invoice_no",
            "reference_number",
            "number",
        )
        if not query:
            return {"error": "query is required"}
        next_params = dict(params)
        next_params["search_text"] = query
        return await self.list_invoices(**next_params)

    async def _resolve_invoice_id_from_reference(self, reference: str) -> dict[str, Any]:
        search_result = await self.search_invoices(query=reference)
        invoices = search_result.get("invoices", [])
        if not isinstance(invoices, list) or not invoices:
            return {
                "error": "invoice_reference_not_found",
                "message": f"No invoice found for reference {reference!r}.",
            }
        record = self._pick_exact_record(
            [r for r in invoices if isinstance(r, dict)],
            reference,
            id_key="invoice_id",
            reference_keys=("invoice_id", "invoice_number", "number", "reference_number"),
        )
        if record.get("error"):
            return record
        invoice_id = record.get("invoice_id") or record.get("id")
        if not invoice_id:
            return {
                "error": "invoice_id_missing",
                "message": f"Invoice search found {reference!r} but did not return invoice_id.",
                "invoice": record,
            }
        return {"invoice_id": str(invoice_id), "invoice": record}

    async def get_invoice_by_id(self, **params) -> dict[str, Any]:
        """Fetch one invoice by internal invoice_id, resolving visible references first."""
        invoice_id = self._first_param(params, "invoice_id", "id")
        reference = self._first_param(
            params,
            "invoice_number",
            "invoice_no",
            "reference_number",
            "number",
        )
        if invoice_id and self._looks_like_visible_reference(invoice_id, ("INV-",)):
            reference = reference or invoice_id
            invoice_id = ""
        if not invoice_id:
            if not reference:
                return {"error": "invoice_id or invoice_number is required"}
            resolved = await self._resolve_invoice_id_from_reference(reference)
            if resolved.get("error"):
                return resolved
            invoice_id = str(resolved["invoice_id"])

        data = await self._get(f"/invoices/{invoice_id}", params=self._org_params(source_params=params))
        invoice = self._unwrap(data, "invoice")
        if isinstance(invoice, dict) and reference:
            invoice.setdefault("resolved_from_reference", reference)
        return {"invoice": invoice, **invoice} if isinstance(invoice, dict) else {"invoice": invoice}

    async def list_bills(self, **params) -> dict[str, Any]:
        """Alias for list_vendor_bills."""
        return await self.list_vendor_bills(**params)

    async def list_vendor_bills(self, **params) -> dict[str, Any]:
        """List vendor bills / purchase invoices from Zoho Books.

        Optional: vendor_id, status, date_start, date_end, bill_number, search_text, page.
        """
        qp: dict[str, Any] = {}
        search_text = (
            params.get("search_text")
            or params.get("query")
            or params.get("bill_number")
            or params.get("bill_no")
            or params.get("reference_number")
            or params.get("number")
        )
        if search_text:
            qp["search_text"] = search_text
        for field in ("vendor_id", "status", "date_start", "date_end", "bill_number", "page"):
            if params.get(field):
                qp[field] = params[field]
        data = await self._get("/bills", params=self._org_params(qp, source_params=params))
        bills = self._unwrap(data, "bills")
        bill_list = bills if isinstance(bills, list) else []
        return {
            "bills": bill_list,
            "vendor_bills": bill_list,
            "page_context": data.get("page_context", {}),
        }

    async def search_bills(self, **params) -> dict[str, Any]:
        """Search vendor bills by visible bill/reference number or text."""
        query = self._first_param(
            params,
            "query",
            "search_text",
            "bill_number",
            "bill_no",
            "reference_number",
            "number",
        )
        if not query:
            return {"error": "query is required"}
        next_params = dict(params)
        next_params["search_text"] = query
        return await self.list_vendor_bills(**next_params)

    async def _resolve_bill_id_from_reference(self, reference: str) -> dict[str, Any]:
        search_result = await self.search_bills(query=reference)
        bills = search_result.get("bills", [])
        if not isinstance(bills, list) or not bills:
            return {
                "error": "bill_reference_not_found",
                "message": f"No bill found for reference {reference!r}.",
            }
        record = self._pick_exact_record(
            [r for r in bills if isinstance(r, dict)],
            reference,
            id_key="bill_id",
            reference_keys=("bill_id", "bill_number", "number", "reference_number"),
        )
        if record.get("error"):
            return record
        bill_id = record.get("bill_id") or record.get("id")
        if not bill_id:
            return {
                "error": "bill_id_missing",
                "message": f"Bill search found {reference!r} but did not return bill_id.",
                "bill": record,
            }
        return {"bill_id": str(bill_id), "bill": record}

    async def get_bill_by_id(self, **params) -> dict[str, Any]:
        """Fetch one vendor bill by internal bill_id, resolving visible references first."""
        bill_id = self._first_param(params, "bill_id", "id")
        reference = self._first_param(
            params,
            "bill_number",
            "bill_no",
            "reference_number",
            "number",
        )
        if bill_id and self._looks_like_visible_reference(bill_id, ("BILL-",)):
            reference = reference or bill_id
            bill_id = ""
        if not bill_id:
            if not reference:
                return {"error": "bill_id or bill_number is required"}
            resolved = await self._resolve_bill_id_from_reference(reference)
            if resolved.get("error"):
                return resolved
            bill_id = str(resolved["bill_id"])

        data = await self._get(f"/bills/{bill_id}", params=self._org_params(source_params=params))
        bill = self._unwrap(data, "bill")
        if isinstance(bill, dict) and reference:
            bill.setdefault("resolved_from_reference", reference)
        return {"bill": bill, **bill} if isinstance(bill, dict) else {"bill": bill}

    async def get_purchase_invoices(self, **params) -> dict[str, Any]:
        """Alias for list_vendor_bills used by accounting agents."""
        return await self.list_vendor_bills(**params)

    # ── Expenses ───────────────────────────────────────────────────────

    async def list_overdue_invoices(self, **params) -> dict[str, Any]:
        """List overdue invoices from Zoho Books.

        Optional: customer_id, page.
        """
        query = dict(params)
        query["status"] = "overdue"
        return await self.list_invoices(**query)

    async def list_expense_transactions(self, **params) -> dict[str, Any]:
        """List expense transactions from Zoho Books.

        Optional: vendor_id, account_id, date_start, date_end, status, page.
        """
        qp: dict[str, Any] = {}
        for field in ("vendor_id", "account_id", "date_start", "date_end", "status", "page"):
            if params.get(field):
                qp[field] = params[field]
        data = await self._get("/expenses", params=self._org_params(qp, source_params=params))
        expenses = self._unwrap(data, "expenses")
        expense_list = expenses if isinstance(expenses, list) else []
        return {
            "expenses": expense_list,
            "expense_transactions": expense_list,
            "page_context": data.get("page_context", {}),
        }

    async def get_expense_transactions(self, **params) -> dict[str, Any]:
        """Alias for list_expense_transactions."""
        return await self.list_expense_transactions(**params)

    async def record_expense(self, **params) -> dict[str, Any]:
        """Record an expense in Zoho Books.

        Required: account_id, amount, date.
        Optional: vendor_id, description.
        """
        for required in ("account_id", "amount", "date"):
            if not params.get(required):
                return {"error": f"{required} is required"}

        body: dict[str, Any] = {
            "account_id": params["account_id"],
            "amount": params["amount"],
            "date": params["date"],
        }
        for field in ("vendor_id", "description"):
            if params.get(field):
                body[field] = params[field]

        data = await self._post(
            "/expenses",
            data=body,
            params=self._org_params(source_params=params),
        )
        return self._unwrap(data, "expense")

    async def get_vendor_payables(self, **params) -> dict[str, Any]:
        """Return unpaid/open vendor bills for payable and TDS workflows."""
        query = dict(params)
        query.setdefault("status", "open")
        bills = await self.list_vendor_bills(**query)
        return {
            "payables": bills.get("bills", []),
            "vendor_payables": bills.get("bills", []),
            **bills,
            "page_context": bills.get("page_context", {}),
        }

    async def list_vendors(self, **params) -> dict[str, Any]:
        """List vendor contacts, including PAN/GST fields where Zoho returns them.

        Optional: search_text, status, page.
        """
        qp: dict[str, Any] = {"contact_type": "vendor"}
        for field in ("search_text", "status", "page"):
            if params.get(field):
                qp[field] = params[field]
        data = await self._get("/contacts", params=self._org_params(qp, source_params=params))
        contacts = self._unwrap(data, "contacts")
        return {
            "vendors": contacts if isinstance(contacts, list) else [],
            "page_context": data.get("page_context", {}),
        }

    async def create_vendor(self, **params) -> dict[str, Any]:
        """Create a vendor contact in Zoho Books."""
        contact_name = (
            params.get("contact_name")
            or params.get("vendor_name")
            or params.get("name")
            or params.get("company_name")
        )
        if not contact_name:
            return {"error": "contact_name or vendor_name is required"}

        body: dict[str, Any] = {
            "contact_name": contact_name,
            "contact_type": "vendor",
        }
        for field in (
            "company_name",
            "website",
            "gst_no",
            "gst_treatment",
            "place_of_contact",
            "billing_address",
            "shipping_address",
        ):
            if params.get(field) is not None:
                body[field] = params[field]

        contact_persons = params.get("contact_persons")
        if isinstance(contact_persons, list) and contact_persons:
            body["contact_persons"] = contact_persons
        else:
            person: dict[str, Any] = {}
            for src, dest in (
                ("first_name", "first_name"),
                ("last_name", "last_name"),
                ("email", "email"),
                ("phone", "phone"),
                ("mobile", "mobile"),
            ):
                if params.get(src):
                    person[dest] = params[src]
            if person:
                body["contact_persons"] = [person]

        data = await self._post(
            "/contacts",
            data=body,
            params=self._org_params(source_params=params),
        )
        return self._unwrap(data, "contact")

    async def create_item(self, **params) -> dict[str, Any]:
        """Create an item in Zoho Books."""
        item_name = params.get("name") or params.get("item_name")
        if not item_name:
            return {"error": "name or item_name is required"}
        if params.get("rate") is None:
            return {"error": "rate is required"}

        body: dict[str, Any] = {
            "name": item_name,
            "rate": params["rate"],
        }
        for field in (
            "description",
            "sku",
            "purchase_rate",
            "vendor_id",
            "account_id",
            "purchase_account_id",
            "item_type",
            "product_type",
            "hsn_or_sac",
        ):
            if params.get(field) is not None:
                body[field] = params[field]

        data = await self._post(
            "/items",
            data=body,
            params=self._org_params(source_params=params),
        )
        return self._unwrap(data, "item")

    async def create_bill(self, **params) -> dict[str, Any]:
        """Create a vendor bill in Zoho Books."""
        vendor_id = params.get("vendor_id")
        if not vendor_id:
            return {"error": "vendor_id is required"}
        line_items = params.get("line_items")
        if not line_items:
            return {"error": "line_items is required"}

        body: dict[str, Any] = {
            "vendor_id": vendor_id,
            "line_items": line_items,
        }
        for field in (
            "bill_number",
            "date",
            "due_date",
            "reference_number",
            "notes",
            "payment_terms",
        ):
            if params.get(field) is not None:
                body[field] = params[field]

        data = await self._post(
            "/bills",
            data=body,
            params=self._org_params(source_params=params),
        )
        return self._unwrap(data, "bill")

    async def get_vendor_details(self, **params) -> dict[str, Any]:
        """Fetch one vendor/contact master record."""
        vendor_id = params.get("vendor_id") or params.get("contact_id") or params.get("id")
        if not vendor_id:
            return {"error": "vendor_id is required"}
        data = await self._get(
            f"/contacts/{vendor_id}",
            params=self._org_params(source_params=params),
        )
        contact = self._unwrap(data, "contact")
        if isinstance(contact, dict):
            return {"vendor": contact, "contact": contact, **contact}
        return {"vendor": contact, "contact": contact}

    async def create_journal_entry(self, **params) -> dict[str, Any]:
        """Create a Zoho Books journal entry.

        Required: journal_date/date, line_items. Optional: notes, reference_number.
        """
        journal_date = params.get("journal_date") or params.get("date")
        if not journal_date:
            return {"error": "journal_date is required"}
        line_items = (
            params.get("line_items")
            or params.get("journal_line_items")
            or params.get("journal_lines")
        )
        if not isinstance(line_items, list) or not line_items:
            return {"error": "line_items is required"}
        body: dict[str, Any] = {"journal_date": journal_date, "line_items": line_items}
        for field in (
            "notes",
            "reference_number",
            "journal_type",
            "currency_id",
            "exchange_rate",
            "custom_fields",
            "tags",
        ):
            if params.get(field) is not None:
                body[field] = params[field]
        data = await self._post(
            "/journals",
            data=body,
            params=self._org_params(source_params=params),
        )
        return self._unwrap(data, "journal")

    async def create_tds_entry(self, **params) -> dict[str, Any]:
        """Create a TDS accounting journal entry."""
        if params.get("line_items") or params.get("journal_line_items") or params.get("journal_lines"):
            return await self.create_journal_entry(**params)
        journal_date = params.get("journal_date") or params.get("date")
        if not journal_date:
            return {"error": "journal_date is required"}
        for required in ("expense_account_id", "vendor_account_id", "tds_payable_account_id"):
            if params.get(required) in (None, ""):
                return {"error": f"{required} is required"}
        if params.get("amount") in (None, "") and params.get("gross_amount") in (None, ""):
            return {"error": "amount is required"}
        if params.get("tds_amount") in (None, ""):
            return {"error": "tds_amount is required"}
        try:
            gross_amount = float(params.get("gross_amount") or params["amount"])
            tds_amount = float(params["tds_amount"])
        except (TypeError, ValueError):
            return {"error": "amount and tds_amount must be numeric"}
        if gross_amount <= 0 or tds_amount < 0 or tds_amount > gross_amount:
            return {"error": "tds_amount must be between 0 and amount"}
        vendor_credit = round(gross_amount - tds_amount, 2)
        line_items = [
            {
                "account_id": params["expense_account_id"],
                "debit_or_credit": "debit",
                "amount": round(gross_amount, 2),
                "description": params.get("description", "Expense gross amount"),
            },
            {
                "account_id": params["vendor_account_id"],
                **({"customer_id": params["vendor_id"]} if params.get("vendor_id") else {}),
                "debit_or_credit": "credit",
                "amount": vendor_credit,
                "description": params.get("vendor_description", "Vendor net payable"),
            },
            {
                "account_id": params["tds_payable_account_id"],
                "debit_or_credit": "credit",
                "amount": round(tds_amount, 2),
                "description": params.get("tds_description", "TDS payable"),
            },
        ]
        return await self.create_journal_entry(
            journal_date=journal_date,
            line_items=line_items,
            notes=params.get("notes") or "TDS deduction entry",
            reference_number=params.get("reference_number"),
            **self._copy_org_aliases(params),
        )

    async def update_bill(self, **params) -> dict[str, Any]:
        """Update a vendor bill in Zoho Books."""
        bill_id = params.get("bill_id") or params.get("id")
        if not bill_id:
            return {"error": "bill_id is required"}
        body = {
            key: value
            for key, value in params.items()
            if key not in {"bill_id", "id", *_ORG_ID_KEYS} and value is not None
        }
        if not body:
            return {"error": "at least one field is required"}
        data = await self._put(
            f"/bills/{bill_id}",
            data=body,
            params=self._org_params(source_params=params),
        )
        return self._unwrap(data, "bill")

    # ── Reports ────────────────────────────────────────────────────────

    async def get_balance_sheet(self, **params) -> dict[str, Any]:
        """Get balance sheet report.

        Optional: date (as_of_date, YYYY-MM-DD).
        """
        qp: dict[str, Any] = {}
        if params.get("date"):
            qp["date"] = params["date"]
        data = await self._get(
            "/reports/balancesheet",
            params=self._org_params(qp, source_params=params),
        )
        return self._unwrap(data, "balance_sheet")

    async def get_profit_loss(self, **params) -> dict[str, Any]:
        """Get profit and loss report.

        Optional: from_date, to_date (YYYY-MM-DD).
        """
        qp: dict[str, Any] = {}
        if params.get("from_date"):
            qp["from_date"] = params["from_date"]
        if params.get("to_date"):
            qp["to_date"] = params["to_date"]
        data = await self._get(
            "/reports/profitandloss",
            params=self._org_params(qp, source_params=params),
        )
        return self._unwrap(data, "profit_and_loss")

    # ── Organization details ───────────────────────────────────────────

    async def get_ledger_balance(self, **params) -> dict[str, Any]:
        """Get a ledger report or account balance from Zoho Books.

        Optional: account_id, account_name, from_date, to_date.
        """
        qp: dict[str, Any] = {}
        for field in ("account_id", "account_name", "from_date", "to_date"):
            if params.get(field):
                qp[field] = params[field]
        data = await self._get("/reports/ledger", params=self._org_params(qp, source_params=params))
        ledger = self._unwrap(data, "ledger")
        if isinstance(ledger, dict):
            return ledger
        return {"ledger": ledger}

    async def get_trial_balance(self, **params) -> dict[str, Any]:
        """Get the trial balance report from Zoho Books.

        Optional: date, from_date, to_date.
        """
        qp: dict[str, Any] = {}
        for field in ("date", "from_date", "to_date"):
            if params.get(field):
                qp[field] = params[field]
        data = await self._get(
            "/reports/trialbalance",
            params=self._org_params(qp, source_params=params),
        )
        return self._unwrap(data, "trial_balance")

    async def generate_gst_report(self, **params) -> dict[str, Any]:
        """Get a GST summary report from Zoho Books.

        Optional: from_date, to_date, gstin.
        """
        qp: dict[str, Any] = {}
        for field in ("from_date", "to_date", "gstin"):
            if params.get(field):
                qp[field] = params[field]

        if self.config.get("region") == "in" or self.base_url == _ZOHO_IN_BASE:
            raise RuntimeError(
                "GST reports (GSTR-1, GSTR-3B, GSTR-2A) are not available "
                "via the Zoho Books India /reports/gstsummary endpoint. "
                "Use the GSTN connector for statutory GST reports; use "
                "list_invoices only when you explicitly need invoice source data."
            )
        query = self._org_params(qp, source_params=params)
        data = await self._get("/reports/gstsummary", params=query)
        return self._unwrap(data, "gst_summary")

    async def calculate_tds(self, **params) -> dict[str, Any]:
        """Calculate Indian TDS for a supplied transaction.

        Required: amount, section. Optional: deductee_type, pan_available.
        This is a deterministic calculation helper and does not file a return.
        """
        try:
            amount = float(params.get("amount") or params.get("payment_amount") or 0)
        except (TypeError, ValueError):
            return {"error": "amount must be numeric"}
        if amount <= 0:
            return {"error": "amount is required"}

        section = str(params.get("section") or "").upper().replace("SECTION", "").strip()
        section = section.replace(" ", "")
        deductee_type = str(params.get("deductee_type") or "individual").lower()
        pan_available = _as_bool(params.get("pan_available"), True)

        section_rates = {
            "194A": 0.10,
            "194C": 0.01 if deductee_type in {"individual", "huf"} else 0.02,
            "194H": 0.05,
            "194I": 0.10,
            "194J": 0.10,
            "194O": 0.01,
            "194Q": 0.001,
        }
        if section not in section_rates:
            return {"error": f"unsupported TDS section: {section or 'missing'}"}

        rate = max(section_rates[section], 0.20) if not pan_available else section_rates[section]
        tds_amount = round(amount * rate, 2)
        return {
            "status": "calculated",
            "section": section,
            "amount": amount,
            "rate": rate,
            "tds_amount": tds_amount,
            "net_payable": round(amount - tds_amount, 2),
            "filing_required": True,
        }

    async def get_organization(self, **params) -> dict[str, Any]:
        """Get organization details (name, organization_id, address, currency).

        Returns the active organization from the connected Zoho Books
        account. Useful for agents whose prompts reference the
        company by name or need the organization_id for follow-up calls.
        If a caller supplies organization_id, the connector records that
        as the active org for later calls in the same session.
        """
        explicit_org_id = ""
        query: dict[str, Any] | None = None
        if any(key in params for key in _ORG_ID_KEYS):
            explicit_org_id, _source = self._org_id_from_params(params)
            if explicit_org_id:
                query = {"organization_id": explicit_org_id}

        data = await super()._get("/organizations", query)
        self._raise_for_zoho_error(data, "/organizations")
        orgs = data.get("organizations", []) if isinstance(data, dict) else []
        if not orgs:
            raise RuntimeError(
                "Zoho Books returned no organizations. Ensure access_token, "
                "refresh_token, client_id, client_secret, and organization_id "
                "are configured."
            )
        if explicit_org_id:
            self._set_org_id(explicit_org_id)
            orgs = [
                org
                for org in orgs
                if str(org.get("organization_id") or "") == str(explicit_org_id)
            ]
            if not orgs:
                raise RuntimeError(
                    f"Zoho Books organization_id {explicit_org_id} was not found "
                    "in the authenticated account."
                )
        else:
            configured_org_id = _normalise_org_id(
                self._org_id or self.config.get("organization_id")
            )
            if configured_org_id:
                configured_orgs = [
                    org
                    for org in orgs
                    if str(org.get("organization_id") or "") == str(configured_org_id)
                ]
                if configured_orgs:
                    orgs = configured_orgs
            self._set_org_id(orgs[0].get("organization_id"))
        return {"organizations": [self._normalize_organization(orgs[0])]}

    # ── Chart of Accounts ──────────────────────────────────────────────

    async def list_chartofaccounts(self, **params) -> dict[str, Any]:
        """List chart of accounts.

        Optional: filter_by (e.g. AccountType.asset, AccountType.expense).
        """
        qp: dict[str, Any] = {}
        if params.get("filter_by"):
            qp["filter_by"] = params["filter_by"]
        data = await self._get(
            "/chartofaccounts",
            params=self._org_params(qp, source_params=params),
        )
        accounts = self._unwrap(data, "chartofaccounts")
        return {
            "chartofaccounts": accounts if isinstance(accounts, list) else [],
        }

    # ── Bank Reconciliation ────────────────────────────────────────────

    async def fetch_bank_statement(self, **params) -> dict[str, Any]:
        """Fetch bank transactions from Zoho Books.

        Optional: account_id, from_date, to_date, page.
        """
        return await self.get_transaction_list(**params)

    async def check_account_balance(self, **params) -> dict[str, Any]:
        """Fetch bank account balance details from Zoho Books.

        Optional: account_id.
        """
        account_id = params.get("account_id")
        path = f"/bankaccounts/{account_id}" if account_id else "/bankaccounts"
        data = await self._get(path, params=self._org_params(source_params=params))
        if account_id:
            return self._unwrap(data, "bankaccount")
        accounts = self._unwrap(data, "bankaccounts")
        return {"bankaccounts": accounts if isinstance(accounts, list) else []}

    async def get_transaction_list(self, **params) -> dict[str, Any]:
        """List bank transactions from Zoho Books.

        Optional: account_id, from_date, to_date, page.
        """
        qp: dict[str, Any] = {}
        for field in ("account_id", "from_date", "to_date", "page"):
            if params.get(field):
                qp[field] = params[field]
        data = await self._get(
            "/banktransactions",
            params=self._org_params(qp, source_params=params),
        )
        transactions = self._unwrap(data, "banktransactions")
        return {
            "transactions": transactions if isinstance(transactions, list) else [],
            "page_context": data.get("page_context", {}),
        }

    async def reconcile_bank(self, **params) -> dict[str, Any]:
        """Alias for reconcile_transaction used by CA pack prompts."""
        return await self.reconcile_transaction(**params)

    async def reconcile_transaction(self, **params) -> dict[str, Any]:
        """Match a bank transaction to an existing book transaction.

        Required: transaction_id, match_id. Optional: match_type.
        """
        transaction_id = params.get("transaction_id")
        match_id = params.get("match_id") or params.get("matched_transaction_id")
        if not transaction_id:
            return {"error": "transaction_id is required"}
        if not match_id:
            return {"error": "match_id is required"}

        body: dict[str, Any] = {
            "transactions_to_be_matched": [
                {
                    "transaction_id": match_id,
                    "transaction_type": params.get("match_type", "expense"),
                }
            ],
        }

        data = await self._put(
            f"/banktransactions/{transaction_id}/match",
            data=body,
            params=self._org_params(source_params=params),
        )
        return self._unwrap(data, "bank_transaction")

    # ── Internal: override _get/_post to inject organization_id ────────

    def _requires_org_id(self, path: str) -> bool:
        return path.rstrip("/") != "/organizations"

    async def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        """GET with organization_id injected for Zoho resource endpoints."""
        query = dict(params or {})
        if self._requires_org_id(path):
            query = self._org_params(query)
        else:
            query = self._clean_non_org_params(query)
            if params and any(key in params for key in _ORG_ID_KEYS):
                org_id, _source = self._org_id_from_params(params)
                if org_id:
                    query["organization_id"] = org_id
                    self._set_org_id(org_id)
        data = await super()._get(path, query or None)
        self._raise_for_zoho_error(data, path)
        return data

    async def _post(
        self,
        path: str,
        data: dict | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """POST with organization_id injected as query param.

        Zoho Books requires organization_id on the query string even for POST.
        """
        if not self._client:
            raise RuntimeError("Connector not connected")
        query = self._org_params(params)
        resp = await self._client.post(
            path,
            json=data,
            params=query,
        )
        resp.raise_for_status()
        body = resp.json()
        self._raise_for_zoho_error(body, path)
        return body

    async def _put(
        self,
        path: str,
        data: dict | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """PUT with organization_id injected as query param."""
        if not self._client:
            raise RuntimeError("Connector not connected")
        query = self._org_params(params)
        resp = await self._client.put(
            path,
            json=data,
            params=query,
        )
        resp.raise_for_status()
        body = resp.json()
        self._raise_for_zoho_error(body, path)
        return body
