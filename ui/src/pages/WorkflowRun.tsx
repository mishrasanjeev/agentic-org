import { useParams } from "react-router-dom";

export default function WorkflowRun() {
  const { id, runId } = useParams();
  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">Workflow Run</h2>
      <p className="text-muted-foreground">Workflow: {id} | Run: {runId}</p>
    </div>
  );
}
