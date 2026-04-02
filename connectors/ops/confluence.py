"""Confluence connector — ops.

Integrates with Atlassian Confluence Cloud REST API v2 for
knowledge base management, page creation, and content search.
"""

from __future__ import annotations

import base64
from typing import Any

from connectors.framework.base_connector import BaseConnector


class ConfluenceConnector(BaseConnector):
    name = "confluence"
    category = "ops"
    auth_type = "oauth2"
    base_url = "https://org.atlassian.net"
    rate_limit_rpm = 200

    def _register_tools(self):
        self._tool_registry["create_page"] = self.create_page
        self._tool_registry["update_page"] = self.update_page
        self._tool_registry["search_content"] = self.search_content
        self._tool_registry["get_page"] = self.get_page
        self._tool_registry["get_page_tree"] = self.get_page_tree
        self._tool_registry["list_spaces"] = self.list_spaces

    async def _authenticate(self):
        email = self._get_secret("email")
        api_token = self._get_secret("api_token")
        credentials = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self._auth_headers = {
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def health_check(self) -> dict[str, Any]:
        try:
            await self._get("/wiki/api/v2/spaces", {"limit": "1"})
            return {"status": "healthy"}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def create_page(self, **params) -> dict[str, Any]:
        """Create a new Confluence page.

        Params: spaceId (required), title (required),
                body (HTML content string), parentId (optional),
                status (current/draft).
        """
        body_content = params.pop("body", "")
        payload: dict[str, Any] = {
            "spaceId": params["spaceId"],
            "title": params["title"],
            "status": params.get("status", "current"),
            "body": {"representation": "storage", "value": body_content},
        }
        if params.get("parentId"):
            payload["parentId"] = params["parentId"]
        return await self._post("/wiki/api/v2/pages", payload)

    async def update_page(self, **params) -> dict[str, Any]:
        """Update an existing page.

        Params: page_id (required), title, body (HTML), version_number (required).
        """
        page_id = params.pop("page_id")
        body_content = params.pop("body", "")
        payload: dict[str, Any] = {
            "id": page_id,
            "status": "current",
            "title": params.get("title", ""),
            "body": {"representation": "storage", "value": body_content},
            "version": {"number": params["version_number"], "message": params.get("message", "Updated by AgenticOrg")},
        }
        return await self._put(f"/wiki/api/v2/pages/{page_id}", payload)

    async def search_content(self, **params) -> dict[str, Any]:
        """Search Confluence using CQL (Confluence Query Language).

        Params: cql (required — e.g., 'type=page AND text~"budget"'),
                limit (default 25).
        """
        params.setdefault("limit", "25")
        return await self._get("/wiki/api/v2/search", params)

    async def get_page(self, **params) -> dict[str, Any]:
        """Get a page by ID.

        Params: page_id (required), body_format (storage/atlas_doc_format, default storage).
        """
        page_id = params["page_id"]
        return await self._get(f"/wiki/api/v2/pages/{page_id}", {"body-format": params.get("body_format", "storage")})

    async def get_page_tree(self, **params) -> dict[str, Any]:
        """Get child pages of a parent page.

        Params: page_id (required), limit (default 50).
        """
        page_id = params["page_id"]
        return await self._get(f"/wiki/api/v2/pages/{page_id}/children", {"limit": str(params.get("limit", 50))})

    async def list_spaces(self, **params) -> dict[str, Any]:
        """List Confluence spaces.

        Params: type (global/personal), status (current/archived), limit (default 25).
        """
        params.setdefault("limit", "25")
        return await self._get("/wiki/api/v2/spaces", params)
