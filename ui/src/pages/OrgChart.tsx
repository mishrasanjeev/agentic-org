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

const DOMAIN_COLOR: Record<string, string> = {
  finance: "border-emerald-500/50 bg-emerald-500/5",
  hr: "border-purple-500/50 bg-purple-500/5",
  marketing: "border-amber-500/50 bg-amber-500/5",
  ops: "border-blue-500/50 bg-blue-500/5",
  backoffice: "border-slate-500/50 bg-slate-500/5",
};

function humanize(s: string) {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/* ─── Single Node Card ─── */
function NodeCard({ node, onClick }: { node: OrgNode; onClick: () => void }) {
  const displayName = node.employee_name || node.name;
  return (
    <button
      onClick={onClick}
      className={`border-2 rounded-xl px-4 py-3 min-w-[180px] max-w-[220px] text-left hover:shadow-lg transition-all cursor-pointer ${DOMAIN_COLOR[node.domain] || "border-border bg-card"}`}
    >
      <div className="flex items-center gap-2.5">
        {node.avatar_url ? (
          <img src={node.avatar_url} alt={displayName} className="w-9 h-9 rounded-full object-cover flex-shrink-0" />
        ) : (
          <div className="w-9 h-9 rounded-full bg-primary/10 flex items-center justify-center text-sm font-bold text-primary flex-shrink-0">
            {displayName.charAt(0).toUpperCase()}
          </div>
        )}
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full flex-shrink-0 ${STATUS_COLOR[node.status] || "bg-gray-400"}`} />
            <p className="text-sm font-semibold truncate">{displayName}</p>
          </div>
          <p className="text-[11px] text-muted-foreground truncate">
            {node.designation || humanize(node.agent_type)}
          </p>
        </div>
      </div>
      {node.specialization && (
        <p className="text-[10px] text-muted-foreground mt-1.5 line-clamp-2">{node.specialization}</p>
      )}
      <div className="flex gap-1 mt-2">
        <Badge variant="outline" className="text-[10px] px-1.5 py-0">{humanize(node.domain)}</Badge>
        <Badge variant="secondary" className="text-[10px] px-1.5 py-0">{node.status}</Badge>
      </div>
    </button>
  );
}

/* ─── Recursive Tree Branch ─── */
function TreeBranch({ node, onNavigate, depth }: { node: OrgNode; onNavigate: (id: string) => void; depth: number }) {
  const [collapsed, setCollapsed] = useState(depth >= 3);
  const hasChildren = node.children.length > 0;

  return (
    <div className="flex flex-col items-center">
      {/* The node card */}
      <div className="relative">
        <NodeCard node={node} onClick={() => onNavigate(node.id)} />
        {hasChildren && (
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="absolute -bottom-3 left-1/2 -translate-x-1/2 w-5 h-5 rounded-full bg-muted border border-border flex items-center justify-center text-[10px] font-bold text-muted-foreground hover:bg-primary hover:text-primary-foreground transition-colors z-10"
            title={collapsed ? `Expand (${node.children.length})` : "Collapse"}
          >
            {collapsed ? node.children.length : "\u2212"}
          </button>
        )}
      </div>

      {/* Connector line down to children */}
      {hasChildren && !collapsed && (
        <>
          <div className="w-px h-6 bg-border" />
          {/* Horizontal bar + children */}
          {node.children.length === 1 ? (
            <TreeBranch node={node.children[0]} onNavigate={onNavigate} depth={depth + 1} />
          ) : (
            <div className="flex flex-col items-center">
              {/* Horizontal connector bar */}
              <div className="flex items-start">
                {node.children.map((child, i) => (
                  <div key={child.id} className="flex flex-col items-center">
                    {/* Top connector: vertical line up to the horizontal bar */}
                    <div className="flex">
                      {/* Left half of horizontal bar */}
                      <div className={`h-px w-6 ${i === 0 ? "bg-transparent" : "bg-border"} self-start mt-0`} />
                      {/* Right half of horizontal bar */}
                      <div className={`h-px w-6 ${i === node.children.length - 1 ? "bg-transparent" : "bg-border"} self-start mt-0`} />
                    </div>
                    {/* Vertical line down to child */}
                    <div className="w-px h-4 bg-border" />
                    {/* Recurse */}
                    <TreeBranch node={child} onNavigate={onNavigate} depth={depth + 1} />
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
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
    <div className="space-y-1">
      {flat.map(({ node, level }) => (
        <button
          key={node.id}
          onClick={() => onNavigate(node.id)}
          className="flex items-center gap-3 w-full text-left px-3 py-2 rounded-lg hover:bg-muted/50 transition-colors"
          style={{ paddingLeft: `${level * 28 + 12}px` }}
        >
          {level > 0 && <span className="text-muted-foreground text-xs">{"└"}</span>}
          <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${STATUS_COLOR[node.status] || "bg-gray-400"}`} />
          {node.avatar_url ? (
            <img src={node.avatar_url} alt="" className="w-7 h-7 rounded-full object-cover" />
          ) : (
            <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center text-xs font-bold text-primary">
              {(node.employee_name || node.name).charAt(0).toUpperCase()}
            </div>
          )}
          <div className="min-w-0 flex-1">
            <span className="text-sm font-medium">{node.employee_name || node.name}</span>
            <span className="text-xs text-muted-foreground ml-2">{node.designation || humanize(node.agent_type)}</span>
          </div>
          <Badge variant="outline" className="text-[10px]">{humanize(node.domain)}</Badge>
          <Badge variant="secondary" className="text-[10px]">{node.status}</Badge>
        </button>
      ))}
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

  // Count agents with hierarchy (has parent or has children)
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
      <div className="flex gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-green-500" /> Active</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-yellow-500" /> Shadow</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-red-500" /> Paused</span>
        <span className="text-border">|</span>
        <span className="flex items-center gap-1"><span className="w-3 h-2 rounded border-2 border-emerald-500/50" /> Finance</span>
        <span className="flex items-center gap-1"><span className="w-3 h-2 rounded border-2 border-purple-500/50" /> HR</span>
        <span className="flex items-center gap-1"><span className="w-3 h-2 rounded border-2 border-amber-500/50" /> Marketing</span>
        <span className="flex items-center gap-1"><span className="w-3 h-2 rounded border-2 border-blue-500/50" /> Ops</span>
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
        <div className="overflow-x-auto pb-8">
          <div className="flex gap-12 justify-center min-w-max pt-4">
            {tree.map((root) => (
              <TreeBranch key={root.id} node={root} onNavigate={handleNavigate} depth={0} />
            ))}
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
