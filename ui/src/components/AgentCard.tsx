import { Card, CardHeader, CardTitle, CardContent } from "./ui/card";
import { Badge } from "./ui/badge";
import type { Agent } from "@/types";

interface Props { agent: Agent; onClick?: () => void; }

export default function AgentCard({ agent, onClick }: Props) {
  const statusColor = { active: "success", shadow: "warning", paused: "destructive" }[agent.status] || "default";
  return (
    <Card className="cursor-pointer hover:shadow-md transition-shadow" onClick={onClick}>
      <CardHeader>
        <div className="flex justify-between items-center">
          <CardTitle className="text-base">{agent.name}</CardTitle>
          <Badge variant={statusColor as any}>{agent.status}</Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div>Domain: <span className="font-medium">{agent.domain}</span></div>
          <div>Version: <span className="font-medium">{agent.version}</span></div>
          <div>Confidence: <span className="font-medium">{(agent.confidence_floor * 100).toFixed(0)}%</span></div>
          <div>Shadow: <span className="font-medium">{agent.shadow_sample_count} samples</span></div>
        </div>
      </CardContent>
    </Card>
  );
}
