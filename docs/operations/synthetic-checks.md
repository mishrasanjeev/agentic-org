# Synthetic checks

A synthetic check is a scheduled probe of one of a tenant's own paths with a fixed, synthetic
input, and a stored result for every run. It answers a question a health endpoint cannot: not
"is the service up" but "does a model call still answer, does the knowledge search still find
the document, do the guardrail rules still catch what they are there to catch, does the audit
chain still verify".

Implementation: `observability/synthetic.py` (configuration, probes, running, storage),
`core/tasks/synthetic_tasks.py` (the sweep and the prune), `api/v1/observability.py` (the
endpoints), and the **Checks** tab of the console page `/dashboard/observability`.

## The switch

| Setting | Default | Meaning |
| --- | --- | --- |
| `AGENTICORG_SYNTHETIC_CHECKS_ENABLED` | `false` | Run the scheduled sweep. Off, no check runs on its own; a check can still be run by hand. |
| `AGENTICORG_SYNTHETIC_CHECKS_RETENTION_DAYS` | `30` | How long results are kept. |

The sweep (`core.tasks.synthetic_tasks.run_synthetic_checks`) runs every five minutes. It reads
which tenants have an enabled check, then runs each tenant's due checks under that tenant's own
row-level security context, the longest-waiting first and at most ten per tenant per sweep. A
check is due when it has never run or its interval has passed. One tenant's or one check's
failure never stops the sweep.

## Kinds

Every kind accepts `max_latency_ms`; a run slower than it fails with `too_slow`. A probe that
has not answered within 60 seconds ends as an error.

| Kind | Configuration | What it does | Fails with |
| --- | --- | --- | --- |
| `model` | `prompt` (required, up to 2000 characters), `model`, `contains` | Sends the prompt through the direct router as the tenant, with at most 64 answer tokens. The model gateway's policies, limits and records apply as for any call. | `empty_answer`, `answer_missing_expected_text` |
| `knowledge` | `query` (required), `top_k` (5), `min_results` (1) | Runs the query against the tenant's knowledge search. | `too_few_results` |
| `guardrail` | `stage` (required), `text` (required), `expect`: `blocked`, `detected` (default) or `clean` | Dry-runs the tenant's guardrail rules for the stage over the text. Nothing is enforced, metered or audited by the dry run. | `guardrail_not_blocked`, `guardrail_not_detected`, `guardrail_unexpected_finding` |
| `audit_chain` | `recent` (1000) | Verifies the newest `recent` links of the tenant's audit chain (`docs/operations/audit-chain.md`), held against its stored anchor. | `audit_chain_broken` |

An unknown configuration key is refused, so a check cannot be pointed at anything but these
four paths; there is no arbitrary URL probe.

A `model` check makes a real, billed model call on every run. Keep its prompt short and its
interval long enough for the question it answers.

## Results

A run ends in one of three states:

| Status | Meaning |
| --- | --- |
| `ok` | The probe answered and every expectation held. |
| `failed` | The probe answered and an expectation did not hold; `reasons` lists which. |
| `error` | The probe could not run (the provider refused, a timeout, a stored configuration that is no longer valid); `detail.error_type` names the exception type. |

A result keeps the status, the latency, the reasons and counts (tokens, results found, findings,
links verified). It never keeps the model's answer, retrieved text, the probe's input or an
exception message. Inputs are the administrator's own; use synthetic text only.

Every run counts in `agenticorg_synthetic_checks_total{kind,result}` (no tenant label), opens a
span `agenticorg.synthetic.check` when tracing is on, and a run that is not `ok` logs
`synthetic_check_not_ok` with the check id, the kind, the status and the reasons. Alert on the
counter's `failed` and `error` series or on the log line.

Results older than the retention period are removed daily
(`core.tasks.synthetic_tasks.prune_synthetic_results`); deleting a check removes its results.

## Endpoints

All under `/api/v1/observability`, tenant administrators only.

| Endpoint | Does |
| --- | --- |
| `GET /checks` | the tenant's checks with their last status, the kinds, the limit and whether the scheduled sweep is on |
| `POST /checks` | add a check: `name`, `kind`, `config`, `interval_minutes` (5 to 1440, default 60), `enabled` |
| `PATCH /checks/{id}` | change the name, the configuration, the interval or the enabled state |
| `DELETE /checks/{id}` | remove the check and its results |
| `POST /checks/{id}/run` | run the check now and return the result (works with the sweep off) |
| `GET /checks/{id}/results?limit=` | the newest results (default 50, at most 500) |

A tenant has at most 20 checks and a name is unique within the tenant. Changes are attributed
to the calling principal (`created_by`, `updated_by`).

## Storage

`synthetic_checks` and `synthetic_check_results` (migration `v6z42_synthetic_checks`), both
tenant-scoped under row-level security.

## Tests

`tests/unit/observability/test_synthetic.py` covers the configuration of each kind and what an
invalid one says, when a check is due, each probe against its expectation, the three result
states, the latency limit and the probe time limit, that an error keeps only the exception type,
the stored result and the metric, the sweep off by default and isolating a failing tenant and a
failing check, the schedule, the row-level security of both tables and every endpoint with its
admin requirement. `ui/src/__tests__/SyntheticChecksPanel.test.tsx` covers the console tab.

## What is not here yet

- **No agent-run or workflow probe.** A check exercises one path, not a whole agent run.
- **No built-in alert routing.** A failing check is a metric and a log line; routing it to a
  channel is the deployment's alerting.
- **No quality scoring.** `contains` is a text match; model-graded scoring belongs to the
  evaluation framework.
