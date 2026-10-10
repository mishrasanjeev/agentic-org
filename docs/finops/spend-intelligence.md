# AI spend intelligence: reference data, pricing, usage records and coverage

Behind `spend_intelligence_enabled` (default off; `AGENTICORG_SPEND_INTELLIGENCE_ENABLED`). Off,
`GET /spend/status` answers `enabled: false`, every other spend route is not found (the request
gate answers 404 `spend_disabled` before the body is read) and nothing is metered or written; no
existing path changes.

The feature keeps the reference data spend is measured against (the organisation tree, source
mappings, model aliases, rate cards, commitments and FX rates) and the pricing engine that prices
a usage at its date in the card's currency and in INR. It meters every model call into a usage
record with its server-owned attribution, keeps a daily rollup, and reports coverage: the share of
spend attributed to the organisation, the first measure of Gate 1. Non-token metering, invoice
reconciliation and the Gate 1 status build on it in later parts. The code is in `core/spend/`
(reference data: `org.py`, `mappings.py`, `rates.py`, `commitments.py`, `fx.py`, `pricing.py`,
`imports.py`, `audit.py`; usage: `context.py`, `tokens.py`, `meter.py`, `writer.py`,
`resolver.py`, `billing.py`, `rollups.py`, `maintenance.py`, `jobs.py`, `ledgers.py`,
`partitions.py`, `metering.py`), the routes in `api/v1/spend.py`, the tables in
`core/models/spend.py` and `core/models/spend_usage.py`, migrations `v6z79_spend_reference` and
`v6z80_spend_usage`, the tasks in `core/tasks/spend_tasks.py`.

## Two calendars

| Date | Zone | Used for |
|---|---|---|
| event date | `spend_reporting_timezone` (default `Asia/Kolkata`) | FX rate lookup, the rollup day, coverage, the Gate 1 attribution month |
| billing date | the provider's billing zone: `spend_provider_billing_timezones_json`, then the defaults (`gemini` closes in `America/Los_Angeles`), then UTC | rate-card and commitment dating, volume tiers, the reconciliation month |

In-house providers and platform storage bill in the reporting zone. An event at 23:00 UTC on
30 September is billed on 30 September by a UTC provider and reported (and converted) on
1 October in India. With the feature on, the deployment refuses to start when a zone cannot be
loaded. `spend_sweeps_enabled` (default on) gates the beat jobs (partition horizon, FX
settlement, commitment recompute, the job sweep); it has no effect while the feature is off.

## Organisation tree

A node has the organisation's own code (`CC-4120`: 1 to 64 of `A-Z 0-9 _ . : / -`, upper-cased,
unique per tenant), a name, a kind, a parent, an owner (a user of the tenant) and an active flag.
Kinds nest as follows; only a group may be a root, and a tree is at most 16 levels below its
root.

| Kind | May sit under |
|---|---|
| `group` | nothing (a root), a group |
| `business_unit` | a group, a business unit |
| `department` | a group, a business unit, a department |
| `team` | a department, a team |
| `cost_centre` | a group, a business unit, a department, a team |

A node is never deleted: `active: false` deactivates it (and stamps `deactivated_at`), and its
children keep rolling up through it. The database refuses to delete a referenced node. Every
parent change and every import take the tenant's org-tree advisory lock and check the whole
tree for a cycle, so two concurrent moves cannot build one together. A node's business unit is
the nearest business unit at or above it.

## Source mappings and legacy labels

