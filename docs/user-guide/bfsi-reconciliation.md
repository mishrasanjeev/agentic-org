## Scenario and boundary

Example Bank's finance team wants a daily exception pack comparing a reviewed ledger export with bank/settlement statements. AgenticOrg assists with evidence extraction, comparison, break classification and a reviewable summary. Posting entries, releasing money and certifying balances remain the finance team's authorized process.

This is a configurable reference workflow, not a universally pre-integrated bank reconciliation product. The actual ledger/bank connector contract, matching logic and accounting policy must be implemented/reviewed for the institution.

## Prepare the sources

Use synthetic CSV/XLSX statements with transaction reference, date, amount, currency and relevant account mapping. Define timezone, date boundaries, sign conventions, duplicates and tolerances. Avoid real account numbers in training examples.

Upload the reconciliation procedure to Knowledge Base and verify search. Configure read-only exports/connectors with the correct company binding. Make sure the agent's selected tools actually read the required data; a finance template alone does not do so.

## Configure and run

For a small reproducible exercise, prepare these two **synthetic** datasets
using the schema expected by your reviewed read tool. Do not infer that uploading
CSV to Knowledge Base automatically connects the reconciliation agent to it.

```csv
source,reference,date,amount,currency
bank,DEMO-001,2026-09-28,1000.00,INR
bank,DEMO-002,2026-09-28,2500.00,INR
bank,DEMO-003,2026-09-28,750.00,INR
```

```csv
source,reference,date,amount,currency
ledger,DEMO-001,2026-09-28,1000.00,INR
ledger,DEMO-002,2026-09-28,2400.00,INR
ledger,DEMO-004,2026-09-28,500.00,INR
```

The reviewed expected result is one exact match (`DEMO-001`), an INR 100
amount difference (`DEMO-002`), one bank-only entry (`DEMO-003`) and one
ledger-only entry (`DEMO-004`). Bank total is INR 4,250; ledger total is INR
3,900. These figures are teaching assertions, not reconciled customer balances.
Require source-row references and validated calculations in the exception pack.

1. Review the available reconciliation agent or create a bounded finance candidate.
2. Specify output: matched items, unmatched items, reason, evidence reference and proposed follow-up.
3. For exact financial totals, require deterministic calculations/validated tool results; do not ask an LLM to invent or approximate amounts.
4. Build a manual workflow: read sources, validate schema, compare, prepare exception pack, human review.
5. Run a synthetic day with known expected matches and deliberate breaks.
6. Inspect source freshness, totals, duplicate handling and every exception.
7. Hand approved corrections to the accounting system through a separately authorized process.

## Process map

The comparison below assumes a reviewed read tool, mappings and deterministic
calculation. It does not imply an automatic ledger posting connector.

```flow
Read-only sources | Collect reviewed statement and ledger exports for one business period. | Owner: Finance data owner and approved read tool | If blocked: A late, partial or inaccessible source makes the period incomplete.
Validate and compare | Apply reviewed schema, currency, duplicate and matching rules with validated calculations. | Owner: Configured comparison and finance operator | If blocked: Corrupt rows, mismatched currencies or totals become exceptions, not forced matches.
Exception pack | List matched pairs and each break with source-row evidence and proposed follow-up. | Owner: Finance assistant | If blocked: Missing provenance or uncertain arithmetic must be checked before review.
Finance review | Recheck ambiguous matches and proposed corrections against the source records. | Owner: Authorized finance reviewer | Human decision: Approve, revise or reject each proposed correction through the institution's controls. | If blocked: Unresolved breaks remain open and cannot be reported as balanced.
Authoritative posting | Send approved corrections through a separately authorized accounting process. | Owner: Institution finance and accounting system | If blocked: A prepared pack is neither a posted entry nor a verified balance.
```

## Sample acceptance cases

| Input | Expected evidence |
| --- | --- |
| Same reference and amount | A documented matched pair |
| Missing ledger item | Unmatched source entry, not a guessed posting |
| Same amount in different currencies | No unsupported cross-currency match |
| Duplicate reference | Explicit duplicate exception |
| Corrupt/partial source | Failed validation or incomplete evidence, not balanced totals |

## Scheduling and operations

Schedule only after a manual run is reproducible. Confirm timezone, statement arrival, worker availability, rate limits and retry/idempotency. A late source must not be represented as a complete daily reconciliation.

Measure correctly identified breaks, false matches, review effort, source delays and reopened corrections. Compare against an independently reviewed ledger sample. Retest mappings after upstream changes.

Next: [Capability status and gaps](/docs/bfsi-capability-status), [Connectors](/docs/connectors), [Workflows](/docs/workflows), [Audit and monitoring](/docs/audit-and-monitoring).
