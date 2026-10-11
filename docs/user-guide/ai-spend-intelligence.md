## What it answers

AI spend intelligence keeps a priced, attributed record of the AI work a deployment does and checks it against what each provider invoices. This guide is for the administrators who set it up and reconcile the figures, and the auditors who read and check them. The CFO role and the other domain roles are not readers of tenant-wide or commercial figures (see [Who can read what](/docs/ai-spend-intelligence#who-can-read-what)).

| Question | Where the answer comes from |
| --- | --- |
| Which business unit, department, team or cost centre used which model, and what did it cost in rupees? | Usage records and daily rollups |
| How much of the month's spend is not attributed to the organisation, and why? | Coverage: the attributed share, the unattributed share and its reasons |
| Does each provider's invoice agree with what was metered, priced at the rates you agreed? | Invoice import and reconciliation within the 1% match tolerance |
| Does the month meet Gate 1: attributed, priced, converted to INR and reconciled? | The Gate 1 status, read together with the month's meter gaps |

The feature is off by default, and phase 1 is API-only: there is no spend screen in the app. Every request in this guide goes to `/api/v1/spend/...` with an authenticated session, and the values in the examples are illustrative. The figures are what was metered and priced with the rate cards you entered. They are not a provider's bill, a tax figure or an accounting entry. Usage records are a separate source: the existing cost ledgers, the model-call records and the screens that read them stay as they are, and `GET /spend/coverage/ledgers` sets the two side by side with the expected differences listed.

## Turning it on

Spend intelligence is controlled by one deployment setting. While it is off, `GET /api/v1/spend/status` answers with `enabled: false`, every other spend address answers 404 `spend_disabled` before the request body is read, nothing is metered or written, and no existing behaviour changes.

| Setting | Default | What it controls |
| --- | --- | --- |
| `AGENTICORG_SPEND_INTELLIGENCE_ENABLED` | off | The whole feature |
| `AGENTICORG_SPEND_REPORTING_TIMEZONE` | `Asia/Kolkata` | The reporting day: FX lookup, rollups, coverage and the Gate 1 attribution month |
| `AGENTICORG_SPEND_PROVIDER_BILLING_TIMEZONES_JSON` | empty | The zone each provider closes its billing day in; it dates rate cards, commitments and the reconciliation month |
| `AGENTICORG_SPEND_SWEEPS_ENABLED` | on | The scheduled spend jobs (partition check, FX settlement, commitment recompute, storage samples, GPU allocation, job sweep); no effect while the feature is off |
| `AGENTICORG_SPEND_GPU_POOLS_JSON` | empty | Standing in-house GPU pools whose node hours are shared out to the calls that used them |
| `AGENTICORG_SPEND_AUDIT_COMMERCIAL_FILTER` | on | `GET /audit` hides the audit rows of rate cards, commitments, invoices, reconciliations and spend jobs from readers other than an administrator or auditor, whether the feature is on or off (see "Who can read what" below); off restores the previous `GET /audit` for every reader |

The tables come from four migrations, `v6z79_spend_reference`, `v6z80_spend_usage`, `v6z81_spend_gpu` and `v6z82_spend_reconciliation`. Apply them before the release that turns the switch on; strict runtimes do not create tables at start-up (see [Self-hosting](/docs/self-hosting)). With the feature on, a deployment refuses to start when the reporting zone or a provider billing zone cannot be loaded, or when the GPU pool setting does not parse.

**Who can write.** Every change (reference data, imports, maintenance jobs, invoice imports, reconciliations and acceptances) needs a tenant administrator signed in as a person. The server checks the administrator scope and then confirms an active administrator of the tenant in the database. API keys and agent tokens are refused, and `approvals:write` alone, which developers and domain heads hold, is not enough. Each change is audited with the acting administrator's id in the same transaction. Metering can be paused for one tenant with the feature flag `spend.metering_paused`; paused usage is not written and is counted as a `paused` meter gap.

```flow
Apply the migrations | Run v6z79 to v6z82 in the release before the one that turns the switch on.
Turn on the switch | Turn on the spend switch from the settings table above and check that the reporting and billing zones load.
Confirm the status | GET /api/v1/spend/status answers enabled true and says whether this process's writer started.
Load reference data | Build the organisation tree, map sources, then enter rate cards, FX rates and commitments.
Watch coverage | Read coverage and meter gaps; add mappings and rate cards, then re-attribute and restate the records already written.
Reconcile the first month | Import each provider's invoice, reconcile the month and read the Gate 1 status.
```

## Setting up the reference data

Reference data is what spend is measured against. Every kind can be written one row at a time, and the organisation tree, mappings, rate cards and FX rates can also be imported from a CSV or JSON file (`POST .../import`, at most 2 MiB and 5,000 rows). Add `dry_run=true` to an import to see exactly what it would create, update or reject without writing anything. The examples below omit the `Authorization` header that every request carries.

### Organisation tree

A node has your own code (such as `CC-4120`), a name, a kind, a parent and an optional owner. Only a group may be a root. A node is never deleted: setting `active` to false deactivates it, and its children keep rolling up through it.

| Kind | May sit under |
| --- | --- |
| `group` | nothing (a root) or a group |
| `business_unit` | a group or a business unit |
| `department` | a group, a business unit or a department |
| `team` | a department or a team |
| `cost_centre` | a group, a business unit, a department or a team |

```http
POST /api/v1/spend/org-nodes
Authorization: Bearer <administrator session token>
Content-Type: application/json

{"code":"CC-4120","name":"Card disputes","kind":"cost_centre","parent_code":"DEPT-CARDS"}
```

### Source mappings

A mapping ties a spend source to a node, a product line and a use case. Sources are an `agent`, a `workflow`, an `application` (such as `chat`, `voice`, `knowledge` or `documents`) or a legacy `cost_center` or `department` label that agents carry today. A mapping is turned off with `active: false`, never deleted.

```http
PUT /api/v1/spend/mappings
Content-Type: application/json

{"source_type":"agent","source_ref":"<agent id>","org_node_code":"CC-4120","product_line":"cards","use_case":"dispute-triage"}
```

Attribution is worked out on the server from the agent's configuration, these mappings and the tree. The department, cost-centre, business-unit and use-case labels a caller sends with a run are ignored, so a caller cannot charge its usage to someone else's cost centre.

### Rate cards in rupees or the provider's currency

A rate card prices one provider, usage type, model, unit and source (`contract` or `list`) from `effective_from` until `effective_to`, in the provider's billing dates. A rate change is a new card with a later start, so history keeps the rate of its day; two active cards of one key may never overlap.

```http
POST /api/v1/spend/rate-cards
Content-Type: application/json

{"provider":"openai","usage_type":"llm_tokens","model_sku":"example-chat-model","unit":"1m_input_tokens","unit_price":"212.50","cached_unit_price":"106.25","currency":"INR","effective_from":"2026-10-01","source":"contract","reference":"MSA-2026-17"}
```

- **Which card prices a call.** A model-specific card comes before a provider default; within one level, a cached-token price comes before the plain input price; then a contract card comes before a list card; then the latest start.
- **Cached and batch prices.** An input-token card may carry a cached-input price. Any card may carry a batch discount percentage, but phase 1 records carry no batch flag, so the discount is stored but not yet applied in metering, price quotes or reconciliation.
- **Volume tiers.** A card may carry up to 20 tiers, `graduated` or `all_units`. Usage is priced at the base price; the tiers are applied per contract and billing month in reconciliation.
- **Adding a card later.** A new card prices usage metered from then on. Records already written stay unpriced, or fallback-priced, until you run `POST /spend/usage/restate` for the provider and its billing days (at most 92 per job, with a reason); `include_unpriced` is on by default, so the job re-prices those records with the cards as known now.
- **No card.** Model tokens fall back to the deployment's list prices in USD and are marked as fallback-priced. In-house models, embeddings, OCR and Whisper price at zero unless you add a card; in-house model serving is charged through GPU node hours. Anything else with no price is **unpriced**: it has no amount, never zero, and is counted.
- **Corrections.** A card that has priced usage changes only through `POST /spend/rate-cards/{card_id}/correct` with a reason. The correction queues a restatement of the records the old card priced, in the same transaction.

`GET /api/v1/spend/price?provider=openai&usage_type=llm_tokens&unit=input_token&quantity=120000&model=example-chat-model&on=2026-10-15` prices a usage the way metering will, so you can check a card before usage arrives.

### FX rates to INR

Rates are kept per currency and date as the rate to INR. A record converts on its reporting date. With no rate that day (a holiday or a rate not yet published), the latest earlier rate converts and the record is marked `fx_estimated`; with no rate at all the record is `unconverted`. A new or changed rate queues an FX settlement that converts those records again with the rate of their day.

```http
PUT /api/v1/spend/fx-rates
Content-Type: application/json

{"rate_date":"2026-10-01","currency":"USD","rate_to_inr":"84.10","source":"reference"}
```

### Commitments

A **quantity** commitment names a provider, usage type, optional model, card unit and quantity, and may carry an overage price. A **money** commitment names a provider, an optional usage type and an amount in a currency. Drawdown is counted from usage records in time order by a recompute job; a record past the capacity is flagged as overage, and the overage price is applied in reconciliation, not on the record.

```http
POST /api/v1/spend/commitments
Content-Type: application/json

{"provider":"openai","kind":"quantity","usage_type":"llm_tokens","model_sku":"example-chat-model","unit":"1m_input_tokens","committed_quantity":"500","period_start":"2026-10-01","period_end":"2027-04-01","overage_unit_price":"240.00","overage_currency":"INR"}
```

## What gets metered

Each billable quantity in one unit becomes one usage record, with the same pricing, attribution and daily rollups whatever produced it.

| Usage | Record unit | Where it is counted |
| --- | --- | --- |
| Model calls | input, cached input and output tokens | Every model call through the router and the agent graph, plus four direct callers; one estimated `token` record when a provider does not split its count |
| Embeddings | embedding tokens, estimated from characters | Knowledge ingestion, re-indexing, search and RPA ingestion |
| OCR pages | pages | Document processing, knowledge uploads and ingestion that extracted a file itself |
| Speech minutes | audio minutes | Each transcription by the in-house Whisper engine or Deepgram, metered when the engine returns, so a later redaction, encryption or save failure does not lose minutes already transcribed |
| Priced tool calls | calls | A successful connector tool call, only when a rate card prices that tool |
| Storage | GB-days | A daily sample of each tenant's knowledge, document, document-processing and recording storage |
| In-house GPU | GPU node hours | Each closed pool hour, shared over the calls of the pool's models by tokens |

A record holds identifiers, counts and amounts only, never a prompt, a response, a document or customer data. It is append-only: its event fields never change, and only audited maintenance jobs revise its price, FX, attribution or commitment fields.

**The meter does no database work or waiting on the call path.** The hook builds the call's usage in memory and puts it on a bounded in-process queue. If metering fails, the failure is logged and counted and the call carries on unchanged. A writer thread in each process writes the queue in batches; a batch it cannot write is handed to a retry task with the same keys, so a retry never writes a record twice. Usage that still cannot be written is counted as a meter gap (`GET /spend/gaps`), and backfill can recreate model-call records from the model gateway's records while those exist.

**How attribution is chosen.** The first rule that matches wins and is stored on the record as its `attribution_path`:

| Situation | Order of rules |
| --- | --- |
| A call made by an agent | the agent's mapping; its legacy cost centre (mapping, then a node with the same code); the application's mapping |
| A workflow step with no agent | the workflow's mapping; the application's mapping; the initiating user's department |
| Anything else | the application's mapping; the initiating user's department |

A legacy label that names no node stops there: the record is kept and counted as unattributed with the reason `unknown_label`, never moved to a broader mapping. The business unit stored with a record is the nearest business unit at or above its node.

## Reading spend

| Read | Window | What it returns |
| --- | --- | --- |
| `GET /spend/usage` | up to 31 days | Records a page at a time, filtered by usage type, provider, node, agent, unattributed or unpriced |
| `GET /spend/rollups` | up to 366 days | Sums per day or per one dimension (node, business unit, use case, provider, model, agent and more), per currency and in INR |
| `GET /spend/coverage` | up to 366 days | Per day and for the period: the attributed and unattributed shares, unpriced and unconverted records, pending FX and gaps by reason |
| `GET /spend/gaps` | up to 92 days | Usage that could not be metered, by day, usage type and reason |
| `GET /spend/coverage/ledgers` | up to 31 days | Usage beside the existing cost ledgers and model-call records, with the expected differences |

The **attributed share** is the INR amount attributed to a business unit, department, team or cost centre over the whole INR amount. Attribution to a `group` node is reported separately as `group_share` and does not count. Records with no INR amount (unpriced or unconverted) count only in the record-count share and are listed beside it, so their volume stays visible. Shares are shown to six decimals and rounded so they never look better than they are.

| Unattributed reason | What it means | What to do |
| --- | --- | --- |
| `no_mapping` | No rule matched | Add a mapping for the agent, workflow or application |
| `unknown_label` | A legacy cost-centre or department label names no node | Map the label or create a node with its code |
| `inactive_node` | The matched node is deactivated | Reactivate it or move the mapping |
| `no_source` | A system call with no user | Map the application |
| `resolver_failed` | A database failure during resolution | Check the logs; the record was still written |

After adding mappings or nodes, `POST /spend/usage/reattribute` resolves the still-unattributed records again. Long operations like this one are maintenance jobs: the request answers `202` with a job id, one job of a kind runs at a time per tenant, and `GET /spend/jobs/{job_id}` reports its status and result. The other jobs rebuild rollups, backfill model calls, restate prices, settle FX and recompute commitments.

## Reconciling invoices

**Import the invoice.** `POST /spend/invoices/import` takes one provider's invoice or billing export for one billing month, in the provider's own billing zone. An invoice is stored whole or refused whole: one bad line refuses the file with 422 `invoice_rejected`, listing every refused line and its reason, because a partial invoice would reconcile against a wrong total.

```http
POST /api/v1/spend/invoices/import?provider=openai&period=2026-10&invoice_ref=INV-2026-10-0042&currency=USD&dry_run=true
Content-Type: multipart/form-data

file: invoice-2026-10.csv
```

```csv
line_kind,usage_type,model_sku,unit,quantity,amount
usage,llm_tokens,example-chat-model,1m_input_tokens,412.5,1031.25
usage,llm_tokens,example-chat-model,1m_output_tokens,96,960
tax,,,,,358.43
```

| Line kind | In the comparison |
| --- | --- |
| `usage` (the default) | Yes |
| `credit`, `tax`, `fee` | No, reported beside it |
| `commitment` (shortfall or prepaid charges) | No, reported beside it |

A second current invoice with the same reference is refused unless `replace=true`, which supersedes the earlier one; both are kept. Several references for one provider and month, such as two accounts, are summed. A month holds at most what a reconciliation can store: an import that would take the month's current invoices past that is refused with 413 (`too_many_rows`, `period_amount_too_large` or `period_quantity_too_large`), dry runs included.

**Run the reconciliation.** `POST /spend/reconciliations` with `{"provider":"openai","period":"2026-10"}` compares the provider's month with its current invoices and answers the run with its items. Only usage billed to the tenant's own key is compared (usage whose paying account could not be inferred is included and counted); usage on the platform's key is reported separately, and in-house serving, storage and GPU hours have no provider invoice. Each item carries two figures in the invoice currency:

- **stored**: the amounts as metered;
- **re-priced**: the same usage priced again with the cards in force on each billing day as known now.

The month's volume tiers and any commitment overage are worked out for each figure from its own base: the stored figure from the cards that priced the records, the re-priced figure from the cards in force now. The run reports both adjustment totals (`stored_adjustments_amount`, `repriced_adjustments_amount`).

An item is within tolerance only when both figures are known and each is within 1% of the invoice. Otherwise it needs review. So a card entered or corrected after metering leaves its items needing review until a restatement brings the records to the contracted rates. A run goes `stale` when anything it read would now read differently: late records, a card, an alias, an FX row or the month's invoices.

**Accept a difference.** An item that needs review can be accepted with a reason of 10 to 500 characters (`POST /spend/reconciliations/{reconciliation_id}/items/{item_id}/accept`). The acceptor must be an active tenant administrator other than whoever imported one of the run's invoices (409 `same_actor`); this is the maker-checker rule, and a tenant with a single administrator cannot accept anything. Every acceptance is audited with the reason, both variances and the actor. An accepted difference is recorded, but it does not meet Gate 1.

```flow
Check the month's reference data | Confirm the month's rate cards, commitments and FX rates are entered and settled. | Owner: Spend administrator | Human decision: Decide whether any contract change dated inside the month needs a correction and restatement first. | If blocked: Unpriced records stay listed in coverage until a card covers them and a restatement re-prices them; unconverted records convert when the settlement a new FX rate queues has run.
Review coverage | Read the month's coverage and meter gaps and clear unattributed spend with mappings and re-attribution. | Owner: Spend administrator, reviewed by an auditor | Human decision: Agree which unattributed reasons are fixed now and which are explained. | If blocked: A share below 0.98 or any unpriced record keeps attribution unmet.
Import the provider invoice | Dry-run the invoice file, then import it for the provider's billing month. | Owner: Administrator A, the importer | Human decision: Confirm the invoice reference, currency and billing month against the provider document. | If blocked: 422 invoice_rejected lists every refused line; nothing is stored until the file is corrected.
Run the reconciliation | Reconcile the provider's month and read each item's stored and re-priced variance. | Owner: Spend administrator | Human decision: Decide whether a difference is a metering, pricing or invoice question. | If blocked: 409 invoice_missing when the month has no current invoice; 422 currency_mismatch when its invoices differ in currency.
Resolve or accept differences | Correct cards and restate, or accept an explained difference with a written reason. | Owner: Administrator B, not the importer | Human decision: Accept only a difference that is explained and evidenced outside the system. | If blocked: 409 same_actor when the importer tries to accept; the item stays open.
Read the Gate 1 status | Request the month's gate and meter gaps and keep both responses with the month's evidence. | Owner: Auditor or administrator | Human decision: Treat the month as reconciled only when gate_met is true and the meter gaps are explained. | If blocked: Missing, stale or accepted reconciliations are listed by provider and keep the gate unmet.
```

## The Gate 1 status

`GET /api/v1/spend/gate?period=2026-10` answers whether a month meets Gate 1. It is met only when both halves are met.

| Half | Met when | Reasons or lists when not met |
| --- | --- | --- |
| Attribution (the reporting month) | The attributed share is at least 0.98, and no record is unpriced, unconverted or waiting for an FX rate | `below_target`, `no_priced_usage`, `unpriced_usage`, `unconverted_usage`, `fx_pending` |
| Reconciliation (each provider's billing month) | Every provider with a current invoice or tenant-billed priced usage has a current, fresh run within tolerance | `missing_reconciliations`, `stale_reconciliations`, `accepted_exceptions` |

```json
{
  "period": "2026-10",
  "gate_met": false,
  "attribution": {"attributed_share": "0.991204", "target": "0.98", "met": true, "reasons": []},
  "reconciliation": {"met": false, "missing_reconciliations": ["anthropic"], "stale_reconciliations": [], "accepted_exceptions": [], "tolerance_pct": "1.00"},
  "backfill_source": "model_gateway_records"
}
```

The rules are deliberately strict: no unpriced record is allowed, group attribution does not count, an accepted difference does not meet the gate, and an item needs both figures within 1%. Shares are shown rounded toward zero and variances away from it, while the verdicts use the exact values, so a share of 0.979996 is shown as `0.979996` and is not met. An invoiced provider with no metered records is listed too, because it can point to a broken hook, a provider-name mismatch or usage on a key the tenant did not pay for. The response repeats `backfill_source`, so you know whether lost model-call writes can still be recreated before you trust the month.

Gate 1 does not check everything. Meter gaps are reported beside the attribution figures but do not fail the gate: usage that could not be written or was lost at shutdown, tool calls that no card prices, and usage paused for the tenant with `spend.metering_paused`, which is not in the share at all. Storage, in-house GPU hours and in-house model serving have no provider invoice, so they are never reconciled. Read `GET /spend/gaps` for the month before you treat its figures as complete.

```flow
Read the reasons | Start from attribution.reasons and the three reconciliation lists in the gate response.
Fix attribution | Add mappings or nodes, then run re-attribution for the month.
Fix prices and rates | After adding a card, run POST /spend/usage/restate for the provider's billing days (at most 92). A correction queues its own restatement, and a new FX rate queues its settlement.
Re-run reconciliation | Reconcile every missing or stale provider again after the records change.
Read the gate again | Request the gate for the same month and compare it with the earlier response.
```

## Who can read what

Reads need the `audit:read` scope. Beyond that, one rule decides what each reader sees.

| Read | Administrator (a person) | Auditor (a person with unrestricted domains) | Anyone else |
| --- | --- | --- | --- |
| Usage records and rollups | Every record and row | Every record and row | Only records of agents they may see, and records with no agent; contract terms and user ids hidden; no rate-card or commitment grouping |
| Coverage, meter gaps, jobs and GPU allocations | The whole tenant | The whole tenant | Refused, 403 `tenant_wide_read_refused` |
| Rate cards, commitments and price quotes | Yes | Yes | Refused, 403 `commercial_read_refused` |
| Invoices, reconciliations and the Gate 1 status | Yes | Yes | Refused, 403 `commercial_read_refused` |
| Changes, imports, jobs and acceptance | Yes, signed in as a person | No | No |

"Anyone else" includes the CFO role and every other domain role, and machine credentials without the administrator scope. A finance team reads coverage, reconciliations and the gate through a person with the auditor or administrator role. Under the agent visibility rule a reader sees records with no agent, records of shared agents in their domains (every shared agent, for a machine credential) and records of their own personal agents. Tenant-wide figures are refused rather than filtered, because a filtered total would let a reader work out other agents' spend by comparing two views. Rate cards, commitments, prices, invoices, reconciliations and the gate are for a human administrator or auditor only: an API key or agent token is refused them whatever scopes it holds, since machine credentials can hold `audit:read` through agent grants. The audit rows of those writes and of spend jobs carry the same figures, so `GET /audit` hides them from the same readers, also after the feature is turned off, since audit rows are kept. A deployment can opt out with `AGENTICORG_SPEND_AUDIT_COMMERCIAL_FILTER=false`, which shows those rows to every audit reader again, as before.

## What is not in phase 1

- **No spend screen.** Phase 1 is API-only. The app's existing cost views, such as the Cost Dashboard at `/dashboard/costs` (administrators and CFOs; it has no sidebar entry), read the existing cost ledgers, not these usage records.
- **No spend dashboards, budgets, alerts, forecasts, statements or month close.** These belong to later phases; freezing closed months and statements belong to phase 2. Spend intelligence does not block or throttle a call because of its spend.
- **Some usage is not metered:** the embedding backfill command and seed data, the voice runtime's own speech-to-text and text-to-speech outside the speech store, and the tool gateway's own completion path.
- **No batch flag on records.** Reconciliation re-prices every group as non-batch usage.
- **No HTTP write for GPU hours.** Standing pools come from the deployment setting and metered hours from an operator command, because a tenant administrator must not set a cost shared by every tenant.

For the full behaviour, error codes and limits, read the [spend intelligence reference](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/finops/spend-intelligence.md).

Next: [Model Gateway](/docs/model-gateway), [Audit and monitoring](/docs/audit-and-monitoring), [Workspace and roles](/docs/workspace-and-roles), [API, SDK and MCP](/docs/api-sdk-mcp).
