"""LangSmith connector — comms / observability.

Integrates with LangSmith API for agent tracing, evaluation,
and prompt comparison. Used for internal monitoring, not customer-facing.
"""

from __future__ import annotations

from typing import Any

from connectors.framework.base_connector import BaseConnector


class LangsmithConnector(BaseConnector):
    name = "langsmith"
    category = "comms"
    auth_type = "api_key"
    base_url = "https://api.smith.langchain.com"
    rate_limit_rpm = 100

    def _register_tools(self):
        self._tool_registry["list_runs"] = self.list_runs
        self._tool_registry["get_run"] = self.get_run
        self._tool_registry["get_run_stats"] = self.get_run_stats
        self._tool_registry["list_datasets"] = self.list_datasets
        self._tool_registry["create_feedback"] = self.create_feedback

    async def _authenticate(self):
        api_key = self._get_secret("api_key")
        self._auth_headers = {"X-API-Key": api_key}

    async def health_check(self) -> dict[str, Any]:
        try:
            await self._get("/sessions", {"limit": 1})
            return {"status": "healthy"}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    async def list_runs(self, **params) -> dict[str, Any]:
        """List traced runs (agent executions).

        Params: session_id (optional), run_type (chain/llm/tool),
                error (true/false), limit (default 20).
        """
        params.setdefault("limit", 20)
        return await self._get("/runs", params)

    async def get_run(self, **params) -> dict[str, Any]:
        """Get details of a specific run.

        Params: run_id (required).
        """
        run_id = params["run_id"]
        return await self._get(f"/runs/{run_id}")

    async def get_run_stats(self, **params) -> dict[str, Any]:
        """Get aggregate run statistics.

        Params: session_id (optional), start_time (ISO8601), end_time.
        """
        return await self._post("/runs/stats", params)

    async def list_datasets(self, **params) -> dict[str, Any]:
        """List evaluation datasets.

        Params: limit (default 20), name (optional filter).
        """
        params.setdefault("limit", 20)
        return await self._get("/datasets", params)

    async def create_feedback(self, **params) -> dict[str, Any]:
        """Create feedback for a run (for evaluation).

        Params: run_id (required), key (required — feedback dimension name),
                score (optional float 0-1), value (optional string),
                comment (optional).
        """
        return await self._post("/feedback", params)
