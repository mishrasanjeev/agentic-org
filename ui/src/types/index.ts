export interface Agent {
  id: string; name: string; agent_type: string; domain: string; status: string;
  version: string; confidence_floor: number; shadow_sample_count: number;
  shadow_accuracy_current: number | null; created_at: string;
}
export interface Workflow { id: string; name: string; version: string; is_active: boolean; trigger_type: string | null; created_at: string; }
export interface WorkflowRun { id: string; workflow_def_id: string; status: string; steps_total: number; steps_completed: number; started_at: string; }
export interface HITLItem { id: string; title: string; trigger_type: string; priority: string; status: string; assignee_role: string; context: any; expires_at: string; }
export interface Connector { id: string; name: string; category: string; status: string; auth_type: string; rate_limit_rpm: number; }
export interface AuditEntry { id: string; event_type: string; actor_type: string; action: string; outcome: string; created_at: string; }
