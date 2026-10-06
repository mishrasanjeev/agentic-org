# Evaluation datasets and runs

A tenant's administrators keep named sets of reference cases, score prompts against them with
deterministic checks, labels and model-graded judges, and keep the runs. Promotion gates are
not here yet (see the end of this page).

Behind `AGENTICORG_EVALS_V2_ENABLED`, off by default. Off, the list answers `enabled: false` and
every other endpoint answers 409; nothing is stored.

## What a dataset is

A dataset is a name and a sequence of versions (`core/evals/datasets.py`).

A **version** is a list of cases. A case is the form prompt evaluation already scores:

| Key | Meaning |
|---|---|
| `input` | What the model is asked. Required. |
| `id` | A label for the case. Defaults to its position. Distinct within a version. |
| `contains` | Texts the answer must contain (case-insensitive), at most ten. |
| `not_contains` | Texts the answer must not contain, at most ten. |
| `equals` | The exact answer, ignoring surrounding whitespace. |
| `matches` | A bounded regular expression searched in the start of the answer. |
| `label` | The class the answer should name, for classification cases (at most 80 characters). |
| `reference` | A reference answer, for the context-recall judge. Not a check. |
| `context` | The material the answer should rest on, for the faithfulness judge. Not a check. |

A case needs `input` and at least one expectation (`label` counts). The rules are the scorer's
own, so a version that is stored can always be run. A version holds at most 200 cases and one
megabyte.

**Labels.** The dataset's labels are the distinct `label` values of the version. An answer's
predicted label is the dataset label it names first, as a whole word, case-insensitively; an
answer that names none has no predicted label. A labelled case passes when the predicted label is
the expected one. Labels are distinct whatever the letter case, and a slice of a version is scored
against every label of the version, not only those its own cases carry.

**A version is never changed.** Saving different cases makes the next version; the database
refuses an update to a version row. Each version carries the SHA-256 of its cases in canonical
form, so a result can name exactly what it was measured on. Saving cases identical to the latest
version is refused (`unchanged`) rather than stored under a new number.

**A dataset is archived, not deleted.** It leaves the list and its name becomes free; its versions
stay readable by id.

Names are unique in a tenant, whatever the letter case. A tenant keeps at most 200 datasets.

## API

All routes are for tenant administrators.

| Route | Purpose |
|---|---|
| `GET /eval-datasets` | The datasets, without cases. `?include_archived=true` adds archived ones. |
| `POST /eval-datasets` | Create a dataset with version 1: `name`, `cases`, optional `description`, `note`. |
| `GET /eval-datasets/{id}` | The dataset and its versions, newest first, without cases. |
| `GET /eval-datasets/{id}/versions/{n}` | One version with its cases. |
| `POST /eval-datasets/{id}/versions` | Save `cases` as the next version. `expected_latest` refuses the save (`stale`) when someone else added a version meanwhile. |
| `DELETE /eval-datasets/{id}` | Archive. |
| `POST /eval-datasets/{id}/run` | Score a prompt against a version and keep the run. |
| `GET /eval-datasets/{id}/runs` | The dataset's stored runs, newest first (at most 50), without per-case outcomes. |
| `GET /eval-runs/{run_id}` | One stored run with its per-case outcomes. |
| `GET /eval-datasets/{id}/compare?version=` | The models that ran a version, ranked from their newest stored runs. |
| `PUT /agents/{id}/eval-gate`, `GET /agents/{id}/eval-gate` | An agent's promotion gate and whether its prompt passes it. |

Refusals carry a code: `invalid`, `invalid_cases`, `name_taken`, `too_many`, `not_found`,
`version_not_found`, `archived`, `stale`, `unchanged`, `run_not_found`.

### Running a version

`POST /eval-datasets/{id}/run` takes the prompt as `system`, or `agent_id` to use an agent's
prompt text (the run is then labelled `agent:<name>` unless `prompt_label` says otherwise, and
its hash matches the agent's promotion gate), `model`, and optionally `version` (the latest when
omitted), `max_tokens`, `offset`, `limit`, `judges` with `judge_model`, `prompt_label` and
`store` (true by default).

