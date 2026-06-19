import { useState, useEffect } from "react";
import { Helmet } from "react-helmet-async";
import { useTranslation } from "react-i18next";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import api from "@/lib/api";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";

type BadgeVariant = "default" | "success" | "warning" | "destructive" | "secondary" | "outline";
type CapabilityStatus = "production" | "beta" | "stub" | "unavailable" | "demo";
type ConnectorHealthStatus = "healthy" | "missing" | "expired_auth" | "insufficient_scope" | "stale" | "degraded" | "unknown";
type ConnectorCTAState = "none" | "setup" | "reconnect" | "add_scope" | "refresh" | "review";
type ConnectorContractState =
  | "healthy"
  | "missing_scope"
  | "auth_expired"
  | "rate_limited"
  | "timeout"
  | "vendor_5xx"
  | "partial_data"
  | "stale_data"
  | "degraded"
  | "write_unconfirmed"
  | "write_confirmed"
  | "unknown";
type FieldMappingStatus = "unmapped" | "partially_mapped" | "valid" | "invalid" | "stale" | "blocked" | "unknown";
type BackfillStatus = "not_started" | "queued" | "running" | "completed" | "partial" | "failed" | "blocked" | "unknown";
type WorkflowState = "unavailable" | "shadow" | "promotion_blocked" | "promotion_ready" | "active" | "degraded" | "paused" | "unknown";
type WorkQueueSeverity = "critical" | "high" | "medium" | "low" | "info";
type WorkQueueStatus = "open" | "blocked" | "waiting" | "resolved" | "dismissed";
type KpiDrilldownStatus = "ready" | "degraded" | "blocked" | "unavailable";
type ApprovalReviewStatus = "pending" | "approved" | "rejected" | "overridden" | "timed_out" | "escalated" | "blocked";

const STATUS_BADGE_VARIANT: Record<CapabilityStatus, BadgeVariant> = {
  production: "success",
  beta: "warning",
  stub: "secondary",
  unavailable: "destructive",
  demo: "outline",
};

const CONNECTOR_BADGE_VARIANT: Record<ConnectorHealthStatus, BadgeVariant> = {
  healthy: "success",
  missing: "secondary",
  expired_auth: "destructive",
  insufficient_scope: "warning",
  stale: "warning",
  degraded: "destructive",
  unknown: "outline",
};

const CONNECTOR_CONTRACT_BADGE_VARIANT: Record<ConnectorContractState, BadgeVariant> = {
  healthy: "success",
  missing_scope: "warning",
  auth_expired: "destructive",
  rate_limited: "warning",
  timeout: "warning",
  vendor_5xx: "warning",
  partial_data: "warning",
  stale_data: "warning",
  degraded: "warning",
  write_unconfirmed: "destructive",
  write_confirmed: "success",
  unknown: "outline",
};

const FIELD_MAPPING_BADGE_VARIANT: Record<FieldMappingStatus, BadgeVariant> = {
  valid: "success",
  unmapped: "secondary",
  partially_mapped: "warning",
  invalid: "destructive",
  stale: "warning",
  blocked: "destructive",
  unknown: "outline",
};

const BACKFILL_BADGE_VARIANT: Record<BackfillStatus, BadgeVariant> = {
  completed: "success",
  not_started: "secondary",
  queued: "warning",
  running: "warning",
  partial: "warning",
  failed: "destructive",
  blocked: "destructive",
  unknown: "outline",
};

const WORKFLOW_BADGE_VARIANT: Record<WorkflowState, BadgeVariant> = {
  active: "success",
  promotion_ready: "warning",
  shadow: "secondary",
  promotion_blocked: "destructive",
  degraded: "warning",
  paused: "secondary",
  unavailable: "destructive",
  unknown: "outline",
};

const WORK_QUEUE_SEVERITY_BADGE_VARIANT: Record<WorkQueueSeverity, BadgeVariant> = {
  critical: "destructive",
  high: "destructive",
  medium: "warning",
  low: "secondary",
  info: "outline",
};

const WORK_QUEUE_STATUS_BADGE_VARIANT: Record<WorkQueueStatus, BadgeVariant> = {
  open: "warning",
  blocked: "destructive",
  waiting: "secondary",
  resolved: "success",
  dismissed: "outline",
};

const KPI_DRILLDOWN_BADGE_VARIANT: Record<KpiDrilldownStatus, BadgeVariant> = {
  ready: "success",
  degraded: "warning",
  blocked: "destructive",
  unavailable: "secondary",
};

const APPROVAL_REVIEW_BADGE_VARIANT: Record<ApprovalReviewStatus, BadgeVariant> = {
  pending: "warning",
  approved: "success",
  rejected: "destructive",
  overridden: "warning",
  timed_out: "destructive",
  escalated: "warning",
  blocked: "destructive",
};

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DomainBreakdown {
  domain: string;
  total: number;
  completed: number;
  failed: number;
  avg_confidence: number;
}

interface MarketingConnectorSetupItem {
  key: string;
  name: string;
  category: string;
  required_scopes: string[];
  required_credentials: string[];
  configured_status: "configured" | "unconfigured" | string;
  health_status: ConnectorHealthStatus | string;
  last_sync_at: string | null;
  owner: string;
  account_id: string | null;
  data_coverage_status: string;
  cta_state: ConnectorCTAState | string;
  missing_scopes?: string[];
  detail?: string;
}

interface MarketingConnectorSetupSummary {
  total: number;
  healthy: number;
  missing: number;
  stale: number;
  degraded: number;
  auth_actions: number;
  needs_action: number;
  readiness: string;
}

interface MarketingConnectorRetryBudget {
  max_attempts: number;
  attempts_used: number;
  remaining_attempts: number;
  reset_at: string | null;
  next_retry_at: string | null;
  idempotency_key: string | null;
  idempotency_supported: boolean;
}

interface MarketingConnectorDataFreshness {
  status: string;
  ttl_seconds: number | null;
  last_sync_at: string | null;
}

interface MarketingConnectorContractItem {
  connector_key: string;
  name: string;
  category: string;
  configured_status: string;
  vendor_id: string | null;
  account_id: string | null;
  workspace_id: string | null;
  read_capabilities: string[];
  write_capabilities: string[];
  required_read_scopes: string[];
  required_write_scopes: string[];
  granted_scopes: string[];
  missing_read_scopes: string[];
  missing_write_scopes: string[];
  read_scope_evidence?: string[];
  auth_status: string;
  health_status: string;
  contract_state: ConnectorContractState | string;
  read_status: string;
  write_status: string;
  read_ready: boolean;
  write_ready: boolean;
  production_ready: boolean;
  mock_or_test_double: boolean;
  last_sync_at: string | null;
  source_objects: Array<{ id?: string; url?: string; type?: string; [key: string]: unknown }>;
  data_freshness: MarketingConnectorDataFreshness;
  retry_budget: MarketingConnectorRetryBudget;
  degraded_mode_reason: string | null;
  idempotency_key_supported: boolean;
  external_write_confirmation_status: string;
  external_write_confirmations: Array<Record<string, unknown>>;
  next_action_cta: string;
}

interface MarketingConnectorContractSummary {
  total: number;
  configured: number;
  read_ready: number;
  write_ready: number;
  blocked: number;
  degraded: number;
  missing_write_scope: number;
  write_unconfirmed: number;
  write_confirmed: number;
  mock_or_test_double: number;
  readiness: string;
}

interface MarketingFieldMappingItem {
  key: string;
  name: string;
  status: FieldMappingStatus | string;
  source_categories: string[];
  sources: string[];
  required_fields: string[];
  missing_fields: string[];
  affected_kpis: string[];
  missing_blocks: boolean;
  last_updated_at: string | null;
  blocking_reason: string | null;
  next_action_cta: string;
}

interface MarketingFieldMappingSummary {
  total: number;
  unmapped: number;
  partially_mapped: number;
  valid: number;
  invalid: number;
  stale: number;
  blocked: number;
  needs_action: number;
  readiness: string;
  blocking: number;
  degraded: number;
}

interface MarketingBackfillItem {
  source_connector_key: string;
  source_name: string;
  category: string;
  status: BackfillStatus | string;
  requested_start: string | null;
  requested_end: string | null;
  records_discovered: number | null;
  records_imported: number | null;
  records_skipped: number | null;
  records_failed: number | null;
  last_run_at: string | null;
  blocking_reason: string | null;
  next_action_cta: string;
}

interface MarketingBackfillSummary {
  total: number;
  not_started: number;
  queued: number;
  running: number;
  completed: number;
  partial: number;
  failed: number;
  blocked: number;
  needs_action: number;
  readiness: string;
}

interface MarketingKPIReadiness {
  status: "ready" | "degraded" | "blocked" | string;
  historical_status: "ready" | "blocked" | string;
  field_mapping_readiness: string;
  backfill_readiness: string;
  blocked_reasons: string[];
  degraded_reasons: string[];
  affected_kpis: string[];
  next_action_cta: string;
}

interface MarketingWorkflowShadowQuality {
  status: string;
  sample_count: number;
  success_rate: number;
  required_sample_count: number;
  required_success_rate: number;
  last_run_at: string | null;
  blocking_reason: string | null;
  next_action_cta: string;
}

interface MarketingWorkflowActivationItem {
  workflow_key: string;
  name: string;
  state: WorkflowState | string;
  configured_mode: string;
  required_connectors: string[];
  optional_connectors: string[];
  required_mappings: string[];
  optional_mappings: string[];
  required_backfill_categories: string[];
  optional_backfill_categories: string[];
  approval_owner: string | null;
  policy_owner: string | null;
  shadow_quality: MarketingWorkflowShadowQuality;
  blocked_reasons: string[];
  degraded_reasons: string[];
  next_action_cta: string;
  external_writes_allowed: boolean;
}

interface MarketingWorkflowActivationSummary {
  total: number;
  unavailable: number;
  shadow: number;
  promotion_blocked: number;
  promotion_ready: number;
  active: number;
  degraded: number;
  paused: number;
  external_writes_allowed: number;
  needs_action: number;
  readiness: string;
}

interface CMOWorkQueueCTA {
  action_key: string;
  label: string;
  path: string;
}

interface CMOWorkQueueItem {
  item_id: string;
  type: string;
  category: string;
  severity: WorkQueueSeverity | string;
  priority_score: number;
  title: string;
  message: string;
  affected_workflow?: string | null;
  affected_capability?: string | null;
  affected_kpi?: string | null;
  affected_report?: string | null;
  affected_connector?: string | null;
  owner_role?: string | null;
  due_at?: string | null;
  source_refs?: Array<Record<string, unknown>>;
  audit_refs?: string[];
  next_action_cta?: CMOWorkQueueCTA | string;
  next_action_label?: string;
  next_action_path?: string;
  next_action_key?: string;
  status: WorkQueueStatus | string;
  created_at?: string;
  updated_at?: string;
}

