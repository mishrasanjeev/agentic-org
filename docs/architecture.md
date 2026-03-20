# Architecture Guide

## System Overview

AgentFlow OS is an 8-layer enterprise platform that orchestrates AI agents to automate business workflows.

```
┌─────────────────────────────────────────────────────┐
│  L8: Auth & Compliance                              │
│  Grantex/OAuth2 · JWT · OPA · WORM Audit · DSAR    │
├─────────────────────────────────────────────────────┤
│  L7: Observability                                  │
│  OpenTelemetry (7 spans) · Prometheus (13 metrics)  │
│  LangSmith · Alerting (11 thresholds)               │
├─────────────────────────────────────────────────────┤
│  L6: Data Layer                                     │
│  PostgreSQL 16 + pgvector · Redis 7 · S3-compat     │
│  18 tables · RLS · Partitioning · HMAC audit        │
├─────────────────────────────────────────────────────┤
│  L5: Connector Layer (42 adapters)                  │
│  Finance(10) · HR(8) · Marketing(9) · Ops(7) ·     │
│  Comms(8) · Circuit breaker · Auth adapters         │
├─────────────────────────────────────────────────────┤
│  L4: Tool Gateway                                   │
│  Scope enforcement · Token bucket rate limiting      │
│  Idempotency · PII masking · Audit logging          │
├─────────────────────────────────────────────────────┤
│  L3: NEXUS Orchestrator                             │
│  Task decomposition · Routing · Conflict resolution  │
│  Checkpointing · State machine · HITL evaluation    │
├─────────────────────────────────────────────────────┤
│  L2: Agent Layer (24 specialists)                   │
│  Confidence scoring · HITL trigger evaluation        │
│  Structured output · Anti-hallucination rules       │
├─────────────────────────────────────────────────────┤
│  L1: LLM Backbone                                   │
│  Claude Sonnet (primary) · GPT-4o (fallback)        │
│  Auto-failover · Token tracking · Cost ledger       │
└─────────────────────────────────────────────────────┘
```

## Message Flow

```
User/Trigger → API → NEXUS Orchestrator
                         │
                         ├── Decompose intent into sub-tasks
                         ├── Route each task to specialist agent
                         │        │
                         │        ├── Agent reasons via LLM
                         │        ├── Agent calls tools via Tool Gateway
                         │        │        │
                         │        │        ├── Scope validation
                         │        │        ├── Rate limit check
                         │        │        ├── Idempotency check
                         │        │        ├── Execute connector
                         │        │        ├── PII mask result
                         │        │        └── Audit log (HMAC signed)
                         │        │
                         │        ├── Confidence scoring
                         │        └── Return TaskResult
                         │
                         ├── Evaluate HITL conditions
                         │   (if triggered → pause workflow, notify human)
                         │
                         ├── Checkpoint state
                         └── Continue or complete workflow
```

## Key Design Decisions

### 1. HITL at Orchestrator Level
Agents cannot observe, reason about, or bypass their own HITL gates. The threshold expression is evaluated by the Orchestrator after receiving the TaskResult — never by the agent itself. This prevents prompt injection from disabling safety controls.

### 2. Shadow Mode Mandatory
No agent gets write access without first proving accuracy in shadow mode. Minimum 100 samples at 95% accuracy. This is enforced by architecture, not configuration — there is no flag to skip it in production.

### 3. Append-Only Audit
The `audit_log` table has RLS blocking all UPDATE and DELETE. Every row carries an HMAC-SHA256 signature. This is a hard system property verified by the SEC-INFRA-002 test.

### 4. Tenant Isolation at Every Layer
PostgreSQL RLS, Redis key namespacing, S3 prefix isolation, JWT tenant claim validation, LLM context isolation. A tenant breach is classified as a critical security incident.

### 5. Error Taxonomy (not ad-hoc strings)
All 50 error codes have defined severity, retry policy, and escalation rules. The API always returns the ErrorEnvelope schema. This enables automated error handling and monitoring.

## Database Schema

18 tables organized across 6 migration files:

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

## Workflow Engine

The workflow engine supports 9 step types and executes via dependency graph resolution:

1. Parses YAML definition and validates (circular dependency detection)
2. Builds topological sort from `depends_on` relationships
3. Executes steps in order, respecting dependencies
4. Checkpoints state after every step
5. Supports HITL pause/resume, timeouts, and retry with backoff
6. Condition steps branch to `true_path` / `false_path`
7. Parallel steps execute concurrently with `wait_for: all|any|N`

## Connector Framework

All 42 connectors extend `BaseConnector` with:
- `_register_tools()` — declares available tool functions
- `_authenticate()` — obtains credentials via `_get_secret()`
- Circuit breaker (Redis-backed, 3-state: closed/open/half-open)
- Rate limiting per connector per tenant
- Health check endpoint
