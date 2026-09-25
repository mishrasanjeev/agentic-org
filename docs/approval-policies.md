# Approval policies

An approval policy turns one approval item into a chain of steps. Each step names an approver role,
a quorum (how many approvals it needs) and an optional condition. `POST /api/v1/approvals/{id}/decide`
feeds each decision through the policy engine (`core/approvals/policy_engine.py`): a step collects
approvals until its quorum is met, the item then moves to the next step that applies, and it is
decided only when no step remains. A rejection at any step rejects the item.

## Rules a policy cannot be talked out of

- **One vote per person per item.** A reviewer who has voted at any step of an item cannot vote on
  it again, at the same step or a later one. The approval count resets when an item moves to its
  next step, so a per-step check would let one person satisfy each step in turn and decide a
  multi-person policy alone. A second vote gets `409` and the item is unchanged.
- **A step whose condition cannot be evaluated applies.** Skipping a step removes approvals, so
  "cannot tell" never skips one. A condition cannot be evaluated when it names a field the item does
  not carry, compares a non-number with `<`, `>`, `<=` or `>=`, uses a list that does not parse, or
  is not in the grammar. The step applies and a warning is logged
  (`approval_policy_condition_unevaluable`).
- **A step that disappears mid-approval stops the item.** If the policy is edited and the step an
  item is waiting on no longer exists, decisions on that item get `409` and it stays pending for an
  administrator. It is never decided on the next vote with no policy applied.

## Conditions

Conditions use the grammar of `workflows/condition_evaluator.py` - comparisons, `in` and `not in`
over lists, and `AND`/`OR`/`NOT` - evaluated over the item's context:

```text
amount > 1000000
plan in ['enterprise']
status == mismatch
```

For a policy step they are evaluated three-valued (`evaluate_condition_strict`): true, false, or
unknown. `AND`, `OR` and `NOT` combine the answers by Kleene logic, so `a > 1 OR b > 1` is true when
`a` is 5 even if `b` is missing, `a > 1 AND b > 1` is false when `a` is 0 even if `b` is missing, and
`NOT amount > 100` is unknown - not true - when `amount` is missing. An unknown answer makes the
step apply.

Name fields exactly as the item's context carries them. A condition on `output.amount` for an item
that carries `amount` is unknown, and its step always applies.
