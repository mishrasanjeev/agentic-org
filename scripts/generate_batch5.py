#!/usr/bin/env python3
"""Generate batch 5: Frontend, Tests, CI/CD, Docker, Helm, Docs."""

import json
import os
import textwrap

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def w(p, c):
    full = os.path.join(BASE, p)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(c).lstrip("\n"))
    print(f"  {p}")


# ════════════════ FRONTEND ════════════════
w(
    "ui/package.json",
    json.dumps(
        {
            "name": "agenticorg-ui",
            "version": "2.0.0",
            "private": True,
            "type": "module",
            "scripts": {
                "dev": "vite",
                "build": "tsc && vite build",
                "preview": "vite preview",
                "lint": "eslint . --ext ts,tsx",
            },
            "dependencies": {
                "react": "^18.3.0",
                "react-dom": "^18.3.0",
                "react-router-dom": "^6.28.0",
                "@tanstack/react-query": "^5.62.0",
                "axios": "^1.7.0",
                "clsx": "^2.1.0",
                "tailwind-merge": "^2.6.0",
                "lucide-react": "^0.460.0",
                "recharts": "^2.14.0",
                "date-fns": "^4.1.0",
                "zod": "^3.24.0",
                "reactflow": "^11.11.0",
                "@monaco-editor/react": "^4.6.0",
                "@radix-ui/react-dialog": "^1.1.0",
                "@radix-ui/react-dropdown-menu": "^2.1.0",
                "@radix-ui/react-select": "^2.1.0",
                "@radix-ui/react-tooltip": "^1.1.0",
                "@radix-ui/react-switch": "^1.1.0",
                "@radix-ui/react-tabs": "^1.1.0",
            },
            "devDependencies": {
                "typescript": "^5.6.0",
                "vite": "^6.0.0",
                "@vitejs/plugin-react": "^4.3.0",
                "@types/react": "^18.3.0",
                "@types/react-dom": "^18.3.0",
                "tailwindcss": "^3.4.0",
                "postcss": "^8.4.0",
                "autoprefixer": "^10.4.0",
                "eslint": "^9.0.0",
            },
        },
        indent=2,
    ),
)

w(
    "ui/tsconfig.json",
    json.dumps(
        {
            "compilerOptions": {
                "target": "ES2020",
                "useDefineForClassFields": True,
                "lib": ["ES2020", "DOM", "DOM.Iterable"],
                "module": "ESNext",
                "skipLibCheck": True,
                "moduleResolution": "bundler",
                "resolveJsonModule": True,
                "isolatedModules": True,
                "noEmit": True,
                "jsx": "react-jsx",
                "strict": True,
                "noUnusedLocals": True,
                "noUnusedParameters": True,
                "baseUrl": ".",
                "paths": {"@/*": ["src/*"]},
            },
            "include": ["src"],
        },
        indent=2,
    ),
)

w(
    "ui/vite.config.ts",
    """
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: { proxy: { "/api": "http://localhost:8000", "/ws": { target: "ws://localhost:8000", ws: true } } },
});
""",
)

w(
    "ui/tailwind.config.ts",
    """
import type { Config } from "tailwindcss";
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))", background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))", primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
      },
      borderRadius: { lg: "var(--radius)", md: "calc(var(--radius) - 2px)", sm: "calc(var(--radius) - 4px)" },
    },
  },
  plugins: [],
} satisfies Config;
""",
)

w("ui/postcss.config.js", "export default { plugins: { tailwindcss: {}, autoprefixer: {} } };\n")

w(
    "ui/index.html",
    """
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" /><title>AgenticOrg</title></head>
<body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body>
</html>
""",
)

w(
    "ui/src/globals.css",
    """
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --background: 0 0% 100%; --foreground: 222 84% 5%;
    --primary: 222 47% 11%; --primary-foreground: 210 40% 98%;
    --muted: 210 40% 96%; --muted-foreground: 215 16% 47%;
    --accent: 210 40% 96%; --accent-foreground: 222 47% 11%;
    --destructive: 0 84% 60%; --destructive-foreground: 210 40% 98%;
    --border: 214 32% 91%; --radius: 0.5rem;
  }
  .dark {
    --background: 222 84% 5%; --foreground: 210 40% 98%;
    --primary: 210 40% 98%; --primary-foreground: 222 47% 11%;
    --muted: 217 33% 17%; --muted-foreground: 215 20% 65%;
    --border: 217 33% 17%;
  }
}
""",
)

