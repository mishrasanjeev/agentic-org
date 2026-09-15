# Pseudonymisation before the model

With the per-tenant flag `pseudonymisation.pre_model` on, AgenticOrg replaces
personal data with placeholders before any text is sent to a language model,
and puts the real values back only where they are needed: in tool calls, and
in results shown to people. A model sees `[[PERSON_1:3fa9c2]]`; the connector
that screens or emails that person receives the name.

The service is `core/pii/pseudonymiser.py`. The recognisers for United States,
United Kingdom and European identifiers are in
`core/pii/international_recognizers.py`.

## Turning it on

The flag is off by default. A tenant administrator enables it for their tenant
through the feature-flag API (`POST /api/v1/feature-flags` with flag key
`pseudonymisation.pre_model`, `enabled` true and `rollout_percentage` 100).

The flag is read once when a run starts (flag reads are cached for 30 seconds).
If the flag cannot be read, the run proceeds as if it were off, which means
the existing redaction described under [With the flag off](#with-the-flag-off)
still applies.

Roll it out as PRD §10 describes: staging with synthetic traffic first, then
one internal tenant, watching the metrics below.

## What is masked

| Kind | Entity types | How it is found |
|---|---|---|
| Names | `PERSON` | Values of personal-data fields anywhere in the task input (`full_name`, `first_name`, `last_name`, `surname`, `director_name`, `beneficial_owner_name`, … see `_KEY_ENTITIES`); in free text, names of two or more words found by the NLP analyser when it is installed (it is required in strict runtimes) |
| Dates of birth | `DATE_OF_BIRTH` | Fields `dob`, `date_of_birth`, `birth_date`; in free text, a date after a label such as "date of birth", "DOB" or "born on" |
| Addresses | `ADDRESS` | Fields `address`, `street_address`, `registered_address`, `address_line1`, `postcode`, … and every part of an address object except `country`, `state`, `region`, `province` and `county` |
| United States | `US_SSN`, `US_ITIN`, `US_EIN` | Recognisers (below) and fields `ssn`, `itin`, `ein` |
| United Kingdom | `UK_NINO`, `UK_COMPANY_NUMBER` | Recognisers and fields `nino`, `national_insurance_number`, `company_number` |
| Europe | `EU_VAT`, `IBAN_CODE` | Recognisers and fields `vat_number`, `iban` |
| India | `AADHAAR`, `PAN`, `GSTIN`, `UPI` | Patterns and fields of the same names |
| Contact | `EMAIL_ADDRESS`, `PHONE_NUMBER` | E-mail pattern, Indian mobile numbers, and fields `email`, `phone`, `mobile` |
| Other identifiers | `PASSPORT_NUMBER`, `TAX_ID`, `BANK_ACCOUNT` | Fields `passport_number`, `tax_id`, `account_number` |

### How the identifier recognisers limit false positives

- **Checksums.** An IBAN must have the right length for its country and pass
  ISO 13616 mod 97. A VAT number must pass its national check digits for AT,
  BE, DE, DK, FI, FR, GB, HR, HU, IT, LU, NL, PL, PT, SE, SI and XI. A failing
  checksum is never masked as that type.
- **Specific shapes.** A dash-delimited `ddd-dd-dddd` is an SSN (an ITIN when
  it starts with 9 and its middle group is in the ITIN ranges). A
  dash-delimited EIN with a prefix the IRS assigns, and a National Insurance
  number with allocated prefix letters, need nothing else.
- **Labels.** Shapes that are otherwise ordinary numbers are only masked when a
  label appears in the 48 characters before them: bare nine-digit numbers
  ("SSN", "ITIN", "EIN", "TIN", "tax id"), Companies House numbers ("company
  number", "Companies House", "registered number", "CRN"), EINs with an
  unassigned prefix, National Insurance numbers with a reserved prefix, and
  VAT numbers for countries without a check-digit rule here ("VAT", "TVA",
  "IVA", "BTW", "MwSt", "USt", …). `EIN` and `TIN` only count in upper case.

Issuance rules are not applied: a mistyped or never-issued number in a
recognisable shape is still masked.

## Where it applies

**Before every model call, on both model paths, including the system prompt.**

- LangGraph agents (`core/langgraph/runner.py`, `agent_graph.py`): the runner
  pseudonymises the task input, the user message and the system prompt before
  they enter the graph state, and the `reason` node pseudonymises every message
  again immediately before each call to the model built by
  `core/langgraph/llm_factory.py`. Tool results are pseudonymised before they
  are added to the conversation. The plain-language explanation of a run,
  which is also written by a model, is given the pseudonymised output and
  trace.
- `LLMRouter.complete` (`core/llm/router.py`), used by `BaseAgent`: every
  message is pseudonymised before the primary or fallback model is called.

The model is told, in a short block appended to the system prompt, to copy
placeholders exactly, including into tool arguments.

**Restored inside the tool boundary.** Tool arguments are restored in the
LangGraph tool wrapper (`core/langgraph/tool_adapter.py`), in
`execute_agent_tool` and in `ToolGateway.execute`, before authorisation checks
and dispatch. Run output, reasoning trace, HITL trigger text and the
explanation are restored before they are returned.

## Stability and storage

A value gets one placeholder for the whole case: the same name has the same
placeholder in the task, in tool results, in later model turns and after a
human-in-the-loop pause or a restart. The case is `case_id` in the task (at the
top level, in `inputs` or in `context`; 1–200 characters of `A-Za-z0-9._:@-`),
otherwise the run's thread or workflow-run id.

The map is stored in `case_pseudonym_maps`, one row per tenant and case,
encrypted with `encrypt_for_tenant` (the tenant's BYOK key, the platform key,
or the vault keyring), under row-level security. Writes take a row lock, so
two workers on the same case never give one placeholder two values. The run's
checkpoint records the case, and resuming reloads the map, even if the flag
has been turned off since. The table is registered with `verify_all` so key
retirement sees it.

The six hex characters in a placeholder are a random tag per case. Text from
outside the case, such as a web page or a filing, cannot write a placeholder
that restores to one of the case's values without knowing it.

## Failing closed

| Situation | What happens |
|---|---|
| The map cannot be read or written | No model call is made. A LangGraph run fails with `pseudonymisation_unavailable: <reason>`; a `BaseAgent` step fails with the reason. |
| A tool argument contains a placeholder that is unknown for the case, belongs to another case, is damaged (for example `[[PERSON_1:3fa9c`), or appears in an argument name | The tool call is refused with `E1012 pseudonym_restore_failed: <reason>` and is never dispatched, with the placeholder or partly restored. The map is reloaded once first, in case another worker on the case added the placeholder. |
| A resumed run's map cannot be read | The resume fails with `pseudonymisation_unavailable: <reason>`. |
| A `case_id` is malformed | The run fails with `case_id_invalid` rather than starting a separate map. |

Reasons: `map_store_unavailable`, `map_store_failed`, `map_unreadable`,
`tenant_invalid`, `case_id_invalid`, `unknown_pseudonym`,
`malformed_pseudonym`, `pseudonym_in_argument_name`,
`pseudonymisation_unstable`.

## Metrics

All low cardinality; no tenant, case or value is ever a label.

| Metric | Labels |
|---|---|
| `agenticorg_pii_pseudonymised_total` — distinct values given a placeholder | `entity_type` (the types above, or `other`) |
| `agenticorg_pii_pseudonym_restore_refused_total` — tool calls refused | `reason` |
| `agenticorg_pii_pseudonym_store_failures_total` — map reads or writes that failed | `reason` |

## Limits

- **Names in free text need the NLP analyser**, and only names of two or more
  words are taken from it, to avoid masking ordinary capitalised words. A
  first name that appears alone in free text is masked only if it is also the
  value of a name field in the task.
- **A bare `name` field is not treated as a person**, because it is as often a
  company, product or tool name. Use `full_name` or another listed field.
- **Exact values.** A placeholder stands for one exact spelling: `Quinta
  Placeholder` and `QUINTA PLACEHOLDER` get different placeholders.
- **Masked everywhere in the case.** A known value is replaced wherever it
  appears as a whole word, including in the system prompt. Values under two
  characters, and all-digit values under six digits (ZIP codes, house
  numbers), are not given placeholders from fields, so amounts elsewhere are
  not hidden.
- **Only string values.** A number stored as a JSON number in a personal-data
  field is not masked.
- **Coverage of other patterns.** Phone numbers other than Indian mobiles,
  passport numbers in free text, and most national identifiers outside the
  United States, United Kingdom, European VAT/IBAN and India are not
  recognised in free text.
- **Other model callers are not covered.** Pseudonymisation applies to agent
  runs on the two paths above. The feedback analyser, SOP parser, agent and
  workflow generators, the marketing content factory's own completions and,
  with the flag off, the run explanation send text to a model without it.
- **Tool call log.** The `tool_calls` log returned with a run shows the model's
  arguments with placeholders and pseudonymised results.
- **Placeholders are case data, not secrets.** Anyone who can read a case's
  model conversation (for example from the checkpoint store) can see its tag.

## With the flag off

Nothing changes. The existing `AGENTICORG_PII_REDACTION_MODE=before_llm`
redaction (`core/pii/redactor.py`) masks the task input for LangGraph runs
with per-run tokens such as `<PERSON_1>`; those tokens are not stored, the
system prompt and the `LLMRouter` path are not masked, and a resumed run cannot
restore them.
