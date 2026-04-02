# API Reference

Base URL: `http://localhost:8000`
OpenAPI docs: `http://localhost:8000/docs`

All endpoints require authentication via Bearer JWT token or API key (except `/api/v1/health`). API keys use the `ao_sk_` prefix, are bcrypt-hashed, scoped, and revocable. Generate them from Settings > API Keys in the dashboard (admin-only).

## Request Flow

Every API request passes through a standard middleware pipeline before reaching the handler:

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant CORS as CORS Middleware
    participant Auth as Auth Middleware
    participant Tenant as Tenant Context
    participant RateLimit as Rate Limiter
    participant Route as Route Handler
    participant DB as PostgreSQL (RLS)
    participant Audit as Audit Log

    Client->>CORS: HTTP Request
    CORS->>Auth: Forward (origin validated)

    Auth->>Auth: Extract Bearer JWT
    Auth->>Auth: Validate signature + expiry
    Auth->>Auth: Check required scopes

    alt Invalid/Expired Token
        Auth-->>Client: 401 Unauthorized
    else Missing Scope
        Auth-->>Client: 403 Forbidden (E1007)
    end

    Auth->>Tenant: Inject tenant_id from JWT claims
    Tenant->>Tenant: Set PostgreSQL RLS context
    Tenant->>RateLimit: Forward with tenant context

    RateLimit->>RateLimit: Token bucket check
    alt Rate Exceeded
        RateLimit-->>Client: 429 Too Many Requests
    end

    RateLimit->>Route: Execute handler

    Route->>DB: Query (RLS-filtered by tenant)
    DB-->>Route: Results

    Route->>Audit: Log action (HMAC signed)
    Route-->>Client: JSON Response
```

---

## Authentication

```mermaid
sequenceDiagram
    participant Client
    participant Grantex as Grantex Auth Server
    participant API as AgenticOrg API

    Client->>Grantex: POST /oauth2/token<br/>(client_credentials grant)
    Note right of Client: client_id + client_secret<br/>+ requested scopes
    Grantex-->>Client: JWT access token<br/>(contains tenant_id, scopes, exp)

    Client->>API: GET /api/v1/agents<br/>Authorization: Bearer {token}
    API->>API: Validate JWT
    API-->>Client: 200 OK + response
```

```bash
# Obtain platform token
curl -X POST https://auth.yourorg.com/oauth2/token \
  -d "grant_type=client_credentials" \
  -d "client_id=$GRANTEX_CLIENT_ID" \
  -d "client_secret=$GRANTEX_CLIENT_SECRET" \
  -d "scope=agenticorg:orchestrate agenticorg:agents:read"

# Use token in requests
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/agents
```

### Scope Hierarchy

```mermaid
graph TD
    Root["agenticorg:*<br/><i>superadmin</i>"]
    Root --> Orchestrate["agenticorg:orchestrate<br/><i>run workflows</i>"]
    Root --> AgentsAll["agenticorg:agents:*"]
    Root --> WorkflowsAll["agenticorg:workflows:*"]
    Root --> ComplianceAll["agenticorg:compliance:*"]

    AgentsAll --> AgentsRead["agenticorg:agents:read"]
    AgentsAll --> AgentsWrite["agenticorg:agents:write"]
    AgentsAll --> AgentsAdmin["agenticorg:agents:admin<br/><i>promote, pause, delete</i>"]

    WorkflowsAll --> WfRead["agenticorg:workflows:read"]
    WorkflowsAll --> WfWrite["agenticorg:workflows:write"]
    WorkflowsAll --> WfRun["agenticorg:workflows:run"]

    ComplianceAll --> DSAR["agenticorg:compliance:dsar"]
    ComplianceAll --> Evidence["agenticorg:compliance:evidence"]
    ComplianceAll --> AuditRead["agenticorg:compliance:audit:read"]

    style Root fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style AgentsAll fill:#fff3e0,stroke:#e65100
    style WorkflowsAll fill:#e1f5fe,stroke:#01579b
    style ComplianceAll fill:#f3e5f5,stroke:#6a1b9a