w(
    "ui/src/main.tsx",
    """
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./globals.css";

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter><App /></BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
""",
)

w(
    "ui/src/App.tsx",
    """
import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Agents from "./pages/Agents";
import AgentDetail from "./pages/AgentDetail";
import Workflows from "./pages/Workflows";
import WorkflowRun from "./pages/WorkflowRun";
import Approvals from "./pages/Approvals";
import Connectors from "./pages/Connectors";
import Schemas from "./pages/Schemas";
import Audit from "./pages/Audit";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/agents/:id" element={<AgentDetail />} />
        <Route path="/workflows" element={<Workflows />} />
        <Route path="/workflows/:id/runs/:runId" element={<WorkflowRun />} />
        <Route path="/approvals" element={<Approvals />} />
        <Route path="/connectors" element={<Connectors />} />
        <Route path="/schemas" element={<Schemas />} />
        <Route path="/audit" element={<Audit />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </Layout>
  );
}
""",
)

w(
    "ui/src/lib/utils.ts",
    """
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
export function cn(...inputs: ClassValue[]) { return twMerge(clsx(inputs)); }
""",
)

w(
    "ui/src/lib/api.ts",
    """
import axios from "axios";
const api = axios.create({ baseURL: "/api/v1" });
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});
export default api;
export const agentsApi = {
  list: (params?: Record<string, string>) => api.get("/agents", { params }),
  get: (id: string) => api.get(`/agents/${id}`),
  create: (data: any) => api.post("/agents", data),
  pause: (id: string) => api.post(`/agents/${id}/pause`),
  resume: (id: string) => api.post(`/agents/${id}/resume`),
  promote: (id: string) => api.post(`/agents/${id}/promote`),
};
export const workflowsApi = {
  list: () => api.get("/workflows"),
  create: (data: any) => api.post("/workflows", data),
  run: (id: string, payload?: any) => api.post(`/workflows/${id}/run`, payload),
};
export const approvalsApi = {
  list: () => api.get("/approvals"),
  decide: (id: string, decision: string, notes: string) => api.post(`/approvals/${id}/decide`, { decision, notes }),
};
export const auditApi = { query: (params: any) => api.get("/audit", { params }) };
""",
)

w(
    "ui/src/lib/websocket.ts",
    """
export class AgenticOrgWS {
  private ws: WebSocket | null = null;
  private listeners: ((data: any) => void)[] = [];

  connect(tenantId: string) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    this.ws = new WebSocket(`${protocol}//${window.location.host}/ws/feed/${tenantId}`);
    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      this.listeners.forEach((fn) => fn(data));
    };
    this.ws.onclose = () => setTimeout(() => this.connect(tenantId), 3000);
  }

  subscribe(fn: (data: any) => void) { this.listeners.push(fn); }
  disconnect() { this.ws?.close(); }
}
""",
)

w(
    "ui/src/types/index.ts",
    """
export interface Agent {
  id: string; name: string; agent_type: string; domain: string; status: string;
  version: string; confidence_floor: number; shadow_sample_count: number;
  shadow_accuracy_current: number | null; created_at: string;
}
export interface Workflow { id: string; name: string; version: string; is_active: boolean; trigger_type: string | null; created_at: string; }
export interface WorkflowRun { id: string; workflow_def_id: string; status: string; steps_total: number; steps_completed: number; started_at: string; }
export interface HITLItem { id: string; title: string; trigger_type: string; priority: string; status: string; assignee_role: string; context: any; expires_at: string; }
export interface Connector { id: string; name: string; category: string; status: string; auth_type: string; rate_limit_rpm: number; }
export interface AuditEntry { id: string; event_type: string; actor_type: string; action: string; outcome: string; created_at: string; }
""",
)

# Shadcn UI components
w(
    "ui/src/components/ui/button.tsx",
    """
import React from "react";
import { cn } from "@/lib/utils";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "destructive" | "outline" | "ghost";
  size?: "default" | "sm" | "lg";
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "default", ...props }, ref) => {
    const variants = {
      default: "bg-primary text-primary-foreground hover:bg-primary/90",
      destructive: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
      outline: "border border-border bg-background hover:bg-accent",
      ghost: "hover:bg-accent hover:text-accent-foreground",
    };
    const sizes = { default: "h-10 px-4 py-2", sm: "h-9 px-3 text-sm", lg: "h-11 px-8" };
    return <button ref={ref} className={cn("inline-flex items-center justify-center rounded-md font-medium transition-colors disabled:opacity-50", variants[variant], sizes[size], className)} {...props} />;
  }
);
Button.displayName = "Button";
""",
)

