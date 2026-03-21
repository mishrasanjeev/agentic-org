"""Github connector — comms."""

from __future__ import annotations

from connectors.framework.base_connector import BaseConnector


class GithubConnector(BaseConnector):
    name = "github"
    category = "comms"
    auth_type = "pat_oauth2"
    base_url = "https://api.github.com"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["create_pull_request"] = self.create_pull_request
        self._tool_registry["list_repository_issues"] = self.list_repository_issues
        self._tool_registry["trigger_github_action_workflow"] = self.trigger_github_action_workflow
        self._tool_registry["get_repository_statistics"] = self.get_repository_statistics
        self._tool_registry["create_release"] = self.create_release

    async def _authenticate(self):
        personal_access_token = self._get_secret("personal_access_token")
        self._auth_headers = {"Authorization": f"Bearer {personal_access_token}"}

    async def create_pull_request(self, **params):
        """Execute create_pull_request on github."""
        return await self._post("/create/pull/request", params)

    async def list_repository_issues(self, **params):
        """Execute list_repository_issues on github."""
        return await self._post("/list/repository/issues", params)

    async def trigger_github_action_workflow(self, **params):
        """Execute trigger_github_action_workflow on github."""
        return await self._post("/trigger/github/action/workflow", params)

    async def get_repository_statistics(self, **params):
        """Execute get_repository_statistics on github."""
        return await self._post("/get/repository/statistics", params)

    async def create_release(self, **params):
        """Execute create_release on github."""
        return await self._post("/create/release", params)
