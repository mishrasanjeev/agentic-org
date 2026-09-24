import { useState, useEffect } from "react";
import { useNavigate } from "react-router";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import ConnectorCard from "@/components/ConnectorCard";
import api, { extractApiError } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { canManageConnector, isAdminUser } from "@/lib/roles";
import type { Connector } from "@/types";

const CATEGORIES = ["all", "finance", "hr", "marketing", "ops", "comms"];

/**
 * Native connector catalog item â€” shape returned by
 * `GET /api/v1/connectors/registry` (Enterprise Readiness P5 PR-B2).
 * Prior to this PR the UI embedded a hardcoded array of 55 connectors
 * that drifted away from the runtime registry; the catalog is now
 * backend-served so a connector add/rename/reclass doesn't require UI
 * code changes.
 */
interface NativeCatalogItem {
  id: string;
  connector_id: string;
  name: string;
  display_name: string;
  category: string;
  description: string;
  auth_type?: string;
}

// ---------------------------------------------------------------------------
// Composio Marketplace â€” real data from /api/v1/composio/apps
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
  // Bug sheet 2026-09-14 rows 17-19/22: non-admin roles see shared and their
  // own personal connectors; health/archive only on rows they manage.
  const { user } = useAuth();
  const isAdmin = isAdminUser(user);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [connectorPage, setConnectorPage] = useState(1);
  const [connectorTotal, setConnectorTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [connectorsError, setConnectorsError] = useState<string | null>(null);
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
  const [marketplaceError, setMarketplaceError] = useState<string | null>(null);

  // Native-connector catalog sourced from /api/v1/connectors/registry â€” the
  // single source of truth (runtime registry + connectors/catalog_meta.py).
  const [nativeCatalog, setNativeCatalog] = useState<NativeCatalogItem[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);

  useEffect(() => {
    fetchConnectors();
    fetchNativeCatalog();
  }, []);

  async function fetchNativeCatalog() {
    setCatalogLoading(true);
    setCatalogError(null);
    try {
      const { data } = await api.get<{ items: NativeCatalogItem[]; total: number }>(
        "/connectors/registry",
      );
      setNativeCatalog(Array.isArray(data?.items) ? data.items : []);
    } catch (err: unknown) {
      setNativeCatalog([]);
      setCatalogError(extractApiError(err, "Failed to load connector catalog"));
    } finally {
      setCatalogLoading(false);
    }
  }

  async function fetchConnectors(page = connectorPage) {
    setLoading(true);
    setConnectorsError(null);
    try {
      const { data } = await api.get("/connectors", { params: { page, per_page: 50 } });
      const raw = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : [];
      // API returns connector_id, map to id for consistency
      const items = raw.map((c: any) => ({ ...c, id: c.id || c.connector_id }));
      // No registry fallback here: the catalog below is the place to browse
      // unregistered connectors. Substituting registry entries for tenant
      // connectors fabricated stats and produced Edit/Archive buttons that
      // pointed at non-existent instances.
      setConnectors(items);
      setConnectorTotal(typeof data?.total === "number" ? data.total : items.length);
      setConnectorPage(page);
    } catch (err: unknown) {
      setConnectors([]);
      setConnectorTotal(0);
      setConnectorsError(extractApiError(err, "Failed to load connectors"));
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
      await fetchConnectors();
    } catch (err: any) {
      const detail = err.response?.data?.detail || err.message || "Unknown error";
      setHealthResult({ id, msg: `Health check failed: ${detail}`, ok: false });
    }
  }

  async function deleteConnector(id: string, name: string) {
    // Uday 2026-04-22: Connectors page had no way to remove stale
    // instances. Backend exposes soft-delete via DELETE /connectors/{id};
    // confirm + call + refetch.
    if (!id) return;
    const ok = window.confirm(
      `Archive the connector "${name}"?\n\n` +
        "The connector will be marked inactive and hidden from this list. " +
        "Any agent tool references remain intact so you can restore via " +
        "Edit if needed. Continue?",
    );
    if (!ok) return;
    try {
      await api.delete(`/connectors/${id}`);
      await fetchConnectors();
    } catch (err: any) {
      const detail = err.response?.data?.detail || err.message || "Unknown error";
      setHealthResult({ id, msg: `Archive failed: ${detail}`, ok: false });
    }
  }

  const filtered = connectors.filter(
    (c) => categoryFilter === "all" || c.category?.toLowerCase() === categoryFilter.toLowerCase()
  );

  const stats = {
    recentlyChecked: connectors.filter((c) => c.readiness?.state === "recent_health").length,
    needsAttention: connectors.filter((c) => c.readiness?.state !== "recent_health").length,
  };

  // Fetch marketplace apps from Composio API
  async function fetchMarketplace() {
    setMarketplaceLoading(true);
    setMarketplaceError(null);
    try {
      const params: Record<string, string> = { limit: "200" };
      if (marketplaceSearch) params.search = marketplaceSearch;
      if (marketplaceCategory) params.category = marketplaceCategory;
      const { data } = await api.get("/composio/apps", { params });
      const apps: ComposioApp[] = Array.isArray(data?.apps) ? data.apps : [];
      setMarketplaceApps(apps);
      setMarketplaceTotal(typeof data?.total === "number" ? data.total : apps.length);
    } catch (err: unknown) {
      setMarketplaceApps([]);
      setMarketplaceTotal(0);
      setMarketplaceError(extractApiError(err, "Failed to load marketplace apps"));
    } finally {
      setMarketplaceLoading(false);
    }
  }

  async function fetchCategories() {
    try {
      const { data } = await api.get("/composio/categories");
      setMarketplaceCategories(Array.isArray(data) && data.length > 0
        ? data
        : ["crm", "comms", "finance", "marketing", "ops", "productivity"]);
    } catch {
      setMarketplaceCategories(["crm", "comms", "finance", "marketing", "ops", "productivity"]);
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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap justify-between items-center gap-3">
        <h2 className="text-2xl font-bold">Connectors</h2>
        <div className="flex flex-wrap gap-2">
          {isAdmin && (
            <Button variant="outline" onClick={() => navigate("/dashboard/connectors/cmo-vendor-sandbox")}>
              CMO Sandbox Setup
            </Button>
          )}
          <Button onClick={() => navigate("/dashboard/connectors/new")}>Register Connector</Button>
        </div>
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
          Marketplace{marketplaceTotal > 0 ? ` (${marketplaceTotal})` : ""}
        </button>
      </div>

      {/* â”€â”€ Native Connectors Tab â”€â”€ */}
      {activeTab === "native" && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Registered</CardTitle></CardHeader><CardContent><p className="text-3xl font-bold tabular-nums">{connectorTotal}</p></CardContent></Card>
            <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Recently checked on page</CardTitle></CardHeader><CardContent><p className="text-3xl font-bold text-green-600 tabular-nums">{stats.recentlyChecked}</p></CardContent></Card>
            <Card><CardHeader><CardTitle className="text-sm text-muted-foreground">Needs attention on page</CardTitle></CardHeader><CardContent><p className="text-3xl font-bold tabular-nums">{stats.needsAttention}</p></CardContent></Card>
          </div>
          <p className="text-xs text-muted-foreground">A recent health check is not proof of provider scopes, contract access, or production readiness.</p>

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

          {/* TC_005 (Aishwarya 2026-04-23): the three action buttons
              (Edit / Health Check / Archive) were absolutely
              positioned at bottom-right of each card, which caused
              them to overlap the card content (category/auth/rate
              row) at 100% zoom and on narrower viewports. Render
              them in a dedicated flex row below the card. */}
          {loading ? (
            <p className="text-muted-foreground">Loading connectors...</p>
          ) : connectorsError ? (
            <div
              className="rounded-lg px-4 py-3 text-sm bg-red-50 text-red-800 border border-red-200 flex items-center justify-between"
              data-testid="connectors-error"
            >
              <span>Failed to load connectors: {connectorsError}</span>
              <Button variant="outline" size="sm" onClick={() => fetchConnectors()}>Retry</Button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border p-6 text-center" data-testid="connectors-empty">
              <p className="text-sm font-medium">
                {connectors.length === 0
                  ? "No connectors registered for this tenant yet."
                  : "No registered connectors in this category."}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                Pick one from the catalog below or use Register Connector to add your own.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {filtered.map((connector) => {
                const manageable = canManageConnector(user, connector);
                return (
                  <div key={connector.id} className="flex flex-col gap-2">
                    <ConnectorCard connector={connector} />
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => navigate(`/dashboard/connectors/${connector.id}`)}
                        data-testid={`connector-edit-${connector.name || connector.id}`}
                      >
                        {manageable ? "Edit" : "View"}
                      </Button>
                      {manageable && (
                        <>
                          <Button variant="outline" size="sm" onClick={() => healthCheck(connector.id)}>
                            Health Check
                          </Button>
                          <Button
                            variant="destructive"
                            size="sm"
                            onClick={() => deleteConnector(connector.id, connector.name)}
                          >
                            Archive
                          </Button>
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {!loading && !connectorsError && connectorTotal > 50 && (
            <div className="flex items-center justify-end gap-3 text-sm" aria-label="Connector pages">
              <Button variant="outline" size="sm" disabled={connectorPage === 1} onClick={() => fetchConnectors(connectorPage - 1)}>Previous</Button>
              <span>Page {connectorPage} of {Math.ceil(connectorTotal / 50)}</span>
              <Button variant="outline" size="sm" disabled={connectorPage * 50 >= connectorTotal} onClick={() => fetchConnectors(connectorPage + 1)}>Next</Button>
            </div>
          )}

          {/* Browse All Native Connectors â€” catalog from /api/v1/connectors/registry */}
          {!loading && (
            <div className="mt-8" data-testid="native-catalog">
              <h3 className="text-lg font-semibold mb-4">
                Browse All Native Connectors{nativeCatalog.length > 0 ? ` (${nativeCatalog.length})` : ""}
              </h3>
              <p className="text-sm text-muted-foreground mb-4">
                Full catalog of supported native connectors. Click "Register" to add one to your tenant.
              </p>
              {catalogLoading && nativeCatalog.length === 0 ? (
                <p className="text-sm text-muted-foreground">Loading catalogâ€¦</p>
              ) : catalogError ? (
                <p className="text-sm text-red-700" data-testid="native-catalog-error">
                  Catalog unavailable: {catalogError}
                </p>
              ) : nativeCatalog.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Catalog is empty. Check that /api/v1/connectors/registry is reachable.
                </p>
              ) : (
                <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-3">
                  {nativeCatalog
                    .filter((c) =>
                      categoryFilter === "all"
                        || c.category?.toLowerCase() === categoryFilter.toLowerCase())
                    .map((c) => {
                      const alreadyRegistered = connectors.some((existing) =>
                        existing.id === c.id
                          || (existing as { connector_id?: string }).connector_id === c.id
                          || existing.name?.toLowerCase() === c.name.toLowerCase(),
                      );
                      return (
                        <Card key={c.id} className="p-3" data-testid={`catalog-item-${c.name}`}>
                          <div className="flex flex-col gap-1">
                            <span className="text-sm font-medium truncate">{c.display_name}</span>
                            <span className="text-[10px] text-muted-foreground">{c.category}</span>
                            <span className="text-[10px] text-muted-foreground line-clamp-2">
                              {c.description}
                            </span>
                            {alreadyRegistered ? (
                              <Badge variant="outline" className="text-[10px] mt-1 w-fit">
                                Registered
                              </Badge>
                            ) : (
                              <Button
                                variant="outline"
                                size="sm"
                                className="mt-1 text-xs h-7"
                                onClick={() =>
                                  navigate(`/dashboard/connectors/new?type=${c.name}`)
                                }
                              >
                                Register
                              </Button>
                            )}
                          </div>
                        </Card>
                      );
                    })}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* â”€â”€ Marketplace Tab â”€â”€ */}
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
          ) : marketplaceError ? (
            <div
              className="rounded-lg px-4 py-3 text-sm bg-red-50 text-red-800 border border-red-200 flex items-center justify-between"
              data-testid="marketplace-error"
            >
              <span>Failed to load marketplace apps: {marketplaceError}</span>
              <Button variant="outline" size="sm" onClick={() => fetchMarketplace()}>Retry</Button>
            </div>
          ) : filteredMarketplace.length === 0 ? (
            <p className="text-muted-foreground" data-testid="marketplace-empty">
              {marketplaceSearch || marketplaceCategory
                ? "No marketplace apps match your search."
                : "No marketplace apps are available for this tenant."}
            </p>
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
                      variant="outline"
                      className="w-full"
                      disabled
                      title="OAuth connect is not wired up yet"
                      data-testid={`marketplace-connect-${app.key}`}
                    >
                      {app.enabled ? "Connected" : "Connect (coming soon)"}
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
