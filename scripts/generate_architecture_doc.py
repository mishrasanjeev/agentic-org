"""Generate AgenticOrg v4.0.0 Architecture & Product Document PDF.

A comprehensive technical reference for architects, developers,
product managers, and engineering managers.

Output: docs/AgenticOrg_Architecture_v4.0.0.pdf
"""

from __future__ import annotations

import datetime
import os

from fpdf import FPDF

VERSION = "4.0.0"
DATE = datetime.datetime.now(tz=datetime.UTC).strftime("%B %d, %Y")

# Colors
NAVY = (20, 40, 80)
BLUE = (30, 100, 200)
L_BLUE = (220, 235, 255)
GREEN = (34, 139, 34)
L_GREEN = (220, 245, 220)
ORANGE = (220, 120, 20)
L_ORANGE = (255, 240, 220)
RED = (200, 40, 40)
L_RED = (255, 230, 230)
PURPLE = (100, 40, 160)
L_PURPLE = (240, 230, 255)
TEAL = (0, 128, 128)
L_TEAL = (220, 245, 245)
GRAY = (100, 100, 100)
L_GRAY = (245, 245, 250)
BLACK = (30, 30, 30)
WHITE = (255, 255, 255)
D_GREEN = (0, 100, 0)


class ArchDoc(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=22)

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 5, "AgenticOrg Architecture & Product Document", align="L")
        self.cell(0, 5, f"v{VERSION}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")

    def section(self, num, title, color=NAVY):
        self.set_font("Helvetica", "B", 15)
        self.set_text_color(*color)
        label = f"{num}. {title}" if num else title
        self.cell(0, 10, label, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*color)
        self.set_line_width(0.5)
        self.line(10, self.get_y(), 70, self.get_y())
        self.ln(4)

    def sub(self, title, color=BLACK):
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*color)
        self.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def sub2(self, title, color=GRAY):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*color)
        self.set_x(15)
        self.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def body(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*BLACK)
        self.multi_cell(190, 5.5, text)
        self.ln(2)

    def bullet(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*BLACK)
        self.set_x(15)
        self.multi_cell(185, 5.5, f"- {text}")

    def bold_bullet(self, label, text):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*BLACK)
        self.set_x(15)
        self.multi_cell(185, 5.5, f"- {label}: {text}")

    def code_block(self, text, bg=L_GRAY):
        y = self.get_y()
        lines = text.split("\n")
        h = len(lines) * 5 + 6
        if y + h > 275:
            self.add_page()
            y = self.get_y()
        self.set_fill_color(*bg)
        self.rect(12, y, 186, h, style="F")
        self.set_font("Helvetica", "", 8)
        self.set_text_color(50, 50, 50)
        self.set_xy(15, y + 3)
        for line in lines:
            self.cell(180, 5, line, new_x="LMARGIN", new_y="NEXT")
            self.set_x(15)
        self.set_y(y + h + 3)

    def info_box(self, label, text, bg=L_BLUE, border_color=BLUE):
        y = self.get_y()
        if y > 265:
            self.add_page()
            y = self.get_y()
        self.set_fill_color(*bg)
        self.set_draw_color(*border_color)
        self.rect(12, y, 186, 14, style="DF")
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*border_color)
        self.set_xy(15, y + 2)
        self.cell(25, 5, label)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*BLACK)
        self.multi_cell(155, 5, text)
        self.set_y(y + 17)

    def table_header(self, cols, widths, color=NAVY):
        self.set_fill_color(*color)
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 8)
        for i, col in enumerate(cols):
            self.cell(widths[i], 7, col, border=1, align="C", fill=True)
        self.ln()

    def table_row(self, cells, widths, fill=False):
        bg = L_GRAY if fill else WHITE
        self.set_fill_color(*bg)
        self.set_text_color(*BLACK)
        self.set_font("Helvetica", "", 8)
        for i, cell in enumerate(cells):
            self.cell(widths[i], 6, cell, border=1, fill=fill)
        self.ln()

    def ensure_space(self, needed=30):
        if self.get_y() > (297 - 22 - needed):
            self.add_page()


