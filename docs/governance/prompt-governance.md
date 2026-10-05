# Prompt governance

How prompt templates are declared, checked, filled and changed. This page covers typed
parameters and maker-checker approval; side-by-side comparison, evaluation against datasets and
context-window management follow in later parts and are listed at the end.

Implementation: `core/prompts/parameters.py` (declarations, checks, resolution, rendering),
`core/prompts/change_requests.py` (maker-checker for templates), `core/prompts/activation.py`
(maker-checker for agent prompts) and `api/v1/prompt_templates.py` (the template endpoints).

## Typed parameters

A prompt template carries `{{name}}` placeholders. A template used to list its variables by name
only, and substitution replaced whatever it was handed: a missing value left `{{name}}` in the
prompt a model then read, and nothing said a value had to be a number or one of a few choices.

A parameter is a declared placeholder. It is one entry of the template's `variables` list:

| Key | Meaning |
| --- | --- |
| `name` | the placeholder's name: letters, digits and underscores, not starting with a digit |
| `type` | `string` (default), `integer`, `number`, `boolean` or `enum` |
| `required` | whether a value must be supplied; defaults to true unless a `default` is given |
| `default` | the value used when none is supplied; checked against the type and bounds; a required parameter has none |
| `description` | what the parameter is for |
| `choices` | an `enum`'s allowed values (1 to 100) |
| `min`, `max` | a number's range |
| `max_length`, `pattern` | a string's limit (at most 20,000 characters) and a regular expression it must match in full |

The matcher has no timeout, so a pattern is bounded by what it may be and what it is given: a
pattern that nests or repeats a group, uses a backreference or has more than two unbounded
repetitions is refused, and a string held to a pattern is at most 500 characters.

A variable declared the old way, a name with or without a description, is a required string, so
existing templates read as they did. That includes the shape built-in templates were seeded
with, an empty description and an empty `default`: an empty default declares no default, and the
variable stays required. A parameter that may be left out says `"required": false`. A template has at most 50 parameters. Any other key is
refused.

```json
[
  {"name": "role", "description": "what the agent does"},
  {"name": "org_name", "type": "string", "max_length": 40},
  {"name": "max_words", "type": "integer", "min": 10, "max": 500, "default": 120},
  {"name": "tone", "type": "enum", "choices": ["formal", "plain"], "default": "plain"}
]
```

Tool references (`{{tool:name}}`, `{{tools.name}}`) are not parameters; they are checked against
the connector registry as before and left in the text.

## Checks

**The template against its declarations.** A placeholder the text uses but does not declare is
`undeclared`; a declaration the text never uses is `unused`. An undeclared placeholder is an
error, an unused declaration is reported only.

**Values against the declarations.** Each supplied value is read as its parameter's type
(`"80"` is a valid integer, read digit by digit so a large one is not changed by a floating-point
round trip; `"yes"` is a valid boolean), held to its bounds, and a default fills a
parameter that was not supplied. A missing required value, an unknown name, a value of the wrong
type or outside its bounds is refused, and every problem is reported together rather than one at
a time. An optional parameter with no default and no value renders as empty text.

**Rendering.** The resolved values are substituted and no placeholder is left behind: a template
with an undeclared placeholder is not rendered at all. A value is inserted as text and is never
itself read as a placeholder.

## Endpoints

| Endpoint | Does |
| --- | --- |
| `POST /api/v1/prompt-templates/check` | checks `template_text` and `variables` without storing anything: the parameters as they would be stored, the placeholders used, `undeclared`, `unused` and the `problems` with the declarations |
| `POST /api/v1/prompt-templates/{id}/render` | fills a stored template with `values` after checking them; 422 with every problem otherwise |

Both need the template read permission; the render endpoint applies the same domain check as
reading the template.

## The switch for writes

| Setting | Default | Meaning |
| --- | --- | --- |
| `AGENTICORG_PROMPT_TYPED_PARAMETERS_ENABLED` | `false` | On, creating or changing a template checks its parameters and its text together and stores the parameters in their checked form; a bad declaration or an undeclared placeholder is a 422. Off, a template is stored as it was given and a variable is a mapping of text to text, as before: a typed declaration is refused. |

