"""Jira connector — real Atlassian REST API v3 integration."""

from __future__ import annotations

import base64
from typing import Any

from connectors.framework.base_connector import BaseConnector


class JiraConnector(BaseConnector):
    name = "jira"
    category = "ops"
    auth_type = "oauth2"
    base_url = "https://org.atlassian.net"  # overridden per-tenant via config
    rate_limit_rpm = 300

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        # Allow tenant-specific Jira domain
        domain = (config or {}).get("domain", "")
        if domain:
            self.base_url = f"https://{domain}.atlassian.net"

    def _register_tools(self):
        self._tool_registry["list_projects"] = self.list_projects
        self._tool_registry["get_project"] = self.get_project
        self._tool_registry["search_issues"] = self.search_issues
        self._tool_registry["get_issue"] = self.get_issue
        self._tool_registry["create_issue"] = self.create_issue
        self._tool_registry["update_issue"] = self.update_issue
        self._tool_registry["transition_issue"] = self.transition_issue
        self._tool_registry["get_transitions"] = self.get_transitions
        self._tool_registry["add_comment"] = self.add_comment
        self._tool_registry["get_sprint_data"] = self.get_sprint_data
        self._tool_registry["get_project_metrics"] = self.get_project_metrics

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
            data = await self._get("/rest/api/3/myself")
            return {"status": "healthy", "user": data.get("displayName")}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # ── Read tools ──────────────────────────────────────────────────────

    async def list_projects(self, **params) -> dict[str, Any]:
        """List all accessible Jira projects."""
        data = await self._get("/rest/api/3/project")
        if not isinstance(data, list):
            return data
        return {
            "projects": [
                {
                    "key": p["key"],
                    "name": p["name"],
                    "project_type": p.get("projectTypeKey"),
                    "style": p.get("style"),
                }
                for p in data
            ],
            "count": len(data),
        }

    async def get_project(self, **params) -> dict[str, Any]:
        """Get project details by key."""
        key = params.get("project_key", "")
        if not key:
            return {"error": "project_key is required"}
        return await self._get(f"/rest/api/3/project/{key}")

    async def search_issues(self, **params) -> dict[str, Any]:
        """Search issues using JQL."""
        jql = params.get("jql", "project is not EMPTY order by created DESC")
        max_results = params.get("max_results", 20)
        data = await self._get(
            "/rest/api/3/search/jql",
            params={
                "jql": jql,
                "maxResults": max_results,
                "fields": "summary,status,assignee,priority,created,updated",
            },
        )
        issues = data.get("issues", [])
        return {
            "total": data.get("total", 0),
            "issues": [
                {
                    "key": i["key"],
                    "summary": i.get("fields", {}).get("summary"),
                    "status": i.get("fields", {}).get("status", {}).get("name"),
                    "priority": (i.get("fields", {}).get("priority") or {}).get("name"),
                    "assignee": (i.get("fields", {}).get("assignee") or {}).get("displayName"),
                    "created": i.get("fields", {}).get("created"),
                }
                for i in issues
            ],
        }

    async def get_issue(self, **params) -> dict[str, Any]:
        """Get a single issue by key."""
        issue_key = params.get("issue_key", "")
        if not issue_key:
            return {"error": "issue_key is required"}
        return await self._get(f"/rest/api/3/issue/{issue_key}")

    async def get_transitions(self, **params) -> dict[str, Any]:
        """Get available transitions for an issue."""
        issue_key = params.get("issue_key", "")
        if not issue_key:
            return {"error": "issue_key is required"}
        return await self._get(f"/rest/api/3/issue/{issue_key}/transitions")

    async def get_sprint_data(self, **params) -> dict[str, Any]:
        """Get active sprint for a board."""
        board_id = params.get("board_id", "")
        if not board_id:
            return {"error": "board_id is required"}
        return await self._get(f"/rest/agile/1.0/board/{board_id}/sprint", params={"state": "active"})

    async def get_project_metrics(self, **params) -> dict[str, Any]:
        """Get issue counts by status for a project (via JQL)."""
        project_key = params.get("project_key", "")
        if not project_key:
            return {"error": "project_key is required"}
        data = await self._get("/rest/api/3/search/jql", params={
            "jql": f"project = {project_key}",
            "maxResults": 0,
        })
        total = data.get("total", 0)
        # Get open vs closed
        open_data = await self._get("/rest/api/3/search/jql", params={
            "jql": f"project = {project_key} AND statusCategory != Done",
            "maxResults": 0,
        })
        return {
            "project": project_key,
            "total_issues": total,
            "open_issues": open_data.get("total", 0),
            "closed_issues": total - open_data.get("total", 0),
        }

    # ── Write tools ─────────────────────────────────────────────────────

    async def create_issue(self, **params) -> dict[str, Any]:
        """Create a new Jira issue."""
        project_key = params.get("project_key", "")
        if not project_key:
            return {"error": "project_key is required"}
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": params.get("summary", ""),
            "issuetype": {"name": params.get("issue_type", "Task")},
        }
        if params.get("description"):
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": params["description"]}]}],
            }
        if params.get("priority"):
            fields["priority"] = {"name": params["priority"]}
        if params.get("assignee_id"):
            fields["assignee"] = {"accountId": params["assignee_id"]}
        if params.get("labels"):
            fields["labels"] = params["labels"]
        return await self._post("/rest/api/3/issue", {"fields": fields})

    async def update_issue(self, **params) -> dict[str, Any]:
        """Update fields on an existing issue."""
        issue_key = params.get("issue_key", "")
        if not issue_key:
            return {"error": "issue_key is required"}
        fields: dict[str, Any] = {}
        if params.get("summary"):
            fields["summary"] = params["summary"]
        if params.get("priority"):
            fields["priority"] = {"name": params["priority"]}
        if params.get("labels"):
            fields["labels"] = params["labels"]
        return await self._put(f"/rest/api/3/issue/{issue_key}", {"fields": fields})

    async def transition_issue(self, **params) -> dict[str, Any]:
        """Transition an issue to a new status."""
        issue_key = params.get("issue_key", "")
        transition_id = params.get("transition_id", "")
        if not issue_key or not transition_id:
            return {"error": "issue_key and transition_id are required"}
        return await self._post(
            f"/rest/api/3/issue/{issue_key}/transitions",
            {"transition": {"id": str(transition_id)}},
        )

    async def add_comment(self, **params) -> dict[str, Any]:
        """Add a comment to an issue."""
        issue_key = params.get("issue_key", "")
        if not issue_key:
            return {"error": "issue_key is required"}
        body_text = params.get("body", "")
        return await self._post(
            f"/rest/api/3/issue/{issue_key}/comment",
            {
                "body": {
                    "type": "doc",
                    "version": 1,
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": body_text}]}],
                }
            },
        )
