// SPDX-License-Identifier: Apache-2.0
/**
 * The visual workflow builder: steps as nodes, dependencies, condition paths and fallbacks as edges.
 *
 * The definition is the source of truth (the same `steps` list the API stores); the graph is derived
 * from it, and every edit (add a step, connect two steps, change a field, remove a step) is an edit of
 * the definition handed back through `onChange`. Read-only, it draws a stored workflow.
 */
import { useCallback, useMemo, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  type Connection,
  type Edge,
  type Node,
} from "reactflow";
import "reactflow/dist/style.css";

export interface WorkflowStep {
  id: string;
  type?: string;
  name?: string;
  depends_on?: string[];
  on_failure?: string;
  [key: string]: unknown;
}

export interface WorkflowDefinition {
  steps: WorkflowStep[];
  [key: string]: unknown;
}

export type EdgeKind = "then" | "true" | "false" | "rule" | "fallback";

export interface GraphEdge {
  source: string;
  target: string;
  kind: EdgeKind;
  label: string;
}

export const STEP_TYPES = [
  { type: "agent", label: "Agent step" },
  { type: "human_in_loop", label: "Human checkpoint" },
  { type: "condition", label: "Condition" },
  { type: "wait", label: "Wait" },
  { type: "notify", label: "Notify" },
  { type: "collaboration", label: "Collaboration" },
] as const;

export const FAILURE_DIRECTIVES = ["halt", "continue", "retry(3)", "retry(3) then continue", "fallback"] as const;

const COLOURS: Record<string, string> = {
  agent: "#dbeafe",
  case_agent: "#dbeafe",
  human_in_loop: "#fef3c7",
  condition: "#ede9fe",
  wait: "#e5e7eb",
  wait_for_event: "#e5e7eb",
  notify: "#dcfce7",
  collaboration: "#fce7f3",
};

const EDGE_STYLE: Record<EdgeKind, { stroke: string; dashed?: boolean }> = {
  then: { stroke: "#64748b" },
  true: { stroke: "#16a34a" },
  false: { stroke: "#dc2626" },
  rule: { stroke: "#7c3aed" },
  fallback: { stroke: "#f59e0b", dashed: true },
};

/** The step id, as the API derives it. */
export function stepId(step: WorkflowStep | Record<string, unknown>, index: number): string {
  const raw = (step as WorkflowStep).id ?? (step as Record<string, unknown>).step;
  return raw === undefined || raw === null || raw === "" ? `step_${index + 1}` : String(raw);
}

/** The fallback target of a step's failure directive, if any. */
export function fallbackTarget(step: WorkflowStep): string | null {
  const match = /fallback\(\s*([A-Za-z0-9_.-]+)\s*\)/.exec(String(step.on_failure ?? ""));
  return match ? match[1] : null;
}

/** Every edge of a definition: dependencies, condition paths and fallbacks. */
export function stepsToEdges(steps: WorkflowStep[]): GraphEdge[] {
  const edges: GraphEdge[] = [];
  steps.forEach((step, index) => {
    const id = stepId(step, index);
    for (const dep of step.depends_on ?? []) edges.push({ source: String(dep), target: id, kind: "then", label: "" });
    if (step.type === "condition") {
      if (step.true_path) edges.push({ source: id, target: String(step.true_path), kind: "true", label: "true" });
      if (step.false_path) edges.push({ source: id, target: String(step.false_path), kind: "false", label: "false" });
      for (const rule of (step.rules as Array<Record<string, unknown>> | undefined) ?? []) {
        if (rule && rule.path) {
          edges.push({
            source: id,
            target: String(rule.path),
            kind: "rule",
            label: String(rule.label ?? rule.expression ?? "rule").slice(0, 40),
          });
        }
      }
    }
    const fallback = fallbackTarget(step);
    if (fallback) edges.push({ source: id, target: fallback, kind: "fallback", label: "on failure" });
  });
  return edges;
}

export interface GraphNode {
  id: string;
  type?: string;
  name?: string;
  summary?: string;
  on_failure?: string;
}

