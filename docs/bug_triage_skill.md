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
- [ ] Derived contracts fixed at the producer, not patched at the consumer (Rule 12).
- [ ] Any list fed to a fail-closed gate has an explicit required/optional split defaulting to all-required; narrowing a gate names the second control that still enforces what was removed (Rule 12).
- [ ] Coupled client error-path swept — a structured error body does not crash the UI (Rule 12).
- [ ] Baseline / route-inventory re-keys proven debt-neutral (counts unchanged, only line churn); new broad excepts annotated, not rebaselined (Rule 12).
- [ ] Connector discovery-to-action context is preserved: IDs returned by discovery tools are accepted by downstream tools, normalized without lossy type coercion, sent in the provider-required location, and kept out of request bodies when they are query/path context (Rule 13).
- [ ] Connector catalog/tool-list claims are backed by executable native connector methods, endpoint-path tests, and a UI/Playwright check when the claim is operator-visible (Rule 13).

---

## Rule 10 — OAuth surfaces must use canonical redirect URIs and provider-aware regions

Added 2026-05-14 after the Zoho OAuth reopen on Uday's CA-Firms sweep.
The same root cause reopened four times (May-04, May-11, two on May-14)
because every fix patched a per-request URL derivation instead of the
underlying invariants:

1. **Redirect URIs are never computed from request data in production.**
   Cloud Run terminates TLS at the edge — the in-container request is
   `http://` while the registered provider URL is `https://`. The
   resulting redirect URI mismatches and the provider rejects every
   authorize attempt. The canonical fix is a single
   `settings.public_api_base_url` (env: `AGENTICORG_PUBLIC_API_BASE_URL`)
   pinned to the https host the operator registered with the provider.
   Strict envs validate https-only.
2. **Authorize/token URLs are region-aware, picked from a registry.** A
   hardcoded `accounts.zoho.com` works for US tenants but bounces .in
   accounts through a redirect that drops the carry-over (and may not
   mint a refresh_token on re-consent). Every multi-region provider
   declares its DC variants in
   `core/connectors/provider_registry.py:ZOHO_REGIONS` (or equivalent).
   Adding a region is a registry entry, not a code refactor.
3. **Missing-refresh-token must surface a structured error, not prose.**
   When a provider returns `code` without a `refresh_token` because the
   app was already consented, the user has no way to recover from a
   400 free-text instruction. The handler raises
   `OAuthNeedsReconsent` (HTTP 409,
   `code=oauth_refresh_token_missing`) and the UI matches on that code
   to render a Reconnect button that POSTs
   `/connectors/oauth/revoke-and-retry`.
4. **Every OAuth PR adds a Playwright spec that exercises the live
   `/connectors/oauth/initiate` endpoint and asserts the produced
   authorize URL is https + correct region + `access_type=offline` +
   `prompt=consent`.** Source-grep tests are not sufficient — they
   would have caught none of the May-04..May-14 reopens.

Reference: `tests/regression/test_bugs_uday_14may2026.py`,
`api/v1/oauth_connector.py`, `core/connectors/provider_registry.py`,
`ui/e2e/qa-uday-14may2026.spec.ts`.

---

## Rule 11 - Connector registration must not produce false healthy state

Added 2026-05-15 after the Zoho Books registration reopen on Uday's
CA-Firms sweep. The failure pattern was not just a button label. It was
contract drift between the UI, provider registry, encrypted credential
store, connector health checks, and agent activation.

1. **Do not mark a connector healthy from static fields alone.** Client ID,
   client secret, organization_id, or base URL formatting are not evidence
   that runtime tool execution can call the provider. A connector is healthy
   only after the same encrypted credential path used by runtime execution
   passes the connector's health check.
2. **No-redirect registration must require refreshable token material.**
   If the UI is not opening a provider consent screen, the registration
   payload must include refresh_token or an exchangeable one-time code.
   Otherwise the correct verdict is "not ready", not "registered".
3. **Agent activation is downstream of connector health.** Creating,
   resuming, or promoting an active agent must fail closed when any linked
   connector has missing encrypted credentials, non-configured status,
   unhealthy/unknown health, or a missing refresh_token for refresh-token
   providers such as Zoho Books.
