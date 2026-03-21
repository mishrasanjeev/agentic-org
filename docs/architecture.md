# Architecture Guide

## System Overview

AgenticOrg is an 8-layer enterprise platform that orchestrates AI agents to automate business workflows.

```mermaid
graph TB
    subgraph L8["<b>L8: Auth & Compliance</b>"]
        direction LR
        Grantex["Grantex/OAuth2"]
        JWT["JWT"]
        OPA["OPA Policy Engine"]
        WORM["WORM Audit"]
        DSAR["DSAR Handler"]
    end

    subgraph L7["<b>L7: Observability</b>"]
        direction LR
        OTel["OpenTelemetry<br/><i>7 spans</i>"]
        Prom["Prometheus<br/><i>13 metrics</i>"]
        LS["LangSmith"]
        Alert["Alerting<br/><i>11 thresholds</i>"]
    end

    subgraph L6["<b>L6: Data Layer</b>"]
        direction LR
        PG["PostgreSQL 16<br/>+ pgvector"]
        Redis["Redis 7"]
        S3["S3-compat<br/>Storage"]
        DB_Detail["18 tables &middot; RLS &middot; Partitioning &middot; HMAC"]
    end

    subgraph L5["<b>L5: Connector Layer</b><br/><i>42 adapters</i>"]
        direction LR
        Fin["Finance (10)"]
        HR["HR (8)"]
        Mkt["Marketing (9)"]
        Ops["Ops (7)"]
        Comms["Comms (8)"]
    end

    subgraph L4["<b>L4: Tool Gateway</b>"]
        direction LR
        Scope["Scope Enforcement"]
        RateLimit["Token Bucket<br/>Rate Limiting"]
        Idemp["Idempotency"]
        PII["PII Masking"]
        AuditLog["Audit Logging"]
    end

    subgraph L3["<b>L3: NEXUS Orchestrator</b>"]
        direction LR
        TaskDecomp["Task Decomposition"]
        Routing["Routing"]
        Conflict["Conflict Resolution"]
        Checkpoint["Checkpointing"]
        StateMachine["State Machine"]
        HITL_Eval["HITL Evaluation"]
    end

    subgraph L2["<b>L2: Agent Layer</b><br/><i>24 specialists</i>"]
        direction LR
        Confidence["Confidence Scoring"]
        HITL_Trigger["HITL Trigger<br/>Evaluation"]
        StructOut["Structured Output"]
        AntiHalluc["Anti-Hallucination<br/>Rules"]
    end

    subgraph L1["<b>L1: LLM Backbone</b>"]
        direction LR
        Claude["Claude Sonnet<br/><i>primary</i>"]
        GPT["GPT-4o<br/><i>fallback</i>"]
        Failover["Auto-Failover"]
        TokenTrack["Token Tracking"]
        CostLedger["Cost Ledger"]
    end

    L8 --- L7
    L7 --- L6
    L6 --- L5
    L5 --- L4
    L4 --- L3
    L3 --- L2
    L2 --- L1

    style L8 fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style L7 fill:#e8eaf6,stroke:#283593,stroke-width:2px
    style L6 fill:#e0f2f1,stroke:#00695c,stroke-width:2px
    style L5 fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style L4 fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px
    style L3 fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style L2 fill:#f1f8e9,stroke:#33691e,stroke-width:2px
    style L1 fill:#fafafa,stroke:#424242,stroke-width:2px
```

---