/**
 * Steps rebuilt from the graph `GET /workflows/{id}/graph` answers, enough to draw a stored workflow
 * read-only: dependencies, condition paths, rule paths and the failure directive come back from the edges.
 */
export function graphToSteps(graph: { nodes?: unknown; edges?: unknown } | null | undefined): WorkflowStep[] {
  const nodes = Array.isArray(graph?.nodes) ? (graph!.nodes as GraphNode[]) : [];
  const edges = Array.isArray(graph?.edges) ? (graph!.edges as GraphEdge[]) : [];
  return nodes
    .filter((node) => node && typeof node.id === "string")
    .map((node) => {
      const step: WorkflowStep = {
        id: node.id,
        type: node.type ?? "agent",
        name: node.name ?? node.id,
        depends_on: edges.filter((e) => e.kind === "then" && e.target === node.id).map((e) => e.source),
        on_failure: node.on_failure ?? "halt",
      };
      const outgoing = edges.filter((e) => e.source === node.id);
      const truePath = outgoing.find((e) => e.kind === "true");
      const falsePath = outgoing.find((e) => e.kind === "false");
      const rules = outgoing.filter((e) => e.kind === "rule").map((e) => ({ path: e.target, label: e.label }));
      if (truePath) step.true_path = truePath.target;
      if (falsePath) step.false_path = falsePath.target;
      if (rules.length) step.rules = rules;
      return step;
    });
}

/** A layered layout: each step sits one column right of the steps it follows. */
export function layoutPositions(steps: WorkflowStep[], edges: GraphEdge[]): Record<string, { x: number; y: number }> {
  const ids = steps.map(stepId);
  const incoming = new Map<string, string[]>();
  for (const id of ids) incoming.set(id, []);
  for (const edge of edges) {
    if (edge.kind === "fallback") continue;
    if (incoming.has(edge.target)) incoming.get(edge.target)!.push(edge.source);
  }
  const depth = new Map<string, number>();
  const seen = new Set<string>();
  const depthOf = (id: string): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (seen.has(id)) return 0;
    seen.add(id);
    const parents = (incoming.get(id) ?? []).filter((p) => incoming.has(p));
    const value = parents.length ? Math.max(...parents.map(depthOf)) + 1 : 0;
    depth.set(id, value);
    return value;
  };
  const rows = new Map<number, number>();
  const positions: Record<string, { x: number; y: number }> = {};
  for (const id of ids) {
    const column = depthOf(id);
    const row = rows.get(column) ?? 0;
    rows.set(column, row + 1);
    positions[id] = { x: 40 + column * 260, y: 40 + row * 120 };
  }
  return positions;
}

/** A new step of a type, with an id no other step uses. */
export function addStep(steps: WorkflowStep[], type: string): WorkflowStep[] {
  const ids = new Set(steps.map(stepId));
  let n = steps.length + 1;
  while (ids.has(`${type}_${n}`)) n += 1;
  const id = `${type}_${n}`;
  const base: WorkflowStep = { id, type, name: id.replace(/_/g, " "), depends_on: [], on_failure: "halt" };
  if (type === "agent") Object.assign(base, { agent_type: "", action: "", inputs: {} });
  if (type === "human_in_loop") {
    Object.assign(base, { assignee_role: "", decision_options: ["approve", "reject"], timeout_hours: 24 });
  }
  if (type === "condition") Object.assign(base, { expression: "", true_path: "", false_path: "" });
  if (type === "wait") Object.assign(base, { duration: "1h" });
  if (type === "collaboration") Object.assign(base, { agents: [], aggregation: "merge", timeout_minutes: 10 });
  return [...steps, base];
}

/** Connecting two steps: the target now depends on the source (nothing twice, never on itself). */
export function connectSteps(steps: WorkflowStep[], source: string, target: string): WorkflowStep[] {
  if (source === target) return steps;
  return steps.map((step, index) => {
    if (stepId(step, index) !== target) return step;
    const deps = step.depends_on ?? [];
    return deps.includes(source) ? step : { ...step, depends_on: [...deps, source] };
  });
}