- It also needs `AGENTICORG_PROMPT_COMPARE_ENABLED`: a run makes one billed model call per case,
  through the same path as prompt evaluation, so the model is the one asked for, the tenant's
  model policy applies and inputs are pseudonymised where the tenant requires it.
- A request scores at most 25 cases. A larger version is run in slices with `offset`; the
  response gives `cases_total`, `offset`, `cases_run` and `complete`, so a partial result is never
  mistaken for the whole version.
- The response names the `version` and its `content_hash`, the pass, fail and error counts, the
  pass rate, latency and cost, the metrics, the judges' scores, and per case its id and outcome
  (which expectations failed, the expected and predicted label, each judge's score, or the error
  type). It never returns an answer or an input.
- Runs share the `prompt-compare` rate limit.

### Metrics

Computed over the slice's outcomes, with no further model call (`core/evals/metrics.py`):

- `pass_rate`: passed over scored cases; a case whose model call failed is an error and is not
  scored.
- `exact_match`: over the cases that carry `equals`, how many matched.
- `classification`: over the labelled cases, accuracy and the macro-averaged precision, recall
  and F1 across the dataset's labels, with per-label counts. An answer that names no label is a
  miss for its expected label and no false positive for any other. Absent when no case is
  labelled.

### Judges

Model-graded scorers (`core/evals/scoring.py`). Each is a fixed rubric the judge model answers on
a five-point scale as JSON; the score is that rating mapped to 0 to 1, and a run reports each
judge's mean over the cases it rated.

| Judge | Rates | Needs |
|---|---|---|
| `faithfulness` | whether every claim in the answer is supported by the case's `context` | `context` |
| `relevance` | whether the answer addresses the case's `input` | nothing more |
| `instruction_adherence` | whether the answer follows the prompt under test | nothing more |
| `context_recall` | whether the answer covers what the case's `reference` says | `reference` |

A case without what a judge needs is not rated by it. A judge call that fails, is answered by
another model, or does not come back as a rating is an error for that judge and case, counted
apart from the scores. Judge calls go through the same path as the answers: the requested judge
model, the tenant's model policy, pseudonymisation where the tenant requires it. Each is one more
billed call per case and judge. A judge's one-sentence reason is returned with the run once; it is
not stored or logged.

Judges rate; they do not decide. A judge model has the limits of any model: it can be wrong,
inconsistent between runs, and partial to answers in its own style. Use its scores beside the
deterministic checks, not instead of them.

### Stored runs

A run is kept unless `store` is false (`eval_runs`, tenant-scoped). What is kept is what was
measured and what came out: the version and its hash, the model, the judges and judge model, the
prompt as a SHA-256 hash with the optional `prompt_label`, the answer limit (`max_tokens`), the
slice, the counts, metrics, scores and the outcome per case id. Never an answer, an input, the prompt's text or a judge's reason.

## Console

The Prompt Templates page has an **Evaluation datasets** panel while the switch is on: the list,
creating a dataset from a name and cases written as JSON, opening a dataset at any version,
saving the edited cases as a new version (against the version that was opened), archiving, and,
where prompt evaluation is on, scoring a prompt with a chosen model, a label for the prompt and
any of the judges with a judge model, against the shown version. The panel runs the first 25
cases, marks a partial result, shows the metrics and scores with each judge's reason for a case
that did not pass, and lists the dataset's earlier runs.

## Data handling

Cases are the tenant's own reference data and can hold business content. They are stored under
row-level security, returned only to the tenant's administrators, and are not logged or put in
metrics; logs carry the dataset id, version number and case count. Use synthetic content.

The author of a dataset and of each version is recorded as the caller's local user id. A request
made with an API key records no author.

## Comparing models