On an update the template is checked as the template it will be after the change, so new text
cannot use a placeholder the stored parameters do not declare. The check and render endpoints
work whatever the switch is.

## Maker-checker

With maker-checker on, a change to a prompt template does not happen when it is asked for.
Creating, changing, rolling back or deleting a template is stored as a **change request** holding
the template as it would be afterwards, the API answers `202` with the request's id, and the
template is untouched. A different person approves or rejects the request; only an approval
applies it.

| Rule | |
| --- | --- |
| Two people | The person who proposed a change cannot approve or reject it. Identity is the caller's local user id and nothing else: an API key, or any credential without a local user, cannot propose or decide, so one person cannot be maker and checker by switching credentials. With maker-checker on, a template write through an API key is refused with 403. The table also refuses a row whose decider is its proposer. |
| One at a time | A template has at most one pending change; a second proposal is refused (409) until the first is decided or withdrawn. A partial unique index holds this under concurrent proposals. |
| Against what was reviewed | A change is applied only to the template it was proposed against. If the template has changed since (or was deleted), the request becomes `stale`, nothing is applied and the approval answers 409. |
| Reasons | A rejection needs a note. The proposer can withdraw a pending request. |
| Record | An applied change writes the template's history row with who proposed it (`edited_by`), who approved it (`approved_by`) and the request it came from (`change_request_id`); `GET /prompt-templates/{id}/history` returns them. |

The validation a direct change gets (name and text rules, tool references, typed parameters when
enabled, the duplicate-name check) runs when the change is proposed, so a request that waits is
one that would have been accepted.

### Agent prompts

An agent's own prompt is not changed through a change request, because it cannot be changed in
production at all: the prompt of an active agent is locked. It is edited on a shadow or paused
agent, or set when an agent is created or cloned, and it reaches production when the agent
becomes active. The second person for an agent's prompt is therefore required at activation
(`core/prompts/activation.py`). With maker-checker on:

| Rule | |
| --- | --- |
| A second person activates | An agent whose prompt has changed since it was last active is promoted, or resumed to active, only by a signed-in user other than the one who last changed the prompt (403 otherwise). The activator is recorded on the lifecycle event. |
| The author is known | The author is whoever made the newest entry in the agent's prompt history. Creating or cloning an agent writes its first prompt there with who set it, so a new agent has an author before anyone edits it. A prompt change with no recorded author is not activated (409). |
| Not an API key | An API key cannot activate an agent with a changed prompt. |
| No shortcut | An agent is not created or cloned straight into `active` (409): the person who writes a prompt would be activating it in the same step. |
| Unchanged prompts | A pause and a resume with no prompt change in between needs no second person and can be done with an API key: nothing new reaches production. |

What counts as the prompt is everything that shapes what the model is told: the prompt text, the
prompt reference, the prompt variables and the prompt amendments. A change to any of them is
written to the agent's prompt history with who made it, so a change to the variables or the
amendments on a paused agent needs a second person exactly as a change to the text does.

"Last active" is the newest lifecycle event into or out of `active`. An agent that was created
active, or was active before a lifecycle event was written at creation, therefore counts as
having been active when it was paused, and resumes unchanged without a second person. Creating an
agent straight into `active` (possible only with maker-checker off) now writes that event.

Prompt edits and activations take the agent's row lock, so an edit cannot land between the check
and the status change. The activator is recorded on the lifecycle event for a promotion and for a
resume.

A pack install or resync does not replace the prompt of an existing agent while maker-checker is
on (or its switch cannot be read): the installer has no author and no second person, and the agent
may be active. The existing prompt is kept and the skip is logged
(`pack_prompt_update_skipped_maker_checker`); a new pack prompt is then applied by a person through
the agent's own prompt edit.

The same switch applies, read the same strict way: if it cannot be read, the activation is
refused with 503. With the switch off, activation is as it was. The first-prompt history entry is
written on every create and clone by a signed-in user, whatever the switch. It records authorship and is
not an edit: `GET /agents/{id}/prompt-history` lists edits and leaves it out.

An agent created before this was introduced has no first-prompt entry. If it has never been
active and its prompt has never been edited, it cannot be activated under maker-checker until a
signed-in user saves its prompt and another activates it.

