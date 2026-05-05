import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { useTranslation } from "react-i18next";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface ClientHealth {
  id: string;
  name: string;
  health_score: number;
  pending_filings: number;
  overdue_filings: number;
  status: string;
  subscription: string;
}

interface Deadline {
  id: string;
  type: string;
  period: string;
  company: string;
  due_date: string;
}

interface PartnerSummary {
  total_clients: number;
  active_clients: number;
  avg_health_score: number;
  total_pending_filings: number;
  total_overdue: number;
  revenue_per_month_inr: number;
}


/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function healthColor(score: number): string {
  if (score >= 80) return "bg-emerald-500";
  if (score >= 50) return "bg-amber-500";
  return "bg-red-500";
}

function healthTextColor(score: number): string {
  if (score >= 80) return "text-emerald-600";
  if (score >= 50) return "text-amber-600";
  return "text-red-600";
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function PartnerDashboard() {
  const { t } = useTranslation();
  const [clients, setClients] = useState<ClientHealth[]>([]);
  const [deadlines, setDeadlines] = useState<Deadline[]>([]);
  const [summary, setSummary] = useState<PartnerSummary>({
    total_clients: 0,
    active_clients: 0,
    avg_health_score: 0,
    total_pending_filings: 0,
    total_overdue: 0,
    revenue_per_month_inr: 0,
  });
  const [loading, setLoading] = useState(true);

  const fetchPartnerData = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get("/partner-dashboard");
      const data = (res.data || {}) as Record<string, unknown>;

      setSummary({
        total_clients: Number(data.total_clients || 0),
        active_clients: Number(data.active_clients || 0),
        avg_health_score: Number(data.avg_health_score || 0),
        total_pending_filings: Number(data.total_pending_filings || 0),
        total_overdue: Number(data.total_overdue || 0),
        revenue_per_month_inr: Number(data.revenue_per_month_inr || 0),
      });

      if (Array.isArray(data.clients)) {
        setClients(data.clients.map((c: Record<string, unknown>) => ({
          id: String(c.id || c.company_id || ""),
          name: String(c.name || c.company_name || ""),
          health_score: Number(c.health_score ?? c.client_health_score ?? 0),
          pending_filings: Number(c.pending_filings ?? c.pending_approvals ?? 0),
          overdue_filings: Number(c.overdue_filings ?? 0),
          status: c.is_active === false ? "inactive" : String(c.status || "active"),
          subscription: String(c.subscription || c.subscription_status || "active"),
        })));
      } else {
        setClients([]);
      }

      const rawDeadlines = Array.isArray(data.deadlines)
        ? data.deadlines
        : Array.isArray(data.upcoming_deadlines)
          ? data.upcoming_deadlines
          : [];

      if (rawDeadlines.length > 0) {
        setDeadlines(rawDeadlines.map((d: Record<string, unknown>, index: number) => ({
          id: String(d.id || `${d.company_name || d.company || "company"}-${d.deadline_type || d.type || index}`),
          type: String(d.type || d.deadline_type || ""),
          period: String(d.period || d.filing_period || ""),
          company: String(d.company || d.company_name || ""),
          due_date: String(d.due_date || ""),
        })));
      } else {
        setDeadlines([]);
      }
    } catch {
      // API unavailable, show empty state
      setClients([]);
      setDeadlines([]);
      setSummary({
        total_clients: 0,
        active_clients: 0,
        avg_health_score: 0,
        total_pending_filings: 0,
        total_overdue: 0,
        revenue_per_month_inr: 0,
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPartnerData();
  }, [fetchPartnerData]);

  const totalClients = summary.total_clients > 0 ? summary.total_clients : clients.length;
  const activeClients = summary.active_clients > 0
    ? summary.active_clients
    : clients.filter((c) => c.status === "active").length;
  const avgHealth = summary.avg_health_score;
  const totalPending = summary.total_pending_filings > 0
    ? summary.total_pending_filings
    : clients.reduce((sum, c) => sum + c.pending_filings, 0);
  const overdueFilings = summary.total_overdue > 0
    ? summary.total_overdue
    : clients.reduce((sum, c) => sum + c.overdue_filings, 0);

  const filedCount = clients.filter((c) => c.pending_filings === 0 && c.overdue_filings === 0 && c.status === "active").length;
  const filedWidth = totalClients > 0 ? (filedCount / totalClients) * 100 : 0;
  const pendingWidth = totalClients > 0 ? Math.min(100, (totalPending / totalClients) * 100) : 0;
  const overdueWidth = totalClients > 0 ? Math.min(100, (overdueFilings / totalClients) * 100) : 0;

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <p className="text-muted-foreground">{t("partnerDashboard.loading")}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Helmet>
        <title>{t("partnerDashboard.title")} | AgenticOrg</title>
      </Helmet>

      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-2xl font-bold">{t("partnerDashboard.title")}</h2>
          <p className="text-sm text-muted-foreground mt-1">
            {t("partnerDashboard.subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link to="/dashboard/companies">
            <Button variant="outline">{t("partnerDashboard.viewAllClients")}</Button>
          </Link>
          <Link to="/dashboard/companies/new">
            <Button>{t("partnerDashboard.addClient")}</Button>
          </Link>
        </div>
      </div>

      {/* Summary KPI Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold">{totalClients}</p>
            <p className="text-xs text-muted-foreground">{t("partnerDashboard.totalClients")}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold text-emerald-600">{activeClients}</p>
            <p className="text-xs text-muted-foreground">{t("partnerDashboard.active")}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className={`text-2xl font-bold ${healthTextColor(avgHealth)}`}>{avgHealth}%</p>
            <p className="text-xs text-muted-foreground">{t("partnerDashboard.avgHealthScore")}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold text-amber-600">{totalPending}</p>
            <p className="text-xs text-muted-foreground">{t("partnerDashboard.pendingFilings")}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold text-red-600">{overdueFilings}</p>
            <p className="text-xs text-muted-foreground">{t("partnerDashboard.overdueFilings")}</p>
          </CardContent>
        </Card>
      </div>

      {/* Revenue Card */}
      <Card>
        <CardContent className="pt-4 pb-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">{t("partnerDashboard.monthlyRecurringRevenue")}</p>
              <p className="text-3xl font-bold mt-1">
                INR {summary.revenue_per_month_inr.toLocaleString("en-IN")}/month
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                {t("partnerDashboard.revenueBasis")}
              </p>
            </div>
            <div className="text-right">
              <Badge variant={totalClients > 0 ? "success" : "secondary"}>
                {totalClients > 0 ? t("partnerDashboard.active") : t("partnerDashboard.noCaData")}
              </Badge>
              <p className="text-xs text-muted-foreground mt-2">
                {t("partnerDashboard.revenueEmptyHint")}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Client Health Table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("partnerDashboard.clientHealthOverview")}</CardTitle>
        </CardHeader>
        <CardContent>
          {clients.length === 0 ? (
            <p className="text-muted-foreground text-sm py-4">{t("partnerDashboard.noClientHealthData")}</p>
          ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-2 px-3 font-medium text-muted-foreground">{t("partnerDashboard.companyName")}</th>
                  <th className="text-left py-2 px-3 font-medium text-muted-foreground">{t("partnerDashboard.healthScore")}</th>
                  <th className="text-left py-2 px-3 font-medium text-muted-foreground">{t("partnerDashboard.pendingFilings")}</th>
                  <th className="text-left py-2 px-3 font-medium text-muted-foreground">{t("partnerDashboard.overdueFilings")}</th>
                  <th className="text-left py-2 px-3 font-medium text-muted-foreground">{t("partnerDashboard.status")}</th>
                  <th className="text-left py-2 px-3 font-medium text-muted-foreground">{t("partnerDashboard.subscription")}</th>
                </tr>
              </thead>
              <tbody>
                {clients.map((client) => (
                  <tr key={client.id} className="border-b last:border-0 hover:bg-muted/50">
                    <td className="py-2 px-3 font-medium">
                      <Link to={`/dashboard/companies/${client.id}`} className="hover:underline text-primary">
                        {client.name}
                      </Link>
                    </td>
                    <td className="py-2 px-3">
                      <div className="flex items-center gap-2">
                        <div className="w-24 h-2 bg-muted rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full ${healthColor(client.health_score)}`}
                            style={{ width: `${client.health_score}%` }}
                          />
                        </div>
                        <span className={`text-xs font-semibold ${healthTextColor(client.health_score)}`}>
                          {client.health_score}%
                        </span>
                      </div>
                    </td>
                    <td className="py-2 px-3">
                      {client.pending_filings > 0 ? (
                        <Badge variant="warning">{client.pending_filings}</Badge>
                      ) : (
                        <Badge variant="success">0</Badge>
                      )}
                    </td>
                    <td className="py-2 px-3">
                      {client.overdue_filings > 0 ? (
                        <Badge variant="destructive">{client.overdue_filings}</Badge>
                      ) : (
                        <Badge variant="success">0</Badge>
                      )}
                    </td>
                    <td className="py-2 px-3">
                      <Badge variant={client.status === "active" ? "success" : "secondary"}>
                        {client.status === "active" ? t("partnerDashboard.active") : t("partnerDashboard.inactive")}
                      </Badge>
                    </td>
                    <td className="py-2 px-3">
                      <Badge variant={
                        client.subscription === "active" ? "success" :
                        client.subscription === "expired" ? "destructive" : "warning"
                      }>
                        {client.subscription}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          )}
        </CardContent>
      </Card>

      <div className="grid md:grid-cols-2 gap-6">
        {/* Upcoming Deadlines Panel */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("partnerDashboard.upcomingDeadlines")}</CardTitle>
          </CardHeader>
          <CardContent>
            {deadlines.length === 0 ? (
              <p className="text-muted-foreground text-sm">{t("common.noData")}</p>
            ) : (
            <div className="space-y-3">
              {deadlines.map((dl) => {
                const dueDate = new Date(dl.due_date);
                const today = new Date();
                const daysUntil = Math.ceil((dueDate.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
                const urgent = daysUntil <= 7;
                return (
                  <div key={dl.id} className="flex items-center justify-between border-b last:border-0 pb-2 last:pb-0">
                    <div>
                      <p className="text-sm font-medium">{dl.type} - {dl.period}</p>
                      <p className="text-xs text-muted-foreground">{dl.company}</p>
                    </div>
                    <div className="text-right">
                      <p className={`text-xs font-semibold ${urgent ? "text-red-600" : "text-muted-foreground"}`}>
                        {dueDate.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}
                      </p>
                      {urgent && (
                        <Badge variant="destructive" className="text-[10px] mt-0.5">
                          {daysUntil <= 0 ? t("partnerDashboard.overdue") : t("partnerDashboard.daysLeft", { count: daysUntil })}
                        </Badge>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
            )}
          </CardContent>
        </Card>

        {/* Compliance Score Overview */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("partnerDashboard.complianceWorkload")}</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {/* Filed */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-medium">{t("partnerDashboard.noPendingFilingClients")}</span>
                  <span className="text-sm text-emerald-600 font-semibold">{t("partnerDashboard.clientsCount", { count: filedCount })}</span>
                </div>
                <div className="w-full h-4 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-emerald-500 rounded-full"
                    style={{ width: `${filedWidth}%` }}
                  />
                </div>
              </div>

              {/* Pending */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-medium">{t("partnerDashboard.pendingFilings")}</span>
                  <span className="text-sm text-amber-600 font-semibold">{t("partnerDashboard.filingsCount", { count: totalPending })}</span>
                </div>
                <div className="w-full h-4 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-amber-500 rounded-full"
                    style={{ width: `${pendingWidth}%` }}
                  />
                </div>
              </div>

              {/* Overdue */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-medium">{t("partnerDashboard.overdueFilings")}</span>
                  <span className="text-sm text-red-600 font-semibold">{t("partnerDashboard.filingsCount", { count: overdueFilings })}</span>
                </div>
                <div className="w-full h-4 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-red-500 rounded-full"
                    style={{ width: `${overdueWidth}%` }}
                  />
                </div>
              </div>

              {/* Summary */}
              <div className="border-t pt-3 mt-3">
                <div className="flex items-center gap-4 text-xs">
                  <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-emerald-500" /> {t("partnerDashboard.noPendingFilingClients")}</span>
                  <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-amber-500" /> {t("partnerDashboard.pendingFilings")}</span>
                  <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-red-500" /> {t("partnerDashboard.overdueFilings")}</span>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
