"""GitHub connector - real GitHub REST API v3 integration."""

from __future__ import annotations

import base64
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
        self._tool_registry["repository_search"] = self.repository_search
        self._tool_registry["list_files"] = self.list_files
        self._tool_registry["get_directory_tree"] = self.get_directory_tree
        self._tool_registry["read_file"] = self.read_file
        self._tool_registry["create_file"] = self.create_file
        self._tool_registry["update_file"] = self.update_file
        self._tool_registry["delete_file"] = self.delete_file
        self._tool_registry["create_branch"] = self.create_branch
        self._tool_registry["commit_changes"] = self.commit_changes
        self._tool_registry["push_changes"] = self.push_changes
        self._tool_registry["create_release"] = self.create_release
        self._tool_registry["trigger_github_action_workflow"] = self.trigger_github_action_workflow

    async def _authenticate(self):
        personal_access_token = self._get_secret("personal_access_token")
        self._auth_headers = {
            "Authorization": f"Bearer {personal_access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def health_check(self) -> dict[str, Any]:
        """Verify PAT is valid by fetching authenticated user."""
        try:
            data = await self._get("/user")
            return {"status": "healthy", "login": data.get("login")}
        # enterprise-gate: broad-except-ok reason=connector-health-boundary-reports-unhealthy
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    def _repo_parts(self, params: dict[str, Any]) -> tuple[str, str] | dict[str, str]:
        owner = str(params.get("owner") or "").strip()
        repo = str(params.get("repo") or "").strip()
        full_name = str(params.get("repository") or params.get("full_name") or "").strip()
        if full_name and (not owner or not repo):
            if "/" not in full_name:
                return {"error": "repository must be in owner/repo form"}
            owner, repo = full_name.split("/", 1)
        if not owner or not repo:
            return {"error": "owner and repo are required"}
        return owner, repo

    @staticmethod
    def _contents_path(path: Any) -> str:
        return "/".join(part for part in str(path or "").strip("/").split("/") if part)

    @staticmethod
    def _b64(content: Any) -> str:
        return base64.b64encode(str(content).encode("utf-8")).decode("ascii")

    @staticmethod
    def _decode_content(data: dict[str, Any]) -> str:
        raw = str(data.get("content") or "")
        if data.get("encoding") != "base64":
            return raw
        compact = "".join(raw.splitlines())
        return base64.b64decode(compact.encode("ascii")).decode("utf-8")

    # Read-only tools

    async def list_repos(self, **params) -> dict[str, Any]:
        """List repositories for the authenticated user."""
        per_page = params.get("per_page", 30)
        sort = params.get("sort", "updated")
        data = await self._get("/user/repos", params={"per_page": per_page, "sort": sort})
        if not isinstance(data, list):
            return data
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
        parts = self._repo_parts(params)
        if isinstance(parts, dict):
            return parts
        owner, repo = parts
        return await self._get(f"/repos/{owner}/{repo}")

    async def list_repository_issues(self, **params) -> dict[str, Any]:
        """List issues for a repository."""
        parts = self._repo_parts(params)
        if isinstance(parts, dict):
            return parts
        owner, repo = parts
        state = params.get("state", "open")
        per_page = params.get("per_page", 30)
        data = await self._get(
            f"/repos/{owner}/{repo}/issues",
            params={"state": state, "per_page": per_page},
        )
        if not isinstance(data, list):
            return data
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
        parts = self._repo_parts(params)
        if isinstance(parts, dict):
            return parts
        owner, repo = parts
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
        query = str(params.get("query") or "").strip()
        if not query:
            return {"error": "query is required"}
        data = await self._get(
            "/search/code",
            params={"q": query, "per_page": params.get("per_page", 10)},
        )
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

    async def repository_search(self, **params) -> dict[str, Any]:
        """Search code, scoped to a repository when owner/repo is supplied."""
        query = str(params.get("query") or "").strip()
        if not query:
            return {"error": "query is required"}
        parts = self._repo_parts(params)
        if not isinstance(parts, dict):
            owner, repo = parts
            query = f"{query} repo:{owner}/{repo}"
        return await self.search_code(query=query, per_page=params.get("per_page", 10))

    async def list_files(self, **params) -> dict[str, Any]:
        """List repository files/directories or the recursive git tree."""
        parts = self._repo_parts(params)
        if isinstance(parts, dict):
            return parts
        owner, repo = parts
        ref = params.get("ref") or params.get("branch")
        path = self._contents_path(params.get("path", ""))

        if params.get("recursive"):
            tree_ref = ref
            if not tree_ref:
                repo_data = await self.get_repo(owner=owner, repo=repo)
                tree_ref = repo_data.get("default_branch") or "main"
            data = await self._get(
                f"/repos/{owner}/{repo}/git/trees/{tree_ref}",
                params={"recursive": "1"},
            )
            tree = data.get("tree", []) if isinstance(data, dict) else []
            return {
                "files": [
                    {
                        "path": item.get("path"),
                        "type": item.get("type"),
                        "sha": item.get("sha"),
                        "size": item.get("size"),
                        "url": item.get("url"),
                    }
                    for item in tree
                ],
                "truncated": bool(data.get("truncated", False)) if isinstance(data, dict) else False,
                "count": len(tree),
            }

        query: dict[str, Any] = {}
        if ref:
            query["ref"] = ref
        data = await self._get(f"/repos/{owner}/{repo}/contents/{path}", params=query or None)
        entries = data if isinstance(data, list) else [data]
        return {
            "files": [
                {
                    "name": item.get("name"),
                    "path": item.get("path"),
                    "type": item.get("type"),
                    "sha": item.get("sha"),
                    "size": item.get("size"),
                    "download_url": item.get("download_url"),
                    "url": item.get("html_url") or item.get("url"),
                }
                for item in entries
                if isinstance(item, dict)
            ],
            "count": len(entries),
        }

    async def get_directory_tree(self, **params) -> dict[str, Any]:
        """Return the recursive repository tree for an owner/repo/ref."""
        params["recursive"] = True
        return await self.list_files(**params)

    async def read_file(self, **params) -> dict[str, Any]:
        """Read and decode a text file from a repository."""
        parts = self._repo_parts(params)
        if isinstance(parts, dict):
            return parts
        owner, repo = parts
        path = self._contents_path(params.get("path", ""))
        if not path:
            return {"error": "path is required"}
        query: dict[str, Any] = {}
        if params.get("ref") or params.get("branch"):
            query["ref"] = params.get("ref") or params.get("branch")
        data = await self._get(f"/repos/{owner}/{repo}/contents/{path}", params=query or None)
        if isinstance(data, list):
            return {"error": "path points to a directory"}
        return {
            "name": data.get("name"),
            "path": data.get("path"),
            "sha": data.get("sha"),
            "encoding": data.get("encoding"),
            "size": data.get("size"),
            "content": self._decode_content(data),
        }

    # Write tools

    async def create_issue(self, **params) -> dict[str, Any]:
        """Create an issue in a repository."""
        parts = self._repo_parts(params)
        if isinstance(parts, dict):
            return parts
        owner, repo = parts
        body = {
            "title": params.get("title", ""),
            "body": params.get("body", ""),
        }
        if params.get("labels"):
            body["labels"] = params["labels"]
        return await self._post(f"/repos/{owner}/{repo}/issues", body)

    async def create_pull_request(self, **params) -> dict[str, Any]:
        """Create a pull request."""
        parts = self._repo_parts(params)
        if isinstance(parts, dict):
            return parts
        owner, repo = parts
        body = {
            "title": params.get("title", ""),
            "body": params.get("body", ""),
            "head": params.get("head", ""),
            "base": params.get("base", "main"),
        }
        return await self._post(f"/repos/{owner}/{repo}/pulls", body)

    async def create_file(self, **params) -> dict[str, Any]:
        """Create a UTF-8 file through GitHub's contents API."""
        parts = self._repo_parts(params)
        if isinstance(parts, dict):
            return parts
        owner, repo = parts
        path = self._contents_path(params.get("path", ""))
        if not path:
            return {"error": "path is required"}
        if "content" not in params:
            return {"error": "content is required"}
        body: dict[str, Any] = {
            "message": params.get("message") or f"Create {path}",
            "content": self._b64(params["content"]),
        }
        if params.get("branch"):
            body["branch"] = params["branch"]
        return await self._put(f"/repos/{owner}/{repo}/contents/{path}", body)

    async def update_file(self, **params) -> dict[str, Any]:
        """Update a UTF-8 file through GitHub's contents API."""
        parts = self._repo_parts(params)
        if isinstance(parts, dict):
            return parts
        owner, repo = parts
        path = self._contents_path(params.get("path", ""))
        if not path:
            return {"error": "path is required"}
        if "content" not in params:
            return {"error": "content is required"}
        sha = params.get("sha")
        if not sha:
            current = await self.read_file(owner=owner, repo=repo, path=path, ref=params.get("branch"))
            sha = current.get("sha")
        if not sha:
            return {"error": "sha is required for update_file"}
        body: dict[str, Any] = {
            "message": params.get("message") or f"Update {path}",
            "content": self._b64(params["content"]),
            "sha": sha,
        }
        if params.get("branch"):
            body["branch"] = params["branch"]
        return await self._put(f"/repos/{owner}/{repo}/contents/{path}", body)

    async def delete_file(self, **params) -> dict[str, Any]:
        """Delete a file through GitHub's contents API."""
        parts = self._repo_parts(params)
        if isinstance(parts, dict):
            return parts
        owner, repo = parts
        path = self._contents_path(params.get("path", ""))
        if not path:
            return {"error": "path is required"}
        sha = params.get("sha")
        if not sha:
            current = await self.read_file(owner=owner, repo=repo, path=path, ref=params.get("branch"))
            sha = current.get("sha")
        if not sha:
            return {"error": "sha is required for delete_file"}
        body: dict[str, Any] = {
            "message": params.get("message") or f"Delete {path}",
            "sha": sha,
        }
        if params.get("branch"):
            body["branch"] = params["branch"]
        if not self._client:
            raise RuntimeError("Connector not connected")
        resp = await self._client.request(
            "DELETE",
            f"/repos/{owner}/{repo}/contents/{path}",
            json=body,
        )
        resp.raise_for_status()
        return resp.json()

    async def create_branch(self, **params) -> dict[str, Any]:
        """Create a branch from another branch or commit SHA."""
        parts = self._repo_parts(params)
        if isinstance(parts, dict):
            return parts
        owner, repo = parts
        branch = str(params.get("branch") or params.get("branch_name") or "").strip()
        if not branch:
            return {"error": "branch is required"}
        sha = params.get("sha")
        if not sha:
            base = params.get("base_branch") or params.get("base") or "main"
            ref_data = await self._get(f"/repos/{owner}/{repo}/git/ref/heads/{base}")
            sha = ref_data.get("object", {}).get("sha")
        if not sha:
            return {"error": "base sha could not be resolved"}
        return await self._post(
            f"/repos/{owner}/{repo}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": sha},
        )

    async def commit_changes(self, **params) -> dict[str, Any]:
        """Commit multiple file changes atomically through the git data API."""
        parts = self._repo_parts(params)
        if isinstance(parts, dict):
            return parts
        owner, repo = parts
        branch = str(params.get("branch") or "main").strip()
        message = str(params.get("message") or "Update repository files").strip()
        changes = params.get("changes") or []
        if not isinstance(changes, list) or not changes:
            return {"error": "changes must be a non-empty list"}

        ref_data = await self._get(f"/repos/{owner}/{repo}/git/ref/heads/{branch}")
        parent_sha = ref_data.get("object", {}).get("sha")
        if not parent_sha:
            return {"error": "branch head sha could not be resolved"}
        parent_commit = await self._get(f"/repos/{owner}/{repo}/git/commits/{parent_sha}")
        base_tree_sha = parent_commit.get("tree", {}).get("sha")
        if not base_tree_sha:
            return {"error": "base tree sha could not be resolved"}

        tree: list[dict[str, Any]] = []
        for change in changes:
            if not isinstance(change, dict):
                return {"error": "each change must be an object"}
            path = self._contents_path(change.get("path", ""))
            if not path:
                return {"error": "each change requires path"}
            action = str(change.get("action") or "update").lower()
            if action == "delete":
                tree.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
                continue
            if "content" not in change:
                return {"error": f"content is required for {path}"}
            blob = await self._post(
                f"/repos/{owner}/{repo}/git/blobs",
                {"content": str(change["content"]), "encoding": "utf-8"},
            )
            blob_sha = blob.get("sha")
            if not blob_sha:
                return {"error": f"blob sha missing for {path}"}
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})

        new_tree = await self._post(
            f"/repos/{owner}/{repo}/git/trees",
            {"base_tree": base_tree_sha, "tree": tree},
        )
        tree_sha = new_tree.get("sha")
        if not tree_sha:
            return {"error": "new tree sha missing"}
        new_commit = await self._post(
            f"/repos/{owner}/{repo}/git/commits",
            {"message": message, "tree": tree_sha, "parents": [parent_sha]},
        )
        commit_sha = new_commit.get("sha")
        if not commit_sha:
            return {"error": "new commit sha missing"}
        ref_update = await self.push_changes(
            owner=owner,
            repo=repo,
            branch=branch,
            sha=commit_sha,
            force=bool(params.get("force", False)),
        )
        return {
            "status": "committed",
            "branch": branch,
            "commit_sha": commit_sha,
            "files_changed": len(changes),
            "ref": ref_update,
        }

    async def push_changes(self, **params) -> dict[str, Any]:
        """Move a branch ref to a commit SHA."""
        parts = self._repo_parts(params)
        if isinstance(parts, dict):
            return parts
        owner, repo = parts
        branch = str(params.get("branch") or params.get("branch_name") or "").strip()
        sha = str(params.get("sha") or "").strip()
        if not branch:
            return {"error": "branch is required"}
        if not sha:
            return {"error": "sha is required"}
        return await self._patch(
            f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
            {"sha": sha, "force": bool(params.get("force", False))},
        )

    async def create_release(self, **params) -> dict[str, Any]:
        """Create a new release."""
        parts = self._repo_parts(params)
        if isinstance(parts, dict):
            return parts
        owner, repo = parts
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
        parts = self._repo_parts(params)
        if isinstance(parts, dict):
            return parts
        owner, repo = parts
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
