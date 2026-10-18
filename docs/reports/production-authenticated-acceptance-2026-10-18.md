# Pre-rollout Authenticated Production Acceptance: 18 October 2026

This snapshot preserves the initial test evidence before the corrective rollout.
It is not a live deployment inventory. Later rollout and exact-task replay
outcomes must be checked in the corrective release's deployment record.

## Scope and Controls

Synthetic, authenticated tests covered agents, durable workflows, document
ingestion/retrieval and an external read-only MCP tool. Existing tenant limits,
feature flags and channel credentials were retained. No calls or payments were
performed. This is scoped evidence, not whole-product release approval.

## Observed Results

| Path | Evidence | Outcome |
| --- | --- | --- |
| Agent management | Create, read, update, invalid-provider refusal, delete, deleted run/chat refusal, unknown-company scope refusal | Passed |
| Durable workflows | Two-step transformation, explicit failure for an unsupported transform, delayed continuation, graph/status and step counts | Passed |
| Knowledge | TXT, CSV, text PDF, DOCX, XLSX and PPTX upload, indexing, retrieval, excerpt and exact-source filtering | Passed |
| OCR | Real PNG and scanned-PDF extraction, indexing, known text and numeric code retrieval, citations and excerpts | Passed |
| Invalid knowledge input | Duplicate, corrupt replacement, retained original, unsupported video and empty document | Passed |
| Resource cleanup | Only resources created by this synthetic smoke run were removed | Passed |
| Remote MCP | Public documentation server discovery, unreviewed-tool refusal, one reviewed read-only tool call, real documentation response, cleanup | Passed |
| Real model reasoning | Expected arithmetic answer 18; UI showed 5, completed and 100% confidence; provider telemetry recorded a real call | Failed; investigation and corrected-production replay required |
| Original Telegram transport | Fresh uniquely labeled owner message matched the inbox and completed copy-workflow output; owner received the status reply; original connection retained | Passed transport; not a combined model-backed channel proof |
| Paused-agent execution refusal | Not invoked because installed refusal controls were to remain unchanged | Not tested |

The main authenticated smoke run recorded 90 passes and no failures, with
paused-agent execution separately skipped. MCP added five successful steps.
These counts do not include the independent failed reasoning acceptance.
OCR confidence was 94.83 for the PNG and 94.17 for the scanned PDF; confidence
is diagnostic metadata, not an accuracy guarantee for other documents.

## Quantity Redaction Defect

The exact synthetic task used counts `7, 11 and 13 items`, removing the
11-item box and two further items. The real local PII detector replaced the
first `11` with a date placeholder before the model received the task. This
reproduces loss of an arithmetic operand, but does not by itself prove the
entire cause of the model's wrong answer.

The narrow correction excludes only exact numeric spans within explicit count
phrases from `DATE_TIME` candidates. Actual dates, ambiguous numbers without
count context, explicit nearby birth/date/time labels and other entity types
remain protected, including identifiers
that overlap a count. No redaction mode or feature flag changes are required.
Regression coverage includes the exact runner message, count lists, genuine
dates, contact data, stronger identifiers and restoration round trips.

The common redactor is used by agent input processing and memory redaction.
The separate pseudonymisation path asks this detector only for person spans,
so this date-specific correction does not change that path's identity handling.

The full NLP replay also exposed partial recognition of reserved-domain email
addresses as UPI handles. Complete email matches now participate in the same
overlap resolution, preserving the entire protected value for round-trip
restoration. This tightens coverage; it does not disable an existing detector.

Adversarial scan tests also cover malformed long lists, long whitespace runs
and long email-like strings. Boundary-anchored matchers avoid repeatedly
rescanning each list element or local-part segment. Separate-process deadlines
guard this behavior without counting interpreter startup as parsing time.

Acceptance of the correction requires deployment and a bounded, exact-task
reasoning replay. Do not replace the initial failed evidence with
an easier task or combine independent Telegram/model passes into a claim of a
single model-backed channel run.

## Limits

No external customer data, provider account mutations, paid voice, payments,
unconfigured native providers, all-language OCR certification or production
fault injection was tested. The original Telegram route was not replaced.
Model price estimates are not invoice-confirmed charges. Credentials, workspace
identifiers, sender details and raw provider payloads are excluded from this
report.

The company-scope negative case used an unknown company reference. A full
independent two-live-tenant penetration test was not performed.
