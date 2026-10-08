// SPDX-License-Identifier: Apache-2.0
/**
 * The visual builder: the graph derived from the definition, and edits that are edits of the definition.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import WorkflowBuilder, {
  addStep,
  connectSteps,
  fallbackTarget,
  layoutPositions,
  removeStep,
  stepsToEdges,
  updateStep,
  type WorkflowStep,
} from "@/components/WorkflowBuilder";

const STEPS: WorkflowStep[] = [
  { id: "extract", type: "agent", agent_type: "ap_processor", action: "extract" },
  { id: "check", type: "condition", depends_on: ["extract"], expression: "extract.total > 1000", true_path: "approve", false_path: "post" },
  { id: "approve", type: "human_in_loop", depends_on: ["check"], assignee_role: "finance_lead", decision_options: ["approve", "reject"] },
  { id: "post", type: "agent", agent_type: "ap_processor", action: "post", depends_on: ["check"], on_failure: "fallback(manual)" },
  { id: "manual", type: "human_in_loop", depends_on: ["post"], assignee: "ops", decision_options: ["done"] },
];

beforeAll(() => {
  // ReactFlow measures its container; jsdom has no ResizeObserver or DOMMatrix.
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = RO;
  (globalThis as unknown as { DOMMatrixReadOnly: unknown }).DOMMatrixReadOnly = class {
    m22 = 1;
    constructor() {}
  };
});

describe("graph helpers", () => {
  it("derives dependencies, condition paths and fallbacks as labelled edges", () => {
    const edges = stepsToEdges(STEPS).map((e) => `${e.source}>${e.target}:${e.kind}`);
    expect(edges).toEqual([
      "extract>check:then",
      "check>approve:true",
      "check>post:false",
      "check>approve:then",
      "check>post:then",
      "post>manual:fallback",
      "post>manual:then",
    ]);
    expect(fallbackTarget(STEPS[3])).toBe("manual");
    expect(fallbackTarget(STEPS[0])).toBeNull();
  });

  it("lays steps out in columns by depth, ignoring fallbacks", () => {
    const positions = layoutPositions(STEPS, stepsToEdges(STEPS));
    expect(positions.extract.x).toBeLessThan(positions.check.x);
    expect(positions.check.x).toBeLessThan(positions.approve.x);
    expect(positions.approve.x).toBe(positions.post.x);
    expect(positions.approve.y).not.toBe(positions.post.y);
    expect(positions.manual.x).toBeGreaterThan(positions.post.x);
  });

  it("adds, connects, updates and removes steps as edits of the definition", () => {
    const added = addStep(STEPS, "human_in_loop");
    expect(added).toHaveLength(6);
    expect(added[5]).toMatchObject({ id: "human_in_loop_6", type: "human_in_loop", decision_options: ["approve", "reject"], on_failure: "halt" });
    const connected = connectSteps(added, "manual", "human_in_loop_6");
    expect(connected[5].depends_on).toEqual(["manual"]);
    expect(connectSteps(connected, "manual", "human_in_loop_6")[5].depends_on).toEqual(["manual"]);
    expect(connectSteps(connected, "manual", "manual")).toBe(connected);
    const updated = updateStep(connected, "human_in_loop_6", { assignee_role: "cfo" });
    expect(updated[5].assignee_role).toBe("cfo");
    const removed = removeStep(updated, "manual");
    expect(removed.map((s) => s.id)).toEqual(["extract", "check", "approve", "post", "human_in_loop_6"]);
    expect(removed[3].on_failure).toBe("halt");
    expect(removed[4].depends_on).toEqual([]);
    const withoutApprove = removeStep(STEPS, "approve");
    expect(withoutApprove[1].true_path).toBe("");
  });
});

describe("WorkflowBuilder", () => {
  it("draws the steps and hands edits back as a definition", () => {
    const onChange = vi.fn();
    render(<WorkflowBuilder definition={{ steps: STEPS }} onChange={onChange} />);
    expect(screen.getByTestId("workflow-builder")).toBeInTheDocument();
    expect(screen.getByTestId("workflow-builder-panel")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("add-step-condition"));
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as { steps: WorkflowStep[] };
    expect(next.steps).toHaveLength(6);
    expect(next.steps[5].type).toBe("condition");
  });

  it("read-only, it shows no palette", () => {
    render(<WorkflowBuilder definition={{ steps: STEPS }} readOnly />);
    expect(screen.queryByTestId("workflow-builder-panel")).not.toBeInTheDocument();
  });
});
