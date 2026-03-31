-- migrations/011_api_keys.sql
-- API key management for SDK/CLI/MCP integrators
CREATE TABLE api_keys (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  user_id UUID NOT NULL REFERENCES users(id),
  name VARCHAR(100) NOT NULL,
  prefix VARCHAR(12) NOT NULL,           -- visible prefix (e.g. "ao_sk_a1b2c3")
  key_hash VARCHAR(128) NOT NULL,        -- bcrypt hash of the full key
  scopes TEXT[] NOT NULL DEFAULT '{}',   -- e.g. {"agents:read","agents:run","connectors:read"}
  last_used_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  status VARCHAR(20) NOT NULL DEFAULT 'active',  -- active, revoked
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_api_keys_tenant ON api_keys(tenant_id) WHERE status = 'active';
CREATE INDEX idx_api_keys_prefix ON api_keys(prefix);
