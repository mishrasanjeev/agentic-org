import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import ConnectorCard from "@/components/ConnectorCard";
import api from "@/lib/api";
import type { Connector } from "@/types";

const CATEGORIES = ["all", "finance", "hr", "marketing", "ops", "comms", "microsoft"];

const MICROSOFT_CONNECTORS: Connector[] = [
  { id: "ms-teams", name: "Microsoft Teams", category: "microsoft", status: "active", description: "Team chat, channels, and meetings", connector_id: "ms-teams" } as unknown as Connector,
  { id: "ms-outlook", name: "Microsoft Outlook", category: "microsoft", status: "active", description: "Email, calendar, and contacts", connector_id: "ms-outlook" } as unknown as Connector,
  { id: "ms-sharepoint", name: "Microsoft SharePoint", category: "microsoft", status: "active", description: "Document management and collaboration", connector_id: "ms-sharepoint" } as unknown as Connector,
  { id: "ms-onedrive", name: "Microsoft OneDrive", category: "microsoft", status: "active", description: "Cloud file storage and sharing", connector_id: "ms-onedrive" } as unknown as Connector,
  { id: "ms-excel-online", name: "Excel Online", category: "microsoft", status: "active", description: "Spreadsheets and data analysis", connector_id: "ms-excel-online" } as unknown as Connector,
  { id: "ms-power-bi", name: "Power BI", category: "microsoft", status: "active", description: "Business analytics and reporting", connector_id: "ms-power-bi" } as unknown as Connector,
];

// ---------------------------------------------------------------------------
// Composio Marketplace mock data
// ---------------------------------------------------------------------------
const COMPOSIO_CATEGORIES = ["All", "CRM", "HR", "Finance", "Dev Tools", "Support", "Marketing", "Productivity"];

interface ComposioApp {
  name: string;
  description: string;
  category: string;
  icon: string;
}

const COMPOSIO_APPS: ComposioApp[] = [
  { name: "Notion", description: "All-in-one workspace for notes, docs, and project management", category: "Productivity", icon: "N" },
  { name: "Asana", description: "Work management platform for teams to orchestrate work", category: "Productivity", icon: "A" },
  { name: "Trello", description: "Visual project management with boards, lists, and cards", category: "Productivity", icon: "T" },
  { name: "Monday", description: "Work operating system for managing projects and workflows", category: "Productivity", icon: "M" },
  { name: "Zoho CRM", description: "Customer relationship management for sales and marketing", category: "CRM", icon: "Z" },
  { name: "Pipedrive", description: "Sales CRM and pipeline management for small teams", category: "CRM", icon: "P" },
  { name: "Freshdesk", description: "Customer support ticketing and helpdesk software", category: "Support", icon: "F" },
  { name: "Intercom", description: "Customer messaging platform for sales, marketing, and support", category: "Support", icon: "I" },
  { name: "Shopify", description: "E-commerce platform for online stores and retail POS", category: "Finance", icon: "S" },
  { name: "Stripe", description: "Online payment processing for internet businesses", category: "Finance", icon: "S" },
  { name: "QuickBooks", description: "Accounting software for small and medium businesses", category: "Finance", icon: "Q" },
  { name: "Xero", description: "Cloud-based accounting software for small businesses", category: "Finance", icon: "X" },
  { name: "Workday", description: "Enterprise cloud for finance and human capital management", category: "HR", icon: "W" },
  { name: "BambooHR", description: "HR software for small and medium businesses", category: "HR", icon: "B" },
  { name: "Linear", description: "Issue tracking and project management for software teams", category: "Dev Tools", icon: "L" },
  { name: "ClickUp", description: "All-in-one productivity and project management platform", category: "Productivity", icon: "C" },
  { name: "Airtable", description: "Spreadsheet-database hybrid for flexible data management", category: "Productivity", icon: "A" },
  { name: "Typeform", description: "Interactive forms and surveys with conversational UI", category: "Marketing", icon: "T" },
  { name: "Calendly", description: "Scheduling automation for meetings and appointments", category: "Productivity", icon: "C" },
  { name: "Zendesk", description: "Customer service software and support ticket system", category: "Support", icon: "Z" },
  { name: "HubSpot", description: "Inbound marketing, sales, and CRM platform", category: "Marketing", icon: "H" },
  { name: "Mailchimp", description: "Email marketing and automation platform", category: "Marketing", icon: "M" },
];

