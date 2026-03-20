import { Card, CardHeader, CardTitle, CardContent } from "./ui/card";
import { Badge } from "./ui/badge";
import type { Connector } from "@/types";

export default function ConnectorCard({ connector }: { connector: Connector }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex justify-between"><CardTitle className="text-base">{connector.name}</CardTitle>
          <Badge variant={connector.status === "active" ? "success" : "destructive"}>{connector.status}</Badge></div>
      </CardHeader>
      <CardContent>
        <div className="text-sm">Category: {connector.category} | Rate: {connector.rate_limit_rpm}/min</div>
      </CardContent>
    </Card>
  );
}
