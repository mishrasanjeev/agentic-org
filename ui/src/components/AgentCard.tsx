import { Card, CardHeader, CardTitle, CardContent } from "./ui/card";
import { Badge } from "./ui/badge";
import type { Agent } from "@/types";

const DOMAIN_COLORS: Record<string, string> = {
  finance: "bg-blue-100 text-blue-800",
  hr: "bg-green-100 text-green-800",
  marketing: "bg-purple-100 text-purple-800",
  ops: "bg-orange-100 text-orange-800",
  backoffice: "bg-gray-100 text-gray-800",
  it: "bg-cyan-100 text-cyan-800",
};

interface Props { agent: Agent; onClick?: () => void; }

export default function AgentCard({ agent, onClick }: Props) {
  const statusColor = { active: "success", shadow: "warning", paused: "destructive" }[agent.status] || "default";
  const displayName = agent.employee_name || agent.name;
  const initial = displayName.charAt(0).toUpperCase();

  return (
    <Card className="cursor-pointer hover:shadow-md transition-shadow" onClick={onClick}>
      <CardHeader>
        <div className="flex justify-between items-center">
          <div className="flex items-center gap-3">
            {agent.avatar_url ? (
              <img src={agent.avatar_url} alt={displayName} className="w-8 h-8 rounded-full object-cover" />
            ) : (
              <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-sm font-bold text-primary">
                {initial}
              </div>
            )}
            <div>
              <CardTitle className="text-base">{displayName}</CardTitle>
              {agent.designation && <p className="text-xs text-muted-foreground">{agent.designation}</p>}
            </div>
          </div>
          <Badge variant={statusColor as any}>{agent.status}</Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div>Type: <span className="font-medium">{agent.agent_type.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}</span></div>
          <div>Domain: {agent.domain ? (
            <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${DOMAIN_COLORS[agent.domain.toLowerCase()] || "bg-gray-100 text-gray-800"}`}>
              {agent.domain}
            </span>
          ) : <span className="font-medium">—</span>}</div>
          <div>Confidence: <span className="font-medium">{agent.confidence_floor != null ? `${(agent.confidence_floor * 100).toFixed(0)}%` : "N/A"}</span></div>
          <div>Shadow: <span className="font-medium">{agent.shadow_sample_count ?? 0} samples</span></div>
          {agent.specialization && (
            <div className="col-span-2 text-xs text-muted-foreground mt-1 truncate">
              Specialization: {agent.specialization}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
