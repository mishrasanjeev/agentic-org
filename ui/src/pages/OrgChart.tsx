import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { agentsApi } from "@/lib/api";

interface OrgNode {
  id: string;
  name: string;
  employee_name: string | null;
  designation: string | null;
  domain: string;
  agent_type: string;
  status: string;
  avatar_url: string | null;
  org_level: number;
  parent_agent_id: string | null;
  specialization: string | null;
  children: OrgNode[];
}

const DOMAINS = ["all", "finance", "hr", "marketing", "ops", "backoffice"];

const STATUS_COLOR: Record<string, string> = {
  active: "bg-green-500",
  shadow: "bg-yellow-500",
  paused: "bg-red-500",
  staging: "bg-blue-500",
};

const DOMAIN_BORDER: Record<string, string> = {
  finance: "border-emerald-500",
  hr: "border-purple-500",
  marketing: "border-amber-500",
  ops: "border-blue-500",
  backoffice: "border-slate-400",
};

const DOMAIN_BG: Record<string, string> = {
  finance: "bg-emerald-50 dark:bg-emerald-950/30",
  hr: "bg-purple-50 dark:bg-purple-950/30",
  marketing: "bg-amber-50 dark:bg-amber-950/30",
  ops: "bg-blue-50 dark:bg-blue-950/30",
  backoffice: "bg-slate-50 dark:bg-slate-950/30",
};

