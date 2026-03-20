import { useParams } from "react-router-dom";
import KillSwitch from "@/components/KillSwitch";

export default function AgentDetail() {
  const { id } = useParams();
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Agent Detail</h2>
        <KillSwitch agentId={id || ""} agentName="Agent" />
      </div>
      <p className="text-muted-foreground">Agent ID: {id}</p>
    </div>
  );
}