## Message Flow

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant API as API Gateway
    participant NEXUS as NEXUS Orchestrator
    participant Agent as Specialist Agent
    participant LLM as LLM Backbone
    participant TGW as Tool Gateway
    participant Conn as Connector
    participant HITL as HITL Queue
    participant Audit as Audit Log

    User->>API: POST /workflows/{id}/run
    API->>API: JWT validation + tenant context
    API->>NEXUS: Execute workflow

    rect rgb(230, 245, 255)
        Note over NEXUS: Task Decomposition
        NEXUS->>NEXUS: Parse intent into sub-tasks
        NEXUS->>NEXUS: Topological sort by depends_on
    end

    loop For each sub-task
        NEXUS->>Agent: Route task to specialist

        rect rgb(241, 248, 233)
            Note over Agent,LLM: Agent Reasoning
            Agent->>LLM: Send prompt + context
            LLM-->>Agent: Structured response
        end

        rect rgb(243, 229, 245)
            Note over Agent,Conn: Tool Execution
            Agent->>TGW: Request tool call
            TGW->>TGW: Scope validation
            TGW->>TGW: Rate limit check
            TGW->>TGW: Idempotency check
            TGW->>Conn: Execute connector
            Conn-->>TGW: Raw result
            TGW->>TGW: PII mask result
            TGW->>Audit: HMAC-signed audit entry
            TGW-->>Agent: Masked result
        end

        Agent->>Agent: Confidence scoring
        Agent-->>NEXUS: TaskResult

        rect rgb(255, 243, 224)
            Note over NEXUS,HITL: HITL Evaluation
            NEXUS->>NEXUS: Evaluate HITL conditions
            alt HITL triggered
                NEXUS->>HITL: Pause workflow, notify human
                HITL-->>NEXUS: Approval/rejection
            end
        end

        NEXUS->>NEXUS: Checkpoint state
    end

    NEXUS-->>API: Workflow result
    API-->>User: JSON response
```

---

## Agent Lifecycle FSM

Every agent follows a governed promotion path through these states:

```mermaid
stateDiagram-v2
    [*] --> draft: create_agent()

    draft --> shadow: start_shadow
    note right of shadow
        Read-only mode
        Compared against reference agent
        Min 100 samples required
    end note

    shadow --> review_ready: accuracy >= 95%
    shadow --> shadow_failing: accuracy < floor

    shadow_failing --> shadow: retrain / adjust
    shadow_failing --> deprecated: abandon

    review_ready --> staging: reviewer_approve
    staging --> production_ready: all 6 gates pass

    production_ready --> active: final_promote

    active --> paused: kill_switch / budget_exceeded
    paused --> active: resume

    active --> deprecated: sunset
    paused --> deprecated: sunset

    deprecated --> deleted: cleanup (after retention)
    deleted --> [*]

    state "Quality Gates" as QG {
        [*] --> OutputAccuracy
        OutputAccuracy --> ConfidenceCalibration: >= 95%
        ConfidenceCalibration --> HITLRate: r >= 0.70
        HITLRate --> HallucinationRate: within +/- 5pp
        HallucinationRate --> ToolErrorRate: 0%
        ToolErrorRate --> Latency: < 2%
        Latency --> [*]: <= 1.3x reference
    }
```

### Quality Gates Detail

| Gate | Metric | Threshold |
|------|--------|-----------|
| Output Accuracy | Shadow vs reference match | >= 95% |
| Confidence Calibration | Pearson correlation | r >= 0.70 |
| HITL Rate | Deviation from reference | within +/- 5pp |
| Hallucination Rate | Fabricated data detected | 0% |
| Tool Error Rate | Failed tool calls | < 2% |
| Latency | Compared to reference | <= 1.3x |

---

## Key Design Decisions

### 1. HITL at Orchestrator Level
Agents cannot observe, reason about, or bypass their own HITL gates. The threshold expression is evaluated by the Orchestrator after receiving the TaskResult -- never by the agent itself. This prevents prompt injection from disabling safety controls.

```mermaid
flowchart LR
    Agent["Agent<br/><i>cannot see gate</i>"] -->|TaskResult| NEXUS["NEXUS Orchestrator"]
    NEXUS -->|evaluate expression| Gate{"HITL<br/>condition?"}
    Gate -->|true| HITL["HITL Queue<br/><i>pause & notify</i>"]
    Gate -->|false| Continue["Continue<br/>Workflow"]
    HITL -->|human decision| NEXUS

    style Agent fill:#f1f8e9,stroke:#33691e
    style NEXUS fill:#e1f5fe,stroke:#01579b
    style Gate fill:#fff3e0,stroke:#e65100
    style HITL fill:#fce4ec,stroke:#c62828
    style Continue fill:#e8eaf6,stroke:#283593