### The switch

| Setting | Default | Meaning |
| --- | --- | --- |
| `AGENTICORG_PROMPTS_MAKER_CHECKER` | `false` | On for every tenant of the deployment. |
| authority flag `prompts.maker_checker` | off | On for one tenant. |

The flag is read strictly: if it cannot be read, the change is refused with 503 rather than
applied unchecked. Requests made while maker-checker was on can still be decided after it is
turned off.

### Endpoints

| Endpoint | Does |
| --- | --- |
| `GET /api/v1/prompt-templates/changes?status=` | change requests (default `pending`), newest first, and whether maker-checker is on |
| `GET /api/v1/prompt-templates/changes/{id}` | one request with the template as it is now, for a side-by-side review |
| `POST /api/v1/prompt-templates/changes/{id}/approve` | applies the change; body `note` (optional) |
| `POST /api/v1/prompt-templates/changes/{id}/reject` | closes the request; body `note` (required) |
| `POST /api/v1/prompt-templates/changes/{id}/withdraw` | the proposer takes the request back |

Deciding needs tenant administrator rights and the same domain access as the template. The prompt
templates page shows the requests that are waiting and, for the one under review, every field the
change touches (name, description, text, parameters) as it is now beside what is proposed, with
the three decisions. It shows nothing while maker-checker is off and nothing waits, reloads after
a write on the page, and says so, with a retry, when the queue cannot be loaded.

Storage: `prompt_change_requests` (tenant-scoped under row-level security) and two columns on
`prompt_template_edit_history` (migration `v6z43_prompt_change_requests`).

## Comparing models and evaluating variants

Two ways to judge a prompt on what models actually return (`core/prompts/compare.py`). Both
make real, billed model calls through the direct router as the tenant, so the model gateway's
policies, limits and records apply to every call and a model the tenant may not use is refused
there.

**Compare.** One prompt and one input run against up to four models at once. Each model gets
its own result: the answer, the model that served it, latency, tokens and cost. A model that
fails is that model's result with the error type; it never fails the comparison. The models
offered are the catalogue entries the direct router can call (the Gemini, OpenAI and Anthropic
families).

**Held to the requested model.** The router may answer from a fallback model of the same
provider. An answer served by another model is reported as that model's failure
(`served_by_other_model`, with the model that served it), never shown or scored as the requested
model's answer.

**Pseudonymisation.** Where the tenant has pre-model pseudonymisation on, the prompt and the input
are pseudonymised before they leave, as for an agent's model call, and the answer is restored
before it is returned or scored. If the setting or the pseudonym map cannot be read, the request
is refused with 503 and no model is called. With pseudonymisation on, the calls of a request run
one at a time, and an encrypted pseudonym map for the request is stored as for any pseudonymised
call.

**Evaluate.** Up to three prompt variants answer a reference dataset of up to 25 cases with one
model. A case is an input with at least one deterministic expectation:

| Expectation | Passes when |
| --- | --- |
| `contains` | the answer contains every listed text (case-insensitive) |
| `not_contains` | the answer contains none of the listed texts |
| `equals` | the answer, trimmed, is exactly this |
| `matches` | a bounded regular expression is found in the first 2,000 characters |

The report gives each variant its pass rate, average latency, cost and, per case, `passed`,
`failed` with the expectations it failed, or `error` with the error type. A failed model call is
an error, not a failure of the prompt; the three are kept apart. Answers are not returned by an
evaluation.

A prompt is either a stored template (`template_id`) or text given in the request
(`template_text` with its `variables`), filled with `values` and checked as for rendering, so a
variant that is not saved yet can be scored before it is proposed.

| Setting | Default | Meaning |
| --- | --- | --- |
| `AGENTICORG_PROMPT_COMPARE_ENABLED` | `false` | Off, both endpoints answer 409 and the console shows no comparison panel. |

| Endpoint | Does |
| --- | --- |
| `GET /api/v1/prompt-templates/compare/models` | whether comparison is on, the models it can call and its limits |
| `POST /api/v1/prompt-templates/compare` | `template_id` or `template_text`, `values`, `input`, `models` (1 to 4), `max_tokens` (default 512, at most 2,048) |
| `POST /api/v1/prompt-templates/evaluate` | `variants` (1 to 3, each a named prompt), `cases` (1 to 25), `model`, `max_tokens` |

