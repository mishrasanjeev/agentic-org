/**
 * CMODashboard Component Tests
 *
 * Tests rendering states and KPI cards using mocked API responses
 * that match the current CMOKPIData interface (basic metrics shape).
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { HelmetProvider } from "react-helmet-async";

// ---------------------------------------------------------------------------
// Mock API module
// ---------------------------------------------------------------------------

const mockGet = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  },
}));

// ---------------------------------------------------------------------------
// Import component under test
// ---------------------------------------------------------------------------

import CMODashboard from "@/pages/CMODashboard";

// ---------------------------------------------------------------------------
// Test data matching current CMOKPIData interface (basic metrics shape)
// ---------------------------------------------------------------------------

const MOCK_CONNECTOR_SETUP = [
  {
    key: "hubspot",
    name: "HubSpot",
    category: "CRM",
    required_scopes: ["crm.objects.contacts.read", "crm.objects.deals.read"],
    required_credentials: ["client_id", "client_secret", "refresh_token"],
    configured_status: "configured",
    health_status: "healthy",
    last_sync_at: "2026-05-23T07:00:00+00:00",
    owner: "revops@example.com",
    account_id: "portal-123",
    data_coverage_status: "ready",
    cta_state: "none",
    missing_scopes: [],
  },
  {
    key: "google_ads",
    name: "Google Ads",
    category: "Ads",
    required_scopes: ["https://www.googleapis.com/auth/adwords"],
    required_credentials: ["developer_token", "customer_id"],
    configured_status: "unconfigured",
    health_status: "missing",
    last_sync_at: null,
    owner: "Unassigned",
    account_id: null,
    data_coverage_status: "missing",
    cta_state: "setup",
    missing_scopes: ["https://www.googleapis.com/auth/adwords"],
  },
  {
    key: "ga4",
    name: "Google Analytics 4",
    category: "Analytics",
    required_scopes: ["https://www.googleapis.com/auth/analytics.readonly"],
    required_credentials: ["property_id"],
    configured_status: "configured",
    health_status: "expired_auth",
    last_sync_at: "2026-05-20T07:00:00+00:00",
    owner: "analytics@example.com",
    account_id: "properties/123",
    data_coverage_status: "blocked",
    cta_state: "reconnect",
    missing_scopes: [],
  },
  {
    key: "mailchimp",
    name: "Mailchimp",
    category: "Email",
    required_scopes: ["campaigns.read", "lists.read"],
    required_credentials: ["api_key", "audience_id"],
    configured_status: "configured",
    health_status: "insufficient_scope",
    last_sync_at: "2026-05-23T07:00:00+00:00",
    owner: "email@example.com",
    account_id: "aud-1",
    data_coverage_status: "blocked",
    cta_state: "add_scope",
    missing_scopes: ["lists.read"],
  },
  {
    key: "ahrefs",
    name: "Ahrefs",
    category: "SEO",
    required_scopes: [],
    required_credentials: ["api_token", "site_url"],
    configured_status: "configured",
    health_status: "stale",
    last_sync_at: "2026-05-18T07:00:00+00:00",
    owner: "seo@example.com",
    account_id: "agenticorg.ai",
    data_coverage_status: "stale",
    cta_state: "refresh",
    missing_scopes: [],
  },
  {
    key: "brandwatch",
    name: "Brandwatch",
    category: "Brand",
    required_scopes: ["read_mentions"],
    required_credentials: ["client_id", "client_secret"],
    configured_status: "configured",
    health_status: "degraded",
    last_sync_at: "2026-05-23T07:00:00+00:00",
    owner: "brand@example.com",
    account_id: "project-9",
    data_coverage_status: "partial",
    cta_state: "review",
    missing_scopes: [],
  },
];

const MOCK_CONNECTOR_CONTRACTS = [
  {
    connector_key: "hubspot",
    name: "HubSpot",
    category: "CRM",
    configured_status: "configured",
    vendor_id: null,
    account_id: "portal-123",
    workspace_id: null,
    read_capabilities: ["contacts", "deals", "campaigns"],
    write_capabilities: ["update_crm"],
    required_read_scopes: ["crm.objects.contacts.read", "crm.objects.deals.read"],
    required_write_scopes: ["crm.objects.contacts.write"],
    granted_scopes: ["crm.objects.contacts.read", "crm.objects.deals.read", "crm.objects.contacts.write"],
    missing_read_scopes: [],
    missing_write_scopes: [],
    auth_status: "valid",
    health_status: "healthy",
    contract_state: "healthy",
    read_status: "ready",
    write_status: "ready",
    read_ready: true,
    write_ready: true,
    production_ready: true,
    mock_or_test_double: false,
    last_sync_at: "2026-05-23T07:00:00+00:00",
    source_objects: [{ id: "portal-123", type: "portal", url: "https://app.hubspot.com" }],
    data_freshness: {
      status: "fresh",
      ttl_seconds: 86400,
      last_sync_at: "2026-05-23T07:00:00+00:00",
    },
    retry_budget: {
      max_attempts: 3,
      attempts_used: 1,
      remaining_attempts: 2,
      reset_at: null,
      next_retry_at: "2026-05-23T08:00:00+00:00",
      idempotency_key: "hubspot-update-1",
      idempotency_supported: true,
    },
    degraded_mode_reason: null,
    idempotency_key_supported: true,
    external_write_confirmation_status: "write_confirmed",
    external_write_confirmations: [
      {
        action: "update_crm",
        status: "write_confirmed",
        idempotency_key: "hubspot-update-1",
      },
    ],
    next_action_cta: "none",
  },
  {
    connector_key: "google_ads",
    name: "Google Ads",
    category: "Ads",
    configured_status: "unconfigured",
    vendor_id: "google",
    account_id: null,
    workspace_id: null,
    read_capabilities: ["campaigns", "spend"],
    write_capabilities: ["launch_campaign", "mutate_ad_budget"],
    required_read_scopes: ["https://www.googleapis.com/auth/adwords"],
    required_write_scopes: ["https://www.googleapis.com/auth/adwords"],
    granted_scopes: [],
    missing_read_scopes: ["https://www.googleapis.com/auth/adwords"],
    missing_write_scopes: ["https://www.googleapis.com/auth/adwords"],
    auth_status: "missing",
    health_status: "missing",
    contract_state: "missing_scope",
    read_status: "blocked",
    write_status: "blocked",
    read_ready: false,
    write_ready: false,
    production_ready: false,
    mock_or_test_double: false,
    last_sync_at: null,
    source_objects: [],
    data_freshness: {
      status: "missing",
      ttl_seconds: 14400,
      last_sync_at: null,
    },
    retry_budget: {
      max_attempts: 0,
      attempts_used: 0,
      remaining_attempts: 0,
      reset_at: null,
      next_retry_at: null,
      idempotency_key: null,
      idempotency_supported: false,
    },
    degraded_mode_reason: "Missing read scopes: https://www.googleapis.com/auth/adwords.",
    idempotency_key_supported: false,
    external_write_confirmation_status: "none",
    external_write_confirmations: [],
    next_action_cta: "setup",
  },
  {
    connector_key: "mailchimp",
    name: "Mailchimp",
    category: "Email",
    configured_status: "configured",
    vendor_id: null,
    account_id: "aud-1",
    workspace_id: null,
    read_capabilities: ["campaigns", "audiences"],
    write_capabilities: ["send_email"],
    required_read_scopes: ["campaigns.read", "lists.read"],
    required_write_scopes: ["campaigns.write"],
    granted_scopes: ["campaigns.read"],
    missing_read_scopes: ["lists.read"],
    missing_write_scopes: ["campaigns.write"],
    auth_status: "valid",
    health_status: "insufficient_scope",
    contract_state: "missing_scope",
    read_status: "blocked",
    write_status: "blocked",
    read_ready: false,
    write_ready: false,
    production_ready: true,
    mock_or_test_double: false,
    last_sync_at: "2026-05-23T07:00:00+00:00",
    source_objects: [{ id: "aud-1", type: "audience", url: "https://mailchimp.com" }],
    data_freshness: {
      status: "fresh",
      ttl_seconds: 86400,
      last_sync_at: "2026-05-23T07:00:00+00:00",
    },
    retry_budget: {
      max_attempts: 2,
      attempts_used: 0,
      remaining_attempts: 2,
      reset_at: null,
      next_retry_at: null,
      idempotency_key: "mailchimp-send-1",
      idempotency_supported: true,
    },
    degraded_mode_reason: "Missing write scopes: campaigns.write.",
    idempotency_key_supported: true,
    external_write_confirmation_status: "write_unconfirmed",
    external_write_confirmations: [
      {
        action: "send_email",
        status: "write_unconfirmed",
        idempotency_key: "mailchimp-send-1",
      },
    ],
    next_action_cta: "add_scope",
  },
];

const MOCK_FIELD_MAPPING_STATUS = [
  {
    key: "lifecycle_stages",
    name: "Lifecycle stages",
    status: "valid",
    source_categories: ["CRM"],
    sources: ["HubSpot"],
    required_fields: ["source_field", "stage_map"],
    missing_fields: [],
    affected_kpis: ["MQL", "SQL", "Pipeline contribution"],
    missing_blocks: true,
    last_updated_at: "2026-05-01T00:00:00+00:00",
    blocking_reason: null,
    next_action_cta: "none",
  },
  {
    key: "opportunity_revenue",
    name: "Opportunity revenue fields",
    status: "unmapped",
    source_categories: ["CRM", "Finance"],
    sources: ["HubSpot"],
    required_fields: ["amount_field", "close_date_field", "currency_field"],
    missing_fields: ["amount_field", "close_date_field", "currency_field"],
    affected_kpis: ["Pipeline contribution", "CAC", "ROAS"],
    missing_blocks: true,
    last_updated_at: null,
    blocking_reason: null,
    next_action_cta: "map_fields",
  },
  {
    key: "utm_fields",
    name: "UTM fields",
    status: "partially_mapped",
    source_categories: ["Ads", "Analytics", "Email", "CMS"],
    sources: ["Google Ads", "Google Analytics 4"],
    required_fields: ["source", "medium", "campaign"],
    missing_fields: ["campaign"],
    affected_kpis: ["ROAS", "CAC", "Attribution"],
    missing_blocks: false,
    last_updated_at: "2026-05-01T00:00:00+00:00",
    blocking_reason: null,
    next_action_cta: "complete_mapping",
  },
  {
    key: "currency",
    name: "Currency",
    status: "invalid",
    source_categories: [],
    sources: [],
    required_fields: ["currency"],
    missing_fields: [],
    affected_kpis: ["CAC", "ROAS", "Pipeline contribution"],
    missing_blocks: true,
    last_updated_at: "2026-05-01T00:00:00+00:00",
    blocking_reason: "Currency 'ZZZ' is not in the supported ISO currency allowlist.",
    next_action_cta: "fix_mapping",
  },
  {
    key: "timezone",
    name: "Timezone",
    status: "stale",
    source_categories: [],
    sources: [],
    required_fields: ["timezone"],
    missing_fields: [],
    affected_kpis: ["Campaign performance", "Reporting freshness"],
    missing_blocks: true,
    last_updated_at: "2025-01-01T00:00:00+00:00",
    blocking_reason: null,
    next_action_cta: "review_mapping",
  },
  {
    key: "consent_unsubscribe",
    name: "Consent and unsubscribe fields",
    status: "blocked",
    source_categories: ["Email", "CRM"],
    sources: ["Mailchimp"],
    required_fields: ["consent_field", "unsubscribe_field"],
    missing_fields: ["consent_field"],
    affected_kpis: ["Email performance", "Lead nurture readiness"],
    missing_blocks: true,
    last_updated_at: null,
    blocking_reason: "Legal approval required before mapping consent fields.",
    next_action_cta: "resolve_mapping_blocker",
  },
];

const MOCK_BACKFILL_STATUS = [
  {
    source_connector_key: "hubspot",
    source_name: "HubSpot",
    category: "CRM",
    status: "completed",
    requested_start: "2025-05-01",
    requested_end: "2026-05-23",
    records_discovered: 1000,
    records_imported: 996,
    records_skipped: 4,
    records_failed: 0,
    last_run_at: "2026-05-23T10:00:00+00:00",
    blocking_reason: null,
    next_action_cta: "none",
  },
  {
    source_connector_key: "google_ads",
    source_name: "Google Ads",
    category: "Ads",
    status: "failed",
    requested_start: "2025-05-01",
    requested_end: "2026-05-23",
    records_discovered: 200,
    records_imported: 120,
    records_skipped: 10,
    records_failed: 70,
    last_run_at: "2026-05-23T10:00:00+00:00",
    blocking_reason: "Vendor export failed",
    next_action_cta: "retry_backfill",
  },
  {
    source_connector_key: "ga4",
    source_name: "Google Analytics 4",
    category: "Analytics",
    status: "blocked",
    requested_start: "2025-05-01",
    requested_end: "2026-05-23",
    records_discovered: null,
    records_imported: null,
    records_skipped: null,
    records_failed: null,
    last_run_at: null,
    blocking_reason: "Connector auth is expired and must be reauthorized.",
    next_action_cta: "resolve_blocker",
  },
];

const MOCK_SHADOW_QUALITY = {
  status: "passed",
  sample_count: 5,
  success_rate: 0.94,
  required_sample_count: 3,
  required_success_rate: 0.8,
  last_run_at: "2026-05-23T09:00:00+00:00",
  blocking_reason: null,
  next_action_cta: "none",
};

const MOCK_WORKFLOW_ACTIVATION_STATUS = [
  {
    workflow_key: "weekly_marketing_report",
    name: "Weekly Marketing Report",
    state: "promotion_ready",
    configured_mode: "shadow",
    required_connectors: ["CRM", "Ads", "Analytics", "Email"],
    optional_connectors: ["SEO", "Brand", "Finance"],
    required_mappings: ["lifecycle_stages", "opportunity_revenue", "campaign_ids"],
    optional_mappings: [],
    required_backfill_categories: ["CRM", "Ads", "Analytics", "Email"],
    optional_backfill_categories: ["SEO", "Brand", "Finance"],
    approval_owner: "cmo@example.com",
    policy_owner: "legal@example.com",
    shadow_quality: MOCK_SHADOW_QUALITY,
    blocked_reasons: [],
    degraded_reasons: [],
    next_action_cta: "promote_workflow",
    external_writes_allowed: false,
  },
  {
    workflow_key: "campaign_launch",
    name: "Campaign Launch",
    state: "promotion_blocked",
    configured_mode: "shadow",
    required_connectors: ["CRM", "Ads", "Analytics", "Email"],
    optional_connectors: ["CMS", "Social"],
    required_mappings: ["campaign_ids", "utm_fields", "consent_unsubscribe"],
    optional_mappings: ["account_domains"],
    required_backfill_categories: ["CRM", "Ads", "Analytics", "Email"],
    optional_backfill_categories: ["CMS", "Social"],
    approval_owner: null,
    policy_owner: "legal@example.com",
    shadow_quality: MOCK_SHADOW_QUALITY,
    blocked_reasons: ["Required Ads connector is not healthy (missing)."],
    degraded_reasons: [],
    next_action_cta: "fix_required_connector",
    external_writes_allowed: false,
  },
  {
    workflow_key: "daily_spend_optimization",
    name: "Daily Spend Optimization",
    state: "degraded",
    configured_mode: "active",
    required_connectors: ["CRM", "Ads", "Analytics"],
    optional_connectors: ["Finance"],
    required_mappings: ["opportunity_revenue", "campaign_ids", "utm_fields"],
    optional_mappings: [],
    required_backfill_categories: ["CRM", "Ads", "Analytics"],
    optional_backfill_categories: ["Finance"],
    approval_owner: "cmo@example.com",
    policy_owner: "finance@example.com",
    shadow_quality: MOCK_SHADOW_QUALITY,
    blocked_reasons: [],
    degraded_reasons: ["Optional Finance connector Stripe is stale."],
    next_action_cta: "review_degraded_dependency",
    external_writes_allowed: false,
  },
  {
    workflow_key: "content_pipeline",
    name: "Content Pipeline",
    state: "active",
    configured_mode: "active",
    required_connectors: ["CMS"],
    optional_connectors: ["Analytics", "SEO", "Social"],
    required_mappings: ["campaign_ids", "utm_fields", "timezone"],
    optional_mappings: [],
    required_backfill_categories: ["CMS"],
    optional_backfill_categories: ["Analytics", "SEO", "Social"],
    approval_owner: "content@example.com",
    policy_owner: "brand@example.com",
    shadow_quality: MOCK_SHADOW_QUALITY,
    blocked_reasons: [],
    degraded_reasons: [],
    next_action_cta: "none",
    external_writes_allowed: true,
  },
  {
    workflow_key: "lead_nurture",
    name: "Lead Nurture",
    state: "shadow",
    configured_mode: "shadow",
    required_connectors: ["CRM", "Email"],
    optional_connectors: ["ABM"],
    required_mappings: ["lifecycle_stages", "consent_unsubscribe", "timezone"],
    optional_mappings: ["account_domains"],
    required_backfill_categories: ["CRM", "Email"],
    optional_backfill_categories: ["ABM"],
    approval_owner: "cmo@example.com",
    policy_owner: "legal@example.com",
    shadow_quality: {
      ...MOCK_SHADOW_QUALITY,
      status: "not_measured",
      sample_count: 0,
      success_rate: 0,
      blocking_reason: "Recent shadow-run quality gate has not collected enough runs (0/3).",
      next_action_cta: "run_shadow_quality",
    },
    blocked_reasons: ["Recent shadow-run quality gate has not collected enough runs (0/3)."],
    degraded_reasons: [],
    next_action_cta: "run_shadow_quality",
    external_writes_allowed: false,
  },
  {
    workflow_key: "abm_sprint",
    name: "ABM Sprint",
    state: "unavailable",
    configured_mode: "shadow",
    required_connectors: ["CRM", "ABM"],
    optional_connectors: [],
    required_mappings: ["account_domains", "lifecycle_stages", "timezone"],
    optional_mappings: [],
    required_backfill_categories: ["CRM", "ABM"],
    optional_backfill_categories: [],
    approval_owner: null,
    policy_owner: null,
    shadow_quality: MOCK_SHADOW_QUALITY,
    blocked_reasons: ["First-class core marketing ABM agent is not implemented yet."],
    degraded_reasons: [],
    next_action_cta: "implement_first_class_agent",
    external_writes_allowed: false,
  },
  {
    workflow_key: "brand_crisis_response",
    name: "Brand Crisis Response",
    state: "paused",
    configured_mode: "paused",
    required_connectors: ["Brand"],
    optional_connectors: ["Social"],
    required_mappings: ["timezone"],
    optional_mappings: [],
    required_backfill_categories: ["Brand"],
    optional_backfill_categories: ["Social"],
    approval_owner: "brand@example.com",
    policy_owner: "legal@example.com",
    shadow_quality: MOCK_SHADOW_QUALITY,
    blocked_reasons: [],
    degraded_reasons: [],
    next_action_cta: "resume_workflow",
    external_writes_allowed: false,
  },
  {
    workflow_key: "seo_sprint",
    name: "SEO Sprint",
    state: "unavailable",
    configured_mode: "shadow",
    required_connectors: ["SEO", "Analytics", "CMS"],
    optional_connectors: [],
    required_mappings: ["campaign_ids", "utm_fields", "timezone"],
    optional_mappings: [],
    required_backfill_categories: ["SEO", "Analytics", "CMS"],
    optional_backfill_categories: [],
    approval_owner: null,
    policy_owner: null,
    shadow_quality: MOCK_SHADOW_QUALITY,
    blocked_reasons: ["SEO Strategist still wraps shared/base behavior and is not production-grade SEO execution."],
    degraded_reasons: [],
    next_action_cta: "implement_first_class_agent",
    external_writes_allowed: false,
  },
];

const MOCK_WORK_QUEUE = [
  {
    item_id: "cmo_wq_external_write_timeout_campaign_launch",
    type: "external_write_failure",
    category: "external_write",
    severity: "critical",
    priority_score: 1170,
    title: "External marketing write is timeout unknown",
    message: "Unknown vendor write state",
    affected_workflow: "campaign_launch",
    affected_capability: "Campaign Pilot",
    affected_kpi: null,
    affected_report: null,
    affected_connector: "google_ads",
    owner_role: "Marketing Ops",
    due_at: "2026-05-23T12:00:00+00:00",
    source_refs: [{ kind: "external_write", external_write_id: "write-123" }],
    audit_refs: ["mkt_write_timeout"],
    next_action_cta: {
      action_key: "manual_reconcile_before_retry",
      label: "Reconcile Before Retry",
      path: "/dashboard/approvals",
    },
    next_action_label: "Reconcile Before Retry",
    next_action_path: "/dashboard/approvals",
    next_action_key: "manual_reconcile_before_retry",
    status: "blocked",
    created_at: "2026-05-23T11:45:00+00:00",
    updated_at: "2026-05-23T11:45:00+00:00",
  },
  {
    item_id: "cmo_wq_report_campaign_ad_hoc_warning",
    type: "report_quality_gate_warning",
    category: "report",
    severity: "medium",
    priority_score: 520,
    title: "Report quality gate needs attention: Campaign Ad Hoc",
    message: "Report is internal_only until stale optional data is reviewed.",
    affected_workflow: "weekly_marketing_report",
    affected_capability: null,
    affected_kpi: null,
    affected_report: "campaign_performance_ad_hoc",
    affected_connector: null,
    owner_role: "Marketing Ops",
    due_at: null,
    source_refs: [{ kind: "report_quality_gate", report_type: "campaign_performance_ad_hoc" }],
    audit_refs: [],
    next_action_cta: {
      action_key: "review_report_warnings",
      label: "Review Report Warnings",
      path: "/dashboard/reports",
    },
    next_action_label: "Review Report Warnings",
    next_action_path: "/dashboard/reports",
    next_action_key: "review_report_warnings",
    status: "open",
    created_at: "2026-05-23T11:30:00+00:00",
    updated_at: "2026-05-23T11:30:00+00:00",
  },
];

const MOCK_APPROVAL_REVIEWS = [
  {
    approval_review_id: "cmo_approval_review_campaign",
    approval_id: "apr-campaign-1",
    workflow_id: "campaign_launch",
    workflow_run_id: "run-1",
    run_id: "run-1",
    step_id: "launch",
    action: "launch_campaign",
    action_type: "campaign_launch",
    status: "pending",
    requester: "campaign_pilot",
    agent_ref: "campaign_pilot",
    assigned_approver: "cmo@example.com",
    assigned_approver_role: "cmo",
    created_at: "2026-05-23T10:00:00+00:00",
    due_at: "2026-05-23T14:00:00+00:00",
    timeout_state: "pending",
    preview_payload: {
      campaign_name: "Q2 Pipeline Sprint",
      headline: "Pipeline without guesswork",
    },
    before_after_diff: {
      summary: "Draft campaign becomes live Google Ads launch.",
    },
    budget_impact: {
      amount: 1200,
      currency: "USD",
      summary: "Daily launch budget +$1,200.",
    },
    audience_impact: {
      estimated_recipients: 25000,
      summary: "Enterprise search audience.",
    },
    risk_flags: ["budget_change", "requires_approval"],
    source_refs: [{ type: "campaign_brief", id: "brief-1" }],
    connector_refs: [{ connector_key: "google_ads", write_status: "ready" }],
    agent_rationale: "ROAS shadow runs exceeded launch threshold.",
    policy_result: { decision: "requires_approval", reason: "Campaign launches require CMO approval." },
    policy_result_ref: "policy:approval",
    escalation_result: { decision: "no_escalation" },
    escalation_result_ref: "esc:no_escalation",
    timeout_result: { status: "pending", timed_out: false },
    timeout_result_ref: "timeout:apr-campaign-1",
    external_write_readiness: {
      status: "safe",
      reason: "Required connector contracts are write-safe.",
    },
    external_write_result_ref: "write:pending",
    audit_evidence: { ready: true, audit_refs: ["audit-approval-context"] },
    audit_refs: ["audit-approval-context"],
    rollback_stop_plan: {
      summary: "Pause campaign and restore previous budget cap.",
    },
    allowed_reviewer_actions: ["approve", "reject", "override", "request_changes", "pause"],
    blocked_reasons: [],
    related_work_queue_item_ids: ["cmo_wq_approval_campaign_launch"],
    next_action_cta: {
      action_key: "review_pending_approval",
      label: "Review Approval",
      path: "/dashboard/approvals",
    },
    evaluated_at: "2026-05-23T12:00:00+00:00",
  },
];

const MOCK_KPI_DRILLDOWNS = [
  {
    drilldown_id: "cmo_kpi_drilldown:cac",
    kpi_key: "cac",
    display_name: "CAC",
    description: "Customer acquisition cost for the evaluated period.",
    status: "blocked",
    value: null,
    unit: "currency",
    confidence: 0,
    formula: "total_marketing_spend / new_customers",
    formula_inputs: [
      { name: "total_marketing_spend", source_key: "total_marketing_spend", value: 1000, resolved: true },
      { name: "new_customers", source_key: "new_customers", value: 10, resolved: true },
    ],
    source_refs: [{ connector_key: "hubspot", object: "deals" }],
    connector_refs: [
      {
        type: "connector_setup",
        connector_key: "hubspot",
        category: "CRM",
        health_status: "healthy",
        last_sync_at: "2026-05-23T07:00:00+00:00",
      },
      {
        type: "connector_setup",
        connector_key: "google_ads",
        category: "Ads",
        health_status: "missing",
        last_sync_at: null,
      },
    ],
    field_mappings_used: [
      { key: "opportunity_revenue", status: "unmapped", audit_ref: "audit-mapping-opportunity_revenue" },
      { key: "campaign_ids", status: "valid", audit_ref: "audit-mapping-campaign_ids" },
    ],
    backfill_state: [{ source_connector_key: "hubspot", category: "CRM", status: "completed" }],
    reconciliation_checks: [
      {
        reconciliation_key: "paid_spend_totals_by_channel",
        status: "failed",
        severity: "high",
        confidence_impact: 0.25,
        next_action_cta: {
          action_key: "resolve_spend_reconciliation",
          label: "Resolve Spend Reconciliation",
          path: "/dashboard/cmo?panel=reconciliation",
        },
      },
    ],
    freshness_status: "fresh",
    freshness: { status: "fresh", last_updated_at: "2026-05-23T07:00:00+00:00" },
    confidence_impact_reasons: ["KPI reconciliation paid_spend_totals_by_channel is failed."],
    missing_requirements: { field_mappings: ["opportunity_revenue"] },
    blocked_reasons: ["Required mapping opportunity_revenue is unmapped."],
    degraded_reasons: [],
    related_work_queue_item_ids: ["cmo_wq_kpi_cac"],
    related_report_gate_ids: ["weekly_marketing_report"],
    policy_refs: [],
    audit_refs: ["audit-kpi-source"],
    owner_role: "growth_lead",
    next_action_cta: {
      action_key: "fix_required_mapping",
      label: "Fix Mapping",
      path: "/dashboard/connectors",
    },
    production_lineage_ready: true,
    production_lineage_status: "ready",
    last_computed_at: "2026-05-23T12:00:00+00:00",
  },
  {
    drilldown_id: "cmo_kpi_drilldown:roas",
    kpi_key: "roas",
    display_name: "ROAS",
    description: "Return on ad spend from attributed revenue and ad spend.",
    status: "degraded",
    value: 5,
    unit: "ratio",
    confidence: 0.65,
    formula: "attributed_revenue / ad_spend",
    formula_inputs: [
      { name: "attributed_revenue", source_key: "attributed_revenue", value: 5000, resolved: true },
      { name: "ad_spend", source_key: "ad_spend", value: 1000, resolved: true },
    ],
    source_refs: [{ connector_key: "hubspot", object: "deals" }],
    connector_refs: [{ type: "connector_setup", connector_key: "hubspot", category: "CRM" }],
    field_mappings_used: [{ key: "utm_fields", status: "partially_mapped" }],
    backfill_state: [{ source_connector_key: "hubspot", category: "CRM", status: "completed" }],
    reconciliation_checks: [],
    freshness_status: "stale",
    freshness: { status: "stale", last_updated_at: "2026-05-20T07:00:00+00:00" },
    confidence_impact_reasons: ["Source data is stale for this KPI TTL."],
    missing_requirements: {},
    blocked_reasons: [],
    degraded_reasons: ["Source data is stale for this KPI TTL."],
    related_work_queue_item_ids: [],
    related_report_gate_ids: ["daily_ad_performance"],
    policy_refs: [],
    audit_refs: ["audit-kpi-source"],
    owner_role: "growth_lead",
    next_action_cta: {
      action_key: "refresh_source_data",
      label: "Refresh Source Data",
      path: "/dashboard/connectors",
    },
    production_lineage_ready: true,
    production_lineage_status: "ready",
    last_computed_at: "2026-05-23T12:00:00+00:00",
  },
];

const MOCK_CMO_DATA = {
  demo: true,
  company_id: "comp-001",
  agent_count: 5,
  total_tasks_30d: 89,
  success_rate: 91.2,
  hitl_interventions: 3,
  total_cost_usd: 15.80,
  domain_breakdown: [
    { domain: "marketing", total: 54, completed: 49, failed: 5, avg_confidence: 0.91 },
    { domain: "sales", total: 35, completed: 32, failed: 3, avg_confidence: 0.87 },
  ],
  connector_setup: MOCK_CONNECTOR_SETUP,
  connector_setup_summary: {
    total: 6,
    healthy: 1,
    missing: 1,
    stale: 1,
    degraded: 1,
    auth_actions: 2,
    needs_action: 5,
    readiness: "setup_required",
  },
  connector_contracts: MOCK_CONNECTOR_CONTRACTS,
  connector_contract_summary: {
    total: 3,
    configured: 2,
    read_ready: 1,
    write_ready: 1,
    blocked: 1,
    degraded: 0,
    missing_write_scope: 1,
    write_unconfirmed: 1,
    write_confirmed: 1,
    mock_or_test_double: 0,
    readiness: "blocked",
  },
  field_mapping_status: MOCK_FIELD_MAPPING_STATUS,
  field_mapping_summary: {
    total: 6,
    unmapped: 1,
    partially_mapped: 1,
    valid: 1,
    invalid: 1,
    stale: 1,
    blocked: 1,
    needs_action: 5,
    readiness: "blocked",
    blocking: 3,
    degraded: 2,
  },
  backfill_status: MOCK_BACKFILL_STATUS,
  backfill_summary: {
    total: 3,
    not_started: 0,
    queued: 0,
    running: 0,
    completed: 1,
    partial: 0,
    failed: 1,
    blocked: 1,
    needs_action: 2,
    readiness: "blocked",
  },
  kpi_readiness: {
    status: "blocked",
    historical_status: "blocked",
    field_mapping_readiness: "blocked",
    backfill_readiness: "blocked",
    blocked_reasons: ["Opportunity revenue fields mapping is unmapped."],
    degraded_reasons: ["UTM fields mapping is partially mapped."],
    affected_kpis: ["CAC", "ROAS", "Pipeline contribution"],
    next_action_cta: "resolve_data_readiness",
  },
  workflow_activation_status: MOCK_WORKFLOW_ACTIVATION_STATUS,
  workflow_activation_summary: {
    total: 8,
    unavailable: 2,
    shadow: 1,
    promotion_blocked: 1,
    promotion_ready: 1,
    active: 1,
    degraded: 1,
    paused: 1,
    external_writes_allowed: 1,
    needs_action: 7,
    readiness: "blocked",
  },
  cmo_work_queue: MOCK_WORK_QUEUE,
  cmo_work_queue_summary: {
    total: 2,
    readiness: "blocked",
    critical_or_high: 1,
    needs_action: 2,
    top_priority_score: 1170,
    first_item_id: "cmo_wq_external_write_timeout_campaign_launch",
    next_action_cta: {
      action_key: "manual_reconcile_before_retry",
      label: "Reconcile Before Retry",
      path: "/dashboard/approvals",
    },
    by_severity: { critical: 1, high: 0, medium: 1, low: 0, info: 0 },
    by_status: { open: 1, blocked: 1, waiting: 0, resolved: 0, dismissed: 0 },
    by_category: { external_write: 1, report: 1 },
    empty_state: null,
  },
  cmo_approval_reviews: MOCK_APPROVAL_REVIEWS,
  cmo_approval_review_summary: {
    total: 1,
    readiness: "pending",
    approval_ready: 1,
    blocked: 0,
    timed_out: 0,
    unsafe_write: 0,
    missing_audit: 0,
    needs_action: 1,
    next_action_cta: {
      action_key: "review_pending_approval",
      label: "Review Approval",
      path: "/dashboard/approvals",
    },
  },
  cmo_kpi_drilldowns: MOCK_KPI_DRILLDOWNS,
  cmo_kpi_drilldown_summary: {
    total: 2,
    ready: 0,
    degraded: 1,
    blocked: 1,
    unavailable: 0,
    lineage_blocked: 0,
    readiness: "blocked",
    needs_action: 2,
    next_action_cta: {
      action_key: "fix_required_mapping",
      label: "Fix Mapping",
      path: "/dashboard/connectors",
    },
  },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderCMO() {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={["/dashboard/cmo"]}>
        <CMODashboard />
      </MemoryRouter>
    </HelmetProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CMODashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders loading state initially", () => {
    mockGet.mockReturnValue(new Promise(() => {}));
    renderCMO();
    expect(screen.getByText("CMO Dashboard")).toBeInTheDocument();
  });

  it("renders all KPI cards after data loads", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Agents")).toBeInTheDocument();
    });
    expect(screen.getByText("Total Tasks (30d)")).toBeInTheDocument();
    expect(screen.getByText("Success Rate")).toBeInTheDocument();
    expect(screen.getByText("HITL Interventions")).toBeInTheDocument();
    expect(screen.getByText("Total Cost (USD)")).toBeInTheDocument();
  });

  it("displays agent count correctly", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Agents")).toBeInTheDocument();
    });
    // Agent count of 5 rendered somewhere in the document
    expect(document.body.textContent).toContain("5");
  });

  it("displays success rate as percentage", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("91.2%")).toBeInTheDocument();
    });
  });

  it("shows Demo Data badge when demo is true", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Demo Data")).toBeInTheDocument();
    });
  });

  it("does not show Demo Data badge when demo is false", async () => {
    mockGet.mockResolvedValue({ data: { ...MOCK_CMO_DATA, demo: false } });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Agents")).toBeInTheDocument();
    });
    expect(screen.queryByText("Demo Data")).not.toBeInTheDocument();
  });

  it("renders domain breakdown when data exists", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Domain Breakdown")).toBeInTheDocument();
    });
    expect(screen.getByText("marketing")).toBeInTheDocument();
  });

  it("renders truthful CMO capability states", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("CMO Capability Status")).toBeInTheDocument();
    });

    expect(screen.getByText("Campaign Pilot")).toBeInTheDocument();
    expect(screen.getByText("Production")).toBeInTheDocument();
    expect(screen.getByText("Content Factory")).toBeInTheDocument();
    expect(screen.getAllByText("Beta")).toHaveLength(3);
    expect(screen.getAllByText("Stub")).toHaveLength(2);
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText("Demo")).toBeInTheDocument();
    expect(screen.getByText("Do not treat this dashboard as proof of end-to-end CMO agent autonomy.")).toBeInTheDocument();
  });

  it("renders marketing connector setup states and CTAs", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Marketing Connector Setup")).toBeInTheDocument();
    });

    expect(screen.getAllByText("HubSpot").length).toBeGreaterThan(0);
    expect(screen.getByText("portal-123")).toBeInTheDocument();
    expect(screen.getByText("revops@example.com")).toBeInTheDocument();
    expect(screen.getAllByText("Healthy").length).toBeGreaterThan(0);
    expect(screen.getAllByText("No Action").length).toBeGreaterThan(0);

    expect(screen.getAllByText("Google Ads").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Missing").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "Set Up" }).length).toBeGreaterThan(0);

    expect(screen.getAllByText("Google Analytics 4").length).toBeGreaterThan(0);
    expect(screen.getByText("Expired Auth")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Reconnect" }).length).toBeGreaterThan(0);

    expect(screen.getAllByText("Mailchimp").length).toBeGreaterThan(0);
    expect(screen.getByText("Insufficient Scope")).toBeInTheDocument();
    expect(screen.getByText("Missing: lists.read")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Add Scope" }).length).toBeGreaterThan(0);

    expect(screen.getByText("Ahrefs")).toBeInTheDocument();
    expect(screen.getByText("Stale Sync")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Refresh Sync" }).length).toBeGreaterThan(0);

    expect(screen.getByText("Brandwatch")).toBeInTheDocument();
    expect(screen.getAllByText("Degraded").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "Review" }).length).toBeGreaterThan(0);
  });

  it("renders marketing connector contract read/write readiness", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Marketing Connector Contracts")).toBeInTheDocument();
    });

    expect(screen.getByText("Read access, write access, retry budgets, idempotency, and external write confirmation are evaluated separately for production CMO workflows.")).toBeInTheDocument();
    expect(screen.getAllByText("Write Confirmed").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Write Unconfirmed").length).toBeGreaterThan(0);
    expect(screen.getByText("2/3 left, 1 used")).toBeInTheDocument();
    expect(screen.getAllByText("Idempotency ready").length).toBeGreaterThan(0);
    expect(screen.getByText("Missing write scopes: campaigns.write.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Add Scope" }).length).toBeGreaterThan(0);
  });

  it("renders marketing field mapping and backfill readiness states", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Marketing Data Readiness")).toBeInTheDocument();
    });

    expect(screen.getByText("CMO KPI readiness is blocked by missing mappings or incomplete backfill.")).toBeInTheDocument();
    expect(screen.getByText("Opportunity revenue fields mapping is unmapped.")).toBeInTheDocument();

    expect(screen.getByText("Lifecycle stages")).toBeInTheDocument();
    expect(screen.getByText("Valid")).toBeInTheDocument();

    expect(screen.getByText("Opportunity revenue fields")).toBeInTheDocument();
    expect(screen.getByText("Unmapped")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Map Fields" }).length).toBeGreaterThan(0);

    expect(screen.getByText("UTM fields")).toBeInTheDocument();
    expect(screen.getByText("Partially Mapped")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Complete Mapping" }).length).toBeGreaterThan(0);

    expect(screen.getByText("Currency")).toBeInTheDocument();
    expect(screen.getByText("Currency 'ZZZ' is not in the supported ISO currency allowlist.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Fix Mapping" }).length).toBeGreaterThan(0);

    expect(screen.getByText("Consent and unsubscribe fields")).toBeInTheDocument();
    expect(screen.getByText("Legal approval required before mapping consent fields.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Resolve Blocker" }).length).toBeGreaterThan(0);

    expect(screen.getByText("Historical Backfill")).toBeInTheDocument();
    expect(screen.getByText("996/1000 imported, 0 failed")).toBeInTheDocument();
    expect(screen.getByText("Vendor export failed")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Retry Backfill" }).length).toBeGreaterThan(0);
    expect(screen.getByText("Connector auth is expired and must be reauthorized.")).toBeInTheDocument();
  });

  it("renders per-workflow shadow promotion gates and write readiness", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("CMO Workflow Promotion Gates")).toBeInTheDocument();
    });

    expect(screen.getByText("Weekly Marketing Report")).toBeInTheDocument();
    expect(screen.getByText("Ready to Promote")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Promote" }).length).toBeGreaterThan(0);

    expect(screen.getByText("Campaign Launch")).toBeInTheDocument();
    expect(screen.getByText("Required Ads connector is not healthy (missing).")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Fix Connector" }).length).toBeGreaterThan(0);

    expect(screen.getByText("Daily Spend Optimization")).toBeInTheDocument();
    expect(screen.getByText("Optional Finance connector Stripe is stale.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Review Degraded" }).length).toBeGreaterThan(0);

    expect(screen.getByText("Content Pipeline")).toBeInTheDocument();
    expect(screen.getByText("Allowed")).toBeInTheDocument();
    expect(screen.getAllByText("Read-only").length).toBeGreaterThan(0);

    expect(screen.getByText("Lead Nurture")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Run Shadow QA" }).length).toBeGreaterThan(0);
    expect(screen.getByText("ABM Sprint")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Implement Agent" }).length).toBeGreaterThan(0);
    expect(screen.getByText("Brand Crisis Response")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Resume" }).length).toBeGreaterThan(0);
  });

  it("renders CMO work queue items and CTAs", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("CMO Work Queue")).toBeInTheDocument();
    });

    expect(screen.getByText("External marketing write is timeout unknown")).toBeInTheDocument();
    expect(screen.getByText("Unknown vendor write state")).toBeInTheDocument();
    expect(document.body.textContent).toContain("google_ads");
    expect(screen.getByRole("button", { name: "Reconcile Before Retry" })).toBeInTheDocument();
    expect(screen.getByText("Report quality gate needs attention: Campaign Ad Hoc")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review Report Warnings" })).toBeInTheDocument();
  });

  it("renders a non-deceptive empty CMO work queue state", async () => {
    mockGet.mockResolvedValue({
      data: {
        ...MOCK_CMO_DATA,
        cmo_work_queue: [],
        cmo_work_queue_summary: {
          total: 0,
          readiness: "ready",
          critical_or_high: 0,
          needs_action: 0,
          top_priority_score: 0,
          first_item_id: null,
          next_action_cta: null,
          by_severity: { critical: 0, high: 0, medium: 0, low: 0, info: 0 },
          by_status: { open: 0, blocked: 0, waiting: 0, resolved: 0, dismissed: 0 },
          by_category: {},
          empty_state: null,
        },
      },
    });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("CMO Work Queue")).toBeInTheDocument();
    });
    expect(
      screen.getByText(
        "No CMO work queue items are open. This does not make stub, unavailable, or demo capabilities production-ready.",
      ),
    ).toBeInTheDocument();
  });

  it("renders CMO approval review cards with safety context", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("CMO Approval Reviews")).toBeInTheDocument();
    });

    expect(screen.getByText("Q2 Pipeline Sprint")).toBeInTheDocument();
    expect(document.body.textContent).toContain("Campaign Launch / launch_campaign");
    expect(screen.getByText("Approve enabled")).toBeInTheDocument();
    expect(document.body.textContent).toContain("Daily launch budget +$1,200.");
    expect(document.body.textContent).toContain("Enterprise search audience.");
    expect(document.body.textContent).toContain("Draft campaign becomes live Google Ads launch.");
    expect(document.body.textContent).toContain("ROAS shadow runs exceeded launch threshold.");
    expect(document.body.textContent).toContain("Required connector contracts are write-safe.");
    expect(document.body.textContent).toContain("Pause campaign and restore previous budget cap.");
    expect(document.body.textContent).toContain("Allowed actions: Approve, Reject, Override, Request Changes, Pause");
    expect(screen.getByRole("button", { name: "Review Approval" })).toBeInTheDocument();
  });

  it("renders CMO KPI drill-down lineage details", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("CMO KPI Drill-Down")).toBeInTheDocument();
    });

    expect(screen.getAllByText("CAC").length).toBeGreaterThan(0);
    expect(screen.getByText("total_marketing_spend / new_customers")).toBeInTheDocument();
    expect(document.body.textContent).toContain("total_marketing_spend: 1,000");
    expect(document.body.textContent).toContain("new_customers: 10");
    expect(document.body.textContent).toContain("hubspot");
    expect(document.body.textContent).toContain("Work queue: cmo_wq_kpi_cac");
    expect(document.body.textContent).toContain("Reports: weekly_marketing_report");
    expect(screen.getAllByRole("button", { name: "Fix Mapping" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Refresh Source Data" })).toBeInTheDocument();
  });

  it("shows production connector block when demo fallback is suppressed", async () => {
    mockGet.mockResolvedValue({
      data: {
        ...MOCK_CMO_DATA,
        demo: false,
        demo_suppressed: true,
        production_data_blocked: true,
        message: "No real CMO KPI data is available for this production tenant.",
      },
    });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("No real CMO KPI data is available for this production tenant.")).toBeInTheDocument();
    });
    expect(screen.queryByText("Demo Data")).not.toBeInTheDocument();
  });

  it("shows error message when API fails", async () => {
    mockGet.mockRejectedValue(new Error("Internal Server Error"));
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Failed to load CMO KPIs")).toBeInTheDocument();
    });
  });

  it("shows 'No data available' when response data is null", async () => {
    mockGet.mockResolvedValue({ data: null });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("No data available")).toBeInTheDocument();
    });
  });

  it("handles missing/null KPI fields without crashing", async () => {
    const partialData = {
      demo: false,
      company_id: "comp-001",
      agent_count: null,
      total_tasks_30d: null,
      success_rate: null,
      hitl_interventions: null,
      total_cost_usd: null,
      domain_breakdown: [],
    };
    mockGet.mockResolvedValue({ data: partialData });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Agents")).toBeInTheDocument();
    });
    expect(screen.getByText("0.0%")).toBeInTheDocument();
  });

  it("calls /kpis/cmo endpoint on mount", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      // Codex 2026-04-22 multi-company fix: the dashboard now passes a
      // params object (empty when no company is selected) so KPI helpers
      // can filter by company_id.
      expect(mockGet).toHaveBeenCalledWith("/kpis/cmo", { params: {} });
    });
  });
});