```

### 2. Shadow Mode Mandatory
No agent gets write access without first proving accuracy in shadow mode. Minimum 100 samples at 95% accuracy. This is enforced by architecture, not configuration -- there is no flag to skip it in production.

### 3. Append-Only Audit
The `audit_log` table has RLS blocking all UPDATE and DELETE. Every row carries an HMAC-SHA256 signature. This is a hard system property verified by the SEC-INFRA-002 test.

### 4. Tenant Isolation at Every Layer
PostgreSQL RLS, Redis key namespacing, S3 prefix isolation, JWT tenant claim validation, LLM context isolation. A tenant breach is classified as a critical security incident.

```mermaid
flowchart TB
    subgraph "Tenant Isolation Layers"
        direction TB
        JWT_T["JWT: tenant_id claim validation"]
        API_T["API: tenant context middleware"]
        DB_T["PostgreSQL: RLS on all 18 tables"]
        Redis_T["Redis: key prefix = tenant:{id}:"]
        S3_T["S3: bucket prefix = /{tenant_id}/"]
        LLM_T["LLM: context scoped to tenant"]
    end

    Request["Incoming Request"] --> JWT_T
    JWT_T --> API_T
    API_T --> DB_T
    API_T --> Redis_T
    API_T --> S3_T
    API_T --> LLM_T

    style JWT_T fill:#fce4ec,stroke:#c62828
    style API_T fill:#e8eaf6,stroke:#283593
    style DB_T fill:#e0f2f1,stroke:#00695c
    style Redis_T fill:#fff3e0,stroke:#e65100
    style S3_T fill:#f3e5f5,stroke:#6a1b9a
    style LLM_T fill:#f1f8e9,stroke:#33691e
