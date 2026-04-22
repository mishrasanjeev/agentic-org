# Bug-Fixing Skill — Fail-Closed Triage + Honest Verdicts

**Status:** Mandatory for every bug fix, reopen analysis, and QA-driven PR.
**Owner:** CLAUDE.md references this file directly.
**Last updated:** 2026-04-22.

This file exists because reopens kept happening on the same bug IDs across three QA sweeps (Aishwarya 21-Apr, 22-Apr AM, 22-Apr PM). The diagnosis — documented in `~/.claude/projects/C--Users-mishr-agentic-org/memory/feedback_shallow_fix_autopsy.md` — was that I (the author) claimed "fixed" when the fix was hypothesis-grade. This skill codifies the process that must be followed so that "fixed" means the user will not see the bug again.

---

## Rule 1 — Fail-closed triage: every input is a valid bug until proven otherwise

When a bug/reopen lands, the default stance is **"this reproduces"**. The path to reclassifying it as "not a bug / duplicate / already fixed / enhancement" is:

1. Open the exact environment the tester used (URL, account, browser when specified).
2. Run the reproduction steps verbatim.
3. Observe the visible outcome.
4. Compare to the tester's "Actual Result" line.

If you can't complete step 1-3 (no local reproduction environment, no access to the test account), write that down — do **not** substitute a mental walk-through of the code path for a real reproduction. The code-path walk is a *hypothesis*, not a verdict.

Permitted verdicts and the evidence they require:

| Verdict | Minimum evidence |
|---|---|
| Fixed | Reproduced the bug → applied the fix → re-ran the reproduction → saw the expected result. Regression test added that replays the reproduction steps. |
| Partially closed | Reproduced → applied a fix that removes some observable symptoms → re-ran → confirmed what changed and what did not. Document the residual explicitly. |
| Already fixed (deploy lag) | Located the commit that fixes it → confirmed that commit is in `main` but has not yet reached the environment the tester is using. Include commit SHA + deploy ETA. |
| Not reproducible | Ran the exact steps in the exact environment → did not see the Actual Result described. Attach a short note (timestamp, session id, screenshot if UI). Do not mark "Not reproducible" from code-reading alone. |
| Enhancement | The Expected Result describes functionality that does not exist yet. State that out loud and open a tracked item. Do not silently close as "by design". |
| Duplicate | Link the other bug ID. The other bug must itself have a verdict under this matrix. |

**Forbidden:** closing a bug with "should be fixed" / "the code looks correct" / "probably a cache issue". If you can't get to one of the verdicts above, the bug stays open.

---

## Rule 2 — Grep the symptom, not the pointer

When the bug report or reviewer points at a specific line (`api/v1/chat.py:373`), that line is a hint, not the fix target. Before writing any code:

1. Grep the whole repo for the user-visible symptom — the number, the string, the constant.
2. Enumerate every location that could produce it.
3. If more than one candidate, fix all of them in the same change. If there's a reason to leave some alone, write that reason.

Example: "confidence stuck at 60%" means `grep -n "0\.6\|0.60\|60%"` on the whole file. If three locations match, three locations get fixed.

---

## Rule 3 — Sibling-path sweep is not optional

For every route, endpoint, UI page, or helper touched by a fix:

1. Open the sibling file(s) that implement the same pattern for adjacent resources.
2. Ask: does the bug class apply here too?
3. If yes, fix. If no, write one sentence on why not.

Example patterns:
- If you add admin-gate to `POST /X`, audit `POST /Y`, `PUT /X`, `DELETE /X`.
- If you add a domain-RBAC check on `GET /agents/{id}`, audit `PUT /agents/{id}`, `PATCH`, `DELETE`, and the same shape on `/prompt-templates/{id}`.
- If you wrap a route in a try/except to prevent 500 leaks, audit every sibling mutation in the same router.

---

## Rule 4 — Tests must replay the tester's steps, not your mental model

Source-inspection tests are useful guardrails but insufficient for QA-driven fixes. For every PR that claims "fixed" against a QA ticket, at least one test must:

1. Start from the same inputs the tester used (payload, form fields).
2. Exercise the same code path (route, handler, component).
3. Assert the same observable outcome the tester expected ("Expected Result").

If writing that test requires live infrastructure (DB, RAGFlow, a connector), state the limitation in the PR body and write the next-best-available test (integration with fixtures, component test with mocked API, etc.). Never claim "fixed" without some automated evidence.

---

## Rule 5 — Merged ≠ Deployed. Say so explicitly.

