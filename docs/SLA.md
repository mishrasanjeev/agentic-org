# AgenticOrg Service Level Agreement

**Effective date:** 2026-04-11
**Version:** 1.0

This SLA applies to the managed SaaS deployment at `https://app.agenticorg.ai`
and to customers on the Pro and Enterprise plans. Self-hosted / air-gapped
deployments are governed by a separate support contract.

## 1. Uptime commitment

| Plan         | Monthly uptime target | Scheduled maintenance window |
|--------------|-----------------------|------------------------------|
| Free         | Best effort           | Any time                     |
| Pro          | **99.9%**             | Sundays 02:00–04:00 IST      |
| Enterprise   | **99.95%**            | Coordinated per customer     |

Uptime excludes:
- Scheduled maintenance announced ≥48 hours in advance via the status page.
- Failures caused by customer misuse (e.g., exhausting plan quota).
- Failures in customer-supplied credentials or third-party connectors
  (we publish an "upstream health" row on the status page but do not
  consider those outages).
- Force majeure (regional cloud provider outages, DDoS against a
  shared upstream).

## 2. Service credits

If we miss the uptime target in a calendar month, eligible customers
receive credits against the next invoice:

| Monthly uptime    | Pro credit | Enterprise credit |
|-------------------|------------|-------------------|
| < 99.9%           | 10%        | 25%               |
| < 99.5%           | 25%        | 50%               |
| < 99.0%           | 50%        | 100%              |

Credits must be requested within 30 days of the incident.

## 3. Incident severity & response SLA

| Severity | Definition                                              | First response | Status update cadence | Target resolution |
|----------|---------------------------------------------------------|----------------|------------------------|-------------------|
| **SEV-1**| Full outage or data loss                                | 15 minutes     | Every 30 minutes       | 4 hours           |
| **SEV-2**| Major feature degraded (billing, login, agent execution)| 1 hour         | Every 2 hours          | 24 hours          |
| **SEV-3**| Minor feature broken, workaround available              | 4 business hrs | Daily                  | 5 business days   |
| **SEV-4**| Cosmetic / enhancement request                          | 2 business days| Weekly                 | Next release      |

## 4. Support channels & hours

- **Email:** `support@agenticorg.ai`
- **Slack Connect:** available for Enterprise customers on request
- **Phone:** +91-80-XXXXXXXX (Enterprise SEV-1 only, 24×7)
- **Status page:** `https://status.agenticorg.ai` (external Statuspage
  integration, updated automatically by our monitoring)

Business hours are 09:00–18:00 IST Monday–Friday. SEV-1 incidents are
handled 24×7 on all paid plans.

## 5. Escalation path

1. Frontline support engineer (first response)
2. On-call SRE (if not resolved within SEV response window)
3. Engineering lead for the affected subsystem
4. CTO (for SEV-1 > 2 hours or any data-loss event)

## 6. Data protection & backups

- Backups: daily full + continuous WAL archive. Retention 30 days on
  Pro, 1 year on Enterprise.
- **Recovery Point Objective (RPO):** 5 minutes.
- **Recovery Time Objective (RTO):** 60 minutes for SEV-1 database failures.
- Encryption at rest: AES-256 via Google Cloud KMS.
- Encryption in transit: TLS 1.2+.
- BYOK / CMEK: available on Enterprise plans (roadmap — see
  `docs/BYOK_ROADMAP.md`).

## 7. Exclusions

This SLA does not cover:
- Beta or alpha features (see `docs/AGENT_MATURITY_MATRIX.md`).
- Usage beyond your plan's quota — the platform will rate-limit and
  return 429, which is the documented behavior, not an outage.
- Issues in customer-written workflow YAML or custom agents.
- Networking issues between the customer and our edge.

## 8. Review cadence

This SLA is reviewed annually. Material changes are communicated at
least 30 days in advance via the status page and in-app banner.

## Contact

Questions: `legal@agenticorg.ai` or your account manager.
