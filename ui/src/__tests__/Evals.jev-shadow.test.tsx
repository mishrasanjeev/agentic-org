// SPDX-License-Identifier: Apache-2.0

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Evals from "../pages/Evals";

const scorecard = {
  generated_at: "2026-09-20T00:00:00Z",
  version: "synthetic-test",
  platform_metrics: {
    stp_rate: 0.8,
    hitl_rate: 0.2,
    mean_confidence: 0.9,
    uptime_sla: 0.99,
    total_cases: 1,
  },
  domain_aggregates: {},
  agent_aggregates: {},
  case_results: [],
  data_quality: "simulated",
};

const shadowPlan = {
  status: "ready",
  effective_mode: "off",
  active_routing_enabled: false,
  non_executing: true,
  corpus: { case_count: 6, domains: ["commerce", "finance"], content_policy: "synthetic_metadata_only" },
  controls: { sample_rate: 1, max_calls_per_run: 100, failure_threshold: 3, cooldown_seconds: 60 },
  review_gates: {
    minimum_agreement_rate: 0.95,
    maximum_invalid_or_unavailable: 0,
    maximum_p95_latency_ms: 800,
    human_review_required: true,
  },
  reporting: { status: "not_run", run_policy: "explicit_operator_cli_only" },
};

describe("Evals Jev shadow panel", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve({
          ok: true,
          json: async () => (url.endsWith("jev-shadow") ? shadowPlan : scorecard),
        })
      )
    );
  });

  it("shows an explicit non-executing, not-run plan", async () => {
    render(<Evals />);

    expect(await screen.findByTestId("jev-shadow-plan")).toBeInTheDocument();
    expect(screen.getByText("Advisory only")).toBeInTheDocument();
    expect(screen.getByText("not_run")).toBeInTheDocument();
    expect(screen.getByText("existing runtime remains authoritative")).toBeInTheDocument();
    expect(screen.getByText("Agreement gate: 95%+")).toBeInTheDocument();
  });
});
