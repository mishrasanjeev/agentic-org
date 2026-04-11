# HIPAA Compliance

AgenticOrg's healthcare industry pack is designed to operate as a
Business Associate under the U.S. Health Insurance Portability and
Accountability Act (HIPAA). This document summarizes the controls in
place and what remains the customer's responsibility.

> **Important:** HIPAA controls are only active when the
> `healthcare_hipaa` feature flag is enabled for your tenant. Enabling
> this flag requires a signed Business Associate Agreement (BAA).

## Business Associate Agreement (BAA)

A BAA is required before any Protected Health Information (PHI) may be
processed. Template: `https://agenticorg.ai/legal/baa`. Contact
`sanjeev@agenticorg.ai` to execute.

## Technical safeguards (45 CFR §164.312)

| Requirement          | Our control                                   |
|----------------------|-----------------------------------------------|
| Access control       | Role-based + row-level security, MFA enforced for admin roles. |
| Audit controls       | All PHI access logged to the append-only `audit_log` table with HMAC chain. UPDATE/DELETE is blocked by a Postgres trigger (migration `v460_enterprise`). |
| Integrity            | Database checksums, per-row digital signatures on audit records. |
| Person authentication| SSO (SAML/OIDC) required, 12h session TTL.    |
| Transmission security| TLS 1.2+ enforced, HSTS, no cleartext paths. |

## Physical safeguards

Hosted on Google Cloud Platform which is itself BAA-covered. See
Google's HIPAA compliance page for data center certifications.

## Administrative safeguards

- Workforce training: annual security/HIPAA refresher, logged.
- Access management: quarterly access reviews.
- Incident response: see `docs/incident-response.md`. PHI breaches
  escalate to the DPO within 1 hour.
- Business associate agreements with sub-processors (Anthropic has a
  BAA; LLM inference routed to their HIPAA-eligible endpoint).

## Audit log immutability

PHI access is append-only. The underlying Postgres table has:

```sql
CREATE TRIGGER audit_log_immutable
  BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_reject_mutation();
```

Defined in migration `migrations/versions/v4_6_0_enterprise_readiness.py`.

## PHI lifecycle

- **Ingestion**: customer uploads via authenticated API. PII recognizers
  in `core/pii/india_recognizers.py` tag the sensitive spans.
- **Processing**: PHI is passed to LLMs only via the HIPAA-eligible
  Anthropic endpoint. Prompt caching is **disabled** on these routes.
- **Storage**: encrypted at rest (AES-256 / Google KMS).
- **Retention**: per customer instructions; minimum 6 years per §164.530.
- **Destruction**: on DSAR / retention expiry, data is hard-deleted and
  the audit log records the deletion event.

## Patient rights

Patient requests flow through the customer. The customer uses the
DSAR endpoints (`/api/v1/compliance/dsar/*`) to fulfill them.

## Breach notification

HIPAA-specific:
- Customer notification: within 24 hours of detection.
- Breach affecting >500 individuals: customer must notify HHS.
- We provide forensic logs and cooperation within 24h of request.

## Limitations

- Self-service HIPAA mode is **not** enabled by default. A customer
  must explicitly sign a BAA and have their tenant flag flipped.
- Alpha/beta agents are not covered by HIPAA — only GA agents listed
  in `docs/AGENT_MATURITY_MATRIX.md`.

## Contacts

- HIPAA questions: `sanjeev@agenticorg.ai`
- Security incidents: `sanjeev@agenticorg.ai`