w(
    "ui/src/components/ui/card.tsx",
    """
import React from "react";
import { cn } from "@/lib/utils";
export const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => <div ref={ref} className={cn("rounded-lg border bg-background shadow-sm", className)} {...props} />
);
export const CardHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn("flex flex-col space-y-1.5 p-6", className)} {...props} />;
export const CardTitle = ({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) => <h3 className={cn("text-2xl font-semibold", className)} {...props} />;
export const CardContent = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn("p-6 pt-0", className)} {...props} />;
""",
)

w(
    "ui/src/components/ui/badge.tsx",
    """
import React from "react";
import { cn } from "@/lib/utils";
interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> { variant?: "default" | "success" | "warning" | "destructive"; }
export function Badge({ className, variant = "default", ...props }: BadgeProps) {
  const variants = { default: "bg-primary/10 text-primary", success: "bg-green-100 text-green-800", warning: "bg-yellow-100 text-yellow-800", destructive: "bg-red-100 text-red-800" };
  return <div className={cn("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold", variants[variant], className)} {...props} />;
}
""",
)

# Core components
w(
    "ui/src/components/ApprovalCard.tsx",
    """
import { Card, CardHeader, CardTitle, CardContent } from "./ui/card";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import type { HITLItem } from "@/types";

interface Props { item: HITLItem; onDecide: (id: string, decision: string, notes: string) => void; }

export default function ApprovalCard({ item, onDecide }: Props) {
  return (
    <Card>
      <CardHeader>
        <div className="flex justify-between items-start">
          <CardTitle className="text-lg">{item.title}</CardTitle>
          <Badge variant={item.priority === "critical" ? "destructive" : "warning"}>{item.priority}</Badge>
        </div>
        <p className="text-sm text-muted-foreground">Trigger: {item.trigger_type} | Role: {item.assignee_role}</p>
      </CardHeader>
      <CardContent>
        <details className="mb-4"><summary className="cursor-pointer text-sm font-medium">Reasoning Trace</summary>
          <pre className="mt-2 text-xs bg-muted p-3 rounded overflow-auto max-h-40">{JSON.stringify(item.context, null, 2)}</pre>
        </details>
        <div className="flex gap-2">
          <Button variant="default" onClick={() => onDecide(item.id, "approve", "")}>Approve</Button>
          <Button variant="destructive" onClick={() => onDecide(item.id, "reject", "")}>Reject</Button>
          <Button variant="outline" onClick={() => onDecide(item.id, "defer", "")}>Defer</Button>
        </div>
      </CardContent>
    </Card>
  );
}
""",
)

w(
    "ui/src/components/AgentCard.tsx",
    """
import { Card, CardHeader, CardTitle, CardContent } from "./ui/card";
import { Badge } from "./ui/badge";
import type { Agent } from "@/types";

interface Props { agent: Agent; onClick?: () => void; }

export default function AgentCard({ agent, onClick }: Props) {
  const statusColor = { active: "success", shadow: "warning", paused: "destructive" }[agent.status] || "default";
  return (
    <Card className="cursor-pointer hover:shadow-md transition-shadow" onClick={onClick}>
      <CardHeader>
        <div className="flex justify-between items-center">
          <CardTitle className="text-base">{agent.name}</CardTitle>
          <Badge variant={statusColor as any}>{agent.status}</Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div>Domain: <span className="font-medium">{agent.domain}</span></div>
          <div>Version: <span className="font-medium">{agent.version}</span></div>
          <div>Confidence: <span className="font-medium">{(agent.confidence_floor * 100).toFixed(0)}%</span></div>
          <div>Shadow: <span className="font-medium">{agent.shadow_sample_count} samples</span></div>
        </div>
      </CardContent>
    </Card>
  );
}
""",
)

