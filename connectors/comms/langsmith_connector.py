"""Langsmith connector — comms."""
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
    self._tool_registry["log_agent_trace"] = self.log_agent_trace
    self._tool_registry["get_run_performance_stats"] = self.get_run_performance_stats
    self._tool_registry["evaluate_output_quality"] = self.evaluate_output_quality
    self._tool_registry["compare_prompt_versions"] = self.compare_prompt_versions
    self._tool_registry["export_evaluation_dataset"] = self.export_evaluation_dataset

    async def _authenticate(self):
        self._auth_headers = {"Authorization": "Bearer <token>"}

async def log_agent_trace(self, **params):
    """Execute log_agent_trace on langsmith."""
    return await self._post("/log/agent/trace", params)


async def get_run_performance_stats(self, **params):
    """Execute get_run_performance_stats on langsmith."""
    return await self._post("/get/run/performance/stats", params)


async def evaluate_output_quality(self, **params):
    """Execute evaluate_output_quality on langsmith."""
    return await self._post("/evaluate/output/quality", params)


async def compare_prompt_versions(self, **params):
    """Execute compare_prompt_versions on langsmith."""
    return await self._post("/compare/prompt/versions", params)


async def export_evaluation_dataset(self, **params):
    """Execute export_evaluation_dataset on langsmith."""
    return await self._post("/export/evaluation/dataset", params)

