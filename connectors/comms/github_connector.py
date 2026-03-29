"""Github connector — real GitHub REST API v3 integration."""

from __future__ import annotations

from typing import Any

from connectors.framework.base_connector import BaseConnector


class GithubConnector(BaseConnector):
    name = "github"
    category = "comms"
    auth_type = "pat_oauth2"
    base_url = "https://api.github.com"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["list_repos"] = self.list_repos
        self._tool_registry["get_repo"] = self.get_repo
        self._tool_registry["list_repository_issues"] = self.list_repository_issues
        self._tool_registry["create_issue"] = self.create_issue
        self._tool_registry["create_pull_request"] = self.create_pull_request
        self._tool_registry["get_repository_statistics"] = self.get_repository_statistics
        self._tool_registry["search_code"] = self.search_code
        self._tool_registry["create_release"] = self.create_release
        self._tool_registry["trigger_github_action_workflow"] = self.trigger_github_action_workflow

    async def _authenticate(self):
        personal_access_token = self._get_secret("personal_access_token")
        self._auth_headers = {
            "Authorization": f"Bearer {personal_access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        # Re-init client with updated headers
        if self._client:
            await self._client.aclose()
        import httpx

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_ms / 1000,
            headers=self._auth_headers,
        )

    async def health_check(self) -> dict[str, Any]:
        """Verify PAT is valid by fetching authenticated user."""
        try:
            data = await self._get("/user")
            return {"status": "healthy", "login": data.get("login")}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    # ── Read-only tools ─────────────────────────────────────────────────

    async def list_repos(self, **params) -> dict[str, Any]:
        """List repositories for the authenticated user."""
        per_page = params.get("per_page", 30)
        sort = params.get("sort", "updated")
        data = await self._get("/user/repos", params={"per_page": per_page, "sort": sort})
        return {
            "repos": [
                {
                    "full_name": r["full_name"],
                    "description": r.get("description"),
                    "language": r.get("language"),
                    "stars": r.get("stargazers_count", 0),
                    "updated_at": r.get("updated_at"),
                    "private": r.get("private", False),
                }
                for r in data
            ],
            "count": len(data),
        }

    async def get_repo(self, **params) -> dict[str, Any]:
        """Get details of a specific repository."""
        owner = params.get("owner", "")
        repo = params.get("repo", "")
        return await self._get(f"/repos/{owner}/{repo}")

    async def list_repository_issues(self, **params) -> dict[str, Any]:
        """List issues for a repository."""
        owner = params.get("owner", "")
        repo = params.get("repo", "")
        state = params.get("state", "open")
        per_page = params.get("per_page", 30)
        data = await self._get(
            f"/repos/{owner}/{repo}/issues",
            params={"state": state, "per_page": per_page},
        )
        return {
            "issues": [
                {
                    "number": i["number"],
                    "title": i["title"],
                    "state": i["state"],
                    "user": i.get("user", {}).get("login"),
                    "created_at": i.get("created_at"),
                    "labels": [lbl["name"] for lbl in i.get("labels", [])],
                }
                for i in data
            ],
            "count": len(data),
        }

    async def get_repository_statistics(self, **params) -> dict[str, Any]:
        """Get repository stats (stars, forks, issues, etc.)."""
        owner = params.get("owner", "")
        repo = params.get("repo", "")
        data = await self._get(f"/repos/{owner}/{repo}")
        return {
            "full_name": data.get("full_name"),
            "description": data.get("description"),
            "stars": data.get("stargazers_count", 0),
            "forks": data.get("forks_count", 0),
            "open_issues": data.get("open_issues_count", 0),
            "language": data.get("language"),
            "size_kb": data.get("size", 0),
            "default_branch": data.get("default_branch"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
        }

    async def search_code(self, **params) -> dict[str, Any]:
        """Search code across GitHub repositories."""
        query = params.get("query", "")
        data = await self._get("/search/code", params={"q": query, "per_page": 10})
        return {
            "total_count": data.get("total_count", 0),
            "items": [
                {
                    "name": item.get("name"),
                    "path": item.get("path"),
                    "repository": item.get("repository", {}).get("full_name"),
                    "url": item.get("html_url"),
                }
                for item in data.get("items", [])
            ],
        }

    # ── Write tools ─────────────────────────────────────────────────────

    async def create_issue(self, **params) -> dict[str, Any]:
        """Create an issue in a repository."""
        owner = params.get("owner", "")
        repo = params.get("repo", "")
        body = {
            "title": params.get("title", ""),
            "body": params.get("body", ""),
        }
        if params.get("labels"):
            body["labels"] = params["labels"]
        return await self._post(f"/repos/{owner}/{repo}/issues", body)

    async def create_pull_request(self, **params) -> dict[str, Any]:
        """Create a pull request."""
        owner = params.get("owner", "")
        repo = params.get("repo", "")
        body = {
            "title": params.get("title", ""),
            "body": params.get("body", ""),
            "head": params.get("head", ""),
            "base": params.get("base", "main"),
        }
        return await self._post(f"/repos/{owner}/{repo}/pulls", body)

    async def create_release(self, **params) -> dict[str, Any]:
        """Create a new release."""
        owner = params.get("owner", "")
        repo = params.get("repo", "")
        body = {
            "tag_name": params.get("tag_name", ""),
            "name": params.get("name", ""),
            "body": params.get("release_body", ""),
            "draft": params.get("draft", False),
            "prerelease": params.get("prerelease", False),
        }
        return await self._post(f"/repos/{owner}/{repo}/releases", body)

    async def trigger_github_action_workflow(self, **params) -> dict[str, Any]:
        """Trigger a GitHub Actions workflow dispatch."""
        owner = params.get("owner", "")
        repo = params.get("repo", "")
        workflow_id = params.get("workflow_id", "")
        body = {
            "ref": params.get("ref", "main"),
        }
        if params.get("inputs"):
            body["inputs"] = params["inputs"]
        resp = await self._post(
            f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches", body
        )
        return resp if resp else {"status": "dispatched"}