A merge to `main` is step 1 of a multi-step release. QA tests on a deployed environment (often `app.agenticorg.ai`). Between merge and deploy, reopens are legitimate.

Required wording in every PR description and bug-fix summary:

- "Fixed in commit X; deploy-production workflow status: Y (link)."
- If deploy workflow is still running: say "deploy in progress".
- If deploy failed: say "deploy blocked on Z; triaging" — do not write "fixed".
- If deploy succeeded: link the deploy job + mention the ETA at which QA should re-verify.

A bug is not closable until the tester can re-verify against a version of the product that contains the fix.

---

## Rule 6 — Production URL verification for reopens

When a bug is marked **Reopen**, the tester's reality is authoritative. Before doing anything else:

1. Open the tester's URL in a real browser (or local dev reproduction).
2. Run the exact steps.
3. If the bug reproduces against the deployed environment, apply this skill to find the root cause.
4. If the bug does NOT reproduce against the deployed environment, the verdict is "Not reproducible in current deploy — please re-verify" with timestamp + deploy SHA.

Never argue with a Reopen by saying "but my fix is in main". The tester already knows that.

---

## Rule 7 — Honest verdict in the summary artifact

Every QA bug-list engagement produces a summary (usually xlsx). The Verdict column must use the exact strings from the Rule 1 matrix. "Fixed" with no residual mention is a claim the user can measure against.

Common dishonest patterns to avoid:

- Writing "Fixed" when only a tripwire was added (use "Partially closed — tripwire added, surface expansion pending").
- Writing "Fixed" when the fix is in code but the environment hasn't deployed (use "Fixed in code, deploy pending").
- Writing "Not reproducible" when you didn't try to reproduce (use "Unverified — need reproduction steps").
- Collapsing three related symptoms into one "Fixed" when only one surface was addressed.

---

## Rule 8 — Alembic migrations must be safe on empty + production databases

Every schema change is a deploy gate. Production deploys run migrations in a pre-rollout job; a failing migration blocks the release.

Every migration must:

1. Guard table/column existence before `ALTER`/`CREATE INDEX` (use `DO $$ IF EXISTS $$` blocks).
2. Be idempotent — re-running produces the same state, does not error.
3. Be rollback-safe — `downgrade()` must undo `upgrade()` cleanly.
4. Tolerate empty tables (no rows to backfill) and populated tables (with invalid values that should be skipped, not raised on).

Reference: `migrations/versions/v4_8_8_report_schedule_company.py` — guarded against the case where the base table doesn't exist yet.

Before merging a PR with a new migration:

- `alembic heads` returns a single head.
- Apply the migration against an empty local DB (works or cleanly skips).
- Apply the migration against a production-shaped DB (one with the target table already).
- Both must complete without errors.

---

## Rule 9 — The one question to ask before writing code

**"If I was the tester, would the fix I'm about to write make my reproduction produce the Expected Result instead of the Actual Result?"**

If the answer is "probably", the fix is a hypothesis. Tighten it until the answer is "yes, and here's the test that proves it" — then write code.

---

## Minimum checklist per bug-fix PR

- [ ] Verdict matrix applied; each bug has a permitted verdict with required evidence.
- [ ] Symptom grepped across the whole repo.
- [ ] Sibling routes / pages audited.
- [ ] At least one test replays the tester's steps.
- [ ] PR body states "Fixed in commit X; deploy state: Y".
- [ ] Summary xlsx uses honest verdicts with explicit residuals.
- [ ] Migrations (if any) guard existence and are idempotent.
- [ ] Reopens are verified against the production URL — not "reads correctly in main".

---

## Why this file lives in the repo, not my memory

Memory files live under `~/.claude/` and don't ship with the product. This skill lives in `docs/` so:

- Reviewers can cite it in PR comments.
- Future collaborators (human or agent) who pick up the repo inherit the discipline.
- CLAUDE.md references it directly, making it mandatory for any author working under the project guide.

Sister memory files (author's private reference):
- `~/.claude/projects/.../memory/feedback_shallow_fix_autopsy.md` — the specific habits behind historical reopens.
- `~/.claude/projects/.../memory/feedback_rootcause_fix_discipline.md` — four failure modes Codex flagged.
- `~/.claude/projects/.../memory/feedback_enterprise_audit_discipline.md` — seven control-plane patterns.
- `~/.claude/projects/.../memory/feedback_bug_sweep_patterns.md` — twelve low-level bug classes.
