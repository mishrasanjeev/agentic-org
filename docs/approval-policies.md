# Approval policies

An approval policy turns one approval item into a chain of steps. Each step names an approver role,
a quorum (how many approvals it needs) and an optional condition. `POST /api/v1/approvals/{id}/decide`
feeds each decision through the policy engine (`core/approvals/policy_engine.py`): a step collects
approvals until its quorum is met, the item then moves to the next step that applies, and it is
decided only when no step remains. A rejection at any step rejects the item.

## Rules a policy cannot be talked out of

- **One vote per person per item.** A reviewer who has voted at any step of an item cannot vote on
  it again, at the same step or a later one - approve or reject. The approval count resets when an
  item moves to its next step, so a per-step check would let one person satisfy each step in turn
  and decide a multi-person policy alone. A person is recognised by every identifier their session
  carries (user id, subject and email), so signing in a second way does not make them a second
  reviewer. A second vote gets `409` and the item is unchanged.
- **A step whose condition cannot be evaluated applies.** Skipping a step removes approvals, so
  "cannot tell" never skips one. A condition cannot be evaluated when it names a field the item does
  not carry, compares a non-number with `<`, `>`, `<=` or `>=`, uses a list that does not parse,
  has a malformed operand (`status ==`, `status === ok`, an unterminated quote, an unquoted value
  with spaces), or is not in the grammar. The step applies and a warning is logged
  (`approval_policy_condition_unevaluable`).
- **A policy that changes mid-approval stops the item.** An item part-way through a policy stays
  bound to it. If that policy is deleted, another policy now resolves for the item, or the step it
  is waiting on no longer exists, decisions on the item get `409` and it stays pending for an
  administrator. It is never decided on the next vote with no policy, or the wrong one, applied.

## What this means for operators

These rules refuse rather than guess, so a policy that relied on the old behaviour can leave an item
unable to finish:

- **A person who voted early cannot act later.** Anyone whose role is senior enough may approve a
  junior step. If the only CFO approves a manager step, they cannot also approve the later CFO step,
  and the item cannot complete. Likewise a policy that asks for the same role at two steps needs
  that many different people in the role, and someone who approved at step 1 cannot reject at step
  2. Design steps so that each needs people who have not been asked before.
- **Editing a policy strands its in-flight items.** Policies are changed by deleting and recreating
  them, and adding a more specific policy changes which one resolves. Either way, items part-way
  through the old policy refuse every decision from then on.
- **There is no override yet.** A stranded item stays pending until it expires (`expires_at` - four
  hours for agent and chat approvals, the workflow's own timeout for workflow approvals); no
  endpoint resets it or moves it to a new policy (FINDINGS A-67). Change policies when no item is in
  flight, or expect to re-raise the affected items.
- **Check conditions against real items.** A condition naming a field the item does not carry now
  requires its step instead of skipping it.

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

Each operand is a quoted string or a single token with no spaces, quotes or comparison characters:
`risk-level == high`, `owner == a@b.com` and `'admin' in roles` are all fine. Values with spaces
must be quoted (`status == 'two words'`), and an apostrophe inside double quotes is fine
(`name == "O'Brien"`). `<`, `>`, `<=` and `>=` compare numbers, or ISO dates when both sides are
dates (`created_at > '2026-01-01'`), with time zones taken into account; a date with a time zone
cannot be ordered against one without. Anything else - `tier >= 'b'`, `version < '1.10'`, an amount
written as `1,50,000` - is unknown, because comparing it character by character would give a
confident wrong answer. A boolean field compares with `true` and `false` (`flag == true`).

Name fields exactly as the item's context carries them. A condition on `output.amount` for an item
that carries `amount` is unknown, and its step always applies.
