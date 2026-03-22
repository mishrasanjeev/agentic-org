import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import api from "@/lib/api";

export default function WorkflowDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [workflow, setWorkflow] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    api.get(`/workflows/${id}`)
      .then(({ data }) => setWorkflow(data))
      .catch(() => setError("Workflow not found"))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) return <p className="text-muted-foreground">Loading...</p>;
  if (error || !workflow) {
    return (
      <div className="space-y-4">
        <h2 className="text-2xl font-bold">Workflow Not Found</h2>
        <p className="text-muted-foreground">{error || "The requested workflow does not exist."}</p>
        <button onClick={() => navigate("/dashboard/workflows")} className="text-primary hover:underline text-sm">
          Back to Workflows
        </button>
      </div>
    );
  }

  const steps = workflow.definition?.steps || [];

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold">{workflow.name}</h2>
          <p className="text-muted-foreground text-sm mt-1">{workflow.description}</p>
        </div>
        <div className="flex gap-2">
          <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${workflow.is_active ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-800"}`}>
            {workflow.is_active ? "Active" : "Inactive"}
          </span>
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
            v{workflow.version}
          </span>
        </div>
      </div>

      {/* Info Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="border rounded-lg p-4">
          <p className="text-xs text-muted-foreground">Domain</p>
          <p className="text-sm font-medium mt-1 capitalize">{workflow.domain || "—"}</p>
        </div>
        <div className="border rounded-lg p-4">
          <p className="text-xs text-muted-foreground">Trigger</p>
          <p className="text-sm font-medium mt-1">{workflow.trigger_type || "manual"}</p>
        </div>
        <div className="border rounded-lg p-4">
          <p className="text-xs text-muted-foreground">Steps</p>
          <p className="text-sm font-medium mt-1">{steps.length}</p>
        </div>
        <div className="border rounded-lg p-4">
          <p className="text-xs text-muted-foreground">Created</p>
          <p className="text-sm font-medium mt-1">{new Date(workflow.created_at).toLocaleDateString()}</p>
        </div>
      </div>

      {/* Steps */}
      {steps.length > 0 && (
        <div className="border rounded-lg p-6">
          <h3 className="text-lg font-semibold mb-4">Workflow Steps</h3>
          <div className="space-y-3">
            {steps.map((step: any, i: number) => (
              <div key={step.id || i} className="flex items-center gap-4">
                <div className="flex-shrink-0 w-8 h-8 rounded-full bg-primary/10 text-primary flex items-center justify-center text-sm font-bold">
                  {i + 1}
                </div>
                <div className="flex-1 border rounded-lg p-3">
                  <p className="font-medium text-sm">{step.id}</p>
                  <p className="text-xs text-muted-foreground">
                    {step.type === "hitl" ? "Human-in-the-Loop approval" : `Agent: ${step.agent}`}
                  </p>
                </div>
                {i < steps.length - 1 && (
                  <div className="text-muted-foreground text-lg">→</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <button onClick={() => navigate("/dashboard/workflows")} className="text-sm text-muted-foreground hover:text-foreground">
        ← Back to Workflows
      </button>
    </div>
  );
}