```

### Password Reset

**Forgot Password**
```
POST /api/v1/auth/forgot-password
```
Public endpoint (no auth required). Sends a password reset email if the email is registered. Always returns 200 to prevent email enumeration. Rate-limited to 3 requests per email per hour.

**Request Body:**
```json
{
  "email": "user@company.com"
}
```

**Response:** `200 OK`
```json
{
  "status": "ok",
  "message": "If that email is registered, a reset link has been sent."
}
```

**Reset Password**
```
POST /api/v1/auth/reset-password
```
Public endpoint (no auth required). Validates the reset JWT token and sets a new password. Token is single-use (blacklisted after use) and expires in 1 hour.

**Request Body:**
```json
{
  "token": "eyJhbGciOi...",
  "password": "NewSecurePass1"
}
```

**Response:** `200 OK`
```json
{
  "status": "ok",
  "message": "Password has been reset. You can now sign in."
}
```

**Error Responses:**
| Status | Detail |
|--------|--------|
| 400 | Invalid or expired reset token |
| 400 | Password must be at least 8 characters with uppercase, lowercase, and a number |

---

## Agents

### Create Agent
```
POST /api/v1/agents
```
Creates a new agent in `shadow` status. Requires `agenticorg:agents:write` scope.

**Request Body:**
```json
{
  "name": "Invoice Validator — GST Specialist",
  "agent_type": "invoice_validator_gst",
  "domain": "finance",
  "llm_model": "claude-3-5-sonnet-20241022",
  "confidence_floor": 0.90,
  "hitl_condition": "total > 500000 OR einvoice_failed==true",
  "authorized_tools": ["oracle_fusion:read:purchase_order", "gstn_api:read:validate_gstin"],
  "initial_status": "shadow",
  "shadow_comparison_agent": "ap-processor-001",
  "shadow_min_samples": 100,
  "shadow_accuracy_floor": 0.95,
  "cost_controls": {
    "daily_token_budget": 500000,
    "monthly_cost_cap_usd": 200,
    "on_budget_exceeded": "pause_and_alert"
  }
}
```

**Response:** `201 Created`
```json
{
  "agent_id": "uuid",
  "status": "shadow",
  "token_issued": true
}
```

### Agent CRUD Flow

```mermaid
flowchart LR
    Create["POST /agents<br/><i>creates in shadow</i>"] --> Read["GET /agents/{id}<br/><i>read details</i>"]
    Read --> Update["PUT/PATCH /agents/{id}<br/><i>update config</i>"]
    Update --> Promote["POST /agents/{id}/promote<br/><i>advance lifecycle</i>"]
    Promote --> Pause["POST /agents/{id}/pause<br/><i>kill switch</i>"]
    Pause --> Resume["POST /agents/{id}/resume"]

    Create --> Clone["POST /agents/{id}/clone<br/><i>inherit scopes</i>"]
    Promote --> Rollback["POST /agents/{id}/rollback<br/><i>revert version</i>"]

    style Create fill:#f1f8e9,stroke:#33691e
    style Pause fill:#fce4ec,stroke:#c62828
    style Promote fill:#e1f5fe,stroke:#01579b
