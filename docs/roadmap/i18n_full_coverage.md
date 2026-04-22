# Roadmap: Full In-App Hindi Coverage (TC_002)

**Status:** Planned — not shipped.
**Owner:** Frontend team.
**Inputs:** Aishwarya QA sheet 2026-04-21 + 2026-04-22 (TC_002 flagged as Reopen).
**Honest reclassification per `docs/bug_triage_skill.md` Rule 1 and `agenticorg-bug-fix-fail-closed` verdict matrix:** this is an Enhancement, not a bug fix.

## Why this lives in the roadmap, not a bug fix

TC_002 was repeatedly marked "Fixed" on short-scope interventions (locale key additions, `useTranslation` imports on a handful of pages, tripwire tests). Codex's 2026-04-22 release-signoff review called that out:

> The test that was added explicitly says it does NOT prove full translation coverage ... The dashboard itself documents that the rest of the page still falls back to English.

A tripwire enforces that new code uses `t()`. It does not wrap existing code. To honestly close TC_002 the product needs every user-visible string on every in-app page wrapped in `t()` against populated English + Hindi locale maps. That is a multi-week coordinated effort across ~40 in-app pages, not a bug fix.

## Scope of a real fix

### Pages to translate (enumerated)

- **Core navigation & layout**: `Layout.tsx`, `Header.tsx`, `Sidebar.tsx` — partially done.
- **Dashboards**: `Dashboard.tsx`, `CBODashboard.tsx`, `CEODashboard.tsx`, `CFODashboard.tsx`, `CHRODashboard.tsx`, `CMODashboard.tsx`, `COODashboard.tsx`, `CompanyDashboard.tsx`, `CostDashboard.tsx`, `PartnerDashboard.tsx` — only headers wrapped today.
- **Feature pages**: `Agents.tsx`, `AgentCreate.tsx`, `AgentDetail.tsx`, `Workflows.tsx`, `WorkflowCreate.tsx`, `WorkflowDetail.tsx`, `WorkflowRun.tsx`, `Connectors.tsx`, `ConnectorDetail.tsx`, `Approvals.tsx`, `PromptTemplates.tsx`, `ReportScheduler.tsx`, `Schemas.tsx`, `Settings.tsx`, `KnowledgeBase.tsx`, `ABMDashboard.tsx`, `Integrations.tsx`, `Audit.tsx`, `EnforceAudit.tsx`, `IndustryPacks.tsx`, `Observatory.tsx`, `Playground.tsx`, `Companies.tsx`, `CompanyOnboard.tsx`, `CompanyDetail.tsx`, `Billing.tsx`, `BillingCallback.tsx`.
- **Auth flows**: `Login.tsx`, `Signup.tsx`, `ForgotPassword.tsx`, `ResetPassword.tsx`.

### Out of scope (English-only by design)

- Public marketing pages (`Landing.tsx`, `/ads/*`, `/blog/*`, `/resources/*`) — public surface, SEO-focused.

### Definition of done

1. Every in-app page listed above imports `useTranslation` **and** wraps every visible string (headings, labels, buttons, placeholders, empty-state text, error messages).
2. `hi.json` has a populated key for every string in `en.json`. A lint/CI check fails if any key is missing.
3. Visual regression test per page comparing English vs Hindi render (snapshot or Playwright screenshot).
4. Manual QA sign-off by a Hindi-native reviewer on the top 10 most-visited in-app pages.
5. Removed the comment on `Dashboard.tsx:130` that documents English fallback — because the fallback won't exist.

### Tracking

Cut one PR per page with `[i18n-hi]` prefix. Each PR must include:

- `t()` wraps for every visible string.
- New `hi.json` keys + English pair.
- A Playwright snapshot confirming the Hindi render renders without missing-key fallbacks.

Expected cadence: 5 pages per week, ~6 weeks total.

### Interim honest UX

Until full coverage ships, the language-picker dropdown should surface:

> "Hindi coverage in progress — most in-app pages are still English. Public pages and marketing remain English-only."

Add this string in both `en.json` and `hi.json` so it's shown only when the user picks Hindi.

## Why TC_002 is not a bug

Per `docs/bug_triage_skill.md` Rule 1 verdict matrix, the verdicts are:

| Criterion | TC_002 |
|---|---|
| Fixed | No. Reproducing the tester's steps against the current product still shows a mixed-language UI. |
| Partially closed | Technically yes — locale keys added, Dashboard heading wrapped — but claiming that as "fixed" is the exact dishonesty Codex flagged. |
| Enhancement | **Yes.** The Expected Result ("entire application fully translated") describes functionality that does not exist yet and requires a multi-PR initiative. |

Until that initiative is delivered, TC_002 remains in the Enhancement tracker — not the bug sheet.

## Sign-off policy

Per `agenticorg-bug-fix-fail-closed` / `docs/bug_triage_skill.md`, a release sign-off cannot cite TC_002 as "Fixed". Honest options for the release narrative are:

- "Hindi translation: partial coverage — tracked as roadmap item at `docs/roadmap/i18n_full_coverage.md`."
- "Hindi translation: not in this release."

Both are acceptable. Claiming "Fixed" is not.
