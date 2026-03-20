-- migrations/004_scaling.sql

CREATE TABLE agent_versions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  agent_id UUID NOT NULL REFERENCES agents(id),
  version VARCHAR(20) NOT NULL,
  system_prompt TEXT NOT NULL,
  authorized_tools JSONB NOT NULL,
  hitl_policy JSONB NOT NULL,
  llm_config JSONB NOT NULL,
  confidence_floor NUMERIC(4,3) NOT NULL,
  is_verified_good BOOLEAN NOT NULL DEFAULT FALSE,
  created_by UUID REFERENCES users(id),
  deployed_at TIMESTAMPTZ,
  retired_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(agent_id, version)
);

CREATE TABLE agent_lifecycle_events (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  agent_id UUID NOT NULL REFERENCES agents(id),
  from_status VARCHAR(30) NOT NULL,
  to_status VARCHAR(30) NOT NULL,
  triggered_by VARCHAR(30) NOT NULL,
  triggered_by_user UUID REFERENCES users(id),
  reason TEXT,
  notes TEXT,
  shadow_accuracy NUMERIC(4,3),
  shadow_samples INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE agent_teams (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  name VARCHAR(255) NOT NULL,
  domain VARCHAR(50),
  routing_rules JSONB NOT NULL DEFAULT '[]',
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(tenant_id, name)
);

CREATE TABLE agent_team_members (
  team_id UUID NOT NULL REFERENCES agent_teams(id),
  agent_id UUID NOT NULL REFERENCES agents(id),
  role VARCHAR(100) NOT NULL,
  weight NUMERIC(3,2) NOT NULL DEFAULT 1.0,
  added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (team_id, agent_id)
);

CREATE TABLE agent_cost_ledger (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  agent_id UUID NOT NULL REFERENCES agents(id),
  period_date DATE NOT NULL,
  token_count BIGINT NOT NULL DEFAULT 0,
  cost_usd NUMERIC(10,4) NOT NULL DEFAULT 0,
  task_count INTEGER NOT NULL DEFAULT 0,
  cost_per_task NUMERIC(8,6),
  budget_pct_used NUMERIC(5,2),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(tenant_id, agent_id, period_date)
);

CREATE TABLE shadow_comparisons (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  shadow_agent_id UUID NOT NULL REFERENCES agents(id),
  reference_agent_id UUID NOT NULL REFERENCES agents(id),
  workflow_run_id UUID,
  outputs_match BOOLEAN NOT NULL,
  match_score NUMERIC(4,3),
  shadow_confidence NUMERIC(4,3),
  shadow_hitl_would_trigger BOOLEAN,
  reference_hitl_triggered BOOLEAN,
  shadow_latency_ms INTEGER,
  reference_latency_ms INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (created_at);