export default function Connectors() {
  const navigate = useNavigate();
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [loading, setLoading] = useState(true);
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [healthResult, setHealthResult] = useState<{ id: string; msg: string; ok: boolean } | null>(null);

  // Tab state
  const [activeTab, setActiveTab] = useState<"native" | "marketplace">("native");

  // Marketplace state
  const [marketplaceSearch, setMarketplaceSearch] = useState("");
  const [marketplaceCategory, setMarketplaceCategory] = useState("All");
  const [connectedApps, setConnectedApps] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetchConnectors();
  }, []);

  async function fetchConnectors() {
    setLoading(true);
    try {
      const { data } = await api.get("/connectors");
      const raw = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : [];
      // API returns connector_id, map to id for consistency
      const items = raw.map((c: any) => ({ ...c, id: c.id || c.connector_id }));

      if (items.length > 0) {
        setConnectors(items);
      } else {
        // Fallback: show available connectors from the code registry
        // when no tenant connectors have been registered yet
        try {
          const { data: regData } = await api.get("/connectors/registry");
          const regRaw = Array.isArray(regData) ? regData : Array.isArray(regData?.items) ? regData.items : [];
          const regItems = regRaw.map((c: any) => ({ ...c, id: c.id || c.connector_id }));
          setConnectors(regItems);
        } catch {
          setConnectors([]);
        }
      }
    } catch {
      setConnectors([]);
    } finally {
      setLoading(false);
    }
  }

  async function healthCheck(id: string) {
    if (!id) return;
    setHealthResult(null);
    try {
      const { data } = await api.get(`/connectors/${id}/health`);
      const status = data.healthy ? "Healthy" : "Unhealthy";
      setHealthResult({ id, msg: `${data.name || "Connector"}: ${status} | Last check: ${data.health_check_at || "Never"}`, ok: !!data.healthy });
    } catch (err: any) {
      const detail = err.response?.data?.detail || err.message || "Unknown error";
      setHealthResult({ id, msg: `Health check failed: ${detail}`, ok: false });
    }
  }

  const baseFiltered = connectors.filter(
    (c) => categoryFilter === "all" || c.category === categoryFilter
  );
  // When "microsoft" category is selected, show Microsoft 365 mock cards
  const filtered = categoryFilter === "microsoft"
    ? MICROSOFT_CONNECTORS
    : categoryFilter === "all"
      ? [...baseFiltered, ...MICROSOFT_CONNECTORS]
      : baseFiltered;

  const stats = {
    total: connectors.length,
    active: connectors.filter((c) => c.status === "active").length,
    unhealthy: connectors.filter((c) => c.status !== "active").length,
  };

  // Marketplace filtering
  const filteredMarketplace = COMPOSIO_APPS.filter((app) => {
    const matchesSearch = !marketplaceSearch || app.name.toLowerCase().includes(marketplaceSearch.toLowerCase()) || app.description.toLowerCase().includes(marketplaceSearch.toLowerCase());
    const matchesCategory = marketplaceCategory === "All" || app.category === marketplaceCategory;
    return matchesSearch && matchesCategory;
  });

  function handleConnect(appName: string) {
    setConnectedApps((prev) => {
      const next = new Set(prev);
      if (next.has(appName)) {
        next.delete(appName);
      } else {
        next.add(appName);
      }
      return next;
    });
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Connectors</h2>
        <Button onClick={() => navigate("/dashboard/connectors/new")}>Register Connector</Button>
      </div>

      {/* Tab System */}
      <div className="flex border-b">
        <button
          data-testid="tab-native"
          onClick={() => setActiveTab("native")}
          className={`px-6 py-3 text-sm font-medium border-b-2 transition-colors ${activeTab === "native" ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-primary hover:border-primary/50"}`}
        >
          Native Connectors
        </button>
        <button
          data-testid="tab-marketplace"
          onClick={() => setActiveTab("marketplace")}
          className={`px-6 py-3 text-sm font-medium border-b-2 transition-colors ${activeTab === "marketplace" ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-primary hover:border-primary/50"}`}
        >
          Marketplace (1000+)
        </button>
      </div>

      {/* ── Native Connectors Tab ── */}
      {activeTab === "native" && (
        <>
          <div className="grid grid-cols-3 gap-4">
            <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Total</CardTitle></CardHeader><CardContent><p className="text-3xl font-bold">{stats.total}</p></CardContent></Card>
            <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Active</CardTitle></CardHeader><CardContent><p className="text-3xl font-bold text-green-600">{stats.active}</p></CardContent></Card>
            <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Unhealthy</CardTitle></CardHeader><CardContent><p className="text-3xl font-bold text-red-600">{stats.unhealthy}</p></CardContent></Card>
          </div>

          {healthResult && (
            <div className={`rounded-lg px-4 py-3 text-sm flex items-center justify-between ${healthResult.ok ? "bg-green-50 text-green-800 border border-green-200" : "bg-red-50 text-red-800 border border-red-200"}`}>
              <span>{healthResult.msg}</span>
              <button onClick={() => setHealthResult(null)} className="ml-2 text-xs underline">Dismiss</button>
            </div>
          )}

          <div className="flex gap-4 items-center">
            <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)} className="border rounded px-3 py-2 text-sm">
              {CATEGORIES.map((c) => <option key={c} value={c}>{c === "all" ? "All Categories" : c.charAt(0).toUpperCase() + c.slice(1)}</option>)}
            </select>
          </div>

          {loading ? (
            <p className="text-muted-foreground">Loading connectors...</p>
          ) : filtered.length === 0 ? (
            <p className="text-muted-foreground">No connectors found.</p>
          ) : (
            <div className="grid grid-cols-3 gap-4">
              {filtered.map((connector) => (
                <div key={connector.id} className="relative">
                  <ConnectorCard connector={connector} />
                  <Button variant="outline" size="sm" className="absolute bottom-3 right-3" onClick={() => healthCheck(connector.id)}>
                    Health Check
                  </Button>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* ── Marketplace Tab ── */}
      {activeTab === "marketplace" && (
        <>
          <div className="flex gap-4 items-center">
            <input
              data-testid="marketplace-search"
              type="text"
              value={marketplaceSearch}
              onChange={(e) => setMarketplaceSearch(e.target.value)}
              placeholder="Search 1000+ Composio tools..."
              className="border rounded px-3 py-2 text-sm flex-1"
            />
            <select
              data-testid="marketplace-category"
              value={marketplaceCategory}
              onChange={(e) => setMarketplaceCategory(e.target.value)}
              className="border rounded px-3 py-2 text-sm"
            >
              {COMPOSIO_CATEGORIES.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>

          {filteredMarketplace.length === 0 ? (
            <p className="text-muted-foreground">No marketplace tools match your search.</p>
          ) : (
            <div className="grid grid-cols-3 gap-4">
              {filteredMarketplace.map((app) => (
                <Card key={app.name} data-testid={`composio-card-${app.name.toLowerCase().replace(/\s+/g, "-")}`}>
                  <CardHeader className="pb-2">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center text-lg font-bold text-primary">
                        {app.icon}
                      </div>
                      <div className="flex-1 min-w-0">
                        <CardTitle className="text-sm">{app.name}</CardTitle>
                        <span className="text-[10px] text-muted-foreground">{app.category}</span>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <p className="text-xs text-muted-foreground mb-3">{app.description}</p>
                    <Button
                      size="sm"
                      variant={connectedApps.has(app.name) ? "outline" : "default"}
                      className="w-full"
                      onClick={() => handleConnect(app.name)}
                    >
                      {connectedApps.has(app.name) ? "Connected" : "Connect"}
                    </Button>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}

          {/* Composio badge */}
          <div className="flex justify-center pt-4 pb-2">
            <span className="text-xs text-muted-foreground bg-muted/50 rounded-full px-4 py-1.5 border">
              Powered by Composio (MIT)
            </span>
          </div>
        </>
      )}
    </div>
  );
}
