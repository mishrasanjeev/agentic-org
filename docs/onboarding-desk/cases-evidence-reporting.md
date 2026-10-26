# Merchant onboarding desk: cases, evidence and reporting

When an applicant submits, the desk opens a governed case from the [application](applications.md). From there the governed-case workflow runs:
- the checks through the configured verification provider;
- the policy result and the underwriting memo;
- the review queue;
- the decision, which a person makes and records as a decision grant;
- the signed case push to the partner.

The desk adds an evidence pack per application that can be checked offline, and a report of the funnel.

Nothing here approves or declines an application. Every approval and every decline is a person's decision on the governed case.

## Opening the case

**What it needs.** An application can open a case once it is submitted and governed cases are on for the tenant (`governed_cases.enabled`).

**What the case gets:**
- the `business_case` application built from the answers;
- the case policy for the jurisdiction: `business_onboarding_us` in the United States and `business_onboarding_ca` in Canada;
- the configured verification provider (`AGENTICORG_CASE_PROVIDER`).

**One case per application.** The case reference is stored on the application in the same transaction that creates the case, so an application never has two cases. Opening it again returns the same case.

**Two ways to open it.**

- **By hand.** `POST /onboarding-desk/applications/{id}/case` with `{"investigate": true}` to start the checks straight away. It answers `{"case_ref", "created", "investigation_scheduled"}`.
- **On its own.** `PUT /onboarding-desk/case-settings` with:

  | Setting | Effect |
  |---|---|
  | `auto_open` | Opens the case when the applicant submits; the hand-off runs on the workflow queue |
  | `investigate` | Starts the checks as soon as the case is open |

  Both are off by default.

**When it is refused.** A refusal, such as governed cases being off, a jurisdiction with no case policy, or an application the case schema refuses, is returned with its code. It is also kept on the application as `case_error`, so an operator can see why and open the case by hand once the cause is fixed.

**Finding the application from a case.** `GET /onboarding-desk/cases/{case_ref}` returns the application, the lead with its `external_ref`, and the case's state, recommendation, tier and decision. A partner receiving the case push uses it to match the case to its own record. The application view (`GET /onboarding-desk/applications/{id}`) carries the same case summary.

### Case policies

The shipped case policies are examples. `business_onboarding_ca` has the same rules as the US example: registry match and status, the Business Number match, ownership reconciliation, screening and web presence. Like the others, it has not been reviewed by a compliance owner, and strict runtimes refuse it. Before real use, a compliance owner reviews and adapts it, sets `status: production`, and the reviewed file goes in `AGENTICORG_CASE_POLICY_DIR`. See [policy authoring](../policies/authoring.md).

### The hand-off to the partner

Approved merchants reach the partner through the governed case's signed push (see [case hand-off](../governance/case-hand-off.md)):
- an outbox written in the same transaction as the decision;
- HMAC signatures with key rotation;
- retries with backoff until the partner acknowledges.

## Evidence packs

`GET /onboarding-desk/applications/{id}/evidence` downloads the application's evidence pack as JSON. A pack has three parts.

**`sections`**

| Section | Holds |
|---|---|
| `application` | Status, merchant type, country, region, language, times, case reference, every answer and where it came from (web, text message or a document) |
| `lead` | Source, external reference, business name, masked contact details, status |
| `consents` | Every consent: channel, the exact wording and its SHA-256, method, where and when it was captured, whether in writing, expiry, withdrawal |
| `contact_attempts` | Each outreach attempt: channel, kind, status, refusal reason, provider reference |
| `transcript` | The conversation, both directions |
| `documents` | Each upload's item, name, type, size, read result and SHA-256 |
| `handoffs` | Requests for a person and who resolved them |
| `case` | The governed case: application, subject, policy result, recommendation, screening results and dispositions, decision with its approvers and decision grants |
| `case_evidence` | The Grantex evidence service's signed export of the case's agent runs (`included`, with its root and anchor hash), or `off`, `unavailable` with the reason, or `no_case` |

**`manifest`.** The schema (`agenticorg.onboarding_evidence/1`), tenant, application, time, and the SHA-256 of each section's canonical JSON. Canonical JSON is UTF-8, with sorted keys and no whitespace.

**`signature`.** Ed25519 over the manifest's canonical JSON, with its key id. It is null when no signing key is configured.

Document contents are not in the pack; each document's SHA-256 binds it, and it can be downloaded from the application.

### Signing and checking

Set `AGENTICORG_ONBOARDING_DESK_EVIDENCE_SIGNING_KEY` to a base64 Ed25519 seed of 32 bytes. Keep it in the secret manager. `GET /api/v1/onboarding-desk/evidence-key` publishes the public key and its id; it answers 404 `evidence_unsigned` when no key is set.

To check a pack offline:

```bash
python scripts/verify_onboarding_evidence.py onboarding-evidence-<id>.json --public-key <public_key>
```

The script recomputes every section digest and checks the signature. It exits 0 when the pack verifies, and lists each problem otherwise:
- a changed section;
- a missing section;
- a signature that does not verify;
- a different key.

## Reporting

`GET /onboarding-desk/reports/summary?since=<ISO time>&until=<ISO time>` reports on the leads created in the period: by default the last 30 days, and at most 366 days.

| Figure | Means |
|---|---|
| `funnel` | Leads by status, applications by status, cases opened, cases by state, approved and declined |
| `hours_to_submission` | Median and 90th percentile hours from lead to submitted application |
| `hours_to_decision` | Median and 90th percentile hours from submitted application to decision |
| `consent_coverage` | Share of leads with a current consent on any channel, and on each channel |
| `checks_per_merchant` | Provider calls the case agents made per case (mean and maximum), and screening results per case |

**Cohort.** Every figure follows the same cohort, the leads created in the period, from first contact to decision.

**Truncation.** A period with more than 20,000 leads is marked `truncated`.

## Records

Migration `v6z99_onboarding_cases` adds three columns to `onboarding_desk_applications`, with a unique index on the tenant and case reference:

| Column | Holds |
|---|---|
| `case_ref` | The governed case opened from the application |
| `case_opened_at` | When it was opened |
| `case_error` | Why opening it last failed |

## Who may do what

| Action | Needs |
|---|---|
| Read case settings, look up a case, download an evidence pack, read the report | `audit:read` |
| Open a case | `approvals:write` |
| Change the case settings | `approvals:write` and a tenant administrator |
| Read the evidence key | Nothing; it is public |
