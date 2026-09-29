# Public Documentation Site

## Architecture And Current Status

The end-user manual lives in `docs/user-guide/`. The landing UI renders it at
`/docs` and `/docs/<slug>`. The build generates the interactive reader data and
route-specific HTML with the complete article in a no-JavaScript fallback.
Full-text search runs locally in the browser; reading guides needs no login or
API key. No document search terms are sent to a backend by this implementation.

Use `https://agenticorg.ai/docs` as the canonical public URL after release.
`docs.agenticorg.ai` is an optional hostname alias for the same public UI service,
not a separate nameserver and not an authenticated tenant application. On that
host only, `/` redirects to `/docs`. Canonical links still point to
`agenticorg.ai/docs/...` to avoid duplicate search results. A future canonical
host move must update metadata, sitemap, LLM assets and links together.

Read-only DNS inspection on 2026-09-29 found no `docs.agenticorg.ai` record. This
change does not provision DNS, TLS, a load balancer or a Cloud Run domain mapping.
The owner confirmed that DNS is managed at the domain registrar.
The deployment helper defaults to Cloud Run `asia-southeast1`; verify the actual
deployed region and hosting before choosing a domain-binding method.

## Maintain And Verify The Manual

1. Update article Markdown and `index.json` with reviewed source references.
2. Run `npm --prefix ui run seo:sync` to regenerate reader data, discoverability
   files and both nginx CSP hash maps.
3. Run `npm --prefix ui run typecheck`, `npm --prefix ui run lint`,
   `npm --prefix ui test`, `npm --prefix ui run test:seo` and
   `npm --prefix ui run build`.
4. Serve the built UI with the repository nginx configuration. Run
   `npx playwright test --config=playwright.docs.config.ts` from `ui`, setting
   `DOCS_BASE_URL` to that local server. Never point this regression at production
   without reviewing the target.
5. Inspect desktop/mobile screenshots, search results, article anchors, images,
   print view, keyboard navigation, metadata and no-JavaScript article content.
6. Confirm unknown `/docs/<slug>` addresses return HTTP 404, not the homepage.

Generated `ui/src/content/userDocs.generated.json` is checked in so a fresh
checkout can typecheck before running the generator. Markdown remains the sole
authored source. Node regression tests detect stale generated data.

## Publish The UI

Follow the existing UI release process and release permissions; do not bypass CI
or assume a local preview is public. The standard UI image includes the guides,
screenshots, static HTML, public discovery files and nginx routing. No API schema,
database migration or external model/provider call is required to read the manual.

After deployment, verify `/docs` and representative guides on the public domain,
including the first-agent exercise, OCR guide and BFSI business-onboarding flow.
Confirm the app-sidebar link reaches the same maintained manual. Check that static
metadata has one title/canonical and that articles appear in `sitemap.xml`,
`llms.txt` and `llms-full.txt`.

## Add docs.agenticorg.ai

Use the existing public hosting arrangement if it supports another hostname.
For a production Cloud Run origin, prefer the existing supported external HTTPS
Application Load Balancer/custom-host solution; Google describes direct Cloud Run
domain mapping as limited/preview and not recommended for production. Do not
create duplicate infrastructure without inspecting the current host binding.

1. Confirm who controls the `agenticorg.ai` DNS zone and the existing public UI
   host binding. Leave apex and `app.agenticorg.ai` routing intact.
2. Configure `docs.agenticorg.ai` as an HTTPS frontend host with a managed
   certificate and route it to the public UI service, preserving the Host header.
3. Obtain the **actual DNS target from that hosting configuration**. Add only the
   required A/AAAA/CNAME and ownership/certificate validation records. Do not
   invent a CNAME from a Cloud Run service name or change NS records for the zone.
4. Start with a modest DNS TTL such as 300 seconds during activation. Verify the
   authoritative DNS answer, certificate issuance and HTTPS host routing.
5. Verify `https://docs.agenticorg.ai/` redirects to `/docs`, article URLs load,
   unknown guides return 404, and canonical links still use `agenticorg.ai`.
6. Check the existing apex/app hosts still work. Do not broaden authentication
   cookies to `.agenticorg.ai`, expose private APIs, or add CORS trust merely to
   publish a public manual.

Rollback removes the new host routing/DNS record only; the manual remains on
`agenticorg.ai/docs`. Never remove the application's existing domain bindings.

### Registrar Handoff Checklist

In the registrar console, open the DNS zone for `agenticorg.ai`, not its
nameserver-transfer page. Add the record supplied by the configured HTTPS host:

| Field | Value |
| --- | --- |
| Name / Host | `docs` (or the full hostname if the registrar requires it) |
| Type | The A/AAAA or CNAME type required by the actual hosting configuration |
| Value / Target | The exact IP/hostname supplied by that configuration |
| TTL | 300 seconds during activation, where supported |

Add any certificate/ownership validation record exactly as supplied, then wait
for authoritative DNS and managed-certificate validation. A CNAME alone does not
bind the origin's TLS certificate to `docs.agenticorg.ai`. Do not use this table
to guess the target or copy a record from an unrelated project.

Provider references (reviewed 2026-09-29):
- [Cloud Run custom domain options](https://docs.cloud.google.com/run/docs/mapping-custom-domains)
- [Cloud Run regions](https://cloud.google.com/run/docs/locations)
- [HTTPS load-balancer URL maps](https://docs.cloud.google.com/load-balancing/docs/url-map-concepts)

## Content Ownership

Keep user instructions separate from historical C6/C6W/C6X/C6Y reports and retained
QA evidence. Do not delete release evidence to resolve a wording conflict; update
the current manual and explicitly label historical sources. BFSI playbooks must
retain institution-specific integration, human decision and data-authorization
boundaries. No compliance, universal provider availability or marketplace approval
claim follows from publishing documentation.
