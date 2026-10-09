// SPDX-License-Identifier: Apache-2.0
/**
 * Guardrails console: the mode banners, the rule list, add, edit, enable and
 * delete with the payloads the API expects, and the dry run.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockDelete = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import Guardrails from "@/pages/Guardrails";

const LISTS = {
  stages: ["input", "retrieval", "output", "action"],
  detectors: ["sensitive_data", "toxicity", "pattern", "injection", "output_policy", "grounding"],
  actions: ["flag", "mask", "redact", "tokenise", "block"],
  risk_tiers: ["low", "medium", "high", "critical"],
};

const RULE = {
  id: "r1",
  name: "cards-out",
  stage: "output",
  detector: "sensitive_data",
  action: "redact",
  priority: 10,
  enabled: true,
  threshold: 0.5,
  agent_id: "support-agent",
  use_case: null,
  risk_tier: null,
  options: { entities: ["CREDIT_CARD"] },
  reason: "card numbers never leave",
};

function route(status: Record<string, unknown>, rules = [RULE], checksEnabled: boolean | undefined = true) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/guardrails/status") return Promise.resolve({ data: { ...LISTS, ...status } });
    if (url === "/guardrails/rules") return Promise.resolve({ data: rules });
    if (url === "/observability/checks") return Promise.resolve({ data: { enabled: checksEnabled, checks: scheduledChecks } });
    if (url === "/observability/checks/chk-1/results")
      return Promise.resolve({
        data: {
          results: [
            {
              status: "failed",
              started_at: "2026-10-06T02:00:00+00:00",
              reasons: ["adversarial_recall_below_minimum"],
              detail: { recall: 0.6, attacks: 20, detected: 12, false_positives: 1, controls: 9 },
            },
          ],
        },
      });
    return Promise.reject(new Error(`unexpected ${url}`));
  });
}

let scheduledChecks: Array<Record<string, unknown>> = [];

function renderPage() {
  return render(
    <HelmetProvider>
      <Guardrails />
    </HelmetProvider>,
  );
}

describe("Guardrails console", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    scheduledChecks = [];
    mockPost.mockResolvedValue({ data: {} });
    mockPatch.mockResolvedValue({ data: {} });
    mockDelete.mockResolvedValue({ data: null });
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("says when the hooks are off, flag-only or enforcing", async () => {
    route({ hooks_enabled: false, enforcing: false, mode: "flag_only" });
    const { unmount } = renderPage();
    expect(await screen.findByTestId("hooks-off")).toBeInTheDocument();
    expect(screen.queryByTestId("flag-only")).not.toBeInTheDocument();
    unmount();
    route({ hooks_enabled: true, enforcing: false, mode: "flag_only" });
    const second = renderPage();
    expect(await screen.findByTestId("flag-only")).toBeInTheDocument();
    second.unmount();
    route({ hooks_enabled: true, enforcing: true, mode: "enforced" });
    renderPage();
    expect(await screen.findByTestId("enforcing")).toBeInTheDocument();
  });

  it("lists the rules with what each applies to", async () => {
    route({ hooks_enabled: true, enforcing: false });
    renderPage();
    const row = await screen.findByTestId("rule-row-r1");
    expect(row).toHaveTextContent("cards-out");
    expect(row).toHaveTextContent("agent support-agent");
    expect(row).toHaveTextContent("redact");
  });

  it("adds a rule with the detector's example options", async () => {
    route({ hooks_enabled: true, enforcing: false }, []);
    renderPage();
    await screen.findByTestId("rules-empty");
    expect(screen.getByTestId("rule-save")).toBeDisabled();
    fireEvent.change(screen.getByTestId("rule-name"), { target: { value: "grounded answers" } });
    fireEvent.change(screen.getByTestId("rule-detector"), { target: { value: "grounding" } });
    fireEvent.change(screen.getByTestId("rule-action"), { target: { value: "block" } });
    fireEvent.change(screen.getByTestId("rule-agent"), { target: { value: "policy-assistant" } });
    fireEvent.click(screen.getByTestId("rule-save"));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/guardrails/rules", {
        name: "grounded answers",
        stage: "output",
        detector: "grounding",
        action: "block",
        priority: 100,
        threshold: 0.5,
        agent_id: "policy-assistant",
        use_case: null,
        risk_tier: null,
        options: { min_support: 0.5, require_context: false },
        reason: "",
      }),
    );
  });

  it("refuses options that are not JSON without calling the API", async () => {
    route({ hooks_enabled: true, enforcing: false }, []);
    renderPage();
    await screen.findByTestId("rules-empty");
    fireEvent.change(screen.getByTestId("rule-name"), { target: { value: "broken" } });
    fireEvent.change(screen.getByTestId("rule-options"), { target: { value: "{not json" } });
    fireEvent.click(screen.getByTestId("rule-save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("not valid JSON");
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("edits, disables and deletes a rule", async () => {
    route({ hooks_enabled: true, enforcing: false });
    renderPage();
    fireEvent.click(await screen.findByTestId("rule-edit-r1"));
    expect(screen.getByTestId("rule-name")).toHaveValue("cards-out");
    fireEvent.change(screen.getByTestId("rule-action"), { target: { value: "mask" } });
    fireEvent.click(screen.getByTestId("rule-save"));
    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith(
        "/guardrails/rules/r1",
        expect.objectContaining({ name: "cards-out", action: "mask", options: { entities: ["CREDIT_CARD"] } }),
      ),
    );
    fireEvent.click(await screen.findByTestId("rule-toggle-r1"));
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith("/guardrails/rules/r1", { enabled: false }));
    fireEvent.click(await screen.findByTestId("rule-delete-r1"));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("/guardrails/rules/r1"));
  });

  it("dry-runs a stage with a context and shows what each rule would do", async () => {
    route({ hooks_enabled: true, enforcing: false });
    mockPost.mockResolvedValue({
      data: {
        stage: "output",
        text: "The rate is 9.75%.",
        allowed: false,
        enforced: false,
        findings: 1,
        outcomes: [
          {
            rule_id: "r9", rule_name: "grounded answers", detector: "grounding", action: "block", findings: 1,
            score: 0.9, kinds: ["unsupported_number"], blocked: true, transformed: false,
          },
        ],
      },
    });
    renderPage();
    await screen.findByTestId("rule-row-r1");
    fireEvent.change(screen.getByTestId("try-text"), { target: { value: "The rate is 9.75%." } });
    fireEvent.change(screen.getByTestId("try-context"), { target: { value: "The rate is 3.5%.\n\nPaid quarterly." } });
    fireEvent.click(screen.getByTestId("try-run"));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/guardrails/evaluate", {
        stage: "output",
        text: "The rate is 9.75%.",
        context: ["The rate is 3.5%.", "Paid quarterly."],
      }),
    );
    const result = await screen.findByTestId("try-result");
    expect(result).toHaveTextContent("blocked");
    expect(result).toHaveTextContent("unsupported_number");
    expect(result).toHaveTextContent("flag-only");
  });

  it("shows the scheduled adversarial checks with their latest result", async () => {
    scheduledChecks = [
      { id: "chk-1", name: "nightly attacks", kind: "adversarial", interval_minutes: 1440, enabled: true, last_run_at: null, last_status: "failed" },
      { id: "chk-2", name: "nightly model", kind: "model", interval_minutes: 60, enabled: true, last_run_at: null, last_status: "ok" },
      { id: "chk-3", name: "weekly attacks", kind: "adversarial", interval_minutes: 1440, enabled: false, last_run_at: null, last_status: null },
    ];
    route({});
    renderPage();
    const block = await screen.findByTestId("suite-scheduled");
    expect(block).toHaveTextContent("nightly attacks every 1440 min: failed, 12 of 20 attacks detected (60%), 1 of 9 benign texts wrongly caught (adversarial_recall_below_minimum)");
    expect(block).toHaveTextContent("weekly attacks every 1440 min, paused: not run yet");
    expect(block).not.toHaveTextContent("nightly model");
    scheduledChecks = [];
  });

  it.each([false, null])("hides persisted checks and does not fetch results when enabled is %s", async (enabled) => {
    scheduledChecks = [{ id: "chk-1", name: "persisted", kind: "adversarial", enabled: true }];
    route({});
    const original = mockGet.getMockImplementation()!;
    mockGet.mockImplementation((url: string) => url === "/observability/checks"
      ? Promise.resolve({ data: { ...(enabled === null ? {} : { enabled }), checks: scheduledChecks } })
      : original(url));
    renderPage();
    await screen.findByTestId("adversarial");
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/observability/checks"));
    expect(screen.queryByTestId("suite-scheduled")).not.toBeInTheDocument();
    expect(mockGet).not.toHaveBeenCalledWith("/observability/checks/chk-1/results", expect.anything());
  });

  it("is unchanged where the checks cannot be read", async () => {
    scheduledChecks = [];
    route({});
    renderPage();
    await screen.findByTestId("adversarial");
    expect(screen.queryByTestId("suite-scheduled")).not.toBeInTheDocument();
  });

  it("runs the adversarial set against the tenant's rules and the baseline", async () => {
    route({ hooks_enabled: true, enforcing: false });
    const report = {
      rules: "tenant", rule_count: 1, cases: 47, attacks: 31, detected: 6, recall: 0.1935, controls: 15, false_positives: 1,
      categories: [
        {
          category: "sensitive_data", attacks: 7, detected: 6, recall: 0.8571, controls: 3, false_positives: 1,
          missed: ["pii-07"], wrongly_caught: ["pii-c3"],
        },
      ],
      errors: [],
    };
    mockPost.mockResolvedValue({ data: report });
    renderPage();
    await screen.findByTestId("rule-row-r1");
    fireEvent.click(screen.getByTestId("suite-run-tenant"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/guardrails/adversarial/run", { rules: "tenant" }));
    const result = await screen.findByTestId("suite-result");
    expect(result).toHaveTextContent("6 of 31 attacks detected (19%)");
    expect(screen.getByTestId("suite-row-sensitive_data")).toHaveTextContent("6 / 7 (86%)");
    expect(screen.getByTestId("suite-row-sensitive_data")).toHaveTextContent("pii-07");
    expect(screen.getByTestId("suite-row-sensitive_data")).toHaveTextContent("pii-c3");
    fireEvent.click(screen.getByTestId("suite-run-baseline"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/guardrails/adversarial/run", { rules: "baseline" }));
  });

  it("names the call's scope and the user's words in a dry run", async () => {
    route({ hooks_enabled: true, enforcing: false });
    mockPost.mockResolvedValue({
      data: { stage: "output", text: "x", allowed: true, enforced: false, findings: 0, outcomes: [] },
    });
    renderPage();
    await screen.findByTestId("rule-row-r1");
    fireEvent.change(screen.getByTestId("try-text"), { target: { value: "Your nominee is on file." } });
    fireEvent.change(screen.getByTestId("try-agent"), { target: { value: " support-agent " } });
    fireEvent.change(screen.getByTestId("try-use-case"), { target: { value: "agent_run" } });
    fireEvent.change(screen.getByTestId("try-risk-tier"), { target: { value: "high" } });
    fireEvent.change(screen.getByTestId("try-user-input"), { target: { value: "Is my nominee on file?" } });
    fireEvent.click(screen.getByTestId("try-run"));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/guardrails/evaluate", {
        stage: "output",
        text: "Your nominee is on file.",
        agent_id: "support-agent",
        use_case: "agent_run",
        risk_tier: "high",
        user_input: ["Is my nominee on file?"],
      }),
    );
  });

  it("takes the live mode from the status, not from the dry run's result", async () => {
    // The dry run says enforced while the hooks are off: no live call is evaluated at all.
    route({ hooks_enabled: false, enforcing: true });
    mockPost.mockResolvedValue({
      data: { stage: "input", text: "x", allowed: true, enforced: true, findings: 0, outcomes: [] },
    });
    const first = renderPage();
    await screen.findByTestId("rule-row-r1");
    fireEvent.change(screen.getByTestId("try-text"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("try-run"));
    expect(await screen.findByTestId("try-live-mode")).toHaveTextContent("not evaluated");
    first.unmount();
    // No rule matched, so the dry run says not enforced; the tenant is enforcing.
    route({ hooks_enabled: true, enforcing: true });
    mockPost.mockResolvedValue({
      data: { stage: "input", text: "x", allowed: true, enforced: false, findings: 0, outcomes: [] },
    });
    renderPage();
    await screen.findByTestId("rule-row-r1");
    fireEvent.change(screen.getByTestId("try-text"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("try-run"));
    expect(await screen.findByTestId("try-live-mode")).toHaveTextContent("live calls are enforced");
  });
});
