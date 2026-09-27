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
  (`approval_policy_condition_unevaluable`). An operator can have such items refuse every decision
  except a rejection instead, with a reason the reviewer sees - see
  [When a condition cannot be evaluated](#when-a-condition-cannot-be-evaluated).
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

## When a condition cannot be evaluated

By default a step whose condition cannot be evaluated for an item applies, and the vote counts
toward its quorum; only the server log says the condition was unknown. The operator-managed authority
flag `approvals.unevaluable_condition` can refuse the decision instead:

| Mode | Flag rows | A decision on an item whose policy has a step that cannot be evaluated for it |
|---|---|---|
| `off` (default) | none enabled | The step applies and the vote is counted, as above. |
| `deny` | `approvals.unevaluable_condition.deny` enabled | A rejection is taken as usual. Any other decision is refused with `409` and a reason code, and nothing is counted. |

In `deny` mode:

- **Every step is checked before any vote counts,** not only the step the item is waiting on: a
  later step that cannot be evaluated decides where the item goes once the current step is
  satisfied. A policy whose conditions all evaluate for the item is unaffected.
- **A rejection is taken; every other decision is refused.** The policy cannot say which steps, and
  so which approvers, the item needs, so nothing that could move the item forward is counted: an
  approval, a `defer` (which ends an item as decided wherever no policy applies, and which the
  policy engine has no rule for) or any other value. A rejection closes the item at the step it has
  reached and approves nothing, so it is recorded as usual: the vote joins the item's approvals and
  the item is `rejected`. A rejection does not read the flag, so an unreadable flag table never
  blocks one. Nothing waiting on the item proceeds while it is pending.
- **The caller gets the reason.** The response is `409` with the sequences of the steps that could
  not be evaluated:

  ```json
  {
    "detail": {
      "error": "approval_decision_refused",
      "reason_code": "approval_condition_unevaluable",
      "message": "A step of this item's approval policy has a condition that cannot be evaluated for it, so this decision is not counted and the item stays pending. It can still be rejected; to approve it, correct the step's condition, then decide again.",
      "unevaluable_steps": [2]
    }
  }
  ```

- **The refusal is recorded although the request fails.** It is committed before the `409` is
  returned: the item's `context.policy_state` gets `last_action: "refused"`,
  `last_reason: "approval_condition_unevaluable"` and `unevaluable_steps`, and the audit log gets a
  `hitl.decision_refused` event (outcome `denied`) with the policy id, the step sequences and the
  decision that was attempted. The server logs `hitl_decide_refused`. The next decision recorded on
  the item - a rejection, or an approval once its conditions evaluate or the flag is cleared -
  removes those three keys, so a decided item never reads as refused; the audit event stays.
- **A refused vote is not a vote.** It is not added to the item's approvals, the caller is not
  recorded as the decider, and an item that had not entered the policy stays unbound. Policies are
  corrected by deleting and recreating them, so such an item can be decided - by the same reviewer
  too - once the recreated policy's conditions evaluate for it, or once the flag is cleared. An item
  already part-way through the policy can still be rejected, or approved once the flag is cleared;
  recreating the policy strands it (see above).

The check runs when a decision is made, not when the item is raised. Agent runs, chat and workflows
raise approval items without consulting a policy; the policy is resolved and its conditions
evaluated only when someone decides, and a policy can be created or edited while an item waits. A
check at creation would miss both, and the decision is where an unknown condition would otherwise
route the item.

Tenant admins cannot set, change or delete the flag through `/api/v1/feature-flags`
(`403 flag_key_reserved`). Platform operators manage it:

```
python scripts/authority_flags.py set approvals.unevaluable_condition.deny --tenant <tenant id> --operator <name>
python scripts/authority_flags.py clear approvals.unevaluable_condition.deny --tenant <tenant id> --operator <name>
python scripts/authority_flags.py list --tenant <tenant id>
python scripts/authority_flags.py set approvals.unevaluable_condition.deny --global --operator <name>
```

The mode is `deny` when the global row or the tenant's row enables it, so a disabled tenant row does
not lift a global `deny`. `--global` changes need a privileged database role (superuser or
`BYPASSRLS`); see "Global rows need a privileged database role to write" in
`docs/operations/grant-enforcement.md`. The application reads the global row on a role subject to
row-level security from the next release (revision `v6z30_flag_global_read`); before it, set the
tenant row. Changes reach running processes within 30 seconds. If the flag table cannot be read the
mode is `deny`, and `approval_unevaluable_condition_mode_lookup_failed`
(`reason_code=flag_store_unreadable`) is logged;
only decisions other than a rejection, on items with a condition that cannot be evaluated, are
refused, so every other decision goes ahead.

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
