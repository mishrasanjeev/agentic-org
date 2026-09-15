# Authoring and versioning case policies

A case policy is a versioned YAML file that turns a case's evidence into a
**tier**, a **score** and an ordered list of **reasons**. The engine in
`core/policy/` evaluates it deterministically: no model is involved, the same
policy and evidence always give the same result, and a model's confidence is
never an input (see [ADR 0011](../adr/0011-policy-over-confidence.md)).

> **The shipped policies are examples.** `core/policy/examples/business_onboarding_us.yaml`
> and `business_onboarding_uk.yaml` show the format. They have not been reviewed
> by a compliance owner, encode no regulatory advice and must not be used to make
> real onboarding recommendations. Loading one logs `policy_example_loaded` at
> warning level every time. Copy one, have a named compliance owner review and
> adapt every rule, and mark it `status: production` with `reviewed_by` set.

Every YAML block on this page is loaded by
`tests/unit/policy/test_policy_docs_examples.py`, so the examples cannot drift
from what the loader accepts.

## A complete policy

```yaml
policy: business_onboarding_uk
version: 1.2.0
status: example
reviewed_by: null
description: EXAMPLE ONLY - requires compliance review before any real use.
score_thresholds:
  high: 60
rules:
  - id: registry_active
    when: {verification.status: {not_in: [active]}}
    effect: {tier: high, reason: "Registry status is not active"}
  - id: ownership_reconciled
    when: {ownership.missing_owners: {gt: 0}}
    effect: {tier: medium, reason: "Declared owners do not reconcile with the ownership graph"}
  - id: screening_clear
    when: {screening.unresolved_true_matches: {gt: 0}}
    effect: {tier: blocked, reason: "Unresolved true match"}
```