```

### 5. Error Taxonomy (not ad-hoc strings)
All 50 error codes have defined severity, retry policy, and escalation rules. The API always returns the ErrorEnvelope schema. This enables automated error handling and monitoring.

---

## Database Schema

18 tables organized across 6 migration files:

```mermaid
erDiagram
    TENANTS ||--o{ USERS : has
    TENANTS ||--o{ AGENTS : has
    TENANTS ||--o{ WORKFLOW_DEFINITIONS : has
    TENANTS ||--o{ CONNECTORS : has

    AGENTS ||--o{ AGENT_VERSIONS : has
    AGENTS ||--o{ AGENT_LIFECYCLE_EVENTS : tracks
    AGENTS ||--o{ AGENT_COST_LEDGER : "cost tracking"
    AGENTS ||--o{ SHADOW_COMPARISONS : "shadow results"

    AGENTS }o--o{ AGENT_TEAM_MEMBERS : "belongs to"
    AGENT_TEAMS ||--o{ AGENT_TEAM_MEMBERS : contains

    WORKFLOW_DEFINITIONS ||--o{ WORKFLOW_RUNS : triggers
    WORKFLOW_RUNS ||--o{ STEP_EXECUTIONS : contains
    STEP_EXECUTIONS ||--o{ TOOL_CALLS : invokes

    WORKFLOW_RUNS ||--o{ HITL_QUEUE : "may create"
    AGENTS ||--o{ TOOL_CALLS : performs

    TENANTS {
        uuid id PK
        string name
        string data_region
        jsonb settings
    }

    AGENTS {
        uuid id PK
        uuid tenant_id FK
        string name
        string agent_type
        string domain
        string status
        float confidence_floor
        string hitl_condition
        jsonb authorized_tools
    }

    WORKFLOW_RUNS {
        uuid id PK
        uuid workflow_id FK
        string status
        timestamp started_at
        timestamp completed_at
    }

    TOOL_CALLS {
        uuid id PK
        uuid agent_id FK
        string tool_name
        jsonb parameters
        jsonb result
        boolean pii_masked
    }

    HITL_QUEUE {
        uuid id PK
        uuid workflow_run_id FK
        string assignee_role
        string decision
        timestamp decided_at
    }

    AUDIT_LOG {
        uuid id PK
        uuid tenant_id FK
        string event_type
        jsonb payload
        string hmac_signature
        timestamp created_at
    }
```

### Table Reference

| Table | Purpose | Partitioned? |
|-------|---------|-------------|
| `tenants` | Multi-tenant root | No |
| `users` | Human users with roles | No |
| `agents` | Agent definitions (33 columns) | No |
| `workflow_definitions` | YAML workflow specs | No |
| `workflow_runs` | Execution instances | Yes (monthly) |
| `step_executions` | Per-step results | Yes (monthly) |
| `tool_calls` | Every tool invocation | Yes (monthly) |
| `hitl_queue` | Human approval items | No |
| `audit_log` | Append-only audit trail | Yes (monthly) |
| `connectors` | Registered connectors | No |
| `schema_registry` | JSON Schema templates | No |
| `documents` | Document store (pgvector) | No |
| `agent_versions` | Version history | No |
| `agent_lifecycle_events` | State transitions | No |
| `agent_teams` | Agent groupings | No |
| `agent_team_members` | Team membership | No |
| `agent_cost_ledger` | Daily cost tracking | No |
| `shadow_comparisons` | Shadow vs reference results | Yes (monthly) |

---

## Workflow Engine

The workflow engine supports 9 step types and executes via dependency graph resolution:

```mermaid
flowchart TD
    A["Parse YAML<br/>Definition"] --> B["Validate Schema<br/><i>circular dependency check</i>"]
    B --> C["Topological Sort<br/><i>by depends_on</i>"]
    C --> D{"Next Step?"}

    D -->|"agent"| E["Route to<br/>Specialist Agent"]
    D -->|"condition"| F{"Evaluate<br/>Expression"}
    D -->|"human_in_loop"| G["HITL Queue<br/><i>pause & notify</i>"]
    D -->|"parallel"| H["Spawn Parallel<br/><i>wait_for: all|any|N</i>"]
    D -->|"loop"| I["Loop Iterator<br/><i>for_each / while</i>"]
    D -->|"transform"| J["Data Transform<br/><i>jmespath</i>"]
    D -->|"notify"| K["Send<br/>Notification"]
    D -->|"sub_workflow"| L["Invoke Sub-<br/>Workflow"]
    D -->|"wait"| M["Timer /<br/>External Event"]

    E --> N["Checkpoint<br/>State"]
    F -->|true_path| N
    F -->|false_path| N
    G -->|"approved/rejected"| N
    H --> N
    I --> N
    J --> N
    K --> N
    L --> N
    M --> N

    N --> O{"More<br/>Steps?"}
    O -->|Yes| D
    O -->|No| P["Workflow<br/>Complete"]

    D -->|"No steps ready"| Q{"Timeout?"}
    Q -->|Yes| R["Workflow<br/>Failed"]
    Q -->|No| D

    style A fill:#e1f5fe,stroke:#01579b
    style B fill:#e1f5fe,stroke:#01579b
    style C fill:#e1f5fe,stroke:#01579b
    style D fill:#fff3e0,stroke:#e65100
    style G fill:#fce4ec,stroke:#c62828
    style N fill:#e0f2f1,stroke:#00695c
    style P fill:#f1f8e9,stroke:#33691e
    style R fill:#fce4ec,stroke:#c62828
```

### Step Types

1. **agent** -- Route task to a specialist agent for LLM-powered execution
2. **condition** -- Branch to `true_path` / `false_path` based on expression evaluation
3. **human_in_loop** -- Pause workflow and create HITL approval item
4. **parallel** -- Execute multiple steps concurrently with `wait_for: all|any|N`
5. **loop** -- Iterate over collections or repeat while condition holds
6. **transform** -- Apply data transformations (JMESPath expressions)
7. **notify** -- Send notifications via configured channels
8. **sub_workflow** -- Invoke another workflow definition as a nested call
9. **wait** -- Wait for timer expiry or external event

---

## Auth Flow

```mermaid
sequenceDiagram
    autonumber
    participant Platform as Platform Admin
    participant Grantex as Grantex Auth Server
    participant API as API Gateway
    participant TokenPool as Token Pool
    participant Agent as Agent
    participant TGW as Tool Gateway
    participant OPA as OPA Policy Engine

    Note over Platform,Grantex: Platform Authentication
    Platform->>Grantex: POST /oauth2/token<br/>(client_credentials)
    Grantex-->>Platform: Platform JWT<br/>(agenticorg:orchestrate scope)

    Note over Platform,API: Agent Creation
    Platform->>API: POST /agents (Bearer JWT)
    API->>API: Validate JWT + tenant claim
    API->>TokenPool: Issue scoped agent token

    rect rgb(243, 229, 245)
        Note over TokenPool: Agent Token Scoping
        TokenPool->>TokenPool: Scope = tool:{connector}:{action}
        TokenPool->>TokenPool: TTL = 1 hour (auto-rotate)
        TokenPool-->>API: Agent token issued
    end

    Note over Agent,TGW: Runtime Tool Access
    Agent->>TGW: Call tool (agent token)
    TGW->>OPA: Check policy (scope + tenant)
    OPA-->>TGW: allow / deny

    alt Allowed
        TGW->>TGW: Execute tool call
        TGW-->>Agent: Result (PII masked)
    else Denied
        TGW-->>Agent: E1007 TOOL_SCOPE_DENIED
    end

    Note over Platform,Agent: Kill Switch
    Platform->>API: POST /agents/{id}/pause
    API->>TokenPool: Revoke agent token
    TokenPool-->>Agent: Token invalidated (<30s)
```

---

## Connector Framework

All 42 connectors extend `BaseConnector` with:
- `_register_tools()` -- declares available tool functions
- `_authenticate()` -- obtains credentials via `_get_secret()`
- Circuit breaker (Redis-backed, 3-state: closed/open/half-open)
- Rate limiting per connector per tenant
- Health check endpoint

```mermaid
stateDiagram-v2
    [*] --> Closed

    Closed --> Open: failure_count >= threshold
    Closed --> Closed: success (reset counter)

    Open --> HalfOpen: timeout expires

    HalfOpen --> Closed: probe succeeds
    HalfOpen --> Open: probe fails

    note right of Closed
        Normal operation
        Requests pass through
    end note

    note right of Open
        Circuit tripped
        All requests fail fast
    end note

    note right of HalfOpen
        Sending probe request
        Deciding next state
    end note
```

### Connector Categories

```mermaid
graph LR
    subgraph Finance["Finance (10)"]
        OF[Oracle Fusion]
        SAP[SAP]
        GSTN[GSTN Portal]
        Bank[Banking AA]
        Stripe[Stripe]
        IT[Income Tax]
        MCA[MCA Portal]
        Pine[PineLabs]
        Razr[Razorpay]
        Tally[Tally]
    end

    subgraph HR_C["HR (8)"]
        DBox[Darwinbox]
        GH[Greenhouse]
        Okta[Okta SCIM]
        EPFO[EPFO Portal]
        Keka[Keka HR]
        BambooHR[BambooHR]
        Slack_H[Slack HR]
        DocuSign[DocuSign]
    end

    subgraph Marketing_C["Marketing (9)"]
        HubSpot[HubSpot]
        SF[Salesforce]
        GAds[Google Ads]
        Ahrefs[Ahrefs]
        Semrush[Semrush]
        GA[Google Analytics]
        Mailchimp[Mailchimp]
        Meta[Meta Ads]
        LinkedIn[LinkedIn]
    end

    subgraph Ops_C["Ops (7)"]
        Jira[Jira]
        ZD[Zendesk]
        SN[ServiceNow]
        PD[PagerDuty]
        Conf[Confluence]
        AWS_C[AWS]
        GCP[GCP]
    end

    subgraph Comms_C["Comms (8)"]
        SlackC[Slack]
        SG[SendGrid]
        Twilio[Twilio]
        WA[WhatsApp]
        GCS[Cloud Storage]
        GitHub_C[GitHub]
        Teams[MS Teams]
        Zoom[Zoom]
    end

    BC["BaseConnector"] --> Finance
    BC --> HR_C
    BC --> Marketing_C
    BC --> Ops_C
    BC --> Comms_C

    style BC fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style Finance fill:#fff3e0,stroke:#e65100
    style HR_C fill:#f1f8e9,stroke:#33691e
    style Marketing_C fill:#f3e5f5,stroke:#6a1b9a
    style Ops_C fill:#e8eaf6,stroke:#283593
    style Comms_C fill:#fce4ec,stroke:#c62828
```
