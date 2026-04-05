# SOC2 Type II Control Mapping

## Overview

This document maps AgenticOrg's security controls to SOC2 Type II Trust Service Criteria.
Each control includes implementation evidence references and testing procedures.

---

## Control 1: Access Control (CC6.1)

**Objective:** Logical access to information assets is restricted to authorized users.

**Implementation:**
- Grantex RBAC with domain-scoped grants (Finance, HR, Marketing, Ops)
- JWT token validation with per-tenant isolation
- API key rotation enforced every 90 days

**Evidence:** `GET /api/v1/compliance/evidence-package` (access_controls section), audit logs with `auth.*` event types.

---

## Control 2: Audit Logging (CC7.2)

**Objective:** System activities are monitored, logged, and retained.

**Implementation:**
- Immutable audit log for all agent actions, API calls, and data access
- Structured logging via structlog with tenant context
- 365-day retention in PostgreSQL + optional GCS archival

**Evidence:** `GET /api/v1/audit/logs`, evidence-package audit_logs section.

---

## Control 3: Data Encryption at Rest (CC6.7)

**Objective:** Data is encrypted when stored.

**Implementation:**
- PostgreSQL with AES-256 TDE on GCP Cloud SQL
- GCS buckets with Google-managed encryption keys (GMEK)
- Redis with TLS in-transit, ephemeral data only

**Evidence:** GCP Cloud SQL encryption status, GCS bucket encryption config.

---

## Control 4: Data Encryption in Transit (CC6.7)

**Objective:** Data is encrypted during transmission.

**Implementation:**
- TLS 1.3 enforced on all API endpoints
- mTLS for inter-service communication
- WebSocket connections over WSS only

**Evidence:** SSL certificate configuration, Cloud Run service settings.

---

## Control 5: Change Management (CC8.1)

**Objective:** Changes to system components are authorized, tested, and documented.

**Implementation:**
- Git-based version control with required PR reviews
- CI/CD pipeline with automated testing (ruff, mypy, pytest, Playwright)
- Deployment audit trail via evidence-package deployment_records

**Evidence:** GitHub PR history, CI logs, evidence-package deployment_records section.

---

## Control 6: Incident Response (CC7.3)

**Objective:** Security incidents are identified, reported, and resolved.

**Implementation:**
- P1-P4 severity classification (see docs/incident-response.md)
- Automated alerting via GCP Cloud Monitoring
- Incident audit trail in evidence-package incident_history

**Evidence:** docs/incident-response.md, evidence-package incident_history section.

---

## Control 7: Vendor Management (CC9.2)

**Objective:** Third-party service providers are monitored and assessed.

**Implementation:**
- All 54 connectors validated for SOC2/ISO compliance
- Composio integration with OAuth2 scoped access
- Vendor risk assessment documented per connector

**Evidence:** Connector configuration, OAuth scope documentation.

---

## Control 8: Data Retention and Disposal (CC6.5)

**Objective:** Data is retained per policy and securely disposed when no longer needed.

**Implementation:**
- DSAR endpoints for access, export, and erasure (GDPR compliance)
- Configurable retention periods per tenant
- Automated data purge jobs for expired records

**Evidence:** `POST /api/v1/dsar/erase`, data classification matrix (docs/data-classification.md).

---

## Control 9: Session Management (CC6.1)

**Objective:** User sessions are managed securely with appropriate timeouts.

**Implementation:**
- JWT token expiry: 1 hour (access), 7 days (refresh)
- Concurrent session limit: 5 per user
- Token blacklisting on logout

**Evidence:** Auth configuration, token blacklist Redis keys.

---

## Control 10: Password and Authentication Policy (CC6.1)

**Objective:** Authentication mechanisms meet enterprise security standards.

**Implementation:**
- Minimum 12-character passwords with complexity requirements
- Bcrypt hashing with cost factor 12
- Optional MFA via TOTP
- Account lockout after 5 failed attempts (15-minute cooldown)

**Evidence:** Auth configuration, password policy documentation.