w(
    "ui/src/components/LiveFeed.tsx",
    """
import { useEffect, useState } from "react";
import { AgenticOrgWS } from "@/lib/websocket";

interface Props { tenantId: string; maxItems?: number; }

export default function LiveFeed({ tenantId, maxItems = 7 }: Props) {
  const [events, setEvents] = useState<any[]>([]);
  useEffect(() => {
    const ws = new AgenticOrgWS();
    ws.connect(tenantId);
    ws.subscribe((data) => setEvents((prev) => [data, ...prev].slice(0, maxItems)));
    return () => ws.disconnect();
  }, [tenantId, maxItems]);

  return (
    <div className="space-y-2">
      <h3 className="font-semibold">Live Activity</h3>
      {events.length === 0 ? <p className="text-sm text-muted-foreground">Waiting for events...</p> :
        events.map((e, i) => (
          <div key={i} className="text-sm p-2 rounded bg-muted">{e.type}: {JSON.stringify(e).slice(0, 80)}...</div>
        ))
      }
    </div>
  );
}
""",
)

w(
    "ui/src/components/HITLBadge.tsx",
    """
import { useNavigate } from "react-router-dom";

export default function HITLBadge({ count }: { count: number }) {
  const navigate = useNavigate();
  if (count === 0) return null;
  return (
    <button onClick={() => navigate("/approvals")}
      className="relative inline-flex items-center px-3 py-1 rounded-full bg-amber-100 text-amber-800 text-sm font-semibold animate-pulse"
      aria-label={`${count} pending approvals`}>{count} pending</button>
  );
}
""",
)

w(
    "ui/src/components/KillSwitch.tsx",
    """
import { useState } from "react";
import { Button } from "./ui/button";
import { agentsApi } from "@/lib/api";

interface Props { agentId: string; agentName: string; onPaused?: () => void; }

export default function KillSwitch({ agentId, agentName, onPaused }: Props) {
  const [confirming, setConfirming] = useState(false);
  const handlePause = async () => {
    await agentsApi.pause(agentId);
    setConfirming(false);
    onPaused?.();
  };

  if (confirming) return (
    <div className="flex gap-2 items-center">
      <span className="text-sm text-destructive">Pause {agentName}?</span>
      <Button variant="destructive" size="sm" onClick={handlePause}>Confirm</Button>
      <Button variant="outline" size="sm" onClick={() => setConfirming(false)}>Cancel</Button>
    </div>
  );
  return <Button variant="destructive" size="sm" onClick={() => setConfirming(true)}>Kill Switch</Button>;
}
""",
)

w(
    "ui/src/components/Layout.tsx",
    """
import { Link, useLocation } from "react-router-dom";
import HITLBadge from "./HITLBadge";

const NAV = [
  { path: "/", label: "Dashboard" }, { path: "/agents", label: "Agents" },
  { path: "/workflows", label: "Workflows" }, { path: "/approvals", label: "Approvals" },
  { path: "/connectors", label: "Connectors" }, { path: "/schemas", label: "Schemas" },
  { path: "/audit", label: "Audit" }, { path: "/settings", label: "Settings" },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  return (
    <div className="flex h-screen">
      <aside className="w-56 border-r bg-muted/30 p-4 flex flex-col gap-1">
        <h1 className="text-lg font-bold mb-4">AgenticOrg</h1>
        {NAV.map(({ path, label }) => (
          <Link key={path} to={path}
            className={`px-3 py-2 rounded text-sm ${location.pathname === path ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}>{label}</Link>
        ))}
      </aside>
      <div className="flex-1 flex flex-col">
        <header className="h-14 border-b flex items-center justify-between px-6">
          <span className="text-sm text-muted-foreground">Enterprise Agent Swarm Platform</span>
          <HITLBadge count={0} />
        </header>
        <main className="flex-1 overflow-auto p-6">{children}</main>
      </div>
    </div>
  );
}
""",
)

# Pages
w(
    "ui/src/pages/Dashboard.tsx",
    """
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import LiveFeed from "@/components/LiveFeed";

export default function Dashboard() {
  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">Dashboard</h2>
      <div className="grid grid-cols-4 gap-4">
        {[["Active Agents", "24"], ["STP Rate", "94.2%"], ["Pending HITL", "3"], ["Workflows Today", "142"]].map(([label, value]) => (
          <Card key={label}><CardHeader><CardTitle className="text-sm text-muted-foreground">{label}</CardTitle></CardHeader>
            <CardContent><p className="text-3xl font-bold">{value}</p></CardContent></Card>
        ))}
      </div>
      <LiveFeed tenantId="default" />
    </div>
  );
}
""",
)

