# AI spend intelligence: reference data and rupee pricing

Behind `spend_intelligence_enabled` (default off; `AGENTICORG_SPEND_INTELLIGENCE_ENABLED`). Off,
`GET /spend/status` answers `enabled: false`, every other spend route is not found (the request
gate answers 404 `spend_disabled` before the body is read) and nothing is metered or written; no
existing path changes.

This part of the feature keeps the reference data spend is measured against: the organisation
tree, source mappings, model aliases, rate cards, commitments and FX rates, and the pricing
engine that prices a usage at its date in the card's currency and in INR. Usage records, rollups,
non-token metering, invoice reconciliation and the Gate 1 status build on it in later parts. The
code is in `core/spend/` (`org.py`, `mappings.py`, `rates.py`, `commitments.py`, `fx.py`,
`pricing.py`, `imports.py`, `audit.py`), the routes in `api/v1/spend.py`, the tables in
`core/models/spend.py`, migration `v6z79_spend_reference`.

## Two calendars

| Date | Zone | Used for |
|---|---|---|
| event date | `spend_reporting_timezone` (default `Asia/Kolkata`) | FX rate lookup, the rollup day, coverage, the Gate 1 attribution month |
| billing date | the provider's billing zone: `spend_provider_billing_timezones_json`, then the defaults (`gemini` closes in `America/Los_Angeles`), then UTC | rate-card and commitment dating, volume tiers, the reconciliation month |

In-house providers and platform storage bill in the reporting zone. An event at 23:00 UTC on
30 September is billed on 30 September by a UTC provider and reported (and converted) on
1 October in India. With the feature on, the deployment refuses to start when a zone cannot be
loaded. `spend_sweeps_enabled` (default on) gates the beat jobs later parts add; it has no
effect while the feature is off.

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
these mappings (and, in the next part, by matching their codes to node codes); an unknown label
counts as unattributed, never dropped.

## Model aliases

An alias maps a model name as called (a dated name, a deployment name) to the SKU the tenant's
rate cards and invoices use, per provider. Pricing applies it before choosing a card, so a
called alias prices with the SKU's card. Aliases do not chain: an alias may not name another
alias, and a SKU may not itself be an alias.

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
(percent) and volume tiers (`[{"from_quantity": "0", "unit_price": "2.5"}, ...]` in card units,
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
priced by the retired card are restated by a job (from the next part). `supersede: true` on a
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

`PUT /spend/fx-rates` upserts a rate and answers the rate it replaced. Settling estimated records
when the day's rate arrives, and refusing a change to a rate settled records used without
`restate` (`fx_in_use`), come with the usage records in the next part.

## Commitments

A **quantity** commitment names a provider, a usage type, optionally a model, a card unit and a
quantity in card units (500 x `1m_input_tokens`), and may carry an overage price per card unit.
A **money** commitment names a provider, optionally a usage type, and an amount in a currency; it
has no unit to price overage by. Periods are half-open ranges of billing dates. Two active
commitments of the same provider, usage type, model, unit and kind may not overlap, on create and
on every change of the end or the status. Storage commitments are entered in `gb_day` (a GB-month
has no fixed number of GB-days). Drawdown is counted in record units from usage records by a job
in the next part; every create or change marks the commitment for a full recompute.

## Imports

Each kind of reference data takes a CSV or JSON file (`POST .../import`, `dry_run` query
parameter), at most 2 MiB and 5,000 rows; larger bodies are refused with 413 `import_too_large`
before they are read in full, and a 5,001st row with 413 `too_many_rows`. JSON is a list of
objects or `{"rows": [...]}`. CSV is UTF-8 (a byte-order mark is dropped) or Latin-1; header
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
reference-data values only.

## API

| Method and path | Who | Notes |
|---|---|---|
| `GET /spend/status` | `audit:read` | `enabled`, the reporting currency and zone, the vocabularies, the import bounds |
| `GET /spend/org-nodes`, `GET /spend/org-nodes/{node_id}` | `audit:read` | a node with its ancestors and business unit |
| `POST /spend/org-nodes`, `PATCH /spend/org-nodes/{node_id}`, `POST /spend/org-nodes/import` | administrator | |
| `GET /spend/mappings`, `GET /spend/model-aliases` | `audit:read` | |
| `PUT /spend/mappings`, `POST /spend/mappings/import`, `PUT /spend/model-aliases` | administrator | |
| `GET /spend/rate-cards` | administrator or auditor | `as_of` keeps the cards in force on a billing date |
| `POST /spend/rate-cards`, `PATCH /spend/rate-cards/{card_id}`, `POST /spend/rate-cards/{card_id}/correct`, `POST /spend/rate-cards/import` | administrator | |
| `GET /spend/commitments` | administrator or auditor | with drawdown and what remains |
| `POST /spend/commitments`, `PATCH /spend/commitments/{commitment_id}` | administrator | |
| `GET /spend/fx-rates` | `audit:read` | |
| `PUT /spend/fx-rates`, `POST /spend/fx-rates/import` | administrator | |
| `GET /spend/price` | administrator or auditor | the price of a usage at a date |

## Scopes, administrators and commercial reads

The routes form the `spend` scope family: reads need `audit:read`, other methods
`approvals:write`. Every write also needs a tenant administrator signed in as a person: the
route checks the admin scope, then confirms an active administrator of the tenant in the
database (API keys and agent tokens are refused), so a deployment in route-enforcement log mode
still never opens a write. Rate cards, commitments and the price quote are commercial: they are
answered to a human administrator or auditor only (403 `commercial_read_refused` otherwise),
because machine credentials can hold `audit:read` through agent grants. Every table is tenant
scoped under forced row-level security, and spend-to-spend foreign keys are composite on
`(tenant_id, id)`.
