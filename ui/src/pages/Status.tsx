import { useEffect, useState } from "react";
import { Helmet } from "react-helmet-async";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface ServiceStatus {
  name: string;
  status: "operational" | "degraded" | "outage";
  message?: string;
}

interface Incident {
  id: string;
  title: string;
  severity: string;
  status: string;
  started_at: string;
  resolved_at?: string | null;
  updates: unknown[];
}

interface StatusPayload {
  overall: "operational" | "degraded" | "outage";
  services: ServiceStatus[];
  active_incidents: Incident[];
  recent_incidents: Incident[];
  uptime_30d_percent: number;
  last_updated: string;
}

const badgeColor: Record<string, string> = {
  operational: "bg-green-100 text-green-800 border-green-300",
  degraded: "bg-yellow-100 text-yellow-800 border-yellow-300",
  outage: "bg-red-100 text-red-800 border-red-300",
};

const headline: Record<StatusPayload["overall"], string> = {
  operational: "All systems operational",
  degraded: "Some systems degraded",
  outage: "Partial outage in progress",
};

export default function StatusPage() {
  const [data, setData] = useState<StatusPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const resp = await fetch("/api/v1/status");
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        setData(await resp.json());
        setError(null);
      } catch (e) {
        setError(String(e));
      }
    };
    load();
    const id = window.setInterval(load, 60_000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-4 md:p-8">
      <Helmet>
        <title>Status — AgenticOrg</title>
        <meta name="description" content="AgenticOrg platform status page" />
      </Helmet>

      <header>
        <h1 className="text-3xl font-bold">AgenticOrg Status</h1>
        {data && (
          <p className="mt-2 text-lg text-muted-foreground">
            {headline[data.overall]}
          </p>
        )}
        {error && (
          <p className="mt-2 text-sm text-destructive" role="alert">
            Failed to load status: {error}
          </p>
        )}
      </header>

      {data && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>30-day uptime</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-4xl font-bold text-primary">
                {data.uptime_30d_percent.toFixed(2)}%
              </div>
              <p className="text-sm text-muted-foreground">
                Target: 99.9% (Pro) / 99.95% (Enterprise) — see SLA
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Services</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-3" aria-label="Service statuses">
                {data.services.map((svc) => (
                  <li
                    key={svc.name}
                    className="flex items-start justify-between border-b pb-3 last:border-b-0"
                  >
                    <div>
                      <div className="font-medium">{svc.name}</div>
                      {svc.message && (
                        <div className="text-sm text-muted-foreground">
                          {svc.message}
                        </div>
                      )}
                    </div>
                    <span
                      className={`rounded-full border px-3 py-1 text-xs font-medium ${
                        badgeColor[svc.status] || ""
                      }`}
                    >
                      {svc.status}
                    </span>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>

          {data.active_incidents.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Active incidents</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-3">
                  {data.active_incidents.map((inc) => (
                    <li key={inc.id}>
                      <div className="flex items-center gap-2">
                        <Badge variant="destructive">{inc.severity}</Badge>
                        <span className="font-medium">{inc.title}</span>
                      </div>
                      <div className="text-sm text-muted-foreground">
                        {inc.status} — started {new Date(inc.started_at).toLocaleString()}
                      </div>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          {data.recent_incidents.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Recent incidents (last 7 days)</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-3">
                  {data.recent_incidents.map((inc) => (
                    <li key={inc.id}>
                      <div className="font-medium">{inc.title}</div>
                      <div className="text-sm text-muted-foreground">
                        Resolved{" "}
                        {inc.resolved_at
                          ? new Date(inc.resolved_at).toLocaleString()
                          : "—"}
                      </div>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          <footer className="text-xs text-muted-foreground">
            Last updated: {new Date(data.last_updated).toLocaleString()} — page
            auto-refreshes every 60 seconds.
          </footer>
        </>
      )}
    </div>
  );
}