for page, title in [
    ("Agents", "Agent Fleet"),
    ("Workflows", "Workflows"),
    ("Approvals", "Approval Queue"),
    ("Connectors", "Connectors"),
    ("Schemas", "Schema Registry"),
    ("Audit", "Audit Log"),
    ("Settings", "Settings"),
]:
    w(
        f"ui/src/pages/{page}.tsx",
        f"""
    export default function {page}() {{
      return (
        <div className="space-y-6">
          <h2 className="text-2xl font-bold">{title}</h2>
          <p className="text-muted-foreground">Manage {title.lower()} here.</p>
        </div>
      );
    }}
    """,
    )

w(
    "ui/src/pages/AgentDetail.tsx",
    """
import { useParams } from "react-router-dom";
import KillSwitch from "@/components/KillSwitch";

export default function AgentDetail() {
  const { id } = useParams();
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Agent Detail</h2>
        <KillSwitch agentId={id || ""} agentName="Agent" />
      </div>
      <p className="text-muted-foreground">Agent ID: {id}</p>
    </div>
  );
}
""",
)

w(
    "ui/src/pages/WorkflowRun.tsx",
    """
import { useParams } from "react-router-dom";

export default function WorkflowRun() {
  const { id, runId } = useParams();
  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">Workflow Run</h2>
      <p className="text-muted-foreground">Workflow: {id} | Run: {runId}</p>
    </div>
  );
}
""",
)

w(
    "ui/src/components/WorkflowBuilder.tsx",
    """
import { useCallback } from "react";
import ReactFlow, { Background, Controls, MiniMap } from "reactflow";
import "reactflow/dist/style.css";

export default function WorkflowBuilder({ definition, onChange }: { definition: any; onChange?: (d: any) => void }) {
  const nodes = (definition?.steps || []).map((s: any, i: number) => ({
    id: s.id, position: { x: 100, y: i * 120 }, data: { label: `${s.type}: ${s.id}` },
  }));
  return (
    <div style={{ height: 500 }}>
      <ReactFlow nodes={nodes} edges={[]}><Background /><Controls /><MiniMap /></ReactFlow>
    </div>
  );
}
""",
)

w(
    "ui/src/components/SchemaEditor.tsx",
    """
import Editor from "@monaco-editor/react";

export default function SchemaEditor({ schema, onChange, readOnly }: { schema: any; onChange?: (v: string) => void; readOnly?: boolean }) {
  return (
    <Editor height="400px" language="json" theme="vs-dark"
      value={JSON.stringify(schema, null, 2)}
      onChange={(v) => onChange?.(v || "")}
      options={{ readOnly, minimap: { enabled: false } }} />
  );
}
""",
)

w(
    "ui/src/components/ConnectorCard.tsx",
    """
import { Card, CardHeader, CardTitle, CardContent } from "./ui/card";
import { Badge } from "./ui/badge";
import type { Connector } from "@/types";

export default function ConnectorCard({ connector }: { connector: Connector }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex justify-between"><CardTitle className="text-base">{connector.name}</CardTitle>
          <Badge variant={connector.status === "active" ? "success" : "destructive"}>{connector.status}</Badge></div>
      </CardHeader>
      <CardContent>
        <div className="text-sm">Category: {connector.category} | Rate: {connector.rate_limit_rpm}/min</div>
      </CardContent>
    </Card>
  );
}
""",
)

# ════════════════ TESTS ════════════════
w("tests/__init__.py", "")

w(
    "tests/conftest.py",
    '''
"""Shared test fixtures."""
import pytest
import uuid

@pytest.fixture
def tenant_id():
    return str(uuid.uuid4())

@pytest.fixture
def agent_config(tenant_id):
    return {
        "id": str(uuid.uuid4()), "tenant_id": tenant_id, "agent_type": "ap_processor",
        "domain": "finance", "authorized_tools": ["oracle_fusion:read:purchase_order"],
        "prompt_variables": {"org_name": "TestCorp", "ap_hitl_threshold": "500000"},
        "hitl_condition": "total > 500000", "output_schema": "Invoice",
    }

@pytest.fixture
def sample_invoice():
    return {
        "invoice_id": "INV-001", "vendor_id": "VND-001", "total": 94000,
        "status": "matched", "gstin": "29ABCDE1234F1Z5", "confidence": 0.96,
    }
''',
)