All three need tenant administrator rights. The two that call models share a rate class of six
requests a minute per tenant, and calls within a request run four at a time. No prompt, input or
answer is stored: a comparison's answers go to the administrator who asked, and the logs carry
counts and outcomes only. The prompt templates page has a **Compare models** panel on a selected template: values for
its parameters, an input, the models, and the answers side by side with latency, tokens and cost.

Deterministic expectations catch a missing figure or a forbidden phrase; they do not judge
quality. Model-graded scoring and stored datasets belong to the evaluation framework.

## Context-window management

An agent run accumulates tool results. Sent whole, a long run eventually exceeds the model's
context window and the provider refuses the call, or it fits but most of the window is spent on
results the model no longer needs. `core/prompts/context_window.py` measures the conversation
before each model call of an agent run and, when it does not fit, sends a trimmed copy.

| Setting | Default | Meaning |
| --- | --- | --- |
| `AGENTICORG_CONTEXT_WINDOW_MANAGED` | `false` | Off, the conversation is sent whole and the provider's own limit applies, as before. |

**The budget.** The context window of the model that is actually called, from the catalogue, less
the room kept for its answer (its answer limit, at most a quarter of the window), less a 10%
margin, less the definitions of the tools bound to the call, which the provider is sent too. The
model is read through a tool binding when the run did not name one, and the catalogue is searched
by provider, so a self-hosted or deployment-named model takes its provider's entry.

A model whose window cannot be established is not managed at all: the conversation is sent whole
and `context_window_unmanaged` is logged. Trimming to a guessed window would drop evidence a
larger window could have held.

**What is trimmed, in order.**

1. Never: system messages, what the user wrote, the model's own turns, and the newest round of
   tool results, which the model is about to read.
2. Older tool results are ranked by how much of the latest user message's wording they share and
   by how recent they are; the lowest ranked are omitted first. An omitted result is replaced in
   place by a short marker, so every tool call still has its answer and the provider accepts the
   conversation.
3. If that is not enough, the largest remaining tool results are cut to their beginning.

Only the copy sent to the model changes. The run's history keeps every result, later turns are
measured afresh, and the grounding check still reads everything that was retrieved. The run's
trace says how many results were omitted or cut, `context_window_fitted` is logged with the
counts, and `agenticorg_context_window_trims_total{result}` counts trimmed calls (`fitted`, or
`still_over` when even this was not enough).

**Limits.** Token counts are estimates: characters over four, plus a small cost per message. No
provider tokeniser is loaded, which is what the margin is for. Relevance is shared wording with
the latest user message, not meaning. A conversation that still does not fit (a very large system
prompt or user message) is sent as it is, and the provider's limit applies. The direct router is
not managed this way; it has no accumulated tool results.

## Structured output

An agent can declare the shape of what it returns. While enforcement is on, an answer that does
not have that shape is not returned as a completed result (`core/prompts/output_schema.py`).

**Declaring a schema.** Two ways:

- The agent's own schema: `PUT /agents/{id}/output-schema` with `{"schema": {...}}`, or
  `{"schema": null}` to remove it. It is a JSON Schema (2020-12) for an object, at most 32,000
  bytes, self-contained (`$ref` is refused) and checked to be a valid schema when it is stored.
  It changes what the agent may return, so, like the prompt, it cannot be changed while the agent
  is active, and the agent's edit rules apply. It is stored in the agent's `config` under
  `output_schema_json`.
- A registered name: the agent's existing `output_schema` field, when it names one of the
  platform's registered document schemas.

The agent's own schema wins when both are set.

**What happens on a run.** After the model's final answer is parsed, it is validated:

1. A valid answer completes as before.
2. An invalid answer goes back to the model with what is wrong (the JSON path and the schema's
   message for each problem, at most ten), up to two times.
3. An answer that is still invalid is escalated to a human reviewer with the trigger
   `output_schema_invalid`, whatever its confidence. The reviewer sees the answer and decides;
   the run does not end as `completed` on its own.