function humanize(s: string) {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/* ═══ Org Chart CSS (inline style tag) ═══ */
const orgChartStyles = `
.org-tree ul {
  padding-top: 20px;
  position: relative;
  display: flex;
  justify-content: center;
  gap: 0;
}
.org-tree li {
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
  padding: 20px 12px 0 12px;
}
/* Vertical line from parent down to horizontal bar */
.org-tree li::before {
  content: '';
  position: absolute;
  top: 0;
  width: 2px;
  height: 20px;
  background: var(--connector-color, #d1d5db);
}
/* Horizontal bar connecting siblings */
.org-tree li::after {
  content: '';
  position: absolute;
  top: 0;
  width: 50%;
  height: 2px;
  background: var(--connector-color, #d1d5db);
  right: 0;
}
.org-tree li:first-child::after {
  left: 50%;
  right: auto;
}
.org-tree li:last-child::after {
  right: 50%;
  left: auto;
}
.org-tree li:only-child::before {
  height: 20px;
}
.org-tree li:only-child::after {
  display: none;
}
.org-tree li:first-child::before {
  border: none;
}
.org-tree li:last-child::before {
  border: none;
}
/* Both sides for middle children */
.org-tree li:not(:first-child):not(:last-child)::after {
  width: 100%;
  left: 0;
}
/* Vertical line from node down to its children */
.org-tree ul::before {
  content: '';
  position: absolute;
  top: 0;
  left: 50%;
  width: 2px;
  height: 20px;
  background: var(--connector-color, #d1d5db);
}
/* Root level: no connector above */
.org-tree > ul > li::before,
.org-tree > ul > li::after,
.org-tree > ul::before {
  display: none;
}
`;

/* ─── Single Node Card ─── */
function NodeCard({ node, onClick, childCount, collapsed, onToggle }: {
  node: OrgNode;
  onClick: () => void;
  childCount: number;
  collapsed: boolean;
  onToggle?: () => void;
}) {
  const displayName = node.employee_name || node.name;
  const borderClass = DOMAIN_BORDER[node.domain] || "border-border";
  const bgClass = DOMAIN_BG[node.domain] || "bg-card";

  return (
    <div className="flex flex-col items-center">
      <button
        onClick={onClick}
        className={`border-2 ${borderClass} ${bgClass} rounded-xl px-4 py-3 w-[200px] text-left hover:shadow-xl hover:scale-105 transition-all cursor-pointer relative`}
      >
        <div className="flex items-center gap-2.5">
          {node.avatar_url ? (
            <img src={node.avatar_url} alt={displayName} className="w-10 h-10 rounded-full object-cover flex-shrink-0 ring-2 ring-white" />
          ) : (
            <div className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0 ring-2 ring-white ${bgClass} text-foreground`}>
              {displayName.charAt(0).toUpperCase()}
            </div>
          )}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${STATUS_COLOR[node.status] || "bg-gray-400"}`} />
              <p className="text-sm font-semibold truncate leading-tight">{displayName}</p>
            </div>
            <p className="text-[11px] text-muted-foreground truncate leading-tight mt-0.5">
              {node.designation || humanize(node.agent_type)}
            </p>
          </div>
        </div>
        {node.specialization && (
          <p className="text-[10px] text-muted-foreground mt-2 line-clamp-1 italic">{node.specialization}</p>
        )}
        <div className="flex gap-1 mt-2">
          <Badge variant="outline" className="text-[9px] px-1.5 py-0 font-medium">{humanize(node.domain)}</Badge>
          <Badge variant="secondary" className="text-[9px] px-1.5 py-0">{node.status}</Badge>
          {node.org_level === 0 && <Badge className="text-[9px] px-1.5 py-0 bg-primary/10 text-primary border-0">Head</Badge>}
        </div>
      </button>
      {/* Expand/collapse toggle */}
      {childCount > 0 && onToggle && (
        <button
          onClick={(e) => { e.stopPropagation(); onToggle(); }}
          className="mt-1 w-6 h-6 rounded-full bg-muted border-2 border-border flex items-center justify-center text-[10px] font-bold text-muted-foreground hover:bg-primary hover:text-primary-foreground hover:border-primary transition-colors z-10"
          title={collapsed ? `Expand ${childCount} reports` : "Collapse"}
        >
          {collapsed ? `+${childCount}` : "\u2212"}
        </button>
      )}
    </div>
  );
}

/* ─── Recursive Tree Node ─── */
function TreeNode({ node, onNavigate, depth }: { node: OrgNode; onNavigate: (id: string) => void; depth: number }) {
  const [collapsed, setCollapsed] = useState(depth >= 3);
  const hasChildren = node.children.length > 0;

  return (
    <li>
      <NodeCard
        node={node}
        onClick={() => onNavigate(node.id)}
        childCount={node.children.length}
        collapsed={collapsed}
        onToggle={hasChildren ? () => setCollapsed(!collapsed) : undefined}
      />
      {hasChildren && !collapsed && (
        <ul>
          {node.children.map((child) => (
            <TreeNode key={child.id} node={child} onNavigate={onNavigate} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

/* ─── Flat List View ─── */
function ListView({ nodes, onNavigate }: { nodes: OrgNode[]; onNavigate: (id: string) => void }) {
  function flatten(node: OrgNode, level: number): Array<{ node: OrgNode; level: number }> {
    const result = [{ node, level }];
    for (const child of node.children) {
      result.push(...flatten(child, level + 1));
    }
    return result;
  }

  const flat = nodes.flatMap((n) => flatten(n, 0));

  return (
    <div className="space-y-0.5">
      {flat.map(({ node, level }) => {
        const borderClass = DOMAIN_BORDER[node.domain] || "border-border";
        return (
          <button
            key={node.id}
            onClick={() => onNavigate(node.id)}
            className={`flex items-center gap-3 w-full text-left px-3 py-2.5 rounded-lg hover:bg-muted/50 transition-colors ${level === 0 ? `border-l-4 ${borderClass}` : ""}`}
            style={{ paddingLeft: `${level * 32 + 12}px` }}
          >
            {level > 0 && (
              <span className="text-muted-foreground/40 text-xs font-mono">
                {"\u2514\u2500"}
              </span>
            )}
            <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${STATUS_COLOR[node.status] || "bg-gray-400"}`} />
            {node.avatar_url ? (
              <img src={node.avatar_url} alt="" className="w-8 h-8 rounded-full object-cover" />
            ) : (
              <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-xs font-bold text-primary">
                {(node.employee_name || node.name).charAt(0).toUpperCase()}
              </div>
            )}
            <div className="min-w-0 flex-1">
              <span className="text-sm font-medium">{node.employee_name || node.name}</span>
              <span className="text-xs text-muted-foreground ml-2">{node.designation || humanize(node.agent_type)}</span>
              {node.children.length > 0 && (
                <span className="text-[10px] text-muted-foreground ml-2">({node.children.length} reports)</span>
              )}
            </div>
            <Badge variant="outline" className="text-[10px]">{humanize(node.domain)}</Badge>
            <Badge variant="secondary" className="text-[10px]">{node.status}</Badge>
          </button>
        );
      })}
    </div>
  );
}

/* ─── Main Page ─── */
export default function OrgChart() {
  const navigate = useNavigate();
  const [tree, setTree] = useState<OrgNode[]>([]);
  const [flatCount, setFlatCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [domain, setDomain] = useState("all");
  const [viewMode, setViewMode] = useState<"tree" | "list">("tree");

  useEffect(() => {
    setLoading(true);
    const params: Record<string, string> = {};
    if (domain !== "all") params.domain = domain;
    agentsApi.orgTree(params).then(({ data }) => {
      setTree(data.tree || []);
      setFlatCount(data.flat_count || 0);
    }).catch(() => {
      setTree([]);
      setFlatCount(0);
    }).finally(() => setLoading(false));
  }, [domain]);

  function handleNavigate(id: string) {
    navigate(`/dashboard/agents/${id}`);
  }

  function countWithHierarchy(nodes: OrgNode[]): number {
    let count = 0;
    for (const n of nodes) {
      if (n.children.length > 0 || n.parent_agent_id) count++;
      count += countWithHierarchy(n.children);
    }
    return count;
  }
  const hierarchyCount = countWithHierarchy(tree);
  const rootCount = tree.length;

  return (
    <div className="space-y-6">
      <style>{orgChartStyles}</style>
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold">Organization Chart</h2>
          <p className="text-sm text-muted-foreground mt-1">
            {flatCount} agents | {rootCount} department head{rootCount !== 1 ? "s" : ""} | {hierarchyCount} in hierarchy
          </p>
        </div>
        <div className="flex items-center gap-3">
          <select
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            className="border rounded px-3 py-1.5 text-sm"
          >
            {DOMAINS.map((d) => (
              <option key={d} value={d}>{d === "all" ? "All Departments" : humanize(d)}</option>
            ))}
          </select>
          <div className="flex border rounded overflow-hidden">
            <button
              onClick={() => setViewMode("tree")}
              className={`px-3 py-1.5 text-sm ${viewMode === "tree" ? "bg-primary text-primary-foreground" : "bg-background hover:bg-muted"}`}
            >
              Tree
            </button>
            <button
              onClick={() => setViewMode("list")}
              className={`px-3 py-1.5 text-sm ${viewMode === "list" ? "bg-primary text-primary-foreground" : "bg-background hover:bg-muted"}`}
            >
              List
            </button>
          </div>
        </div>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-green-500" /> Active</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-yellow-500" /> Shadow</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-red-500" /> Paused</span>
        <span className="text-border">|</span>
        <span className="flex items-center gap-1"><span className="w-4 h-3 rounded border-2 border-emerald-500" /> Finance</span>
        <span className="flex items-center gap-1"><span className="w-4 h-3 rounded border-2 border-purple-500" /> HR</span>
        <span className="flex items-center gap-1"><span className="w-4 h-3 rounded border-2 border-amber-500" /> Marketing</span>
        <span className="flex items-center gap-1"><span className="w-4 h-3 rounded border-2 border-blue-500" /> Ops</span>
      </div>

      {loading ? (
        <p className="text-muted-foreground">Loading org chart...</p>
      ) : tree.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <p className="text-muted-foreground text-lg">No agents with hierarchy found.</p>
            <p className="text-sm text-muted-foreground mt-2">Create agents and set "Reports To" to build your org chart, or import a hierarchy via CSV.</p>
            <div className="flex gap-3 justify-center mt-4">
              <Button onClick={() => navigate("/dashboard/agents/new")}>Create Agent</Button>
              <Button variant="outline" onClick={() => navigate("/dashboard/agents")}>Go to Agent Fleet</Button>
            </div>
          </CardContent>
        </Card>
      ) : viewMode === "tree" ? (
        <div className="overflow-x-auto pb-8" style={{ "--connector-color": "hsl(var(--border))" } as React.CSSProperties}>
          <div className="org-tree min-w-max">
            <ul>
              {tree.map((root) => (
                <TreeNode key={root.id} node={root} onNavigate={handleNavigate} depth={0} />
              ))}
            </ul>
          </div>
        </div>
      ) : (
        <Card>
          <CardContent className="pt-4">
            <ListView nodes={tree} onNavigate={handleNavigate} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
