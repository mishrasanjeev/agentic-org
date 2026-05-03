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
  org_level?: number;
  connector_ids?: string[];
  config?: {
    tool_connectors?: Record<string, string>;
    required_connector_ids?: string[];
    enforcement_log?: any[];
    grantex?: {
      grant_token?: string;
      grantex_agent_id?: string;
      grantex_did?: string;
      enforcement_log?: any[];
      grantex_scopes?: string[];
    };
    [key: string]: any;
  };
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
export interface HITLItem { id: string; title: string; trigger_type: string; priority: string; status: string; assignee_role: string; context: any; expires_at: string; decision?: string; decision_at?: string; decision_notes?: string; }
export interface Connector { id: string; name: string; category: string; status: string; auth_type: string; rate_limit_rpm: number; base_url?: string; description?: string; secret_ref?: string; tool_functions?: any[]; timeout_ms?: number; created_at?: string; }
export interface AuditEntry { id: string; event_type: string; actor_type: string; action: string; outcome: string; created_at: string; }

// CFO Dashboard KPIs
export interface CFOKPIs {
  cash_runway_months: number;
  burn_rate: number;
  dso_days: number;
  dpo_days: number;
  ar_aging: { bucket: string; amount: number }[];
  ap_aging: { bucket: string; amount: number }[];
  monthly_pl: { label: string; amount: number; change_pct: number }[];
  bank_balances: { account: string; balance: number; currency: string }[];
  pending_approvals: number;
  tax_calendar: { filing: string; due_date: string; status: string }[];
}

// CMO Dashboard KPIs
export interface CMOKPIs {
  cac: number;
  mqls: number;
  sqls: number;
  pipeline_value: number;
  roas_by_channel: { channel: string; roas: number; spend: number }[];
  email_performance: { metric: string; value: number }[];
  social_engagement: { platform: string; engagement: number; followers: number }[];
  website_traffic: { date: string; sessions: number; users: number }[];
  content_top_pages: { path: string; title: string; views: number }[];
  brand_sentiment: number;
  pending_content_approvals: number;
}

// Company (multi-tenant)
export interface Company {
  id: string;
  name: string;
  gstin?: string;
  industry?: string;
  is_active: boolean;
  created_at: string;
}

// Chat message
export interface ChatMessage {
  id: string;
  role: "user" | "agent";
  content: string;
  agent?: string;
  domain?: string;
  confidence?: number;
  timestamp: string;
}
