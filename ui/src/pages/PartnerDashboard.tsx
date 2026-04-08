import { useState } from "react";
import { Link } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface ClientHealth {
  id: string;
  name: string;
  health_score: number;
  pending_filings: number;
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

/* ------------------------------------------------------------------ */
/*  Mock data                                                          */
/* ------------------------------------------------------------------ */

const MOCK_CLIENTS: ClientHealth[] = [
  { id: "c1", name: "Sharma Manufacturing Pvt Ltd", health_score: 95, pending_filings: 0, status: "active", subscription: "active" },
  { id: "c2", name: "Gupta Traders & Sons", health_score: 88, pending_filings: 1, status: "active", subscription: "active" },
  { id: "c3", name: "Patel Pharma LLP", health_score: 72, pending_filings: 1, status: "active", subscription: "active" },
  { id: "c4", name: "Mehta Textiles", health_score: 45, pending_filings: 2, status: "inactive", subscription: "expired" },
  { id: "c5", name: "Singh Logistics Corp", health_score: 91, pending_filings: 0, status: "active", subscription: "active" },
  { id: "c6", name: "Joshi IT Solutions", health_score: 85, pending_filings: 1, status: "active", subscription: "active" },
  { id: "c7", name: "Reddy Agro Exports", health_score: 67, pending_filings: 0, status: "active", subscription: "trial" },
];

const MOCK_DEADLINES: Deadline[] = [
  { id: "d1", type: "GSTR-3B", period: "Apr 2026", company: "Sharma Manufacturing Pvt Ltd", due_date: "2026-04-20" },
  { id: "d2", type: "GSTR-1", period: "Apr 2026", company: "Gupta Traders & Sons", due_date: "2026-05-11" },
  { id: "d3", type: "TDS 26Q", period: "Q1 FY27", company: "Patel Pharma LLP", due_date: "2026-07-31" },
  { id: "d4", type: "PF ECR", period: "Apr 2026", company: "Singh Logistics Corp", due_date: "2026-05-15" },
  { id: "d5", type: "GSTR-3B", period: "Apr 2026", company: "Joshi IT Solutions", due_date: "2026-04-20" },
];

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
  const [clients] = useState<ClientHealth[]>(MOCK_CLIENTS);
  const [deadlines] = useState<Deadline[]>(MOCK_DEADLINES);

  const totalClients = clients.length;
  const activeClients = clients.filter((c) => c.status === "active").length;
  const avgHealth = Math.round(clients.reduce((sum, c) => sum + c.health_score, 0) / clients.length);
  const totalPending = clients.reduce((sum, c) => sum + c.pending_filings, 0);
  const overdueCount = clients.filter((c) => c.health_score < 50).length;

  const filedCount = clients.filter((c) => c.pending_filings === 0 && c.status === "active").length;
  const pendingCount = clients.filter((c) => c.pending_filings > 0).length;

  return (
    <div className="space-y-6">
      <Helmet>
        <title>Partner Dashboard | AgenticOrg</title>
      </Helmet>

      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-2xl font-bold">Partner Dashboard</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Kumar & Associates, Chartered Accountants
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link to="/dashboard/companies">
            <Button variant="outline">View All Clients</Button>
          </Link>
          <Link to="/dashboard/companies/new">
            <Button>Add Client</Button>
          </Link>
        </div>
      </div>

      {/* Summary KPI Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold">{totalClients}</p>
            <p className="text-xs text-muted-foreground">Total Clients</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold text-emerald-600">{activeClients}</p>
            <p className="text-xs text-muted-foreground">Active</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className={`text-2xl font-bold ${healthTextColor(avgHealth)}`}>{avgHealth}%</p>
            <p className="text-xs text-muted-foreground">Avg Health Score</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold text-amber-600">{totalPending}</p>
            <p className="text-xs text-muted-foreground">Pending Filings</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold text-red-600">{overdueCount}</p>
            <p className="text-xs text-muted-foreground">Overdue</p>
          </CardContent>
        </Card>
      </div>

      {/* Revenue Card */}
      <Card>
        <CardContent className="pt-4 pb-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">Monthly Recurring Revenue</p>
              <p className="text-3xl font-bold mt-1">INR 34,993/month</p>
              <p className="text-xs text-muted-foreground mt-1">{totalClients} clients x INR 4,999</p>
            </div>
            <div className="text-right">
              <Badge variant="success">Active</Badge>
              <p className="text-xs text-muted-foreground mt-2">Next billing: Apr 15, 2026</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Client Health Table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Client Health Overview</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-2 px-3 font-medium text-muted-foreground">Company Name</th>
                  <th className="text-left py-2 px-3 font-medium text-muted-foreground">Health Score</th>
                  <th className="text-left py-2 px-3 font-medium text-muted-foreground">Pending Filings</th>
                  <th className="text-left py-2 px-3 font-medium text-muted-foreground">Status</th>
                  <th className="text-left py-2 px-3 font-medium text-muted-foreground">Subscription</th>
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
                      <Badge variant={client.status === "active" ? "success" : "secondary"}>
                        {client.status}
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
        </CardContent>
      </Card>

      <div className="grid md:grid-cols-2 gap-6">
        {/* Upcoming Deadlines Panel */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Upcoming Deadlines</CardTitle>
          </CardHeader>
          <CardContent>
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
                          {daysUntil <= 0 ? "Overdue" : `${daysUntil}d left`}
                        </Badge>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>

        {/* Compliance Score Overview */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Compliance Score Across All Clients</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {/* Filed */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-medium">Filed</span>
                  <span className="text-sm text-emerald-600 font-semibold">{filedCount} clients</span>
                </div>
                <div className="w-full h-4 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-emerald-500 rounded-full"
                    style={{ width: `${(filedCount / totalClients) * 100}%` }}
                  />
                </div>
              </div>

              {/* Pending */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-medium">Pending</span>
                  <span className="text-sm text-amber-600 font-semibold">{pendingCount} clients</span>
                </div>
                <div className="w-full h-4 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-amber-500 rounded-full"
                    style={{ width: `${(pendingCount / totalClients) * 100}%` }}
                  />
                </div>
              </div>

              {/* Overdue */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-medium">Overdue</span>
                  <span className="text-sm text-red-600 font-semibold">{overdueCount} clients</span>
                </div>
                <div className="w-full h-4 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-red-500 rounded-full"
                    style={{ width: `${(overdueCount / totalClients) * 100}%` }}
                  />
                </div>
              </div>

              {/* Summary */}
              <div className="border-t pt-3 mt-3">
                <div className="flex items-center gap-4 text-xs">
                  <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-emerald-500" /> Filed (all clear)</span>
                  <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-amber-500" /> Pending filings</span>
                  <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-red-500" /> Overdue (score &lt; 50)</span>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
