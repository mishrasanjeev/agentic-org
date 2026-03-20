"""Jira connector — ops."""
from __future__ import annotations
import base64
from typing import Any
from connectors.framework.base_connector import BaseConnector

class JiraConnector(BaseConnector):
    name = "jira"
    category = "ops"
    auth_type = "oauth2"
    base_url = "https://org.atlassian.net/rest/api/3"
    rate_limit_rpm = 300

    def _register_tools(self):
    self._tool_registry["create_issue"] = self.create_issue
    self._tool_registry["update_issue"] = self.update_issue
    self._tool_registry["transition_issue"] = self.transition_issue
    self._tool_registry["get_sprint_data"] = self.get_sprint_data
    self._tool_registry["bulk_update"] = self.bulk_update
    self._tool_registry["get_project_metrics"] = self.get_project_metrics
    self._tool_registry["create_dashboard_widget"] = self.create_dashboard_widget

    async def _authenticate(self):
        email = self._get_secret("email")
        api_token = self._get_secret("api_token")
        credentials = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self._auth_headers = {"Authorization": f"Basic {credentials}"}

async def create_issue(self, **params):
    """Execute create_issue on jira."""
    return await self._post("/create/issue", params)


async def update_issue(self, **params):
    """Execute update_issue on jira."""
    return await self._post("/update/issue", params)


async def transition_issue(self, **params):
    """Execute transition_issue on jira."""
    return await self._post("/transition/issue", params)


async def get_sprint_data(self, **params):
    """Execute get_sprint_data on jira."""
    return await self._post("/get/sprint/data", params)


async def bulk_update(self, **params):
    """Execute bulk_update on jira."""
    return await self._post("/bulk/update", params)


async def get_project_metrics(self, **params):
    """Execute get_project_metrics on jira."""
    return await self._post("/get/project/metrics", params)


async def create_dashboard_widget(self, **params):
    """Execute create_dashboard_widget on jira."""
    return await self._post("/create/dashboard/widget", params)

