import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { agentsApi } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";

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
  isHuman?: boolean;
}

const DOMAINS = ["all", "finance", "hr", "marketing", "ops", "backoffice"];

const STATUS_COLOR: Record<string, string> = {
  active: "bg-green-500",
  shadow: "bg-yellow-500",
  paused: "bg-red-500",
  staging: "bg-blue-500",
  online: "bg-green-500",
};

const DOMAIN_BORDER: Record<string, string> = {
  finance: "border-emerald-500",
  hr: "border-purple-500",
  marketing: "border-amber-500",
  ops: "border-blue-500",
  backoffice: "border-slate-400",
  all: "border-indigo-500",
};

const DOMAIN_BG: Record<string, string> = {
  finance: "bg-emerald-50 dark:bg-emerald-950/30",
  hr: "bg-purple-50 dark:bg-purple-950/30",
  marketing: "bg-amber-50 dark:bg-amber-950/30",
  ops: "bg-blue-50 dark:bg-blue-950/30",
  backoffice: "bg-slate-50 dark:bg-slate-950/30",
  all: "bg-indigo-50 dark:bg-indigo-950/30",
};

const ROLE_LABELS: Record<string, { title: string; domain: string }> = {
  admin: { title: "CEO / Admin", domain: "all" },
  cfo: { title: "Chief Financial Officer", domain: "finance" },
  chro: { title: "Chief HR Officer", domain: "hr" },
  cmo: { title: "Chief Marketing Officer", domain: "marketing" },
  coo: { title: "Chief Operations Officer", domain: "ops" },
  auditor: { title: "Auditor", domain: "all" },
};

const DOMAIN_TO_ROLE: Record<string, string> = {
  finance: "CFO",
  hr: "CHRO",
  marketing: "CMO",
  ops: "COO",
  backoffice: "Admin",
};

