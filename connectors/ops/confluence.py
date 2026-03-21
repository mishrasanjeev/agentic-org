"""Confluence connector — ops."""

from __future__ import annotations

import base64

from connectors.framework.base_connector import BaseConnector


class ConfluenceConnector(BaseConnector):
    name = "confluence"
    category = "ops"
    auth_type = "oauth2"
    base_url = "https://org.atlassian.net/wiki/rest/api"
    rate_limit_rpm = 200

    def _register_tools(self):
        self._tool_registry["create_page"] = self.create_page
        self._tool_registry["update_page_content"] = self.update_page_content
        self._tool_registry["search_content_fulltext"] = self.search_content_fulltext
        self._tool_registry["publish_from_template"] = self.publish_from_template
        self._tool_registry["manage_space_permissions"] = self.manage_space_permissions
        self._tool_registry["get_page_tree"] = self.get_page_tree

    async def _authenticate(self):
        email = self._get_secret("email")
        api_token = self._get_secret("api_token")
        credentials = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self._auth_headers = {"Authorization": f"Basic {credentials}"}

    async def create_page(self, **params):
        """Execute create_page on confluence."""
        return await self._post("/create/page", params)

    async def update_page_content(self, **params):
        """Execute update_page_content on confluence."""
        return await self._post("/update/page/content", params)

    async def search_content_fulltext(self, **params):
        """Execute search_content_fulltext on confluence."""
        return await self._post("/search/content/fulltext", params)

    async def publish_from_template(self, **params):
        """Execute publish_from_template on confluence."""
        return await self._post("/publish/from/template", params)

    async def manage_space_permissions(self, **params):
        """Execute manage_space_permissions on confluence."""
        return await self._post("/manage/space/permissions", params)

    async def get_page_tree(self, **params):
        """Execute get_page_tree on confluence."""
        return await self._post("/get/page/tree", params)
