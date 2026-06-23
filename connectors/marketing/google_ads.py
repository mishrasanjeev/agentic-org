# ruff: noqa: S608 — GAQL is not SQL; query construction is safe (sent to Google Ads API)
"""Google Ads connector — marketing.

Integrates with Google Ads API via the REST interface.
Google Ads uses GAQL (Google Ads Query Language) through a single
searchStream endpoint rather than individual REST paths per resource.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import httpx

from connectors.framework.base_connector import BaseConnector

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ARG_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?P<key>start[_\s-]?date|end[_\s-]?date|campaign[_\s-]?id|limit)\s*[:=]\s*(?P<value>[A-Za-z0-9_-]+)"
)
PARAM_WRAPPER_KEYS = (
    "kwargs",
    "params",
    "parameters",
    "arguments",
    "args",
    "input",
    "tool_input",
    "toolInput",
    "payload",
    "data",
)
TEXT_PARAM_KEYS = ("query", "prompt", "text", "message", "instruction", "instructions")


def _parse_tool_text(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for match in _ARG_ASSIGNMENT_RE.finditer(text):
        key = match.group("key").lower().replace(" ", "_").replace("-", "_")
        parsed[key] = match.group("value")
    return parsed


def _flatten_tool_params(params: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}

    def merge_payload(payload: dict[str, Any]) -> None:
        for key, value in payload.items():
            if key not in flattened or flattened[key] in (None, ""):
                flattened[key] = value
        for wrapper_key in PARAM_WRAPPER_KEYS:
            nested = payload.get(wrapper_key)
            if isinstance(nested, dict):
                merge_payload(nested)
            elif isinstance(nested, str) and nested.strip():
                text = nested.strip()
                try:
                    parsed = json.loads(text)
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict):
                    merge_payload(parsed)
                else:
                    flattened.update(_parse_tool_text(text))
        for text_key in TEXT_PARAM_KEYS:
            text_value = payload.get(text_key)
            if isinstance(text_value, str):
                flattened.update(_parse_tool_text(text_value))

    merge_payload(dict(params or {}))
    return flattened


def _first_value(params: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = params.get(name)
        if value not in (None, ""):
            return value
    return ""


def _normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if not _DATE_RE.match(text):
        return ""
    try:
        date.fromisoformat(text)
    except ValueError:
        return ""
    return text


def _date_range_or_error(params: dict[str, Any], *, tool: str) -> tuple[str, str, dict[str, Any] | None]:
    start_raw = _first_value(params, "start_date", "startDate", "date_from", "from_date", "from")
    end_raw = _first_value(params, "end_date", "endDate", "date_to", "to_date", "to")
    start = _normalize_date(start_raw)
    end = _normalize_date(end_raw)
    missing = [
        name
        for name, raw in (("start_date", start_raw), ("end_date", end_raw))
        if raw in (None, "")
    ]
    invalid = [
        name
        for name, raw, normalized in (
            ("start_date", start_raw, start),
            ("end_date", end_raw, end),
        )
        if raw not in (None, "") and not normalized
    ]
    if not missing and not invalid and date.fromisoformat(start) > date.fromisoformat(end):
        invalid.append("date_range")
    if missing or invalid:
        return "", "", {
            "error": "validation_failed",
            "tool": tool,
            "message": "start_date and end_date are required in YYYY-MM-DD format.",
            "expected_format": "YYYY-MM-DD",
            "missing_parameters": missing,
            "invalid_parameters": invalid,
        }
    return start, end, None


def _campaign_filter_or_error(params: dict[str, Any], *, tool: str) -> tuple[str, dict[str, Any] | None]:
    campaign_id = str(_first_value(params, "campaign_id", "campaignId", "campaign") or "").strip()
    if not campaign_id:
        return "", None
    if not campaign_id.isdigit():
        return "", {
            "error": "validation_failed",
            "tool": tool,
            "message": "campaign_id must be a numeric Google Ads campaign id.",
            "invalid_parameters": ["campaign_id"],
        }
    return f" AND campaign.id = {campaign_id}", None


def _safe_limit(value: Any, default: int, maximum: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, maximum))


class GoogleAdsConnector(BaseConnector):
    name = "google_ads"
    category = "marketing"
    auth_type = "oauth2"
    base_url = "https://googleads.googleapis.com/v24"
    rate_limit_rpm = 200

    def __init__(self, config: dict[str, Any] | None = None):
        safe_config = dict(config or {})
        safe_config.pop("base_url", None)
        super().__init__(safe_config)
        self._customer_id = self._normalize_customer_id(
            self.config.get("customer_id", "")
        )
        self._developer_token = self.config.get("developer_token", "")

    @staticmethod
    def _normalize_customer_id(value: Any) -> str:
        return str(value or "").replace("-", "").strip()

    def _register_tools(self):
        self._tool_registry["search_campaigns"] = self.search_campaigns
        self._tool_registry["get_campaign_performance"] = self.get_campaign_performance
        self._tool_registry["mutate_campaign_budget"] = self.mutate_campaign_budget
        self._tool_registry["get_search_terms"] = self.get_search_terms
        self._tool_registry["create_user_list"] = self.create_user_list

    async def _authenticate(self):
        client_id = self._get_secret("client_id")
        client_secret = self._get_secret("client_secret")
        refresh_token = self._get_secret("refresh_token")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]

        self._auth_headers = {
            "Authorization": f"Bearer {token}",
            "developer-token": self._developer_token,
        }
        if self.config.get("login_customer_id"):
            self._auth_headers["login-customer-id"] = self._normalize_customer_id(
                self.config["login_customer_id"]
            )

    async def _gaql_search(self, query: str) -> list[dict[str, Any]]:
        """Execute a GAQL query via the searchStream endpoint."""
        customer_id = self._customer_id
        resp = await self._post(
            f"/customers/{customer_id}/googleAds:searchStream",
            {"query": query},
        )
        if isinstance(resp, list):
            rows: list[dict[str, Any]] = []
            for batch in resp:
                rows.extend(batch.get("results", []))
            return rows
        return resp.get("results", [resp])

    async def health_check(self) -> dict[str, Any]:
        try:
            rows = await self._gaql_search(
                "SELECT customer.id, customer.descriptive_name FROM customer LIMIT 1"
            )
            customer = rows[0].get("customer", {}) if rows else {}
            return {
                "status": "healthy",
                "customer_id": self._customer_id,
                "customer": customer.get("resourceName", ""),
                "customer_name": customer.get("descriptiveName", ""),
            }
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            reason = (
                "Google Ads health check failed through googleAds:searchStream. "
                "Verify customer_id/login_customer_id, developer token approval, "
                "OAuth scopes, and that the configured Google Ads API version is supported."
            )
            if status == 404:
                reason = (
                    "Google Ads returned HTTP 404 for the customer searchStream probe. "
                    "This usually means the customer_id/login_customer_id is wrong for "
                    "the authorized account or the configured Google Ads API version is retired."
                )
            return {
                "status": "unhealthy",
                "http_status": status,
                "reason": reason,
                "error": "google_ads_health_check_failed",
            }
        # enterprise-gate: broad-except-ok reason=connector-health-boundary-reports-unhealthy
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def search_campaigns(self, **params) -> dict[str, Any]:
        """Search campaigns with optional status filter.

        Params: status (ENABLED/PAUSED/REMOVED), limit (default 50).
        """
        status_filter = ""
        if params.get("status"):
            status_filter = f" AND campaign.status = '{params['status']}'"
        limit = params.get("limit", 50)
        query = (
            "SELECT campaign.id, campaign.name, campaign.status, "  # noqa: S608  # nosec B608
            "campaign.advertising_channel_type, campaign_budget.amount_micros "
            f"FROM campaign WHERE campaign.status != 'REMOVED'{status_filter} "
            f"LIMIT {limit}"
        )
        rows = await self._gaql_search(query)
        return {"campaigns": rows, "total": len(rows)}

    async def get_campaign_performance(self, **params) -> dict[str, Any]:
        """Get campaign metrics for a date range.

        Params: start_date (YYYY-MM-DD), end_date, campaign_id (optional).
        """
        params = _flatten_tool_params(params)
        start, end, validation_error = _date_range_or_error(
            params,
            tool="get_campaign_performance",
        )
        if validation_error:
            return validation_error
        cid_filter, campaign_error = _campaign_filter_or_error(
            params,
            tool="get_campaign_performance",
        )
        if campaign_error:
            return campaign_error
        query = (
            "SELECT campaign.id, campaign.name, segments.date, "
            "metrics.impressions, metrics.clicks, metrics.cost_micros, "
            "metrics.conversions, metrics.cost_per_conversion "
            f"FROM campaign WHERE segments.date BETWEEN '{start}' AND '{end}'"  # noqa: S608  # nosec B608
            f"{cid_filter}"
        )
        rows = await self._gaql_search(query)
        return {"performance": rows, "date_range": {"start": start, "end": end}}

    async def mutate_campaign_budget(self, **params) -> dict[str, Any]:
        """Update a campaign budget.

        Params: campaign_budget_id (required), amount_micros (required).
        """
        customer_id = self._customer_id
        budget_id = params["campaign_budget_id"]
        amount = params["amount_micros"]
        return await self._post(
            f"/customers/{customer_id}/campaignBudgets:mutate",
            {
                "operations": [{
                    "update": {
                        "resourceName": f"customers/{customer_id}/campaignBudgets/{budget_id}",
                        "amountMicros": str(amount),
                    },
                    "updateMask": "amountMicros",
                }],
            },
        )

    async def get_search_terms(self, **params) -> dict[str, Any]:
        """Get search term performance report.

        Params: start_date, end_date, campaign_id (optional), limit (100).
        """
        params = _flatten_tool_params(params)
        start, end, validation_error = _date_range_or_error(
            params,
            tool="get_search_terms",
        )
        if validation_error:
            return validation_error
        cid_filter, campaign_error = _campaign_filter_or_error(
            params,
            tool="get_search_terms",
        )
        if campaign_error:
            return campaign_error
        limit = _safe_limit(params.get("limit"), 100, 1000)
        query = (
            "SELECT search_term_view.search_term, segments.date, "
            "metrics.impressions, metrics.clicks, metrics.cost_micros, "
            "metrics.conversions "
            f"FROM search_term_view WHERE segments.date BETWEEN '{start}' AND '{end}'"  # noqa: S608  # nosec B608
            f"{cid_filter} ORDER BY metrics.impressions DESC LIMIT {limit}"
        )
        rows = await self._gaql_search(query)
        return {"search_terms": rows, "total": len(rows)}

    async def create_user_list(self, **params) -> dict[str, Any]:
        """Create a remarketing user list.

        Params: name (required), description, membership_life_span_days (30).
        """
        customer_id = self._customer_id
        return await self._post(
            f"/customers/{customer_id}/userLists:mutate",
            {
                "operations": [{
                    "create": {
                        "name": params["name"],
                        "description": params.get("description", ""),
                        "membershipLifeSpan": str(params.get("membership_life_span_days", 30)),
                        "crmBasedUserList": {},
                    },
                }],
            },
        )