A mapping ties a spend source to a node, a product line and a use case. Sources are an `agent`,
a `workflow`, a legacy `cost_center` or `department` (each by its id) or an `application`
(`agents`, `chat`, `voice`, `workflows`, `a2a`, `mcp`, `api`, `console`, `knowledge`,
`documents`, `speech`, `content`, `txn`, `system`). A mapping is upserted by source and turned
off with `active: false`, never deleted. Product lines and use cases are bounded lower-case
labels. Legacy cost-centre and department labels that agents carry today are resolved through
these mappings, else by matching their codes to node codes (see [Attribution](#attribution-server-owned));
an unknown label counts as unattributed, never dropped.

## Model aliases

An alias maps a model name as called (a dated name, a deployment name) to the SKU the tenant's
rate cards and invoices use, per provider. Pricing applies it before choosing a card, so a
called alias prices with the SKU's card. With no card for the SKU, the list fallback tries the
SKU and then the name as called, so an alias never leaves unpriced a call the list prices.
Aliases do not chain: an alias may not name another alias, and a SKU may not itself be an alias.

## Rate cards

A card prices one key `(provider, usage_type, model_sku, unit, source)` from `effective_from`
to `effective_to` (exclusive; empty is open-ended), in the provider's billing dates. An empty
`model_sku` is the provider-wide default for the usage type. `source` is `list` or `contract`.

| Usage type | Card units | Record units |
|---|---|---|
| `llm_tokens` | `1m_input_tokens`, `1m_output_tokens`, `1m_cached_input_tokens`, `1m_tokens` | `input_token`, `output_token`, `cached_input_token`, `token` |
| `embedding_tokens` | `1m_embedding_tokens` | `embedding_token` |
| `ocr_pages` | `ocr_page` | `ocr_page` |
| `speech_minutes` | `audio_minute` | `audio_minute` |
| `tool_calls` | `call` | `call` |
| `storage` | `gb_month`, `gb_day` | `gb_day` |
| `gpu_hours` | `gpu_node_hour` | `gpu_node_hour` |

A `1m_input_tokens` card may also carry a cached-input price. A card may carry a batch discount
(percent): a batch usage takes the discount of the card that priced it, and a blend discounts its
input and output shares each by its own card's; the recorded unit price stays undiscounted. A
card may also carry volume tiers (`[{"from_quantity": "0", "unit_price": "2.5"}, ...]` in card units,
ascending from 0, at most 20) with a tier mode (`graduated` or `all_units`). Usage is priced at
the base price; tiers are applied per contract key and billing month in reconciliation.

**Effective dating.** A usage is priced with the card in force on its billing date. A rate change
is a new card with a later start; history keeps the rate of its day. Two active cards of one key
may never overlap: the check runs under the key's advisory lock on every create, every change
of a date or the status, every correction and every import row. A list card and a contract card
of one key may coexist.

**Precedence.** The candidates of every way a record unit can be priced are ranked together:

1. a model-specific card beats a provider default, whatever its unit;
2. within one specificity, a card that prices cached tokens (a `1m_cached_input_tokens` card, or an
   input card's cached price) beats the input price with no discount;
3. then a contract card beats a list card;
4. then the order of the paths, then the latest `effective_from`.

So a provider-default cached price never reaches a model that has its own card. Unsplit `token`
usage is priced by a `1m_tokens` card or by a blend of an input card and an output card of the
same specificity and currency (input three to one, computed in `Decimal`), marked estimated and
recording both cards. `gb_day` usage may be priced by a `gb_month` card divided by the days in
the billing month.

**Fallback.** With no card, LLM tokens fall back to the deployment's list prices and
`AGENTICORG_MODEL_PRICE_OVERRIDES_JSON` (`core/governance/model_pricing.py`), in USD, marked
`fallback_list` or `fallback_override`. The fallback tables have no dates, so the price a record
was priced at is the one stored on it. A model with no price anywhere is **unpriced**: no
amount, never zero, counted.

**In-house.** With no card, the in-house providers (`ollama`, `vllm`, `local_embeddings`, `tei`,
`tesseract`, `faster_whisper`) price tokens, pages and minutes at zero in INR (`in_house`); their
cost is the GPU node hours a later part allocates. A tenant's card on an in-house provider wins.
GPU hours and storage need a card.

**Corrections.** Price fields change by `PATCH` only before the card starts and while no record
references it. Otherwise `POST /spend/rate-cards/{card_id}/correct` (with a reason of 10 to 500
characters) retires the card and inserts its replacement with the same key and start; records
priced by the retired card are restated by a job queued with the correction. `supersede: true` on a
create closes the open predecessor at the new card's start; backdating it over priced records
needs `restate: true`. Shortening a card's end into priced history needs `restate: true` too.
**Retired** means "replaced by a correction"; a contract ends with `effective_to`. A retired card
prices nothing.

`GET /spend/price` prices a usage the way metering will: `provider`, `usage_type`, `unit` (a
record unit), `quantity`, `model`, `on` (the billing date) and `fx_on` (the reporting date,
default `on`).

## Money and FX

Money is `NUMERIC` and `Decimal`, never a float. `amount = quantity / divisor * unit_price`,
rounded half-even to ten places; the INR amount is converted per record, never on a sum. JSON
carries amounts, prices and rates as decimal text.

FX rates are kept per `(currency, rate_date)` as the rate to INR, with a source (`reference`,
`manual`, `import`); INR has no row. A usage converts on its reporting date:

- INR converts at one; a zero amount converts to zero without a lookup;
- the date's rate converts exactly;
- with no rate that day (a holiday, a weekend, or a rate not yet published), the latest earlier
  rate converts and the record is marked `fx_estimated` with the rate's date;
- with no rate at all, the INR amount is empty and the record is marked `unconverted`.

`PUT /spend/fx-rates` upserts a rate and answers the rate it replaced and the FX settlement it
queued (`settle_job_id`). Writes of a currency's rates (a `PUT` and an import) take that
currency's lock first, so two writers of the same new rate never both insert it. Settlement of
estimated and unconverted records, and the `fx_in_use` rule for a rate settled records use, are
described under [Maintenance jobs](#maintenance-jobs).

## Commitments

A **quantity** commitment names a provider, a usage type, optionally a model, a card unit and a
quantity in card units (500 x `1m_input_tokens`), and may carry an overage price per card unit.
A **money** commitment names a provider, optionally a usage type, and an amount in a currency; it
has no unit to price overage by. Periods are half-open ranges of billing dates. Two active
commitments of the same provider, usage type, model, unit and kind may not overlap, on create and
on every change of the end or the status. Storage commitments are entered in `gb_day` (a GB-month
has no fixed number of GB-days). Drawdown is counted in record units from usage records by the
commitment recompute job (see [Maintenance jobs](#maintenance-jobs)); every create or change marks
the commitment for a full recompute and queues the job.

## Imports

Each kind of reference data takes a CSV or JSON file (`POST .../import`, `dry_run` query
parameter), at most 2 MiB and 5,000 rows; larger bodies are refused with 413 `import_too_large`
before they are read in full, and a 5,001st row with 413 `too_many_rows` (a JSON file as soon as
it is parsed, before its rows are copied). JSON is a list of objects or `{"rows": [...]}`; its
numbers are read as decimals, never binary floats, so a price or tier keeps every decimal place
it was given. CSV is UTF-8 (a byte-order mark is dropped) or Latin-1; header
names are trimmed and lower-cased; unknown columns are ignored. A file that cannot be read is
400 `bad_file`, a missing required column 400 `missing_columns`. An optional column absent from
the file leaves that field of an existing row as it is; an empty cell clears the field (an empty
`parent_code` makes a node a root), except `active`, which an empty cell leaves as it is.

| Import | Required | Optional | Upsert key |
|---|---|---|---|
| organisation nodes | `code, name, kind` | `parent_code, owner_user_id, active` | `code` |
| mappings | `source_type, source_ref` | `org_node_code, product_line, use_case, active` | `(source_type, source_ref)` |
| rate cards | `provider, usage_type, unit, unit_price, currency, effective_from, source` | `model_sku, effective_to, cached_unit_price, batch_discount_pct, volume_tiers, tier_mode, reference, supersede, restate` | `(provider, usage_type, model_sku, unit, source, effective_from)` among active cards |
| FX rates | `rate_date, currency, rate_to_inr` | `source` (default `import`) | `(currency, rate_date)` |

Every import answers `{"dry_run", "received", "created", "updated", "unchanged", "rejected"}`;
rejected rows carry their row number (the CSV header is row 1) and a reason. An organisation
import checks the whole result first: parents created later in the file, parent kinds, cycles,
depth and the existing children of a node whose kind changes; a row whose parent row was
rejected is rejected as `parent_rejected`. Rate-card rows follow the single-write rules (overlap
against the database and the file's earlier rows, `card_in_use`, `restate_required`). A dry run
reports exactly what would happen and writes nothing.

## Audit

Every change to reference data is written as signed `audit_log` rows (`event_type`
`spend.<resource>.<verb>`, the acting administrator's user id) in the same transaction as the
change. A single write records its changed rows with their before and after values; a
supersede records the predecessor's end as its own change. An import records a manifest: a
summary row (the counts and the uploaded file's sha256) and one row per 200 changed rows, every
changed row with its before and after values. Audit details hold identifiers, codes, counts and
reference-data values only. The rows of rate-card and commitment writes (`spend.rate_cards.*`,
`spend.commitments.*`) carry prices and committed amounts, so `GET /audit` shows them only to a
human administrator or auditor, the callers the commercial routes answer. That filter applies
whatever `spend_intelligence_enabled` says, because the rows stay after the flag is turned off.
It is applied only for a tenant that keeps a rate card or a commitment (spend rows are never
deleted, and their audit rows commit with them); every other tenant's audit query is unchanged.

## API

| Method and path | Who | Notes |
|---|---|---|
| `GET /spend/status` | `audit:read` | `enabled`, the reporting currency and zone, the vocabularies, the import bounds |
| `GET /spend/org-nodes`, `GET /spend/org-nodes/{node_id}` | `audit:read` | a node with its ancestors and business unit |
| `POST /spend/org-nodes`, `PATCH /spend/org-nodes/{node_id}`, `POST /spend/org-nodes/import` | administrator | |
| `GET /spend/mappings`, `GET /spend/model-aliases` | `audit:read` | |
| `PUT /spend/mappings`, `POST /spend/mappings/import`, `PUT /spend/model-aliases` | administrator | |
| `GET /spend/rate-cards` | administrator or auditor | `as_of` keeps the active cards in force on a billing date (`status=retired` lists retired ones) |
| `POST /spend/rate-cards`, `PATCH /spend/rate-cards/{card_id}`, `POST /spend/rate-cards/{card_id}/correct`, `POST /spend/rate-cards/import` | administrator | |
| `GET /spend/commitments` | administrator or auditor | with drawdown and what remains |
| `POST /spend/commitments`, `PATCH /spend/commitments/{commitment_id}` | administrator | |
| `GET /spend/fx-rates` | `audit:read` | |
| `PUT /spend/fx-rates`, `POST /spend/fx-rates/import` | administrator | |
| `GET /spend/price` | administrator or auditor | the price of a usage at a date |
| `GET /spend/usage` | `audit:read` | records of at most 31 days, a page at a time (`cursor`); filters `usage_type`, `provider`, `org_node_id`, `agent_id`, `unattributed`, `unpriced`; other people's personal agents hidden, user ids shown to administrators and auditors only |
| `GET /spend/rollups` | `audit:read` | sums per a dimension or per `day` (at most 366 days), amounts per currency and in INR; grouping by agent applies the agent visibility rule |
| `GET /spend/coverage` | `audit:read` | the Gate 1 attribution measure (at most 366 days) |
| `GET /spend/coverage/ledgers` | `audit:read` | usage beside the existing ledgers (at most 31 days) |
| `GET /spend/gaps` | `audit:read` | meter gaps (at most 92 days) |
| `POST /spend/rollups/rebuild`, `POST /spend/usage/backfill`, `POST /spend/usage/restate`, `POST /spend/usage/reattribute`, `POST /spend/fx-rates/settle`, `POST /spend/commitments/recompute` | administrator | `202` with a job id |
| `GET /spend/jobs`, `GET /spend/jobs/{job_id}` | `audit:read` | |

`GET /spend/status` also reports the usage limits (`usage_window_days` 31, `rebuild_days` 31,
`restate_days` 92), `backfill_source`, `partition_horizon` and this process's writer
(`started`, `pending`).

## Usage records

Every billable quantity in one unit is one usage record (`spend_usage_records`). A model call
gives up to three records (uncached input, cached input, output), or one estimated `token` record
when the provider did not split its count; the first record of a call carries `calls = 1`, so
calls are countable from the rollups. A record holds:

- the event: `event_time`, `event_date` (reporting zone), `billing_date` (the provider's zone),
  `usage_type`, `unit`, `quantity`, `provider`, `model` (canonical: the tenant's aliases applied),
  `quantity_estimated`, and its keys (`idempotency_key`, `source_ref`, and `correlation_ref`, a
  hash of the request's correlation id, never the id itself, which can be a client's request id);
- the price: `rate_card_id` (and `blend_card_id` for a blended price), `price_source`,
  `unit_price`, `amount` and `currency`, `fx_rate`, `fx_rate_date`, `amount_inr`, and the flags
  `unpriced`, `fx_estimated`, `unconverted`, `price_estimated`;
- the attribution: `agent_id` and `agent_version`, `org_node_id`, `business_unit_node_id`,
  `attribution_path` or `unattributed_reason`, `product_line`, `use_case`, `application`, `region`,
  `workflow_id`, `run_id`, `initiating_user_id` (an id, never a name), `environment`, `risk_tier`;
- `billing_account` (`tenant_key`, `platform_key`, `in_house`), commitment drawdown
  (`commitment_id`, `overage`, `overage_quantity`), and `revised_at`.

A record holds identifiers, counts and amounts only: never a prompt, a response, a document or
customer data.

**Append-only.** A record is never deleted and its event fields never change. Four audited
maintenance jobs may revise its derived fields, each setting `revised_at` and moving its rollup
contribution in the same transaction: FX settlement (the FX fields), restatement (the price and
FX fields), re-attribution (the attribution of records still unattributed) and the commitment
recompute (`commitment_id`, `overage`, `overage_quantity`).

## Attribution (server-owned)

Attribution is resolved on the server from the agent's configuration, the registry, the source
mappings and the organisation tree, never from a request: the department, cost-centre, business
unit and use-case labels a caller sends to the agents run route are ignored. The first matching
rule wins and is recorded in `attribution_path`:

| Situation | Order |
|---|---|
| an agent | the agent's mapping (`agent_mapping`); when the agent carries a legacy cost centre, its mapping (`cost_centre_mapping`), else the node whose code is the cost centre's code (`cost_centre_code`), else **unattributed, `unknown_label`**; the application's mapping (`application_mapping`); else `no_mapping` |
| a workflow, no agent | the workflow's mapping (`workflow_mapping`); the application's mapping; the initiating user's department; else `no_mapping` |
| anything else | the application's mapping; the initiating user's department; else `no_mapping` (`no_source` for a system call with no user) |

A user's department resolves through its mapping (`department_mapping`), else the node with its
code (`department_code`), else `unknown_label`. A legacy label that names no node stops
resolution: the record is kept and counted as unattributed with the reason, never silently moved
to a broader mapping. A matched inactive node gives `inactive_node`; a database failure gives
`resolver_failed`, and the record is still written. Department and cost-centre codes are unique
per company, organisation-node codes per tenant, so one code in two companies resolves to one
node.

The business unit is the nearest business-unit ancestor of the node, stored with the record. The
use case is the first of: the agent's (or workflow's) mapping, the application's mapping, the
registry's use case, the agent type, the call site's default, else `unattributed`. The product
line comes from the agent's (or workflow's) mapping, else the application's. The risk tier comes
from the registry, the environment from the deployment, the region from the tenant's data region.

**Retired agents.** On the metering path, an agent hint that names a missing, retired or deleted
agent is dropped, so a workflow author cannot charge spend to a retired agent's cost centre by
naming it; a workflow step binds only the id of an agent the loader found runnable. Backfill hints
come from signed gateway rows of real runs, so a backfilled record keeps an absent agent's id.

Resolutions are cached for 60 seconds per process and dropped on a local mapping, alias or tree
change; another process sees a change within 60 seconds, and re-attribution fixes records written
in that window. Every query carries the tenant predicate on every joined table.

**Entry points.** Each entry point binds its application for the span of its work: `agents` (the
run route, binding the run id, the agent version and the initiating user; the debugger; approval
resumes), `chat`, `a2a`, `mcp`, `voice`, `workflows` (each step, with the workflow and its run;
the re-planner), `content`, `speech` (the summary), `txn` (the narrative) and `console` (prompt
comparison, evaluation judges, the workflow and agent generators). The first binder wins, so a
speech summary stays `speech` inside the content service it calls.

## The model-call meter and the writer

The hook runs in `record_model_call`, the one funnel for the agent graph and the direct router,
after the gateway record is written. It is synchronous: it builds the call's events in memory
and puts them on a bounded in-process queue, with no I/O and no await on the call path. Any
failure is logged and counted (`hook_error`); the call proceeds unchanged. The time it adds is
measured (`agenticorg_spend_hook_seconds`). A call with no tokens is counted as a gap
(`failed_no_usage`); a router call whose primary attempt timed out with no response gets one
estimated input record from the prompt's length (`timeout_estimated`); a router call cancelled by
its outer timeout is counted, never estimated. Four direct model callers that bypass the router
(the run explainer, the feedback analyser, the SOP parser, the workflow re-planner) are metered
through `spend.note`.

One writer thread per process, with its own event loop and a two-connection engine, flushes the
queue every 200 ms in batches of up to 500 events, tenant by tenant, so it never competes with
request handlers for the shared pool:

- a **paused** tenant (the feature flag `spend.metering_paused`, a tenant row or the global row,
  with `enabled = true` and `rollout_percentage = 100`; read by the writer, never on the call path)
  has its events dropped and counted, taking effect within 30 seconds without a restart;
- a rollup day held by a **rebuild** makes that tenant's events wait for the next backoff step
  while other tenants are written; after two minutes they are spilled;
- a **transient** database error (a dropped connection, a pool timeout, a deadlock) is retried
  once, then the events are spilled;
- **any other** failure is logged, counted and spilled.

A **spill** hands events to the Celery task `persist_usage` on the `maintenance` queue, which
writes them idempotently with the same keys, one tenant per transaction. A transient failure
there (a lock or statement timeout and a deadlock included) is retried with backoff up to eight
times, each retry carrying only the tenants not yet written; events it finally cannot write are
counted (`spill_failed`) and recorded as gaps per tenant. At shutdown (the API lifespan, a worker's exit) the
writer stops, waits up to 5 seconds and spills what is left; what cannot be spilled is counted
as `shutdown_lost`. The only loss on the call path is a full queue (5,000 events), counted
globally and per tenant (`queue_full`).

### Writer alerts

`AgenticOrgSpendWriteFailures` fires when usage events have failed to be written for 15 minutes
(any reason but `paused`); `AgenticOrgSpendWriterBacklog` when an instance has held more than
4,000 unwritten events for 10 minutes. Check the `reason` label of
`agenticorg_spend_usage_write_failures_total`, the database and the `maintenance` queue; the
per-tenant meter gaps (`GET /spend/gaps`) show what was lost, and backfill can recover model calls
while gateway records exist.

## Billing accounts

`billing_account` says who paid: `tenant_key` (the tenant's own credential), `platform_key`
(the platform's, which a tenant without its own key runs on) or `in_house` (local models and
storage). The router notes the credential it resolved and the graph reads the credential the
runner prefetched; for other records the writer infers it: a tenant with an active or unverified
credential of its own for the provider pays itself, otherwise the platform does. Reconciliation
compares only tenant-billed usage with the tenant's invoice.

## Idempotency

A record is unique on `(tenant_id, idempotency_key, event_time)` and written with
`INSERT ... ON CONFLICT DO NOTHING RETURNING`, so a retry, a spill or a backfill never writes a
record twice and never rolls a batch back. A model call's key is `llm:{call_hash}:{unit}`, the
hash taken over fields a `model_gateway_records` row also stores (tenant, correlation id, time,
provider, model, outcome and token counts), so the backfill reproduces the hook's keys. A direct
call's key is fixed when its event is built.

## Rollups and rebuild

`spend_usage_rollups` sums records per reporting day and dimension combination (billing date,
organisation node, business unit, attribution path or reason, product line, use case,
application, agent, provider, model, usage type, unit, currency, rate card, price source,
commitment, billing account, region, environment, risk tier): quantity, amount (per currency),
INR, unconverted amount, unpriced quantity, overage quantity and counts of records, calls and each
flag. The writer adds each inserted record's contribution in the same transaction with one sorted
additive upsert, under a shared lock on each affected tenant-day; only rows the insert returned
add, so a duplicate is never counted. A rebuild (`POST /spend/rollups/rebuild`, at most 31 days,
a job) takes one day's lock exclusively, deletes the day's rows and re-sums its records as they
are; it never re-prices or re-resolves, so a rebuilt day equals the incrementally kept one.

## Coverage: the Gate 1 attribution measure

`GET /spend/coverage` (at most 366 days) reports per reporting day and for the period: records,
calls, the INR amount and the amount with an organisation node, the **attributed share** (the INR
amount attributed to a business unit, department, team or cost centre over the whole; attribution
to a `group` node is reported as `group_share` and does not count), the **unattributed share** by
amount and by count, unpriced records and quantities per unit, unconverted records and amounts per
currency, FX conversions still pending (estimated records dated after their currency's newest
rate), gaps by reason, the records and amount per unattributed reason and per attribution path
and node kind, and the twenty unpriced keys with the most records. Records with no INR amount
count in the count share only and are reported beside it, so unpriced and unconverted volume is
visible; a day whose records are all unpriced or unconverted has no INR amount. Shares are shown
to six decimals rounded so they never look better than they are: attributed shares toward zero,
unattributed shares away from it. The finops `unattributed_share` is a different measure and is
not used here.

## Meter gaps

Usage that could not be metered is counted per tenant, reporting day, usage type, reason and
detail in `spend_meter_gaps` (`GET /spend/gaps`, at most 92 days): `queue_full`, `spill_failed`,
`shutdown_lost`, `paused`, `tenant_mismatch`, `failed_no_usage` (the detail names the provider,
or `cancelled:<provider>`), `timeout_estimated` and `unpriced_tool`. The detail never carries text
from a call.

## Maintenance jobs

Long operations are jobs: a route answers `202 {"job_id", "status"}` and a worker runs the job on
the `maintenance` queue; `GET /spend/jobs` and `GET /spend/jobs/{job_id}` read them. One job of a
kind runs at a time per tenant. An administrator's request is refused with 409 `job_running`,
naming the job, while one of its kind is queued or running.

A job started by a reference-data change (a restatement after a correction, a settlement after a
new or corrected rate, a recompute after a commitment change) is never dropped, and the change's
response (`restate_job_id`, `settle_job_id`) names a job that covers it:

- it is merged into a queued job of its kind when one job can cover both: a settlement joins the
  ranges and adds the forced dates; a restatement of the same provider joins the ranges, adds the
  cards (or restates every record of the provider when either job does) and lists both reasons; a
  recompute covers the one provider both name, or every provider. The merge is audited
  (`spend.job.merge`, with the parameters before and after). A joined range is never longer than a
  job may run (ten years); widening one only re-checks records that are already right;
- otherwise it is queued as its own job (a restatement of another provider, or while the job of
  its kind is running) and runs after the one before it: a job's end sends the next queued job of
  its kind to a worker.

A running job writes a heartbeat every minute. A job whose heartbeat has stopped for ten minutes
(its worker was lost) can be taken over by a redelivered task, and the job sweep (every 15 minutes)
queues it again and resends queued jobs nothing of their kind is running for. Jobs are idempotent,
so running one again is safe. A transient database failure (a lock or statement timeout, a
deadlock, a dropped connection) queues the job again after one, then two minutes; the third
failure fails it. A failure stores the exception's type name, never its message.

Jobs work one day at a time, 1,000 records per transaction, under the shared lock of each day
they touch, with a 30-second lock timeout and a 120-second statement timeout. Each transaction
locks the records it reads, so two jobs revising the same record (a settlement and a restatement,
or a job run twice) take turns, and each reads the other's committed values before moving the
rollup. A transaction that revises records writes its own audit row (`spend.fx.settle.chunk`,
`spend.usage.restate.chunk`, `spend.usage.reattribute.chunk`, `spend.commitments.recompute.chunk`:
counts and the totals before and after per key) and marks the commitments to replay, so a job that
stops part-way leaves no revision unaudited; the job's summary row follows at its end.

- **FX settlement** (`POST /spend/fx-rates/settle`, at most 92 days; the daily beat at 19:30 IST
  for the last seven days of every tenant with pending conversions; every new or changed rate for
  the days it governs, from its date to the day before the next rate, or 31 days without one).
  Records that used an earlier rate, or none, are converted with the rate of their day as known
  now. A record whose day has no published rate keeps the latest earlier rate and stays
  `fx_estimated`. Changing a rate that settled records already use needs `restate: true`
  (409 `fx_in_use` otherwise); its settlement re-converts those records. An import row that
  would change such a rate is rejected as `fx_in_use`. Money commitments of the touched providers
  are marked for a full recompute.
- **Restatement** (`POST /spend/usage/restate`, a provider and at most 92 billing days, a reason;
  queued automatically by a correction of a card that priced records, a backdated supersede and an
  earlier `effective_to` sent with `restate`; an automatic restatement covers every affected billing
  day, however long the card was in force). Records of the given cards (and, with
  `include_unpriced`, the unpriced and fallback-priced records of the provider; with neither,
  every record of the provider) are re-priced with the active cards as known now and converted
  again. A record priced by the deployment's fallback list that still has no card keeps its stored
  price, because the fallback tables carry no dates. The audit row records the reason and the
  amounts before and after per billing date and card.
- **Re-attribution** (`POST /spend/usage/reattribute`, at most 92 days). Records still
  unattributed are resolved again (a mapping or node added after the usage); only those that now
  resolve change, and only their attribution, product line (when empty) and use case (when
  `unattributed`).
- **Commitment recompute** (`POST /spend/commitments/recompute`; the beat every 15 minutes for
  tenants with an active commitment; every commitment change). A record draws from the most
  specific active commitment covering its provider, usage type, unit and model on its billing date
  (quantity with model, quantity for the provider, money for the usage type, money for the
  provider). Quantity is drawn in record units, unpriced records included; money in the
  commitment's currency, through INR at the record's reporting-date rate when the currencies
  differ, and a priced record that cannot be converted is counted as undrawn. Records draw in
  `(event_time, id)` order, so the result never depends on arrival order: a full replay runs from
  the earliest period after a commitment change, a late record, a restatement or a settlement;
  otherwise an append pass draws the window from the stored watermark to two hours ago. A record
  past the capacity is flagged `overage` with the quantity beyond it (for a money commitment, the
  matching share of the record's quantity); the record's amount is not changed, and the overage
  price is applied in reconciliation. The flags that ask for a full replay are read and cleared
  together, and the watermark is dropped until the replay ends: a commitment change or a late
  record that arrives while a replay runs leaves its flag set for the next run, and a replay that
  stops part-way runs in full again. A provider whose commitments are all closed is replayed once
  after the change (its records lose their assignments) and skipped afterwards. Each recompute that
  changes anything is audited (`spend.commitments.recompute`, with each commitment's drawn totals
  before and after), and its audit rows are commercial like the commitment's own.
- **Rebuild** and **backfill** are described above and below.

## Partitions

`spend_usage_records` is range-partitioned by `event_time`, one partition per month from July 2026
to December 2028 plus a default partition, all created by migration `v6z80_spend_usage` (strict
runtimes forbid DDL at startup) and each tenant-isolated under forced row-level security. A record
past the horizon lands in the default partition and is never lost; a daily beat (01:10 IST) logs
`spend_usage_partitions_horizon_low` and `GET /spend/status` reports `partition_horizon` with the
months left, low below six; a later migration adds months. No partition is detached in this phase.

## Existing ledgers and backfill

Usage records are a new, separate source: spend never writes `agent_cost_ledger`,
`finops_cost_ledger` or `model_gateway_records`, and their readers (budgets, thresholds,
forecasts, dashboards) are unchanged. `GET /spend/coverage/ledgers` (at most 31 days) sets the
usage tokens, calls and USD amounts per day beside the ledgers and the gateway rows, with the
expected differences listed in its `notes` (direct callers' tokens, runs that cross midnight or
raise, resumes and runs that write no ledger row, extra output a provider reports separately, UTC
versus reporting dates, router timeout estimates). Amounts differ by design: the ledgers carry a
blended estimate, usage records carry rate-card prices.

**Backfill** (`POST /spend/usage/backfill`, at most 31 days, a job) recreates model-call records
from `model_gateway_records` with the hook's own keys; a call that already has any record is
skipped whole, so a fully cached prompt never gets a second, uncached record. Gateway rows exist
only while the model gateway and its records are on, and are pruned after their retention period;
`GET /spend/status` reports `backfill_source` (`model_gateway_records` or `none`), so an operator
knows whether lost model-call writes can be recovered. With the durable spill, backfill is the
second line of defence, not the first.

## Rate-card and FX hooks

`card_in_use` answers the latest billing date of a record a card priced (directly, or as the
output half of a blended price within the card's own dates); a card in use can be retired only by
a correction and its price changed only by one. A correction of a card in use, a backdated
supersede over priced records sent with `restate`, and an earlier `effective_to` over priced
records sent with `restate` each queue the restatement of the affected billing days once the
change has committed, and answer its `restate_job_id`; a rate-card import queues one restatement
per provider for the rows that call for one (`restate_jobs` in its report). A mapping, alias or
tree change drops this process's attribution and alias caches.

## Scopes, administrators and commercial reads

The routes form the `spend` scope family: reads need `audit:read`, other methods
`approvals:write`. Every write also needs a tenant administrator signed in as a person: the
route checks the admin scope, then confirms an active administrator of the tenant in the
database (API keys and agent tokens are refused), so a deployment in route-enforcement log mode
still never opens a write. Rate cards, commitments and the price quote are commercial: they are
answered to a human administrator or auditor only (403 `commercial_read_refused` otherwise),
because machine credentials can hold `audit:read` through agent grants. The same readers get a usage
record's contract terms: anyone else reading `GET /spend/usage` gets `rate_card_id`, `unit_price`,
`commitment_id` and `overage_quantity` as `null` and no `overage` flag (amounts stay, they are the
spend), and `GET /spend/rollups` grouped by `rate_card_id` or `commitment_id` is 403
`commercial_read_refused` for them. Every table is tenant
scoped under forced row-level security, and spend-to-spend foreign keys are composite on
`(tenant_id, id)`.
