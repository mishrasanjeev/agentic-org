# API Reference

Base URL: `http://localhost:8000`
OpenAPI docs: `http://localhost:8000/docs`

All endpoints require a Bearer JWT token (except `/api/v1/health`).

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
