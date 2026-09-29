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

The approved hostname setup on 2026-09-29 created a Cloud Run domain mapping to
`agenticorg-ui` in `asia-southeast1`, project `perfect-period-305406`. Google
returned `docs.agenticorg.ai. CNAME ghs.googlehosted.com.`; the record was added
with TTL 300 in the existing Cloud DNS zone `agenticorg-ai`. The domain mapping
and managed certificate are Ready. Namecheap is the registrar, but the
authoritative nameservers are `ns-cloud-e1.googledomains.com` through
`ns-cloud-e4.googledomains.com`: DNS edits belong in Cloud DNS, not Namecheap
Advanced DNS. No nameserver transfer or apex/app/email record change is needed.

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
7. Verify the alias root behind HTTP upstream/TLS termination: its Location must
   be exactly `/docs`, never `http://docs.agenticorg.ai:8080/docs`. Both nginx
   root locations use `absolute_redirect off`; forwarded headers must not change
   the destination. Checking only that a Location ends in `/docs` is insufficient.
8. For an approved public recheck, set
   `DOCS_HOST_BASE_URL=https://docs.agenticorg.ai` alongside `DOCS_BASE_URL`.
   The dedicated HTTPS alias test follows the real root redirect, searches and
   opens a guide, checks its canonical URL and verifies unknown-guide 404.

Generated `ui/src/content/userDocs.generated.json` is checked in so a fresh
checkout can typecheck before running the generator. Markdown remains the sole
authored source. Node regression tests detect stale generated data.

## Publish The UI

Follow the existing UI release process and release permissions; do not bypass CI
or assume a local preview is public. The standard UI image includes the guides,
screenshots, static HTML, public discovery files and nginx routing. No API schema,
database migration or external model/provider call is required to read the manual.

For a documentation-only release, use the existing `Dockerfile.ui.cloudrun` to
build and push a commit-tagged UI image from the CI-green merged commit. Keep
the existing UI runtime configuration and API origin unchanged. Stage the image
with `gcloud run services update --no-traffic`, verify the new ready revision
through a temporary revision tag, then route UI traffic to that exact revision.
Retain the previous UI revision for traffic rollback. Do not redeploy the API,
workers, beat service or migration job merely to publish guides. The full
`scripts/deploy_cloud_run.sh` remains the migration-first procedure when backend
changes are deliberately part of a release.

Both UI Dockerfiles preserve the repository layout for source validation and
copy only the public manual and reviewed documentation references allowed by
`.dockerignore`. A missing manual must fail the build, not produce zero guides.
Only built HTML/assets are copied into the final nginx UI image.

After deployment, verify `/docs` and representative guides on the public domain,
including the first-agent exercise, OCR guide and BFSI business-onboarding flow.
Confirm the app-sidebar link reaches the same maintained manual. Check that static
metadata has one title/canonical and that articles appear in `sitemap.xml`,
`llms.txt` and `llms-full.txt`.
Also open `https://docs.agenticorg.ai/` in desktop and mobile browsers with normal
certificate verification. A working `/docs` URL does not prove the hostname's
root redirect works. If a UI-only routing regression occurs, roll UI traffic
back to the recorded previous revision; do not remove a healthy DNS/TLS binding.

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

### Authoritative DNS And Host Checks

The current deployment uses the existing approved direct Cloud Run domain
mapping. Its limited/preview status remains an infrastructure consideration;
this routing repair does not create a new load balancer or change that hosting
decision. Inspect the mapping and authoritative DNS with:

```powershell
gcloud beta run domain-mappings describe --domain docs.agenticorg.ai --region asia-southeast1 --project perfect-period-305406 --platform managed --format=json
gcloud dns record-sets describe docs.agenticorg.ai. --type=CNAME --zone=agenticorg-ai --project=perfect-period-305406
Resolve-DnsName docs.agenticorg.ai -Type CNAME -Server 8.8.8.8
curl.exe -I https://docs.agenticorg.ai/
curl.exe -L --fail https://docs.agenticorg.ai/
```

Ready certificate/mapping conditions and a successful HTTPS redirect are
separate checks. Use the following registrar checklist only if a future DNS
provider change makes the registrar authoritative; it does not apply to the
current Google Cloud DNS zone.

### Conditional Registrar Handoff Checklist

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
