"""OPA (Open Policy Agent) client for authorization decisions."""
from __future__ import annotations

from typing import Any

import httpx


class OPAClient:
    """Evaluate authorization policies against OPA."""

    def __init__(self, opa_url: str = "http://localhost:8181"):
        self.opa_url = opa_url

    async def evaluate(self, policy_path: str, input_data: dict[str, Any]) -> bool:
        """Evaluate a policy and return allow/deny."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.opa_url}/v1/data/{policy_path}",
                    json={"input": input_data},
                    timeout=5.0,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    return result.get("result", {}).get("allow", False)
                return False
        except httpx.HTTPError:
            # Fail closed — deny on OPA unavailability
            return False


opa_client = OPAClient()
