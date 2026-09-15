# HITL conditions

An agent's `hitl_policy.condition` (stored as `hitl_condition`) decides, after
each run, whether the output goes to a human before anything else happens. It
is checked in addition to the confidence floor: a run below the floor always
goes to review.

The evaluator is `core/langgraph/hitl_condition.py`, shared by the LangGraph
runtime and the legacy agent base class.

## Grammar

- Comparisons: `<`, `<=`, `>`, `>=`, `==`, `!=`, `in`, `not in`; chained
  comparisons such as `0 < amount <= 10` work.
- Combined with `and`, `or`, `not` (or `AND`, `OR`, `NOT`) and parentheses.
- Operands are output keys (`amount`), nested keys (`claims_summary.fraud_score`,
  `items[0]`), numbers, quoted strings, `True` / `False` / `None`, and list
  literals (`plan in ['enterprise', 'pro']`). Numeric strings in the output
  compare as numbers. `confidence` is available even when the output has no
  such key.
- `always`, or `always_<label>` (for example `always_before_filing`), sends
  every run to review.
- An empty condition adds nothing to the confidence floor.

Not supported: function calls, arithmetic, `is`, assignment, and bare values.
Every operand of `and` / `or` / `not` must be a comparison, so a boolean output
key is written `needs_review == True`, not `needs_review`.

## At run time

Anything the evaluator cannot evaluate sends the run to review (fail closed):
a condition outside the grammar, a type error, or a key missing from the
output. A condition such as `high_value_procurement` names no output key, so it
sent every run to review. Grammar failures at run time are counted in
`agenticorg_hitl_condition_parse_failures_total{stage="run", outcome="fail_closed"}`;
a missing key is logged (`hitl_condition_eval_failed_fail_closed`) but not
counted, because the condition itself is well formed.

## When saving

`AGENTICORG_HITL_CONDITION_VALIDATION` controls what happens when a condition
outside the grammar is saved through `POST /agents`, `PUT /agents/{id}`,
`PATCH /agents/{id}`, `POST /agents/generate` with `deploy: true`, and
`POST /sop/deploy` (which joins the parsed `hitl_conditions` with `OR`).

| Value | Behaviour |
|---|---|
| `off` (default) | Accepted, as before. |
| `warn` | Accepted; logged as `hitl_condition_unparseable_on_save` with the reason and counted with `stage="save", outcome="warned"`. |
| `reject` | Refused with `422` and counted with `stage="save", outcome="rejected"`. |

Any other value stops the application from starting. Roll out with `warn`,
review the log and the counter, fix the conditions, then switch to `reject`.

A refused save returns:

```json
{
  "detail": {
    "error": "invalid_hitl_condition",
    "reason": "not_a_comparison",
    "message": "HITL condition cannot be evaluated: 'high_value_procurement' is not a comparison; compare an output key, e.g. high_value_procurement == True"
  }
}
```

Reason codes (also the metric's `reason` label): `syntax_error`,
`unsupported_syntax`, `unsupported_operator`, `not_a_comparison`, and
`invalid_mode` when the validation mode itself is misconfigured.

Conditions already stored are not re-checked; they keep failing closed at run
time until they are edited.

## Shipped industry packs

The CA, healthcare, legal and manufacturing packs use `always_<label>` where
their prompts require review before every filing, registration, opinion,
schedule commit or disposition. Four agents used bare labels and were replaced
with expressions over the keys their prompts' `<output_format>` defines:

| Agent | Was | Now |
|---|---|---|
| insurance `underwriting_analyst` | `high_value_or_complex_risk` | `underwriting_summary.authority_status != 'within' OR underwriting_summary.recommendation != 'bind' OR underwriting_summary.risk_score < 40` |
| insurance `claims_adjudicator` | `high_value_or_fraud_indicator` | `claims_summary.loss_amount > 2500 OR claims_summary.reserve > 30000 OR claims_summary.fraud_score > 60 OR claims_summary.coverage_verified != True` |
| insurance `policy_manager` | `cancellation_or_major_endorsement` | `policy_summary.action not in ['issuance', 'renewal', 'billing']` |
| manufacturing `supply_chain_optimizer` | `high_value_procurement` | `supply_chain_summary.total_po_value > 30000 OR supply_chain_summary.vendor_flags > 0` |

Choices made where the output cannot express the prompt's rule exactly:

- The summaries carry no currency, and the prompts give each threshold in two
  currencies (for example INR 2,00,000 / USD 2,500). The conditions use the
  lower number so that neither currency skips review; in INR most claims and
  purchase orders will still reach a reviewer. Tenants that know their
  currency should set their own thresholds.
- "Major endorsement" is defined by the premium change as a share of premium,
  which needs arithmetic the grammar does not have, so every endorsement,
  cancellation, reinstatement and unrecognised action goes to review.
- Single-source procurement has no output key; `vendor_flags > 0` is the
  nearest signal.

Agents installed from a pack keep their stored condition until the pack is
re-synced.
