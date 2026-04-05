# Data Classification Matrix

## Overview

This document defines AgenticOrg's data classification levels, handling requirements,
and retention policies for all data types processed by the platform.

---

## Classification Levels

### Level 1 — Public
- **Definition:** Information intended for public disclosure.
- **Examples:** Marketing content, public API documentation, pricing pages.
- **Handling:** No restrictions on storage or transmission.
- **Retention:** Indefinite.

### Level 2 — Internal
- **Definition:** Business information not intended for public release.
- **Examples:** Agent configurations, workflow definitions, system metrics, non-PII logs.
- **Handling:** Access restricted to authenticated users within tenant scope.
- **Retention:** 1 year, then archived.

### Level 3 — Confidential
- **Definition:** Sensitive business data that could cause harm if disclosed.
- **Examples:** Customer contact data, financial reports, CRM records, HR data.
- **Handling:** Encrypted at rest and in transit. Access logged. Role-based access control.
- **Retention:** Per tenant policy (default 3 years).

### Level 4 — Restricted
- **Definition:** Highly sensitive data subject to regulatory requirements.
- **Examples:** PII, PHI, payment data, authentication credentials, API secrets.
- **Handling:** Encrypted with tenant-specific keys. Access requires explicit grant. Full audit trail.
- **Retention:** Per regulatory requirement (GDPR: until erasure request; HIPAA: 6 years).

---

## Data Type Mapping

| Data Type                 | Classification | Encryption | Audit Logged | Retention       |
|---------------------------|----------------|------------|--------------|-----------------|
| Agent prompt templates    | Internal       | At rest    | No           | 1 year          |
| Workflow definitions      | Internal       | At rest    | Yes          | 1 year          |
| Audit logs                | Confidential   | At rest    | N/A          | 365 days        |
| User profiles (name/email)| Confidential   | At rest    | Yes          | Account lifetime|
| Customer CRM data         | Confidential   | At rest    | Yes          | Per tenant      |
| Financial records         | Restricted     | At rest    | Yes          | 7 years         |
| Employee HR data          | Restricted     | At rest    | Yes          | Per regulation  |
| Patient health info (PHI) | Restricted     | At rest    | Yes          | 6 years (HIPAA) |
| API keys and secrets      | Restricted     | At rest    | Yes          | Until rotated   |
| OAuth tokens              | Restricted     | At rest    | Yes          | Until expiry    |
| Session tokens (JWT)      | Restricted     | In transit | Yes          | 1 hour          |

---

## Handling Requirements by Level

### Storage
- **Public/Internal:** Standard PostgreSQL with TDE.
- **Confidential:** PostgreSQL TDE + column-level encryption for sensitive fields.
- **Restricted:** PostgreSQL TDE + application-layer encryption + tenant-scoped keys.

### Transmission
- **All levels:** TLS 1.3 minimum for external traffic.
- **Confidential/Restricted:** mTLS for inter-service communication.

### Access Control
- **Internal:** Authenticated user within tenant.
- **Confidential:** Role-based (domain-scoped Grantex grants).
- **Restricted:** Explicit per-resource grants + approval workflow.

### Disposal
- **Internal:** Soft delete + hard purge after retention period.
- **Confidential:** Secure overwrite + audit log entry.
- **Restricted:** Cryptographic erasure (destroy encryption keys) + DSAR compliance.
