# DPDP Act (India) Compliance

AgenticOrg is headquartered in India and processes personal data for
Indian customers. This document describes how we comply with the
Digital Personal Data Protection Act, 2023 (DPDP Act).

## Our role under DPDP

- **Data Fiduciary**: the AgenticOrg customer (e.g., a CA firm or
  enterprise).
- **Data Processor**: AgenticOrg Technologies Pvt Ltd.

We only process Data Principal data on the documented instructions of
the Data Fiduciary.

## Consent

- Consent notices are the Data Fiduciary's responsibility.
- We store consent metadata (timestamp, IP, policy version) on any
  row where a user signs up via `/api/v1/auth/register`.
- Consent can be withdrawn via `POST /api/v1/compliance/dsar/withdraw`
  which is equivalent in effect to an erasure request.

## Data localization

Production data for Indian customers is stored exclusively in the
`asia-south1` region (Mumbai) on Google Cloud. Cross-region backups
stay within India (`asia-south2` in Delhi).

LLM inference is routed to Anthropic's us-east-1 endpoint by default;
Indian customers with a data residency requirement can enable the
`india_only_llm_routing` feature flag which uses a locally hosted
open model pack. See `docs/INDIA_RESIDENCY.md`.

## Data principal rights

| Right         | How to exercise                              |
|---------------|----------------------------------------------|
| Access        | `POST /api/v1/compliance/dsar/access`        |
| Correction    | `PATCH /api/v1/users/{id}`                   |
| Erasure       | `POST /api/v1/compliance/dsar/erase`         |
| Grievance     | Email `grievance@agenticorg.ai`              |
| Nomination    | via UI: Settings → Personal Data → Nominee   |

Response SLA: 30 days (DPDP s.13).

## Children's data

By default, signup is blocked for users under 18. Parental consent
flows are available on request for specific customer verticals.

## Significant Data Fiduciary obligations

If a customer's deployment processes the personal data of more than
1 million Indian users, they should self-declare as a Significant
Data Fiduciary and the following additional controls apply:
- Appointment of a Data Protection Officer (DPO).
- Annual Data Protection Impact Assessment (DPIA).
- Independent audit (AgenticOrg supports customer audits).

## Grievance redressal

- **Grievance officer**: grievance@agenticorg.ai
- **Response time**: 7 days acknowledgement, 30 days resolution
- **Escalation**: Data Protection Board of India (once notified)

## Sub-processors

See `docs/GDPR.md` section "Sub-processors". The list is identical.

## Related docs

- `docs/GDPR.md` — EU counterpart
- `docs/data-classification.md`
- `docs/SECURITY.md`
