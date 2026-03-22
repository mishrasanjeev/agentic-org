import { Link, useLocation, useNavigate } from "react-router-dom";
import HITLBadge from "./HITLBadge";
import { useAuth } from "../contexts/AuthContext";

const NAV = [
  { path: "/dashboard", label: "Dashboard" }, { path: "/dashboard/agents", label: "Agents" },
  { path: "/dashboard/workflows", label: "Workflows" }, { path: "/dashboard/approvals", label: "Approvals" },
  { path: "/dashboard/connectors", label: "Connectors" }, { path: "/dashboard/schemas", label: "Schemas" },
  { path: "/dashboard/audit", label: "Audit" }, { path: "/dashboard/settings", label: "Settings" },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const auth = useAuth();

  const handleLogout = () => {
    auth.logout();
    navigate("/login");
  };

  return (
    <div className="flex h-screen">
      <aside className="w-56 border-r bg-muted/30 p-4 flex flex-col">
        <h1 className="text-lg font-bold mb-4">AgenticOrg</h1>
        <nav className="flex flex-col gap-1 flex-1">
          {NAV.map(({ path, label }) => (
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
