import { Link, useLocation, useNavigate } from "react-router-dom";
import HITLBadge from "./HITLBadge";
import { useAuth } from "../contexts/AuthContext";

const ALL_NAV = [
  { path: "/dashboard", label: "Dashboard", roles: ["admin", "cfo", "chro", "cmo", "coo", "auditor"] },
  { path: "/dashboard/observatory", label: "Observatory", roles: ["admin", "cfo", "chro", "cmo", "coo"] },
  { path: "/dashboard/agents", label: "Agents", roles: ["admin", "cfo", "chro", "cmo", "coo"] },
  { path: "/dashboard/org-chart", label: "Org Chart", roles: ["admin", "cfo", "chro", "cmo", "coo"] },
  { path: "/dashboard/workflows", label: "Workflows", roles: ["admin", "cfo", "chro", "cmo", "coo"] },
  { path: "/dashboard/approvals", label: "Approvals", roles: ["admin", "cfo", "chro", "cmo", "coo"] },
  { path: "/dashboard/connectors", label: "Connectors", roles: ["admin"] },
  { path: "/dashboard/prompt-templates", label: "Prompt Templates", roles: ["admin"] },
  { path: "/dashboard/sales", label: "Sales Pipeline", roles: ["admin"] },
  { path: "/dashboard/schemas", label: "Schemas", roles: ["admin"] },
  { path: "/dashboard/audit", label: "Audit Log", roles: ["admin", "cfo", "chro", "cmo", "coo", "auditor"] },
  { path: "/dashboard/sla", label: "SLA Monitor", roles: ["admin"] },
  { path: "/dashboard/settings", label: "Settings", roles: ["admin"] },
];

const ROLE_LABELS: Record<string, { title: string; domain: string }> = {
  cfo: { title: "CFO", domain: "Finance" },
  chro: { title: "CHRO", domain: "HR" },
  cmo: { title: "CMO", domain: "Marketing" },
  coo: { title: "COO", domain: "Operations" },
  admin: { title: "CEO/Admin", domain: "All Domains" },
  auditor: { title: "Auditor", domain: "Read-only" },
};

export default function Layout({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const auth = useAuth();

  const handleLogout = () => {
    auth.logout();
    navigate("/login");
  };

  const userRole = auth.user?.role || "";
  const filteredNav = ALL_NAV.filter(item => item.roles.includes(userRole));
  const roleLabel = ROLE_LABELS[userRole];

  return (
    <div className="flex h-screen">
      <aside className="w-56 border-r bg-muted/30 p-4 flex flex-col">
        <h1 className="text-lg font-bold mb-4">AgenticOrg</h1>
        <nav className="flex flex-col gap-1 flex-1">
          {filteredNav.map(({ path, label }) => (
            <Link key={path} to={path}
              className={`px-3 py-2 rounded text-sm ${location.pathname === path ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}>{label}</Link>
          ))}
        </nav>
        <div className="border-t pt-3 mt-3">
          {auth.user && (
            <div className="px-3 py-1 mb-2">
              <p className="text-sm font-medium truncate">{auth.user.name || auth.user.email}</p>
              {auth.user.name && (
                <p className="text-xs text-muted-foreground truncate">{auth.user.email}</p>
              )}
              {roleLabel && (
                <span className="inline-block mt-1 px-2 py-0.5 rounded-full bg-primary/10 text-primary text-[10px] font-semibold">
                  {roleLabel.title} | {roleLabel.domain}
                </span>
              )}
            </div>
          )}
          <button
            onClick={handleLogout}
            className="w-full px-3 py-2 rounded text-sm text-left hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
          >
            Logout
          </button>
        </div>
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
