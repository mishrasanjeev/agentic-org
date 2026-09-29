# End-User Documentation Validation - 2026-09-29

## Scope And Status

Local validation started on main `5099d43e`, then rebased onto `3e513d7f`.
The final complete Docker gate validated implementation commit `dd8d4e65`.
CI, merge and production rollout evidence are attached to the release PR after
completion; local results alone do not establish production deployment.

The manual contains 29 source Markdown guides, approximately 14,400 words and
five BFSI playbooks: business onboarding/KYB, reconciliation, customer service,
insurance and merchant services. It includes first-agent exercises, synthetic
procedure/data examples, prerequisites, visual step flows, troubleshooting,
source references and adoption checks.

The public reader supports full-text browser search, grouped navigation,
table-of-contents anchors, mobile navigation, copy-link, print and source links.
The landing page and authenticated sidebar link to the manual. Complete article
text is available without JavaScript. Sitemap, structured metadata and LLM
discovery files include the guides. Markdown is the authored source; generation
and regression tests prevent a stale reader copy.

Local Docker preview: `http://127.0.0.1:4193/docs`.
Container: `agenticorg-user-docs-20260929`.

At this initial validation checkpoint, no production deployment, DNS update,
HTTPS host binding, cloud resource change or production database change had
been performed. Local validation is not evidence of production deployment.

## Validation Results

| Check | Result |
| --- | --- |
| UI unit/component suite | 52 files, 341 tests passed |
| Documentation and SEO generator suite | 24 tests, including packaging and cross-platform generation regressions |
| Playwright documentation suite | 14 tests passed against the rebuilt Cloud Run image with production analytics configuration, including the final corrections |
| TypeScript and production UI build | Passed |
| SEO output verification | Passed for 109 route descriptors and 98 canonical sitemap URLs |
| ESLint | No errors; 23 existing warnings in unrelated application pages |
| npm dependency audit | Zero reported vulnerabilities |
| Docker nginx configuration | Passed |
| Tracked diff whitespace check | Passed |
| New-file ASCII and scoped credential-literal/bidi scans | Passed |
| Complete Docker `make check` | Passed |
| Complete Docker preflight | 10,344 passed, 16 skipped, five expected failures; 78.96% coverage and all gates passed |
| Docker `make test` unit gate | 7,931 passed, seven skipped; 62.81% coverage cleared the 55% floor |
| Docker `make test` integration/regression gate | 2,645 passed, 13 skipped, five expected failures against isolated Postgres/Redis; combined coverage 83% |

Playwright loaded every guide and checked HTTP status, title/canonical/TechArticle
metadata, rendered images, page overflow and browser exceptions. It also covered
search and empty states, article navigation, menu/anchors, printable content,
no-JavaScript reading, invalid-guide HTTP 404, the documentation-host root
redirect, landing-page links and accessibility. Compact/tablet checks exercised
320px, 768px and 1100px widths in addition to the 390px/1440px projects.

Automated Axe checks reported no violations on the selected overview, OCR/table
and BFSI reader views. Desktop/mobile screenshots were manually inspected for
text clipping, diagram layout and overlapping content. This is not a claim of
universal accessibility certification.

Commands, from `ui` unless noted:

```powershell
npm test -- --run
npm run test:seo
npm run lint
npm run typecheck
npm run seo:sync
npm run build
npm audit --audit-level=low
npx playwright test --config=playwright.docs.config.ts
docker exec agenticorg-user-docs-20260929 nginx -t
# From the repository root:
git diff --check
```

Browser artifacts remain under `ui/test-results/docs/` and
`ui/playwright-report/docs/`; these generated artifacts are not committed.

## Corrections And Boundaries

Documentation was checked against current application routes and repository
sources. It distinguishes implemented UI from configuration-only or gated paths,
global/company scope, extraction from indexing/grounding, and human decisions
from model recommendations. Commerce keeps AgenticOrg runtime, Grantex authority,
merchant systems of record and provider-owned payment execution separate.

The first metadata browser assertion incorrectly treated JSON-LD script content
as visible text. It was corrected to parse the actual script JSON and verify the
TechArticle URL for each guide; the entire suite then passed. Responsive diagram
layout and muted text contrast were also corrected before final verification.

The full Python preflight reproduced three release-contract failures: the new
documentation overview lacked `primaryQuestion`, the reader duplicated ownership
markup instead of using `ProductOwnership`, and the security-alert regression
still asserted Undici 7.29.0 after the patched 7.29.1 update. The overview now
includes the answer-oriented metadata, the reader uses the shared ownership
component (with a component assertion), and the version assertion matches the
reviewed lockfile. Existing whole-registry and whole-public-footer tests cover
sibling surfaces; no backend runtime or security assertion was removed.

The local-only Host-header alias assertion is a separate browser test. It skips
explicitly on hosted targets until registrar/HTTPS setup is complete, while
no-JavaScript reading and unknown-guide HTTP 404 remain tested in production.