interface CMOWorkQueueSummary {
  total: number;
  readiness: string;
  critical_or_high: number;
  needs_action: number;
  top_priority_score: number;
  first_item_id: string | null;
  next_action_cta?: CMOWorkQueueCTA | null;
  by_severity?: Record<string, number>;
  by_status?: Record<string, number>;
  by_category?: Record<string, number>;
  empty_state?: string | null;
}

interface CMOKpiFormulaInput {
  name: string;
  source_key?: string;
  value: unknown;
  resolved: boolean;
}

interface CMOKpiDrilldown {
  drilldown_id: string;
  kpi_key: string;
  display_name: string;
  description: string;
  status: KpiDrilldownStatus | string;
  value: unknown;
  unit: string | null;
  confidence: number;
  formula: string | null;
  formula_inputs: CMOKpiFormulaInput[];
  source_refs?: Array<Record<string, unknown>>;
  connector_refs?: Array<Record<string, unknown>>;
  field_mappings_used?: Array<Record<string, unknown>>;
  backfill_state?: Array<Record<string, unknown>>;
  reconciliation_checks?: Array<Record<string, unknown>>;
  freshness_status?: string;
  freshness?: Record<string, unknown>;
  confidence_impact_reasons?: string[];
  missing_requirements?: Record<string, string[]>;
  blocked_reasons?: string[];
  degraded_reasons?: string[];
  related_work_queue_item_ids?: string[];
  related_report_gate_ids?: string[];
  policy_refs?: string[];
  audit_refs?: string[];
  owner_role?: string;
  next_action_cta?: CMOWorkQueueCTA | string;
  production_lineage_ready?: boolean;
  production_lineage_status?: string;
  last_computed_at?: string;
}

interface CMOKpiDrilldownSummary {
  total: number;
  ready: number;
  degraded: number;
  blocked: number;
  unavailable: number;
  lineage_blocked: number;
  readiness: string;
  needs_action: number;
  next_action_cta?: CMOWorkQueueCTA;
}

interface CMOApprovalReview {
  approval_review_id: string;
  approval_id: string;
  workflow_id?: string | null;
  workflow_run_id?: string | null;
  run_id?: string | null;
  step_id?: string | null;
  action: string;
  action_type: string;
  status: ApprovalReviewStatus | string;
  requester?: string | null;
  agent_ref?: string | null;
  actor_refs?: Array<Record<string, unknown>>;
  assigned_approver?: string | null;
  assigned_approver_role?: string | null;
  created_at?: string | null;
  due_at?: string | null;
  timeout_state?: string | null;
  preview_payload?: Record<string, unknown>;
  before_after_diff?: Record<string, unknown> | string | null;
  budget_impact?: Record<string, unknown> | string | null;
  audience_impact?: Record<string, unknown> | string | null;
  risk_flags?: string[];
  source_refs?: Array<Record<string, unknown>>;
  connector_refs?: Array<Record<string, unknown>>;
  agent_rationale?: string | null;
  policy_result?: Record<string, unknown>;
  policy_result_ref?: string | null;
  escalation_result?: Record<string, unknown>;
  escalation_result_ref?: string | null;
  timeout_result?: Record<string, unknown>;
  timeout_result_ref?: string | null;
  external_write_readiness?: Record<string, unknown>;
  external_write_result_ref?: string | null;
  audit_evidence?: Record<string, unknown>;
  audit_refs?: string[];
  rollback_stop_plan?: Record<string, unknown> | string | null;
  allowed_reviewer_actions?: string[];
  blocked_reasons?: string[];
  related_work_queue_item_ids?: string[];
  next_action_cta?: CMOWorkQueueCTA | string;
  evaluated_at?: string | null;
}

interface CMOApprovalReviewSummary {
  total: number;
  readiness: string;
  approval_ready: number;
  blocked: number;
  timed_out: number;
  unsafe_write: number;
  missing_audit: number;
  needs_action: number;
  next_action_cta?: CMOWorkQueueCTA | null;
}