/** Removing a step clears every reference to it. */
export function removeStep(steps: WorkflowStep[], id: string): WorkflowStep[] {
  return steps
    .filter((step, index) => stepId(step, index) !== id)
    .map((step) => {
      const next: WorkflowStep = { ...step, depends_on: (step.depends_on ?? []).filter((d) => d !== id) };
      if (next.true_path === id) next.true_path = "";
      if (next.false_path === id) next.false_path = "";
      if (fallbackTarget(next) === id) next.on_failure = "halt";
      return next;
    });
}

export function updateStep(steps: WorkflowStep[], id: string, patch: Partial<WorkflowStep>): WorkflowStep[] {
  return steps.map((step, index) => (stepId(step, index) === id ? { ...step, ...patch } : step));
}

function toNodes(steps: WorkflowStep[], edges: GraphEdge[], selected: string | null): Node[] {
  const positions = layoutPositions(steps, edges);
  return steps.map((step, index) => {
    const id = stepId(step, index);
    const type = String(step.type ?? "agent");
    return {
      id,
      position: positions[id],
      data: { label: `${step.name || id}\n${type === "human_in_loop" ? "human checkpoint" : type}` },
      style: {
        background: COLOURS[type] ?? "#f1f5f9",
        border: selected === id ? "2px solid #0f172a" : "1px solid #94a3b8",
        borderRadius: 8,
        padding: 8,
        width: 200,
        fontSize: 12,
        whiteSpace: "pre-line",
      },
    };
  });
}

function toFlowEdges(edges: GraphEdge[]): Edge[] {
  return edges.map((edge, index) => {
    const style = EDGE_STYLE[edge.kind];
    return {
      id: `${edge.kind}-${edge.source}-${edge.target}-${index}`,
      source: edge.source,
      target: edge.target,
      label: edge.label || undefined,
      animated: edge.kind === "fallback",
      style: { stroke: style.stroke, strokeDasharray: style.dashed ? "6 4" : undefined },
      markerEnd: { type: MarkerType.ArrowClosed, color: style.stroke },
    };
  });
}

interface Props {
  definition: WorkflowDefinition | { steps?: unknown } | null | undefined;
  onChange?: (definition: WorkflowDefinition) => void;
  readOnly?: boolean;
  height?: number;
}

function TextField({
  label,
  value,
  onChange,
  testId,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  testId?: string;
}) {
  return (
    <label className="block text-xs">
      {label}
      <input
        className="border rounded px-2 py-1 w-full"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testId}
      />
    </label>
  );
}