4. A declared schema that cannot be used (a name that is not registered, a stored schema that is
   no longer valid) escalates the same way with `output_schema_unusable`. An agent that says it
   has a schema does not run as if it had none.

A run refused by grant enforcement ends as it did, before any of this. An agent with no declared
schema is not affected.

**Switch.** `AGENTICORG_OUTPUT_SCHEMA_ENFORCED`, off by default. Off, nothing is validated and
runs end as before; schemas can still be stored. Before turning it on, look at the `output_schema`
names existing agents carry: a name that is not a registered schema escalates every run of that
agent (point 4). Clear the name or give the agent its own schema first.

**Observability.** The run's trace records each correction and the escalation.
`agenticorg_output_schema_checks_total{result}` counts `valid`, `repaired` (valid after a
correction), `retry`, `escalated` and `unusable`. No tenant or agent label, and no answer content;
a schema message can quote a short value from the answer, so messages are bounded and go only to
the model and the reviewer.

**Limits.** Enforcement is on runs started through the agents API (`POST /agents/{id}/run`), which
is where an agent's stored configuration is read. Runs started by the voice channel and by the
typed agent entry points do not pass a schema and are not validated. Each correction is one more
model call. A reviewer who approves an escalated answer releases it as it is.

## Tests

`tests/unit/test_prompt_parameters.py` covers declarations (the old form, defaults, every refused
shape, all problems reported together), template checks (order, tool references, undeclared and
unused), resolution and rendering (type reading, bounds, defaults, missing and unknown values, no
placeholder left behind, a value never read as a placeholder) and the endpoints, including writes
with the switch off and on.

`tests/unit/test_prompt_change_requests.py` covers the switch (setting, tenant flag, an unreadable
flag), identity, proposing (one pending per template), approval (the change applied and both
people recorded), the proposer refused, a stale request not applied, delete and create, rejection
and withdrawal, domain access, the four write paths with the switch off and on, and the
endpoints. `ui/src/__tests__/PromptChangeRequests.test.tsx` covers the review panel.

`tests/unit/test_agent_prompt_activation.py` covers activation: off, the author refused and
another user allowed, the last editor as the author, an API key, a change with no author, a pause
and resume with and without a prompt change, creating straight into active, an unreadable switch,
the first-prompt history entry, and that every path to `active` in the agents API passes the
check before the status changes. It also covers changes to the variables, amendments and reference,
an agent that was active before the switch, the row lock, the activator on both events and the
pack installer.

`tests/unit/test_prompt_compare.py` covers the models a comparison can call, the bounds, a result
per model with one failing, the call going through the router as the tenant, the concurrency
limit, the dataset rules and every refused shape, scoring, pass rates with errors kept apart,
and the endpoints off and on. `ui/src/__tests__/PromptCompare.test.tsx` covers the panel.

`tests/unit/test_context_window.py` covers the estimates and budgets, a conversation that fits,
omission by relevance and age, that every tool call stays answered and nothing else changes, the
cut when omission is not enough, a conversation that cannot fit, the switch, the metric and that
the reasoning node sends the fitted copy while the grounding check reads the full one.

`tests/unit/test_output_schema.py` covers the schemas an agent may be given, the errors and their
bounds, a registered name and an unregistered one, the switch, accept, correction and escalation,
the metric, the agent graph run with a scripted model (valid, corrected, escalated, an unusable
name, off, and no schema), the endpoint and the lock on active agents.

## What is not here yet

- **Agents are not held to a template's parameters.** An agent's own `prompt_variables` are
  still substituted as plain text when its prompt is built.
- **Other parts of an agent are not under maker-checker.** The check at activation is about
  the prompt: a change to an agent's tools, model or thresholds on a shadow agent is not
  attributed, so it does not by itself require a second person.
- **Evaluation has no console view and no stored datasets or results.** It is an endpoint that
  takes its dataset in the request; nothing gates a change request on an evaluation result.
- **Structured output is enforced on API runs only.** The voice channel and the typed agent
  entry points do not pass a schema. There is no console editor for an agent's schema.
- **No console editor for parameter types.** The prompt templates page shows a template's
  parameters; typed declarations are written through the API.