interface CMOKPIData {
  demo: boolean;
  demo_suppressed?: boolean;
  production_data_blocked?: boolean;
  kpi_confidence_status?: string;
  data_coverage_status?: string;
  message?: string;
  company_id: string;
  agent_count: number;
  total_tasks_30d: number;
  success_rate: number;
  hitl_interventions: number;
  total_cost_usd: number;
  domain_breakdown: DomainBreakdown[];
  connector_setup?: MarketingConnectorSetupItem[];
  connector_setup_summary?: MarketingConnectorSetupSummary;
  connector_contracts?: MarketingConnectorContractItem[];
  connector_contract_summary?: MarketingConnectorContractSummary;
  field_mapping_status?: MarketingFieldMappingItem[];
  field_mapping_summary?: MarketingFieldMappingSummary;
  backfill_status?: MarketingBackfillItem[];
  backfill_summary?: MarketingBackfillSummary;
  kpi_readiness?: MarketingKPIReadiness;
  workflow_activation_status?: MarketingWorkflowActivationItem[];
  workflow_activation_summary?: MarketingWorkflowActivationSummary;
  cmo_work_queue?: CMOWorkQueueItem[];
  cmo_work_queue_summary?: CMOWorkQueueSummary;
  cmo_approval_reviews?: CMOApprovalReview[];
  cmo_approval_review_summary?: CMOApprovalReviewSummary;
  cmo_kpi_drilldowns?: CMOKpiDrilldown[];
  cmo_kpi_drilldown_summary?: CMOKpiDrilldownSummary;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

function formatNumber(n: number): string {
  return new Intl.NumberFormat("en-IN").format(n);
}

function formatDateTime(value: string | null): string {
  if (!value) return "Not synced";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Not synced";
  return parsed.toLocaleString();
}

function connectorActionTarget(row: MarketingConnectorSetupItem): string {
  if (row.cta_state === "setup") {
    return `/dashboard/connectors/cmo-vendor-sandbox?category=${encodeURIComponent(row.category)}`;
  }
  return "/dashboard/connectors";
}

function dataReadinessActionTarget(): string {
  return "/dashboard/connectors";
}

function workflowActionTarget(): string {
  return "/dashboard/workflows";
}

function workQueueActionTarget(row: CMOWorkQueueItem): string {
  if (typeof row.next_action_cta === "object" && row.next_action_cta?.path) {
    return row.next_action_cta.path;
  }
  return row.next_action_path || "/dashboard/cmo";
}

function workQueueCtaLabel(row: CMOWorkQueueItem): string {
  if (typeof row.next_action_cta === "object" && row.next_action_cta?.label) {
    return row.next_action_cta.label;
  }
  if (typeof row.next_action_cta === "string" && row.next_action_cta) {
    return row.next_action_cta.replace(/_/g, " ");
  }
  return row.next_action_label || "Review";
}

function formatWorkQueueAffected(row: CMOWorkQueueItem): string {
  const affected = [
    row.affected_workflow,
    row.affected_capability,
    row.affected_kpi,
    row.affected_report,
    row.affected_connector,
  ].filter(Boolean);
  return affected.length > 0 ? affected.join(" / ") : "CMO readiness";
}

function kpiDrilldownActionTarget(row: CMOKpiDrilldown): string {
  if (typeof row.next_action_cta === "object" && row.next_action_cta?.path) {
    return row.next_action_cta.path;
  }
  return "/dashboard/cmo";
}

function kpiDrilldownCtaLabel(row: CMOKpiDrilldown): string {
  if (typeof row.next_action_cta === "object" && row.next_action_cta?.label) {
    return row.next_action_cta.label;
  }
  if (typeof row.next_action_cta === "string" && row.next_action_cta) {
    return row.next_action_cta.replace(/_/g, " ");
  }
  return "Review";
}

function formatDrilldownValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "N/A";
  if (typeof value === "number") return new Intl.NumberFormat("en-IN").format(value);
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function formatFormulaInputs(row: CMOKpiDrilldown): string {
  const inputs = row.formula_inputs ?? [];
  if (inputs.length === 0) return "No resolved inputs";
  return inputs
    .slice(0, 4)
    .map((item) => `${item.name}: ${item.resolved ? formatDrilldownValue(item.value) : "missing"}`)
    .join(", ");
}

function formatConnectorKeys(row: CMOKpiDrilldown): string {
  const keys = (row.connector_refs ?? [])
    .map((ref) => ref.connector_key || ref.key || ref.category)
    .filter(Boolean)
    .map(String);
  const unique = Array.from(new Set(keys));
  return unique.length > 0 ? unique.slice(0, 4).join(", ") : "No connector refs";
}

function firstLineageReason(row: CMOKpiDrilldown): string {
  return (
    row.blocked_reasons?.[0] ||
    row.degraded_reasons?.[0] ||
    row.confidence_impact_reasons?.[0] ||
    "No blockers"
  );
}

function humanizeKey(value: string): string {
  return value
    .replace(/[_-]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function safeText(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return null;
}

function recordSummary(value: Record<string, unknown> | string | null | undefined, fallback = "Not provided"): string {
  if (typeof value === "string" && value) return value;
  if (!value || typeof value !== "object") return fallback;
  const directSummary =
    safeText(value.summary) ||
    safeText(value.description) ||
    safeText(value.reason) ||
    safeText(value.status) ||
    safeText(value.decision);
  if (directSummary) return directSummary;
  try {
    return JSON.stringify(value);
  } catch {
    return fallback;
  }
}

function previewTitle(row: CMOApprovalReview): string {
  const payload = row.preview_payload ?? {};
  return (
    safeText(payload.campaign_name) ||
    safeText(payload.title) ||
    safeText(payload.subject) ||
    safeText(payload.headline) ||
    humanizeKey(row.action)
  );
}

function formatApprovalImpact(value: Record<string, unknown> | string | null | undefined, fallback: string): string {
  if (typeof value === "string" && value) return value;
  if (!value || typeof value !== "object") return fallback;
  const summary = safeText(value.summary);
  if (summary) return summary;
  const amount = typeof value.amount === "number" ? value.amount : null;
  const currency = safeText(value.currency) || "USD";
  if (amount !== null) {
    const formatted = new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(amount);
    return `${formatted} ${safeText(value.direction) || "impact"}`;
  }
  const recipients = typeof value.estimated_recipients === "number" ? value.estimated_recipients : null;
  if (recipients !== null) {
    return `${formatNumber(recipients)} estimated recipients`;
  }
  return recordSummary(value, fallback);
}

function approvalReviewActionTarget(row: CMOApprovalReview): string {
  if (typeof row.next_action_cta === "object" && row.next_action_cta?.path) {
    return row.next_action_cta.path;
  }
  return "/dashboard/approvals";
}

function approvalReviewCtaLabel(row: CMOApprovalReview): string {
  if (typeof row.next_action_cta === "object" && row.next_action_cta?.label) {
    return row.next_action_cta.label;
  }
  if (typeof row.next_action_cta === "string" && row.next_action_cta) {
    return humanizeKey(row.next_action_cta);
  }
  return "Review Approval";
}

function approvalSafeguardSummary(row: CMOApprovalReview): string {
  const policy = recordSummary(row.policy_result, "policy missing");
  const timeout = recordSummary(row.timeout_result, row.timeout_state || "timeout unknown");
  const write = recordSummary(row.external_write_readiness, "write readiness unknown");
  const auditReady = row.audit_evidence?.ready === true || (row.audit_refs ?? []).length > 0;
  return `Policy: ${policy}; Timeout: ${timeout}; Write: ${write}; Audit: ${auditReady ? "ready" : "missing"}`;
}

function formatDateRange(start: string | null, end: string | null): string {
  if (!start && !end) return "Not requested";
  return `${start || "?"} to ${end || "?"}`;
}

function formatBackfillRecords(row: MarketingBackfillItem): string {
  const imported = row.records_imported ?? 0;
  const failed = row.records_failed ?? 0;
  const discovered = row.records_discovered ?? 0;
  return `${imported}/${discovered} imported, ${failed} failed`;
}

function formatContractRetryBudget(row: MarketingConnectorContractItem): string {
  const retry = row.retry_budget || {};
  const maxAttempts = retry.max_attempts ?? 0;
  const remaining = retry.remaining_attempts ?? 0;
  const used = retry.attempts_used ?? 0;
  if (maxAttempts <= 0) {
    return row.idempotency_key_supported ? "Idempotency only" : "No retry budget";
  }
  return `${remaining}/${maxAttempts} left, ${used} used`;
}

function contractStatusVariant(value?: string): BadgeVariant {
  if (value === "ready" || value === "healthy" || value === "write_confirmed") {
    return "success";
  }
  if (
    value === "blocked" ||
    value === "auth_expired" ||
    value === "write_unconfirmed" ||
    value === "idempotency_missing"
  ) {
    return "destructive";
  }
  if (
    value === "degraded" ||
    value === "missing_scope" ||
    value === "rate_limited" ||
    value === "timeout" ||
    value === "vendor_5xx" ||
    value === "partial_data" ||
    value === "stale_data"
  ) {
    return "warning";
  }
  return "secondary";
}

const MARKETING_DOMAINS = new Set([
  "marketing",
  "content",
  "social",
  "email",
  "seo",
  "ads",
  "brand",
  "campaign",
]);

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CMODashboard() {
  const { t } = useTranslation();
  const [data, setData] = useState<CMOKPIData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchData();
  }, []);

  async function fetchData() {
    setLoading(true);
    setError(null);
    try {
      // Codex 2026-04-22 multi-company isolation fix — same reason as
      // the CFO dashboard: the marketing board ignored the company
      // switcher because it didn't thread company_id through.
      const companyId = localStorage.getItem("company_id") || "";
      const params = companyId ? { company_id: companyId } : {};
      const resp = await api.get("/kpis/cmo", { params });
      setData(resp.data);
    } catch {
      setError(t("errors.failedToLoadKpis", "Failed to load CMO KPIs"));
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">{t("kpi.cmoDashboard", "CMO Dashboard")}</h2>
        <p className="text-muted-foreground">{t("kpi.loading", "Loading...")}</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-bold">{t("kpi.cmoDashboard", "CMO Dashboard")}</h2>
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-800">
          {error || "No data available"}
        </div>
      </div>
    );
  }

  const domains = data.domain_breakdown ?? [];
  const marketingDomains = domains.filter((d) =>
    MARKETING_DOMAINS.has(d.domain.toLowerCase())
  );
  const displayDomains = marketingDomains.length > 0 ? marketingDomains : domains;

  const kpiCards = [
    { label: t("kpi.agents", "Agents"), value: formatNumber(data.agent_count ?? 0), color: "text-blue-600" },
    { label: t("kpi.totalTasks", "Total Tasks (30d)"), value: formatNumber(data.total_tasks_30d ?? 0), color: "text-emerald-600" },
    { label: t("kpi.successRate", "Success Rate"), value: `${(data.success_rate ?? 0).toFixed(1)}%`, color: "text-purple-600" },
    { label: t("kpi.hitlInterventions", "HITL Interventions"), value: formatNumber(data.hitl_interventions ?? 0), color: "text-orange-600" },
    { label: t("kpi.totalCost", "Total Cost (USD)"), value: USD.format(data.total_cost_usd ?? 0), color: "text-rose-600" },
  ];
  const statusLabels: Record<CapabilityStatus, string> = {
    production: t("cmoCapability.states.production", "Production"),
    beta: t("cmoCapability.states.beta", "Beta"),
    stub: t("cmoCapability.states.stub", "Stub"),
    unavailable: t("cmoCapability.states.unavailable", "Unavailable"),
    demo: t("cmoCapability.states.demo", "Demo"),
  };
  const capabilityRows: Array<{ name: string; status: CapabilityStatus; detail: string }> = [
    {
      name: t("cmoCapability.campaignPilot.name", "Campaign Pilot"),
      status: "production",
      detail: t(
        "cmoCapability.campaignPilot.detail",
        "Strongest current CMO agent: domain-specific campaign execution, budget checks, performance polling, and HITL gates.",
      ),
    },
    {
      name: t("cmoCapability.contentFactory.name", "Content Factory"),
      status: "beta",
      detail: t(
        "cmoCapability.contentFactory.detail",
        "Substantial content workflow exists, including draft generation and brand checks; publish loops and QA policy are still maturing.",
      ),
    },
    {
      name: t("cmoCapability.emailMarketing.name", "Email Marketing"),
      status: "beta",
      detail: t(
        "cmoCapability.emailMarketing.detail",
        "Available through a separate LangGraph path and approval-gated tooling, not a complete autonomous CMO pillar yet.",
      ),
    },
    {
      name: t("cmoCapability.brandMonitor.name", "Brand Monitor"),
      status: "stub",
      detail: t(
        "cmoCapability.brandMonitor.detail",
        "Registered, but the core marketing implementation still wraps shared/base behavior; not production-grade brand monitoring.",
      ),
    },
    {
      name: t("cmoCapability.seoStrategist.name", "SEO Strategist"),
      status: "stub",
      detail: t(
        "cmoCapability.seoStrategist.detail",
        "Registered, but the core marketing implementation still wraps shared/base behavior; not production-grade SEO execution.",
      ),
    },
    {
      name: t("cmoCapability.crmIntelligence.name", "CRM Intelligence"),
      status: "beta",
      detail: t(
        "cmoCapability.crmIntelligence.detail",
        "First-class deterministic CRM/pipeline intelligence for velocity, funnel conversion, lead scoring refresh, churn signals, segments, SQL promotion, and account health; policy/approval/audit/write-confirmation gates for CRM writes. Not production-grade without real HubSpot/Salesforce/intent connectors and pilot proof.",
      ),
    },
    {
      name: t("cmoCapability.socialMedia.name", "Social Media"),
      status: "unavailable",
      detail: t(
        "cmoCapability.socialMedia.detail",
        "Not a first-class core marketing agent yet; any graph-level surface should be treated as experimental/demo only.",
      ),
    },
    {
      name: t("cmoCapability.abm.name", "ABM"),
      status: "unavailable",
      detail: t(
        "cmoCapability.abm.detail",
        "ABM dashboard/API surfaces exist, but a first-class core marketing ABM agent is not implemented yet.",
      ),
    },
    {
      name: t("cmoCapability.competitiveIntel.name", "Competitive Intel"),
      status: "unavailable",
      detail: t(
        "cmoCapability.competitiveIntel.detail",
        "Not a first-class core marketing agent yet; any graph-level surface should be treated as experimental/demo only.",
      ),
    },
    {
      name: t("cmoCapability.kpiFeed.name", "CMO KPI feed"),
      status: data.demo ? "demo" : "beta",
      detail: data.demo
        ? t(
            "cmoCapability.kpiFeed.demoDetail",
            "This dashboard is currently showing demo KPI data, not proof of end-to-end autonomous CMO operation.",
          )
        : t(
            "cmoCapability.kpiFeed.liveDetail",
            "KPI data is not flagged as demo, but capability maturity still follows the agent states listed above.",
          ),
    },
  ];
  const connectorRows = data.connector_setup ?? [];
  const connectorSummary = data.connector_setup_summary;
  const connectorStatusLabels: Record<ConnectorHealthStatus, string> = {
    healthy: t("cmoConnectors.health.healthy", "Healthy"),
    missing: t("cmoConnectors.health.missing", "Missing"),
    expired_auth: t("cmoConnectors.health.expiredAuth", "Expired Auth"),
    insufficient_scope: t("cmoConnectors.health.insufficientScope", "Insufficient Scope"),
    stale: t("cmoConnectors.health.stale", "Stale Sync"),
    degraded: t("cmoConnectors.health.degraded", "Degraded"),
    unknown: t("cmoConnectors.health.unknown", "Unknown"),
  };
  const coverageLabels: Record<string, string> = {
    ready: t("cmoConnectors.coverage.ready", "Ready"),
    missing: t("cmoConnectors.coverage.missing", "Missing"),
    blocked: t("cmoConnectors.coverage.blocked", "Blocked"),
    stale: t("cmoConnectors.coverage.stale", "Stale"),
    partial: t("cmoConnectors.coverage.partial", "Partial"),
    unavailable: t("cmoConnectors.coverage.unavailable", "Unavailable"),
  };
  const ctaLabels: Record<ConnectorCTAState, string> = {
    none: t("cmoConnectors.cta.none", "No Action"),
    setup: t("cmoConnectors.cta.setup", "Set Up"),
    reconnect: t("cmoConnectors.cta.reconnect", "Reconnect"),
    add_scope: t("cmoConnectors.cta.addScope", "Add Scope"),
    refresh: t("cmoConnectors.cta.refresh", "Refresh Sync"),
    review: t("cmoConnectors.cta.review", "Review"),
  };
  const contractRows = data.connector_contracts ?? [];
  const contractSummary = data.connector_contract_summary;
  const contractStateLabels: Record<ConnectorContractState, string> = {
    healthy: t("cmoConnectorContracts.states.healthy", "Healthy"),
    missing_scope: t("cmoConnectorContracts.states.missingScope", "Missing Scope"),
    auth_expired: t("cmoConnectorContracts.states.authExpired", "Auth Expired"),
    rate_limited: t("cmoConnectorContracts.states.rateLimited", "Rate Limited"),
    timeout: t("cmoConnectorContracts.states.timeout", "Timeout"),
    vendor_5xx: t("cmoConnectorContracts.states.vendor5xx", "Vendor 5xx"),
    partial_data: t("cmoConnectorContracts.states.partialData", "Partial Data"),
    stale_data: t("cmoConnectorContracts.states.staleData", "Stale Data"),
    degraded: t("cmoConnectorContracts.states.degraded", "Degraded"),
    write_unconfirmed: t("cmoConnectorContracts.states.writeUnconfirmed", "Write Unconfirmed"),
    write_confirmed: t("cmoConnectorContracts.states.writeConfirmed", "Write Confirmed"),
    unknown: t("cmoConnectorContracts.states.unknown", "Unknown"),
  };
  const contractReadinessLabels: Record<string, string> = {
    ready: t("cmoConnectorContracts.readiness.ready", "Ready"),
    blocked: t("cmoConnectorContracts.readiness.blocked", "Blocked"),
    degraded: t("cmoConnectorContracts.readiness.degraded", "Degraded"),
    read_only: t("cmoConnectorContracts.readiness.readOnly", "Read-only"),
    missing_scope: t("cmoConnectorContracts.readiness.missingScope", "Missing Scope"),
    idempotency_missing: t("cmoConnectorContracts.readiness.idempotencyMissing", "Idempotency Missing"),
    unknown: t("cmoConnectorContracts.readiness.unknown", "Unknown"),
  };
  const contractCtaLabels: Record<string, string> = {
    none: t("cmoConnectorContracts.cta.none", "No Action"),
    setup: t("cmoConnectorContracts.cta.setup", "Set Up"),
    reconnect: t("cmoConnectorContracts.cta.reconnect", "Reconnect"),
    add_scope: t("cmoConnectorContracts.cta.addScope", "Add Scope"),
    review_retry_budget: t("cmoConnectorContracts.cta.reviewRetryBudget", "Review Retry Budget"),
    review_degraded: t("cmoConnectorContracts.cta.reviewDegraded", "Review Degraded"),
    configure_idempotency: t("cmoConnectorContracts.cta.configureIdempotency", "Configure Idempotency"),
  };
  const connectorStatusFor = (value: string): ConnectorHealthStatus =>
    value in CONNECTOR_BADGE_VARIANT ? (value as ConnectorHealthStatus) : "unknown";
  const connectorCtaFor = (value: string): ConnectorCTAState =>
    value in ctaLabels ? (value as ConnectorCTAState) : "review";
  const connectorContractStateFor = (value: string): ConnectorContractState =>
    value in CONNECTOR_CONTRACT_BADGE_VARIANT ? (value as ConnectorContractState) : "unknown";
  const fieldMappingRows = data.field_mapping_status ?? [];
  const fieldMappingSummary = data.field_mapping_summary;
  const backfillRows = data.backfill_status ?? [];
  const backfillSummary = data.backfill_summary;
  const kpiReadiness = data.kpi_readiness;
  const workflowRows = data.workflow_activation_status ?? [];
  const workflowSummary = data.workflow_activation_summary;
  const workQueueRows = data.cmo_work_queue ?? [];
  const workQueueSummary = data.cmo_work_queue_summary;
  const visibleWorkQueueRows = workQueueRows.slice(0, 8);
  const approvalReviewRows = data.cmo_approval_reviews ?? [];
  const approvalReviewSummary = data.cmo_approval_review_summary;
  const visibleApprovalReviews = approvalReviewRows.slice(0, 6);
  const kpiDrilldownRows = data.cmo_kpi_drilldowns ?? [];
  const kpiDrilldownSummary = data.cmo_kpi_drilldown_summary;
  const visibleKpiDrilldowns = kpiDrilldownRows.slice(0, 8);
  const fieldStatusLabels: Record<FieldMappingStatus, string> = {
    valid: t("cmoDataReadiness.fieldStatus.valid", "Valid"),
    unmapped: t("cmoDataReadiness.fieldStatus.unmapped", "Unmapped"),
    partially_mapped: t("cmoDataReadiness.fieldStatus.partiallyMapped", "Partially Mapped"),
    invalid: t("cmoDataReadiness.fieldStatus.invalid", "Invalid"),
    stale: t("cmoDataReadiness.fieldStatus.stale", "Stale"),
    blocked: t("cmoDataReadiness.fieldStatus.blocked", "Blocked"),
    unknown: t("cmoDataReadiness.fieldStatus.unknown", "Unknown"),
  };
  const backfillStatusLabels: Record<BackfillStatus, string> = {
    completed: t("cmoDataReadiness.backfillStatus.completed", "Completed"),
    not_started: t("cmoDataReadiness.backfillStatus.notStarted", "Not Started"),
    queued: t("cmoDataReadiness.backfillStatus.queued", "Queued"),
    running: t("cmoDataReadiness.backfillStatus.running", "Running"),
    partial: t("cmoDataReadiness.backfillStatus.partial", "Partial"),
    failed: t("cmoDataReadiness.backfillStatus.failed", "Failed"),
    blocked: t("cmoDataReadiness.backfillStatus.blocked", "Blocked"),
    unknown: t("cmoDataReadiness.backfillStatus.unknown", "Unknown"),
  };
  const workflowStateLabels: Record<WorkflowState, string> = {
    active: t("cmoWorkflowGates.states.active", "Active"),
    promotion_ready: t("cmoWorkflowGates.states.promotionReady", "Ready to Promote"),
    shadow: t("cmoWorkflowGates.states.shadow", "Shadow"),
    promotion_blocked: t("cmoWorkflowGates.states.promotionBlocked", "Blocked"),
    degraded: t("cmoWorkflowGates.states.degraded", "Degraded"),
    paused: t("cmoWorkflowGates.states.paused", "Paused"),
    unavailable: t("cmoWorkflowGates.states.unavailable", "Unavailable"),
    unknown: t("cmoWorkflowGates.states.unknown", "Unknown"),
  };
  const readinessLabels: Record<string, string> = {
    ready: t("cmoDataReadiness.readiness.ready", "Ready"),
    degraded: t("cmoDataReadiness.readiness.degraded", "Degraded"),
    blocked: t("cmoDataReadiness.readiness.blocked", "Blocked"),
  };
  const mappingCtaLabels: Record<string, string> = {
    none: t("cmoDataReadiness.cta.none", "No Action"),
    connect_source: t("cmoDataReadiness.cta.connectSource", "Connect Source"),
    map_fields: t("cmoDataReadiness.cta.mapFields", "Map Fields"),
    complete_mapping: t("cmoDataReadiness.cta.completeMapping", "Complete Mapping"),
    review_mapping: t("cmoDataReadiness.cta.reviewMapping", "Review Mapping"),
    fix_mapping: t("cmoDataReadiness.cta.fixMapping", "Fix Mapping"),
    resolve_mapping_blocker: t("cmoDataReadiness.cta.resolveMappingBlocker", "Resolve Blocker"),
  };
  const backfillCtaLabels: Record<string, string> = {
    none: t("cmoDataReadiness.cta.none", "No Action"),
    start_backfill: t("cmoDataReadiness.cta.startBackfill", "Start Backfill"),
    monitor_backfill: t("cmoDataReadiness.cta.monitorBackfill", "Monitor Backfill"),
    review_failed_records: t("cmoDataReadiness.cta.reviewFailedRecords", "Review Failed Records"),
    retry_backfill: t("cmoDataReadiness.cta.retryBackfill", "Retry Backfill"),
    resolve_blocker: t("cmoDataReadiness.cta.resolveBlocker", "Resolve Blocker"),
  };
  const workflowCtaLabels: Record<string, string> = {
    none: t("cmoWorkflowGates.cta.none", "No Action"),
    promote_workflow: t("cmoWorkflowGates.cta.promoteWorkflow", "Promote"),
    run_shadow_quality: t("cmoWorkflowGates.cta.runShadowQuality", "Run Shadow QA"),
    fix_required_connector: t("cmoWorkflowGates.cta.fixRequiredConnector", "Fix Connector"),
    fix_required_mapping: t("cmoWorkflowGates.cta.fixRequiredMapping", "Fix Mapping"),
    complete_backfill: t("cmoWorkflowGates.cta.completeBackfill", "Complete Backfill"),
    configure_policy_owner: t("cmoWorkflowGates.cta.configurePolicyOwner", "Configure Policy"),
    review_degraded_dependency: t("cmoWorkflowGates.cta.reviewDegradedDependency", "Review Degraded"),
    resume_workflow: t("cmoWorkflowGates.cta.resumeWorkflow", "Resume"),
    implement_first_class_agent: t("cmoWorkflowGates.cta.implementFirstClassAgent", "Implement Agent"),
    resolve_promotion_blocker: t("cmoWorkflowGates.cta.resolvePromotionBlocker", "Resolve Blocker"),
  };
  const workQueueSeverityLabels: Record<WorkQueueSeverity, string> = {
    critical: t("cmoWorkQueue.severity.critical", "Critical"),
    high: t("cmoWorkQueue.severity.high", "High"),
    medium: t("cmoWorkQueue.severity.medium", "Medium"),
    low: t("cmoWorkQueue.severity.low", "Low"),
    info: t("cmoWorkQueue.severity.info", "Info"),
  };
  const workQueueStatusLabels: Record<WorkQueueStatus, string> = {
    open: t("cmoWorkQueue.status.open", "Open"),
    blocked: t("cmoWorkQueue.status.blocked", "Blocked"),
    waiting: t("cmoWorkQueue.status.waiting", "Waiting"),
    resolved: t("cmoWorkQueue.status.resolved", "Resolved"),
    dismissed: t("cmoWorkQueue.status.dismissed", "Dismissed"),
  };
  const kpiDrilldownStatusLabels: Record<KpiDrilldownStatus, string> = {
    ready: t("cmoKpiLineage.status.ready", "Ready"),
    degraded: t("cmoKpiLineage.status.degraded", "Degraded"),
    blocked: t("cmoKpiLineage.status.blocked", "Blocked"),
    unavailable: t("cmoKpiLineage.status.unavailable", "Unavailable"),
  };
  const approvalReviewStatusLabels: Record<ApprovalReviewStatus, string> = {
    pending: t("cmoApprovalReviews.status.pending", "Pending"),
    approved: t("cmoApprovalReviews.status.approved", "Approved"),
    rejected: t("cmoApprovalReviews.status.rejected", "Rejected"),
    overridden: t("cmoApprovalReviews.status.overridden", "Overridden"),
    timed_out: t("cmoApprovalReviews.status.timedOut", "Timed Out"),
    escalated: t("cmoApprovalReviews.status.escalated", "Escalated"),
    blocked: t("cmoApprovalReviews.status.blocked", "Blocked"),
  };
  const fieldStatusFor = (value: string): FieldMappingStatus =>
    value in FIELD_MAPPING_BADGE_VARIANT ? (value as FieldMappingStatus) : "unknown";
  const backfillStatusFor = (value: string): BackfillStatus =>
    value in BACKFILL_BADGE_VARIANT ? (value as BackfillStatus) : "unknown";
  const workflowStateFor = (value: string): WorkflowState =>
    value in WORKFLOW_BADGE_VARIANT ? (value as WorkflowState) : "unknown";
  const workQueueSeverityFor = (value: string): WorkQueueSeverity =>
    value in WORK_QUEUE_SEVERITY_BADGE_VARIANT ? (value as WorkQueueSeverity) : "medium";
  const workQueueStatusFor = (value: string): WorkQueueStatus =>
    value in WORK_QUEUE_STATUS_BADGE_VARIANT ? (value as WorkQueueStatus) : "open";
  const kpiDrilldownStatusFor = (value: string): KpiDrilldownStatus =>
    value in KPI_DRILLDOWN_BADGE_VARIANT ? (value as KpiDrilldownStatus) : "unavailable";
  const approvalReviewStatusFor = (value: string): ApprovalReviewStatus =>
    value in APPROVAL_REVIEW_BADGE_VARIANT ? (value as ApprovalReviewStatus) : "pending";
  const readinessVariant = (value?: string): BadgeVariant =>
    value === "ready" ? "success" : value === "blocked" ? "destructive" : "warning";

  return (
    <div className="space-y-4 p-3 md:space-y-6 md:p-6" role="main" aria-label={t("kpi.cmoDashboard", "CMO Dashboard")}>
      <Helmet>
        <title>CMO Dashboard — AgenticOrg</title>
      </Helmet>
      <div className="flex flex-col items-start justify-between gap-2 md:flex-row md:items-center">
        <h1 className="text-xl font-bold md:text-2xl">{t("kpi.cmoDashboard", "CMO Dashboard")}</h1>
        {data.demo && <Badge variant="secondary">{t("kpi.demoData", "Demo Data")}</Badge>}
      </div>

      {data.production_data_blocked && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {data.message ||
            t(
              "cmoConnectors.productionBlocked",
              "Production CMO data is blocked until real marketing connectors are configured and synced.",
            )}
        </div>
      )}

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
            <div>
              <CardTitle className="text-sm font-semibold">
                {t("cmoWorkQueue.title", "CMO Work Queue")}
              </CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                {t(
                  "cmoWorkQueue.subtitle",
                  "Operator-visible CMO work from approvals, escalations, connectors, data readiness, writes, policy, audit, KPIs, reconciliation, and report gates.",
                )}
              </p>
            </div>
            {workQueueSummary && (
              <div className="flex flex-wrap gap-2">
                <Badge variant={workQueueSummary.total > 0 ? "warning" : "outline"}>
                  {workQueueSummary.total} {t("cmoWorkQueue.summaryTotalLabel", "open items")}
                </Badge>
                <Badge variant={workQueueSummary.critical_or_high > 0 ? "destructive" : "outline"}>
                  {workQueueSummary.critical_or_high} {t("cmoWorkQueue.summaryCriticalHighLabel", "critical/high")}
                </Badge>
                <Badge variant={readinessVariant(workQueueSummary.readiness)}>
                  {workQueueSummary.readiness}
                </Badge>
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {visibleWorkQueueRows.length === 0 ? (
            <div className="rounded-md border bg-muted/40 px-3 py-3 text-sm text-muted-foreground">
              {workQueueSummary?.empty_state ||
                t(
                  "cmoWorkQueue.empty",
                  "No CMO work queue items are open. This does not make stub, unavailable, or demo capabilities production-ready.",
                )}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[980px] text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-2 pr-4">{t("cmoWorkQueue.priority", "Priority")}</th>
                    <th className="pb-2 pr-4">{t("cmoWorkQueue.item", "Item")}</th>
                    <th className="pb-2 pr-4">{t("cmoWorkQueue.severityLabel", "Severity")}</th>
                    <th className="pb-2 pr-4">{t("cmoWorkQueue.owner", "Owner")}</th>
                    <th className="pb-2 pr-4">{t("cmoWorkQueue.affected", "Affected")}</th>
                    <th className="pb-2 pr-4">{t("cmoWorkQueue.due", "Due")}</th>
                    <th className="pb-2">{t("cmoWorkQueue.action", "Action")}</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleWorkQueueRows.map((row) => {
                    const severity = workQueueSeverityFor(row.severity);
                    const status = workQueueStatusFor(row.status);
                    return (
                      <tr key={row.item_id} className="border-b align-top last:border-0">
                        <td className="py-2 pr-4 font-medium">{row.priority_score}</td>
                        <td className="max-w-[360px] py-2 pr-4">
                          <div className="font-medium">{row.title}</div>
                          <div className="mt-1 text-xs text-muted-foreground">{row.message}</div>
                          <div className="mt-1 flex flex-wrap gap-1">
                            <Badge variant={WORK_QUEUE_STATUS_BADGE_VARIANT[status]}>
                              {workQueueStatusLabels[status]}
                            </Badge>
                            <span className="text-xs text-muted-foreground">{row.category}</span>
                          </div>
                        </td>
                        <td className="py-2 pr-4">
                          <Badge variant={WORK_QUEUE_SEVERITY_BADGE_VARIANT[severity]}>
                            {workQueueSeverityLabels[severity]}
                          </Badge>
                        </td>
                        <td className="py-2 pr-4">
                          {row.owner_role || t("cmoConnectors.unassigned", "Unassigned")}
                        </td>
                        <td className="max-w-[260px] py-2 pr-4 text-xs text-muted-foreground">
                          {formatWorkQueueAffected(row)}
                        </td>
                        <td className="py-2 pr-4">{formatDateTime(row.due_at || null)}</td>
                        <td className="py-2">
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              window.location.href = workQueueActionTarget(row);
                            }}
                          >
                            {workQueueCtaLabel(row)}
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
            <div>
              <CardTitle className="text-sm font-semibold">
                {t("cmoApprovalReviews.title", "CMO Approval Reviews")}
              </CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                {t(
                  "cmoApprovalReviews.subtitle",
                  "Pending and blocked marketing approvals with preview, impact, policy, timeout, write, audit, and rollback context.",
                )}
              </p>
            </div>
            {approvalReviewSummary && (
              <div className="flex flex-wrap gap-2">
                <Badge variant={approvalReviewSummary.needs_action > 0 ? "warning" : "outline"}>
                  {approvalReviewSummary.needs_action} {t("cmoApprovalReviews.summaryNeedsActionLabel", "need review")}
                </Badge>
                <Badge variant={approvalReviewSummary.blocked > 0 ? "destructive" : "outline"}>
                  {approvalReviewSummary.blocked} {t("cmoApprovalReviews.summaryBlockedLabel", "blocked")}
                </Badge>
                <Badge variant={readinessVariant(approvalReviewSummary.readiness)}>
                  {approvalReviewSummary.readiness}
                </Badge>
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {visibleApprovalReviews.length === 0 ? (
            <div className="rounded-md border bg-muted/40 px-3 py-3 text-sm text-muted-foreground">
              {t(
                "cmoApprovalReviews.empty",
                "No CMO approval reviews are projected. External or customer-facing marketing actions still require policy, write-readiness, timeout, and audit evidence before approval.",
              )}
            </div>
          ) : (
            <div className="grid gap-3 xl:grid-cols-2">
              {visibleApprovalReviews.map((row) => {
                const status = approvalReviewStatusFor(row.status);
                const safeToApprove = (row.allowed_reviewer_actions ?? []).includes("approve");
                return (
                  <div key={row.approval_review_id} className="rounded-md border p-3 text-sm">
                    <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                      <div>
                        <div className="font-medium">{previewTitle(row)}</div>
                        <div className="text-xs text-muted-foreground">
                          {humanizeKey(row.action_type)} / {row.action}
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-1">
                        <Badge variant={APPROVAL_REVIEW_BADGE_VARIANT[status]}>
                          {approvalReviewStatusLabels[status]}
                        </Badge>
                        <Badge variant={safeToApprove ? "success" : "destructive"}>
                          {safeToApprove
                            ? t("cmoApprovalReviews.safeToApprove", "Approve enabled")
                            : t("cmoApprovalReviews.approvalBlocked", "Approve blocked")}
                        </Badge>
                      </div>
                    </div>
                    <div className="mt-3 grid gap-2 md:grid-cols-2">
                      <div>
                        <div className="text-xs font-medium text-muted-foreground">
                          {t("cmoApprovalReviews.ownerDue", "Owner / Due")}
                        </div>
                        <div>
                          {row.assigned_approver_role || row.assigned_approver || t("cmoConnectors.unassigned", "Unassigned")}
                        </div>
                        <div className="text-xs text-muted-foreground">{formatDateTime(row.due_at || null)}</div>
                      </div>
                      <div>
                        <div className="text-xs font-medium text-muted-foreground">
                          {t("cmoApprovalReviews.risks", "Risks")}
                        </div>
                        <div>{(row.risk_flags ?? []).slice(0, 3).map(humanizeKey).join(", ") || "None listed"}</div>
                      </div>
                      <div>
                        <div className="text-xs font-medium text-muted-foreground">
                          {t("cmoApprovalReviews.budget", "Budget")}
                        </div>
                        <div>{formatApprovalImpact(row.budget_impact, "No budget impact provided")}</div>
                      </div>
                      <div>
                        <div className="text-xs font-medium text-muted-foreground">
                          {t("cmoApprovalReviews.audience", "Audience")}
                        </div>
                        <div>{formatApprovalImpact(row.audience_impact, "No audience impact provided")}</div>
                      </div>
                    </div>
                    <div className="mt-3 space-y-1 text-xs text-muted-foreground">
                      <div>
                        <span className="font-medium text-foreground">{t("cmoApprovalReviews.diff", "Diff")}: </span>
                        {recordSummary(row.before_after_diff, "No before/after diff provided")}
                      </div>
                      <div>
                        <span className="font-medium text-foreground">{t("cmoApprovalReviews.rationale", "Rationale")}: </span>
                        {row.agent_rationale || t("cmoApprovalReviews.noRationale", "No agent rationale provided")}
                      </div>
                      <div>
                        <span className="font-medium text-foreground">
                          {t("cmoApprovalReviews.safeguards", "Safeguards")}:{" "}
                        </span>
                        {approvalSafeguardSummary(row)}
                      </div>
                      <div>
                        <span className="font-medium text-foreground">
                          {t("cmoApprovalReviews.rollback", "Rollback / Stop")}:{" "}
                        </span>
                        {recordSummary(row.rollback_stop_plan, "No rollback or stop plan provided")}
                      </div>
                      {row.blocked_reasons && row.blocked_reasons.length > 0 && (
                        <div className="text-red-700">
                          {t("cmoApprovalReviews.blockedReasons", "Blocked")}: {row.blocked_reasons.slice(0, 2).join("; ")}
                        </div>
                      )}
                      {row.related_work_queue_item_ids && row.related_work_queue_item_ids.length > 0 && (
                        <div>
                          {t("cmoApprovalReviews.workQueueRefs", "Work queue")}:{" "}
                          {row.related_work_queue_item_ids.slice(0, 2).join(", ")}
                        </div>
                      )}
                    </div>
                    <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                      <div className="text-xs text-muted-foreground">
                        {t("cmoApprovalReviews.allowedActions", "Allowed actions")}:{" "}
                        {(row.allowed_reviewer_actions ?? []).map(humanizeKey).join(", ") ||
                          t("cmoApprovalReviews.noneAllowed", "None")}
                      </div>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          window.location.href = approvalReviewActionTarget(row);
                        }}
                      >
                        {approvalReviewCtaLabel(row)}
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
            <div>
              <CardTitle className="text-sm font-semibold">
                {t("cmoKpiLineage.title", "CMO KPI Drill-Down")}
              </CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                {t(
                  "cmoKpiLineage.subtitle",
                  "Formula, source, mapping, reconciliation, freshness, confidence, work queue, and report-gate lineage for canonical CMO KPIs.",
                )}
              </p>
            </div>
            {kpiDrilldownSummary && (
              <div className="flex flex-wrap gap-2">
                <Badge variant={readinessVariant(kpiDrilldownSummary.readiness)}>
                  {kpiDrilldownSummary.ready}/{kpiDrilldownSummary.total} {t("cmoKpiLineage.summaryReadyLabel", "ready")}
                </Badge>
                <Badge variant={kpiDrilldownSummary.blocked > 0 ? "destructive" : "outline"}>
                  {kpiDrilldownSummary.blocked} {t("cmoKpiLineage.summaryBlockedLabel", "blocked")}
                </Badge>
                <Badge variant={kpiDrilldownSummary.lineage_blocked > 0 ? "destructive" : "outline"}>
                  {kpiDrilldownSummary.lineage_blocked} {t("cmoKpiLineage.summaryLineageBlockedLabel", "lineage blocked")}
                </Badge>
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {visibleKpiDrilldowns.length === 0 ? (
            <div className="rounded-md border bg-muted/40 px-3 py-3 text-sm text-muted-foreground">
              {t(
                "cmoKpiLineage.empty",
                "KPI drill-down lineage is unavailable. Production CMO KPIs cannot be trusted without formula, source, freshness, and confidence evidence.",
              )}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1120px] text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-2 pr-4">{t("cmoKpiLineage.kpi", "KPI")}</th>
                    <th className="pb-2 pr-4">{t("cmoKpiLineage.statusLabel", "Status")}</th>
                    <th className="pb-2 pr-4">{t("cmoKpiLineage.formula", "Formula")}</th>
                    <th className="pb-2 pr-4">{t("cmoKpiLineage.inputs", "Inputs")}</th>
                    <th className="pb-2 pr-4">{t("cmoKpiLineage.sources", "Sources")}</th>
                    <th className="pb-2 pr-4">{t("cmoKpiLineage.confidence", "Confidence")}</th>
                    <th className="pb-2 pr-4">{t("cmoKpiLineage.issue", "Issue")}</th>
                    <th className="pb-2">{t("cmoKpiLineage.action", "Action")}</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleKpiDrilldowns.map((row) => {
                    const status = kpiDrilldownStatusFor(row.status);
                    return (
                      <tr key={row.drilldown_id} className="border-b align-top last:border-0">
                        <td className="py-2 pr-4">
                          <div className="font-medium">{row.display_name}</div>
                          <div className="text-xs text-muted-foreground">{row.kpi_key}</div>
                        </td>
                        <td className="py-2 pr-4">
                          <Badge variant={KPI_DRILLDOWN_BADGE_VARIANT[status]}>
                            {kpiDrilldownStatusLabels[status]}
                          </Badge>
                          {!row.production_lineage_ready && (
                            <div className="mt-1 text-xs text-red-700">
                              {t("cmoKpiLineage.notProductionProof", "Not production lineage proof")}
                            </div>
                          )}
                        </td>
                        <td className="max-w-[260px] py-2 pr-4 text-xs text-muted-foreground">
                          {row.formula || t("cmoKpiLineage.notAvailable", "N/A")}
                        </td>
                        <td className="max-w-[300px] py-2 pr-4 text-xs text-muted-foreground">
                          {formatFormulaInputs(row)}
                        </td>
                        <td className="max-w-[220px] py-2 pr-4 text-xs text-muted-foreground">
                          <div>{formatConnectorKeys(row)}</div>
                          <div>
                            {t("cmoKpiLineage.freshness", "Freshness")}: {row.freshness_status || "unknown"}
                          </div>
                        </td>
                        <td className="py-2 pr-4">
                          {((row.confidence || 0) * 100).toFixed(0)}%
                        </td>
                        <td className="max-w-[300px] py-2 pr-4 text-xs text-muted-foreground">
                          {firstLineageReason(row)}
                          {row.related_work_queue_item_ids && row.related_work_queue_item_ids.length > 0 && (
                            <div className="mt-1">
                              {t("cmoKpiLineage.workQueueRefs", "Work queue")}:{" "}
                              {row.related_work_queue_item_ids.slice(0, 2).join(", ")}
                            </div>
                          )}
                          {row.related_report_gate_ids && row.related_report_gate_ids.length > 0 && (
                            <div className="mt-1">
                              {t("cmoKpiLineage.reportRefs", "Reports")}:{" "}
                              {row.related_report_gate_ids.slice(0, 2).join(", ")}
                            </div>
                          )}
                        </td>
                        <td className="py-2">
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              window.location.href = kpiDrilldownActionTarget(row);
                            }}
                          >
                            {kpiDrilldownCtaLabel(row)}
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
            <div>
              <CardTitle className="text-sm font-semibold">
                {t("cmoConnectors.title", "Marketing Connector Setup")}
              </CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                {t(
                  "cmoConnectors.subtitle",
                  "Real-company CMO readiness depends on connected CRM, ads, analytics, CMS, email, social, SEO, brand, ABM, and finance systems.",
                )}
              </p>
            </div>
            {connectorSummary && (
              <div className="flex flex-wrap gap-2">
                <Badge variant="success">
                  {connectorSummary.healthy} {t("cmoConnectors.summaryHealthyLabel", "healthy")}
                </Badge>
                <Badge variant={connectorSummary.needs_action > 0 ? "warning" : "outline"}>
                  {connectorSummary.needs_action} {t("cmoConnectors.summaryNeedsActionLabel", "need action")}
                </Badge>
                <Badge variant={connectorSummary.missing > 0 ? "secondary" : "outline"}>
                  {connectorSummary.missing} {t("cmoConnectors.summaryMissingLabel", "missing")}
                </Badge>
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {connectorRows.length === 0 ? (
            <div className="rounded-md border bg-muted/40 px-3 py-3 text-sm text-muted-foreground">
              {t(
                "cmoConnectors.empty",
                "Connector setup status is unavailable. The dashboard cannot claim production CMO readiness without it.",
              )}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[980px] text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-2 pr-4">{t("cmoConnectors.system", "System")}</th>
                    <th className="pb-2 pr-4">{t("cmoConnectors.status", "Status")}</th>
                    <th className="pb-2 pr-4">{t("cmoConnectors.coverageLabel", "Coverage")}</th>
                    <th className="pb-2 pr-4">{t("cmoConnectors.owner", "Owner")}</th>
                    <th className="pb-2 pr-4">{t("cmoConnectors.account", "Account")}</th>
                    <th className="pb-2 pr-4">{t("cmoConnectors.lastSync", "Last Sync")}</th>
                    <th className="pb-2 pr-4">{t("cmoConnectors.requiredAccess", "Required Access")}</th>
                    <th className="pb-2">{t("cmoConnectors.action", "Action")}</th>
                  </tr>
                </thead>
                <tbody>
                  {connectorRows.map((row) => {
                    const health = connectorStatusFor(row.health_status);
                    const cta = connectorCtaFor(row.cta_state);
                    const access = row.required_scopes.length > 0
                      ? row.required_scopes.join(", ")
                      : row.required_credentials.join(", ");
                    return (
                      <tr key={row.key} className="border-b align-top last:border-0">
                        <td className="py-2 pr-4">
                          <div className="font-medium">{row.name}</div>
                          <div className="text-xs text-muted-foreground">
                            {row.key} · {row.category}
                          </div>
                        </td>
                        <td className="py-2 pr-4">
                          <Badge variant={CONNECTOR_BADGE_VARIANT[health]}>
                            {connectorStatusLabels[health]}
                          </Badge>
                          {row.missing_scopes && row.missing_scopes.length > 0 && (
                            <div className="mt-1 text-xs text-muted-foreground">
                              {t("cmoConnectors.missingScopesLabel", "Missing")}: {row.missing_scopes.join(", ")}
                            </div>
                          )}
                        </td>
                        <td className="py-2 pr-4">
                          {coverageLabels[row.data_coverage_status] || row.data_coverage_status}
                        </td>
                        <td className="py-2 pr-4">{row.owner || t("cmoConnectors.unassigned", "Unassigned")}</td>
                        <td className="py-2 pr-4">{row.account_id || t("cmoConnectors.notAvailable", "N/A")}</td>
                        <td className="py-2 pr-4">{formatDateTime(row.last_sync_at)}</td>
                        <td className="max-w-[320px] py-2 pr-4 text-xs text-muted-foreground">{access}</td>
                        <td className="py-2">
                          {cta === "none" ? (
                            <span className="text-xs text-muted-foreground">{ctaLabels.none}</span>
                          ) : (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => {
                                window.location.href = connectorActionTarget(row);
                              }}
                            >
                              {ctaLabels[cta]}
                            </Button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
            <div>
              <CardTitle className="text-sm font-semibold">
                {t("cmoConnectorContracts.title", "Marketing Connector Contracts")}
              </CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                {t(
                  "cmoConnectorContracts.subtitle",
                  "Read access, write access, retry budgets, idempotency, and external write confirmation are evaluated separately for production CMO workflows.",
                )}
              </p>
            </div>
            {contractSummary && (
              <div className="flex flex-wrap gap-2">
                <Badge variant={readinessVariant(contractSummary.readiness)}>
                  {contractSummary.read_ready}/{contractSummary.configured} {t("cmoConnectorContracts.summaryReadReadyLabel", "read-ready")}
                </Badge>
                <Badge variant={contractSummary.write_ready > 0 ? "success" : "secondary"}>
                  {contractSummary.write_ready} {t("cmoConnectorContracts.summaryWriteReadyLabel", "write-ready")}
                </Badge>
                <Badge variant={contractSummary.write_unconfirmed > 0 ? "destructive" : "outline"}>
                  {contractSummary.write_unconfirmed} {t("cmoConnectorContracts.summaryUnconfirmedLabel", "unconfirmed")}
                </Badge>
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {contractRows.length === 0 ? (
            <div className="rounded-md border bg-muted/40 px-3 py-3 text-sm text-muted-foreground">
              {t(
                "cmoConnectorContracts.empty",
                "Connector contract status is unavailable. Production CMO workflows cannot treat connector access or external writes as ready without it.",
              )}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1220px] text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-2 pr-4">{t("cmoConnectorContracts.system", "System")}</th>
                    <th className="pb-2 pr-4">{t("cmoConnectorContracts.contractState", "Contract")}</th>
                    <th className="pb-2 pr-4">{t("cmoConnectorContracts.read", "Read")}</th>
                    <th className="pb-2 pr-4">{t("cmoConnectorContracts.write", "Write")}</th>
                    <th className="pb-2 pr-4">{t("cmoConnectorContracts.authFreshness", "Auth / Freshness")}</th>
                    <th className="pb-2 pr-4">{t("cmoConnectorContracts.retry", "Retry")}</th>
                    <th className="pb-2 pr-4">{t("cmoConnectorContracts.confirmation", "Confirmation")}</th>
                    <th className="pb-2">{t("cmoConnectorContracts.action", "Action")}</th>
                  </tr>
                </thead>
                <tbody>
                  {contractRows.map((row) => {
                    const state = connectorContractStateFor(row.contract_state);
                    const cta = row.next_action_cta || "none";
                    const confirmationState = connectorContractStateFor(row.external_write_confirmation_status);
                    const account = row.account_id || row.workspace_id || row.vendor_id || t("cmoConnectors.notAvailable", "N/A");
                    const writeCapabilities = row.write_capabilities.length > 0
                      ? row.write_capabilities.join(", ")
                      : t("cmoConnectorContracts.noWrites", "No write capability");
                    return (
                      <tr key={row.connector_key} className="border-b align-top last:border-0">
                        <td className="py-2 pr-4">
                          <div className="font-medium">{row.name}</div>
                          <div className="text-xs text-muted-foreground">
                            {row.connector_key} / {row.category} / {account}
                          </div>
                        </td>
                        <td className="py-2 pr-4">
                          <Badge variant={CONNECTOR_CONTRACT_BADGE_VARIANT[state]}>
                            {contractStateLabels[state]}
                          </Badge>
                          {row.degraded_mode_reason && (
                            <div className="mt-1 max-w-[280px] text-xs text-muted-foreground">
                              {row.degraded_mode_reason}
                            </div>
                          )}
                          {row.mock_or_test_double && (
                            <div className="mt-1 text-xs text-red-700">
                              {t("cmoConnectorContracts.mockProof", "Mock/test proof is not production proof.")}
                            </div>
                          )}
                        </td>
                        <td className="py-2 pr-4">
                          <Badge variant={contractStatusVariant(row.read_status)}>
                            {contractReadinessLabels[row.read_status] || row.read_status}
                          </Badge>
                          <div className="mt-1 max-w-[220px] text-xs text-muted-foreground">
                            {row.read_capabilities.join(", ")}
                          </div>
                          {row.read_scope_evidence && row.read_scope_evidence.length > 0 && (
                            <div className="mt-1 max-w-[260px] text-xs text-emerald-700">
                              {row.read_scope_evidence.join(" ")}
                            </div>
                          )}
                          {row.missing_read_scopes.length > 0 && (
                            <div className="mt-1 max-w-[260px] text-xs text-amber-700">
                              {t("cmoConnectorContracts.missingReadScopes", "Missing read scopes")}: {row.missing_read_scopes.join(", ")}
                            </div>
                          )}
                        </td>
                        <td className="py-2 pr-4">
                          <Badge variant={contractStatusVariant(row.write_status)}>
                            {contractReadinessLabels[row.write_status] || row.write_status}
                          </Badge>
                          <div className="mt-1 max-w-[260px] text-xs text-muted-foreground">
                            {writeCapabilities}
                          </div>
                          {row.missing_write_scopes.length > 0 && (
                            <div className="mt-1 max-w-[260px] text-xs text-muted-foreground">
                              {t("cmoConnectorContracts.missingWriteScopes", "Missing write scopes")}: {row.missing_write_scopes.join(", ")}
                            </div>
                          )}
                        </td>
                        <td className="py-2 pr-4">
                          <div>{row.auth_status}</div>
                          <div className="text-xs text-muted-foreground">
                            {row.data_freshness.status} / {formatDateTime(row.last_sync_at)}
                          </div>
                        </td>
                        <td className="py-2 pr-4">
                          <div>{formatContractRetryBudget(row)}</div>
                          <div className="text-xs text-muted-foreground">
                            {row.idempotency_key_supported
                              ? t("cmoConnectorContracts.idempotencyReady", "Idempotency ready")
                              : t("cmoConnectorContracts.idempotencyMissing", "No idempotency proof")}
                          </div>
                        </td>
                        <td className="py-2 pr-4">
                          <Badge variant={contractStatusVariant(row.external_write_confirmation_status)}>
                            {row.external_write_confirmation_status === "none"
                              ? t("cmoConnectorContracts.states.none", "None")
                              : contractStateLabels[confirmationState]}
                          </Badge>
                        </td>
                        <td className="py-2">
                          {cta === "none" ? (
                            <span className="text-xs text-muted-foreground">{contractCtaLabels.none}</span>
                          ) : (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => {
                                window.location.href = "/dashboard/connectors";
                              }}
                            >
                              {contractCtaLabels[cta] || cta.replace(/_/g, " ")}
                            </Button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
            <div>
              <CardTitle className="text-sm font-semibold">
                {t("cmoDataReadiness.title", "Marketing Data Readiness")}
              </CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                {t(
                  "cmoDataReadiness.subtitle",
                  "Canonical CMO KPIs need validated field mappings and historical backfill before production confidence is available.",
                )}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {kpiReadiness && (
                <Badge variant={readinessVariant(kpiReadiness.status)}>
                  {readinessLabels[kpiReadiness.status] || kpiReadiness.status}
                </Badge>
              )}
              {fieldMappingSummary && (
                <Badge variant={readinessVariant(fieldMappingSummary.readiness)}>
                  {fieldMappingSummary.valid}/{fieldMappingSummary.total} {t("cmoDataReadiness.summaryMappedLabel", "mapped")}
                </Badge>
              )}
              {backfillSummary && (
                <Badge variant={readinessVariant(backfillSummary.readiness)}>
                  {backfillSummary.completed}/{backfillSummary.total} {t("cmoDataReadiness.summaryBackfilledLabel", "backfilled")}
                </Badge>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          {kpiReadiness && kpiReadiness.status !== "ready" && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              {kpiReadiness.status === "blocked"
                ? t(
                    "cmoDataReadiness.kpiBlocked",
                    "CMO KPI readiness is blocked by missing mappings or incomplete backfill.",
                  )
                : t(
                    "cmoDataReadiness.kpiDegraded",
                    "CMO KPI readiness is degraded; treat values as directional until setup is complete.",
                  )}
              <div className="mt-1 text-xs">
                {(kpiReadiness.blocked_reasons[0] || kpiReadiness.degraded_reasons[0] || "").trim()}
              </div>
            </div>
          )}

          <div>
            <h3 className="mb-2 text-sm font-semibold">{t("cmoDataReadiness.fieldMappingsTitle", "Field Mapping")}</h3>
            {fieldMappingRows.length === 0 ? (
              <div className="rounded-md border bg-muted/40 px-3 py-3 text-sm text-muted-foreground">
                {t(
                  "cmoDataReadiness.emptyMappings",
                  "Field mapping status is unavailable. Production CMO KPIs cannot be considered ready without it.",
                )}
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[920px] text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 pr-4">{t("cmoDataReadiness.mapping", "Mapping")}</th>
                      <th className="pb-2 pr-4">{t("cmoDataReadiness.status", "Status")}</th>
                      <th className="pb-2 pr-4">{t("cmoDataReadiness.sources", "Sources")}</th>
                      <th className="pb-2 pr-4">{t("cmoDataReadiness.missingFields", "Missing Fields")}</th>
                      <th className="pb-2 pr-4">{t("cmoDataReadiness.affectedKpis", "Affected KPIs")}</th>
                      <th className="pb-2 pr-4">{t("cmoDataReadiness.lastUpdated", "Last Updated")}</th>
                      <th className="pb-2">{t("cmoDataReadiness.action", "Action")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {fieldMappingRows.map((row) => {
                      const status = fieldStatusFor(row.status);
                      const cta = row.next_action_cta || "none";
                      return (
                        <tr key={row.key} className="border-b align-top last:border-0">
                          <td className="py-2 pr-4">
                            <div className="font-medium">{row.name}</div>
                            <div className="text-xs text-muted-foreground">{row.key}</div>
                          </td>
                          <td className="py-2 pr-4">
                            <Badge variant={FIELD_MAPPING_BADGE_VARIANT[status]}>
                              {fieldStatusLabels[status]}
                            </Badge>
                            {row.blocking_reason && (
                              <div className="mt-1 text-xs text-muted-foreground">{row.blocking_reason}</div>
                            )}
                          </td>
                          <td className="py-2 pr-4">{row.sources.length > 0 ? row.sources.join(", ") : row.source_categories.join(", ")}</td>
                          <td className="py-2 pr-4">{row.missing_fields.length > 0 ? row.missing_fields.join(", ") : t("cmoDataReadiness.none", "None")}</td>
                          <td className="max-w-[260px] py-2 pr-4 text-xs text-muted-foreground">{row.affected_kpis.join(", ")}</td>
                          <td className="py-2 pr-4">{formatDateTime(row.last_updated_at)}</td>
                          <td className="py-2">
                            {cta === "none" ? (
                              <span className="text-xs text-muted-foreground">{mappingCtaLabels.none}</span>
                            ) : (
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => {
                                  window.location.href = dataReadinessActionTarget();
                                }}
                              >
                                {mappingCtaLabels[cta] || cta.replace(/_/g, " ")}
                              </Button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div>
            <h3 className="mb-2 text-sm font-semibold">{t("cmoDataReadiness.backfillTitle", "Historical Backfill")}</h3>
            {backfillRows.length === 0 ? (
              <div className="rounded-md border bg-muted/40 px-3 py-3 text-sm text-muted-foreground">
                {t(
                  "cmoDataReadiness.emptyBackfill",
                  "No connected marketing source has a historical backfill projection yet.",
                )}
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[920px] text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 pr-4">{t("cmoDataReadiness.source", "Source")}</th>
                      <th className="pb-2 pr-4">{t("cmoDataReadiness.status", "Status")}</th>
                      <th className="pb-2 pr-4">{t("cmoDataReadiness.dateRange", "Date Range")}</th>
                      <th className="pb-2 pr-4">{t("cmoDataReadiness.records", "Records")}</th>
                      <th className="pb-2 pr-4">{t("cmoDataReadiness.lastRun", "Last Run")}</th>
                      <th className="pb-2 pr-4">{t("cmoDataReadiness.blocker", "Blocker")}</th>
                      <th className="pb-2">{t("cmoDataReadiness.action", "Action")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {backfillRows.map((row) => {
                      const status = backfillStatusFor(row.status);
                      const cta = row.next_action_cta || "none";
                      return (
                        <tr key={row.source_connector_key} className="border-b align-top last:border-0">
                          <td className="py-2 pr-4">
                            <div className="font-medium">{row.source_name}</div>
                            <div className="text-xs text-muted-foreground">{row.source_connector_key} Â· {row.category}</div>
                          </td>
                          <td className="py-2 pr-4">
                            <Badge variant={BACKFILL_BADGE_VARIANT[status]}>
                              {backfillStatusLabels[status]}
                            </Badge>
                          </td>
                          <td className="py-2 pr-4">{formatDateRange(row.requested_start, row.requested_end)}</td>
                          <td className="py-2 pr-4">{formatBackfillRecords(row)}</td>
                          <td className="py-2 pr-4">{formatDateTime(row.last_run_at)}</td>
                          <td className="max-w-[240px] py-2 pr-4 text-xs text-muted-foreground">
                            {row.blocking_reason || t("cmoDataReadiness.none", "None")}
                          </td>
                          <td className="py-2">
                            {cta === "none" ? (
                              <span className="text-xs text-muted-foreground">{backfillCtaLabels.none}</span>
                            ) : (
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => {
                                  window.location.href = dataReadinessActionTarget();
                                }}
                              >
                                {backfillCtaLabels[cta] || cta.replace(/_/g, " ")}
                              </Button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
            <div>
              <CardTitle className="text-sm font-semibold">
                {t("cmoWorkflowGates.title", "CMO Workflow Promotion Gates")}
              </CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                {t(
                  "cmoWorkflowGates.subtitle",
                  "Workflows remain read-only until their own connector, mapping, backfill, policy, and shadow-quality gates pass.",
                )}
              </p>
            </div>
            {workflowSummary && (
              <div className="flex flex-wrap gap-2">
                <Badge variant="success">
                  {workflowSummary.active}/{workflowSummary.total} {t("cmoWorkflowGates.summaryActiveLabel", "active")}
                </Badge>
                <Badge variant="warning">
                  {workflowSummary.promotion_ready} {t("cmoWorkflowGates.summaryReadyLabel", "ready")}
                </Badge>
                <Badge variant={workflowSummary.promotion_blocked || workflowSummary.unavailable ? "destructive" : "secondary"}>
                  {workflowSummary.promotion_blocked + workflowSummary.unavailable} {t("cmoWorkflowGates.summaryBlockedLabel", "blocked")}
                </Badge>
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {workflowRows.length === 0 ? (
            <div className="rounded-md border bg-muted/40 px-3 py-3 text-sm text-muted-foreground">
              {t(
                "cmoWorkflowGates.empty",
                "Workflow activation status is unavailable. Production CMO workflows cannot be promoted without per-workflow gates.",
              )}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1040px] text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-2 pr-4">{t("cmoWorkflowGates.workflow", "Workflow")}</th>
                    <th className="pb-2 pr-4">{t("cmoWorkflowGates.state", "State")}</th>
                    <th className="pb-2 pr-4">{t("cmoWorkflowGates.mode", "Mode")}</th>
                    <th className="pb-2 pr-4">{t("cmoWorkflowGates.owners", "Owners")}</th>
                    <th className="pb-2 pr-4">{t("cmoWorkflowGates.shadowQuality", "Shadow Quality")}</th>
                    <th className="pb-2 pr-4">{t("cmoWorkflowGates.blockers", "Blockers")}</th>
                    <th className="pb-2 pr-4">{t("cmoWorkflowGates.externalWrites", "External Writes")}</th>
                    <th className="pb-2">{t("cmoWorkflowGates.action", "Action")}</th>
                  </tr>
                </thead>
                <tbody>
                  {workflowRows.map((row) => {
                    const state = workflowStateFor(row.state);
                    const cta = row.next_action_cta || "none";
                    const firstReason = row.blocked_reasons[0] || row.degraded_reasons[0] || "";
                    const quality = row.shadow_quality;
                    return (
                      <tr key={row.workflow_key} className="border-b align-top last:border-0">
                        <td className="py-2 pr-4">
                          <div className="font-medium">{row.name}</div>
                          <div className="text-xs text-muted-foreground">{row.workflow_key}</div>
                        </td>
                        <td className="py-2 pr-4">
                          <Badge variant={WORKFLOW_BADGE_VARIANT[state]}>
                            {workflowStateLabels[state]}
                          </Badge>
                        </td>
                        <td className="py-2 pr-4">{row.configured_mode}</td>
                        <td className="max-w-[220px] py-2 pr-4 text-xs text-muted-foreground">
                          <div>
                            {t("cmoWorkflowGates.approvalOwner", "Approval")}:{" "}
                            {row.approval_owner || t("cmoConnectors.unassigned", "Unassigned")}
                          </div>
                          <div>
                            {t("cmoWorkflowGates.policyOwner", "Policy")}:{" "}
                            {row.policy_owner || t("cmoConnectors.unassigned", "Unassigned")}
                          </div>
                        </td>
                        <td className="py-2 pr-4">
                          <div>{quality.status.replace(/_/g, " ")}</div>
                          <div className="text-xs text-muted-foreground">
                            {quality.sample_count}/{quality.required_sample_count} {t("cmoWorkflowGates.runs", "runs")} ·{" "}
                            {(quality.success_rate * 100).toFixed(0)}%
                          </div>
                        </td>
                        <td className="max-w-[320px] py-2 pr-4 text-xs text-muted-foreground">
                          {firstReason || t("cmoDataReadiness.none", "None")}
                        </td>
                        <td className="py-2 pr-4">
                          <Badge variant={row.external_writes_allowed ? "success" : "secondary"}>
                            {row.external_writes_allowed
                              ? t("cmoWorkflowGates.writesAllowed", "Allowed")
                              : t("cmoWorkflowGates.readOnly", "Read-only")}
                          </Badge>
                        </td>
                        <td className="py-2">
                          {cta === "none" ? (
                            <span className="text-xs text-muted-foreground">{workflowCtaLabels.none}</span>
                          ) : (
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => {
                                window.location.href = workflowActionTarget();
                              }}
                            >
                              {workflowCtaLabels[cta] || cta.replace(/_/g, " ")}
                            </Button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        {kpiCards.map((m) => (
          <Card key={m.label}>
            <CardHeader>
              <CardTitle className="text-sm text-muted-foreground">{m.label}</CardTitle>
            </CardHeader>
            <CardContent>
              <p className={`text-2xl font-bold ${m.color}`}>{m.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-semibold">{t("cmoCapability.title", "CMO Capability Status")}</CardTitle>
          <p className="text-sm text-muted-foreground">
            {t(
              "cmoCapability.subtitle",
              "Current CMO autonomy is partial: stronger operators are separated from beta, stub, unavailable, and demo surfaces.",
            )}
          </p>
        </CardHeader>
        <CardContent>
          <div className="mb-3 rounded-md border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
            {t(
              "cmoCapability.notice",
              "Do not treat this dashboard as proof of end-to-end CMO agent autonomy.",
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="pb-2 pr-4">{t("cmoCapability.capability", "Capability")}</th>
                  <th className="pb-2 pr-4">{t("cmoCapability.state", "State")}</th>
                  <th className="pb-2">{t("cmoCapability.currentLimit", "Current limit")}</th>
                </tr>
              </thead>
              <tbody>
                {capabilityRows.map((row) => (
                  <tr key={row.name} className="border-b last:border-0">
                    <td className="py-2 pr-4 font-medium">{row.name}</td>
                    <td className="py-2 pr-4">
                      <Badge variant={STATUS_BADGE_VARIANT[row.status]}>{statusLabels[row.status]}</Badge>
                    </td>
                    <td className="py-2 text-muted-foreground">{row.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* ── Domain Breakdown ── */}
      {displayDomains.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            {t("kpi.noActivity", "No agent activity yet. Once agents run tasks, KPIs will appear here.")}
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">{t("kpi.domainBreakdown", "Domain Breakdown")}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 pr-4">{t("kpi.domain", "Domain")}</th>
                      <th className="pb-2 pr-4 text-right">{t("kpi.total", "Total Tasks")}</th>
                      <th className="pb-2 pr-4 text-right">{t("kpi.completed", "Completed")}</th>
                      <th className="pb-2 pr-4 text-right">{t("kpi.failed", "Failed")}</th>
                      <th className="pb-2 text-right">{t("kpi.avgConfidence", "Avg Confidence")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {displayDomains.map((d) => (
                      <tr key={d.domain} className="border-b last:border-0">
                        <td className="py-2 pr-4 font-medium">{d.domain}</td>
                        <td className="py-2 pr-4 text-right">{formatNumber(d.total)}</td>
                        <td className="py-2 pr-4 text-right text-green-600">
                          {formatNumber(d.completed)}
                        </td>
                        <td className="py-2 pr-4 text-right text-red-600">
                          {formatNumber(d.failed)}
                        </td>
                        <td className="py-2 text-right text-muted-foreground">
                          {(d.avg_confidence * 100).toFixed(0)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">{t("kpi.totalTasksPerDomain", "Total Tasks per Domain")}</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={displayDomains}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="domain" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} />
                  <Tooltip />
                  <Bar dataKey="completed" name="Completed" fill="#22c55e" stackId="a" />
                  <Bar dataKey="failed" name="Failed" fill="#ef4444" stackId="a" />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