w(
    "tests/unit/test_tool_gateway.py",
    '''
"""Tool gateway tests — scope, rate limit, idempotency, PII masking."""
import pytest
from auth.scopes import check_scope, parse_scope, validate_clone_scopes
from core.tool_gateway.pii_masker import mask_string

class TestScopeEnforcement:
    def test_read_scope_allowed(self):
        scopes = ["tool:oracle_fusion:read:purchase_order"]
        allowed, _ = check_scope(scopes, "oracle_fusion", "read", "purchase_order")
        assert allowed

    def test_write_scope_denied(self):
        scopes = ["tool:oracle_fusion:read:purchase_order"]
        allowed, _ = check_scope(scopes, "oracle_fusion", "write", "journal_entry")
        assert not allowed

    def test_capped_scope_within_limit(self):
        scopes = ["tool:banking_api:write:queue_payment:capped:500000"]
        allowed, _ = check_scope(scopes, "banking_api", "write", "queue_payment", amount=300000)
        assert allowed

    def test_capped_scope_exceeds(self):
        scopes = ["tool:banking_api:write:queue_payment:capped:500000"]
        allowed, reason = check_scope(scopes, "banking_api", "write", "queue_payment", amount=600000)
        assert not allowed
        assert "cap_exceeded" in reason

    def test_admin_scope(self):
        scopes = ["tool:okta:admin"]
        allowed, _ = check_scope(scopes, "okta", "write", "provision_user")
        assert allowed

class TestCloneScopeCeiling:
    def test_valid_clone(self):
        parent = ["tool:oracle_fusion:read:purchase_order"]
        child = ["tool:oracle_fusion:read:purchase_order"]
        assert validate_clone_scopes(parent, child) == []

    def test_scope_elevation_blocked(self):
        parent = ["tool:banking_api:write:queue_payment:capped:500000"]
        child = ["tool:banking_api:write:queue_payment:capped:1000000"]
        violations = validate_clone_scopes(parent, child)
        assert len(violations) > 0

class TestPIIMasking:
    def test_mask_email(self):
        result = mask_string("user@example.com")
        assert "example.com" not in result

    def test_mask_pan(self):
        result = mask_string("ABCDE1234F")
        assert "ABCDE" not in result

    def test_mask_aadhaar(self):
        result = mask_string("1234 5678 9012")
        assert "XXXX" in result
''',
)

w(
    "tests/unit/test_workflow_engine.py",
    '''
"""Workflow engine tests."""
import pytest
from workflows.parser import WorkflowParser
from workflows.condition_evaluator import evaluate_condition

class TestWorkflowParser:
    def test_parse_valid(self):
        parser = WorkflowParser()
        defn = {"name": "test", "steps": [{"id": "s1", "type": "agent"}]}
        result = parser.parse(defn)
        assert result["name"] == "test"

    def test_circular_dependency(self):
        parser = WorkflowParser()
        defn = {"name": "test", "steps": [
            {"id": "a", "type": "agent", "depends_on": ["b"]},
            {"id": "b", "type": "agent", "depends_on": ["a"]},
        ]}
        with pytest.raises(ValueError, match="E3006"):
            parser.parse(defn)

    def test_invalid_step_type(self):
        parser = WorkflowParser()
        defn = {"name": "test", "steps": [{"id": "s1", "type": "invalid_type"}]}
        with pytest.raises(ValueError):
            parser.parse(defn)

class TestConditionEvaluator:
    def test_greater_than(self):
        assert evaluate_condition("total > 500000", {"total": 600000})

    def test_less_than(self):
        assert not evaluate_condition("total > 500000", {"total": 400000})

    def test_equality(self):
        assert evaluate_condition("status == mismatch", {"status": "mismatch"})

    def test_or_condition(self):
        assert evaluate_condition("total > 500000 OR status == mismatch", {"total": 100, "status": "mismatch"})

    def test_and_condition(self):
        assert not evaluate_condition("total > 500000 AND status == mismatch", {"total": 100, "status": "mismatch"})
''',
)

