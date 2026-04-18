import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import ConnectorCard from "@/components/ConnectorCard";
import api from "@/lib/api";
import type { Connector } from "@/types";

const CATEGORIES = ["all", "finance", "hr", "marketing", "ops", "comms"];

// Hardcoded native-connector catalog. Phase 5 replaces this with a backend-
// served catalog so connector metadata changes do not require UI edits.
const NATIVE_CONNECTOR_CATALOG: Array<{ id: string; name: string; category: string; description: string }> = [
  { id: "salesforce", name: "Salesforce", category: "comms", description: "CRM and sales management" },
  { id: "hubspot", name: "HubSpot", category: "marketing", description: "Inbound marketing and CRM" },
  { id: "zoho_crm", name: "Zoho CRM", category: "comms", description: "Customer relationship management" },
  { id: "slack", name: "Slack", category: "comms", description: "Team messaging and collaboration" },
  { id: "microsoft_teams", name: "Microsoft Teams", category: "comms", description: "Team chat and meetings" },
  { id: "gmail", name: "Gmail", category: "comms", description: "Email communication" },
  { id: "google_workspace", name: "Google Workspace", category: "ops", description: "Productivity suite" },
  { id: "google_sheets", name: "Google Sheets", category: "ops", description: "Spreadsheets and data" },
  { id: "google_drive", name: "Google Drive", category: "ops", description: "Cloud file storage" },
  { id: "quickbooks", name: "QuickBooks", category: "finance", description: "Accounting and invoicing" },
  { id: "xero", name: "Xero", category: "finance", description: "Cloud accounting" },
  { id: "tally", name: "Tally", category: "finance", description: "Indian accounting software" },
  { id: "razorpay", name: "Razorpay", category: "finance", description: "Payment gateway (India)" },
  { id: "stripe", name: "Stripe", category: "finance", description: "Online payment processing" },
  { id: "sap", name: "SAP", category: "finance", description: "Enterprise ERP" },
  { id: "oracle_erp", name: "Oracle ERP", category: "finance", description: "Enterprise resource planning" },
  { id: "jira", name: "Jira", category: "ops", description: "Issue tracking and project management" },
  { id: "zendesk", name: "Zendesk", category: "comms", description: "Customer support platform" },
  { id: "freshdesk", name: "Freshdesk", category: "comms", description: "Customer support ticketing" },
  { id: "twilio", name: "Twilio", category: "comms", description: "SMS and voice communication" },
  { id: "sendgrid", name: "SendGrid", category: "comms", description: "Email delivery service" },
  { id: "aws_s3", name: "AWS S3", category: "ops", description: "Cloud object storage" },
  { id: "gcp_storage", name: "GCP Storage", category: "ops", description: "Google Cloud storage" },
  { id: "postgresql", name: "PostgreSQL", category: "ops", description: "Relational database" },
  { id: "mongodb", name: "MongoDB", category: "ops", description: "Document database" },
  { id: "redis", name: "Redis", category: "ops", description: "In-memory data store" },
  { id: "elasticsearch", name: "Elasticsearch", category: "ops", description: "Search and analytics engine" },
  { id: "snowflake", name: "Snowflake", category: "ops", description: "Cloud data warehouse" },
  { id: "bigquery", name: "BigQuery", category: "ops", description: "Google analytics data warehouse" },
  { id: "power_bi", name: "Power BI", category: "ops", description: "Business intelligence" },
  { id: "tableau", name: "Tableau", category: "ops", description: "Data visualization" },
  { id: "github", name: "GitHub", category: "ops", description: "Code hosting and CI/CD" },
  { id: "gitlab", name: "GitLab", category: "ops", description: "DevOps platform" },
  { id: "bitbucket", name: "Bitbucket", category: "ops", description: "Git code management" },
  { id: "confluence", name: "Confluence", category: "ops", description: "Team knowledge base" },
  { id: "notion", name: "Notion", category: "ops", description: "All-in-one workspace" },
  { id: "asana", name: "Asana", category: "ops", description: "Work management" },
  { id: "trello", name: "Trello", category: "ops", description: "Visual project management" },
  { id: "monday_com", name: "Monday.com", category: "ops", description: "Work operating system" },
  { id: "airtable", name: "Airtable", category: "ops", description: "Spreadsheet-database hybrid" },
  { id: "zapier", name: "Zapier", category: "ops", description: "Workflow automation" },
  { id: "webhook_generic", name: "Webhook (Generic)", category: "ops", description: "Generic webhook integration" },
  { id: "rest_api", name: "REST API", category: "ops", description: "Custom REST API connector" },
  { id: "graphql_api", name: "GraphQL API", category: "ops", description: "Custom GraphQL connector" },
  { id: "smtp", name: "SMTP", category: "comms", description: "Email sending protocol" },
  { id: "imap", name: "IMAP", category: "comms", description: "Email reading protocol" },
  { id: "ftp_sftp", name: "FTP/SFTP", category: "ops", description: "File transfer protocol" },
  { id: "ldap_ad", name: "LDAP / Active Directory", category: "hr", description: "Directory services" },
  { id: "okta", name: "Okta", category: "hr", description: "Identity and access management" },
  { id: "azure_ad", name: "Azure AD", category: "hr", description: "Microsoft identity platform" },
  { id: "whatsapp_business", name: "WhatsApp Business", category: "comms", description: "Business messaging" },
  { id: "indian_gstn", name: "Indian GSTN", category: "finance", description: "GST Network portal" },
  { id: "digilocker", name: "DigiLocker", category: "finance", description: "Digital document store (India)" },
  { id: "account_aggregator", name: "Account Aggregator", category: "finance", description: "Financial data sharing (India)" },
];

