# Governed-case domain schemas

The documents a governed business case produces — the case itself, the
ownership graph, screening results and dispositions, the policy result, the
underwriting memo and the push to a system of record — each have a published
JSON Schema. Providers, agents, the policy engine, the console and any system
that receives a case push all agree on these shapes, and nothing in them is
specific to one data provider.

## The schemas

All are [JSON Schema 2020-12](https://json-schema.org/draft/2020-12/release-notes)
files in `schemas/`:

| Schema | `$id` | What it describes |
|---|---|---|
| `business_case` | `https://agenticorg.ai/schemas/business_case/1.0.0` | The application as declared, the business it resolved to, lifecycle state and the human decision |
| `ownership_graph` | `https://agenticorg.ai/schemas/ownership_graph/1.0.0` | Owners and controllers of a business as nodes and edges; percentages are ranges because registries report bands |
| `screening_result` | `https://agenticorg.ai/schemas/screening_result/1.0.0` | One screening of a person or business: the lists checked and every candidate hit |
| `screening_disposition` | `https://agenticorg.ai/schemas/screening_disposition/1.0.0` | A proposed outcome for one hit with per-identifier comparisons, and the analyst's review |
| `policy_result` | `https://agenticorg.ai/schemas/policy_result/1.0.0` | Score, tier and every fired rule of one versioned policy |
| `underwriting_memo` | `https://agenticorg.ai/schemas/underwriting_memo/1.0.0` | The cited memo: sections, findings, policy result, recommendation, missing items |
| `case_push` | `https://agenticorg.ai/schemas/case_push/1.0.0` | The webhook body that hands a case to another system |
| `common` | `https://agenticorg.ai/schemas/common/1.0.0` | Shared definitions (evidence, identifiers, addresses, dates); not a document type |

The `$id` values are identifiers, not download locations: references between
the schemas resolve from the files in this repository and nothing is fetched
over the network.

Every document has additional properties turned off, so an unknown field is an
error rather than something silently carried along.

## Evidence

Every assertion points back at the upstream record it came from with an
evidence entry:

| Field | Meaning |
|---|---|
| `provider` | Registered provider name, e.g. `mock` or `acme_kyb` |
| `record_id` | The provider's identifier for the record |
| `field` | Dotted path of the field within that record, e.g. `share_pct` or `officers[0].name` |
| `retrieved_at` | When the record was retrieved (RFC 3339 with an offset) |
| `excerpt_ref` | An opaque reference to a stored excerpt for the human reviewer (for example the extractor's `exc_<digest>`), or `null` |

All five keys are required; `excerpt_ref` is explicitly `null` when there is
no excerpt. Excerpts are listed in the memo's `excerpts` with a media type and
digest and are never inlined into a document.

In an `underwriting_memo`, every `complete` or `partial` section and every
finding carries at least one evidence entry. Two statuses have no content and
give a reason instead, with no findings and no evidence requirement:

- `not_available` with a `not_available_reason` — `capability_not_supported`
  when the provider does not offer the data, or `no_registry_match` — so a
  narrower provider degrades a memo instead of breaking it;
- `error` with an `error_reason`, the provider error's taxonomy code (for
  example `provider_timeout` or `provider_unavailable`).

A reason may appear only with its own status.

## Rules the schemas enforce

- A memo's recommendation has `basis: "policy_result"` and
  `requires_human_decision: true`; there is no other value.
- A screening disposition compares each of `name`, `date_of_birth`,
  `nationality`, `address` and `associated_entities` exactly once; an
  identifier that cannot be compared is recorded as `not_comparable`. Its
  outcome is `true_match`, `false_positive` or `insufficient_information`; a
  review that overrides the proposal must give a reason.
- A case in state `decided` has a decision; a case in any other state does
  not.
- A `case_push` for `case.completed` or `case.decided` carries a memo.
- A policy result carries no evaluation timestamp, so identical inputs produce
  an identical document.

## Validating a document

`core/domain_schemas.py` loads the schemas and validates documents. It fails
closed: an unknown schema name, a schema that does not load or whose `$id` does
not match its file, a reference outside these schemas and any validation error
all raise `DomainSchemaError` with a `reason` and the full list of `errors`,
each prefixed with its JSON path.

<!-- snippet: tests/contract/test_domain_schema_contracts.py#validate-document -->
```python
from core.domain_schemas import DomainSchemaError, validate

validate("ownership_graph", document)  # returns None: the document conforms

document["as_of"] = "yesterday"
try:
    validate("ownership_graph", document)
except DomainSchemaError as exc:
    assert exc.reason == "document_invalid"
    assert exc.errors == ["$.as_of: 'yesterday' is not a 'date-time'"]
```

| `reason` | Cause |
|---|---|
| `unknown_schema` | No document schema has that name (`common` is not a document type) |
| `schema_unreadable` | The schema file is missing or is not JSON |
| `schema_id_mismatch` | The file's `$id` is not the expected versioned identifier |
| `schema_invalid` | The file is not a valid 2020-12 schema |
| `schema_reference_unresolvable` | A `$ref` points outside the domain schemas |
| `document_invalid` | The document does not conform; `errors` lists every problem |

Date-time, URI, UUID and date formats are asserted, not just annotated. A
date-time must be strict RFC 3339: a full date, `T`, hours, minutes and
seconds (optionally fractional), and `Z` or a `±hh:mm` offset.

## Fixtures

Example documents live in `schemas/examples/<schema>/<case>.json`. The contract
suite (`tests/contract/test_domain_schema_contracts.py`, run in CI) validates
every one against its schema and fails when:

- a fixture does not validate;
- a file sits anywhere other than `<schema>/<case>.json`, or in a directory
  named after no document schema — a fixture without a schema fails closed;
- a document schema has no fixture at all.

Fixtures are plainly synthetic: invented names, reserved identifiers (UK
company numbers such as `00000001`, US EIN `00-0000001`) and `example.com`
domains.

## Versioning

The version is part of `$id` and documents carry `schema_version`.

- **Minor or patch** (`1.0.0` → `1.1.0`): additions that every existing valid
  document still satisfies, such as a new optional field or a new enum value
  that readers must already treat as unknown. `schema_version` accepts any
  `1.x.y`.
- **Major** (`2.0.0`): anything that could make a valid document invalid or
  change a field's meaning. A new major version is a new `$id`; the previous
  one stays published for as long as documents written against it are
  retained.

Change a schema, its fixtures and this page in the same pull request, and note
it in `CHANGELOG.md`.

These schemas are not seeded into tenants' editable schema registries: they are
platform contracts, not tenant entity schemas.
