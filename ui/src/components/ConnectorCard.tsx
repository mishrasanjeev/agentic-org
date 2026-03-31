import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "./ui/card";
import { Badge } from "./ui/badge";
import type { Connector } from "@/types";

export default function ConnectorCard({ connector }: { connector: Connector }) {
  const navigate = useNavigate();
  const connectorId = connector.id;
  return (
    <Card className="cursor-pointer hover:border-primary/50 transition-colors" onClick={() => navigate(`/dashboard/connectors/${connectorId}`)}>
      <CardHeader>
        <div className="flex justify-between"><CardTitle className="text-base">{connector.name}</CardTitle>
          <Badge variant={connector.status === "active" ? "success" : "destructive"}>{connector.status}</Badge></div>
      </CardHeader>
      <CardContent>
        <div className="text-sm">Category: {connector.category} | Auth: {connector.auth_type} | Rate: {connector.rate_limit_rpm}/min</div>
      </CardContent>
    </Card>
  );
}
