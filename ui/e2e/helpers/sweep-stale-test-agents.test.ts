// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { describe, expect, it } from "vitest";

import { STALE_TEST_AGENT_AGE_MS, embeddedTimestamp, staleTestAgentIds } from "./sweep-stale-test-agents";

const NOW = 1_792_600_000_000;
const OLD = NOW - STALE_TEST_AGENT_AGE_MS - 1;
const RECENT = NOW - 60_000;

describe("staleTestAgentIds", () => {
  it("selects shadow agents whose embedded test timestamp is older than two hours", () => {
    const ids = staleTestAgentIds(
      [
        { id: "old-name", status: "shadow", employee_name: `HITL Bot ${OLD}` },
        { id: "old-type", status: "shadow", employee_name: "Decision bot", agent_type: `hitl_${OLD}` },
        { id: "recent", status: "shadow", employee_name: `DecBot ${RECENT}` },
      ],
      NOW,
    );
    expect(ids).toEqual(["old-name", "old-type"]);
  });

  it("never selects seeded fixtures, other statuses or agents without an id", () => {
    const ids = staleTestAgentIds(
      [
        { id: "fixture", status: "shadow", employee_name: "Contract Intelligence", agent_type: "contract_intelligence" },
        { id: "seeded-e2e", status: "shadow", employee_name: "E2E AP Processor" },
        { id: "active", status: "active", employee_name: `HITL Bot ${OLD}` },
        { status: "shadow", employee_name: `HITL Bot ${OLD}` },
        { id: "short-number", status: "shadow", employee_name: "Invoice 40722" },
      ],
      NOW,
    );
    expect(ids).toEqual([]);
  });

  it("reads agent_id when the listing has no id", () => {
    expect(staleTestAgentIds([{ agent_id: "a1", status: "shadow", name: `Agent ${OLD}` }], NOW)).toEqual(["a1"]);
  });
});

describe("embeddedTimestamp", () => {
  it("takes only a standalone 13-digit millisecond timestamp", () => {
    expect(embeddedTimestamp({ employee_name: `Synthetic reasoning ${OLD}` })).toBe(OLD);
    expect(embeddedTimestamp({ employee_name: "id 17925820000001" })).toBeNull();
    expect(embeddedTimestamp({ employee_name: "Payroll Engine" })).toBeNull();
  });
});