PR CodeQL review found that the Markdown safety test's tag assertion matched
lowercase only. The assertion now checks HTML tags and unsafe URL schemes
case-insensitively, with lowercase, uppercase and mixed-case input probes. All
24 documentation/SEO generator tests passed again. This is a test-only change;
the renderer's existing `html: false` boundary and published assets are unchanged.

The first remote CI pass also rejected eight guide sentences in generated
`llms-full.txt` under the existing public-claims policy. These included ambiguous
availability wording, a hypothetical benefit and operational instructions that
looked like outcome assertions. The authored sentences were clarified without
removing their safety guidance, then reader data, LLM assets and CSP hashes were
regenerated. The complete public-claims scanner passed locally, and three new
regressions cover the published manual and preflight ordering. The preflight now
runs the same public-claims command as CI after the UI build; no claim exception,
unearned evidence record or weakened scanner was added.

Remote Local Stack CI exposed an existing OIDC stub test race: the response body
is sent before the server thread writes its audit line, so immediate captured-log
inspection could see no entry. The test now waits for the original logger to
finish the relevant request using a bounded event. Its OAuth error and
secret-redaction assertions remain intact; development stub and authentication
runtime code are unchanged.

`markdown-it` is pinned to the reviewed patched version 14.3.2. The existing
`undici` override was advanced one patch from 7.29.0 to 7.29.1; the final local
npm audit reports zero vulnerabilities.

Production packaging review found that the previous UI image layout omitted
authored manual sources. Both UI Dockerfiles now preserve the source layout,
include the public manual and its reviewed references, and copy only built
assets to the final nginx image. Generation now fails if the manifest is
missing rather than silently producing an empty reader. `.dockerignore` keeps
historical/internal reports and scratch artifacts excluded while allowing the
specific public build inputs. Tests cover both Dockerfile paths and the
missing-manifest failure.
Windows/Linux line endings are normalized before rendering and indexing; a
regression verifies identical generated output. The Cloud Run image build
generated all 29 guides and passed nginx configuration validation.

The locally installed frontend skill commands and deterministic design detector
were unavailable. The upstream audit, critique and polish checklists were
reviewed directly. A single-context manual review covered task hierarchy,
keyboard navigation, empty/search/missing-guide states, content accuracy,
responsive layout, contrast, screenshot framing and visual consistency. No
dual-agent review, detector overlay or unavailable skill command is claimed.

The reader uses existing product colors/icons, an unframed article layout and
task-focused navigation. Five BFSI playbooks distinguish recommendations from
institution-owned decisions. Existing product screenshots have useful alt text
and source context. Flow diagrams are ordered semantic steps, not images alone.
Table scrolling and compact/mobile navigation were verified. Contrast and flow
wrapping refinements were made before the passing browser run. Remaining design
limits: browser search is local rather than a hosted index, and translation and
contextual in-product help are not part of this documentation release.

These tests validate the documentation and its built reader, not every product
workflow in a production tenant. No bank decision, real call, email, merchant
operation, live model/provider execution or payment was exercised by this suite.

## Local Harness Corrections

The workstation bind-mount full-suite attempts were stopped and replaced with
a committed source snapshot in Docker's Linux filesystem. A first snapshot run
also exposed an invalid harness assumption: globally exporting Git metadata
environment variables broke tests that create their own temporary repositories.
Those setup failures are not documentation/product fixes. The harness now uses
a read-only worktree metadata link instead; the affected denylist test module
then passed all 84 tests.
The first complete corrected-harness run finished with 10,341 passing tests,
16 skips, five expected failures and the three release-contract failures above.
The corrected complete run passed `make check`, the full preflight, all 24
documentation/SEO generator tests and both `make test` gates. The harness used
Python 3.12.14 and the production-pinned Node 26 image in Docker's Linux
filesystem, with read-only Git metadata and a committed source snapshot. The
`COMPOSE=true` argument reused already-healthy isolated Postgres/Redis services;
database reset, unit coverage and integration/regression tests still ran. No
test assertions, security gate or required check was disabled. Live external
provider opt-in cases remained skipped by their existing policy.

## Release And Registrar Handoff

The owner confirmed DNS is managed at the domain registrar. Publishing the
manual requires the normal UI release. Activating `docs.agenticorg.ai` additionally
requires an approved HTTPS host binding and the exact DNS record supplied by that
hosting configuration. It is a subdomain, not a nameserver transfer.

Do not guess a CNAME, alter apex/app records, broaden authentication cookies or
expose private APIs. See [the hosting and registrar runbook](../runbooks/documentation-site.md).
The canonical address remains `https://agenticorg.ai/docs` until a coordinated
host migration is deliberately made. Verify both public URLs after release;
local screenshots and a working Docker preview are not production evidence.