```

### Clone Agent
```
POST /api/v1/agents/{parent_id}/clone
```
Clone an existing agent with overrides. Child cannot elevate parent's scopes.

### Kill Switch
```
POST /api/v1/agents/{id}/pause
```
Immediately pauses agent, revokes token, stops accepting new tasks. Effective in <30 seconds.

---

## Connectors

### Get Connector
```
GET /api/v1/connectors/{conn_id}
```
Returns full connector details including auth type and tool functions.

### Update Connector
```
PUT /api/v1/connectors/{conn_id}
```
Update connector authentication, base URL, rate limit, or status.

**Request Body:**
```json
{
  "auth_type": "bolt_bot_token",
  "auth_config": {"bot_token": "xoxb-..."},
  "secret_ref": "gcp-secret-manager://slack-bot-token",
  "rate_limit_rpm": 200
}
```

**Response:** `200 OK` — Returns updated connector object.

**Error Responses:**
| Status | Detail |
|--------|--------|
| 404 | Connector not found |
| 409 | Connector name already exists (on create) |

### Connector Registry
```
GET /api/v1/connectors/registry
```
Returns the full connector registry with all 51 connectors, their categories, tool counts, and auth types. No write scope required.

**Response:** `200 OK`
```json
{
  "total_connectors": 51,
  "total_tools": 320,
  "categories": ["finance", "hr", "marketing", "ops", "comms"],
  "connectors": [
    {
      "name": "gstn",
      "category": "finance",
      "auth_type": "gsp_dsc",
      "tool_count": 8,
      "tools": ["validate_gstin", "file_gstr1", "file_gstr3b", "..."]
    }
  ]
}
```

### Retest Connector
```
POST /api/v1/connectors/{conn_id}/retest
```
Triggers a health check and connectivity test for the specified connector. Returns updated status (healthy/degraded/down).

**Request Body:** (optional)
```json
{
  "timeout_seconds": 10
}
```

**Response:** `200 OK`
```json
{
  "connector_id": "uuid",
  "status": "healthy",
  "latency_ms": 142,
  "last_tested": "2026-03-31T10:00:00Z"
}
```

---

## API Keys

### List API Keys
```
GET /api/v1/api-keys
```
Returns all active API keys for the current organization. Admin-only.

**Response:** `200 OK`
```json
{
  "keys": [
    {
      "id": "uuid",
      "name": "Production SDK Key",
      "prefix": "ao_sk_a1b2",
      "scopes": ["agents:read", "agents:run", "connectors:read"],
      "created_at": "2026-03-15T08:00:00Z",
      "last_used_at": "2026-03-31T09:30:00Z",
      "expires_at": null
    }
  ],
  "count": 1,
  "max_keys": 10
}
```

### Create API Key
```
POST /api/v1/api-keys
```
Generates a new API key. The full key (`ao_sk_{40 hex chars}`) is returned only once — it is bcrypt-hashed before storage. Admin-only. Maximum 10 active keys per organization.

**Request Body:**
```json
{
  "name": "My SDK Key",
  "scopes": ["agents:read", "agents:run", "connectors:read", "mcp:read", "mcp:call", "a2a:read"],
  "expires_in_days": null
}
```

**Response:** `201 Created`
```json
{
  "id": "uuid",
  "key": "ao_sk_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0",
  "name": "My SDK Key",
  "scopes": ["agents:read", "agents:run", "connectors:read", "mcp:read", "mcp:call", "a2a:read"],
  "created_at": "2026-03-31T10:00:00Z"
}
```

### Revoke API Key
```
DELETE /api/v1/api-keys/{key_id}
```
Permanently revokes an API key. Admin-only.

**Response:** `200 OK`
```json
{
  "status": "revoked",
  "key_id": "uuid"
}
```

### Available Scopes

| Scope | Description |
|-------|-------------|
| `agents:read` | List and view agent details |
| `agents:run` | Execute agent tasks |
| `connectors:read` | List connectors and tools |
| `mcp:read` | List MCP tools |
| `mcp:call` | Execute MCP tool calls |
| `a2a:read` | Access A2A agent cards |

---

## Dashboards

### CFO KPIs
```
GET /api/v1/kpis/cfo
```
Returns all CFO dashboard KPI data. Requires JWT with CFO or CEO role.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company_id` | uuid | No | Filter by company (for multi-company setups). Defaults to user's active company. |
| `period` | string | No | Time period: `today`, `this_week`, `this_month`, `this_quarter`, `this_year`. Default: `this_month`. |
| `compare` | boolean | No | Include comparison to previous period. Default: `true`. |

**Response:** `200 OK`
```json
{
  "period": "2026-03",
  "company_id": "uuid",
  "kpis": {
    "cash_runway_months": 14.2,
    "burn_rate_monthly": 2850000,
    "burn_rate_trend": [2700000, 2900000, 2850000],
    "dso_days": 42,
    "dso_previous": 45,
    "dpo_days": 38,
    "dpo_previous": 35,
    "ar_aging": {
      "current": 12500000,
      "30_days": 4200000,
      "60_days": 1800000,
      "90_days": 650000,
      "120_plus": 320000
    },
    "ap_aging": {
      "current": 8900000,
      "30_days": 3100000,
      "60_days": 980000,
      "90_days": 210000,
      "120_plus": 45000
    },
    "pnl": {
      "revenue": 68500000,
      "cogs": 41100000,
      "gross_margin": 27400000,
      "operating_expenses": 18200000,
      "ebitda": 9200000
    },
    "bank_balances": [
      {"bank": "HDFC", "account": "****1234", "balance": 18500000, "currency": "INR"},
      {"bank": "ICICI", "account": "****5678", "balance": 6200000, "currency": "INR"}
    ],
    "tax_calendar": [
      {"filing": "GSTR-3B", "due_date": "2026-04-20", "status": "pending", "gstin": "29XXXXX1234X1Z5"},
      {"filing": "TDS 26Q", "due_date": "2026-04-30", "status": "not_started"}
    ]
  },
  "updated_at": "2026-04-02T06:15:00Z"
}
```

