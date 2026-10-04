# Prompt governance

How prompt templates are declared, checked and filled. This page covers typed parameters, the
first part of the prompt governance package; approval before production, side-by-side
comparison, evaluation against datasets and context-window management follow in later parts and
are listed at the end.

Implementation: `core/prompts/parameters.py` (declarations, checks, resolution, rendering) and
`api/v1/prompt_templates.py` (the template endpoints).

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
| `max_length`, `pattern` | a string's limit (at most 20,000 characters) and a regular expression it must match in full; patterns with shapes that backtrack catastrophically are refused |

A variable declared the old way, a name with or without a description, is a required string, so
existing templates read as they did. A template has at most 50 parameters. Any other key is
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
(`"80"` is a valid integer, `"yes"` a valid boolean), held to its bounds, and a default fills a
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
| `AGENTICORG_PROMPT_TYPED_PARAMETERS_ENABLED` | `false` | On, creating or changing a template checks its parameters and its text together and stores the parameters in their checked form; a bad declaration or an undeclared placeholder is a 422. Off, a template is stored as it was given, as before. |

On an update the template is checked as the template it will be after the change, so new text
cannot use a placeholder the stored parameters do not declare. The check and render endpoints
work whatever the switch is.

## Tests

`tests/unit/test_prompt_parameters.py` covers declarations (the old form, defaults, every refused
shape, all problems reported together), template checks (order, tool references, undeclared and
unused), resolution and rendering (type reading, bounds, defaults, missing and unknown values, no
placeholder left behind, a value never read as a placeholder) and the endpoints, including writes
with the switch off and on.

## What is not here yet

- **Agents are not held to a template's parameters.** An agent's own `prompt_variables` are
  still substituted as plain text when its prompt is built.
- **No approval before production** (maker-checker), **no side-by-side comparison** across
  models, **no evaluation against a reference dataset**, **no context-window management** and
  **no structured-output enforcement** beyond the governed-case agents. These are the package's
  next parts.
- **No console editor for parameter types.** The prompt templates page shows a template's
  parameters; typed declarations are written through the API.
