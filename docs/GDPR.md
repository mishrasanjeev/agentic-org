# GDPR Compliance

AgenticOrg processes personal data on behalf of its customers. This
document describes how we meet the obligations of Regulation (EU)
2016/679 (GDPR) and the UK GDPR.

## Roles

- **Data Subject**: the natural person whose data is processed.
- **Data Controller**: the AgenticOrg customer (your employer).
- **Data Processor**: AgenticOrg Technologies Pvt Ltd.

The customer decides *what* data is processed. We only process it per
the customer's documented instructions (captured in the Data
Processing Agreement — see "DPA" below).

## Lawful bases we rely on

- Performance of a contract (Art. 6(1)(b)) — all core product usage.
- Legitimate interest (Art. 6(1)(f)) — security logging, fraud
  prevention, service improvement metrics.

We do not rely on legitimate interest to process Special Category data
(Art. 9). Customers handling healthcare data should additionally
review `docs/HIPAA.md`.

## Data subject rights — how we honor each

All data subject rights are surfaced via the DSAR endpoints at
`api/v1/compliance`. Customer admins can also invoke them via the
Compliance tab in the web UI.

| Right                     | Endpoint                                            | Response time |
|---------------------------|-----------------------------------------------------|---------------|
| Access (Art. 15)          | `POST /api/v1/compliance/dsar/access`               | 30 days       |
| Rectification (Art. 16)   | `PATCH /api/v1/users/{id}`                          | immediate     |
| Erasure (Art. 17)         | `POST /api/v1/compliance/dsar/erase`                | 30 days       |
| Restriction (Art. 18)     | `POST /api/v1/compliance/dsar/restrict`             | 30 days       |
| Portability (Art. 20)     | `POST /api/v1/compliance/dsar/export?format=jsonld` | 30 days       |
| Objection (Art. 21)       | Contact `privacy@agenticorg.ai`                     | 30 days       |

Erasure is executed via `audit/dsar.py` which cascades across
customer-owned tables and writes an immutable audit record. Backups
older than 30 days are not rewritten — they expire per the standard
retention policy. See `docs/BACKUP_AND_DR.md`.

## Sub-processors

| Sub-processor   | Purpose                        | Region        |
|-----------------|--------------------------------|---------------|
| Google Cloud    | Hosting, Cloud SQL, GCS        | asia-south1   |
| Anthropic       | LLM inference (Claude)         | us-east-1     |
| Stripe          | USD payments                   | us            |
| PineLabs Plural | INR payments                   | ap-south-1    |
| Composio        | Third-party connector OAuth    | us            |

An updated list is maintained at
`https://agenticorg.ai/legal/subprocessors`. Customers can subscribe
for notifications of changes.

## Cross-border transfers

Production data is stored in `asia-south1` (Mumbai). If a customer
selects the EU data residency add-on, data is stored in `europe-west1`
(Belgium) and LLM inference is routed to Anthropic's EU endpoint.

Transfers to non-adequate countries (e.g., the United States for LLM
inference) rely on Standard Contractual Clauses (SCCs) as per Annex I
of the DPA.

## Breach notification

- Internal escalation: within 1 hour of detection.
- Customer notification: within 24 hours via email + status page.
- Supervisory authority: within 72 hours (Art. 33), handled by our DPO.
- Affected data subjects: per customer instructions + statutory
  requirements.

Our breach runbook is in `docs/BACKUP_AND_DR.md`.

## Data Processing Agreement (DPA)

Our DPA template is available at
`https://agenticorg.ai/legal/dpa`. Signing is required before the
first production invoice for any EU customer.

## DPO contact

Questions about this policy, or to exercise a right:
- Email: `privacy@agenticorg.ai`
- Post: AgenticOrg Technologies, Bangalore, India

The DPO reports directly to the board.

## Related docs

- `docs/DPDP_ACT.md` — India DPDP Act compliance
- `docs/HIPAA.md` — healthcare compliance
- `docs/data-classification.md` — how we classify personal data
- `docs/SECURITY.md` — security controls
