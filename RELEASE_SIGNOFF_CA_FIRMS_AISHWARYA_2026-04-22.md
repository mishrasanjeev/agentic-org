# Release Sign-Off Review — CA Firms + Aishwarya Sheets

Date: 2026-04-22

Inputs reviewed:

- `C:\Users\mishr\Downloads\CA_FIRMS_Bugs_Enhancements_Ramesh_Uday22Apr2026.md`
- `C:\Users\mishr\Downloads\AgenticOrg_Aishwayra22April2026.xlsx`

## Verdict

**NO RELEASE SIGN-OFF.**

Claude did **not** fix these sheets "perfectly well". Several exact ticket
symptoms are fixed, but multiple tickets are only partially closed, one is
still clearly open, and a few related bug classes in the same product surfaces
remain unresolved. For an enterprise release bar, that is a ship blocker.

## Permanent Repo Controls Added

Because the current review found over-optimistic bug closure behavior, I added
repo-owned enforcement:

- Mandatory bug-fix skill:
  `.claude/skills/agenticorg-bug-fix-fail-closed/SKILL.md`
- Mandatory project policy wiring:
  `CLAUDE.md`
- Canonical checklist remains:
  `docs/bug_triage_skill.md`

This now forces fail-closed bug triage, honest verdicts, residual-risk
reporting, and a stricter release sign-off gate for all future bug work and
reopen analysis.

## Findings First

1. **Hindi localization is still not fully fixed.**
   The test that was added explicitly says it does **not** prove full
   translation coverage: `ui/src/__tests__/i18n_coverage_tripwire.test.ts:4-11`.
   The dashboard itself documents that the rest of the page still falls back to
   English: `ui/src/pages/Dashboard.tsx:130-137`. This is not a "fixed"
   language ticket.

2. **NL Query is improved, but not release-grade proven.**
   The dishonest 60% clamp and fake fallback answer were fixed in
   `api/v1/chat.py:332-425`, and chat history isolation was fixed in
   `ui/src/components/ChatPanel.tsx:72-85`. That is real progress. But the
   system still relies on heuristic routing and only produces grounded answers
   when the selected agent and connector state are actually configured. There
   is no end-to-end proof here that the business queries in QA now route
   correctly across real tenant data.

3. **Knowledge Base chunk counts and search are only partially fixed.**
   "Total Chunks = 0" was softened with fallback counting in
   `api/v1/knowledge.py:901-915`, but that fallback is not the real index size.
   Search now tries semantic, keyword, then filename fallback, but the last
   path is literally filename-only in `api/v1/knowledge.py:805-835`. That is
   better than empty UI, but not the same thing as reliable document retrieval.

4. **Billing error handling improved, but working checkout is still unproven.**
   Stripe and India checkout routes now fail honestly when unconfigured in
   `api/v1/billing.py:196-205` and `api/v1/billing.py:236-245`, and the UI
   redirects to `challenge_url || checkout_url` in `ui/src/pages/Billing.tsx:96-103`.
   What I do **not** see is proof of a working end-to-end payment flow in the
   current environment. That is not sign-off quality for a billing bug.

5. **Settings and Schema Registry still contain "surface exists" fixes that do
   not fully close the feature class.**
   User Management and Webhook Configuration now exist on the page, but they are
   informational/static sections, not full management workflows:
   `ui/src/pages/Settings.tsx:487-569`. The Schema page no longer hangs because
   Monaco falls back to a textarea in `ui/src/components/SchemaEditor.tsx:4-13`,
   but the editor still reads only `SCHEMA_DEFINITIONS[selectedSchema]` in
   `ui/src/pages/Schemas.tsx:312` instead of the backend-provided `json_schema`
   returned by `api/v1/schemas.py:24`.

6. **Related multi-company isolation gaps still exist in executive KPI flows.**
   The company switcher is still `localStorage + reload` in
   `ui/src/components/CompanySwitcher.tsx:14-56`. CFO/CMO dashboards now pass
   `company_id` (`ui/src/pages/CFODashboard.tsx:72-78`,
   `ui/src/pages/CMODashboard.tsx:83-89`), but several backend KPI helpers still
   ignore company scoping and query tenant-wide rows in `api/v1/kpis.py:201-315`
   and only echo the selected company back in the response shape via
   `api/v1/kpis.py:365-376`. That is the same class of "UI looks scoped, backend
   is not truly scoped" bug that caused previous reopens.

## Sheet 1 — CA_FIRMS_Bugs_Enhancements_Ramesh_Uday22Apr2026.md

This file is mixed content. The top section is a real bug list. The second
"connector missing requirements report" section is mostly a product-gap /
requirements inventory, not a clean reopen sheet. Claude should not have
flattened those into one blanket "all fixed" claim.

