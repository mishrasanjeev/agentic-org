import { useState, lazy, Suspense } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import HITLBadge from "./HITLBadge";
import { useAuth } from "../contexts/AuthContext";

const NLQueryBar = lazy(() => import("./NLQueryBar"));
const ChatPanel = lazy(() => import("./ChatPanel"));
const CompanySwitcher = lazy(() => import("./CompanySwitcher"));

const ALL_NAV = [
  { path: "/dashboard", label: "Dashboard", roles: ["admin", "cfo", "chro", "cmo", "coo", "auditor"] },
  { path: "/dashboard/cfo", label: "Finance Dashboard", roles: ["admin", "cfo"] },
  { path: "/dashboard/cmo", label: "Marketing Dashboard", roles: ["admin", "cmo"] },
  { path: "/dashboard/observatory", label: "Observatory", roles: ["admin", "cfo", "chro", "cmo", "coo"] },
  { path: "/dashboard/agents", label: "Agents", roles: ["admin", "cfo", "chro", "cmo", "coo"] },
  { path: "/dashboard/org-chart", label: "Org Chart", roles: ["admin", "cfo", "chro", "cmo", "coo"] },
  { path: "/dashboard/workflows", label: "Workflows", roles: ["admin", "cfo", "chro", "cmo", "coo"] },
  { path: "/dashboard/approvals", label: "Approvals", roles: ["admin", "cfo", "chro", "cmo", "coo"] },
  { path: "/dashboard/connectors", label: "Connectors", roles: ["admin"] },
  { path: "/dashboard/prompt-templates", label: "Prompt Templates", roles: ["admin"] },
  { path: "/dashboard/agents/from-sop", label: "Create from SOP", roles: ["admin"] },
  { path: "/dashboard/integrations", label: "A2A / MCP", roles: ["admin"] },
  { path: "/dashboard/sales", label: "Sales Pipeline", roles: ["admin"] },
  { path: "/dashboard/schemas", label: "Schemas", roles: ["admin"] },
  { path: "/dashboard/report-schedules", label: "Report Schedules", roles: ["admin", "cfo", "cmo"] },
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
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);

  const handleLogout = () => {
    auth.logout();
    navigate("/login");
  };

  const userRole = auth.user?.role || "";
  const filteredNav = ALL_NAV.filter(item => item.roles.includes(userRole));
  const roleLabel = ROLE_LABELS[userRole];

  const sidebar = (
    <>
      <h1 className="text-lg font-bold mb-4 px-1">AgenticOrg</h1>
      <nav className="flex flex-col gap-1 flex-1 overflow-y-auto">
        {filteredNav.map(({ path, label }) => (
          <Link key={path} to={path}
            onClick={() => setSidebarOpen(false)}
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
    </>
  );

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar - desktop */}
      <aside className="hidden lg:flex w-56 border-r bg-muted/30 p-4 flex-col flex-shrink-0">
        {sidebar}
      </aside>

      {/* Sidebar - mobile slide-out */}
      <aside
        className={`fixed inset-y-0 left-0 z-50 w-64 bg-background border-r p-4 flex flex-col transform transition-transform duration-200 ease-in-out lg:hidden ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between mb-2">
          <span className="text-lg font-bold">AgenticOrg</span>
          <button
            onClick={() => setSidebarOpen(false)}
            className="p-1 rounded hover:bg-muted"
            aria-label="Close menu"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
        <nav className="flex flex-col gap-1 flex-1 overflow-y-auto">
          {filteredNav.map(({ path, label }) => (
            <Link key={path} to={path}
              onClick={() => setSidebarOpen(false)}
              className={`px-3 py-2 rounded text-sm ${location.pathname === path ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}>{label}</Link>
          ))}
        </nav>
        <div className="border-t pt-3 mt-3">
          {auth.user && (
            <div className="px-3 py-1 mb-2">
              <p className="text-sm font-medium truncate">{auth.user.name || auth.user.email}</p>
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

      {/* Main content area */}
      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-14 border-b flex items-center justify-between px-4 sm:px-6 flex-shrink-0">
          {/* Hamburger button - mobile only */}
          <div className="flex items-center gap-3">
            <button
              onClick={() => setSidebarOpen(true)}
              className="lg:hidden p-1.5 rounded hover:bg-muted"
              aria-label="Open menu"
            >
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
            </button>
            <Suspense fallback={null}>
              <CompanySwitcher />
            </Suspense>
          </div>
          <div className="flex items-center gap-3">
            <Suspense fallback={null}>
              <NLQueryBar onOpenChat={() => setChatOpen(true)} />
            </Suspense>
            <HITLBadge count={0} />
          </div>
        </header>
        <main className="flex-1 overflow-auto p-4 sm:p-6">{children}</main>
      </div>

      {/* Chat slide-out panel */}
      <Suspense fallback={null}>
        <ChatPanel open={chatOpen} onClose={() => setChatOpen(false)} />
      </Suspense>
    </div>
  );
}