w(
    "tests/unit/test_auth.py",
    '''
"""Auth tests — JWT, scopes, tenant isolation."""
import pytest
from auth.scopes import parse_scope

class TestScopeParsing:
    def test_read_scope(self):
        s = parse_scope("tool:oracle_fusion:read:purchase_order")
        assert s and s.connector == "oracle_fusion" and s.permission == "read"

    def test_capped_scope(self):
        s = parse_scope("tool:banking_api:write:queue_payment:capped:500000")
        assert s and s.cap == 500000

    def test_agenticorg_scope(self):
        s = parse_scope("agenticorg:agents:write")
        assert s and s.category == "agenticorg"
''',
)

w(
    "tests/security/test_auth_security.py",
    '''
"""Security tests SEC-AUTH-001 to SEC-AUTH-008."""
import pytest
from auth.scopes import check_scope

class TestScopeEnforcementSecurity:
    def test_sec_auth_002_cross_domain_denied(self):
        """AP agent cannot call HR tools."""
        scopes = ["tool:oracle_fusion:read:purchase_order"]
        allowed, _ = check_scope(scopes, "darwinbox", "read", "get_employee")
        assert not allowed

    def test_sec_auth_006_scope_elevation_ignored(self):
        """Cannot elevate scope via parameters."""
        scopes = ["tool:oracle_fusion:read:purchase_order"]
        allowed, _ = check_scope(scopes, "oracle_fusion", "write", "journal_entry")
        assert not allowed
''',
)

w(
    "tests/security/test_agent_scaling.py",
    '''
"""Scaling tests FT-SCALE-001 to FT-SCALE-015."""
import pytest
from auth.scopes import validate_clone_scopes
from scaling.lifecycle import LifecycleManager

class TestAgentLifecycle:
    def test_ft_scale_003_shadow_pass(self):
        lm = LifecycleManager()
        assert lm.can_transition("shadow", "review_ready")

    def test_ft_scale_004_shadow_fail(self):
        lm = LifecycleManager()
        assert lm.can_transition("shadow", "shadow_failing")

    def test_ft_scale_010_clone_scope_ceiling(self):
        parent = ["tool:banking_api:write:queue_payment:capped:500000"]
        child = ["tool:banking_api:write:queue_payment:capped:1000000"]
        violations = validate_clone_scopes(parent, child)
        assert len(violations) > 0

    def test_ft_scale_015_skip_shadow_blocked(self):
        lm = LifecycleManager()
        assert not lm.can_transition("draft", "active")
''',
)

# ════════════════ INFRASTRUCTURE ════════════════
w(
    "Dockerfile",
    """
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .

FROM python:3.12-slim
RUN useradd -m agenticorg
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .
USER agenticorg
EXPOSE 8000
HEALTHCHECK CMD python -c "import httpx; httpx.get('http://localhost:8000/api/v1/health')" || exit 1
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
""",
)

w(
    "Dockerfile.ui",
    """
FROM node:20-slim AS builder
WORKDIR /app
COPY ui/package.json ui/package-lock.json* ./
RUN npm ci
COPY ui/ .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY ui/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
""",
)

w(
    "ui/nginx.conf",
    """
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;
    location / { try_files $uri $uri/ /index.html; }
    location /api/ { proxy_pass http://api:8000; }
    location /ws/ { proxy_pass http://api:8000; proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; }
}
""",
)

# CI/CD
w(
    ".github/workflows/deploy.yml",
    """
name: AgenticOrg CI/CD
on:
  push: { branches: [main], tags: ["v*"] }
  pull_request: { branches: [main] }

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install ruff mypy
      - run: ruff check .
      - run: mypy --ignore-missing-imports .

  unit-tests:
    needs: lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e ".[dev]"
      - run: pytest tests/unit/ --cov=. --cov-report=xml
      - uses: codecov/codecov-action@v4

  integration-tests:
    needs: unit-tests
    runs-on: ubuntu-latest
    services:
      postgres: { image: "pgvector/pgvector:pg16", env: { POSTGRES_DB: test, POSTGRES_USER: test, POSTGRES_PASSWORD: test }, ports: ["5432:5432"] }
      redis: { image: "redis:7-alpine", ports: ["6379:6379"] }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e ".[dev]"
      - run: pytest tests/integration/

  security-scan:
    needs: lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install bandit pip-audit
      - run: bandit -r core/ connectors/ api/ auth/ -ll
      - run: pip-audit

  build:
    needs: [unit-tests, integration-tests, security-scan]
    if: github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t agenticorg:${{ github.sha }} .
      - run: docker build -t agenticorg-ui:${{ github.sha }} -f Dockerfile.ui .

  deploy-staging:
    needs: build
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    environment: staging
    steps:
      - uses: actions/checkout@v4
      - run: echo "Deploy to staging via Helm"

  e2e-tests:
    needs: deploy-staging
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "Run Playwright + API e2e tests"

  deploy-production:
    needs: e2e-tests
    if: startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: actions/checkout@v4
      - run: echo "Deploy to production via Helm with canary"
""",
)

