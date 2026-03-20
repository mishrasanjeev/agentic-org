-- migrations/003_operational.sql

CREATE TABLE workflow_definitions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  name VARCHAR(255) NOT NULL,
  version VARCHAR(20) NOT NULL DEFAULT '1.0',
  description TEXT,
  domain VARCHAR(50),
  definition JSONB NOT NULL,
  trigger_type VARCHAR(50),
  trigger_config JSONB,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(tenant_id, name, version)
);

CREATE TABLE workflow_runs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  workflow_def_id UUID NOT NULL REFERENCES workflow_definitions(id),
  status VARCHAR(30) NOT NULL DEFAULT 'pending',
  trigger_payload JSONB,
  context JSONB NOT NULL DEFAULT '{}',
  result JSONB,
  error JSONB,
  steps_total SMALLINT,
  steps_completed SMALLINT DEFAULT 0,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  timeout_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (created_at);
CREATE INDEX idx_wf_runs_tenant_status ON workflow_runs(tenant_id, status);
CREATE INDEX idx_wf_runs_created ON workflow_runs(created_at DESC);

CREATE TABLE step_executions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  workflow_run_id UUID NOT NULL,
  step_id VARCHAR(100) NOT NULL,
  step_type VARCHAR(50) NOT NULL,
  agent_id UUID REFERENCES agents(id),
  status VARCHAR(30) NOT NULL DEFAULT 'pending',
  input JSONB,
  output JSONB,
  confidence NUMERIC(4,3),
  reasoning_trace JSONB,
  error JSONB,
  retry_count SMALLINT DEFAULT 0,
  latency_ms INTEGER,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ
) PARTITION BY RANGE (started_at);
CREATE INDEX idx_step_exec_run ON step_executions(workflow_run_id);

CREATE TABLE tool_calls (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  step_exec_id UUID NOT NULL,
  agent_id UUID NOT NULL REFERENCES agents(id),
  tool_name VARCHAR(100) NOT NULL,
  connector_id UUID,
  input_hash VARCHAR(64),
  output_hash VARCHAR(64),
  status VARCHAR(20) NOT NULL,
  http_status SMALLINT,
  error_code VARCHAR(10),
  idempotency_key VARCHAR(255),
  latency_ms INTEGER,
  llm_tokens INTEGER,
  called_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (called_at);
CREATE UNIQUE INDEX idx_tool_calls_idempotency
  ON tool_calls(tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE hitl_queue (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  workflow_run_id UUID NOT NULL,
  agent_id UUID NOT NULL REFERENCES agents(id),
  title VARCHAR(500) NOT NULL,
  trigger_type VARCHAR(50) NOT NULL,
  priority VARCHAR(20) NOT NULL DEFAULT 'normal',
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  assignee_role VARCHAR(100) NOT NULL,
  decision_options JSONB NOT NULL,
  context JSONB NOT NULL,
  decision VARCHAR(100),
  decision_by UUID REFERENCES users(id),
  decision_at TIMESTAMPTZ,
  decision_notes TEXT,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_hitl_pending ON hitl_queue(tenant_id, assignee_role, status)
  WHERE status = 'pending';
CREATE INDEX idx_hitl_expires ON hitl_queue(expires_at) WHERE status = 'pending';

-- APPEND-ONLY: RLS blocks all UPDATE/DELETE
CREATE TABLE audit_log (
  id UUID NOT NULL DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL,
  event_type VARCHAR(100) NOT NULL,
  actor_type VARCHAR(20) NOT NULL,
  actor_id VARCHAR(255) NOT NULL,
  agent_id UUID,
  workflow_run_id UUID,
  resource_type VARCHAR(100),
  resource_id VARCHAR(255),
  action VARCHAR(100) NOT NULL,
  outcome VARCHAR(50) NOT NULL,
  details JSONB NOT NULL DEFAULT '{}',
  signature VARCHAR(512),
  trace_id VARCHAR(64),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_insert_only ON audit_log FOR INSERT WITH CHECK (TRUE);
CREATE POLICY audit_select ON audit_log FOR SELECT
  USING (tenant_id = current_setting('agentflow.tenant_id')::UUID);
CREATE INDEX idx_audit_tenant_time ON audit_log(tenant_id, created_at DESC);

CREATE TABLE connectors (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  name VARCHAR(100) NOT NULL,
  category VARCHAR(50) NOT NULL,
  description TEXT,
  base_url VARCHAR(500),
  auth_type VARCHAR(50) NOT NULL,
  auth_config JSONB NOT NULL DEFAULT '{}',
  secret_ref VARCHAR(255),
  tool_functions JSONB NOT NULL DEFAULT '[]',
  data_schema_ref VARCHAR(100),
  rate_limit_rpm INTEGER NOT NULL DEFAULT 60,
  timeout_ms INTEGER NOT NULL DEFAULT 10000,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  health_check_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(tenant_id, name)
);

CREATE TABLE schema_registry (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID,
  name VARCHAR(100) NOT NULL,
  version VARCHAR(20) NOT NULL DEFAULT '1',
  description TEXT,
  json_schema JSONB NOT NULL,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  created_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(tenant_id, name, version)
);

CREATE TABLE documents (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  name VARCHAR(500) NOT NULL,
  doc_type VARCHAR(100),
  s3_bucket VARCHAR(255) NOT NULL,
  s3_key VARCHAR(1000) NOT NULL,
  embedding VECTOR(1536),
  metadata JSONB NOT NULL DEFAULT '{}',
  retention_until DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_docs_embedding ON documents
  USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