### CMO KPIs
```
GET /api/v1/kpis/cmo
```
Returns all CMO dashboard KPI data. Requires JWT with CMO or CEO role.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company_id` | uuid | No | Filter by company. Defaults to user's active company. |
| `period` | string | No | Time period: `today`, `this_week`, `this_month`, `this_quarter`. Default: `this_month`. |
| `compare` | boolean | No | Include comparison to previous period. Default: `true`. |

**Response:** `200 OK`
```json
{
  "period": "2026-03",
  "company_id": "uuid",
  "kpis": {
    "cac": {
      "blended": 4200,
      "by_channel": {
        "google_ads": 3800,
        "meta_ads": 5100,
        "linkedin_ads": 7200,
        "organic": 1200
      },
      "previous_blended": 4500
    },
    "mqls": {
      "count": 342,
      "previous": 298,
      "conversion_to_sql_pct": 28.4
    },
    "sqls": {
      "count": 97,
      "previous": 84,
      "conversion_to_opp_pct": 45.2
    },
    "pipeline_value": {
      "total": 28500000,
      "by_stage": {
        "discovery": 8200000,
        "proposal": 10300000,
        "negotiation": 6800000,
        "closed_won": 3200000
      }
    },
    "roas_by_channel": {
      "google_ads": {"spend": 450000, "revenue": 1800000, "roas": 4.0},
      "meta_ads": {"spend": 280000, "revenue": 840000, "roas": 3.0},
      "linkedin_ads": {"spend": 180000, "revenue": 360000, "roas": 2.0}
    },
    "email_performance": {
      "open_rate_pct": 24.3,
      "ctr_pct": 3.8,
      "unsubscribe_pct": 0.12,
      "deliverability_pct": 98.7
    },
    "brand_sentiment": {
      "positive": 68,
      "neutral": 24,
      "negative": 8,
      "trend": "improving"
    },
    "content_top_pages": [
      {"path": "/blog/ai-invoice-processing-india", "views": 12400, "avg_time_sec": 245, "conversions": 18}
    ]
  },
  "updated_at": "2026-04-02T06:30:00Z"
}
```

---

## NL Query (Chat)

### Submit Query
```
POST /api/v1/chat/query
```
Submit a natural language question. The system routes it to the appropriate agent(s), executes the query, and returns the answer with agent attribution.

**Request Body:**
```json
{
  "query": "What's my cash position?",
  "company_id": "uuid",
  "context": {
    "page": "/dashboard/cfo",
    "filters": {}
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | Yes | Natural language question |
| `company_id` | uuid | No | Company context for multi-company setups |
| `context` | object | No | Page context and active filters for better routing |

**Response:** `200 OK`
```json
{
  "query_id": "uuid",
  "query": "What's my cash position?",
  "answer": "Your current cash position across all connected accounts is Rs 2,47,00,000. HDFC: Rs 1,85,00,000, ICICI: Rs 62,00,000. Cash runway at current burn rate: 14.2 months.",
  "agents_used": [
    {"agent_id": "treasury-001", "agent_name": "Treasury Agent", "confidence": 0.96}
  ],
  "data": {
    "total_cash": 24700000,
    "accounts": [
      {"bank": "HDFC", "balance": 18500000},
      {"bank": "ICICI", "balance": 6200000}
    ]
  },
  "timestamp": "2026-04-02T10:30:00Z"
}
```

**Error Responses:**
| Status | Detail |
|--------|--------|
| 400 | Query is empty or exceeds 1000 characters |
| 403 | User does not have access to the requested data domain |
| 429 | Rate limit exceeded (default: 30 queries/minute) |

### Chat History
```
GET /api/v1/chat/history
```
Returns the chat history for the current user. Paginated.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `limit` | integer | No | Number of entries to return. Default: 50. Max: 200. |
| `offset` | integer | No | Pagination offset. Default: 0. |
| `company_id` | uuid | No | Filter by company. |

**Response:** `200 OK`
```json
{
  "total": 142,
  "limit": 50,
  "offset": 0,
  "history": [
    {
      "query_id": "uuid",
      "query": "What's my cash position?",
      "answer": "Your current cash position across all connected accounts is Rs 2,47,00,000...",
      "agents_used": [{"agent_id": "treasury-001", "agent_name": "Treasury Agent"}],
      "timestamp": "2026-04-02T10:30:00Z"
    }
  ]
}
```

---

## Companies (Multi-Company)

### List Companies
```
GET /api/v1/companies
```
Returns all companies the current user has access to. Used for the company switcher UI.

**Response:** `200 OK`
```json
{
  "companies": [
    {
      "id": "uuid",
      "name": "Acme Corp Pvt Ltd",
      "gstin": "29XXXXX1234X1Z5",
      "pan": "XXXXX1234X",
      "is_active": true,
      "role": "cfo",
      "agent_count": 10,
      "connector_count": 8,
      "created_at": "2026-01-15T08:00:00Z"
    },
    {
      "id": "uuid",
      "name": "Beta Industries Ltd",
      "gstin": "27XXXXX5678X1Z3",
      "pan": "XXXXX5678X",
      "is_active": true,
      "role": "auditor",
      "agent_count": 6,
      "connector_count": 5,
      "created_at": "2026-02-20T10:00:00Z"
    }
  ],
  "count": 2,
  "active_company_id": "uuid"
}
```

### Create Company
```
POST /api/v1/companies
```
Creates a new company entity. Admin-only.

**Request Body:**
```json
{
  "name": "NewCo Pvt Ltd",
  "gstin": "29XXXXX9012X1Z7",
  "pan": "XXXXX9012X",
  "address": "123 MG Road, Bengaluru, KA 560001",
  "primary_contact_email": "finance@newco.com",
  "financial_year_start": "04-01"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Company legal name |
| `gstin` | string | No | GSTIN (15-character format) |
| `pan` | string | No | PAN (10-character format) |
| `address` | string | No | Registered address |
| `primary_contact_email` | string | No | Primary contact email |
| `financial_year_start` | string | No | Financial year start date (MM-DD). Default: `04-01` (Indian FY). |

**Response:** `201 Created`
```json
{
  "id": "uuid",
  "name": "NewCo Pvt Ltd",
  "gstin": "29XXXXX9012X1Z7",
  "is_active": true,
  "created_at": "2026-04-02T10:00:00Z"
}
```

**Error Responses:**
| Status | Detail |
|--------|--------|
| 400 | Invalid GSTIN or PAN format |
| 409 | Company with this GSTIN already exists |

### Update Company
```
PATCH /api/v1/companies/{id}
```
Update company details. Admin-only.

**Request Body:** (partial update — include only fields to change)
```json
{
  "name": "NewCo Technologies Pvt Ltd",
  "address": "456 Brigade Road, Bengaluru, KA 560025"
}
```

**Response:** `200 OK` — Returns the updated company object.

**Error Responses:**
| Status | Detail |
|--------|--------|
| 404 | Company not found |
| 409 | GSTIN conflict with existing company |

---

## Report Schedules

### List Scheduled Reports
```
GET /api/v1/report-schedules
```
Returns all scheduled reports for the current company.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company_id` | uuid | No | Filter by company. Defaults to active company. |
| `status` | string | No | Filter by status: `active`, `paused`, `all`. Default: `all`. |

**Response:** `200 OK`
```json
{
  "schedules": [
    {
      "id": "uuid",
      "name": "Daily Cash Report",
      "report_type": "cash_position",
      "schedule_cron": "0 6 * * 1-5",
      "schedule_human": "Weekdays at 6:00 AM",
      "output_format": "pdf",
      "delivery_channels": [
        {"type": "email", "target": "cfo@company.com"},
        {"type": "slack", "target": "#finance"}
      ],
      "status": "active",
      "last_run_at": "2026-04-01T06:00:00Z",
      "last_run_status": "delivered",
      "next_run_at": "2026-04-02T06:00:00Z",
      "created_at": "2026-03-15T10:00:00Z"
    }
  ],
  "count": 3
}
```

### Create Scheduled Report
```
POST /api/v1/report-schedules
```
Creates a new scheduled report.

**Request Body:**
```json
{
  "name": "Weekly P&L Report",
  "report_type": "pnl_summary",
  "schedule_cron": "0 8 * * 1",
  "output_format": "excel",
  "delivery_channels": [
    {"type": "email", "target": "cfo@company.com"},
    {"type": "email", "target": "ceo@company.com"},
    {"type": "slack", "target": "#finance"}
  ],
  "parameters": {
    "include_budget_variance": true,
    "include_department_breakdown": true,
    "comparison_period": "previous_month"
  },
  "company_id": "uuid"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Human-readable schedule name |
| `report_type` | string | Yes | Report type: `cash_position`, `pnl_summary`, `close_package`, `ar_aging`, `ap_aging`, `tax_calendar`, `marketing_weekly`, `ad_performance`, `marketing_roi` |
| `schedule_cron` | string | Yes | 5-field cron expression (minute hour day month weekday) |
| `output_format` | string | Yes | `pdf` or `excel` |
| `delivery_channels` | array | Yes | Array of delivery targets (email, slack, whatsapp) |
| `parameters` | object | No | Report-specific parameters |
| `company_id` | uuid | No | Company context. Defaults to active company. |

**Response:** `201 Created`
```json
{
  "id": "uuid",
  "name": "Weekly P&L Report",
  "schedule_cron": "0 8 * * 1",
  "schedule_human": "Mondays at 8:00 AM",
  "status": "active",
  "next_run_at": "2026-04-07T08:00:00Z",
  "created_at": "2026-04-02T10:00:00Z"
}
```

**Error Responses:**
| Status | Detail |
|--------|--------|
| 400 | Invalid cron expression |
| 400 | Invalid report_type |
| 400 | At least one delivery channel is required |

### Update Scheduled Report
```
PATCH /api/v1/report-schedules/{id}
```
Update a scheduled report. Supports partial updates. Use this to toggle status (active/paused), change schedule, or modify delivery channels.

**Request Body:** (partial update — include only fields to change)
```json
{
  "status": "paused"
}
```

Or to update the schedule and delivery:
```json
{
  "schedule_cron": "0 9 * * 1",
  "delivery_channels": [
    {"type": "email", "target": "cfo@company.com"},
    {"type": "whatsapp", "target": "+919876543210"}
  ]
}
```

**Response:** `200 OK` — Returns the updated schedule object.

**Error Responses:**
| Status | Detail |
|--------|--------|
| 404 | Schedule not found |
| 400 | Invalid cron expression |

### Run Report Now
```
POST /api/v1/report-schedules/{id}/run-now
```
Triggers an immediate run of a scheduled report, regardless of its cron schedule. Useful for testing or ad-hoc report generation.

**Request Body:** (optional)
```json
{
  "delivery_override": [
    {"type": "email", "target": "me@company.com"}
  ]
}
```

**Response:** `202 Accepted`
```json
{
  "run_id": "uuid",
  "schedule_id": "uuid",
  "status": "generating",
  "estimated_completion_seconds": 30,
  "message": "Report generation started. You will receive it at the configured delivery channels."
}
```

**Error Responses:**
| Status | Detail |
|--------|--------|
| 404 | Schedule not found |
| 409 | A run is already in progress for this schedule |
| 429 | Rate limit: maximum 5 ad-hoc runs per hour per schedule |

### Delete Scheduled Report
```
DELETE /api/v1/report-schedules/{id}
```
Permanently deletes a scheduled report. This cancels all future runs. Historical run data is retained in the audit log.

**Response:** `200 OK`
```json
{
  "status": "deleted",
  "schedule_id": "uuid",
  "message": "Schedule deleted. Historical run data is retained in the audit log."
}
```

**Error Responses:**
| Status | Detail |
|--------|--------|
| 404 | Schedule not found |

---

## Workflows

### Create Workflow
```
POST /api/v1/workflows
```

**Request Body (YAML-based definition):**
```json
{
  "name": "invoice-processing-v2",
  "version": "2.0",
  "trigger_type": "email_received",
  "trigger_config": {"filter": {"subject_contains": ["invoice", "bill"]}},
  "timeout_hours": 48,
  "definition": {
    "steps": [
      {"id": "extract", "type": "agent", "agent": "ap-processor", "action": "extract_invoice"},
      {"id": "validate", "type": "parallel", "wait_for": "all", "steps": ["validate_gstin", "check_duplicate"]},
      {"id": "match", "type": "agent", "agent": "ap-processor", "action": "three_way_match", "depends_on": ["validate"]},
      {"id": "gate", "type": "condition", "condition": "match.output.total > 500000", "true_path": "hitl", "false_path": "post"},
      {"id": "hitl", "type": "human_in_loop", "assignee_role": "cfo", "timeout_hours": 4},
      {"id": "post", "type": "agent", "agent": "ap-processor", "action": "post_journal_entry", "depends_on": ["gate"]}
    ]
  }
}
```

### Workflow Execution Example

The above invoice processing workflow executes as:

```mermaid
flowchart TD
    Trigger["Email Received<br/><i>subject contains 'invoice'</i>"] --> Extract["extract<br/><b>AP Processor</b><br/><i>extract_invoice</i>"]

    Extract --> Validate["validate<br/><b>parallel</b>"]

    subgraph Parallel["Parallel Validation"]
        GSTIN["validate_gstin"]
        Dedup["check_duplicate"]
    end

    Validate --> GSTIN
    Validate --> Dedup
    GSTIN --> Match
    Dedup --> Match

    Match["match<br/><b>AP Processor</b><br/><i>three_way_match</i>"] --> Gate{"gate<br/><i>total > 5L?</i>"}

    Gate -->|"Yes (> 5L INR)"| HITL["hitl<br/><b>CFO Approval</b><br/><i>4hr timeout</i>"]
    Gate -->|"No (<= 5L INR)"| Post

    HITL -->|approved| Post["post<br/><b>AP Processor</b><br/><i>post_journal_entry</i>"]
    HITL -->|rejected| Rejected["Workflow Rejected"]

    Post --> Done["Workflow Complete"]

    style Trigger fill:#e8eaf6,stroke:#283593
    style Extract fill:#f1f8e9,stroke:#33691e
    style Validate fill:#fff3e0,stroke:#e65100
    style Gate fill:#fff3e0,stroke:#e65100
    style HITL fill:#fce4ec,stroke:#c62828
    style Post fill:#f1f8e9,stroke:#33691e
    style Done fill:#e0f2f1,stroke:#00695c
    style Rejected fill:#fce4ec,stroke:#c62828
```

### Trigger Workflow Run
```
POST /api/v1/workflows/{id}/run
```

### Get Run Details
```
GET /api/v1/workflows/runs/{run_id}
```

---

## HITL Approvals

### Approval Flow

```mermaid
sequenceDiagram
    autonumber
    participant Agent
    participant NEXUS as NEXUS Orchestrator
    participant DB as HITL Queue (DB)
    participant WS as WebSocket Feed
    participant Human as Human Reviewer
    participant API as API Gateway

    Agent-->>NEXUS: TaskResult (low confidence)
    NEXUS->>NEXUS: Evaluate HITL condition
    NEXUS->>DB: Insert HITL item<br/>(assignee_role, context, deadline)
    NEXUS->>WS: Broadcast notification

    WS-->>Human: Real-time alert

    Human->>API: GET /approvals<br/>(list pending items)
    API-->>Human: HITL items with full context

    Human->>API: POST /approvals/{id}/decide
    Note right of Human: decision: approve/reject<br/>+ notes

    API->>DB: Update HITL item
    API->>NEXUS: Resume workflow
    NEXUS->>Agent: Continue / abort
```

### List Pending Approvals
```
GET /api/v1/approvals
```

### Submit Decision
```
POST /api/v1/approvals/{id}/decide
```
```json
{
  "decision": "approve",
  "notes": "Scope change email confirmed, approving amended PO."
}
```

---

## Compliance

### DSAR Access Request
```
POST /api/v1/dsar/access
```
```json
{"subject_email": "user@example.com", "request_type": "access"}
```

### DSAR Erasure Request
```
POST /api/v1/dsar/erase
```
30-day deadline enforced per GDPR/DPDP Act.

### Evidence Package
```
GET /api/v1/compliance/evidence-package
```
Returns SOC2 Type II evidence package with 6 sections.

### Compliance Flow

```mermaid
flowchart TD
    subgraph DSAR["DSAR Request Types"]
        Access["POST /dsar/access<br/><i>data access</i>"]
        Erase["POST /dsar/erase<br/><i>right to erasure</i>"]
        Export["POST /dsar/export<br/><i>data portability</i>"]
    end

    Access --> Scan["Scan all 18 tables<br/><i>for subject data</i>"]
    Erase --> Scan
    Export --> Scan

    Scan --> Report["Generate Report"]
    Report --> Audit_Entry["Audit Log Entry<br/><i>HMAC signed</i>"]

    subgraph Evidence["Evidence Package"]
        EP["GET /compliance/evidence-package"]
        EP --> S1["Access Controls"]
        EP --> S2["Audit Trail Integrity"]
        EP --> S3["Encryption at Rest"]
        EP --> S4["PII Masking Coverage"]
        EP --> S5["Tenant Isolation Proof"]
        EP --> S6["Incident Response Log"]
    end

    style DSAR fill:#f3e5f5,stroke:#6a1b9a
    style Evidence fill:#e0f2f1,stroke:#00695c
```

---

## Error Responses

All errors use the standard envelope:
```json
{
  "error": {
    "code": "E1007",
    "name": "TOOL_SCOPE_DENIED",
    "message": "Agent ap-processor-001 lacks scope tool:okta:write:provision_user",
    "severity": "critical",
    "retryable": false,
    "escalate": true,
    "context": {"agent_id": "ap-processor-001", "workflow_run_id": "wfr_abc123"}
  }
}
```

### Error Code Ranges

```mermaid
graph LR
    subgraph "E1xxx: Auth & Access"
        E1001["E1001 AUTH_FAILED"]
        E1007["E1007 TOOL_SCOPE_DENIED"]
    end

    subgraph "E2xxx: Validation"
        E2001["E2001 INVALID_INPUT"]
        E2010["E2010 SCHEMA_MISMATCH"]
    end

    subgraph "E3xxx: Agent Errors"
        E3001["E3001 AGENT_TIMEOUT"]
        E3010["E3010 HALLUCINATION_DETECTED"]
    end

    subgraph "E4xxx: Connector Errors"
        E4001["E4001 CONNECTOR_TIMEOUT"]
        E4005["E4005 CIRCUIT_OPEN"]
    end

    subgraph "E5xxx: System Errors"
        E5001["E5001 INTERNAL_ERROR"]
        E5005["E5005 DB_UNREACHABLE"]
    end

    style E1001 fill:#fce4ec,stroke:#c62828
    style E1007 fill:#fce4ec,stroke:#c62828
    style E2001 fill:#fff3e0,stroke:#e65100
    style E2010 fill:#fff3e0,stroke:#e65100
    style E3001 fill:#e8eaf6,stroke:#283593
    style E3010 fill:#e8eaf6,stroke:#283593
    style E4001 fill:#f3e5f5,stroke:#6a1b9a
    style E4005 fill:#f3e5f5,stroke:#6a1b9a
    style E5001 fill:#e0f2f1,stroke:#00695c
    style E5005 fill:#e0f2f1,stroke:#00695c
```

---

## WebSocket

### Live Activity Feed
```
WS /api/v1/ws/feed/{tenant_id}
```
Real-time stream of agent activity, workflow events, and HITL notifications.

```mermaid
sequenceDiagram
    participant UI as Browser UI
    participant WS as WebSocket Server
    participant NEXUS as NEXUS Orchestrator
    participant Agent as Agent Layer

    UI->>WS: Connect /ws/feed/{tenant_id}<br/>(Bearer token)
    WS->>WS: Validate JWT + tenant

    loop Real-time Events
        Agent-->>NEXUS: TaskResult / tool_call
        NEXUS-->>WS: Event published
        WS-->>UI: JSON event frame
        Note right of UI: {type, agent_id,<br/>event, timestamp}
    end

    UI->>WS: Close connection
    WS-->>UI: Connection closed
```
