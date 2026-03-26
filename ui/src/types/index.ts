export interface Agent {
  id: string; name: string; agent_type: string; domain: string; status: string;
  version: string; confidence_floor: number; shadow_sample_count: number;
  shadow_accuracy_current: number | null; created_at: string;
  description?: string; hitl_condition?: string; authorized_tools?: string[];
  llm_model?: string; max_retries?: number; retry_backoff?: string;
  shadow_min_samples?: number; shadow_accuracy_floor?: number;
  cost_controls?: { monthly_cap_usd?: number; cost_current_usd?: number };
  // Virtual employee persona fields
  employee_name?: string;
  avatar_url?: string;
  designation?: string;
  specialization?: string;
  routing_filter?: Record<string, string>;
  is_builtin?: boolean;
  system_prompt_text?: string;
  parent_agent_id?: string | null;
  reporting_to?: string | null;
}
export interface PromptTemplate {
  id: string; name: string; agent_type: string; domain: string;
  template_text: string; variables: Array<{ name: string; description: string; default: string }>;
  is_builtin: boolean; is_active: boolean; created_at: string;
  description?: string; created_by?: string; updated_at?: string;
}
export interface PromptEditHistoryEntry {
  id: string; agent_id: string; edited_by: string | null;
  prompt_before: string | null; prompt_after: string;
  change_reason: string | null; created_at: string;
}
export interface Workflow { id: string; name: string; version: string; is_active: boolean; trigger_type: string | null; created_at: string; }
export interface WorkflowRun { id: string; workflow_def_id: string; status: string; steps_total: number; steps_completed: number; started_at: string; }
export interface HITLItem { id: string; title: string; trigger_type: string; priority: string; status: string; assignee_role: string; context: any; expires_at: string; }
export interface Connector { id: string; name: string; category: string; status: string; auth_type: string; rate_limit_rpm: number; }
export interface AuditEntry { id: string; event_type: string; actor_type: string; action: string; outcome: string; created_at: string; }