function humanize(s: string) {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/* ═══ Org Chart CSS ═══ */
const orgChartStyles = `
.org-tree ul {
  padding-top: 20px;
  position: relative;
  display: flex;
  justify-content: center;
}
.org-tree li {
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
  padding: 20px 12px 0 12px;
}
.org-tree li::before {
  content: '';
  position: absolute;
  top: 0;
  width: 2px;
  height: 20px;
  background: var(--connector-color, #d1d5db);
}
.org-tree li::after {
  content: '';
  position: absolute;
  top: 0;
  width: 50%;
  height: 2px;
  background: var(--connector-color, #d1d5db);
  right: 0;
}
.org-tree li:first-child::after { left: 50%; right: auto; }
.org-tree li:last-child::after { right: 50%; left: auto; }
.org-tree li:only-child::before { height: 20px; }
.org-tree li:only-child::after { display: none; }
.org-tree li:first-child::before { border: none; }
.org-tree li:last-child::before { border: none; }
.org-tree li:not(:first-child):not(:last-child)::after { width: 100%; left: 0; }
.org-tree ul::before {
  content: '';
  position: absolute;
  top: 0;
  left: 50%;
  width: 2px;
  height: 20px;
  background: var(--connector-color, #d1d5db);
}
.org-tree > ul > li::before,
.org-tree > ul > li::after,
.org-tree > ul::before {
  display: none;
}
`;

/* ─── Node Card ─── */
function NodeCard({ node, onClick, childCount, collapsed, onToggle }: {
  node: OrgNode; onClick: () => void; childCount: number;
  collapsed: boolean; onToggle?: () => void;
}) {
  const displayName = node.employee_name || node.name;
  const borderClass = DOMAIN_BORDER[node.domain] || "border-border";
  const bgClass = DOMAIN_BG[node.domain] || "bg-card";
  const isHuman = node.isHuman;

  return (
    <div className="flex flex-col items-center">
      <button
        onClick={onClick}
        className={`border-2 ${borderClass} ${isHuman ? "bg-gradient-to-br from-slate-900 to-slate-800 ring-2 ring-indigo-500/30" : bgClass} rounded-xl px-4 py-3 w-[210px] text-left hover:shadow-xl hover:scale-105 transition-all cursor-pointer relative`}
      >
        <div className="flex items-center gap-2.5">
          <div className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0 ring-2 ring-white ${isHuman ? "bg-indigo-600 text-white" : `${bgClass} text-foreground`}`}>
            {isHuman ? displayName.split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase() : displayName.charAt(0).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${STATUS_COLOR[node.status] || "bg-gray-400"}`} />
              <p className={`text-sm font-semibold truncate leading-tight ${isHuman ? "text-white" : ""}`}>{displayName}</p>
            </div>
            <p className={`text-[11px] truncate leading-tight mt-0.5 ${isHuman ? "text-slate-300" : "text-muted-foreground"}`}>
              {node.designation || humanize(node.agent_type)}
            </p>
          </div>
        </div>
        {node.specialization && (
          <p className={`text-[10px] mt-2 line-clamp-1 italic ${isHuman ? "text-slate-400" : "text-muted-foreground"}`}>{node.specialization}</p>
        )}
        <div className="flex gap-1 mt-2">
          {isHuman ? (
            <Badge className="text-[9px] px-1.5 py-0 bg-indigo-500/20 text-indigo-300 border-0">Human</Badge>
          ) : (
            <>
              <Badge variant="outline" className="text-[9px] px-1.5 py-0 font-medium">{humanize(node.domain)}</Badge>
              <Badge variant="secondary" className="text-[9px] px-1.5 py-0">{node.status}</Badge>
            </>
          )}
          {node.org_level === 0 && !isHuman && <Badge className="text-[9px] px-1.5 py-0 bg-primary/10 text-primary border-0">Head</Badge>}
          {node.children.length > 0 && (
            <Badge className="text-[9px] px-1.5 py-0 bg-muted text-muted-foreground border-0">{node.children.length} reports</Badge>
          )}
        </div>
      </button>
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
        onClick={() => { if (!node.isHuman) onNavigate(node.id); }}
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

/* ─── List View ─── */
function ListView({ nodes, onNavigate }: { nodes: OrgNode[]; onNavigate: (id: string) => void }) {
  function flatten(node: OrgNode, level: number): Array<{ node: OrgNode; level: number }> {
    const result = [{ node, level }];
    for (const child of node.children) { result.push(...flatten(child, level + 1)); }
    return result;
  }

  const flat = nodes.flatMap((n) => flatten(n, 0));

  return (
    <div className="space-y-0.5">
      {flat.map(({ node, level }) => {
        const borderClass = DOMAIN_BORDER[node.domain] || "border-border";
        return (
          <button
            key={node.id + level}
            onClick={() => { if (!node.isHuman) onNavigate(node.id); }}
            className={`flex items-center gap-3 w-full text-left px-3 py-2.5 rounded-lg hover:bg-muted/50 transition-colors ${level === 0 ? `border-l-4 ${node.isHuman ? "border-indigo-500 bg-indigo-50/50" : borderClass}` : ""}`}
            style={{ paddingLeft: `${level * 32 + 12}px` }}
          >
            {level > 0 && <span className="text-muted-foreground/40 text-xs font-mono">{"\u2514\u2500"}</span>}
            <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${STATUS_COLOR[node.status] || "bg-gray-400"}`} />
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 ${node.isHuman ? "bg-indigo-600 text-white" : "bg-primary/10 text-primary"}`}>
              {(node.employee_name || node.name).charAt(0).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <span className="text-sm font-medium">{node.employee_name || node.name}</span>
              <span className="text-xs text-muted-foreground ml-2">{node.designation || humanize(node.agent_type)}</span>
              {node.isHuman && <Badge className="ml-2 text-[9px] px-1.5 py-0 bg-indigo-100 text-indigo-700 border-0">You</Badge>}
              {node.children.length > 0 && <span className="text-[10px] text-muted-foreground ml-2">({node.children.length} reports)</span>}
            </div>
            {!node.isHuman && <Badge variant="outline" className="text-[10px]">{humanize(node.domain)}</Badge>}
            <Badge variant="secondary" className="text-[10px]">{node.isHuman ? "online" : node.status}</Badge>
          </button>
        );
      })}
    </div>
  );
}

/* ─── Main Page ─── */
export default function OrgChart() {
  const navigate = useNavigate();
  const auth = useAuth();
  const [tree, setTree] = useState<OrgNode[]>([]);
  const [flatCount, setFlatCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [domain, setDomain] = useState("all");
  const [viewMode, setViewMode] = useState<"tree" | "list">("tree");

  const userRole = auth.user?.role || "admin";
  const userName = auth.user?.name || auth.user?.email || "You";
  const roleInfo = ROLE_LABELS[userRole] || ROLE_LABELS.admin;

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

  // Build the display tree with the human user as root
  function buildUserTree(): OrgNode[] {
    if (tree.length === 0) return [];

    if (userRole === "admin") {
      // CEO sees: themselves at top → CXO virtual nodes → department agents
      const departments: Record<string, OrgNode[]> = {};
      for (const node of tree) {
        const d = node.domain || "backoffice";
        if (!departments[d]) departments[d] = [];
        departments[d].push(node);
      }

      const cxoNodes: OrgNode[] = Object.entries(departments).map(([dept, agents]) => ({
        id: `virtual-${dept}`,
        name: DOMAIN_TO_ROLE[dept] || humanize(dept),
        employee_name: DOMAIN_TO_ROLE[dept] || humanize(dept),
        designation: `Head of ${humanize(dept)}`,
        domain: dept,
        agent_type: "department_head",
        status: "online",
        avatar_url: null,
        org_level: -1,
        parent_agent_id: null,
        specialization: `${agents.length} AI agents in ${humanize(dept)}`,
        children: agents,
        isHuman: true,
      }));

      // CEO root node
      const ceoNode: OrgNode = {
        id: "virtual-ceo",
        name: userName,
        employee_name: userName,
        designation: roleInfo.title,
        domain: "all",
        agent_type: "ceo",
        status: "online",
        avatar_url: null,
        org_level: -2,
        parent_agent_id: null,
        specialization: `${flatCount} AI agents across ${Object.keys(departments).length} departments`,
        children: cxoNodes,
        isHuman: true,
      };

      return [ceoNode];
    }

    // CXO roles (CFO, CHRO, CMO, COO): themselves at top → their department agents
    const cxoNode: OrgNode = {
      id: `virtual-${userRole}`,
      name: userName,
      employee_name: userName,
      designation: roleInfo.title,
      domain: roleInfo.domain,
      agent_type: userRole,
      status: "online",
      avatar_url: null,
      org_level: -1,
      parent_agent_id: null,
      specialization: `${tree.length} AI agents in ${humanize(roleInfo.domain)}`,
      children: tree,
      isHuman: true,
    };

    return [cxoNode];
  }

  const displayTree = buildUserTree();

  return (
    <div className="space-y-6">
      <style>{orgChartStyles}</style>
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold">Organization Chart</h2>
          <p className="text-sm text-muted-foreground mt-1">
            {userName} ({roleInfo.title}) | {flatCount} AI agents reporting to you
          </p>
        </div>
        <div className="flex items-center gap-3">
          {userRole === "admin" && (
            <select value={domain} onChange={(e) => setDomain(e.target.value)} className="border rounded px-3 py-1.5 text-sm">
              {DOMAINS.map((d) => (
                <option key={d} value={d}>{d === "all" ? "All Departments" : humanize(d)}</option>
              ))}
            </select>
          )}
          <div className="flex border rounded overflow-hidden">
            <button onClick={() => setViewMode("tree")} className={`px-3 py-1.5 text-sm ${viewMode === "tree" ? "bg-primary text-primary-foreground" : "bg-background hover:bg-muted"}`}>Tree</button>
            <button onClick={() => setViewMode("list")} className={`px-3 py-1.5 text-sm ${viewMode === "list" ? "bg-primary text-primary-foreground" : "bg-background hover:bg-muted"}`}>List</button>
          </div>
        </div>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-green-500" /> Active / Online</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-yellow-500" /> Shadow</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full bg-red-500" /> Paused</span>
        <span className="text-border">|</span>
        <span className="flex items-center gap-1"><span className="w-4 h-3 rounded border-2 border-indigo-500" /> Human</span>
        <span className="flex items-center gap-1"><span className="w-4 h-3 rounded border-2 border-emerald-500" /> Finance</span>
        <span className="flex items-center gap-1"><span className="w-4 h-3 rounded border-2 border-purple-500" /> HR</span>
        <span className="flex items-center gap-1"><span className="w-4 h-3 rounded border-2 border-amber-500" /> Marketing</span>
        <span className="flex items-center gap-1"><span className="w-4 h-3 rounded border-2 border-blue-500" /> Ops</span>
      </div>

      {loading ? (
        <p className="text-muted-foreground">Loading org chart...</p>
      ) : displayTree.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <p className="text-muted-foreground text-lg">No agents found.</p>
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
              {displayTree.map((root) => (
                <TreeNode key={root.id} node={root} onNavigate={handleNavigate} depth={0} />
              ))}
            </ul>
          </div>
        </div>
      ) : (
        <Card>
          <CardContent className="pt-4">
            <ListView nodes={displayTree} onNavigate={handleNavigate} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
