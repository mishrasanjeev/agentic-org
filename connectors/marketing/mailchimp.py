"""Mailchimp connector — real Mailchimp Marketing API v3.0 integration."""

from __future__ import annotations

import base64
from typing import Any

import httpx
import structlog

from connectors.framework.base_connector import BaseConnector

logger = structlog.get_logger()


class MailchimpConnector(BaseConnector):
    """Mailchimp email marketing connector using the Marketing API v3.0.

    Requires config keys:
        api_key — Mailchimp API key (ends with ``-us21`` or similar dc suffix)

    Optional config overrides:
        dc       — Data center override (extracted from api_key suffix by default)
        base_url — Full base URL if the default template is not suitable
    """

    name = "mailchimp"
    category = "marketing"
    auth_type = "basic"
    base_url = ""  # built dynamically from api_key dc suffix
    rate_limit_rpm = 100

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._api_key: str = self.config.get("api_key", "")
        if not self.base_url:
            dc = self.config.get("dc", "")
            if not dc and self._api_key and "-" in self._api_key:
                # Extract data center from API key suffix (e.g. "abc123-us21" -> "us21")
                dc = self._api_key.rsplit("-", 1)[-1]
            if dc:
                self.base_url = f"https://{dc}.api.mailchimp.com/3.0"

    # ── Tool registration ──────────────────────────────────────────────

    def _register_tools(self):
        self._tool_registry["list_campaigns"] = self.list_campaigns
        self._tool_registry["create_campaign"] = self.create_campaign
        self._tool_registry["send_campaign"] = self.send_campaign
        self._tool_registry["get_campaign_report"] = self.get_campaign_report
        self._tool_registry["add_list_member"] = self.add_list_member
        self._tool_registry["search_members"] = self.search_members
        self._tool_registry["create_template"] = self.create_template

    # ── Authentication ─────────────────────────────────────────────────

    async def _authenticate(self):
        """Authenticate using Mailchimp API key via HTTP Basic Auth.

        Mailchimp accepts Basic auth where the username can be any string
        and the password is the full API key.
        """
        api_key = self._api_key or self._get_secret("api_key")
        if api_key:
            credentials = base64.b64encode(
                f"anystring:{api_key}".encode()
            ).decode()
            self._auth_headers = {
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

    # ── Execute with 401 retry ─────────────────────────────────────────

    async def execute_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute tool with automatic 401 retry (re-authenticates and retries once)."""
        try:
            return await super().execute_tool(tool_name, params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            logger.info("mailchimp_401_retry", tool=tool_name)
            await self._authenticate()
            if self._client:
                await self._client.aclose()
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_ms / 1000,
                headers=self._auth_headers,
            )
            return await super().execute_tool(tool_name, params)

    # ── Health check ───────────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """Verify connectivity using the /ping endpoint.

        Mailchimp returns {"health_status": "Everything's Chimpy!"} on success.
        """
        try:
            data = await self._get("/ping")
            return {
                "status": "healthy",
                "health_status": data.get("health_status", ""),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # ── Campaigns ──────────────────────────────────────────────────────

    async def list_campaigns(self, **params) -> dict[str, Any]:
        """List email campaigns with filtering and pagination.

        Optional: count (default 10, max 1000), offset (default 0),
                  status (save/paused/schedule/sending/sent),
                  since_send_time (ISO 8601 datetime).
        """
        qp: dict[str, Any] = {
            "count": params.get("count", 10),
            "offset": params.get("offset", 0),
        }
        if params.get("status"):
            qp["status"] = params["status"]
        if params.get("since_send_time"):
            qp["since_send_time"] = params["since_send_time"]

        data = await self._get("/campaigns", params=qp)
        campaigns = data.get("campaigns", [])
        return {
            "campaigns": [
                {
                    "id": c.get("id"),
                    "type": c.get("type"),
                    "status": c.get("status"),
                    "subject_line": c.get("settings", {}).get("subject_line", ""),
                    "from_name": c.get("settings", {}).get("from_name", ""),
                    "send_time": c.get("send_time"),
                    "emails_sent": c.get("emails_sent", 0),
                    "list_id": c.get("recipients", {}).get("list_id", ""),
                }
                for c in campaigns
            ],
            "total_items": data.get("total_items", len(campaigns)),
        }

    async def create_campaign(self, **params) -> dict[str, Any]:
        """Create a new email campaign.

        Required: type (regular/plaintext/absplit/variate),
                  list_id (audience list ID),
                  subject_line, from_name, reply_to.
        Optional: segment_opts (dict with saved_segment_id, match, conditions),
                  preview_text, to_name.
        """
        campaign_type = params.get("type", "regular")
        list_id = params.get("list_id")
        if not list_id:
            return {"error": "list_id is required"}
        subject_line = params.get("subject_line")
        if not subject_line:
            return {"error": "subject_line is required"}
        from_name = params.get("from_name")
        if not from_name:
            return {"error": "from_name is required"}
        reply_to = params.get("reply_to")
        if not reply_to:
            return {"error": "reply_to is required"}

        recipients: dict[str, Any] = {"list_id": list_id}
        if params.get("segment_opts"):
            recipients["segment_opts"] = params["segment_opts"]

        settings: dict[str, Any] = {
            "subject_line": subject_line,
            "from_name": from_name,
            "reply_to": reply_to,
        }
        if params.get("preview_text"):
            settings["preview_text"] = params["preview_text"]
        if params.get("to_name"):
            settings["to_name"] = params["to_name"]

        body: dict[str, Any] = {
            "type": campaign_type,
            "recipients": recipients,
            "settings": settings,
        }

        return await self._post("/campaigns", body)

    async def send_campaign(self, **params) -> dict[str, Any]:
        """Send a campaign immediately.

        Required: campaign_id.

        Note: The campaign must be in "save" status and have content set.
        Mailchimp returns 204 No Content on success.
        """
        campaign_id = params.get("campaign_id")
        if not campaign_id:
            return {"error": "campaign_id is required"}

        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.post(
            f"/campaigns/{campaign_id}/actions/send",
            json={},
        )
        resp.raise_for_status()

        # Mailchimp returns 204 with empty body on success
        if resp.status_code == 204:
            return {"status": "sent", "campaign_id": campaign_id}
        return resp.json()

    # ── Reports ────────────────────────────────────────────────────────

    async def get_campaign_report(self, **params) -> dict[str, Any]:
        """Get performance report for a sent campaign.

        Required: campaign_id.
        Returns opens, clicks, bounces, unsubscribes, and more.
        """
        campaign_id = params.get("campaign_id")
        if not campaign_id:
            return {"error": "campaign_id is required"}

        data = await self._get(f"/reports/{campaign_id}")
        return {
            "campaign_id": data.get("id", campaign_id),
            "subject_line": data.get("subject_line", ""),
            "emails_sent": data.get("emails_sent", 0),
            "opens": {
                "total": data.get("opens", {}).get("opens_total", 0),
                "unique": data.get("opens", {}).get("unique_opens", 0),
                "rate": data.get("opens", {}).get("open_rate", 0),
            },
            "clicks": {
                "total": data.get("clicks", {}).get("clicks_total", 0),
                "unique": data.get("clicks", {}).get("unique_clicks", 0),
                "rate": data.get("clicks", {}).get("click_rate", 0),
            },
            "bounces": {
                "hard": data.get("bounces", {}).get("hard_bounces", 0),
                "soft": data.get("bounces", {}).get("soft_bounces", 0),
            },
            "unsubscribes": data.get("unsubscribed", 0),
            "send_time": data.get("send_time", ""),
        }

    # ── List Members ───────────────────────────────────────────────────

    async def add_list_member(self, **params) -> dict[str, Any]:
        """Add a member (subscriber) to an audience list.

        Required: list_id, email_address.
        Optional: status (subscribed/pending/unsubscribed/cleaned, default "pending"),
                  merge_fields (dict, e.g. {"FNAME": "John", "LNAME": "Doe"}),
                  tags (list of tag names), language, vip (bool).
        """
        list_id = params.get("list_id")
        if not list_id:
            return {"error": "list_id is required"}
        email = params.get("email_address")
        if not email:
            return {"error": "email_address is required"}

        body: dict[str, Any] = {
            "email_address": email,
            "status": params.get("status", "pending"),
        }
        if params.get("merge_fields"):
            body["merge_fields"] = params["merge_fields"]
        if params.get("tags"):
            body["tags"] = params["tags"]
        if params.get("language"):
            body["language"] = params["language"]
        if params.get("vip") is not None:
            body["vip"] = params["vip"]

        return await self._post(f"/lists/{list_id}/members", body)

    async def search_members(self, **params) -> dict[str, Any]:
        """Search for audience members by email or name.

        Required: query (email address or name to search).
        Optional: list_id (restrict to a specific list).
        """
        query = params.get("query", "")
        if not query:
            return {"error": "query is required"}

        qp: dict[str, Any] = {"query": query}
        if params.get("list_id"):
            qp["list_id"] = params["list_id"]

        data = await self._get("/search-members", params=qp)
        members = []
        for match in data.get("exact_matches", {}).get("members", []):
            members.append({
                "id": match.get("id"),
                "email": match.get("email_address"),
                "status": match.get("status"),
                "full_name": match.get("full_name", ""),
                "list_id": match.get("list_id"),
            })
        for match in data.get("full_search", {}).get("members", []):
            members.append({
                "id": match.get("id"),
                "email": match.get("email_address"),
                "status": match.get("status"),
                "full_name": match.get("full_name", ""),
                "list_id": match.get("list_id"),
            })

        return {
            "members": members,
            "total_results": len(members),
        }

    # ── Templates ──────────────────────────────────────────────────────

    async def create_template(self, **params) -> dict[str, Any]:
        """Create a reusable email template.

        Required: name (template name), html (full HTML content).
        Optional: folder_id (template folder ID).
        """
        name = params.get("name")
        if not name:
            return {"error": "name is required"}
        html = params.get("html")
        if not html:
            return {"error": "html is required"}

        body: dict[str, Any] = {
            "name": name,
            "html": html,
        }
        if params.get("folder_id"):
            body["folder_id"] = params["folder_id"]

        data = await self._post("/templates", body)
        return {
            "id": data.get("id"),
            "name": data.get("name"),
            "type": data.get("type", ""),
            "date_created": data.get("date_created", ""),
        }