### Ticket-by-ticket verdicts

| Item | Verdict | Evidence | Brutal residual |
|---|---|---|---|
| BUG-006 Onboarding missing `CA/Accounting` industry | Fixed | `ui/src/pages/CompanyOnboard.tsx:72-75` | Exact option now exists. |
| BUG-005 Auto-Detect Company Info failed after successful Test Connection | Fixed | UI sends `bridge_url` / `bridge_id` in `ui/src/pages/CompanyOnboard.tsx:139-156`; backend accepts aliases in `api/v1/companies.py:2050-2062` and returns friendlier failure text in `api/v1/companies.py:2246-2247` | Exact raw-parse symptom is fixed. |
| BUG-003 Company detail missing Tally Connection section | Fixed | `ui/src/pages/CompanyDetail.tsx:338-341`, `ui/src/pages/CompanyDetail.tsx:1411-1521` | Exact panel is there. |
| BUG-002 Company detail missing Delete/Archive action | Fixed | `ui/src/pages/CompanyDetail.tsx:1379-1399` | Exact archive action now exists. |
| Generate bridge credentials returned `E1001 INTERNAL_ERROR` | Fixed for exact symptom | `api/v1/companies.py:2283-2376`, `ui/src/pages/CompanyDetail.tsx:562-583` | Route now validates and surfaces structured failures. I did not prove live bridge minting against production infra. |
| Connectors page had no delete functionality | Fixed | `ui/src/pages/Connectors.tsx:129-147`, `api/v1/connectors.py:413-443` | Exact UI/API gap is closed. |
| Generate Test Sample / shadow accuracy stuck around 40% | Partially fixed | UI fills the shortfall in `ui/src/pages/AgentDetail.tsx:1134-1163`; backend ignores low-signal samples in `api/v1/agents.py:1696-1728` | The numeric "stuck at 40%" symptom is addressed. I still do not have end-to-end proof that real CA pack agents now achieve promotion-grade shadow quality in deployed environments. |

### "CA Firm Connector Missing Requirements Report" classification

These were **not** all valid "open bugs" on current code. Several already
exist:

- Tally health check exists: `connectors/finance/tally.py:227-272`
- GSTN manual upload exists: `api/v1/companies.py:1235-1283`
- GSTN credential vault exists: `api/v1/companies.py:1452-1690`
- GSTN sandbox connector exists: `connectors/finance/gstn_sandbox.py:1`
- Zoho Books connector exists: `connectors/finance/zoho_books.py:1`

What is still **not** proven enough for release:

- Real end-to-end filing with live external credentials and DSC-backed flows
- Real bridge installer / package distribution / field deployment workflow
- Remote bridge ops lifecycle beyond the API and UI surfaces

So the honest answer is: **some of that report was already implemented, and
some of it is still unproven product work. It was never valid to mark that
whole section "perfectly fixed".**

## Sheet 2 — AgenticOrg_Aishwayra22April2026.xlsx

### Ticket-by-ticket verdicts

