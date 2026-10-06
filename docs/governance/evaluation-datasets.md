# Evaluation datasets

A tenant's administrators keep named sets of reference cases and score prompts against them. This
is the first part of the evaluation framework; model-graded scorers, scheduled runs, stored
results and promotion gates are not here yet (see the end of this page).

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

A case needs `input` and at least one expectation. The rules are the scorer's own, so a version
that is stored can always be run. A version holds at most 200 cases and one megabyte.

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
| `POST /eval-datasets/{id}/run` | Score a prompt against a version. |

Refusals carry a code: `invalid`, `invalid_cases`, `name_taken`, `too_many`, `not_found`,
`version_not_found`, `archived`, `stale`, `unchanged`.

### Running a version

`POST /eval-datasets/{id}/run` takes `system` (the prompt), `model`, and optionally `version`
(the latest when omitted), `max_tokens`, `offset` and `limit`.

- It also needs `AGENTICORG_PROMPT_COMPARE_ENABLED`: a run makes one billed model call per case,
  through the same path as prompt evaluation, so the model is the one asked for, the tenant's
  model policy applies and inputs are pseudonymised where the tenant requires it.
- A request scores at most 25 cases. A larger version is run in slices with `offset`; the
  response gives `cases_total`, `offset`, `cases_run` and `complete`, so a partial result is never
  mistaken for the whole version.
- The response names the `version` and its `content_hash`, the pass, fail and error counts, the
  pass rate, latency and cost, and per case its id and outcome (which expectations failed, or the
  error type). It never returns an answer or an input.
- Nothing is stored. Runs share the `prompt-compare` rate limit.

## Console

The Prompt Templates page has an **Evaluation datasets** panel while the switch is on: the list,
creating a dataset from a name and cases written as JSON, opening a dataset at any version,
saving the edited cases as a new version (against the version that was opened), archiving, and,
where prompt evaluation is on, scoring a prompt with a chosen model against the shown version.
The panel runs the first 25 cases and marks a partial result.

## Data handling

Cases are the tenant's own reference data and can hold business content. They are stored under
row-level security, returned only to the tenant's administrators, and are not logged or put in
metrics; logs carry the dataset id, version number and case count. Use synthetic content.

The author of a dataset and of each version is recorded as the caller's local user id. A request
made with an API key records no author.

## What is not here yet

- **No stored runs or results.** A run's report is returned and not kept; there is no history,
  comparison across versions or dashboard.
- **Deterministic expectations only.** Model-graded scorers and aggregate metrics (precision,
  recall, F1, retrieval metrics) are the framework's next part.
- **A run takes a prompt and a model.** Running a dataset against an agent or a workflow is not
  available.
- **No scheduled runs and no promotion gate.**
- **No import from the built-in golden datasets**, and no console view of a run larger than one
  slice.