// ---------------------------------------------------------------------------
// Composio Marketplace — real data from /api/v1/composio/apps
// ---------------------------------------------------------------------------

interface ComposioApp {
  key: string;
  name: string;
  description: string;
  logo: string;
  categories: string[];
  enabled: boolean;
  no_auth: boolean;
}

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
  const [marketplaceCategory, setMarketplaceCategory] = useState("");
  const [marketplaceApps, setMarketplaceApps] = useState<ComposioApp[]>([]);
  const [marketplaceTotal, setMarketplaceTotal] = useState(0);
  const [marketplaceCategories, setMarketplaceCategories] = useState<string[]>([]);
  const [marketplaceLoading, setMarketplaceLoading] = useState(false);
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

  const filtered = connectors.filter(
    (c) => categoryFilter === "all" || c.category?.toLowerCase() === categoryFilter.toLowerCase()
  );

  const stats = {
    total: connectors.length,
    active: connectors.filter((c) => c.status === "active").length,
    unhealthy: connectors.filter((c) => c.status !== "active").length,
  };

  // Fetch marketplace apps from Composio API
  async function fetchMarketplace() {
    setMarketplaceLoading(true);
    try {
      const params: Record<string, string> = { limit: "200" };
      if (marketplaceSearch) params.search = marketplaceSearch;
      if (marketplaceCategory) params.category = marketplaceCategory;
      const { data } = await api.get("/composio/apps", { params });
      setMarketplaceApps(data.apps || []);
      setMarketplaceTotal(data.total || 0);
    } catch {
      setMarketplaceApps([]);
    } finally {
      setMarketplaceLoading(false);
    }
  }

  async function fetchCategories() {
    try {
      const { data } = await api.get("/composio/categories");
      setMarketplaceCategories(Array.isArray(data) ? data : []);
    } catch {
      setMarketplaceCategories([]);
    }
  }

  // Fetch when tab switches to marketplace or filters change
  useEffect(() => {
    if (activeTab === "marketplace") {
      fetchMarketplace();
      if (marketplaceCategories.length === 0) fetchCategories();
    }
  }, [activeTab, marketplaceSearch, marketplaceCategory]); // eslint-disable-line react-hooks/exhaustive-deps

  // Filtering is done server-side via query params
  const filteredMarketplace = marketplaceApps;

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
          Marketplace ({marketplaceTotal || "1000+"})
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

          {/* Browse All Native Connectors (full catalog of 54+) */}
          {!loading && (
            <div className="mt-8">
              <h3 className="text-lg font-semibold mb-4">Browse All Native Connectors ({NATIVE_CONNECTOR_CATALOG.length})</h3>
              <p className="text-sm text-muted-foreground mb-4">
                Full catalog of all supported native connectors. Click "Register" to add one to your tenant.
              </p>
              <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-3">
                {NATIVE_CONNECTOR_CATALOG
                  .filter((c) => categoryFilter === "all" || c.category?.toLowerCase() === categoryFilter.toLowerCase())
                  .map((c) => {
                    const alreadyRegistered = connectors.some((existing) =>
                      existing.id === c.id || (existing as any).connector_id === c.id || existing.name?.toLowerCase() === c.name.toLowerCase()
                    );
                    return (
                      <Card key={c.id} className="p-3">
                        <div className="flex flex-col gap-1">
                          <span className="text-sm font-medium truncate">{c.name}</span>
                          <span className="text-[10px] text-muted-foreground">{c.category}</span>
                          <span className="text-[10px] text-muted-foreground line-clamp-2">{c.description}</span>
                          {alreadyRegistered ? (
                            <Badge variant="outline" className="text-[10px] mt-1 w-fit">Registered</Badge>
                          ) : (
                            <Button
                              variant="outline"
                              size="sm"
                              className="mt-1 text-xs h-7"
                              onClick={() => navigate(`/dashboard/connectors/new?type=${c.id}`)}
                            >
                              Register
                            </Button>
                          )}
                        </div>
                      </Card>
                    );
                  })}
              </div>
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
              placeholder={`Search ${marketplaceTotal || "1000+"}  apps...`}
              className="border rounded px-3 py-2 text-sm flex-1"
            />
            <select
              data-testid="marketplace-category"
              value={marketplaceCategory}
              onChange={(e) => setMarketplaceCategory(e.target.value)}
              className="border rounded px-3 py-2 text-sm"
            >
              <option value="">All Categories</option>
              {marketplaceCategories.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>

          {marketplaceLoading ? (
            <p className="text-muted-foreground">Loading marketplace apps...</p>
          ) : filteredMarketplace.length === 0 ? (
            <p className="text-muted-foreground">No marketplace apps match your search.</p>
          ) : (
            <div className="grid grid-cols-3 gap-4">
              {filteredMarketplace.map((app) => (
                <Card key={app.key} data-testid={`composio-card-${app.key}`}>
                  <CardHeader className="pb-2">
                    <div className="flex items-center gap-3">
                      {app.logo ? (
                        <img src={app.logo} alt={app.name} className="w-10 h-10 rounded-lg object-contain" />
                      ) : (
                        <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center text-lg font-bold text-primary">
                          {app.name.charAt(0).toUpperCase()}
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <CardTitle className="text-sm">{app.name}</CardTitle>
                        <span className="text-[10px] text-muted-foreground">{(app.categories || []).join(", ")}</span>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <p className="text-xs text-muted-foreground mb-3 line-clamp-2">{app.description}</p>
                    <Button
                      size="sm"
                      variant={connectedApps.has(app.key) ? "outline" : "default"}
                      className="w-full"
                      onClick={() => handleConnect(app.key)}
                    >
                      {connectedApps.has(app.key) ? "Connected" : "Connect"}
                    </Button>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}

          {/* Total count + Composio badge */}
          <div className="flex justify-between items-center pt-4 pb-2">
            <span className="text-xs text-muted-foreground">
              Showing {filteredMarketplace.length} of {marketplaceTotal} apps
            </span>
            <span className="text-xs text-muted-foreground bg-muted/50 rounded-full px-4 py-1.5 border">
              Powered by Composio (MIT, Open Source)
            </span>
          </div>
        </>
      )}
    </div>
  );
}
