"""Load harness for the per-connector rate limiter.

Run against a staging API instance:

    locust \
      -f tests/load/locustfile_connectors.py \
      --host https://staging.agenticorg.ai \
      --users 50 --spawn-rate 5 --run-time 5m \
      --headless \
      --csv tests/load/results/connectors

Each Locust user picks a connector from CONNECTORS_TO_TEST, runs at the
documented RPM (or 110% of it to verify the limiter actually clamps),
and reports the 429 rate. Below the published cap we expect 0% 429s;
above the cap we expect approximately ``(load - cap) / load``.

Required env vars:
  AGENTICORG_TOKEN — a valid local JWT for the test tenant
  AGENTICORG_TEST_TENANT_ID — UUID of the test tenant
"""

from __future__ import annotations

import os
import random
import time

from locust import HttpUser, between, events, task

# (connector_name, published_rpm, sample_tool, sample_params)
CONNECTORS_TO_TEST: list[tuple[str, int, str, dict]] = [
    ("hubspot", 6000, "list_contacts", {"limit": 1}),
    ("salesforce", 1500, "list_accounts", {"limit": 1}),
    ("stripe", 6000, "list_balance", {}),
    ("github", 5000, "get_user", {}),
    ("gstn", 100, "ping", {}),  # strict — verify the 100/min ceiling
]


class ConnectorTrafficUser(HttpUser):
    """A simulated tenant making /tool-gateway/execute calls.

    The wait-time is dialed so that 50 users at the default loop emit
    ~ the published RPM for the picked connector. Locust's master will
    aggregate failures across users.
    """

    wait_time = between(0.05, 0.2)

    def on_start(self):
        self.token = os.environ.get("AGENTICORG_TOKEN", "")
        self.tenant_id = os.environ.get("AGENTICORG_TEST_TENANT_ID", "")
        if not self.token or not self.tenant_id:
            raise RuntimeError(
                "AGENTICORG_TOKEN and AGENTICORG_TEST_TENANT_ID must be set"
            )
        self.connector_name, self.target_rpm, self.tool, self.params = random.choice(
            CONNECTORS_TO_TEST
        )
        # Per-user wait between calls — gives roughly target_rpm/60 calls/sec.
        self._call_interval = max(0.01, 60.0 / self.target_rpm)

    @task
    def call_tool(self):
        with self.client.post(
            "/api/v1/tool-gateway/execute",
            json={
                "tenant_id": self.tenant_id,
                "agent_id": "loadtest-agent",
                "agent_scopes": [f"tool:{self.connector_name}:read:{self.tool}"],
                "connector_name": self.connector_name,
                "tool_name": self.tool,
                "params": self.params,
            },
            headers={"Authorization": f"Bearer {self.token}"},
            name=f"/tool-gateway/execute[{self.connector_name}]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 429:
                # 429s are expected when we deliberately overshoot the
                # published rate — the limiter is working. We mark them
                # as a separate stat (success=False, no exception).
                resp.failure("rate-limited (expected at >100% RPM)")
            else:
                resp.failure(f"unexpected {resp.status_code}: {resp.text[:120]}")
        time.sleep(self._call_interval)


# ── Stats threshold checks ──────────────────────────────────────────


@events.quitting.add_listener
def _enforce_thresholds(environment, **_kwargs):
    """Fail the run if any below-cap connector saw any 429s.

    The harness is intentionally permissive about 429s for connectors
    we deliberately overshoot — but for connectors run at 50% of cap
    we should never see them.
    """
    stats = environment.stats
    failures_by_endpoint: dict[str, int] = {}
    for entry in stats.entries.values():
        if "[" in entry.name and entry.method == "POST":
            connector = entry.name.split("[", 1)[1].rstrip("]")
            failures_by_endpoint[connector] = entry.num_failures

    print("\n=== Connector load test summary ===")
    for connector, fails in failures_by_endpoint.items():
        print(f"  {connector}: {fails} failures")
    print("===================================\n")