def build():
    pdf = ArchDoc()

    # ================================================================
    # COVER PAGE
    # ================================================================
    pdf.add_page()
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, 210, 297, style="F")

    # Top accent line
    pdf.set_fill_color(*WHITE)
    pdf.rect(15, 55, 180, 0.8, style="F")

    # Title block
    pdf.set_font("Helvetica", "B", 34)
    pdf.set_text_color(*WHITE)
    pdf.set_xy(15, 65)
    pdf.cell(180, 16, "AgenticOrg", align="C")
    pdf.set_font("Helvetica", "", 18)
    pdf.set_xy(15, 83)
    pdf.cell(180, 10, "Architecture & Product Document", align="C")
    pdf.set_font("Helvetica", "I", 13)
    pdf.set_text_color(180, 200, 255)
    pdf.set_xy(15, 97)
    pdf.cell(180, 8, f"Version {VERSION} -- Project Apex", align="C")

    # Stats row
    stats = [
        ("31", "DB Tables"),
        ("154", "API Endpoints"),
        ("63", "Connectors"),
        ("50+", "AI Agents"),
        ("51", "UI Pages"),
    ]
    sx = 15
    for num, label in stats:
        pdf.set_font("Helvetica", "B", 20)
        pdf.set_text_color(100, 180, 255)
        pdf.set_xy(sx, 125)
        pdf.cell(36, 10, num, align="C")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(180, 200, 255)
        pdf.set_xy(sx, 137)
        pdf.cell(36, 6, label, align="C")
        sx += 36

    # Date
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(180, 200, 255)
    pdf.set_xy(15, 165)
    pdf.cell(180, 7, DATE, align="C")

    # Audience
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(140, 170, 220)
    pdf.set_xy(15, 180)
    pdf.cell(180, 7, "For: Technical Architects, Developers, Product Managers, Engineering Managers", align="C")

    # License
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(140, 170, 220)
    pdf.set_xy(15, 195)
    pdf.cell(180, 6, "Open Source -- Apache 2.0 License", align="C")

    # ================================================================
    # TABLE OF CONTENTS
    # ================================================================
    pdf.add_page()
    pdf.section("", "Table of Contents")
    toc = [
        ("1", "Executive Summary"),
        ("2", "System Architecture"),
        ("3", "LangGraph Agent Runtime"),
        ("4", "Database Schema"),
        ("5", "API Reference"),
        ("6", "Agent System"),
        ("7", "Connector Framework"),
        ("8", "Workflow Engine"),
        ("9", "Grantex Scope Enforcement"),
        ("10", "Security Architecture"),
        ("11", "LLM Routing"),
        ("12", "Knowledge Base (RAG)"),
        ("13", "Voice Agents"),
        ("14", "Browser RPA"),
        ("15", "Billing System"),
        ("16", "CDC (Change Data Capture)"),
        ("17", "UI Architecture"),
        ("18", "DevOps & Deployment"),
        ("19", "Error Taxonomy"),
        ("20", "Environment Configuration"),
        ("A", "Appendix A: Dependency Matrix"),
        ("B", "Appendix B: Summary Stats"),
    ]
    for num, title in toc:
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(*BLACK)
        pdf.cell(12, 7, f"{num}.")
        pdf.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")

    # ================================================================
    # SECTION 1: EXECUTIVE SUMMARY
    # ================================================================
    pdf.add_page()
    pdf.section("1", "Executive Summary")
    pdf.body(
        "AgenticOrg is an AI Virtual Employee Platform that orchestrates 50+ LangGraph-powered "
        "agents to automate enterprise business workflows across finance, HR, marketing, operations, "
        "and back-office functions. It is fully open-source (Apache 2.0), deployable as self-hosted "
        "(Docker Compose, Kubernetes) or cloud-managed on GCP."
    )
    pdf.body(
        "The platform provides a complete enterprise automation stack: from LLM-powered reasoning "
        "and tool execution, through scope-enforced connectors to 1000+ business systems, to "
        "human-in-the-loop approval gates, voice agents, browser RPA, and RAG knowledge bases."
    )

    pdf.sub("Key Metrics")
    pdf.bold_bullet("Agents", "50+ specialist agents across 6 domains + 4 industry packs")
    pdf.bold_bullet("Connectors", "63 native connectors (340+ tools) + 1000+ via Composio (MIT)")
    pdf.bold_bullet("Workflows", "20 pre-built templates with adaptive re-planning")
    pdf.bold_bullet("API Endpoints", "154 endpoints across 34 route modules")
    pdf.bold_bullet("Database", "31 tables (PostgreSQL 16 + pgvector), partitioned, RLS-enforced")
    pdf.bold_bullet("UI", "51 pages (React 18 + TypeScript + Vite + Tailwind + shadcn/ui)")
    pdf.bold_bullet("Security", "Grantex RS256 scopes, Presidio PII redaction, WORM audit")
    pdf.bold_bullet("LLM Routing", "3-tier RouteLLM (85% cost savings), air-gapped option")
    pdf.bold_bullet("Voice", "LiveKit + Pipecat, SIP TLS, Whisper STT, Piper TTS")
    pdf.bold_bullet("Deployment", "Docker Compose, Helm/K8s, GKE Autopilot, air-gapped")
    pdf.ln(2)

    pdf.sub("Technology Stack Summary")
    stack = [
        ("Backend", "Python 3.12, FastAPI, SQLAlchemy 2.0, Celery, Redis 7"),
        ("AI/ML", "LangGraph, LangChain, RouteLLM, Presidio, RAGFlow"),
        ("Database", "PostgreSQL 16 + pgvector, Alembic migrations"),
        ("Frontend", "React 18, TypeScript, Vite, Tailwind CSS, shadcn/ui"),
        ("Auth", "Grantex (RS256 JWT), python-jose, passlib/bcrypt"),
        ("Infra", "Docker, Kubernetes/Helm, GCP (GKE, Cloud SQL, Secret Manager)"),
        ("Observability", "OpenTelemetry, Prometheus, structlog"),
        ("Voice", "LiveKit Agents, Pipecat, Whisper, Piper"),
    ]
    for label, value in stack:
        pdf.bold_bullet(label, value)

    # ================================================================
    # SECTION 2: SYSTEM ARCHITECTURE
    # ================================================================
    pdf.add_page()
    pdf.section("2", "System Architecture")
    pdf.body(
        "AgenticOrg is structured as an 8-layer architecture. Each layer has a clear responsibility "
        "boundary and communicates only with adjacent layers. This ensures separation of concerns, "
        "testability, and the ability to swap implementations."
    )

    pdf.sub("8-Layer Architecture")
    layers = [
        ("L1", "LLM Backbone", BLUE,
         "Multi-model support via RouteLLM. Tiers: Economy (Gemini Flash), Standard (Gemini Pro), "
         "Premium (Claude/GPT). Auto-failover, token tracking, cost ledger. Air-gapped: Ollama + vLLM."),
        ("L2", "Agent Layer", GREEN,
         "50+ specialist agents built on LangGraph. Confidence scoring, HITL trigger evaluation, "
         "structured output parsing, anti-hallucination rules, self-improving feedback loop."),
        ("L3", "NEXUS Orchestrator", TEAL,
         "Workflow execution engine. Task decomposition, topological routing, conflict resolution, "
         "state machine management, checkpointing, HITL evaluation at orchestrator level."),
        ("L4", "Tool Gateway", PURPLE,
         "Scope enforcement (Grantex enforce()), token bucket rate limiting, idempotency keys, "
         "PII masking (Presidio), HMAC-signed audit logging. Dual-path enforcement."),
        ("L5", "Connector Layer", ORANGE,
         "63 native connectors (340+ tools) + 1000+ via Composio. BaseConnector interface, "
         "auth adapters (OAuth2, API key, JWT, Basic), circuit breaker, secret resolution."),
        ("L6", "Data Layer", BLUE,
         "PostgreSQL 16 with pgvector for RAG embeddings. Redis 7 for caching and rate limiting. "
         "S3-compatible storage (GCS/MinIO). RLS on all tables, partitioning on hot tables."),
        ("L7", "Observability", GREEN,
         "OpenTelemetry (7 span types), Prometheus (13+ metrics), structlog structured logging, "
         "alerting (11 thresholds). Full distributed tracing across agent runs."),
        ("L8", "Auth & Compliance", RED,
         "Grantex RS256 JWT with 53 manifests, OPA policy engine, WORM append-only audit log "
         "with HMAC-SHA256 signatures, DSAR handler, 7-year retention, SOC2/ISO 27001 controls."),
    ]
    for lid, name, color, desc in layers:
        pdf.ensure_space(20)
        y = pdf.get_y()
        pdf.set_fill_color(*color)
        pdf.rect(12, y, 22, 10, style="F")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*WHITE)
        pdf.set_xy(12, y + 2)
        pdf.cell(22, 6, lid, align="C")
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*color)
        pdf.set_xy(37, y + 1)
        pdf.cell(80, 5, name)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*BLACK)
        pdf.set_xy(37, y + 7)
        pdf.multi_cell(160, 4, desc)
        pdf.ln(2)

    pdf.add_page()
    pdf.sub("Request Lifecycle")
    pdf.body(
        "Every request follows a deterministic path through the architecture layers:"
    )
    steps_lifecycle = [
        ("1", "User sends request", "HTTP POST to API Gateway (FastAPI)"),
        ("2", "CORS + Auth middleware", "JWT validation, tenant context injection, RLS set"),
        ("3", "Rate limiter", "Token bucket check (Redis), 429 if exceeded"),
        ("4", "Route handler", "Business logic, delegates to agent/workflow engine"),
        ("5", "Agent graph start", "LangGraph compiles and invokes the agent state machine"),
        ("6", "LLM call", "RouteLLM selects tier, prompt assembled with PII redaction"),
        ("7", "Tool call decision", "LLM returns tool_calls in structured output"),
        ("8", "Scope validation", "validate_scopes node calls grantex.enforce() (<1ms)"),
        ("9", "Tool execution", "Tool Gateway executes connector, masks PII in response"),
        ("10", "Evaluate + HITL", "Confidence check, HITL gate at orchestrator level"),
        ("11", "Audit + response", "HMAC-signed audit entry, JSON response to user"),
    ]
    for step_num, title, desc in steps_lifecycle:
        pdf.ensure_space(12)
        y = pdf.get_y()
        pdf.set_fill_color(*NAVY)
        pdf.rect(12, y, 14, 8, style="F")
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*WHITE)
        pdf.set_xy(12, y + 1.5)
        pdf.cell(14, 5, step_num, align="C")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*NAVY)
        pdf.set_xy(29, y + 0.5)
        pdf.cell(50, 4, title)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*GRAY)
        pdf.set_xy(29, y + 4.5)
        pdf.cell(170, 4, desc)
        pdf.set_y(y + 11)

    pdf.ln(3)
    pdf.sub("Tenant Isolation")
    pdf.body(
        "Multi-tenancy is enforced at every layer: JWT tenant_id claim validation, API tenant context "
        "middleware, PostgreSQL RLS on all 31 tables, Redis key prefix (tenant:{id}:), S3 path prefix "
        "(/{tenant_id}/), and LLM context scoped to tenant. A tenant breach is classified as a "
        "P0 security incident."
    )

    # ================================================================
    # SECTION 3: LANGGRAPH AGENT RUNTIME
    # ================================================================
    pdf.add_page()
    pdf.section("3", "LangGraph Agent Runtime")
    pdf.body(
        "Every agent in AgenticOrg runs as a LangGraph compiled state machine. The agent graph "
        "defines a deterministic execution flow with explicit scope validation, HITL gating, and "
        "audit logging at each step."
    )

    pdf.sub("AgentState Fields (13 fields)")
    state_fields = [
        ("messages", "list[BaseMessage]", "Conversation history (system + human + AI + tool)"),
        ("agent_id", "str", "UUID of the executing agent"),
        ("tenant_id", "str", "UUID of the owning tenant"),
        ("task_input", "dict", "Original task payload"),
        ("tool_calls", "list[ToolCall]", "Pending tool invocations from LLM"),
        ("tool_results", "list[ToolResult]", "Results from executed tools"),
        ("confidence", "float", "Agent confidence score (0.0 - 1.0)"),
        ("hitl_triggered", "bool", "Whether HITL gate was activated"),
        ("hitl_decision", "str | None", "approved / rejected / None"),
        ("scopes_granted", "list[str]", "Grantex scopes from JWT"),
        ("iteration_count", "int", "Current reasoning loop iteration"),
        ("max_iterations", "int", "Loop safety limit (default 10)"),
        ("final_output", "dict | None", "Structured result when complete"),
    ]
    w1, w2, w3 = 35, 35, 120
    pdf.table_header(["Field", "Type", "Description"], [w1, w2, w3])
    for i, (field, ftype, desc) in enumerate(state_fields):
        pdf.table_row([field, ftype, desc], [w1, w2, w3], fill=(i % 2 == 0))

    pdf.ln(4)
    pdf.sub("Graph Flow")
    pdf.code_block(
        "START -> reason -> [has tool_calls?]\n"
        "                    | yes                  | no\n"
        "              validate_scopes -> [OK?]     evaluate -> [HITL?] -> END\n"
        "                    | yes                         | yes\n"
        "              execute_tools -> reason (loop)    hitl_gate -> END\n"
        "                    | no (denied)\n"
        "              evaluate -> END (with scope error)"
    )

    pdf.add_page()
    pdf.sub("Execution Pipeline (10 steps)")
    pipeline = [
        ("1", "Prompt Assembly", "System prompt + persona + authorized tools + task input + conversation history"),
        ("2", "Language Injection", "If user language != en, inject translation directive into system prompt"),
        ("3", "PII Redaction", "Presidio scans input for 50+ PII types, replaces with placeholders (<AADHAAR_1>)"),
        ("4", "LLM Routing", "RouteLLM scores complexity, selects tier: Economy / Standard / Premium"),
        ("5", "LLM Call", "Structured output call to selected model with tool definitions"),
        ("6", "PII De-anonymize", "Reverse placeholder mapping to restore original values in tool args only"),
        ("7", "Content Safety", "Scan output for toxicity, PII leakage, near-duplicate detection"),
        ("8", "Explainer", "Generate plain-English 'Why?' explanation for the decision"),
        ("9", "Audit Entry", "HMAC-SHA256 signed log entry with full reasoning trace"),
        ("10", "CDC Emit", "Emit change event if agent modified external data (for downstream triggers)"),
    ]
    for step_num, title, desc in pipeline:
        pdf.ensure_space(14)
        y = pdf.get_y()
        pdf.set_fill_color(*TEAL)
        pdf.rect(12, y, 14, 9, style="F")
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*WHITE)
        pdf.set_xy(12, y + 2)
        pdf.cell(14, 5, step_num, align="C")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*TEAL)
        pdf.set_xy(29, y)
        pdf.cell(60, 5, title)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*BLACK)
        pdf.set_xy(29, y + 5)
        pdf.multi_cell(168, 4, desc)
        pdf.ln(1)

    pdf.ln(2)
    pdf.sub("Anti-Hallucination Rules")
    pdf.bullet("Tool results are injected as ToolMessage -- LLM cannot fabricate tool outputs")
    pdf.bullet("Structured output schema enforced via Pydantic model (no free-form prose)")
    pdf.bullet("Confidence scoring: if confidence < floor, HITL triggered automatically")
    pdf.bullet("Max iteration limit prevents infinite reasoning loops (default 10)")
    pdf.bullet("Shadow mode comparison against reference agent catches drift early")

    # ================================================================
    # SECTION 4: DATABASE SCHEMA
    # ================================================================
    pdf.add_page()
    pdf.section("4", "Database Schema")
    pdf.body(
        "The database uses PostgreSQL 16 with the pgvector extension for RAG embeddings. "
        "31 tables are organized into 4 groups across Alembic migrations. Row-Level Security (RLS) "
        "is enabled on all tenant-scoped tables."
    )

    pdf.sub("Table Groups")
    pdf.bold_bullet("Core (12 tables)", "tenants, users, agents, agent_versions, agent_lifecycle_events, "
                    "agent_teams, agent_team_members, agent_cost_ledger, shadow_comparisons, connectors, "
                    "schema_registry, documents")
    pdf.bold_bullet("Operational (8 tables)", "workflow_definitions, workflow_runs, step_executions, "
                    "tool_calls, hitl_queue, audit_log, cdc_events, notification_log")
    pdf.bold_bullet("Virtual Employee Extensions (3 tables)", "virtual_employees, ve_performance_metrics, "
                    "ve_amendment_log")
    pdf.bold_bullet("v4 Apex (8 tables)", "knowledge_chunks, knowledge_documents, voice_sessions, "
                    "rpa_executions, billing_subscriptions, billing_usage, industry_packs, "
                    "composio_connections")
    pdf.ln(2)

    pdf.sub("Key Tables Reference")
    tw = [40, 80, 25, 45]
    pdf.table_header(["Table", "Key Columns", "Partitioned", "Notes"], tw)
    tables = [
        ("tenants", "id, name, data_region, settings", "No", "Multi-tenant root"),
        ("users", "id, tenant_id, email, role, hashed_pw", "No", "6 roles (CEO..Auditor)"),
        ("agents", "id, tenant_id, name, type, domain, status", "No", "33 columns, lifecycle FSM"),
        ("workflow_definitions", "id, tenant_id, yaml_def, trigger_type", "No", "20 templates"),
        ("workflow_runs", "id, workflow_id, status, started_at", "Yes", "By created_at monthly"),
        ("step_executions", "id, run_id, step_name, status, output", "Yes", "By created_at monthly"),
        ("tool_calls", "id, agent_id, tool_name, params, result", "Yes", "By created_at monthly"),
        ("audit_log", "id, tenant_id, event, payload, hmac", "Yes", "WORM, 7-year retention"),
        ("hitl_queue", "id, run_id, assignee_role, decision", "No", "Approval queue"),
        ("connectors", "id, tenant_id, name, category, auth", "No", "63 native connectors"),
        ("documents", "id, tenant_id, content, embedding", "No", "pgvector, RAG store"),
        ("agent_versions", "id, agent_id, version, config_snapshot", "No", "Version history"),
        ("agent_cost_ledger", "id, agent_id, date, tokens, cost_usd", "No", "Daily cost tracking"),
        ("shadow_comparisons", "id, agent_id, reference_output, match", "Yes", "By created_at monthly"),
        ("knowledge_chunks", "id, doc_id, chunk_text, embedding", "No", "pgvector embeddings"),
        ("billing_subscriptions", "id, tenant_id, plan, stripe_id", "No", "Stripe + PineLabs"),
    ]
    for i, row in enumerate(tables):
        pdf.table_row(list(row), tw, fill=(i % 2 == 0))

    pdf.add_page()
    pdf.sub("Partitioning Strategy")
    pdf.body(
        "High-volume tables are partitioned by created_at using monthly range partitions. "
        "This enables efficient time-range queries, partition pruning, and simplified retention "
        "management (drop old partitions instead of DELETE)."
    )
    pdf.bullet("workflow_runs -- partitioned by created_at (monthly)")
    pdf.bullet("step_executions -- partitioned by created_at (monthly)")
    pdf.bullet("tool_calls -- partitioned by created_at (monthly)")
    pdf.bullet("audit_log -- partitioned by created_at (monthly), 7-year retention")
    pdf.bullet("shadow_comparisons -- partitioned by created_at (monthly)")
    pdf.ln(2)

    pdf.sub("pgvector for RAG")
    pdf.body(
        "The documents and knowledge_chunks tables use the pgvector extension for storing and "
        "querying vector embeddings. Embeddings are generated during document ingestion via "
        "RAGFlow and stored alongside chunk text. Similarity search uses cosine distance with "
        "IVFFlat indexing for sub-50ms query performance at scale."
    )

    pdf.sub("Entity Relationships")
    pdf.body(
        "Key relationships: tenants ->> users, agents, workflow_definitions, connectors. "
        "agents ->> agent_versions, agent_lifecycle_events, agent_cost_ledger, shadow_comparisons. "
        "workflow_definitions ->> workflow_runs ->> step_executions ->> tool_calls. "
        "workflow_runs ->> hitl_queue. All foreign keys cascade on tenant delete for DSAR compliance."
    )

    # ================================================================
    # SECTION 5: API REFERENCE
    # ================================================================
    pdf.add_page()
    pdf.section("5", "API Reference")
    pdf.body(
        "The API exposes 154 endpoints across 34 FastAPI route modules. All endpoints (except /health) "
        "require authentication via Bearer JWT or API key (ao_sk_ prefix). The OpenAPI spec is "
        "auto-generated at /docs."
    )

    pdf.sub("Route Modules (34)")
    rw = [45, 15, 130]
    pdf.table_header(["Module", "Count", "Purpose"], rw)
    routes = [
        ("agents", "12", "CRUD, promote, pause, resume, clone, rollback, run"),
        ("workflows", "10", "CRUD, run, generate (NL), templates, triggers"),
        ("connectors", "8", "Registry, CRUD, retest, Composio bridge"),
        ("auth", "8", "Login, signup, forgot/reset password, Google OAuth, refresh"),
        ("billing", "7", "Plans, subscribe, cancel, usage, invoices, webhooks"),
        ("knowledge", "6", "Upload, search, list, delete, stats, reindex"),
        ("audit", "5", "List, export, verify HMAC, retention, DSAR"),
        ("kpis", "5", "CFO, CMO, ABM, ops, custom dashboards"),
        ("approvals", "5", "List, approve, reject, reassign, bulk"),
        ("api_keys", "4", "List, create, revoke, rotate"),
        ("health", "3", "Liveness, readiness, deep health check"),
        ("agent_teams", "4", "Create team, add/remove members, list"),
        ("packs", "4", "List, install, uninstall, status"),
        ("compliance", "4", "DSAR export, evidence, controls, SOC2 report"),
        ("cdc_webhooks", "4", "Register, list, test, delete"),
        ("config", "3", "Get, update, reset tenant config"),
        ("push", "3", "Subscribe, send, unsubscribe (web push)"),
        ("schemas", "3", "List, get, validate JSON schemas"),
        ("chat", "3", "Send message, history, clear"),
        ("sop", "3", "List, get, generate from workflow"),
        ("sales", "3", "Pipeline, forecast, activities"),
        ("webhooks", "3", "Register, list, delete outbound webhooks"),
        ("a2a", "3", "Agent card, send task, receive task"),
        ("mcp", "3", "List tools, call tool, MCP server config"),
        ("abm", "3", "Target accounts, intent scores, campaigns"),
        ("evals", "3", "Create eval, run eval, results"),
        ("bridge", "2", "A2A/MCP bridge endpoints"),
        ("report_schedules", "3", "Create, list, delete scheduled reports"),
        ("demo", "2", "Seed demo data, reset demo"),
        ("org", "3", "Org chart, departments, hierarchy"),
        ("companies", "3", "List, create, switch active company"),
        ("prompt_templates", "3", "List, get, create prompt templates"),
        ("content_safety", "2", "Scan text, get policy"),
        ("misc", "7", "Static files, sitemap, llms.txt, robots.txt, health redirects"),
    ]
    for i, row in enumerate(routes):
        pdf.table_row(list(row), rw, fill=(i % 2 == 0))

    pdf.add_page()
    pdf.sub("Authentication")
    pdf.body(
        "The API supports three authentication methods:"
    )
    pdf.bold_bullet("Grantex RS256 JWT (primary)", "OAuth2 client_credentials grant. JWT contains tenant_id, "
                    "scopes, exp. Verified offline via cached JWKS. Used for agent-to-agent and service auth.")
    pdf.bold_bullet("HS256 JWT (legacy)", "Signed with shared SECRET_KEY. Used for user login sessions. "
                    "Will be migrated to RS256 in v5.0.")
    pdf.bold_bullet("API Keys (ao_sk_)", "40 hex chars, bcrypt-hashed at rest. Scoped, revocable, max 10 per org. "
                    "Used for SDK/CLI/external integrations.")
    pdf.ln(2)

    pdf.sub("Rate Limiting")
    pdf.body(
        "Token bucket algorithm via Redis. Default: 100 req/min per tenant, 10 req/min per API key. "
        "Configurable per plan (Free: 20/min, Pro: 100/min, Enterprise: 500/min). Returns 429 with "
        "Retry-After header when exceeded."
    )

    pdf.sub("Error Response Format")
    pdf.code_block(
        '{\n'
        '  "error": {\n'
        '    "code": "E1003",\n'
        '    "name": "TOOL_TIMEOUT",\n'
        '    "message": "Tool execution timed out after 30s",\n'
        '    "severity": "warning",\n'
        '    "retryable": true,\n'
        '    "request_id": "uuid"\n'
        '  }\n'
        '}'
    )

    pdf.sub("Common HTTP Status Codes")
    codes = [
        ("200", "Success"),
        ("201", "Created (agent, workflow, connector, etc.)"),
        ("400", "Validation error (bad request body)"),
        ("401", "Unauthorized (missing or invalid token)"),
        ("403", "Forbidden (insufficient scopes, E1007)"),
        ("404", "Resource not found"),
        ("409", "Conflict (duplicate name, idempotency)"),
        ("429", "Rate limit exceeded"),
        ("500", "Internal server error"),
    ]
    for code, desc in codes:
        pdf.bold_bullet(code, desc)

    # ================================================================
    # SECTION 6: AGENT SYSTEM
    # ================================================================
    pdf.add_page()
    pdf.section("6", "Agent System")
    pdf.body(
        "AgenticOrg ships with 50+ specialist agents organized across 6 domains, plus industry-specific "
        "packs. Each agent is a LangGraph state machine with a unique persona, authorized tool set, "
        "confidence floor, and HITL condition expression."
    )

    pdf.sub("Agents by Domain")
    aw = [30, 15, 145]
    pdf.table_header(["Domain", "Count", "Agent Types"], aw)
    agent_domains = [
        ("Finance", "6", "AP Processor, AR Collector, Tax Advisor (GST), Expense Auditor, Treasury, Payroll"),
        ("HR", "6", "Recruiter, Onboarding Coordinator, Leave Manager, Payroll Processor, Compliance, Exit"),
        ("Marketing", "5", "Campaign Manager, SEO Analyst, Content Writer, Social Media, Lead Scorer"),
        ("Operations", "6", "IT Service Desk, Procurement, Vendor Manager, Inventory, Logistics, QA"),
        ("Back-office", "3", "Document Classifier, Data Entry, Report Generator"),
        ("Voice", "2", "Customer Support Voice, IVR Navigator"),
    ]
    for i, row in enumerate(agent_domains):
        pdf.table_row(list(row), aw, fill=(i % 2 == 0))

    pdf.ln(2)
    pdf.sub("Industry Packs (4)")
    pdf.bold_bullet("Healthcare", "Patient intake, claims processing, appointment scheduling, medical records")
    pdf.bold_bullet("Legal", "Contract review, case research, document drafting, compliance checking")
    pdf.bold_bullet("Insurance", "Underwriting, claims adjudication, policy renewal, fraud detection")
    pdf.bold_bullet("Manufacturing", "Production planning, quality inspection, supply chain, maintenance")
    pdf.ln(2)

    pdf.sub("Agent Lifecycle FSM")
    pdf.body(
        "Every agent follows a governed promotion path through these states:"
    )
    lifecycle_states = [
        ("draft", "Initial creation, configuration not yet complete"),
        ("shadow", "Read-only observation mode, compared against reference agent"),
        ("review_ready", "Shadow accuracy >= 95%, awaiting human review"),
        ("staging", "Reviewer approved, running all 6 quality gates"),
        ("active", "Live production agent with full tool access"),
        ("paused", "Kill switch activated or budget exceeded, token revoked"),
        ("retired", "Sunset, no longer accepting tasks"),
    ]
    lw = [30, 160]
    pdf.table_header(["State", "Description"], lw)
    for i, row in enumerate(lifecycle_states):
        pdf.table_row(list(row), lw, fill=(i % 2 == 0))

    pdf.add_page()
    pdf.sub("Shadow Mode Quality Gates (6 gates)")
    pdf.body(
        "All gates must pass before an agent can be promoted from shadow to active. "
        "Minimum 100 shadow samples required."
    )
    gw = [45, 50, 40, 55]
    pdf.table_header(["Gate", "Metric", "Threshold", "Rationale"], gw)
    gates = [
        ("Output Accuracy", "Shadow vs reference", ">= 95%", "Core correctness"),
        ("Confidence Calib.", "Pearson correlation", "r >= 0.70", "Calibrated scoring"),
        ("HITL Rate", "Deviation from ref", "+/- 5pp", "Predictable escalation"),
        ("Hallucination", "Fabricated data", "0%", "Zero tolerance"),
        ("Tool Error Rate", "Failed tool calls", "< 2%", "Reliable execution"),
        ("Latency", "vs reference agent", "<= 1.3x", "Performance budget"),
    ]
    for i, row in enumerate(gates):
        pdf.table_row(list(row), gw, fill=(i % 2 == 0))

    pdf.ln(3)
    pdf.sub("Self-Improving Feedback Loop")
    pdf.body(
        "When a human provides feedback (thumbs up/down + optional amendment) on an agent run, "
        "the system records the amendment in the ve_amendment_log table. After accumulating "
        "sufficient amendments, the system suggests prompt improvements that can be applied or "
        "dismissed by an admin. This creates a continuous improvement cycle without unsupervised "
        "prompt modification."
    )
    pdf.bullet("User submits amendment (corrected output)")
    pdf.bullet("Amendment stored with original output + reasoning trace")
    pdf.bullet("Periodic batch analysis identifies systematic errors")
    pdf.bullet("System generates prompt patch suggestion")
    pdf.bullet("Admin reviews and applies or dismisses the suggestion")
    pdf.bullet("Agent version incremented, old version preserved for rollback")

    pdf.ln(3)
    pdf.sub("Confidence Scoring and HITL Triggers")
    pdf.body(
        "Each agent has a confidence_floor (default 0.88). After every reasoning step, the agent "
        "produces a confidence score. If confidence < floor, execution is paused and routed to "
        "the HITL queue. Additionally, agents can have custom HITL condition expressions that "
        "trigger regardless of confidence (e.g., 'amount > 500000 OR vendor_new == true')."
    )
    pdf.body(
        "HITL conditions are evaluated by the NEXUS Orchestrator, NOT by the agent itself. "
        "This prevents prompt injection from disabling safety controls."
    )

    # ================================================================
    # SECTION 7: CONNECTOR FRAMEWORK
    # ================================================================
    pdf.add_page()
    pdf.section("7", "Connector Framework")
    pdf.body(
        "The connector layer provides a unified interface for agents to interact with external systems. "
        "63 native connectors expose 340+ tools, and Composio adds 1000+ additional integrations."
    )

    pdf.sub("Native Connectors by Category (63)")
    cw = [30, 12, 148]
    pdf.table_header(["Category", "Count", "Connectors"], cw)
    connectors = [
        ("Finance", "14", "Oracle Fusion, SAP, Tally, QuickBooks, Zoho Books, NetSuite, Stripe, "
         "PineLabs Plural, GSTN, Income Tax India, Banking AA, AA Consent, GSTN Sandbox, GST E-Invoice"),
        ("HR", "8", "Darwinbox, Keka, EPFO, Greenhouse, LinkedIn Talent, Okta, Zoom, DocuSign"),
        ("Marketing", "16", "Salesforce, HubSpot, Mailchimp, Google Ads, Meta Ads, LinkedIn Ads, "
         "GA4, Mixpanel, Buffer, Ahrefs, Bombora, G2, TrustRadius, Brandwatch, MoEngage, WordPress"),
        ("Operations", "7", "Jira, Confluence, ServiceNow, Zendesk, PagerDuty, Sanctions API, MCA Portal"),
        ("Comms", "11", "Slack, Gmail, Google Calendar, SendGrid, Twilio, WhatsApp, Twitter, YouTube, "
         "GitHub, S3, LangSmith"),
        ("Microsoft", "1", "Teams Bot"),
        ("Composio", "3", "Composio Adapter, Auth Bridge, Discovery (1000+ tools)"),
        ("Framework", "3", "BaseConnector, Auth Adapters, Circuit Breaker"),
    ]
    for i, row in enumerate(connectors):
        pdf.table_row(list(row), cw, fill=(i % 2 == 0))

    pdf.ln(3)
    pdf.sub("BaseConnector Interface")
    pdf.code_block(
        "class BaseConnector(ABC):\n"
        "    @abstractmethod\n"
        "    def _register_tools(self) -> list[Tool]: ...\n"
        "    @abstractmethod\n"
        "    async def connect(self, config: dict) -> bool: ...\n"
        "    @abstractmethod\n"
        "    async def execute_tool(self, name: str, params: dict) -> dict: ...\n"
        "    @abstractmethod\n"
        "    async def health_check(self) -> HealthStatus: ..."
    )

    pdf.ln(2)
    pdf.sub("Auth Adapters")
    pdf.body(
        "Each connector declares its auth type. The framework provides 4 adapter implementations:"
    )
    pdf.bold_bullet("OAuth2Adapter", "Authorization code + refresh token flow (Salesforce, HubSpot, Google)")
    pdf.bold_bullet("APIKeyAdapter", "Static API key header injection (SendGrid, Ahrefs, PagerDuty)")
    pdf.bold_bullet("JWTAdapter", "Service account JWT generation (Google APIs, Grantex)")
    pdf.bold_bullet("BasicAuthAdapter", "Username:password base64 encoding (legacy systems)")
    pdf.ln(2)

    pdf.sub("Secret Resolution Chain")
    pdf.body(
        "Connector credentials are resolved in priority order: "
        "1) Explicit config (in-memory) -> 2) Environment variable -> 3) GCP Secret Manager. "
        "Secrets are cached in-memory for 5 minutes with Fernet encryption at rest."
    )

    pdf.add_page()
    pdf.sub("Composio Integration (1000+ tools)")
    pdf.body(
        "Composio (MIT, 27.6K stars) is integrated as a connector expansion layer. The "
        "ComposioConnectorAdapter implements BaseConnector and auto-discovers available tools "
        "at startup. Native connectors take priority over Composio for the same app."
    )
    pdf.bullet("Tools registered with composio: prefix (e.g., composio:notion:create_page)")
    pdf.bullet("Auth mapped to existing adapter system (OAuth2, API key)")
    pdf.bullet("Grantex manifests auto-generated based on action type (read/write/admin)")
    pdf.bullet("Composio API key stored encrypted in GCP Secret Manager")
    pdf.bullet("UI shows Composio connectors in separate 'Marketplace' tab with search")

    # ================================================================
    # SECTION 8: WORKFLOW ENGINE
    # ================================================================
    pdf.add_page()
    pdf.section("8", "Workflow Engine")
    pdf.body(
        "The workflow engine executes multi-step business processes by orchestrating agents, "
        "conditions, approvals, and external events. Workflows are defined as YAML templates "
        "and resolved via topological sort of the dependency graph."
    )

    pdf.sub("20 Pre-Built Templates")
    tw2 = [10, 60, 120]
    pdf.table_header(["#", "Template", "Domain / Description"], tw2)
    templates = [
        ("1", "Invoice-to-Pay", "Finance: OCR -> GSTIN validate -> 3-way match -> approval -> pay"),
        ("2", "Month-End Close", "Finance: Reconciliation -> accruals -> reports -> CFO sign-off"),
        ("3", "Expense Reimbursement", "Finance: Submit -> policy check -> manager approval -> payment"),
        ("4", "Vendor Onboarding", "Finance: KYC -> sanctions check -> credit check -> setup"),
        ("5", "Tax Filing (GST)", "Finance: Collect data -> compute -> validate -> file GSTR"),
        ("6", "Employee Onboarding", "HR: Offer letter -> docs -> Darwinbox setup -> IT provisioning"),
        ("7", "Leave Management", "HR: Request -> policy check -> manager approval -> update HRMS"),
        ("8", "Recruitment Pipeline", "HR: Source -> screen -> interview schedule -> offer"),
        ("9", "Exit Process", "HR: Resignation -> clearance -> final settlement -> offboarding"),
        ("10", "Campaign Launch", "Marketing: Brief -> content -> approval -> multi-channel deploy"),
        ("11", "Lead Nurture", "Marketing: Score lead -> segment -> drip email -> sales handoff"),
        ("12", "ABM Campaign", "Marketing: Account select -> intent score -> personalize -> launch"),
        ("13", "Content Publishing", "Marketing: Draft -> review -> SEO optimize -> publish"),
        ("14", "IT Incident Response", "Ops: Alert -> classify -> assign -> resolve -> postmortem"),
        ("15", "Procurement", "Ops: Requisition -> RFQ -> vendor select -> PO -> receipt"),
        ("16", "Compliance Review", "Ops: Gather evidence -> check controls -> report -> remediate"),
        ("17", "Customer Support", "Ops: Ticket -> classify -> agent resolve -> escalate if needed"),
        ("18", "Contract Review", "Legal: Upload -> extract clauses -> risk score -> flag issues"),
        ("19", "Insurance Claim", "Insurance: Submit -> verify -> adjudicate -> settle"),
        ("20", "Patient Intake", "Healthcare: Registration -> medical history -> scheduling"),
    ]
    for i, row in enumerate(templates):
        pdf.table_row(list(row), tw2, fill=(i % 2 == 0))

    pdf.add_page()
    pdf.sub("Step Types (7)")
    stw = [35, 155]
    pdf.table_header(["Step Type", "Description"], stw)
    step_types = [
        ("agent", "Route task to a specialist agent for LLM-powered execution"),
        ("condition", "Branch to true_path / false_path based on expression evaluation"),
        ("wait", "Pause for a specified duration (e.g., wait 24h before follow-up)"),
        ("wait_for_event", "Pause until an external event arrives (webhook, CDC)"),
        ("approval", "Create HITL approval item, pause until human decides"),
        ("parallel", "Execute multiple steps concurrently (wait_for: all | any | N)"),
        ("collaboration", "Multi-agent collaboration with shared context"),
    ]
    for i, row in enumerate(step_types):
        pdf.table_row(list(row), stw, fill=(i % 2 == 0))

    pdf.ln(3)
    pdf.sub("Trigger Types (5)")
    ttw = [35, 155]
    pdf.table_header(["Trigger", "Description"], ttw)
    triggers = [
        ("manual", "User clicks 'Run' in UI or calls POST /workflows/{id}/run"),
        ("schedule", "Cron expression (e.g., '0 9 * * 1' for Monday 9 AM)"),
        ("webhook", "External system sends HTTP POST to registered webhook URL"),
        ("event_based", "Internal event (agent completed, approval received, etc.)"),
        ("cdc", "Change Data Capture event from connected system"),
    ]
    for i, row in enumerate(triggers):
        pdf.table_row(list(row), ttw, fill=(i % 2 == 0))

    pdf.ln(3)
    pdf.sub("Adaptive Re-planning")
    pdf.body(
        "When a workflow step fails (tool error, timeout, connector down), the engine can "
        "automatically re-plan the remaining steps. For example, if PineLabs payment fails, "
        "the engine routes to NEFT/RTGS instead. Re-planning uses the LLM to generate "
        "alternative step sequences given the current state and available tools."
    )

    pdf.sub("State Management and Checkpointing")
    pdf.body(
        "Workflow state is checkpointed to PostgreSQL after every step execution via "
        "langgraph-checkpoint-postgres. This enables: resume after crash, audit trail of "
        "every intermediate state, parallel branch synchronization, and long-running workflow "
        "support (workflows can run for days with wait/wait_for_event steps)."
    )

    # ================================================================
    # SECTION 9: GRANTEX SCOPE ENFORCEMENT
    # ================================================================
    pdf.add_page()
    pdf.section("9", "Grantex Scope Enforcement")
    pdf.body(
        "Grantex is the authorization framework that enforces fine-grained permissions on every "
        "tool call. It uses RS256 JWTs with offline verification and 53 pre-built connector manifests."
    )

    pdf.sub("Permission Hierarchy")
    pdf.code_block(
        "admin > delete > write > read\n"
        "\n"
        "A higher permission covers all lower ones.\n"
        "Example: An agent with 'write' scope can also 'read'.\n"
        "Example: An agent with 'read' scope CANNOT 'write' or 'delete'."
    )

    pdf.sub("53 Pre-Built Manifests")
    pdf.body(
        "Each connector has a Grantex manifest that maps every tool to its required permission level. "
        "Example: salesforce:read:get_contact requires READ, salesforce:write:create_lead requires WRITE, "
        "salesforce:admin:delete_account requires ADMIN."
    )
    pdf.body(
        "Composio tools get auto-generated manifests based on their action type. Read actions map to "
        "READ scope, create/update actions to WRITE, delete actions to DELETE."
    )

    pdf.sub("validate_scopes Graph Node")
    pdf.body(
        "The validate_scopes node in the LangGraph agent graph calls grantex.enforce() for every "
        "pending tool call before execution. This is a mandatory graph node -- it cannot be skipped "
        "or bypassed by prompt injection."
    )
    pdf.code_block(
        "def validate_tool_scopes(state: AgentState) -> AgentState:\n"
        "    client = get_grantex_client()  # lazy singleton, 53 manifests loaded\n"
        "    for tool_call in state['tool_calls']:\n"
        "        result = client.enforce(\n"
        "            token=state['jwt_token'],\n"
        "            resource=tool_call.connector,\n"
        "            action=tool_call.name,\n"
        "        )\n"
        "        if not result.allowed:\n"
        "            # Block tool call, log denial, set error in state\n"
        "            ..."
    )

    pdf.ln(2)
    pdf.sub("Offline JWT Verification (<1ms)")
    pdf.body(
        "grantex.enforce() verifies JWTs offline using cached JWKS (JSON Web Key Set). "
        "The JWKS is fetched once at startup and refreshed every 15 minutes. This eliminates "
        "network round-trips on the hot path, achieving <1ms enforcement latency."
    )

    pdf.sub("Tool Gateway Dual-Path Enforcement")
    pdf.body(
        "Scopes are enforced at two points: (1) the validate_scopes LangGraph node for agent-initiated "
        "tool calls, and (2) the Tool Gateway for API-direct tool calls. This ensures that even "
        "if someone bypasses the agent graph and calls a tool directly via API, scopes are still "
        "enforced."
    )

    vw = [50, 30, 110]
    pdf.table_header(["Method", "Latency", "When Used"], vw)
    pdf.table_row(["grantex.enforce()", "<1ms (cached)", "Every tool call (hot path)"], vw, fill=True)
    pdf.table_row(["verify_grant_scopes()", "~300ms (HTTP)", "Initial auth only (middleware)"], vw)

    # ================================================================
    # SECTION 10: SECURITY ARCHITECTURE
    # ================================================================
    pdf.add_page()
    pdf.section("10", "Security Architecture")
    pdf.body(
        "Security is built into every layer of the platform, not bolted on. This section covers "
        "authentication, PII protection, content safety, audit, and compliance controls."
    )

    pdf.sub("Authentication Stack")
    pdf.bold_bullet("Primary: Grantex RS256 JWT", "OAuth2 client_credentials, offline JWKS verification, "
                    "scoped tokens with tenant_id claim")
    pdf.bold_bullet("Legacy: HS256 JWT", "Shared secret (SECRET_KEY), used for user login sessions, "
                    "to be deprecated in v5.0")
    pdf.bold_bullet("API Keys", "ao_sk_ prefix, 40 hex chars, bcrypt-hashed at rest, scoped, "
                    "revocable, max 10 per org")
    pdf.bold_bullet("Google OAuth", "Optional SSO via Google Workspace for user login")
    pdf.ln(2)

    pdf.sub("PII Redaction (Microsoft Presidio)")
    pdf.body(
        "All user input is scanned by Presidio BEFORE it reaches the LLM. The system uses "
        "50+ built-in recognizers plus custom India-specific recognizers:"
    )
    pdf.bullet("Aadhaar numbers (12-digit with Verhoeff checksum)")
    pdf.bullet("PAN numbers (ABCDE1234F format)")
    pdf.bullet("GSTIN (15-char with state code + checksum)")
    pdf.bullet("UPI IDs (user@bankcode format)")
    pdf.bullet("Indian phone numbers (+91 10-digit)")
    pdf.bullet("Plus all standard types: email, SSN, credit card, IBAN, etc.")
    pdf.ln(1)
    pdf.body(
        "PII is replaced with typed placeholders (<AADHAAR_1>, <PAN_2>) before the LLM call. "
        "After the LLM responds, placeholders in tool arguments are de-anonymized. "
        "Placeholders in the final output are preserved (the user sees masked values)."
    )

    pdf.sub("Content Safety")
    pdf.body(
        "All LLM outputs are checked by a 3-layer content safety pipeline before delivery:"
    )
    pdf.bold_bullet("PII Leakage Detection", "Scans output for any PII that survived redaction")
    pdf.bold_bullet("Toxicity Filter", "Detects harmful, offensive, or inappropriate content")
    pdf.bold_bullet("Duplicate Detection", "Near-duplicate check against recent outputs to prevent loops")

    pdf.add_page()
    pdf.sub("Audit Log (WORM)")
    pdf.body(
        "Every significant action is logged in the audit_log table with these properties:"
    )
    pdf.bullet("Append-only: PostgreSQL RLS blocks all UPDATE and DELETE on audit_log")
    pdf.bullet("HMAC-SHA256 signed: Every row carries a cryptographic signature")
    pdf.bullet("7-year retention: Partitioned by month, retention enforced via partition drop policy")
    pdf.bullet("Exportable: CSV/JSON export for compliance auditors")
    pdf.bullet("Verifiable: verify_hmac endpoint confirms log integrity")
    pdf.ln(2)

    pdf.sub("SOC2 / ISO 27001 Control Framework (10 controls)")
    soc_w = [20, 45, 125]
    pdf.table_header(["ID", "Control", "Implementation"], soc_w)
    soc_controls = [
        ("SEC-01", "Access Control", "Grantex RS256 scopes, RBAC (6 roles), API key scoping"),
        ("SEC-02", "Data Encryption", "TLS 1.3 in transit, Fernet at rest, GCP CMEK for storage"),
        ("SEC-03", "Audit Logging", "WORM audit_log, HMAC-SHA256, 7-year retention"),
        ("SEC-04", "PII Protection", "Presidio pre-LLM redaction, 50+ recognizers"),
        ("SEC-05", "Network Security", "VPC, private subnets, Cloud NAT, no public DB"),
        ("SEC-06", "Incident Response", "P0-P3 classification, auto-escalation, postmortem"),
        ("SEC-07", "Change Management", "Agent lifecycle FSM, shadow mode mandatory"),
        ("SEC-08", "Data Retention", "Configurable retention, DSAR handler, cascade delete"),
        ("SEC-09", "Vulnerability Mgmt", "Bandit (SAST), dependency audit, CVE scanning in CI"),
        ("SEC-10", "Business Continuity", "Multi-zone GKE, checkpoint recovery, failover LLM"),
    ]
    for i, row in enumerate(soc_controls):
        pdf.table_row(list(row), soc_w, fill=(i % 2 == 0))

    pdf.ln(3)
    pdf.sub("Voice Security (SIP TLS)")
    pdf.body(
        "All SIP connections for voice agents require TLS encryption. Unencrypted SIP is rejected. "
        "Voice recordings (if enabled) are encrypted at rest with Fernet and stored in GCS with "
        "tenant-isolated bucket prefixes."
    )

    pdf.sub("Credential Encryption")
    pdf.body(
        "Connector credentials are encrypted with Fernet (symmetric, AES-128-CBC + HMAC-SHA256) "
        "using a per-tenant encryption key derived from the master ENCRYPTION_KEY via HKDF. "
        "The master key is stored in GCP Secret Manager, never in environment variables."
    )

    # ================================================================
    # SECTION 11: LLM ROUTING
    # ================================================================
    pdf.add_page()
    pdf.section("11", "LLM Routing")
    pdf.body(
        "AgenticOrg uses RouteLLM (Apache 2.0, by lm-sys) to intelligently route requests "
        "to the most cost-effective LLM tier based on task complexity. This achieves 85% cost "
        "savings compared to routing everything to premium models."
    )

    pdf.sub("3-Tier Model Hierarchy")
    llm_w = [30, 45, 60, 55]
    pdf.table_header(["Tier", "Model", "Use Case", "Cost"], llm_w)
    pdf.table_row(["Economy", "Gemini 2.0 Flash", "Simple lookups, data retrieval", "Free (Google tier)"], llm_w, fill=True)
    pdf.table_row(["Standard", "Gemini 1.5 Pro", "Moderate reasoning, summaries", "Low"], llm_w)
    pdf.table_row(["Premium", "Claude / GPT-4o", "Complex reasoning, legal, finance", "Standard"], llm_w, fill=True)
    pdf.ln(2)

    pdf.sub("Routing Decision")
    pdf.body(
        "RouteLLM scores each prompt for complexity using a lightweight classifier. The score "
        "determines which tier handles the request. Thresholds are configurable per agent "
        "(some agents always use Premium, e.g., legal contract review)."
    )

    pdf.sub("Air-Gapped Deployment")
    pdf.body(
        "For regulated environments (defense, banking) that cannot make external API calls:"
    )
    pdf.bold_bullet("Tier 1-2", "Ollama (MIT, 130K stars) -- serves open models locally (Llama 3, Mistral)")
    pdf.bold_bullet("Tier 3", "vLLM (Apache 2.0, 45K stars) -- GPU-optimized inference for larger models")
    pdf.bullet("Zero external network calls -- all inference runs on-premise")
    pdf.bullet("Internal container registry for model weights")
    pdf.bullet("Same RouteLLM routing logic, just different backend URLs")

    # ================================================================
    # SECTION 12: KNOWLEDGE BASE (RAG)
    # ================================================================
    pdf.add_page()
    pdf.section("12", "Knowledge Base (RAG)")
    pdf.body(
        "The Knowledge Base enables agents to access company-specific documents during reasoning. "
        "It is powered by RAGFlow (Apache 2.0, 73K stars) for document ingestion and retrieval, "
        "with pgvector for embedding storage."
    )

    pdf.sub("Document Ingestion Pipeline")
    pdf.bullet("Supported formats: PDF, Word (.docx), Excel (.xlsx), TXT, HTML, Markdown")
    pdf.bullet("RAGFlow handles chunking with deep document understanding (table extraction, layout)")
    pdf.bullet("Embeddings generated and stored in pgvector (knowledge_chunks table)")
    pdf.bullet("Per-tenant namespace isolation (multi-tenant)")
    pdf.bullet("Documents encrypted at rest via PostgreSQL transparent data encryption (GCP Cloud SQL)")
    pdf.ln(2)

    pdf.sub("Agent Tool: knowledge_base_search")
    pdf.code_block(
        "# Available to all agents as a registered tool\n"
        "knowledge_base_search(\n"
        "    query: str,        # Natural language search query\n"
        "    top_k: int = 5,    # Number of results to return\n"
        "    namespace: str = None  # Filter to specific doc collection\n"
        ") -> list[ChunkResult]"
    )

    pdf.sub("API Endpoints")
    pdf.bullet("POST /knowledge/upload -- Upload document (PDF/Word/Excel/TXT)")
    pdf.bullet("GET /knowledge/documents -- List uploaded documents (paginated)")
    pdf.bullet("DELETE /knowledge/documents/{id} -- Remove document and chunks")
    pdf.bullet("POST /knowledge/search -- Semantic search across knowledge base")
    pdf.bullet("GET /knowledge/stats -- Document count, chunk count, index size")
    pdf.bullet("POST /knowledge/reindex -- Force re-indexing of all documents")

    # ================================================================
    # SECTION 13: VOICE AGENTS
    # ================================================================
    pdf.add_page()
    pdf.section("13", "Voice Agents")
    pdf.body(
        "Voice agents turn any AgenticOrg agent into a phone-based assistant. Built on "
        "LiveKit (Apache 2.0) and Pipecat (BSD-2), with local STT/TTS for privacy."
    )

    pdf.sub("Technology Stack")
    pdf.bold_bullet("Realtime Engine", "LiveKit Agents (Apache 2.0, 9.9K stars)")
    pdf.bold_bullet("Voice Framework", "Pipecat (BSD-2, 8K+ stars)")
    pdf.bold_bullet("STT (Default)", "Whisper (local) -- no audio leaves the server")
    pdf.bold_bullet("TTS (Default)", "Piper (local) -- no text leaves the server")
    pdf.bold_bullet("SIP Providers", "Twilio, Vonage, or custom SIP trunk (TLS required)")
    pdf.ln(2)

    pdf.sub("Voice Pipeline")
    pdf.code_block(
        "Phone Call -> SIP (TLS) -> LiveKit Server -> STT (Whisper)\n"
        "    -> Presidio PII Redaction -> Agent Reasoning (LangGraph)\n"
        "    -> TTS (Piper) -> SIP (TLS) -> Phone Call"
    )

    pdf.sub("Key Features")
    pdf.bullet("SIP TLS enforced -- unencrypted SIP connections are rejected")
    pdf.bullet("PII redaction applied to transcribed speech before agent processing")
    pdf.bullet("Same agent logic, tools, and scopes as text-based agents")
    pdf.bullet("Session recording (optional, encrypted at rest, tenant-isolated)")
    pdf.bullet("Real-time interruption handling (barge-in support)")
    pdf.bullet("Multi-language support via Whisper + Piper language models")

    # ================================================================
    # SECTION 14: BROWSER RPA
    # ================================================================
    pdf.add_page()
    pdf.section("14", "Browser RPA")
    pdf.body(
        "For legacy systems without APIs (especially Indian government portals), AgenticOrg "
        "provides browser automation via Playwright (Apache 2.0, 70K stars). All RPA runs "
        "execute in sandboxed Docker containers."
    )

    pdf.sub("Pre-Built Scripts")
    rpa_w = [45, 145]
    pdf.table_header(["Portal", "Capabilities"], rpa_w)
    rpa_scripts = [
        ("EPFO", "ECR download, PF balance check, UAN status, compliance filing"),
        ("MCA (ROC)", "Company search, director lookup, financial statements download"),
        ("Income Tax", "26AS download, ITR status, refund status, AIS retrieval"),
        ("GST Portal", "Return filing status, GSTR download, GSTIN verification"),
    ]
    for i, row in enumerate(rpa_scripts):
        pdf.table_row(list(row), rpa_w, fill=(i % 2 == 0))

    pdf.ln(3)
    pdf.sub("Architecture")
    pdf.bullet("Playwright runs in headless Chromium inside a Docker container")
    pdf.bullet("Sandboxed: no network access except target portal, no persistent storage")
    pdf.bullet("Screenshot captured at every step for audit compliance")
    pdf.bullet("Results extracted as structured JSON and returned to the agent")
    pdf.bullet("Timeout: 120 seconds per script execution (configurable)")
    pdf.bullet("Retry: up to 3 attempts with exponential backoff on transient failures")

    # ================================================================
    # SECTION 15: BILLING SYSTEM
    # ================================================================
    pdf.add_page()
    pdf.section("15", "Billing System")
    pdf.body(
        "AgenticOrg supports both global (Stripe) and India-specific (PineLabs Plural) payment "
        "processing. The platform is free for self-hosted deployments."
    )

    pdf.sub("Plans")
    pw = [35, 25, 35, 30, 30, 35]
    pdf.table_header(["Plan", "Price", "India Price", "Agents", "Workflows", "Runs/mo"], pw)
    pdf.table_row(["Free", "$0", "Free", "3", "5", "1,000"], pw, fill=True)
    pdf.table_row(["Pro", "$49/mo", "Rs 999/mo", "15", "25", "10,000"], pw)
    pdf.table_row(["Enterprise", "$299/mo", "Rs 4999/mo", "Unlimited", "Unlimited", "Unlimited"], pw, fill=True)
    pdf.ln(2)

    pdf.sub("Usage Tracking")
    pdf.body(
        "Usage is tracked via Redis counters (billing_usage table for persistence). Counters "
        "track: agent runs, tool calls, LLM tokens, knowledge base storage, voice minutes. "
        "When a limit is reached, the system returns 402 Payment Required with upgrade prompt."
    )

    pdf.sub("Payment Processing")
    pdf.bold_bullet("Stripe (Global)", "Subscription billing, metered usage, webhook signature validation")
    pdf.bold_bullet("PineLabs Plural (India)", "NEFT/RTGS/IMPS, UPI, INR pricing, webhook integration")
    pdf.bullet("Webhook signatures validated with HMAC-SHA256 (fail-closed)")
    pdf.bullet("Replay prevention: event_id deduplication with 1-hour window")
    pdf.bullet("Idempotency keys on all payment API calls")

    # ================================================================
    # SECTION 16: CDC (CHANGE DATA CAPTURE)
    # ================================================================
    pdf.add_page()
    pdf.section("16", "CDC (Change Data Capture)")
    pdf.body(
        "The CDC system enables real-time reactions to changes in connected systems. When data "
        "changes in Salesforce, Jira, GSTN, or any connected system, AgenticOrg can automatically "
        "trigger workflows."
    )

    pdf.sub("Architecture")
    pdf.bold_bullet("Webhook Receivers", "Each connector can register webhook endpoints that receive "
                    "change notifications from external systems")
    pdf.bold_bullet("Polling Fallback", "For systems without webhooks, periodic polling detects changes")
    pdf.bold_bullet("Event Normalization", "Raw events are normalized to a standard CDC event schema")
    pdf.ln(2)

    pdf.sub("Security")
    pdf.bullet("HMAC-SHA256 signature validation on all incoming webhooks (fail-closed)")
    pdf.bullet("Unverified webhooks are rejected with 401 and logged as security events")
    pdf.bullet("Event deduplication: 1-hour sliding window using event_id + source hash")
    pdf.bullet("Replay prevention: timestamp validation (reject events older than 5 minutes)")
    pdf.ln(2)

    pdf.sub("Event Processing Pipeline")
    pdf.body(
        "1. Webhook received -> 2. Signature validated -> 3. Event deduplicated -> "
        "4. Normalized to CDC schema -> 5. Trigger evaluation (match against workflow triggers) -> "
        "6. Matching workflows dispatched -> 7. Audit log entry created."
    )

    # ================================================================
    # SECTION 17: UI ARCHITECTURE
    # ================================================================
    pdf.add_page()
    pdf.section("17", "UI Architecture")
    pdf.body(
        "The frontend is a single-page application built with React 18, TypeScript, Vite, "
        "Tailwind CSS, and shadcn/ui components. It communicates with the backend exclusively "
        "via the REST API."
    )

    pdf.sub("Technology Stack")
    pdf.bold_bullet("Framework", "React 18 with TypeScript strict mode")
    pdf.bold_bullet("Build Tool", "Vite (sub-second HMR)")
    pdf.bold_bullet("Styling", "Tailwind CSS + shadcn/ui component library")
    pdf.bold_bullet("State", "React Query (server state) + React Context (local state)")
    pdf.bold_bullet("Routing", "React Router v6 with lazy-loaded routes")
    pdf.bold_bullet("i18n", "react-i18next (English, Hindi)")
    pdf.bold_bullet("Testing", "19 Playwright E2E specs covering all critical paths")
    pdf.ln(2)

    pdf.sub("51 Pages (lazy-loaded)")
    ui_pages = [
        ("Auth", "Login, Signup, Forgot Password, Reset Password, Google OAuth Callback"),
        ("Dashboard", "Main Dashboard, CFO Dashboard, CMO Dashboard, ABM Dashboard, Scope Dashboard"),
        ("Agents", "Agent List, Agent Create (5-step wizard), Agent Detail, Agent Edit, Agent Runs"),
        ("Workflows", "Workflow List, Workflow Create, Workflow Detail, Workflow Runs, NL Generator"),
        ("Knowledge", "Knowledge Base, Upload, Document Detail, Search Results"),
        ("Voice", "Voice Agent Setup (5-step wizard), Voice Sessions, Voice Test"),
        ("RPA", "RPA Scripts, RPA Execution Detail, Screenshot Viewer"),
        ("Connectors", "Connector List, Connector Create, Composio Marketplace, Connector Detail"),
        ("Industry Packs", "Pack List, Pack Detail, Install Wizard"),
        ("Approvals", "Approval Queue, Approval Detail"),
        ("Settings", "General Settings, API Keys, Team Management, Billing, Profile"),
        ("Compliance", "Audit Log, DSAR, Evidence Collection, SOC2 Controls"),
        ("Misc", "Org Chart, Help, 404 Not Found"),
    ]
    for group, pages in ui_pages:
        pdf.bold_bullet(group, pages)

    pdf.add_page()
    pdf.sub("Authentication Flow")
    pdf.body(
        "The UI stores JWT tokens in localStorage. Every API request includes the token in the "
        "Authorization header. Token expiry is checked client-side; expired tokens trigger a "
        "redirect to the login page. ProtectedRoute HOC wraps all authenticated routes."
    )

    pdf.sub("i18n (Internationalization)")
    pdf.body(
        "The platform uses react-i18next with namespace-based translation files. Currently "
        "supported languages: English (en) and Hindi (hi). The language picker is in the top-right "
        "header. Agent responses also respect the selected language via a language directive "
        "injected into the system prompt."
    )

    pdf.sub("E2E Testing (Playwright)")
    pdf.body(
        "19 Playwright test specs cover critical user flows:"
    )
    pdf.bullet("Authentication: login, signup, logout, forgot password")
    pdf.bullet("Agent CRUD: create, edit, run, promote, pause")
    pdf.bullet("Workflow CRUD: create, run, NL generation")
    pdf.bullet("Connector management: registry, create, retest")
    pdf.bullet("Dashboard navigation: CFO, CMO, ABM, Scope")
    pdf.bullet("Approvals: approve, reject")
    pdf.bullet("Settings: API keys, billing, team")
    pdf.bullet("Knowledge Base: upload, search")
    pdf.bullet("Voice and RPA: setup wizards")

    # ================================================================
    # SECTION 18: DEVOPS & DEPLOYMENT
    # ================================================================
    pdf.add_page()
    pdf.section("18", "DevOps & Deployment")
    pdf.body(
        "AgenticOrg supports 4 deployment modes: Docker Compose (dev), GKE Autopilot Lean (demo), "
        "GKE Production (enterprise), and Air-gapped (regulated). All deployments use the same "
        "container images."
    )

    pdf.sub("Docker Compose Services")
    dcw = [35, 15, 25, 115]
    pdf.table_header(["Service", "Port", "Required", "Purpose"], dcw)
    dc_services = [
        ("api", "8000", "Yes", "FastAPI backend (main application)"),
        ("ui", "3000", "Yes", "React frontend (Nginx)"),
        ("postgres", "5432", "Yes", "PostgreSQL 16 + pgvector"),
        ("redis", "6379", "Yes", "Caching, rate limiting, Celery broker"),
        ("minio", "9000", "Yes", "S3-compatible object storage"),
        ("celery-worker", "--", "Yes", "Background task execution"),
        ("ragflow", "9380", "No", "RAGFlow document ingestion (RAG)"),
        ("livekit", "7880", "No", "Voice agent realtime server"),
        ("ollama", "11434", "No", "Local LLM serving (air-gapped)"),
        ("playwright", "--", "No", "RPA browser automation (sandboxed)"),
    ]
    for i, row in enumerate(dc_services):
        pdf.table_row(list(row), dcw, fill=(i % 2 == 0))

    pdf.ln(3)
    pdf.sub("Kubernetes / Helm")
    pdf.body(
        "The Helm chart includes 5 template files and 5 values profiles for different deployment sizes."
    )
    pdf.bold_bullet("Templates", "deployment.yaml, service.yaml, ingress.yaml, configmap.yaml, secrets.yaml")
    pdf.bold_bullet("values.yaml", "Production defaults (3 API replicas, managed Cloud SQL, Memorystore)")
    pdf.bold_bullet("values-lean.yaml", "Minimal (1 replica, in-cluster Redis, db-f1-micro Cloud SQL)")
    pdf.bold_bullet("values-airgap.yaml", "Air-gapped (Ollama, vLLM, internal registry)")
    pdf.bold_bullet("values-dev.yaml", "Development (all optional services enabled, debug logging)")
    pdf.bold_bullet("values-ha.yaml", "High availability (multi-zone, 5+ replicas, cross-region DB)")

    pdf.add_page()
    pdf.sub("CI/CD Pipeline (GitHub Actions)")
    pdf.body(
        "The pipeline runs on every push to main and on all pull requests. Stages execute sequentially "
        "with fail-fast behavior."
    )
    ci_stages = [
        ("1", "Lint", "ruff check + ruff format --check + mypy type checking"),
        ("2", "Unit Tests", "pytest with coverage (target: 80%+ on core modules)"),
        ("3", "Security Scan", "bandit (SAST), pip-audit (dependency CVEs)"),
        ("4", "Build", "Docker build for API + UI images, push to Artifact Registry"),
        ("5", "Deploy", "Helm upgrade to staging cluster, wait for rollout"),
        ("6", "E2E Tests", "19 Playwright specs against staging, screenshot on failure"),
        ("7", "Promote", "On success: tag image as :latest, promote to production"),
    ]
    for step_num, title, desc in ci_stages:
        pdf.ensure_space(12)
        y = pdf.get_y()
        pdf.set_fill_color(*GREEN)
        pdf.rect(12, y, 14, 8, style="F")
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*WHITE)
        pdf.set_xy(12, y + 1.5)
        pdf.cell(14, 5, step_num, align="C")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*D_GREEN)
        pdf.set_xy(29, y + 0.5)
        pdf.cell(30, 4, title)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*BLACK)
        pdf.set_xy(60, y + 0.5)
        pdf.cell(140, 4, desc)
        pdf.set_y(y + 11)

    pdf.ln(3)
    pdf.sub("Air-Gapped Deployment")
    pdf.body(
        "For defense, regulated banking, and other environments that prohibit external network access:"
    )
    pdf.bullet("LLM: Ollama (Tier 1/2) + vLLM (Tier 3) -- all models served locally")
    pdf.bullet("Container Registry: Internal harbor/registry -- no Docker Hub pulls")
    pdf.bullet("DNS: Internal DNS resolution only")
    pdf.bullet("Secrets: Vault or local encrypted file (no GCP Secret Manager)")
    pdf.bullet("Monitoring: Internal Prometheus + Grafana stack")
    pdf.bullet("Zero external API calls -- all functionality works offline")

    # ================================================================
    # SECTION 19: ERROR TAXONOMY
    # ================================================================
    pdf.add_page()
    pdf.section("19", "Error Taxonomy")
    pdf.body(
        "All errors follow a structured taxonomy with 41 error codes across 5 categories. "
        "Every API error returns an ErrorEnvelope with code, name, severity, retryable flag, "
        "and request_id for correlation."
    )

    pdf.sub("Error Categories")
    ew = [20, 55, 115]
    pdf.table_header(["Range", "Category", "Description"], ew)
    pdf.table_row(["E1xxx", "Tool Errors", "Tool execution failures, timeouts, connector errors"], ew, fill=True)
    pdf.table_row(["E2xxx", "Validation Errors", "Input validation, schema mismatch, data format"], ew)
    pdf.table_row(["E3xxx", "Workflow Errors", "Step failures, timeout, circular deps, state errors"], ew, fill=True)
    pdf.table_row(["E4xxx", "Auth Errors", "JWT invalid, scope denied, rate limit, key revoked"], ew)
    pdf.table_row(["E5xxx", "LLM Errors", "Model timeout, token limit, content filter, routing"], ew, fill=True)

    pdf.ln(3)
    pdf.sub("Error Code Reference (41 codes)")
    ecw = [15, 55, 20, 20, 20, 60]
    pdf.table_header(["Code", "Name", "Severity", "Retry", "Max", "Escalation"], ecw)
    errors = [
        ("E1001", "TOOL_NOT_FOUND", "error", "No", "--", "Log + return 404"),
        ("E1002", "TOOL_AUTH_FAILED", "error", "Yes", "3", "Refresh token, retry"),
        ("E1003", "TOOL_TIMEOUT", "warning", "Yes", "3", "Exponential backoff"),
        ("E1004", "TOOL_RATE_LIMITED", "warning", "Yes", "5", "Wait + retry"),
        ("E1005", "TOOL_RESPONSE_INVALID", "error", "Yes", "2", "Schema validation"),
        ("E1006", "CONNECTOR_DOWN", "critical", "Yes", "3", "Circuit breaker open"),
        ("E1007", "SCOPE_DENIED", "error", "No", "--", "Log denial, notify admin"),
        ("E1008", "TOOL_IDEMPOTENCY_CONFLICT", "warning", "No", "--", "Return cached result"),
        ("E2001", "VALIDATION_FAILED", "error", "No", "--", "Return 400 + details"),
        ("E2002", "SCHEMA_MISMATCH", "error", "No", "--", "Return 400 + schema"),
        ("E2003", "INVALID_ENUM_VALUE", "error", "No", "--", "Return 400 + options"),
        ("E2004", "REQUIRED_FIELD_MISSING", "error", "No", "--", "Return 400 + field"),
        ("E2005", "DUPLICATE_RESOURCE", "warning", "No", "--", "Return 409"),
        ("E3001", "WORKFLOW_STEP_FAILED", "error", "Yes", "3", "Re-plan or escalate"),
        ("E3002", "WORKFLOW_TIMEOUT", "critical", "No", "--", "Pause + alert admin"),
        ("E3003", "CIRCULAR_DEPENDENCY", "critical", "No", "--", "Reject definition"),
        ("E3004", "HITL_TIMEOUT", "warning", "No", "--", "Escalate to next role"),
        ("E3005", "STATE_CORRUPTION", "critical", "No", "--", "Restore checkpoint"),
    ]
    for i, row in enumerate(errors):
        pdf.table_row(list(row), ecw, fill=(i % 2 == 0))

    pdf.add_page()
    pdf.sub("Error Code Reference (continued)")
    pdf.table_header(["Code", "Name", "Severity", "Retry", "Max", "Escalation"], ecw)
    errors2 = [
        ("E3006", "PARALLEL_BRANCH_FAILED", "error", "Yes", "2", "Retry branch"),
        ("E3007", "ADAPTIVE_REPLAN_FAILED", "critical", "No", "--", "Manual intervention"),
        ("E4001", "JWT_EXPIRED", "warning", "No", "--", "Redirect to login"),
        ("E4002", "JWT_INVALID", "error", "No", "--", "Return 401"),
        ("E4003", "INSUFFICIENT_SCOPES", "error", "No", "--", "Return 403 + needed"),
        ("E4004", "API_KEY_REVOKED", "error", "No", "--", "Return 401"),
        ("E4005", "RATE_LIMIT_EXCEEDED", "warning", "Yes", "1", "Return 429 + Retry"),
        ("E4006", "TENANT_SUSPENDED", "critical", "No", "--", "Return 403"),
        ("E4007", "MFA_REQUIRED", "warning", "No", "--", "Redirect to MFA"),
        ("E4008", "IP_BLOCKED", "error", "No", "--", "Return 403"),
        ("E5001", "LLM_TIMEOUT", "warning", "Yes", "2", "Failover to backup"),
        ("E5002", "LLM_TOKEN_LIMIT", "error", "No", "--", "Truncate context"),
        ("E5003", "LLM_CONTENT_FILTER", "warning", "No", "--", "Rephrase + retry"),
        ("E5004", "LLM_ROUTING_FAILED", "error", "Yes", "2", "Default to Premium"),
        ("E5005", "LLM_HALLUCINATION", "critical", "No", "--", "Block output, HITL"),
        ("E5006", "LLM_PARSE_ERROR", "warning", "Yes", "3", "Re-prompt structured"),
        ("E5007", "LLM_PROVIDER_DOWN", "critical", "Yes", "3", "Auto-failover"),
        ("E1009", "PII_REDACTION_FAILED", "critical", "No", "--", "Block request"),
        ("E1010", "CONTENT_SAFETY_BLOCK", "error", "No", "--", "Block + log"),
        ("E3008", "CHECKPOINT_FAILED", "critical", "Yes", "3", "Retry checkpoint"),
        ("E3009", "EVENT_DEDUP_CONFLICT", "info", "No", "--", "Drop duplicate"),
        ("E2006", "FILE_TOO_LARGE", "error", "No", "--", "Return 413 + limit"),
        ("E2007", "UNSUPPORTED_FORMAT", "error", "No", "--", "Return 415 + types"),
    ]
    for i, row in enumerate(errors2):
        pdf.table_row(list(row), ecw, fill=(i % 2 == 0))

    # ================================================================
    # SECTION 20: ENVIRONMENT CONFIGURATION
    # ================================================================
    pdf.add_page()
    pdf.section("20", "Environment Configuration")
    pdf.body(
        "AgenticOrg uses 72 environment variables organized by category. Required variables must "
        "be set for the application to start. Optional variables have sensible defaults."
    )

    env_w = [55, 70, 15, 50]
    pdf.table_header(["Variable", "Default", "Req?", "Purpose"], env_w)
    envs = [
        ("DATABASE_URL", "(none)", "Yes", "PostgreSQL connection string"),
        ("REDIS_URL", "redis://localhost:6379", "Yes", "Redis connection string"),
        ("SECRET_KEY", "(none)", "Yes", "HS256 JWT signing key"),
        ("ENCRYPTION_KEY", "(none)", "Yes", "Fernet credential encryption"),
        ("GOOGLE_GENAI_API_KEY", "(none)", "Yes", "Gemini API key (LLM)"),
        ("ANTHROPIC_API_KEY", "(none)", "No", "Claude API key (Premium tier)"),
        ("OPENAI_API_KEY", "(none)", "No", "GPT API key (Premium tier)"),
        ("GRANTEX_ISSUER_URL", "(none)", "No", "Grantex RS256 auth server"),
        ("GRANTEX_CLIENT_ID", "(none)", "No", "Grantex OAuth client ID"),
        ("GRANTEX_CLIENT_SECRET", "(none)", "No", "Grantex OAuth client secret"),
        ("COMPOSIO_API_KEY", "(none)", "No", "Composio 1000+ integrations"),
        ("STRIPE_SECRET_KEY", "(none)", "No", "Stripe billing key"),
        ("STRIPE_WEBHOOK_SECRET", "(none)", "No", "Stripe webhook HMAC"),
        ("PINELABS_API_KEY", "(none)", "No", "PineLabs Plural (India)"),
        ("TWILIO_ACCOUNT_SID", "(none)", "No", "Twilio voice/SMS"),
        ("TWILIO_AUTH_TOKEN", "(none)", "No", "Twilio auth"),
        ("LIVEKIT_API_KEY", "(none)", "No", "LiveKit voice server"),
        ("LIVEKIT_API_SECRET", "(none)", "No", "LiveKit secret"),
        ("SENDGRID_API_KEY", "(none)", "No", "SendGrid email delivery"),
        ("SLACK_BOT_TOKEN", "(none)", "No", "Slack connector"),
        ("HUBSPOT_API_KEY", "(none)", "No", "HubSpot CRM connector"),
        ("SALESFORCE_CLIENT_ID", "(none)", "No", "Salesforce OAuth"),
        ("GCP_PROJECT_ID", "(none)", "No", "GCP project (Secret Mgr)"),
        ("GCS_BUCKET", "agenticorg-files", "No", "GCS bucket for uploads"),
        ("CORS_ORIGINS", "http://localhost:3000", "No", "Allowed CORS origins"),
        ("LOG_LEVEL", "INFO", "No", "Logging level"),
        ("ENVIRONMENT", "development", "No", "dev / staging / production"),
        ("RATE_LIMIT_RPM", "100", "No", "Default rate limit/min"),
        ("MAX_AGENT_ITERATIONS", "10", "No", "LangGraph loop limit"),
        ("CONFIDENCE_FLOOR", "0.88", "No", "Default confidence floor"),
        ("PII_REDACTION_ENABLED", "true", "No", "Enable/disable Presidio"),
        ("CONTENT_SAFETY_ENABLED", "true", "No", "Enable/disable safety"),
        ("OLLAMA_BASE_URL", "(none)", "No", "Ollama URL (air-gapped)"),
        ("VLLM_BASE_URL", "(none)", "No", "vLLM URL (air-gapped)"),
        ("RAGFLOW_API_URL", "http://ragflow:9380", "No", "RAGFlow service URL"),
        ("AUDIT_RETENTION_YEARS", "7", "No", "Audit log retention"),
    ]
    for i, row in enumerate(envs):
        pdf.table_row(list(row), env_w, fill=(i % 2 == 0))

    pdf.add_page()
    pdf.sub("Environment Variable Categories")
    pdf.bold_bullet("Core (4)", "DATABASE_URL, REDIS_URL, SECRET_KEY, ENCRYPTION_KEY")
    pdf.bold_bullet("LLM (5)", "GOOGLE_GENAI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, OLLAMA_BASE_URL, VLLM_BASE_URL")
    pdf.bold_bullet("Auth (3)", "GRANTEX_ISSUER_URL, GRANTEX_CLIENT_ID, GRANTEX_CLIENT_SECRET")
    pdf.bold_bullet("Billing (4)", "STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, PINELABS_API_KEY, PINELABS_WEBHOOK_SECRET")
    pdf.bold_bullet("Voice (4)", "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, LIVEKIT_API_KEY, LIVEKIT_API_SECRET")
    pdf.bold_bullet("Comms (4)", "SENDGRID_API_KEY, SLACK_BOT_TOKEN, HUBSPOT_API_KEY, SALESFORCE_CLIENT_ID")
    pdf.bold_bullet("GCP (2)", "GCP_PROJECT_ID, GCS_BUCKET")
    pdf.bold_bullet("Feature Flags (4)", "PII_REDACTION_ENABLED, CONTENT_SAFETY_ENABLED, CDC_ENABLED, VOICE_ENABLED")
    pdf.bold_bullet("Tuning (6)", "RATE_LIMIT_RPM, MAX_AGENT_ITERATIONS, CONFIDENCE_FLOOR, LOG_LEVEL, ENVIRONMENT, CORS_ORIGINS")
    pdf.bold_bullet("RAG (2)", "RAGFLOW_API_URL, RAGFLOW_API_KEY")
    pdf.bold_bullet("Integrations (34)", "Individual connector API keys and OAuth credentials (all optional)")

    # ================================================================
    # APPENDIX A: DEPENDENCY MATRIX
    # ================================================================
    pdf.add_page()
    pdf.section("A", "Appendix A: Dependency Matrix")
    pdf.body(
        "All 59 Python dependencies with their licenses. Every dependency MUST be MIT, "
        "Apache 2.0, or BSD-2-Clause. No AGPL, ELv2, SSPL, or proprietary libraries."
    )

    dw = [8, 55, 22, 15, 90]
    pdf.table_header(["#", "Package", "License", "Req?", "Purpose"], dw)
    deps = [
        ("1", "fastapi", "MIT", "Yes", "Web framework (API server)"),
        ("2", "uvicorn[standard]", "BSD-3", "Yes", "ASGI server"),
        ("3", "sqlalchemy[asyncio]", "MIT", "Yes", "ORM + async database access"),
        ("4", "asyncpg", "Apache 2.0", "Yes", "PostgreSQL async driver"),
        ("5", "alembic", "MIT", "Yes", "Database migrations"),
        ("6", "redis[hiredis]", "MIT", "Yes", "Redis client + C parser"),
        ("7", "pydantic", "MIT", "Yes", "Data validation + settings"),
        ("8", "pydantic-settings", "MIT", "Yes", "Settings from env vars"),
        ("9", "python-jose[crypto]", "MIT", "Yes", "JWT creation + validation"),
        ("10", "passlib[bcrypt]", "BSD-3", "Yes", "Password hashing (bcrypt)"),
        ("11", "httpx", "BSD-3", "Yes", "Async HTTP client"),
        ("12", "google-genai", "Apache 2.0", "Yes", "Gemini API client"),
        ("13", "anthropic", "MIT", "No", "Claude API client"),
        ("14", "openai", "Apache 2.0", "No", "GPT API client"),
        ("15", "google-cloud-storage", "Apache 2.0", "No", "GCS file storage"),
        ("16", "google-cloud-secret-mgr", "Apache 2.0", "No", "GCP Secret Manager"),
        ("17", "google-api-python-client", "Apache 2.0", "No", "Google APIs (OAuth)"),
        ("18", "google-auth", "Apache 2.0", "No", "Google authentication"),
        ("19", "dnspython", "ISC", "Yes", "DNS resolution"),
        ("20", "structlog", "Apache 2.0", "Yes", "Structured logging"),
    ]
    for i, row in enumerate(deps):
        pdf.table_row(list(row), dw, fill=(i % 2 == 0))

    pdf.add_page()
    pdf.table_header(["#", "Package", "License", "Req?", "Purpose"], dw)
    deps2 = [
        ("21", "opentelemetry-api", "Apache 2.0", "Yes", "Distributed tracing API"),
        ("22", "opentelemetry-sdk", "Apache 2.0", "Yes", "Distributed tracing SDK"),
        ("23", "opentelemetry-exporter", "Apache 2.0", "Yes", "OTLP trace export"),
        ("24", "otel-instr-fastapi", "Apache 2.0", "Yes", "FastAPI auto-instrumentation"),
        ("25", "prometheus-client", "Apache 2.0", "Yes", "Prometheus metrics"),
        ("26", "langgraph", "MIT", "Yes", "Agent state machine framework"),
        ("27", "langchain", "MIT", "Yes", "LLM application framework"),
        ("28", "langchain-google-genai", "MIT", "Yes", "Gemini LangChain integration"),
        ("29", "langchain-anthropic", "MIT", "No", "Claude LangChain integration"),
        ("30", "langchain-openai", "MIT", "No", "OpenAI LangChain integration"),
        ("31", "langgraph-ckpt-postgres", "MIT", "Yes", "State checkpointing"),
        ("32", "grantex", "MIT", "Yes", "Scope enforcement framework"),
        ("33", "langsmith", "MIT", "No", "LLM observability (tracing)"),
        ("34", "pypdf", "BSD-3", "Yes", "PDF parsing for RAG ingestion"),
        ("35", "pyyaml", "MIT", "Yes", "YAML parsing (workflows)"),
        ("36", "jinja2", "BSD-3", "Yes", "Template rendering (emails)"),
        ("37", "python-multipart", "Apache 2.0", "Yes", "File upload parsing"),
        ("38", "websockets", "BSD-3", "Yes", "WebSocket support (voice)"),
        ("39", "celery[redis]", "BSD-3", "Yes", "Background task queue"),
        ("40", "pgvector", "MIT", "Yes", "Vector similarity (RAG)"),
    ]
    for i, row in enumerate(deps2):
        pdf.table_row(list(row), dw, fill=(i % 2 == 0))

    pdf.add_page()
    pdf.table_header(["#", "Package", "License", "Req?", "Purpose"], dw)
    deps3 = [
        ("41", "jsonschema", "MIT", "Yes", "JSON schema validation"),
        ("42", "defusedxml", "PSF", "Yes", "Safe XML parsing"),
        ("43", "fpdf2", "LGPL-3", "Yes", "PDF generation (reports)"),
        ("44", "openpyxl", "MIT", "Yes", "Excel generation"),
        ("45", "pywebpush", "Apache 2.0", "Yes", "Web push notifications"),
        ("46", "composio-core", "MIT", "No", "1000+ tool integrations"),
        ("47", "routellm", "Apache 2.0", "No", "LLM routing + cost savings"),
        ("48", "presidio-analyzer", "MIT", "No", "PII detection (50+ types)"),
        ("49", "presidio-anonymizer", "MIT", "No", "PII redaction + deanonymize"),
        ("50", "pytest", "MIT", "Dev", "Test framework"),
        ("51", "pytest-asyncio", "Apache 2.0", "Dev", "Async test support"),
        ("52", "pytest-cov", "MIT", "Dev", "Code coverage"),
        ("53", "respx", "BSD-3", "Dev", "HTTP mock for httpx"),
        ("54", "testcontainers", "Apache 2.0", "Dev", "Docker test containers"),
        ("55", "factory-boy", "MIT", "Dev", "Test data factories"),
        ("56", "ruff", "MIT", "Dev", "Linter + formatter"),
        ("57", "mypy", "MIT", "Dev", "Static type checker"),
        ("58", "bandit", "Apache 2.0", "Dev", "Security linter (SAST)"),
        ("59", "pre-commit", "MIT", "Dev", "Git hook framework"),
    ]
    for i, row in enumerate(deps3):
        pdf.table_row(list(row), dw, fill=(i % 2 == 0))

    # ================================================================
    # APPENDIX B: SUMMARY STATS
    # ================================================================
    pdf.add_page()
    pdf.section("B", "Appendix B: Summary Stats")
    pdf.body("Complete platform statistics for AgenticOrg v4.0.0 (Project Apex).")
    pdf.ln(2)

    sw2 = [80, 110]
    pdf.table_header(["Metric", "Value"], sw2)
    summary = [
        ("Version", "4.0.0 (Project Apex)"),
        ("License", "Apache 2.0 (open source)"),
        ("Python Version", "3.12+"),
        ("Node.js Version", "18+ (frontend)"),
        ("Total AI Agents", "50+ (6 domains + 4 industry packs)"),
        ("Native Connectors", "63 (340+ tools)"),
        ("Composio Integrations", "1000+ (MIT)"),
        ("Pre-Built Workflows", "20 templates"),
        ("API Endpoints", "154 across 34 route modules"),
        ("Database Tables", "31 (PostgreSQL 16 + pgvector)"),
        ("UI Pages", "51 (React 18 + TypeScript)"),
        ("Playwright E2E Specs", "19"),
        ("Python Dependencies", "59 (45 runtime + 14 v4/dev)"),
        ("Environment Variables", "72 (4 required + 68 optional)"),
        ("Error Codes", "41 across 5 categories"),
        ("Grantex Manifests", "53"),
        ("PII Recognizers", "50+ (includes India: Aadhaar, PAN, GSTIN, UPI)"),
        ("LLM Tiers", "3 (Economy/Standard/Premium)"),
        ("Auth Methods", "3 (Grantex RS256, HS256 legacy, API key)"),
        ("Deployment Modes", "4 (Docker, K8s Lean, K8s Prod, Air-gapped)"),
        ("SOC2 Controls", "10"),
        ("Quality Gates", "6 (shadow mode)"),
        ("Agent Lifecycle States", "7"),
        ("Workflow Step Types", "7"),
        ("Workflow Triggers", "5"),
        ("Voice Providers", "3 (Twilio, Vonage, Custom SIP)"),
        ("RPA Portals", "4 (EPFO, MCA, Income Tax, GST)"),
        ("Billing Plans", "3 (Free / Pro $49 / Enterprise $299)"),
        ("Supported Languages", "2 (English, Hindi)"),
        ("Audit Retention", "7 years (WORM, HMAC-signed)"),
        ("Cost Savings (LLM)", "85% via RouteLLM routing"),
        ("Scope Enforcement Latency", "<1ms (offline JWT + cached JWKS)"),
    ]
    for i, row in enumerate(summary):
        pdf.table_row(list(row), sw2, fill=(i % 2 == 0))

    # ================================================================
    # FINAL PAGE
    # ================================================================
    pdf.add_page()
    pdf.ln(40)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*NAVY)
    pdf.cell(0, 10, "AgenticOrg v4.0.0 -- Project Apex", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 13)
    pdf.set_text_color(*GRAY)
    pdf.cell(0, 8, "Architecture & Product Document", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, "agenticorg.ai | github.com/agenticorg", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, "support@agenticorg.ai", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(0, 7, f"Generated: {DATE} | Version {VERSION}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, "Open Source -- Apache 2.0 License", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*GRAY)
    pdf.cell(
        0, 7,
        "50+ Agents | 63 Connectors | 1000+ Integrations | 154 Endpoints | 31 Tables | 51 Pages",
        align="C",
    )

    return pdf


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "..", "docs")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f"AgenticOrg_Architecture_v{VERSION}.pdf")
    print("Generating Architecture & Product Document...")
    p = build()
    p.output(path)
    print(f"Done! {p.pages_count} pages -> {path}")
