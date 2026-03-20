import { useState } from "react";
import { Button } from "./ui/button";
import { agentsApi } from "@/lib/api";

interface Props { agentId: string; agentName: string; onPaused?: () => void; }

export default function KillSwitch({ agentId, agentName, onPaused }: Props) {
  const [confirming, setConfirming] = useState(false);
  const handlePause = async () => {
    await agentsApi.pause(agentId);
    setConfirming(false);
    onPaused?.();
  };

  if (confirming) return (
    <div className="flex gap-2 items-center">
      <span className="text-sm text-destructive">Pause {agentName}?</span>
      <Button variant="destructive" size="sm" onClick={handlePause}>Confirm</Button>
      <Button variant="outline" size="sm" onClick={() => setConfirming(false)}>Cancel</Button>
    </div>
  );
  return <Button variant="destructive" size="sm" onClick={() => setConfirming(true)}>Kill Switch</Button>;
}
