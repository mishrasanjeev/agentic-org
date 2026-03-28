import { useState } from "react";
import { Button } from "./ui/button";
import { agentsApi } from "@/lib/api";

interface Props { agentId: string; agentName: string; onPaused?: () => void; }

export default function KillSwitch({ agentId, agentName, onPaused }: Props) {
  const [confirming, setConfirming] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handlePause = async () => {
    setPausing(true);
    setError(null);
    try {
      await agentsApi.pause(agentId);
      setConfirming(false);
      onPaused?.();
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to pause agent");
    } finally {
      setPausing(false);
    }
  };

  if (confirming) return (
    <div className="flex gap-2 items-center flex-wrap">
      <span className="text-sm text-destructive">Pause {agentName}?</span>
      <Button variant="destructive" size="sm" onClick={handlePause} disabled={pausing}>
        {pausing ? "Pausing..." : "Confirm"}
      </Button>
      <Button variant="outline" size="sm" onClick={() => { setConfirming(false); setError(null); }} disabled={pausing}>Cancel</Button>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  );
  return <Button variant="destructive" size="sm" onClick={() => setConfirming(true)}>Kill Switch</Button>;
}
