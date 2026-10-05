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
written on every create and clone by a signed-in user, whatever the switch.

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

## What is not here yet

- **Agents are not held to a template's parameters.** An agent's own `prompt_variables` are
  still substituted as plain text when its prompt is built.
- **Other parts of an agent are not under maker-checker.** The check at activation is about
  the prompt: a change to an agent's tools, model or thresholds on a shadow agent is not
  attributed, so it does not by itself require a second person.
- **No side-by-side comparison** across models, **no evaluation against a reference dataset**,
  **no context-window management** and **no structured-output enforcement** beyond the
  governed-case agents. These are the package's next parts.
- **No console editor for parameter types.** The prompt templates page shows a template's
  parameters; typed declarations are written through the API.
