# End-User Documentation Validation - 2026-09-29

## Scope And Status

Initial local validation snapshot, based on main `5099d43e`, then rebased onto
`3e513d7f`. Release validation
against the final main branch is recorded separately below when completed.

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

## Verification Results

| Check | Result |
| --- | --- |
| UI unit/component suite | 52 files, 341 tests passed |
| Documentation and SEO generator suite | 24 tests, including packaging and cross-platform generation regressions |
| Playwright documentation suite | 12 tests passed against the actual Cloud Run image across desktop and mobile projects |
| TypeScript and production UI build | Passed |
| SEO output verification | Passed for 109 route descriptors and 98 canonical sitemap URLs |
| ESLint | No errors; 23 existing warnings in unrelated application pages |
| npm dependency audit | Zero reported vulnerabilities |
| Docker nginx configuration | Passed |
| Tracked diff whitespace check | Passed |
| New-file ASCII and scoped credential-literal/bidi scans | Passed |

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
