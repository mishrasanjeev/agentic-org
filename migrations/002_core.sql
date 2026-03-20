-- migrations/002_core.sql
CREATE TABLE tenants (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name VARCHAR(255) NOT NULL,
  slug VARCHAR(100) UNIQUE NOT NULL,
  plan VARCHAR(50) NOT NULL DEFAULT 'enterprise',
  data_region VARCHAR(10) NOT NULL DEFAULT 'IN',
  settings JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at TIMESTAMPTZ
);

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  email VARCHAR(255) NOT NULL,
  name VARCHAR(255),
  role VARCHAR(50) NOT NULL,
  domain VARCHAR(50),
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  last_login_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(tenant_id, email)
);

CREATE TABLE agents (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  name VARCHAR(255) NOT NULL,
  agent_type VARCHAR(100) NOT NULL,
  domain VARCHAR(50) NOT NULL,
  description TEXT,
  system_prompt_ref VARCHAR(500) NOT NULL,
  prompt_variables JSONB NOT NULL DEFAULT '{}',
  llm_model VARCHAR(100) NOT NULL DEFAULT 'claude-3-5-sonnet-20241022',
  llm_fallback VARCHAR(100),
  llm_config JSONB NOT NULL DEFAULT '{}',
  confidence_floor NUMERIC(4,3) NOT NULL DEFAULT 0.880,
  hitl_condition TEXT NOT NULL,
  max_retries SMALLINT NOT NULL DEFAULT 3,
  retry_backoff VARCHAR(20) NOT NULL DEFAULT 'exponential',
  authorized_tools JSONB NOT NULL DEFAULT '[]',
  output_schema VARCHAR(100),
  status VARCHAR(30) NOT NULL DEFAULT 'shadow',
  version VARCHAR(20) NOT NULL DEFAULT '1.0.0',
  parent_agent_id UUID REFERENCES agents(id),
  shadow_comparison_agent_id UUID REFERENCES agents(id),
  shadow_min_samples INTEGER NOT NULL DEFAULT 100,
  shadow_accuracy_floor NUMERIC(4,3) NOT NULL DEFAULT 0.950,
  shadow_sample_count INTEGER NOT NULL DEFAULT 0,
  shadow_accuracy_current NUMERIC(4,3),
  cost_controls JSONB NOT NULL DEFAULT '{}',
  scaling JSONB NOT NULL DEFAULT '{"min_replicas":1,"max_replicas":5}',
  tags VARCHAR(50)[] NOT NULL DEFAULT '{}',
  ttl_hours INTEGER,
  expires_at TIMESTAMPTZ,
  config JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(tenant_id, agent_type, version)
);
CREATE INDEX idx_agents_tenant_domain ON agents(tenant_id, domain);
CREATE INDEX idx_agents_status ON agents(status) WHERE status = 'active';