| Key | Required | Meaning |
|---|---|---|
| `policy` | yes | Policy id: lower-case snake_case, up to 64 characters. |
| `version` | yes | [Semantic version](https://semver.org) as a string, e.g. `1.2.0` or `2.0.0-rc.1`. `1.2` is refused. |
| `status` | no | `example` (the default) or `production`. |
| `reviewed_by` | for `production` | The compliance owner who reviewed this version. Placeholders such as `TODO`, `tbd`, `n/a`, `none` or a single character are refused. |
| `description` | no | Free text for people. |
| `score_thresholds` | no | Minimum scores that raise the tier; see [Tiers and score](#tiers-and-score). |
| `rules` | yes | A non-empty list of rules. |

Each rule has an `id` (snake_case, unique within the policy), a `when`
condition, an `effect` with `tier`, `reason` and optionally `score`, and an
optional `description`. Any other key anywhere in the file is refused.

## Evidence paths

Conditions read a **plain nested mapping** of the case's evidence fields by
dotted path. `verification.status` reads `evidence["verification"]["status"]`.
A path is one or more lower-case snake_case segments, at most eight deep; list
indexing is not supported. The engine does not know the case schema — the
workflow that builds the evidence mapping decides what each path means — so
keep paths aligned with the published case schemas.

## Conditions and operators

A condition is a mapping with **exactly one key**: either a dotted path mapped
to **exactly one operator**, or one of the combinators `all`, `any`, `not`.
Combine several tests with `all` rather than putting two keys in one mapping.
`all`, `any` and `not` cannot be used as single-segment path names.

| Operator | Operand | True when the value at the path… |
|---|---|---|
| `eq` | string, number or boolean | equals the operand |
| `ne` | string, number or boolean | does not equal the operand |
| `in` | non-empty list, all one type | equals one of the items |
| `not_in` | non-empty list, all one type | equals none of the items |
| `gt`, `gte`, `lt`, `lte` | number | compares as stated |
| `exists` | `true` | is present and not `null` |
| `missing` | `true` | is absent or `null` |

Comparisons never convert types. Strings compare exactly (case-sensitive).
Numbers compare numerically (`1` equals `1.0`). Booleans are not numbers, so
`true` never equals `1`. `null` is not an operand: use `exists` or `missing`.
Integers must lie within ±2^53 (the range JSON consumers agree on), both as
operands and as evidence values.

| Combinator | Takes | Meaning |
|---|---|---|
| `all` | non-empty list of conditions | every item holds |
| `any` | non-empty list of conditions | at least one item holds |
| `not` | one condition | the item does not hold |

```yaml
policy: combinators_example
version: 0.1.0
rules:
  - id: owners_do_not_reconcile
    when:
      any:
        - {ownership.missing_owners: {gt: 0}}
        - {ownership.undeclared_owners: {gt: 0}}
    effect: {tier: medium, reason: "Declared owners do not reconcile with the ownership graph"}
  - id: registry_not_matched
    when:
      not: {verification.registry_match: {eq: true}}
    effect: {tier: high, reason: "No registry record matched the application"}
```

YAML note: the loader treats only `true`/`false` as booleans. `yes`, `no`, `on`
and `off` stay strings, and dates such as `2026-01-01` stay strings. Explicit
YAML tags (`!!int`, `!!str`, `!custom`, `!`) are refused; write values plainly
and quote strings that would otherwise read as numbers.

## Missing evidence

Evidence is often incomplete: a provider may not offer ownership data, a lookup
may have failed, a field may hold the wrong type. The engine never lets that
silently pass.

- A comparison whose path is **absent or `null`**, or whose value the operator
  cannot compare (wrong type, a list or mapping, `NaN` or infinity, an integer
  outside ±2^53), is **unresolved** — neither true nor false.
- `exists` and `missing` are never unresolved for evidence that can be read;
  they are how you test for absence explicitly.
- If reading a path raises (for example a mapping backed by a failing store),
  the path is recorded as `{"non_scalar": "unreadable"}` in `inputs`, logged as
  `policy_evidence_unreadable`, and **every** operator on it — including
  `exists` and `missing` — is unresolved, so the rules that read it fire.
  Evaluation itself never raises on evidence content.
- Combinators use three-valued logic. `not` of unresolved is unresolved. `all`
  is false if any item is false, otherwise unresolved if any item is
  unresolved. `any` is true if any item is true, otherwise unresolved if any
  item is unresolved.
- **A rule whose condition is unresolved fires.** Its reason is marked
  `indeterminate: true` and lists the `unresolved_paths`.

Because firing only ever raises the tier and adds score, missing evidence moves
a case towards the stricter tier. In the complete policy above, an empty
evidence mapping fires all three rules and the case is `blocked`, with every
reason indeterminate.

| Operator | Path absent or `null` | Present, wrong type or non-finite | Reading raised |
|---|---|---|---|
| `eq`, `ne`, `in`, `not_in` | unresolved → rule fires | unresolved → rule fires | unresolved → rule fires |
| `gt`, `gte`, `lt`, `lte` | unresolved → rule fires | unresolved → rule fires | unresolved → rule fires |
| `exists` | false | true | unresolved → rule fires |
| `missing` | true | false | unresolved → rule fires |

When a rule should apply only to evidence that is present, guard it with
`exists` **and** make sure another rule handles the absent case. The examples
do this for a dissolved registry status:

```yaml
policy: explicit_absence_example
version: 0.1.0
rules:
  - id: registry_dissolved
    when:
      all:
        - {verification.status: {exists: true}}
        - {verification.status: {in: [dissolved]}}
    effect: {tier: blocked, reason: "Registry record shows the company as dissolved"}
  - id: registry_active
    when: {verification.status: {not_in: [active]}}
    effect: {tier: high, reason: "Registry status is not active"}
```

With no status, `registry_dissolved` does not fire and `registry_active` fires
as indeterminate (`high`). With `status: dissolved` both fire (`blocked`).

## Tiers and score

Tiers, least to most severe: `low` < `medium` < `high` < `blocked`.

- Each fired rule contributes its `score`, a whole number from 0 to 100. When
  the effect does not declare one it contributes the tier default: `low` 0,
  `medium` 20, `high` 50, `blocked` 100.
- The case **score** is the sum of fired rule scores, capped at 100.
- The case **tier** is the most severe fired rule's tier, or `low` if nothing
  fired.
- `score_thresholds` optionally raises the tier when the score reaches a
  minimum, so several moderate findings can add up to a stricter tier. It never
  lowers a tier. Thresholds are whole numbers from 1 to 100 and must increase
  with severity; `low` takes none. `tier_source` in the result says whether
  the tier came from `rules` or a `score_threshold`.

```yaml
policy: scoring_example
version: 0.1.0
score_thresholds: {medium: 10, high: 60}
rules:
  - id: minor_observation
    when: {web_presence.activity_mismatch: {eq: true}}
    effect: {tier: low, reason: "Observed activity differs from the declared activity", score: 10}
  - id: possible_screening_match
    when: {screening.unresolved_possible_matches: {gt: 0}}
    effect: {tier: medium, reason: "Screening hits awaiting disposition", score: 50}
```

Here a lone `minor_observation` is raised to `medium` (score 10), and both
rules together reach `high` (score 60).

## Results

`core.policy.evaluate(policy, evidence)` returns a `PolicyResult`;
`result.to_dict()` is JSON-safe and is what the memo and evidence package
record:

| Field | Meaning |
|---|---|
| `policy_id`, `policy_version`, `policy_status`, `reviewed_by` | From the policy file |
| `policy_hash` | `sha256:` over the exact bytes of the policy file that was loaded |
| `engine_version` | Version of these evaluation semantics |
| `tier`, `tier_source`, `score` | As above |
| `reasons` | Fired rules, most severe tier first, then in file order: `rule_id`, `tier`, `score`, `reason`, `indeterminate`, `unresolved_paths` |
| `fired_rules` | The rule ids of `reasons`, in the same order |
| `inputs` | Every path the policy reads, with the value read: a scalar, `null` when missing, or `{"non_scalar": "mapping" \| "list" \| "non_finite_number" \| "integer_out_of_range" \| "unreadable" \| "other"}` |
| `missing_inputs`, `invalid_inputs` | Referenced paths that were absent, or present but unusable, sorted |
| `inputs_hash` | `sha256:` over the canonical JSON of `inputs` |

The result contains no timestamp, so it is byte-for-byte reproducible; the
caller records when it evaluated.

## Loading fails closed

Policies are loaded when the process starts, never while a case is evaluated.
`load_policy(path)`, `load_policy_bytes(data)` and `load_policies(directory)`
refuse anything that is not a valid policy with a `PolicyLoadError` naming the
file, a stable reason code and the location, for example
`policies/uk.yaml: policy_unknown_operator at rules[0].when.all[1].b.between: …`.
A directory loads every file ending in `.yaml` or `.yml` in any letter case,
and is refused as a whole if any file in it is invalid, if it is empty, or if
two files declare the same policy id. Pass `require_production=True` in
deployments that must refuse example policies.

| Reason code | Cause |
|---|---|
| `policy_file_unreadable` | The file cannot be read |
| `policy_too_large` | Over 256 KiB |
| `policy_encoding_invalid` | Not UTF-8 |
| `policy_yaml_invalid` | Not parseable YAML, more than one document, an explicit tag, or a value the YAML reader cannot build (such as an integer over Python's digit limit) |
| `policy_duplicate_key` | The same key twice in one mapping |
| `policy_yaml_alias_forbidden` | YAML aliases or merge keys |
| `policy_missing_field` | A required key is absent or `null` |
| `policy_unknown_key` | A key the format does not define |
| `policy_invalid_value` | Wrong type or value: ids, tiers, reasons, scores, status, thresholds |
| `policy_invalid_version` | `version` is not a semantic version string |
| `policy_invalid_condition` | A condition without exactly one key, a path without exactly one operator, an empty combinator |
| `policy_unknown_operator` | An operator not in the table above |
| `policy_invalid_path` | A path that is not dotted lower-case snake_case, or deeper than eight segments |
| `policy_invalid_operand` | An operand of the wrong type for its operator |
| `policy_duplicate_rule_id` | Two rules with the same id |
| `policy_limit_exceeded` | More than 500 rules, conditions nested more than 12 deep, over-long strings or lists |
| `policy_production_unreviewed` | `status: production` without `reviewed_by`, or with a placeholder reviewer |
| `policy_not_production` | `require_production=True` and the policy is not production |
| `policy_directory_invalid` | Missing or empty policy directory |
| `policy_duplicate_policy_id` | Two files in a directory declare the same `policy` |

```yaml
# rejected: policy_production_unreviewed
policy: business_onboarding_uk
version: 2.0.0
status: production
rules:
  - id: registry_active
    when: {verification.status: {not_in: [active]}}
    effect: {tier: high, reason: "Registry status is not active"}
```

```yaml
# rejected: policy_invalid_condition
policy: two_keys_in_one_condition
version: 0.1.0
rules:
  - id: owners
    when: {ownership.missing_owners: {gt: 0}, ownership.undeclared_owners: {gt: 0}}
    effect: {tier: medium, reason: "Use all or any to combine tests"}
```

## Versioning

- A policy file is identified by `policy` + `version` + `policy_hash`, and all
  three are recorded with every result. Change the version whenever a rule, a
  tier, a score, a threshold or a reason changes; the hash catches an edit
  that forgot to.
- Follow semantic versioning in spirit: a change that can make any case
  **stricter or less strict** is at least a minor version; a change that can
  make a case less strict should be treated as major and reviewed as such;
  wording-only changes to `description` are a patch.
- Keep superseded versions in version control so any past result can be
  re-evaluated with the policy that produced it (and the same
  `engine_version`).
- A `production` policy names its reviewer in `reviewed_by`. A new version is
  a new review: update `reviewed_by` for each version you promote.
- Policy files are hashed byte for byte, so check them in with fixed line
  endings (`.gitattributes` does this for the shipped examples).

## Operating

- `agenticorg_policy_evaluations_total{tier, policy_status}` counts
  evaluations: the tier distribution, and whether example policies are being
  evaluated at all (`policy_status="example"` in a production environment is a
  finding).
- `agenticorg_policy_load_total{outcome, reason}` counts loads and refusals by
  reason code.
- Logs: `policy_example_loaded` (warning), `policy_loaded` (info, with
  `reviewed_by` and `content_hash`), `policy_load_rejected` (error). Evidence
  values are never logged.

## Relationship to approval policies

`core/approvals/policy_engine.py` is a different thing with a similar name: it
routes an approval request through approver steps (who approves next, quorum).
A case policy decides how risky a case is; an approval policy decides who must
sign off once a human review is requested. See ADR 0011.