# Helm
w(
    "helm/Chart.yaml",
    """
apiVersion: v2
name: agenticorg
description: Enterprise Agent Swarm Platform
type: application
version: 2.0.0
appVersion: "2.0.0"
""",
)

w(
    "helm/values.yaml",
    """
replicaCount:
  api: 3
  orchestrator: 3
  workers: 6

image:
  repository: agenticorg
  tag: latest
  pullPolicy: IfNotPresent

agentScaling:
  ap_processor:
    minReplicas: 2
    maxReplicas: 20
    metrics:
      - type: queue_depth
        target: 30
  recon_agent:
    minReplicas: 1
    maxReplicas: 5
    metrics:
      - type: schedule
        schedules:
          - cron: "0 22 25-31 * *"
            replicas: 4
  _default:
    minReplicas: 1
    maxReplicas: 5
    metrics:
      - type: cpu
        targetAverageUtilization: 70

fleetLimits:
  maxActiveAgents: 50
  maxAgentsPerDomain: 20
  maxShadowAgents: 10
  maxReplicasGlobalCeiling: 20

postgresql:
  enabled: true
  auth:
    database: agenticorg
redis:
  enabled: true
""",
)

w(
    "helm/templates/deployment.yaml",
    """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}-api
spec:
  replicas: {{ .Values.replicaCount.api }}
  selector:
    matchLabels:
      app: agenticorg-api
  template:
    metadata:
      labels:
        app: agenticorg-api
    spec:
      containers:
        - name: api
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          ports:
            - containerPort: 8000
          livenessProbe:
            httpGet: { path: /api/v1/health, port: 8000 }
          readinessProbe:
            httpGet: { path: /api/v1/health, port: 8000 }
""",
)

w(
    "helm/templates/service.yaml",
    """
apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}-api
spec:
  type: ClusterIP
  ports:
    - port: 8000
      targetPort: 8000
  selector:
    app: agenticorg-api
""",
)

w(
    "helm/templates/hpa.yaml",
    """
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ .Release.Name }}-api
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ .Release.Name }}-api
  minReplicas: {{ .Values.replicaCount.api }}
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: { type: Utilization, averageUtilization: 70 }
""",
)

# Project docs
w(
    "LICENSE",
    """
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
""",
)

w(
    "CONTRIBUTING.md",
    """
# Contributing to AgenticOrg

We welcome contributions! Please see our guidelines below.

## Getting Started
1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Run tests: `pytest tests/`
4. Submit a PR against `main`

## Code Standards
- Python: ruff + mypy (strict)
- TypeScript: eslint + tsc --noEmit
- Tests required for all new features
- Minimum 80% code coverage
""",
)

w(
    "SECURITY.md",
    """
# Security Policy

## Reporting a Vulnerability
Email security@agenticorg.dev with details. Do NOT open a public issue.
We will respond within 48 hours and provide a fix timeline.

## Supported Versions
| Version | Supported |
| ------- | --------- |
| 2.x     | Yes       |
| 1.x     | No        |
""",
)

w(
    "CHANGELOG.md",
    """
# Changelog

## [2.0.0] - 2026-03-21
### Added
- 24 specialist agents + NEXUS orchestrator
- 43 typed connectors (PineLabs Plural for payments, Gmail)
- Workflow engine with 9 step types
- Full PostgreSQL DDL with RLS and partitioning
- Shadow mode, agent lifecycle, cost ledger
- React 18 UI with Shadcn components
- 146 test cases
- 9-stage CI/CD pipeline
- Helm charts for Kubernetes deployment
""",
)

print("[OK] Batch 5 complete")

if __name__ == "__main__":
    print("Generating batch 5: Frontend + Tests + Infra...")
    print("[OK] Batch 5 complete")
