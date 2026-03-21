# Security Policy

## Reporting a Vulnerability

**Do NOT open a public issue for security vulnerabilities.**

Email **mishra.sanjeev@gmail.com** with:
- Description of the vulnerability
- Steps to reproduce
- Potential impact assessment
- Suggested fix (if any)

We will acknowledge receipt within **48 hours** and provide a fix timeline within **5 business days**.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 2.x     | Yes       |
| 1.x     | No        |

## Security Architecture

AgenticOrg implements defense-in-depth across all layers:

### Authentication & Authorization
- **OAuth2/Grantex** with RS256 JWT tokens (60-minute TTL)
- **Per-agent token scoping**: `tool:{connector}:{read|write}:{resource}[:capped:{N}]`
- **Token Pool** with Redis-backed caching and pub/sub revocation (<2s propagation)
- **OPA policy engine** for fine-grained access control
- **Rate limiting** with token bucket algorithm (per-tenant, per-connector)
- **IP blocking** after 10 failed auth attempts in 60 seconds (15-minute lockout)

### Data Protection
- **PII masking** default-on for all log writers (email, phone, Aadhaar, PAN, bank accounts)
- **Tenant isolation** at every layer: PostgreSQL RLS, Redis key namespacing, S3 prefix isolation
- **Encryption at rest**: AES-256 for sensitive fields
- **TLS 1.3** enforced on all connections
- **Data residency**: configurable region (IN/EU/US), no cross-border transfer without explicit config

### Audit & Compliance
- **Append-only audit log** with HMAC-SHA256 tamper detection on every row
- **WORM storage** enforcement — RLS blocks all UPDATE/DELETE on audit_log
- **7-year retention** default for compliance
- **DSAR endpoints** for GDPR/DPDP access, erasure, and export
- **Evidence package** auto-generation for SOC2 Type II audits

### Agent Security
- **HITL gates enforced at Orchestrator level** — agents cannot observe or bypass their own gates
- **Shadow mode mandatory** before write access for any new agent
- **Clone scope ceiling** — child agents cannot have scopes parent does not have
- **Kill switch** effective in <30 seconds (token revocation + task stop)
- **Anti-hallucination rules** in every agent prompt — agents cannot invent data

### Error Taxonomy
50 typed error codes (E1001-E5005) with defined severity, retry policy, and escalation rules. Security-relevant codes trigger SIEM alerts:
- `E1004` TOOL_AUTH_FAILED — critical, no retry, immediate escalation
- `E1007` TOOL_SCOPE_DENIED — critical, possible breach, SIEM alert
- `E4002` TOKEN_INVALID_SIGNATURE — critical, possible tampering
- `E4004` TENANT_MISMATCH — critical, cross-tenant attempt, SIEM immediately
- `E5005` LLM_HALLUCINATION_DETECTED — critical, data not from tools

## Security Testing

The test suite includes 47 security-specific tests:
- **SEC-AUTH-001 to SEC-AUTH-008**: Auth bypass, brute force, token replay, scope elevation, cross-tenant access
- **SEC-LLM-001 to SEC-LLM-006**: Prompt injection (direct, indirect, context poisoning), SQL injection, system prompt extraction, hallucination detection
- **SEC-DATA-001 to SEC-DATA-007**: PII masking, encryption, TLS, data residency, tenant isolation, DPDP erasure
- **SEC-INFRA-001 to SEC-INFRA-002**: Container CVE scanning, WORM audit log immutability