4. **When changing connector onboarding, update old tests that assert the
   previous UI contract.** A stale Playwright test looking for an old
   "Authorize Connector" button is a regression source, not harmless
   historical coverage.

Reference: `tests/regression/test_uday_15may_connector_registration.py`,
`ui/e2e/qa-uday-15may2026.spec.ts`, `api/v1/connectors.py`,
`api/v1/agents.py`, `core/tasks/token_refresh.py`.

---

## Rule 12 — Derived requirements must separate "required" from "optional", and a fail-closed gate is only as correct as the set it is fed

Added 2026-05-17 after the CA-Firms promotion reopen on Uday's sweep
(bug 1). A CA pack agent could never be promoted on a Zoho-Books-only
tenant — the exact configuration the pack's own module header documents
as supported — because activation reported `income_tax_india` and
`tally` as `missing_connector_config`.

This was **not** a fail-closed gate misbehaving. The gate
(`_assert_connectors_ready_for_activation`) was correct; it was **fed an
over-broad input**. `installer._connector_ids_for_tools` derived
`agent.connector_ids` from *every* connector prefix in the tool manifest
and the activation path treated all of them as hard requirements. There
was no "required vs optional" distinction even though the pack
explicitly declared optional connectors in prose.

The shallow fixes that would have reopened a fifth time:

- Hardcode-skip `income_tax_india` / `tally` in the gate (breaks tenants
  that *do* configure them and want fail-closed).
- Downgrade `missing_connector_config` to a warning (re-opens the
  Rule 11.3 fail-closed hole wholesale).
- "Only check the first connector" (arbitrary, silent).

The discipline this rule codifies:

1. **When a contract is *derived* (connector_ids from tools, scopes from
   tools, perms from roles), trace it to the producer and fix it there.**
   The bug is in the derivation, not the consumer that trips over it.
2. **Any list fed to a fail-closed gate needs an explicit
   required-vs-optional split.** Default the split to *all-required*
   (fail closed) so every undeclared/hand-built case keeps the strict
   behaviour; narrow only where the product owner explicitly declares
   optionality (here: `required_connectors` in the pack agent spec).
3. **Before narrowing an activation/authorization gate, prove the
   capability you removed from it is still enforced somewhere else.**
   Here: runtime tool dispatch already fails closed on unconfigured
   connectors (Rule "BUG-08", `tests/regression/test_bug_08_tool_gateway_
   fail_closed.py`), so narrowing *activation* did not widen the runtime
   trust boundary. If you cannot point to that second enforcement, you
   are not narrowing — you are removing a control.
4. **Self-heal already-provisioned rows at read time, not via a one-off
   backfill.** The tester's agents already existed with the bad derived
   value; the gate re-derives the correct subset live from the static
   pack so no migration/backfill is needed and no row is left stale.
5. **A structured error body is a UI contract.** The same flow returned
   `detail` as an object `{error,message,connectors:[...]}`; the UI
   stored it into string state and rendered an object as a React child,
   blanking the page so the tester saw *no* recoverable message. A
   backend bug fix is incomplete if the coupled client crashes on the
   error path. Sweep every sink that consumes that body (Rule 3).
6. **Editing a file with line-keyed baselines (enterprise stability
   baseline, route inventory) forces a mechanical re-key. Prove it is
   debt-neutral — never blind-accept `--update-baseline`.** Required
   proof: identical category counts before/after, per-file counts
   unchanged, and the diff is only line/digest churn for the file you
   edited (no route added/removed, no new unannotated handler). A new
   broad `except` must carry `# enterprise-gate: broad-except-ok
   reason=<why fail-closed-on-any-error is correct here>`, not ride in
   on a rebaseline.

Reference: `tests/regression/test_uday_17may_promotion_connector_gate.py`,
`ui/src/__tests__/AgentDetail.errorDetail.uday17may.test.ts`,
`ui/e2e/qa-uday-17may2026.spec.ts`, `core/agents/packs/ca/__init__.py`,
`core/agents/packs/installer.py`, `api/v1/agents.py`,
`ui/src/pages/AgentDetail.tsx`.

---

## Rule 13 - Connector context must survive discovery-to-action hops

Added 2026-05-25 after Uday's CA-Firms Zoho Books reopen and the
CRM-TOOLS-002 connector-surface review. The shallow failure pattern was
again a "green" local path that did not replay the tester's actual
sequence:

- `get_organization` returned real Zoho Books `organization_id` values.
- The next tools (`list_vendors`, `list_vendor_bills`, etc.) accepted an
  `organization_id` argument in the prompt, but the connector silently
  ignored it and reused the stale configured/default org.
- Mutating tools risked putting connector context fields into JSON
  bodies instead of query/path context.
- CRM catalog claims mentioned production CRM operations that did not
  all exist as native HubSpot/Salesforce connector methods.

The discipline this rule adds:

1. **Discovery output is a contract for the next tool call.** If a tool
   returns `organization_id`, `realm_id`, `account_id`, `property_id`,
   or similar provider context, every downstream tool that needs that
   context must accept it explicitly, normalize it as a string without
   rejecting numeric-looking values, and let explicit call params win
   over connector config.
2. **Provider context belongs in the provider-required transport
   location.** For Zoho Books, `organization_id` is query context even
   for POST/PUT. It must not be copied into mutation JSON bodies.
3. **Discovery endpoints must not be polluted by stale scoped context.**
   A list/discovery call such as `GET /organizations` should not inject
   an old configured org unless the caller explicitly supplies one.
4. **Connector health is not action proof.** A health check that lists
   organizations only proves auth; it does not prove downstream tools
   will use the returned org correctly. Regression tests must replay the
   discovery-to-action sequence.
5. **Operator-visible tool claims require executable tools.** If the UI
   or product copy claims CRM operations such as company/deal/contact
   CRUD, associations, tasks, notes, or validation, native connector
   registries must expose real provider-backed methods and endpoint-path
   tests. Otherwise classify the item honestly as an enhancement, not a
   fixed bug.
6. **No raw credential or tenant identifiers in logs/summaries.** Secrets
   are never printed; provider account/context identifiers should be
   redacted in connector logs unless they are already safe public
   metadata required for operator output.

Reference: `tests/regression/test_ca_firms_uday25_zoho_crm_tools.py`,
`ui/e2e/qa-uday-25may2026.spec.ts`,
`connectors/finance/zoho_books.py`,
`connectors/marketing/hubspot.py`, and
`connectors/marketing/salesforce.py`.

---

## Rule 14 - Fix protocol drift and output-envelope leaks at the contract boundary, not one caller

Added 2026-06-09 after the CA/Marketing reopen sweep. Three different
symptoms had the same shallow-fix pattern: patching the visible page or the
named file while leaving the shared contract wrong.

1. **Provider connector auth must pin the current provider contract.** If a
   connector auth bug cites a live provider flow, assert the endpoint, query
   string, credential header names, response token field, and downstream auth
   header in tests. For Adaequare GSTN this means
   `/authenticate?grant_type=token`, `gspappid`/`gspappsecret`,
   `access_token`, and `Authorization: Bearer ...` on normal and DSC-signed
   calls. Do not keep a legacy flow alive just because an older test mocked it.
2. **Final agent output must be sanitized at every render boundary.** Backend
   chat formatting, explicit agent-run result cards, sales-agent result cards,
   and playground traces must all use the same extraction rules: unwrap
   `raw_output`, `answer`, `response`, `message`, `text`, `content`, and
   `result`; parse JSON/Python-repr envelopes; suppress `status`, `signature`,
   `extras`, `metadata`, tool outputs, trace IDs, and secrets. A
   `JSON.stringify(output)` fallback is a bug unless the surface is explicitly
   an operator debug export.
3. **Dropdown additions belong in shared option contracts.** Adding a value
   only to the page named in the ticket creates edit/create drift. Add it to
   the shared constants, verify the API schema accepts it, and cover the
   visible dropdown with Playwright.

Reference: `tests/regression/test_uday_09jun2026_ca_marketing_bugs.py`,
`tests/integration/test_gstn_sandbox.py`, `ui/src/lib/agent-output.ts`,
`ui/src/__tests__/agent-output.test.ts`, `ui/e2e/qa-uday-09jun2026.spec.ts`,
`connectors/finance/gstn.py`, `api/v1/chat.py`,
`ui/src/pages/ConnectorCreate.tsx`, `ui/src/pages/AgentDetail.tsx`,
`ui/src/pages/SalesPipeline.tsx`.

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
