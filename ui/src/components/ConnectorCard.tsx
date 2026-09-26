import { useNavigate } from "react-router";
import { Card, CardHeader, CardTitle, CardContent } from "./ui/card";
import { Badge } from "./ui/badge";
import type { Connector } from "@/types";

const readinessLabels: Record<string, string> = {
  disabled: "Disabled",
  needs_credentials: "Needs credentials",
  health_failed: "Health failed",
  stale_health: "Health check stale",
  recent_health: "Recently checked",
  needs_health_check: "Needs health check",
};

export default function ConnectorCard({ connector }: { connector: Connector }) {
  const navigate = useNavigate();
  const connectorId = connector.id;
  return (
    <Card className="cursor-pointer hover:border-primary/50 transition-colors" onClick={() => navigate(`/dashboard/connectors/${connectorId}`)}>
      <CardHeader>
        <div className="flex justify-between"><CardTitle className="text-base">{connector.name}</CardTitle>
          <div className="flex flex-wrap justify-end gap-1">
            <Badge variant="outline" data-testid="connector-visibility-badge">{connector.visibility === "personal" ? "Personal" : "Shared"}</Badge>
            <Badge
              variant={connector.readiness?.state === "recent_health" ? "success" : connector.readiness?.state === "health_failed" ? "destructive" : "outline"}
              data-testid="connector-readiness-badge"
              title="Health checks do not verify provider scopes, contracts, or error budgets"
            >
              {readinessLabels[connector.readiness?.state || ""] || "Not verified"}
            </Badge>
          </div></div>
      </CardHeader>
      <CardContent>
        <div className="text-sm">Category: {connector.category} | Auth: {connector.auth_type} | Rate: {connector.rate_limit_rpm}/min</div>
        {connector.readiness?.last_health_check && (
          <p className="mt-2 text-xs text-muted-foreground">Last health check: {new Date(connector.readiness.last_health_check).toLocaleString()}</p>
        )}
      </CardContent>
    </Card>
  );
}