function StepSelect({
  label,
  value,
  ids,
  onChange,
  allowNone,
}: {
  label: string;
  value: string;
  ids: string[];
  onChange: (value: string) => void;
  allowNone?: boolean;
}) {
  return (
    <label className="block text-xs">
      {label}
      <select className="border rounded px-2 py-1 w-full" value={value} onChange={(e) => onChange(e.target.value)}>
        {allowNone && <option value="">(none)</option>}
        {ids.map((id) => (
          <option key={id} value={id}>
            {id}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function WorkflowBuilder({ definition, onChange, readOnly = false, height = 480 }: Props) {
  const steps = useMemo<WorkflowStep[]>(
    () => (Array.isArray(definition?.steps) ? (definition!.steps as WorkflowStep[]) : []),
    [definition],
  );
  const [selected, setSelected] = useState<string | null>(null);
  const edges = useMemo(() => stepsToEdges(steps), [steps]);
  const nodes = useMemo(() => toNodes(steps, edges, selected), [steps, edges, selected]);
  const flowEdges = useMemo(() => toFlowEdges(edges), [edges]);
  const emit = useCallback(
    (next: WorkflowStep[]) => onChange?.({ ...(definition as WorkflowDefinition), steps: next }),
    [definition, onChange],
  );
  const onConnect = useCallback(
    (connection: Connection) => {
      if (readOnly || !connection.source || !connection.target) return;
      emit(connectSteps(steps, connection.source, connection.target));
    },
    [emit, readOnly, steps],
  );
  const current = selected ? (steps.find((s, i) => stepId(s, i) === selected) ?? null) : null;
  const ids = steps.map(stepId);
  const others = ids.filter((id) => id !== selected);
  const set = (patch: Partial<WorkflowStep>) => selected && emit(updateStep(steps, selected, patch));

  return (
    <div className="flex gap-3" data-testid="workflow-builder">
      <div className="flex-1 border rounded-lg" style={{ height }}>
        <ReactFlow
          nodes={nodes}
          edges={flowEdges}
          onConnect={onConnect}
          onNodeClick={(_event, node) => setSelected(node.id)}
          onPaneClick={() => setSelected(null)}
          nodesConnectable={!readOnly}
          nodesDraggable={false}
          fitView
        >
          <Background />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable />
        </ReactFlow>
      </div>
      {!readOnly && (
        <div className="w-72 space-y-3 text-sm" data-testid="workflow-builder-panel">
          <div className="border rounded-lg p-3 space-y-2">
            <p className="font-medium">Add a step</p>
            <div className="flex flex-wrap gap-2">
              {STEP_TYPES.map((entry) => (
                <button
                  key={entry.type}
                  type="button"
                  className="border rounded px-2 py-1 text-xs hover:bg-muted"
                  onClick={() => emit(addStep(steps, entry.type))}
                  data-testid={`add-step-${entry.type}`}
                >
                  {entry.label}
                </button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              Drag from a step to another to make the second follow the first.
            </p>
          </div>
          {current && selected && (
            <div className="border rounded-lg p-3 space-y-2" data-testid="workflow-step-form">
              <p className="font-medium">Step {selected}</p>
              <TextField label="Name" value={String(current.name ?? "")} onChange={(v) => set({ name: v })} />
              {(current.type ?? "agent") === "agent" && (
                <>
                  <TextField
                    label="Agent type"
                    value={String(current.agent_type ?? "")}
                    onChange={(v) => set({ agent_type: v })}
                    testId="step-agent-type"
                  />
                  <TextField label="Action" value={String(current.action ?? "")} onChange={(v) => set({ action: v })} />
                </>
              )}
              {current.type === "human_in_loop" && (
                <>
                  <TextField
                    label="Who decides (role)"
                    value={String(current.assignee_role ?? "")}
                    onChange={(v) => set({ assignee_role: v })}
                  />
                  <TextField
                    label="Decision options (comma separated)"
                    value={((current.decision_options as string[] | undefined) ?? []).join(", ")}
                    onChange={(v) => set({ decision_options: v.split(",").map((s) => s.trim()).filter(Boolean) })}
                  />
                </>
              )}
              {current.type === "condition" && (
                <>
                  <TextField
                    label="Expression"
                    value={String(current.expression ?? "")}
                    onChange={(v) => set({ expression: v })}
                  />
                  <StepSelect
                    label="When true, go to"
                    value={String(current.true_path ?? "")}
                    ids={others}
                    onChange={(v) => set({ true_path: v })}
                    allowNone
                  />
                  <StepSelect
                    label="When false, go to"
                    value={String(current.false_path ?? "")}
                    ids={others}
                    onChange={(v) => set({ false_path: v })}
                    allowNone
                  />
                </>
              )}
              <label className="block text-xs">
                On failure
                <select
                  className="border rounded px-2 py-1 w-full"
                  value={fallbackTarget(current) ? "fallback" : String(current.on_failure ?? "halt")}
                  onChange={(e) =>
                    set({ on_failure: e.target.value === "fallback" ? `fallback(${others[0] ?? ""})` : e.target.value })
                  }
                  data-testid="step-on-failure"
                >
                  {FAILURE_DIRECTIVES.map((d) => (
                    <option key={d} value={d}>
                      {d === "fallback" ? "fallback to another step" : d}
                    </option>
                  ))}
                </select>
              </label>
              {fallbackTarget(current) && (
                <StepSelect
                  label="Fallback step"
                  value={fallbackTarget(current) ?? ""}
                  ids={others}
                  onChange={(v) => set({ on_failure: `fallback(${v})` })}
                />
              )}
              <button
                type="button"
                className="border rounded px-2 py-1 text-xs text-destructive"
                onClick={() => {
                  emit(removeStep(steps, selected));
                  setSelected(null);
                }}
                data-testid="remove-step"
              >
                Remove step
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