| Ticket | Verdict | Evidence | Brutal residual |
|---|---|---|---|
| TC_001 Report Scheduler HTTP 500 on invalid email | Fixed | UI validation `ui/src/pages/ReportScheduler.tsx:160-161`; API validation `api/v1/report_schedules.py:146-148` | Exact validation bug is fixed. |
| TC_002 Partial English to Hindi conversion | **Not fixed** | Test explicitly says it is not proving full translation `ui/src/__tests__/i18n_coverage_tripwire.test.ts:4-11`; page still falls back to English `ui/src/pages/Dashboard.tsx:130-137` | This ticket should not be closed. |
| TC_003 NL Query incorrect routing / generic answers / 60% confidence | Partially fixed | Confidence clamp + fake-answer path fixed in `api/v1/chat.py:332-425`; history isolation fixed in `ui/src/components/ChatPanel.tsx:72-85` | Better honesty, not full routing proof. |
| TC_004 KB Total Chunks shows 0 | Partially fixed | Fallback chunk counting in `api/v1/knowledge.py:901-915` | Fallback is still an estimate path, not guaranteed real index state. |
| TC_005 KB Search returns no results because indexing missing | Partially fixed | Search fallbacks in `api/v1/knowledge.py:780-838` | Last-resort fallback is filename-only, not real content retrieval. |
| TC_006 Duplicate document upload shows error but still adds doc | Partially fixed | UI now uses Replace / Keep both / Cancel `ui/src/pages/KnowledgeBase.tsx:72-77`, `ui/src/pages/KnowledgeBase.tsx:217-234`; tests cover it in `ui/src/__tests__/KnowledgeBase.test.tsx:157-262` | Normal path is fixed, but backend dedup remains fail-open if lookup fails: `api/v1/knowledge.py:494-503`. |
| TC_007 ABM tier mismatch docs vs CSV validation | Fixed | `api/v1/abm.py:374-397` | Exact validation mismatch is closed. |
| TC_008 ABM Last Activity empty after CSV upload | Fixed | `api/v1/abm.py:231-252` | Exact symptom is closed. |
| TC_009 Billing upgrade payment gateway error | Partially fixed | Honest 503s for missing config in `api/v1/billing.py:196-205`, `api/v1/billing.py:236-245` | This is better error handling, not proof checkout works. |
| TC_010 Settings missing User Management and Webhook Configuration | Partially fixed | Sections added in `ui/src/pages/Settings.tsx:487-569` | These are explanatory surfaces, not full management features. |
| TC_011 Settings Max Agents Per Domain input missing | Fixed for exact ticket | Input now exists in `ui/src/pages/Settings.tsx:234-240` | Adjacent contract drift remains: UI uses `max_replicas_per_type` (`ui/src/pages/Settings.tsx:10`, `:28`, `:229-230`) while backend schema exposes `max_replicas_global_ceiling` and a `comms` domain default in `core/schemas/api.py:249-260`. |
| TC_012 Create Schema infinite loading | Fixed | Monaco textarea fallback in `ui/src/components/SchemaEditor.tsx:4-13`, `:41-63` | Exact loading hang is addressed. |
| TC_013 Existing schema editor stuck on Loading | Partially fixed | Same Monaco fallback removes the spinner trap | Editor still does not load backend `json_schema`; page only reads `SCHEMA_DEFINITIONS[selectedSchema]` in `ui/src/pages/Schemas.tsx:312`. |
| TC_014 Generate Test Sample stuck at 40% | Partially fixed | `ui/src/pages/AgentDetail.tsx:1134-1163`, `api/v1/agents.py:1696-1728` | Exact numeric plateau is addressed, but promotion-grade behavior is still unproven end-to-end. |

## Related Bugs In The Same Product Surfaces

These were not the named tickets, but they are close enough to the same bug
classes that I would still block release:

1. **Executive KPI multi-company scoping is still weak.**
   The frontend now threads `company_id`, but backend KPI helper queries remain
   tenant-wide in `api/v1/kpis.py:201-315`.

2. **Settings API/UI contract drift remains.**
   UI defaults omit the `comms` domain and use `max_replicas_per_type`, while
   the backend schema expects `max_replicas_global_ceiling` and includes
   `comms`: `ui/src/pages/Settings.tsx:24-29`,
   `core/schemas/api.py:249-260`.

3. **Schema Registry is still not a real editor for persisted custom schemas.**
   The backend returns `json_schema` (`api/v1/schemas.py:24`), but the page
   still edits only hardcoded local definitions: `ui/src/pages/Schemas.tsx:312`.

4. **Knowledge Base duplicate prevention still fails open on lookup failure.**
   `api/v1/knowledge.py:494-503`

5. **Billing still lacks current-environment proof of successful live checkout.**
   The code now fails honestly when unconfigured, but that is not operational
   evidence.

## Verification Run

Focused verification run on the current repo state:

- Backend targeted regressions:
  `uv run pytest tests/unit/test_bug_sweep_22apr_pm.py tests/unit/test_companies_tally_detect_alias.py tests/unit/test_tally_connector_health_and_errors.py tests/unit/test_abm_dashboard_filters_and_seed.py tests/unit/test_abm_intent_seed_preserved.py tests/unit/test_knowledge_replace_vs_duplicate.py tests/unit/test_knowledge_normalize_status.py -q`
  Result: **48 passed**
- Frontend targeted regressions:
  `cd ui; npm run test -- src/__tests__/KnowledgeBase.test.tsx src/__tests__/ReportScheduler.test.tsx src/__tests__/i18n_coverage_tripwire.test.ts`
  Result: **42 passed**
- Frontend build:
  `cd ui; npm run build`
  Result: **passed**

Those runs confirm some exact fixes. They do **not** erase the residual gaps
above, because several of the remaining blockers are product-behavior and
scope-completeness issues, not syntax or build-break issues.

## Sign-Off Decision

**Release sign-off refused.**

Minimum blockers that must be closed before I can honestly sign off:

1. TC_002 must be actually fixed or reclassified honestly as a planned
   partial-translation initiative rather than marked "fixed".
2. KB search / chunk-count behavior must be validated as real retrieval, not
   fallback-only cosmetics.
3. Billing must be proven with an end-to-end working checkout path in the
   target environment.
4. Schema Registry must load and edit persisted schemas, not only local
   hardcoded definitions.
5. KPI multi-company scoping must be enforced server-side, not just threaded
   through the UI.

Until those are closed, this is not an enterprise-grade "no known bug" release.
