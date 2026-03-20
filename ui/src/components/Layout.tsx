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
        <h1 className="text-lg font-bold mb-4">AgentFlow OS</h1>
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
