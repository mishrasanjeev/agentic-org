# SPDX-License-Identifier: Apache-2.0
"""Explicit provider authorization for tests unrelated to grant policy."""

from core.tool_gateway.provider_gateway import ToolDecision


class AllowProviderCalls:
    async def authorize(self, *, connector: str, tool: str) -> ToolDecision:
        return ToolDecision(allowed=True)


ALLOW_PROVIDER_CALLS = AllowProviderCalls()
