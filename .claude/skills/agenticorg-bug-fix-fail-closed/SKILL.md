---
name: agenticorg-bug-fix-fail-closed
description: Use this skill for every bug fix, reopen analysis, QA bug-sheet review, regression triage, and release sign-off in the AgenticOrg repository. It forces fail-closed verdicts, tester-step replay, sibling-path sweeps, production-vs-main honesty, and explicit residual-risk reporting. Use it whenever the work product says Fixed, Partially closed, Not reproducible, Already fixed, Enhancement, Duplicate, or Release sign-off.
---

# AgenticOrg Bug Fix Fail-Closed

This skill is mandatory for all bug work in this repo. It exists so that a
"fixed" verdict means the tester will stop seeing the bug, not that the code
looked plausible during review.

The canonical checklist lives in
[`docs/bug_triage_skill.md`](../../../docs/bug_triage_skill.md). If this skill
and that file ever disagree, the `docs/` file wins.

## Use This Skill When

- A user shares a bug list, QA sheet, reopen note, audit finding, or production
  regression.
- A PR claims to fix a bug or close a reopen.
- Someone asks for release sign-off on bug-related work.
- You are deciding whether a ticket is `Fixed`, `Partially closed`,
  `Already fixed`, `Not reproducible`, `Enhancement`, or `Duplicate`.

## Required Workflow

1. Reproduce the tester's steps exactly, or explicitly state why reproduction
   was impossible in the available environment.
2. Grep the symptom across the repo; do not trust the first file mentioned in
   the ticket.
3. Audit sibling routes, sibling components, and adjacent mutations for the
   same bug class.
4. Fix every confirmed occurrence in the same change, or document the reason a
   sibling path is not affected.
5. Add or run tests that replay the tester's actual inputs and expected
   outcome.
6. State whether the evidence is code-only, local runtime, or deployed
   environment evidence.
7. Report residual gaps plainly. If any residual changes user-visible behavior,
   the verdict is not `Fixed`.

## Non-Negotiable Verdict Rules

- `Fixed` requires reproduced bug -> fix -> replay -> expected result observed.
- `Fixed in code, deploy pending` is not release sign-off.
- `Not reproducible` is invalid without rerunning the tester's steps.
- `Partially closed` must name the residual symptom in one sentence.
- `Enhancement` is allowed only when the expected result is genuinely outside
  current product scope.
- `Duplicate` must link to the canonical ticket that already has an honest
  verdict.

## Release Sign-Off Gate

Do not issue release sign-off when any of the following are true:

- Any bug in the requested sheet is `Not fixed`, `Partially fixed`, or
  `Unverified`.
- A related bug in the same control surface remains open.
- The evidence is only source inspection for a runtime-sensitive flow such as
  auth, billing, connector I/O, indexing, migrations, or deployment.
- A payment, connector, or third-party integration path lacks end-to-end proof.
- The tests do not replay the user-visible bug, or the relevant test/build
  suite is red.

## Output Contract

For every bug reviewed, report:

- Bug ID / title
- Exact verdict from the approved matrix
- Evidence used
- File references for the fix or the gap
- Related sibling-path findings
- Residual release risk

Short summaries that skip the verdict matrix are not acceptable outputs for
this repository.