`GET /eval-datasets/{id}/compare?version=` (the latest version when omitted) reads the stored runs
of that version and gives one row per model from its newest run, ranked by pass rate, then
average latency, then cost per case. Each row carries the pass rate, the classification accuracy
where the version has labels, the average latency, answers a minute (what one sequential caller
would get at that latency: an estimate from the measured latency, not a load test), tokens per
case, cost per case and the judges' means. The console shows the table under the open dataset.
A model's newest run is the one compared, whatever prompt or slice it used; the row names the
prompt label and marks a partial slice.

## Promotion gate

An agent may declare a gate (`PUT /agents/{id}/eval-gate`): an evaluation dataset, optionally a
version (the latest when not given), `min_pass_rate` (100 by default), `max_regression` (0),
and an optional `baseline_run_id` selected by the operator for this agent.
At promotion or resume to `active`, after the maker-checker check, the newest stored run of that
dataset version made with the agent's current prompt text and configured model is read.
The hash and model must both match; using `agent_id` alone does not qualify a run.
The agent fallback and global primary/fallback each need their own passing full-dataset run
of that prompt. A model change invalidates the old model's evidence.
The promotion is refused, with the code and the
numbers, when:

| Code | Meaning |
|---|---|
| `not_evaluated` | no stored run of this version was made with the agent's current prompt text |
| `not_scored` | the newest such run scored no case (every call failed) |
| `incomplete_run` | offset is not zero or not every case was evaluated |
| `dataset_mismatch` | the stored content hash or case count differs from the selected version |
| `evaluation_errors` | an answer or configured judge failed |
| `fallback_not_evaluated` | a runtime fallback lacks full, error-free passing evidence |
| `no_model` | the agent has no pinned model |
| `baseline_unusable` | the explicit baseline is inaccessible, incomplete, errored, or for another dataset/version/model |
| `below_minimum` | its pass rate is below `min_pass_rate` |
| `regressed` | its pass rate is more than `max_regression` points below the explicitly selected baseline |
| `gate_unusable` | the gate names a dataset or version that cannot be read |
| `no_prompt_text` | the agent has no prompt text to evaluate |

Behind `AGENTICORG_EVAL_PROMOTION_GATE_ENABLED`, off by default, beside the evaluation switch.
Off, a gate is stored and `GET /agents/{id}/eval-gate` reports its verdict, and promotion is
not held to it. An agent with no gate is not affected either way. The gate reads the prompt text
only: an agent whose behaviour comes from a prompt reference, variables or amendments is not
measured by it, and a run made with a stale copy of the prompt stops matching as soon as the text
changes.

Without `baseline_run_id`, only the minimum threshold is enforced; no regression comparison
is claimed. The operator must select a representative prior run for this agent. Runs from
other prompts, agents or scheduled checks are never silently chosen as its predecessor.
Datasets larger than the current 25-case run limit cannot pass this gate by combining partial
slices. Use a complete bounded gate dataset until full-dataset aggregation is available.
This is a prompt/model evidence gate, not proof of full tool execution or dynamic routing.

## Scheduled runs

A synthetic check of kind `eval_dataset` (`docs/operations/synthetic-checks.md`) runs a dataset
version with a fixed prompt and model on an interval, keeps each run in the dataset's history
with the label `scheduled:<model>`, and fails when the pass rate falls below `min_pass_rate` or a
case could not be answered. A check of kind `adversarial` runs the guardrail adversarial set the
same way and feeds the Guardrails page. Both need `AGENTICORG_SYNTHETIC_CHECKS_ENABLED`.

## What is not here yet

- **A run takes a prompt and a model.** `agent_id` selects prompt text, not a complete agent
  execution. Tool use and workflow execution are not measured; there are no retrieval metrics.
- **Comparison is within one dataset version.** Cross-version comparison and workload
  throughput/load testing are not implemented.
- **The gate reads the prompt text and one dataset.** Prompt references, variables and
  amendments are outside it; there is no gate on a workflow or on a scheduled check's result.
- **No console editor for the gate**; it is set through the API.
- **No import from the built-in golden datasets**, and no console view of a run larger than one
  slice.
