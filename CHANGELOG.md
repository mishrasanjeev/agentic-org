# Changelog

## Unreleased - Dependency compatibility and security patches

- Refresh the compatible Python and UI dependency updates together, preserving the
  required embedding and PII-analysis dependency bounds.
- Require pypdf 6.21.2 and keep the exact requirements pin aligned with the project floor.
- Update the UI's DOMPurify and Undici overrides to patched releases and retain
  regression checks for their locked versions. No audit exclusions are added.
- Require Brotli runtime library 1.2.1-r0 or newer in both UI images to fix
  CVE-2026-111497, without changing scanner exceptions or severity thresholds.

## Unreleased - Hosted MCP endpoint for the demo shop

- `agenticorg-mcp-server` gains a hosted Streamable HTTP endpoint (`mcp-server/src/http.ts`,
  `mcp-server/Dockerfile.hosted`) so clients with a configured caller bearer credential can search
  the synthetic demo shop and open a checkout. It exposes only the five shop
  tools and nothing that pays, with a fixed shop, profile and key, a host allowlist, a body limit
  and validated rate limits. Caller authentication is separate from the shop credential;
  forwarded addresses are ignored unless an operator reviews the trusted proxy topology.
  The image build runs the MCP security regressions before container scanning. Anonymous and
  OAuth-only hosted-assistant access are not implemented.
## Unreleased - Nilgiri Tea House guide: Telegram screenshot

- The Telegram run in the Nilgiri Tea House guide is now a screenshot of the bot's plain-text reply
  (product, price, stock, checkout id, status, total, stop before payment) instead of a quoted
  Markdown answer.

## Unreleased - Nilgiri Tea House guide: real agent runs

- The Nilgiri Tea House example guide gains "See it with real agents": three screenshots of Gemini
  CLI shopping the live demo shop through the AgenticOrg MCP server (search, checkout, pickup, then
  the stop at payment with `payment_failed`, "A payment instrument is required to complete"), and
  the reply of a Telegram agent using the `ucp_buyer` connector. The checkout step now describes the
  real payment stop instead of `executor_unavailable`.
## Unreleased - Channel replies send plain-text answers as text

- Fixed: when an agent answered in plain text, Telegram and other answer replies showed the agent
  graph's wrapper (`{"raw_output": "...", "status": "completed"}`) instead of the answer. A
  wrapper holding only a text `raw_output` (and `status`) is now sent as that text; every secret and
  size filter still applies, and any other output is unchanged.

## Unreleased - Merchant custom domains through Firebase Hosting

- Behind `AGENTICORG_CUSTOM_DOMAIN_FIREBASE_ENABLED` (default off) and `AGENTICORG_CUSTOM_DOMAIN_FIREBASE_SITE`,
  new merchant custom domains go through a Firebase Hosting site in front of `agenticorg-api` instead of a
  Cloud Run domain mapping. The merchant publishes two DNS records (a CNAME to `<site>.web.app` and the
  `_agenticorg-ucp` TXT that binds the host to its tenant). AgenticOrg creates, follows and deletes the
  domain through the Firebase Hosting API. There is no Search Console owner and no operator step; only
  failed Firebase domains reach the operator queue, without a command.
- `api/middleware/merchant_host.py` is active only with the flag on, and only on the agent paths
  (`/.well-known/ucp`, `/.well-known/ucp/*`, `/ucp/*`, `/acp/*`, `/.well-known/agenticorg-host`). It takes
  `X-Forwarded-Host` as `Host` only when it names a domain AgenticOrg connected through Firebase (provider
  `firebase_hosting`, `pending_certificate` or `live`), and adds `Cache-Control: no-store` to responses
  without a policy, because Firebase caches those for ten minutes.
- `scripts/release_merchant_domain_site.py` releases the site with rewrites for the agent paths only;
  every other path on a merchant domain is Firebase's 404.
- Migration `v6z93_custom_domain_firebase` adds `merchant_custom_domains.provider` (existing rows stay
  `cloud_run_mapping`) and admits `firebase_hosting` as a mapping source.
- Docs: the custom domains guide and runbook describe the Firebase setup, the spike results, limits, cost
  and rollback; the Cloud Run domain-mapping flow remains as the older option.

## Unreleased - Company agents can use tenant-wide connectors again

- Fixed: an agent that belongs to a company was refused with
  `connector_not_ready_for_dispatch` / `missing_connector_config` when its connector was registered
  for the whole tenant (BUG-01 of 2026-09-21, reopened). Row-level security on `connector_configs`
  shows a company-scoped session only that company's rows, so the tenant-wide fallback never found
  the shared row. Config resolution and dispatch readiness now read the tenant-wide binding in a
  tenant-scoped session. Precedence is unchanged: the company's own binding, then the tenant-wide
  one, never another company's. The RLS policy is unchanged.

## Unreleased - Sandbox checkout at operator-listed demo shops

- `ucp_buyer` connector: `sandbox_checkout_create` and `sandbox_checkout_update`, the same checkout
  writes allowed only at merchant hosts listed exactly in `AGENTICORG_UCP_BUYER_SANDBOX_HOSTS`
  (empty by default; any other host is refused before a request is built). The action taxonomy
  classes them as `draft`, so an agent can open a checkout at the synthetic demo shop in production;
  the ordinary `checkout_create` / `checkout_update` stay customer-facing writes and contained.
  Owner decision of 2026-10-22; see `docs/oacp/ucp-buyer-connector.md`.

## Unreleased - UCP buyer connector

- New connector `ucp_buyer`: an agent can shop at a public UCP merchant (for example the demo shop
  on `nilgiritea.agenticorg.ai`) as a buyer. Tools: `catalog_search`, `catalog_lookup`,
  `checkout_create`, `checkout_get` and `checkout_update`, named `ucp_buyer:<tool>` in an agent's
  `authorized_tools`. It reads the merchant's `/.well-known/ucp`, uses the `dev.ucp.shopping` REST
  endpoint at 2026-04-08 only when it is on the profile URL's origin, sends the merchant-issued
  platform key only on checkout calls, and stops before payment: there is no complete, cancel or
  payment tool, and payment members in a checkout update are refused.
- The action taxonomy classes `ucp_buyer` tools by their qualified name: catalog search, catalog
  lookup and checkout read are reads; checkout create and update are customer-facing writes, not
  money. The bare `checkout_create` and `grantex_commerce:checkout_create` keep the money class.
- Guide: `docs/oacp/ucp-buyer-connector.md`.
## Unreleased - Buyer agents can search and check out at the demo shop over MCP

- `agenticorg-mcp-server`: new `search_catalog` and `lookup_catalog` tools over the merchant's
  public UCP catalog routes. They never send the configured credential and need no platform
  profile, so any MCP client can search a UCP shop such as `nilgiritea.agenticorg.ai`.
- A public UCP platform profile for AgenticOrg's demo buyer agent at
  `https://agenticorg.ai/ucp/platforms/agenticorg-demo-buyer/profile.json` (catalog, cart,
  checkout, fulfillment, buyer consent and order at 2026-04-08), for the `UCP-Agent` header of
  demo checkouts.
## Unreleased - Nilgiri Tea House example guide

- New docs guide `/docs/oacp-example-nilgiri-tea` ("Example: Nilgiri Tea House, end to end"): the
  live synthetic example merchant at `nilgiritea.agenticorg.ai`, with try-it commands anyone can run
  against the real host (profile and catalog search, with real answers), the setup steps behind it,
  its two DNS records, a buyer agent's purchase, a "do the same for your shop" table and what the
  demo does not do. Four new diagrams (story, shelf, DNS records, buyer flow). Linked from the OACP
  overview and the merchant setup checklist; the production layout check covers it at 320, 390 and
  1440 px in light and dark mode.

## Unreleased - Custom domains guide on the documentation site

- New OACP guide "Your own domain for buyer agents" (`/docs/oacp-custom-domains`, figure
  `oacp-custom-domain`). It covers the merchant flow on Commerce > Custom Domains, the CNAME and TXT
  records, the Search Console owner, every check and its fix, retry and removal, and the main domain
  without redirects (Cloudflare Worker and nginx examples). For platform operators it covers the
  settings to turn it on, the operator queue (connect and disconnect), the Cloud Run limits, the load
  balancer cost as a future decision, and how to turn it off.
- The merchant setup checklist links to it from phase 3, and the feature switches guide lists every
  custom-domain setting. `docs/oacp/merchant-custom-domains.md` has a production turn-on section with
  the exact commands. The UI image build includes the two custom-domain sources (`.dockerignore`),
  and the sitemap, `llms*.txt` and the CSP hashes are regenerated.

## Unreleased - Demo merchant seed on production schemas

- Fixed: the OACP demo merchant seed failed in production on `ck_c6z_onboarding_connector`.
  Databases migrated from `v6z_runtime_vertical_demo` carry shopify-only checks on the onboarding
  packet connector and the connector evidence source; databases bootstrapped from the ORM carried no
  checks, so the tests never met them. Migration `v6z92_c6z_demo_source` replaces both checks on
  every database: `shopify` or `synthetic_demo`, read-only. Which tenants may use the synthetic
  source is still decided by the application (the demo flag and the demo tenant marker).

## Unreleased - On-demand OACP artifact freshness

- Fixed (behind a flag): every merchant's UCP catalog, ACP feed, checkout decisions and public UCP
  profile went unavailable a minute after a Grantex issuance, because nothing renewed the
  short-lived artifacts (inventory 60 s, mandate capability 2 min, price 5 min, public discovery
  15 min). With `AGENTICORG_OACP_ON_DEMAND_REFRESH_ENABLED=true` (default off) those reads renew the
  merchant scope once when a required artifact is expired or expiring: a Redis single-flight lock
  per scope, at most one issuance every 30 s, backoff after a failure or refusal, a read wait of at
  most `AGENTICORG_OACP_REFRESH_WAIT_MS` (default 3000) after which the existing fail-closed refusal
  stands, connector evidence re-read first when it is too old for Grantex, every write ledgered,
  and the scope's cache bounded (newest 6 records per type, expired ones dropped after 5 minutes).
  Host routes refresh before they open any transaction. Nothing is issued while nobody reads.
- New optional beat task `sweep-oacp-artifact-freshness` (every 5 minutes, existing beat and
  worker; a no-op unless `AGENTICORG_OACP_REFRESH_SWEEP_ENABLED` and the on-demand flag are true)
  refreshing only scopes read in the last 15 minutes. Guide:
  `docs/oacp/artifact-cache-guide.md#on-demand-freshness`.
- The read-only Shopify sync route's body moved into a helper the refresh shares; its behaviour is
  unchanged.
- The seed script's `--keep-fresh-minutes` loop is removed: the on-demand refresh keeps the demo
  merchant fresh while it is being used.

## Unreleased - OACP demo merchant (synthetic)

- New `scripts/seed_oacp_demo_merchant.py`: creates, converges or tears down one clearly labelled
  synthetic demo merchant, the invented tea retailer "Nilgiri Tea House" (tenant
  `13bd6961-b1e4-56ca-b242-91fbdfe1b1b0`, admin `demo-admin@demo.agenticorg.example`): 10 teas and
  accessories in INR with stock, one Ooty store with `Asia/Kolkata` hours, a returns policy, the UCP
  signing key, the Grantex authority request with verified caching of the issued artifacts, and the
  public host in two runs: `--serving-host nilgiritea.agenticorg.ai` prints the exact DNS TXT record
  to create, `--verify-host` attests it after propagation and publishes the UCP profile
  (namespace `ai.agenticorg.nilgiritea`). Dry run by default (`--apply` writes); every write
  goes through the route handler and records its commerce ledger event in a tenant-bound session;
  `--teardown` deletes
  the demo's commerce rows and keeps the append-only ledger. It stops with the tenant id to
  allowlist when Grantex answers `service_tenant_not_allowed`, and with exit code 4 when Grantex
  does not accept the synthetic source. Runbook (with the Cloud Run domain mapping straight to
  `agenticorg-api` and the DNS records): `docs/runbooks/oacp-demo-merchant.md`. A Postgres test
  drives the real app on `Host: nilgiritea.agenticorg.ai`: profile, catalog and location reads,
  a UCP checkout and an ACP checkout session with a merchant-issued API key; no app change was
  needed for the host.
- New flag `AGENTICORG_OACP_DEMO_MERCHANT_ENABLED` (default off): the `synthetic_demo` connector
  source (merchant config, onboarding packet, connector evidence and authority request, which says
  `synthetic_data: true`). Off, every path accepts Shopify read-only sources only and an artifact
  naming the synthetic source is refused at caching, exactly as before. On, such an artifact is
  cached only for a tenant whose catalogue row carries the demo marker; any other tenant gets
  `422 synthetic_demo_artifact_refused` on the ledger. Shopify payloads are unchanged either way.
- Onboarding packets and connector evidence rows now store the connector source they carry instead
  of always writing `shopify` (every existing row is Shopify, so nothing changes for them), and
  synthetic catalog variants get `synthetic_demo_variant:` UCP item ids.

## Unreleased - Merchant config object lists

- Fixed: `PUT /api/v1/commerce/runtime/merchant-configs/{merchant_id}` stored every object inside a
  list of `provider_policy` (and of the brand profile and freshness policy) as its Python text, so
  the documented `provider_policy.ucp.links` (`privacy_policy`, `terms_of_service`), `payment_terms`
  and `fulfillment.method_combinations` were silently lost: UCP checkouts and the ACP feed showed no
  merchant links. Lists of objects and nested lists now keep their shape, each object passing the
  same unsafe-value checks (an object member such as `access_token` inside a list is now refused
  like one outside it); lists of plain values are cleaned exactly as before. Regression:
  `tests/regression/test_merchant_config_object_lists_20261021.py`.

## Unreleased - Merchant custom domains

- Merchants connect their own subdomain for the UCP profile and routes from **Commerce > Custom Domains**
  (`/dashboard/custom-domains`): add the host, publish the CNAME (`ghs.googlehosted.com.`) and the
  `_agenticorg-ucp` TXT record, confirm the Search Console owner, verify, and follow the mapping and
  certificate to live. Every failed check is shown with its fix, and failed domains can be retried. Zone apexes are refused
  with a pointer to the new apex guide (`docs/oacp/merchant-apex-domain.md`: Cloudflare Worker and
  nginx pass-through of `/.well-known/ucp` and `/ucp/*`, no redirects).
- The one manual step, the Cloud Run domain mapping, goes to an operator queue with the exact
  `gcloud` command, an email to the operator, and mark mapped or failed actions. The Cloud Run Admin
  API can create mappings instead behind `AGENTICORG_CUSTOM_DOMAIN_CLOUD_RUN_AUTOMAP_ENABLED`
  (default off; permissions documented).
- A status task (every five minutes) probes `https://<host>/.well-known/agenticorg-host` over the
  DNS-pinned egress transport and marks a domain live once its certificate verifies and the host
  reaches the API intact.
- Removing a domain that may have a mapping leaves a durable `pending_unmap` item in the operator
  queue (or an Admin API delete with automation on) instead of an email alone. A `failed` domain keeps
  its host reserved until removed. Checks use the CNAME target stored on the domain. Without
  `seller_agent_id`, a merchant with domains for several seller agents gets `409
  seller_agent_id_required`.
- Migration `v6z91_merchant_custom_domains`: tenant-scoped table with forced RLS and a SELECT-only
  platform-context policy. Everything is behind `AGENTICORG_CUSTOM_DOMAINS_ENABLED` (default off).
- `docs/oacp/merchant-custom-domains.md` records the Cloud Run domain-mapping limits and when a load
  balancer would replace them, with list-price estimates (a future decision, nothing provisioned).
- The serving-host challenge and verification in `api/v1/commerce_ucp_publishing.py` are now
  functions the custom-domain flow reuses; the routes behave as before.

## Unreleased - Platform licence change to the Orchestrum Personal Use License 1.0

### Breaking - licence

- From 21 October 2026 the AgenticOrg platform is licensed under the Orchestrum Personal Use
  License 1.0 (SPDX `LicenseRef-Orchestrum-Personal-Use-1.0`) instead of the Apache License 2.0.
  The licensor is Orchestrum Technologies LLP. Only an individual using it for their own personal
  purposes is free. Every organisation (companies, firms, sole proprietors' businesses, charities,
  schools, universities, research bodies and government) needs prior written permission in a
  signed agreement with Orchestrum Technologies LLP for any use, including evaluation, testing,
  pilots, internal use, hosting, resale and embedding, paid or free (`COMMERCIAL-LICENSE.md`,
  sanjeev@orchestrum.in). Distribution needs a written agreement as well.
- Not changed: the Python SDK (`sdk/`), the TypeScript SDK (`sdk-ts/`) and the MCP server
  (`mcp-server/`) stay open source under the Apache License 2.0, free for any use including
  commercial. Each now carries its own Apache `LICENSE`; their package metadata, classifiers and
  SPDX headers are unchanged. README, `COMMERCIAL-LICENSE.md`, `LICENSING.md`, the licensing page,
  the website footer and `llms.txt` state the split with a directory-to-licence table.
- The new terms apply to every version published on or after 21 October 2026, including all
  later updates. Versions published before that date remain available under the Apache License
  2.0 they were released with. Existing and new users alike need a signed agreement for any use by
  or for an organisation of a version from 21 October 2026 onward.
- New `TRADEMARKS.md`: "AgenticOrg" and "Grantex", and their names and logos, are trademarks of
  Orchestrum Technologies LLP; commercial use of the names, logos, hosted services or brand needs
  Orchestrum's prior written approval with any version, including earlier ones.
- `LICENSE` holds the owner-approved Orchestrum Personal Use License 1.0 text, which also states
  that the Apache-2.0 directories are licensed separately;
  `NOTICE`, `LICENSING.md`, `docs/OWNERSHIP.md`, the terms of service, the public licensing page
  (`/docs/licensing`), every public footer, the README, the SDK and MCP server READMEs, structured
  data and `llms.txt` state the new licence.
- Every platform SPDX header is now `LicenseRef-Orchestrum-Personal-Use-1.0`; files under `sdk/`, `sdk-ts/`
  and `mcp-server/` keep `Apache-2.0`. `scripts/check_license_headers.py` requires the identifier
  for the file's directory on new source files and rejects the other one. The root
  `pyproject.toml` (whose wheel also carries the Apache-2.0 SDK and vendored schemas) declares
  `LicenseRef-Orchestrum-Personal-Use-1.0 AND Apache-2.0` and drops the OSI Apache classifier; `ui/package.json`
  declares `LicenseRef-Orchestrum-Personal-Use-1.0`.
- Third-party material keeps its own licence: the vendored UCP schemas (`tests/fixtures/ucp/`) and
  ACP schemas (`core/commerce/acp/schemas/`) remain Apache License 2.0, with the licence text in
  `LICENSES/Apache-2.0.txt`.
- `CONTRIBUTING.md` adds a contributor licence: contributors keep their copyright and allow
  Orchestrum to license contributions under the Orchestrum Personal Use License 1.0 and commercially.
- `licensing/scope.json` now records the adopted licence, change date and third-party paths; the
  superseded draft custom licence (`licensing/ORCHESTRUM-SELF-USE-COMMERCIAL-1.0-DRAFT.md`) is
  removed. No runtime, billing, API or schema behaviour changes.
- `scripts/check_migration_required.py` no longer asks for a migration when every changed line of a
  `core/models/` file is a `#` comment (such as the SPDX header); any other change still needs one.
- Both UI images copy `LICENSE`, `COMMERCIAL-LICENSE.md` and `TRADEMARKS.md`, the sources of the
  licensing guide.

## Unreleased - OACP user guide section on the documentation site

- New documentation section "OACP — agentic commerce, made safe" at `/docs` with seven
  plain-language guides: what OACP is, how it works end to end, a purchase step by step,
  the merchant setup checklist, safety and trust, the feature switches reference and an FAQ.
  Each opens with an "In 30 seconds" summary and carries inline SVG diagrams and workflow
  cards with an owner and an "if blocked" answer per step.
- User guides can now include diagrams (a `figure` fence naming a registered diagram, rendered
  in place with its caption kept for readers without scripts and for `llms-full.txt`), a
  `summary` fence and numbered checklists that continue across sections (`flow start=N`).
- Guides marked `"colorScheme": "adaptive"` in `docs/user-guide/index.json` follow the
  reader's light or dark preference; every other guide keeps the light scheme unchanged.
- The SVG building blocks and the OACP diagrams of the blog explainer moved to
  `ui/src/components/figures/` so the blog and the docs share them; the blog renders as before.
- The UI image's build context now includes the `docs/oacp` pages the new guides cite.
## Unreleased - Knowledge upload browser test waits for the worker retry

- Fixed: the production browser test "a short document is indexed, searchable and opens its real
  cited excerpt" (`ui/e2e/knowledge-upload-recovery.spec.ts`) failed on every post-deploy run of
  2026-10-20. The hosted embedding service scales to zero; its cold start took about 80 s, the
  upload's own embedding call gave up after 5 s, and the upload was indexed by the maintenance
  worker's retry 79-86 s after upload, as designed. The test waited inside the suite's 60 s
  timeout. It now accepts exactly the two states an accepted upload may be in (`indexed`, or
  `processing` with a pending retry shown on the page), waits for a final state within a budget
  derived from the retry schedule, and stops at once on `failed`.
- Fixed: the spec's cleanup ran in the test body's `finally`, which runs after a timed-out test's
  request context has closed, so the uploaded document stayed in the production E2E tenant. Its
  three tests now remove what they created in `afterEach`.
- `tests/regression/test_knowledge_upload_e2e_budget_20261021.py` replays the cold-start timeline
  through `core/rag/upload_recovery.py` with the real retry delays, beat period and embedding
  timeout, and fails if the browser budget no longer covers it.
- `docs/runbooks/production_smoke.md` says what a cold embedding service looks like after a deploy
  and when a slow upload is a real failure. No product code changed.
## Unreleased - Security advisories of 2026-10-21 (Pillow 13, follow-redirects, hono)

### Security

- Pillow moves from 12.3.0 to 13.0.0 (`pyproject.toml` floor `pillow>=13.0.0`, and the cassette
  re-record lock). Pillow 13.0.0 fixes the JPEG, TIFF, GIF, PDF-parser and EPS advisories published
  with it, including GHSA-p4wg-p73j-c528, GHSA-65qm-jvmh-6pqg, GHSA-58r4-6rv3-gfh8,
  GHSA-pchr-8j38-3x4p, GHSA-q45j-58j2-45rg, GHSA-462x-pgg6-55fp, GHSA-c6hw-5grf-ff96 and
  GHSA-vx6w-vv9f-6gvg that failed the dependency audit on every pull request. The decoders are
  reachable through document and image OCR (`core/rag/extractors.py`, `core/idp/`), which open
  uploaded images. None of the APIs Pillow 13 removed are used here.
- fastembed 0.9.0, the default local embedding runtime and its latest release, declares
  `pillow<13`, which held resolution at 12.3.0. It is now installed from the hash-pinned
  `requirements-fastembed.txt` with `--no-deps` after the project (API image, tools image, CI test
  jobs, the live RAG eval and the developer setup in `README.md`, `CONTRIBUTING.md` and
  `docs/quickstart-local.md`); its other runtime requirements are declared in `pyproject.toml` with
  its own ranges. fastembed imports Pillow only for image embeddings, which the platform does not
  load. `scripts/pip_check.py` replaces `pip check` in both images and accepts only that one broken
  requirement (a fastembed Pillow requirement failing because Pillow is newer). The dependency audit
  covers `requirements-fastembed.txt` as a fourth target, and its "unreadable output" failure now
  includes pip-audit's exit code and stderr (pip-audit exits 1 with empty stdout when it cannot
  finish, which had hidden the cause in CI). When a fastembed release accepts Pillow 13,
  fastembed moves back into `pyproject.toml` and the file and script are deleted.
- UI: follow-redirects (via axios) moves from 1.16.0 to 1.16.1 through the `overrides` floor
  (`>=1.16.1`), fixing GHSA-q3jh-wrc6-jgq8 and GHSA-8r9p-f939-6h3c (credential headers kept on
  cross-origin redirects).
- MCP server: hono (via `@modelcontextprotocol/sdk`) moves from 4.13.7 to 4.13.13 through the
  `overrides` floor (`>=4.13.11`), fixing GHSA-5r4p-p66f-jhc7 (moderate; it failed the nightly
  `npm audit --audit-level=moderate`). The MCP server does not serve static files.
- Regression tests: `tests/regression/test_pillow13_fastembed_install.py` (patched floor, fastembed
  out of resolution and installed everywhere the project is, fastembed's other requirements declared
  and satisfied, fastembed importing under Pillow 13, the `pip_check.py` acceptance rule).

## Unreleased - Playwright 1.64

- Python `playwright` moves from 1.63.0 to 1.64.0 (`[v4]` extra and `requirements-v4.txt`); the
  API image downloads the matching Chromium build at build time, unchanged.
- Fixed: `testing/channels/Dockerfile.browser` still used the Playwright 1.63 browser image while
  the UI test runner it copies from `ui/node_modules` is `@playwright/test` 1.64, so its browsers
  did not match the runner. It now uses the 1.64 image the compose E2E services already use.
- The packaging regression test checks that the pyproject floor matches the `requirements-v4.txt`
  pin, and that every Playwright browser image matches the UI's locked `@playwright/test`
  version, instead of a hard-coded release.

## Unreleased - OACP closure documentation and the OACP explainer post

- New blog post "OACP explained: one trust layer under UCP, ACP and AP2"
  (`/blog/oacp-one-trust-layer-under-ucp-acp-ap2`) with theme-aware figures, a comparison table,
  a social image and a dated status section (what is on `main`, what is in review, what is in
  production). Blog posts can carry an optional summary, figures and a next step.
- Fixed: `ui/public/llms-full.txt` paired blog slugs with titles by position, so every entry after
  the CERT-In post's nested workflow titles carried another post's title; each entry now takes
  its own post's title.
- OACP documentation audited against `main` with a status date of 2026-10-20: the launch closure
  PRD, truth inventory, end-user and buyer flows, handoff, cache, POS, operations, troubleshooting,
  explainers, product status, merchant checklist, Shopify runbook, A2A guide, user guide, README
  and MCP server README. Artifacts are described as ES256-signed and verified before caching
  (no unqualified "signed" claims), ledger rows as HMAC-signed and hash-chained only once sealing
  is on, and execution as flag-gated with no payment executor registered, instead of "blocked".
- `docs/oacp/explainers/launch-rollback-runbook.md` now lists every OACP flag with its default,
  when to turn it on and its rollback, the staging-equivalent run
  (`AGENTICORG_ROUTE_ENFORCEMENT_MODE=log`, `AGENTICORG_HTTPSIG_VERIFY_MODE=log`,
  `AGENTICORG_UCP_CHECKOUT_ENABLED=false`, `PLURAL_PINE_LIVE_EXECUTION_ENABLED=false`) and the
  production turn-on order, one flag at a time.
- Older OACP blog posts carry a dated update note; the protocol mapping post no longer describes a
  UCP-style capability list, and the payment handoff post no longer lists a human confirmation
  step on the merchant side.

## Unreleased - Agent network identity (G47, G48, G49, G51, G52)

- README public-surface table: "the agent's RFC 9421 signature is verified" (the public-claims lint
  read "9421 agent" as an agent-count claim).
- The token-binding capability check narrows the capability `max_amount` with `isinstance`
  (booleans and strings still refuse) so the `mypy` gate passes.
- Tests: the covered-route and public-route checks walk FastAPI's lazily included routers
  (`tests/app_routes.py`) and fail when they find no route, so they check the real route table on
  the pinned FastAPI 0.143.
- Inbound RFC 9421 agent request signatures (`auth/httpsig.py`, `auth/httpsig_keys.py`) on every
  `/api/v1/public/commerce/` read and on `POST /api/v1/a2a/message:send` and
  `GET /api/v1/a2a/extendedAgentCard`, under the Visa Trusted Agent Protocol (`agent-browser-auth`,
  `agent-payer-auth`) and Web Bot Auth (`web-bot-auth`) profiles of `grantex-agent-httpsig` 0.2.0.
  `AGENTICORG_HTTPSIG_VERIFY_MODE=log` (default) verifies and records and never refuses; `enforce`
  refuses an unsigned or invalid request with 401 and `Accept-Signature` (RFC 9421 section 5.1)
  and answers 503 while the Redis nonce store is down. Default `log` changes no response; it adds a
  structured log line per request and, for a verified write, an `a2a.agent_signature` ledger row.
  Keys: TAP by keyid from pinned key-store URL templates (`AGENTICORG_HTTPSIG_TAP_KEY_STORE_URLS`);
  Web Bot Auth by (Signature-Agent URL, keyid) from allow-listed https directory origins
  (`AGENTICORG_HTTPSIG_SIGNATURE_AGENT_ORIGINS`), only keys with a valid directory response
  signature; the Grantex registry directory through its platform countersignature checked against
  the pinned Grantex JWKS. Async pinned egress, no redirects, 300 s cache, 60 s negative cache.
  Nonces are claimed with one Redis `SET NX` for at least the profile's replay window (8 minutes
  for TAP). Key fetches share a per-process budget (token bucket of 20, 2 per second, at most 4 in
  flight); a fetch over budget is not made or cached and the request is `key_unknown` (refused in
  `enforce`, logged in `log`). Docs: `docs/oacp/agent-request-signing.md`.
- `enforce` scope: only the commerce API surfaces can refuse (`verify_agent_request_signature` on
  `catalog.json`, `products/{product_slug}.json`, `message:send`, `extendedAgentCard`). The seller
  and product HTML pages, `schema-org.jsonld`, `sitemap.xml` and `llms.txt` use
  `observe_agent_request_signature`, which verifies and records but never refuses. Fixed with it:
  `GET /api/v1/public/commerce/sellers/{merchant_id}/products/{product_slug}.json` was shadowed by
  the product HTML route (`{product_slug}` matched `<slug>.json`) and answered HTML; it is now
  registered first and answers the product JSON.
- TAP body objects (G49, inbound): after a TAP signature verifies, `agenticConsumer`,
  `agenticPaymentContainer` and `grantexConsentRecord` in a JSON body are checked with
  `verify_tap_body_objects` and recorded (`usable_for_payment`). `POST /api/v1/a2a/message:send`
  accepts the three optional members.
- Scheme agent ids (G51): a TAP keyid is recorded as `tap_keyid`, never as a scheme agent id. With
  `AGENTICORG_HTTPSIG_REGISTRY_LOOKUP_ENABLED` (default off) a verified agent of either profile is
  looked up in the Grantex registry by the RFC 7638 thumbprint of its verifying key
  (`GET /v1/registry/agents?key_thumbprint=`; async, cached), and the DID and scheme agent ids the
  registry attests for that key are recorded. `AGENTICORG_A2A_SCHEME_AGENT_IDS` publishes AgenticOrg's own ids in the A2A
  agent card as the `https://agenticorg.ai/a2a/extensions/scheme-agent-ids/v1` extension.
  AgenticOrg serves no `/.well-known/agent-registry.json`.
- Outbound signing (G48): with `AGENTICORG_HTTPSIG_SIGN_OUTBOUND` (default off) the Grantex Commerce
  buyer connector signs every request with the tenant agent key under the TAP profile
  (`agent-payer-auth` for payment intents and checkouts, `agent-browser-auth` otherwise; `@method`,
  `@authority`, `@path`, `content-digest`; a fresh nonce per request) and, when configured, adds a
  Web Bot Auth signature with `Signature-Agent`. The key is a JWK sealed with the tenant crypto
  helpers in the connector configuration (`agent_signing_key_encrypted`); with signing on and no
  usable key the call is refused, never sent unsigned.
- TAP body objects (G49, outbound): a payment intent or checkout made with a granted Grantex consent
  carries `agenticPaymentContainer`, optionally `agenticConsumer`, and the Grantex consent record
  object `grantexConsentRecord`, bound to the signature's nonce. The `grantex_consent` and
  `tap_consumer_id_token` inputs are never forwarded as tool arguments.
- Scheme token binding (G52): `core/commerce/scheme_token_binding.py` mirrors the Grantex
  `SchemeTokenBinding` `{scheme, token_ref, agent_id, merchant_scope, limits}` (schemes `visa`,
  `mastercard`, `upi_autopay`, `provider_token`). A `mandate_capability` artifact carrying
  `token_binding` is now refused with `token_binding_invalid` when the binding is malformed, its
  `token_ref` holds a run of 13 to 19 digits, or it does not sit inside the capability (agent,
  merchant scope, currency, cap, expiry), as Grantex already refuses when it issues one. Artifacts
  without the member are unaffected. `ExecutionCheckout.scheme_token_binding` (default `None`)
  carries the parsed binding to the rail's executor; nothing executes it here.
- Dependency: `grantex-agent-httpsig` 0.2.0 is not published to PyPI. `requirements.txt` and
  `pyproject.toml` install it as a direct git reference pinned to a full commit sha of the Grantex
  repository (`#subdirectory=packages/agent-httpsig-py`; hatch `allow-direct-references`); the
  production image's builder stage installs `git`; pip-audit carries a reviewed `[[skip]]` for it.
  A regression test refuses a branch, tag or short-sha reference. The commit must exist on GitHub
  before CI can install it; the sha moves to the Grantex `main` merge commit once Phase 5 lands.
- Fixed: the public buyer-safe seller pages and catalog reads under `/api/v1/public/commerce/`
  (`api/v1/commerce_public.py`, all `GET`, `route_meta(auth_required=False)`, documented as public in
  `docs/commerce-agent-bridges-guide.md`) answered `401 Missing session cookie or Authorization
  header` to every anonymous request because the auth middleware did not exempt them. They are now
  exempt; the merchant's `public_publishing.enabled` switch and
  `OACP_PUBLIC_CATALOG_PLATFORM_DISABLED` still gate them. The same class affected
  `GET /api/v1/org/invite-info` (the accept-invite screen's preview, called before login), now
  exempt as well. A regression test pins that every route declared public is exempt
  (`tests/regression/test_public_route_exemptions_20261020.py`).

## Unreleased - ACP Complete uses server-verified mandates (G41, D1)

- The ACP vendor extension `agenticorg_commerce` accepts `mandate_request` on complete and passes it to
  the UCP Complete it runs, so ACP sessions obtain Intent, Cart and Payment mandates from Grantex and
  are decided on server-verified authority like UCP sessions; strict runtimes refuse caller-asserted
  authority on ACP too. The mandates are ledgered as `commerce.acp.checkout.mandates`. Tests:
  `tests/integration/test_acp_checkout_postgres.py` (strict refusal, verified mandates) and
  `tests/unit/test_acp_checkout_g34.py`.
- `mandate_capability_evidence` builds its rail list as strings (mypy 2.x); no behaviour change.

## Unreleased - UPI mandate extension schema hosted at its advertised URL (G56)

- `ui/public/ucp/extensions/upi-mandate/2026-10-20/schema.json` publishes the JSON Schema of the
  UCP vendor extension `ai.agenticorg.payment.upi_mandate` at
  `https://agenticorg.ai/ucp/extensions/upi-mandate/2026-10-20/schema.json`, the URL the profile
  advertises (served by the UI image like the Plural handler config schema). It declares the
  Action `ai.agenticorg.payment.upi_mandate_authorization` and the mandate terms. A regression
  test maps the advertised URL to the file and the nginx static rule, and validates the Action the
  server emits against it.

## Unreleased - Plural 3-D Secure and pending payments reconciled from verified webhooks (G55)

- A verified Plural order event relayed to `POST /api/v1/commerce/runtime/ucp/plural/events`
  for an order the executor created (`create_order` journal row with that Plural order id and
  `aoc-` reference) is reconciled once (`PluralExecutor.reconcile`): `PROCESSED`/`CAPTURED`
  records the capture (`source: webhook`) and completes the checkout into its order;
  `AUTHORIZED` (after the buyer's 3-D Secure step, or a pending payment authorised later)
  captures the authorised amount with the same merchant reference under the decision consumed at
  Complete, unless a capture was already requested; `FAILED` records the failure and returns the
  checkout to `ready_for_complete`. Redelivered and out-of-order events change nothing and make no
  provider call. The checkout keeps what this needs in `state.execution` (decision id, executor,
  stored instruments; no credential); the order creation is shared with Complete
  (`handlers._complete_into_order`, `handlers.reconcile_execution`, ledger
  `commerce.ucp.checkout.reconciled`). The route answers `payment: {journal, session}`. No new
  flag: the route and executor are already behind authentication and
  `PLURAL_PINE_LIVE_EXECUTION_ENABLED`. Docs: `docs/oacp/plural-executor.md#reconciliation`.

## Unreleased - Server-verified Grantex mandates close D1; not-present rule set; buyer agent on UCP and refund decisions (G41, G44, G45, D1)

- Mandate client (`core/commerce/mandates_client.py`): a UCP Complete carrying
  `ai.agenticorg.commerce.mandate_request {passport_jwt, presence, description,
  max_amount_minor_units?, webauthn_assertion?}` asks Grantex for the Intent, Cart and Payment
  mandates in order with the service token (async, pinned egress transport, async egress
  validation, 5 s, no redirect, no retry). A `human_present` cart carries the buyer's WebAuthn
  assertion over `checkout_hash` exactly as the platform sent it; nothing is generated
  server-side. The checkout response names `ai.agenticorg.commerce.mandate_checkout
  {checkout, checkout_hash}` for merchants with `provider_policy.ucp.grantex_mandates`. Behind
  `AGENTICORG_GRANTEX_MANDATES_ENABLED` (default off; enable after the Grantex mandate release).
- Local verifier (`core/commerce/oacp_mandates.py`): the ordered refusal codes of the Grantex
  mandate profile (`mandate_malformed` through `mandate_suspended`), the chain rules
  (`mandate_chain_mismatch`) and the binding to the session (`mandate_binding_mismatch`), using
  the pinned JWKS and the Token Status List reader of `oacp_grantex_trust` (new
  `OacpGrantexArtifactVerifier.status_at`, shared with artifacts). Every case of the shared
  vectors passes (`tests/fixtures/oacp/oacp-mandates-v1.json`, `oacp-mandates-v1-extra.json`).
- D1 closed: `buyer_authority_verified` passes only with the server-side ref the verifier
  produced (`oacp-mandate-v1;jti;mandate_hash;verified_at`) naming the decision's own mandate jti
  and hash; any other ref refuses `buyer_authority_ref_invalid`. The decision's mandate evidence
  is the payment mandate jti and `mandate_hash` (hex SHA-256 of the compact JWT); the decision
  and the ledger (`commerce.arbiter.decision`, `commerce.ucp.checkout.mandates`) seal the three
  jtis and hashes, and the Plural order metadata carries the three hashes.
- Arbiter policy `commerce-arbiter-v4` (**breaking for decision shape**: `rules_evaluated` gains
  `human_not_present_limits`, the decision id material gains the presence). A
  `human_not_present` purchase must be within `provider_policy.arbiter.human_not_present` (lower
  caps) and the intent mandate cap, and a UPI AutoPay instrument has no AFA-exempt path. No human
  step: the rule refuses and the refusal is sealed.
- **Breaking in strict runtimes (sibling path of D1):** UCP session complete now passes
  `require_verified_authority` like the handoffs, so caller-asserted authority is refused there
  too. Opt-out: `AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY=false`.
- UCP Complete seals a fresh decision in its own transaction, offers it to Grantex after the
  commit, then consumes the allow in a second transaction, so a strict runtime that requires the
  Grantex grant can consume it (before, UCP decisions were never countersigned and strict
  runtimes refused them `grantex_grant_missing`). A concurrent Complete still gets 409.
- Buyer agent: UCP sessions record `state.platform.buyer_agent_id` (`ucp_platform:` and 40 hex
  characters of the SHA-256 of the `UCP-Agent` profile URL) at create time; session-complete
  decisions name it, and refund decisions name the buyer agent of the order's session, so Grantex
  can countersign them.
- Docs: `docs/oacp/mandates.md#grantex-mandates`, `purchase-mandate-handoff.md`,
  `arbiter-ledger.md` (rules v4), `truth-inventory.md`, the launch and rollback runbook.
  Live check: `tests/integration/test_grantex_mandates_live.py`.

## Unreleased - Mandate-capability evidence for Grantex (G43, AgenticOrg side)

- `core/commerce/mandate_capability_evidence.py` builds `oacp.mandate_capability_evidence.v1` from
  the stored Plural capability verifier result and the merchant's `mandate_capability`: rails
  (`plural`, `card_network_token`, `upi_autopay`), caps per rail, token types, `verified_at`
  (the verifier's `checked_at`) and an `evidence_hash` over the redacted result. With
  `AGENTICORG_GRANTEX_MANDATE_CAPABILITY_EVIDENCE_ENABLED=true` (default off) the Grantex C6Z
  authority request carries it as the top-level member `mandate_capability_evidence`; a stale,
  expired or capless result sends nothing. Docs:
  `docs/oacp/plural-pine-p3p-capability-verifier.md#evidence-for-grantex`.

## Unreleased - Google Merchant Center UCP onboarding runbook (G57)

- New runbook `docs/runbooks/oacp-google-merchant-center-onboarding.md`: the UCP `2026-04-08`
  profile Google targets, AgenticOrg prerequisites and flags, verification against the serving
  host, the Merchant Center steps (cart transfer; native checkout needs the Google Pay handler,
  which AgenticOrg does not offer), rollback and residual gaps, from Google's public UCP pages.

## Unreleased - Multi-source precedence per fact family (G61)

- `provider_policy.source_precedence` sets, per fact family (`price`, `inventory`, `policy`,
  `catalog`, `location`), which source wins when Shopify and the config-only adapters disagree;
  `provider_policy.source_facts` holds the values a config-only adapter (`woocommerce`, `erp`,
  `pim`, `oms`, `wms`, `custom_api`) contributes. Those adapters are never called. The UCP catalog
  routes and checkout pricing resolve price, stock and title through the same table
  (`core/commerce/source_precedence.py`); without a table or saved facts Shopify's evidence is
  used exactly as before. The merchant-config save refuses an invalid table or fact set.
  Docs: `docs/oacp/merchant-commerce-configuration.md#source-precedence`.

## Unreleased - Plural executor, UPI AutoPay and RBI AFA, refunds and tracking (G55, G56, G58)

- Plural executor (`core/commerce/executors/plural.py`, G55): creates a `pre_auth` Plural order,
  pays with the tokenised instrument and captures; refunds captured orders. It runs only with a
  consumed arbiter decision (`session_complete`, or the new `refund` action), checks the amount
  against the session total, the captured amount and the handler allowance
  (`mandate_capability.max_amount_minor_units`) before any call, and carries the decision id and
  the intent, cart and payment mandate hashes (empty until verified mandates land) in the order
  metadata. Every call is journalled in `commerce_payment_operations` (a `requested` row before
  the call; a repeated decision is refused without an HTTP call) and ledgered
  (`commerce.plural.create_order`, `.create_payment`, `.capture`, `.refund`). Async `httpx` over
  the pinned egress transport, no retry of order creation. Registered only while
  `PLURAL_PINE_LIVE_EXECUTION_ENABLED=true`; otherwise UCP Complete keeps answering
  `executor_unavailable`. Decision recorded in `docs/oacp/plural-executor.md`: direct Plural
  execution; Grantex Commerce V1 execution is the alternative, not built.
- Plural webhook verification is shared with billing
  (`core.billing.pinelabs_client.plural_signature_valid`; billing behaviour unchanged). A merchant
  integration relays Plural events to `POST /api/v1/commerce/runtime/ucp/plural/events` (tenant
  admin); the Plural signature is verified, so a relay cannot forge one.
- UPI AutoPay and RBI AFA (`core/commerce/india_rails.py`, G56): `mandate_capability.upi_autopay`
  (amount rule and limit, frequency, validity, purpose and the authorization URL; the
  NPCI-unconfirmed values are configuration) enables `upi_autopay` instruments. RBI e-mandate
  framework 2026 thresholds are settings. The arbiter rule `afa_evidence_present` (last rule of
  `session_complete`) refuses a UPI AutoPay completion until server-side AFA evidence exists;
  the response carries the buyer's out-of-band step as the UCP Action
  `ai.agenticorg.payment.upi_mandate_authorization` (vendor extension
  `ai.agenticorg.payment.upi_mandate`, 2026-08-25) or as `requires_escalation` with
  `continue_url`; `acp_afa_out_of_band` maps the same step for ACP. A verified Plural mandate
  event records the evidence and returns the session to `ready_for_complete`. Nothing waits.
- Arbiter policy `commerce-arbiter-v3`: the `refund` action (rule `refund_within_captured`), the
  `afa_evidence_present` rule on `session_complete`, and `mandate_hashes` in the stored decision
  evidence. `session_complete` decisions list one more rule in `rules_evaluated`; outcomes for
  sessions without a UPI AutoPay instrument are unchanged.
- Refund orchestration on the order entity (G58): `POST
  /api/v1/commerce/runtime/ucp/orders/{order_id}/refunds` takes a fresh `refund` decision,
  consumes it, refunds through the Plural executor and appends a `refund` adjustment; partial
  refunds are bounded by the captured amount less earlier refunds (pending ones included).
- Tracking normaliser (`core/commerce/fulfillment_tracking.py`, G58): Shopify fulfillment and
  fulfillment-event payloads become UCP order fulfillment events (status map, carrier aliases
  from `provider_policy.ucp.carrier_aliases`, source time in UTC), recorded with
  `POST /api/v1/commerce/runtime/ucp/orders/{order_id}/shopify-fulfillments`; a redelivered event
  has the same id and is recorded once.
- Migration `v6z90_mandates_rails` (after `v6z89_commerce_sessions_orders`):
  `commerce_payment_operations` and `commerce_afa_evidence`, ENABLE + FORCE row-level security,
  tenant policy with `WITH CHECK`, append-only trigger `commerce_payment_record_reject_mutation`,
  `SELECT, INSERT` for `agenticorg_app` (classified in `scripts/create_app_role.sql`); the arbiter
  action CHECK admits `refund`. Forward-only.

## Unreleased - ACP product feed, checkout sessions and order events (G33, G34, G35, G37, G38, G40)

- ACP product feed (`core/commerce/acp/feed.py`), behind `AGENTICORG_ACP_FEED_ENABLED` (default off):
  `GET /api/v1/commerce/runtime/acp/feed?merchant_id=&format=json|jsonl|metadata|csv` for tenant
  administrators. ACP Feed `Product` JSON per the 2026-04-17 feed schema (validated in tests against
  the vendored schema file) and the merchant-feed CSV with `availability`, `seller_*`, return and
  `enable_search` / `enable_checkout` columns. Built only from verified, fresh OACP catalog, price
  and inventory artifacts (409 otherwise); return terms only from the verified `policy` artifact.
- ACP checkout sessions at `https://<merchant serving host>/acp/checkout_sessions`
  (`api/v1/commerce_acp.py`), behind `AGENTICORG_ACP_CHECKOUT_ENABLED` (default off: every `/acp`
  route answers 404) and the merchant's `provider_policy.acp.checkout_enabled`. Create (201),
  partial update, get, complete and cancel (405 once not cancelable) run on the UCP session table
  and operations: one state machine, the same arbiter `session_complete` decision and executor.
  Sessions are tagged `protocol: acp` and owned by the creating API key (scopes `acp:read` /
  `acp:write`). ACP 2026-04-17 `Item` has no quantity: a repeated id is one more unit. Docs:
  `docs/oacp/acp-feed-and-sessions.md`.
- ACP headers: `API-Version` pinned to `2026-04-17` (`missing_api_version` /
  `unsupported_api_version` with `supported_versions`); `Idempotency-Key` required on POST, compared as
  canonical JSON (replay with `Idempotent-Replayed: true`, 422 `idempotency_conflict`, 409
  `idempotency_in_flight` with `Retry-After`); `Request-Id` echoed; `Signature` / `Timestamp` checked
  by a verifier registered per agent platform, `AGENTICORG_ACP_SIGNATURE_MODE=log` (default, never
  refuses) or `enforce`. ACP publishes no signing algorithm, so no verifier ships.
  An unexpected failure after the key is claimed releases it, so the client can retry.
- Refusals answered before an ACP route runs (401, 403, 404, 405, 429 from authentication, scope,
  rate-limit, flag and routing) are ACP `Error` objects on every `/acp` path (`AcpErrorShapeMiddleware`).
- ACP error mapping (`core/commerce/acp/errors.py`): every internal message code maps to the closed
  `MessageError` enum, UCP protocol errors to `Error` objects; blocker dicts never reach ACP responses.
- Capability negotiation: sessions declare the payment handlers served over ACP and the requested
  extensions that are served (the vendor extension `ai.agenticorg.commerce` for buyer authority,
  arbiter decisions and the Commerce Passport); the `discount` extension and interventions are not
  served and are not declared.
- Delegate-payment allowance per ACP session from Grantex
  (`POST /v1/commerce/oacp/acp/delegate-payment-allowance`; service token, async pinned transport,
  no retry, no card data), shown in the handler config in the `DelegatePaymentRequest` shape and
  sealed on the ledger; Grantex unreachable is an immediate business message; completion needs an
  issued allowance covering the total.
- `acp_hmac` order webhook subscriptions now deliver ACP `order_create` / `order_update` events (the
  full ACP `Order`) signed with `Merchant-Signature` (`t=..,v1=hex(HMAC-SHA256(secret, t.body))`,
  active key), verified against the shared vectors (`tests/fixtures/acp/merchant-signature-v1.json`).
  They replace the case-push HMAC headers and the placeholder payload of the unreleased UCP work.
  The order-webhook sweep also runs while only `AGENTICORG_ACP_CHECKOUT_ENABLED` is on.
- Order webhook isolation: an `acp_hmac` subscription is bound to the ACP agent platform's API key
  (`platform_api_key_id`, required) and receives only orders from sessions that key created; a UCP
  platform's subscription is never reused by another platform naming the same URL. ACP sessions are
  owned by the calling API key's id (`request.state.api_key_id`).

## Unreleased - UCP handler schema hosting and schema fixtures

- The Plural tokenised-instrument handler's config schema is served at the URL UCP profiles
  advertise, `https://agenticorg.ai/ucp/handlers/plural-tokenized/2026-10-20/config.json`, with its
  instrument schema beside it (`instrument.json`), from `ui/public` (static `*.json` in the UI image).
  A regression test maps both URLs to the files, checks the nginx static rule and validates the
  handler config the server emits against the hosted schema.
- Tests: the two vendored UCP schema fixture trees (`tests/fixtures/ucp` and
  `tests/fixtures/ucp_schemas`) are one, `tests/fixtures/ucp` (published release schemas plus the
  2026-04-08 discovery profile schema).

## Unreleased - UCP checkout tools in the MCP server (G39)

- `agenticorg-mcp-server` 5.1.0 adds the UCP 2026-04-08 MCP binding tools `create_checkout`,
  `get_checkout`, `update_checkout`, `complete_checkout`, `cancel_checkout`, `create_cart`,
  `get_cart`, `update_cart`, `cancel_cart` and `get_order` (the binding's names, not a
  `checkout.*` prefix). They are registered only when `AGENTICORG_UCP_PROFILE_URL` names a
  merchant profile whose capabilities include them, which the API publishes only with
  `AGENTICORG_UCP_CHECKOUT_ENABLED` on; requests go to the profile's same-origin `/ucp` REST
  endpoint. Nothing changes for a server without that variable.
- `complete_checkout` needs an allow decision: `decide_only: true` returns a sealed
  `session_complete` decision without executing, and only a call carrying its `decision_id` can
  execute (consumed once by the API, bound to the payload hash). A call with neither is refused
  before any request. Tool annotations mark reads read-only, updates and cancels destructive and
  idempotent, and Complete destructive and not idempotent.
- HTTP errors become MCP tool errors with only the UCP `{code, content}`; decisions in results keep
  only their id, action, outcome, failing rule, payload hash and expiry. The merchant UCP profile
  does not advertise an MCP transport, since this server is a local stdio adapter. No API change.

## Unreleased - UCP checkout, cart and order (G21, G22, G25, G27, G29-G32, G35)

- The UCP publishing ledger actor for an API key is the authenticated subject (`apikey:<prefix>`),
  never the key's name, and the `X-API-Key` header is read through a named constant; no credential
  reaches a ledger hash.
- Tests for the UCP and ACP HTTP adapters and their production wiring (unknown host refusals and
  their ledger events on every route, `Request-Id` echo, Shopify credentials failing closed to no
  Shopify reads, policies only from a usable verified artifact, the feed export formats and its
  404/409 answers, error shaping of non-JSON `/acp` errors), the buyer authority and AP2 mandate
  parsing, the Shopify Admin reads (allowlist, no redirects, GraphQL errors, delivery-profile
  cache), the order webhook sender (signing, transport failures, the Celery kick and tasks), and,
  on Postgres, the serving-host index, merchant scope reads, `Idempotency-Key` expiry and pruning,
  the subscription, rotation, delivery and replay runtime endpoints and dead-lettering. No
  behaviour change.
- Type narrowing in the UCP and ACP modules (locations, merchant terms, instruments, fulfilment,
  ACP payment data and policy snapshot) so `mypy` 2.x passes; no behaviour change.
- UCP REST binding at `https://<merchant serving host>/ucp` (`api/v1/commerce_ucp.py`), behind
  `AGENTICORG_UCP_CHECKOUT_ENABLED` (default off: every `/ucp` route answers 404). Checkout
  sessions (`POST /ucp/checkout-sessions` 201, `GET`, `PUT` full replacement, `/complete`,
  `/cancel`), carts (`POST /ucp/carts` 201, `GET`, `PUT`, `/cancel`) and `GET /ucp/orders/{id}`,
  for UCP `2026-04-08` (default) and `2026-08-25` (`AGENTICORG_UCP_VERSIONS`). The merchant is
  resolved from `Host` through the attested serving-host index of the profile work (published
  merchants only) and must belong to the tenant of the platform's API key (`X-API-Key` or
  bearer; scopes `ucp:read` / `ucp:write`). The transaction capabilities and payment handlers
  are advertised in `/.well-known/ucp` while the flag is on, and 2026-08-25 responses carry
  `policies[]` from the verified policy artifact. Docs: `docs/oacp/ucp-checkout.md`.
- `UCP-Agent` is parsed as an RFC 8941 dictionary, the platform profile fetched over public HTTPS
  without redirects (cached 60 s to 1 h), its version checked (`version_unsupported`) and
  capabilities intersected with orphaned extensions pruned; responses echo the version and the
  relevant active capabilities. The UCP status machine, the totals rule (one `subtotal`, one
  `total`, signed minor units), business outcomes as 200 with `messages[]`, and
  `Idempotency-Key` (same body replays, different body 409, storage failure 503) are enforced.
- Complete runs the commerce arbiter (action `session_complete`) on the session payload hash and
  executes only through a registered executor (`core/commerce/executors/`) that receives a
  decision consumed by `require_allow_decision`. No executor is registered in this release:
  Complete answers `executor_unavailable` and the session stays `ready_for_complete`.
  `ai.agenticorg.commerce.decide_only` issues a decision without executing; `decision_id`
  presents one. Two concurrent Completes execute once.
- Carts: one-call conversion with `cart_id` (an open session for the cart is returned),
  `cart.id` omitted on update (2026-08-25), `continue_url` as a UCP permalink on the merchant's
  `permalink_endpoint`.
- Orders: one per completed session, `GET /ucp/orders/{id}` for the originating platform, and
  full-snapshot order webhooks to the platform's `config.webhook_url` and merchant subscriptions,
  RFC 9421 signed (ES256 through an injected merchant key signer; nothing is sent without a key),
  with `Webhook-Id`/`Webhook-Timestamp` stable across retries, an outbox, retries with backoff
  and dead letters. Merchant runtime endpoints under `/api/v1/commerce/runtime/ucp` manage
  subscriptions (including an `acp_hmac` variant using the case push HMAC scheme), deliveries and
  order events. `register_order_event_listener` and `register_payload_renderer` are the hooks for
  the ACP order event mapping.
- Quantities are integer step counts with `quantity_unit` (`unit`, `scale` <= 15, `increment`),
  bounded at 2^53-1, exact integer amounts, visible conversion or recoverable rejection of a
  mismatched unit (G29). Fulfillment: shipping and pickup destinations, options from the
  merchant's Shopify delivery profiles, multi-destination and method combinations per release
  (G30). Payment: PAN, network token and handler token credentials (never stored or echoed),
  `ucp.request_constraints` emitted and enforced, payment authentication actions, split payments
  and payment terms (2026-08-25), token binding, AP2 merchant authorization when a merchant key
  exists (G31). Buyer consent as the `dev.ucp.consent.*` purpose map, from session-provided
  consent marked `source: platform` (G32). Loyalty from Shopify customer metafields and
  attribution on the session (G25).
- Payment handlers (`core/commerce/payment_handlers/`): a Plural tokenised-instrument handler whose
  config carries the merchant's `mandate_capability` (rails, cap, instrument types) and the
  allowance (`max_amount` = the cap); it accepts tokens, never a PAN, and executes nothing.
  Risk signals sent with Complete are recorded on the ledger (keys and SHA-256) (G35).
- Every UCP write records its ledger event; `/ucp/` joins the commerce ledger route prefixes and
  `ucp` is an enforced route-scope family.
- Migration `v6z89_commerce_sessions_orders` (after `v6z88_commerce_ledger`):
  `commerce_checkout_sessions`, `commerce_carts`, `commerce_orders`,
  `commerce_order_webhook_subscriptions`, `commerce_order_webhook_deliveries` and
  `commerce_ucp_idempotency`, each with ENABLE + FORCE row-level security and a tenant policy
  with `WITH CHECK`; forward-only (downgrade keeps the data).

## Unreleased - Fixed: commerce ledger details keep digests intact (G41)

- `record_commerce_event` masked PII in `details` with patterns (a ten-digit phone number, an
  8 to 18 digit account number) that also matched digit runs inside hex SHA-256 values and
  `arbdec_<hex>` decision ids, so about one mandate hash in five, and some decision ids, were
  sealed as `...******...` and could not be matched to the mandate or decision they name. Hex
  digests (optionally `sha256:`) and opaque `<prefix>_<hex>` / `<prefix>-<hex>` ids are now kept;
  every other string is masked as before. Rows already sealed are unchanged (append-only).
  Regression: `tests/regression/test_ledger_digest_masking_20261020.py`.

## Unreleased - Commerce ledger routes, offline export and Grantex countersignature (G14-G16)

- Tests for the retention task, the Grantex service client (size limit, non-JSON and non-object
  answers, unsafe endpoint, no retry, one JWKS refresh) and the offline verifier's exit codes in
  process, so each new module meets the new-module coverage floor.
- Tests: the commerce ledger route-table checks walk FastAPI's lazily included routers
  (`tests/app_routes.py`), so they see every route on the pinned FastAPI 0.143 instead of none,
  and the arbiter RLS probe is granted USAGE on the `public` schema, which other integration
  suites recreate without the default grant.
- `scripts/verify_ledger.py` no longer prints the name of the `--audit-secret-env` variable
  when it is unset, and the operator reference on ledger operator decisions is derived from
  the tenant and the principal subject only (the API key name is never read; API keys already
  carry `apikey:<prefix>` as their subject).
- **Breaking for strict runtimes: caller-asserted buyer authority is refused.** The commerce
  arbiter has a new rule, `buyer_authority_verified` (policy `commerce-arbiter-v2`): mandate,
  consent and passport evidence must carry a server-side verification ref, which only Phase 4
  mandates will provide. With `AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY` unset, production,
  staging, preview and unrecognised runtimes refuse every purchase handoff, POS handoff and
  mandate request with `failing_rule: buyer_authority_verified`; local, dev, test and ci
  runtimes pass the rule and record `buyer_authority_source: caller_asserted` in the decision.
  Opt-out: `AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY=false` (the decision still records
  `caller_asserted`). Docs: `docs/oacp/arbiter-ledger.md#rules`.
- **Breaking for strict runtimes: an allow needs a Grantex countersignature to be consumed.**
  After each arbiter decision commits, AgenticOrg posts it to Grantex `POST
  /v1/commerce/oacp/arbiter-decisions` (contract body: `failing_rule` rules, six evidence keys,
  the decision time) and, for an allow, verifies the returned `decision+jwt` against the pinned
  Grantex JWKS (`approver_auth == policy_arbiter`, `action_hash == payload_hash`,
  `decision_request == decision_id`) and attaches its `jti` to the decision. With
  `AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT` unset, strict runtimes consume only countersigned
  allows; an allow without one is refused `grantex_grant_missing` at once (no waiting, no retry;
  the caller asks for a new decision). Purchase prepare and POS handoff responses carry
  `grantex_countersignature`. Posting follows `AGENTICORG_ARBITER_GRANTEX_POST_ENABLED` (unset:
  the grant flag). Opt-out: `AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT=false`.
- Ledger routes under `/api/v1/commerce/runtime/ledger` (tenant admin): ledger events, arbiter
  decisions, operator decisions bound to an arbiter decision (annotation only: they never change
  an outcome or execute anything), review manifests, retention dispositions, what retention would
  delete, and an export bundle. Each write route records its declared ledger event.
- Offline-verifiable export (`GET /api/v1/commerce/runtime/ledger/export`) and
  `scripts/verify_ledger.py <export.json>`: recomputes the chain with no database and no network
  and reports the first break (`sequence_gap`, `previous_link`, `link_hash`, `truncated`,
  `anchor_mismatch`, `witness_countersignature`); exit 1 on a break. Row signatures are HMAC, so
  they are checked offline only with `--audit-secret-env`. The chain link arithmetic moved to
  `core/governance/audit_chain_link.py` (no dependencies) so the verifier loads it by path.
- Retention: a daily task on the shared Celery app (`apply-oacp-retention`, behind
  `AGENTICORG_OACP_RETENTION_APPLY_ENABLED`, default off) deletes review manifests whose
  disposition is due, through `oacp_delete_due_manifest` (SECURITY DEFINER, tenant-bound) and the
  manifest trigger's one DELETE transition, which re-checks every due condition; each deletion is
  ledgered.
- Witness checkpoints: with `AGENTICORG_AUDIT_WITNESS_ENABLED` (default off) each sealed chain
  head is posted to Grantex `POST /v1/audit/witness-checkpoints`; the verified countersignature is
  stored in `audit_chain_witness_checkpoints`, and a `409 WITNESS_FORK` is logged as an
  irregularity with its fork evidence stored.
- Migration `v6z88_commerce_ledger` (unreleased, extended): `audit_chain_witness_checkpoints`
  (forced RLS, append-only), `oacp_manifest_retention_guard` and `oacp_delete_due_manifest`.
- Least-privilege grants in `v6z88_commerce_ledger` for the NOBYPASSRLS application role
  `agenticorg_app` (applied only where the role exists): `oacp_delete_due_manifest` is revoked
  from PUBLIC and executable by `agenticorg_app` alone; UPDATE and DELETE are revoked on
  `audit_chain_witness_checkpoints` and the three `oacp_*` decision record tables; on
  `commerce_arbiter_decisions` the role keeps only `UPDATE (consumed_at, grantex_grant_jti,
  grantex_recorded_at)` for the two one-time transitions. Same rules as
  `scripts/create_app_role.sql`.

## Unreleased - UCP business profile, catalog and locations (G17, G18, G19, G20, G23, G26, G28)

- `GET /.well-known/ucp` (and `/.well-known/ucp/{version}` leaf profiles) on a merchant's serving
  host, behind `AGENTICORG_UCP_PROFILE_ENABLED` (default off; every route answers 404 while off).
  The merchant is chosen by the `Host` header only; the host must carry a DNS TXT attestation, the
  merchant must have opted in (operator publish) and hold an active signing key, and a verified,
  fresh `public_discovery_state` artifact must allow publication for that host. UCP 2026-04-08 by
  default; `AGENTICORG_UCP_VERSIONS=2026-04-08,2026-08-25` serves 2026-08-25 at `/.well-known/ucp`
  with the 2026-04-08 leaf in `ucp.supported_versions`. `Cache-Control: public, max-age=300`, ETag,
  no redirects. Capabilities are only the ones served, from a registry later surfaces register into.
- `POST /ucp/catalog/search` and `/ucp/catalog/lookup` (`dev.ucp.shopping.catalog.*`) in the UCP
  item schema of the requested release, served only from verified (ES256), fresh OACP catalog,
  price and inventory artifacts; otherwise `catalog_unavailable`. 2026-08-25 responses carry
  `policies[]` mapped from the verified OACP `policy` artifact (`core/commerce/ucp/policies.py`).
- `POST /ucp/locations/search` and `/ucp/locations/lookup` (`dev.ucp.common.location.*`, 2026-08-25)
  from POS stores; stores accept an optional `location` block (timezone, address, geo, regular and
  exception hours), validated on save and evaluated in the store's timezone across DST.
- Per-merchant ES256 signing keys (`core/commerce/merchant_keys.py`): generated at onboarding
  while the profile flag is on, or by `POST .../merchant-configs/{id}/ucp/keys`; private key
  encrypted for the tenant; rotation (`.../ucp/keys/rotate`) keeps the old key published for
  `AGENTICORG_UCP_KEY_ROTATION_GRACE_DAYS` (default 7). Profiles publish `signing_keys[]` +
  `keys[]` (2026-04-08) or `keys[]` (2026-08-25).
- Serving-host attestation and namespace authority binding: `POST .../ucp/serving-host`,
  `.../serving-host/verify` (async DNS), `.../publish` (namespace bound to the attested host; vendor
  entries outside it refused), `.../unpublish`, `GET .../ucp`. A published merchant's Grantex
  authority request carries `public_discovery_requested`, `serving_host` and
  `namespace_attestation_ref`; an enabled `public_discovery_state` artifact is accepted into the
  cache only with the profile flag on.
- **Changed: the `ucp_style_capability_profile` protocol-adapter surface is removed.** Its free-text
  capability list is replaced by `ucp_business_profile`, the spec-shaped profile, generated only for
  a published merchant (`unavailable_surfaces` says why otherwise). Clients reading the old surface
  read `ucp_business_profile` instead.
- Merchant-config upserts carry the server-managed UCP state over unchanged and never accept it from
  the request; responses omit the signing-key ciphertext. No migration.

## Unreleased - Commerce ledger and arbiter (G9-G13)

- **Breaking: `live_execution_approved` is ignored.** `POST /api/v1/commerce/runtime/purchase/prepare`
  accepts the body boolean for one more release, logs a deprecation warning and ignores it
  (`deprecated_fields_ignored` in the response). Live provider execution now needs
  `decision_id`: call once with `buyer_authority` to get an `allow` arbiter decision, then
  repeat the identical request with that `decision_id` within 10 minutes (single use), with
  `PLURAL_PINE_LIVE_EXECUTION_ENABLED=true`. Migration path: send `buyer_authority`, read
  `arbiter_decision.decision_id`, resend with it.
- **Breaking: POS handoff packets need an arbiter decision.** `POST
  /api/v1/commerce/runtime/pos/offline/handoffs` without `decision_id` now returns the decision
  (`status: decision_issued`, or `blocked` with `arbiter_refused`) and stores no packet; the
  packet is stored when the same request is repeated with the `allow` `decision_id`. Migration
  path: two calls, as above. There is no opt-out: a handoff without an `allow` decision is the
  behaviour this release removes.
- Commerce arbiter (`core/commerce/arbiter.py`): deterministic `decide(action, context)` over
  mandate, consent, Commerce Passport cap, verified fresh unrevoked OACP artifacts and merchant
  limits (`provider_policy.arbiter` in the merchant config), first failure wins, missing input
  refuses. Decisions are stored append-only in `commerce_arbiter_decisions` and sealed to the
  ledger; refusals are returned as blockers. No path waits for a person; the merchant option
  `escalate_to_human` (default false) copies a refusal into `hitl_queue` for review without
  changing it.
- Commerce ledger (`core/commerce/ledger.py`): every commerce write route and A2A
  `message:send` writes a signed `audit_log` row (`resource_type = commerce_ledger`, RFC 8785
  `payload_hash`) in the same transaction as the write. `route_meta(audit_event=...)` is now
  enforced on commerce write routes: a route that returns without its event answers 500
  `commerce_ledger_event_missing` (logged only with `AGENTICORG_ROUTE_ENFORCEMENT_MODE=log`).
- OACP operator decision, review manifest and retention disposition records are append-only
  (trigger) and mirrored into the ledger; a repeated record id is refused
  `record_append_only`, a non-UUID tenant `ledger_tenant_invalid`.
- `AGENTICORG_AUDIT_CHAIN_DEFAULT_ON` (default off) seals the audit chain of every tenant with a
  commerce merchant config while `AGENTICORG_AUDIT_CHAIN_ENABLED` is off.
- Migration `v6z88_commerce_ledger` (after `v6z87_platform_ctx_policies`):
  `commerce_arbiter_decisions` under forced RLS, append-only triggers, and the `audit_log`
  append-only trigger reinstalled (an empty-schema bootstrap was stamped past the baseline that
  created it). Docs: `docs/oacp/arbiter-ledger.md`.
- The Grantex replay fetch and the OACP trust endpoint fetch validate their URLs with the
  async egress validator (`tests/regression/test_egress_async_callers.py`), so no async
  commerce path calls the synchronous validator.

## Unreleased - Hindi reference-question privacy correction

- Keep unambiguous Hindi reference-question grammar, action conjunctions and
  confirmation verbs readable when the English NLP recognizer mislabels them
  as people or organizations. Do not exempt named values, unknown words,
  custom recognizers or spans crossing the grammatical boundary.
- Apply the same narrow guard to pre-model redaction and entity lookup used by
  pseudonymization behind `AGENTICORG_PII_HINDI_GRAMMAR_ENABLED`, default off.
  Enable only after privacy regression acceptance; disabling restores previous
  detection behavior. Existing flags, redaction modes, genuine identifier
  recognizers and budgets are unchanged; this is not general Hindi NER coverage.
- Replay the entire seed and follow-up through the packaged privacy detector,
  alongside name, quoted-name, email and identifier protection regressions.

## Unreleased - Database sessions ready for a NOBYPASSRLS application role

- Bind tenant or platform context at the raw-session sites that still opened
  `async_session_factory()` without one, so the API and workers can run as a
  `NOSUPERUSER NOBYPASSRLS` role (FINDINGS A-105). Tenant work uses
  `get_tenant_session` / the new `bind_tenant_context`; pre-auth lookups
  (signup, login, password reset, API-key prefix, Grantex agent DID, SSO and
  branding discovery, health probes) use the new `get_platform_session`.
- Migration `v6z87_platform_ctx_policies`: one permissive `SELECT`-only
  policy on `sso_configs`, `tenant_branding` and `agents` that honours
  `agenticorg.platform_ctx = '1'`. Tenant policies are unchanged; no platform
  write policy exists. It also (re)installs the `audit_log_immutable` trigger
  with the sealing transition (the same text as
  `core.governance.audit_chain.SEAL_TRIGGER_SQL`). Databases bootstrapped
  from an empty schema previously had no trigger outside the local-only
  startup DDL. Downgrade leaves the trigger installed.
- Report schedule claims run per tenant (the RPA dispatcher's pattern), the
  synthetic-check sweep no longer reads `synthetic_checks` across tenants, the
  A/B variant helpers require `tenant_id`, CDC event-store writes and lists
  require and bind the event's tenant, and audit entries bind their tenant.
  The cross-tenant spend CLI raises under a non-bypass role instead of
  reporting zero.
- Startup logs the database role's RLS posture (`DB_ROLE_BYPASSES_RLS`
  warning for a superuser or `BYPASSRLS` role). `AGENTICORG_REQUIRE_NOBYPASSRLS_ROLE=1`
  (default off) refuses such a role; `AGENTICORG_SKIP_DB_ROLE_RLS_CHECK=1`
  skips the probe.
- Add `scripts/create_app_role.sql` and `docs/RLS_APP_ROLE.md`. Moving
  production `AGENTICORG_DB_URL` to the application role is an operator step
  documented there; this change does not perform it.
- `scripts/create_app_role.sql` grants least privilege: row DML on ordinary
  tables (never `TRUNCATE`, `REFERENCES` or `TRIGGER`); `SELECT`/`INSERT`
  only on the append-only tables (`capability_evidence_records`,
  `capability_promotion_events`, `eval_dataset_versions`, and, once
  `v6z88_commerce_ledger` has run, the OACP decision records and
  `commerce_arbiter_decisions`, the latter with `UPDATE` of only
  `consumed_at`, `grantex_grant_jti` and `grantex_recorded_at`; every table
  guarded by a known append-only trigger function is classified by rule, so
  the script runs on any head); on `audit_log`
  `SELECT`/`INSERT` plus `UPDATE` of the four hash-chain columns the sealing
  task fills; no `DELETE` on `capability_readiness_records` or
  `audit_chain_anchors`; read-only `alembic_version` and
  `checkpoint_migrations`; no access to `alembic_migration_progress`; and
  `EXECUTE` only on the allow-listed restricted functions
  (`public.gemini_daily_platform_spend()`, and
  `public.oacp_delete_due_manifest(text, text)` where it exists) (default privileges grant no function `EXECUTE`). The
  script runs in one transaction, re-running it converges on these grants,
  and it refuses and rolls back a broader result. Guarded by
  `tests/regression/test_app_role_grants.py` and new cases in the Postgres
  proof.
- Egress URL validation inside async code uses `validate_public_url_async`
  (non-blocking DNS) for connectors, OIDC, JWKS, AI-provider probes and base
  URLs, commerce runtime calls and the lineage feed fetch.
- Regression guards: `tests/regression/test_raw_session_inventory.py`
  (every remaining raw session is justified), `tests/regression/test_egress_async_callers.py`,
  and the Postgres proof `tests/integration/test_rls_nobypass_role.py`.
- The lead delivery Postgres tests no longer patch the removed
  `api.v1.demo.async_session_factory` (the receipt writes through
  `get_platform_session`), and the worker-outcome test seeds the marketing
  tenant itself instead of relying on test order. A regression check keeps
  `api.v1.demo` on the shared session helpers.
- The OACP Grantex replay fetch and trust-endpoint fetch (merged from main)
  validate their URLs with `validate_public_url_async`, as
  `tests/regression/test_egress_async_callers.py` requires for code inside
  `async def`; the policy (scheme and host checks, DNS left to the pinned
  transport) is unchanged.

## Unreleased - OACP artifact verification before caching (G5)

- **Breaking for strict runtimes.** `POST /api/v1/commerce/runtime/artifacts/cache`
  now verifies every Grantex OACP artifact before caching it: ES256 detached JWS
  (protected header `oacp-artifact+jws`, RFC 8785 signing input of the envelope
  without `signature`) against the Grantex JWKS pinned in configuration, payload
  hash, issuer, scope, TTL, and the Token Status List entry the envelope names.
  With `AGENTICORG_OACP_VERIFY_STRICT` on (the default in production, staging,
  preview and unrecognised environments) one failure returns `422
  artifact_verification_refused` and nothing from that issuance is cached.
  Grantex artifacts signed with the legacy template string are refused until the
  Grantex OACP signing release is deployed. Opt-out: set
  `AGENTICORG_OACP_VERIFY_STRICT=false`; verification still runs, failures are
  logged as `oacp_artifact_verification_irregularity` and those artifacts are
  cached without a `verifier_result_ref`. Local, dev, test and ci runtimes
  default to that log-only mode.
- Stop stamping `verifier_result_ref = "<artifact_id>:verified"` on every cached
  artifact and stop accepting a caller-supplied `verifier_result_ref`. Verified
  records carry `oacp-artifact-jws-v1;kid=…;payload_hash=…;status=valid;verified_at=…`.
- The Grantex JWKS is fetched asynchronously over the DNS-pinning egress
  transport (5 s timeout, 300 s cache, 60 s negative cache, one refresh on an
  unknown kid). Status lists are fetched only from the issuer or Grantex API
  origin over https, verified as `statuslist+jwt`, cached within their `ttl`;
  revoked and suspended entries and unreachable lists refuse the artifact.
- Cache rows stamped `<artifact_id>:verified` before this change, and rows cached
  in log-only mode, no longer count as verified on any runtime read (buyer and
  bridge answers, protocol adapters, purchase and POS preparation). Strict
  runtimes refuse them (`artifact_unverified_legacy`, `artifact_unverified`);
  log-only runtimes log `oacp_cache_record_unverified_irregularity`. New
  `POST /api/v1/commerce/runtime/artifacts/refresh` re-fetches a scope from
  Grantex replay and caches it verified. Deploy order: the Grantex signing
  release, a first OACP issuer key and `OACP_STATUS_LIST_PUBLIC_ENABLED` must be
  live before strict verification reaches production (or set the opt-out).
- Match Grantex's OACP issuance: `revocation` artifacts may live 24 h (was 0),
  and `issued_at` / `not_before` tolerate 60 s of clock skew (expiry does not).
  A status list Grantex has not made public (404) refuses the artifact.
- `verify_oacp_artifact` follows the cross-repo verification order (signature
  checks first) and takes a JWKS instead of caller-supplied key metadata and a
  stub signature callback. The unwired in-memory `OacpArtifactCache` class and
  its key-metadata and revocation-snapshot types are removed.
- Shared vectors `tests/fixtures/oacp/oacp-artifact-jws-v1.json` (synthetic key)
  and a conformance test against a running Grantex
  (`tests/integration/test_oacp_grantex_conformance.py`, skipped unless
  `AGENTICORG_GRANTEX_CONFORMANCE_URL` is set). No migration.
- `docs/oacp/explainers/launch-rollback-runbook.md` lists
  `AGENTICORG_OACP_VERIFY_STRICT` with its default, the launch order and the
  rollback step.
- `scripts/oacp_runtime_launch_check.py` no longer writes
  `artifact_verifier_summary.valid = <artifact count>` for unsigned local
  artifacts. It signs them with an ES256 key generated for the run (kid
  `oacp-launchcheck-<random>`, never stored), runs each through
  `OacpGrantexArtifactVerifier` against that key's JWKS and an in-process VALID
  status list, and reports `checked` / `valid` / `invalid` / `status_list_checked`
  / `refusal_codes` from the verifier results, labelled
  `verification_mode: local_throwaway_es256_key` and `grantex_issuer: false`.
  Cache records carry the verifier's `verifier_result_ref`
  (`cache_records_verified_count`); any refusal exits non-zero. The tracked
  local evidence is regenerated.

## Unreleased - Unicode input and private Telegram follow-ups

- Preserve Unicode in model-facing JSON before privacy scanning, including
  base-agent reasoning and synthesis. Do not disable recognizers or redaction;
  keep literal user escape sequences as literal data.
- In the existing explicit Telegram answer-mode opt-in, tool-free agents may
  use four recent confirmed question/answer pairs, bounded to 30 minutes and
  the same tenant, binding, linked user/delegation and agent/company. Keep
  context text-only, tenant-encrypted and subject to existing privacy/budget
  controls; exclude unsafe, legacy, stale and unconfirmed replies.
- Exclude traffic-split targets from context and recheck the server-owned
  answer-only scope after target resolution, before model dispatch. Preserve
  existing channel routing correlation independently of execution checkpoints.
- Retain fresh server-owned execution checkpoints and serialize follow-up
  intake against acknowledgement stamping. No schema, feature-flag, credential,
  webhook, model-default or execution-control changes.

## Unreleased - Configurable Telegram answer replies

- Require patched libx11 in both UI runtime images after the container scan
  reported CVE-2026-88806. Keep scanner policy and production controls unchanged.
- Run synchronous LangGraph regression helpers through the loop-safe database
  bridge and assert zero shared-pool guard trips; keep the debt baseline unchanged.
- Preserve the authenticated binding-scoped sender hash inside encrypted intake
  for final-answer authorization across API/worker service keys. Keep live sender
  revocation and tenant/owner/agent checks; withhold older receipts without this
  proof. No key, feature-flag, budget, webhook or credential changes required.
- Add per-connection opt-in final-answer replies for linked private Telegram
  agent chats. Existing connections and queued messages stay status-only.
  No global feature flag or deployment-control default changes.
- Recheck sender delegation, current tenant/owner and agent access before
  disclosure. Keep approvals, errors, traces, tool-call records and recognized
  secret-bearing outputs out of answer replies. Bound plain-text replies and
  retain one-attempt delivery without replaying model execution on send failure.
- Let channel owners edit an authorized destination through Channels without
  replacing credentials or webhook settings; reject changes while work is queued.
- Document disclosure limits, opt-out, provider-account verification and safe
  acceptance/restoration steps. Migration adds status-default consent snapshots.

## Unreleased - Gemini reasoning request correction

- Add per-agent opt-in medium thinking for `gemini-3.5-flash-lite`, saved in the
  existing LLM configuration and editable during agent creation or configuration.
  Existing agents and generic calls retain the provider default. Both runtime
  clients honor explicit profiles; omit this model's ignored sampling temperature.
  The provider's minimal default
  returned incorrect arithmetic for the exact wrapped acceptance task. Keep the
  task, prompt, model, credentials, feature flags and execution controls unchanged.
  Validate model/profile compatibility, retain the profile on approval/debug
  resume, and distinguish it in replay keys. No migration or global opt-in.
  Carry saved opt-ins through workflow agent loading, legacy sales and the voice
  text bridge without changing unconfigured paths or enabling channels.
- Include thinking tokens in native router totals and legacy cost estimates,
  without double counting the optional detailed spend meter. Medium thinking can
  increase output-token cost and latency; existing limits still apply. Older
  Gemini models retain their supported sampling settings. This is not an answer
  guarantee or a task-specific arithmetic patch.
- Add real SDK request-serialization, tenant credential, budget and exact-input
  regressions. Real-provider Docker and production results must be recorded
  independently of scripted CI responses before closing issue #1715.

## Unreleased - Documentation learning path

- Restore the first-agent guide's next step to workspace and role setup. Keep
  deployment-security guidance with the other governance guides without removing
  its page, search entry or canonical URL. Add a rendered-navigation regression.

## Unreleased - Isolated run authorization

- Add opt-in `AGENTICORG_GRANTS_ISOLATE_RUN_TOKENS` (default off). When on,
  authorization resolution forces deny mode and obtains separate short-lived
  delegated grants for separate server-created run authorization contexts.
  Refresh preserves that isolation key; legacy static agent tokens do not
  substitute for a fresh delegated run grant. External caller authority remains
  an additional restriction. This is not cryptographic per-case purpose binding
  or a network-egress control.
- Apply the isolation minimum before A2A/MCP type routes choose their legacy
  compatibility path, so valid registered agents are selected before minting.

## Unreleased - Checkpoint store library 3.2.0

- `langgraph-checkpoint-postgres` moves from 3.1.3 to 3.2.0 (exact pin; `langgraph-checkpoint`
  stays 4.3.0). The encrypted checkpoint saver was reverified against 3.2.0: the release only
  adds `prune`/`aprune`, which AgenticOrg does not call; `aput`, the blob and write dump paths,
  checkpoint loading and the upstream schema migration list are unchanged, so no new migration
  is needed. The saver now refuses 3.1.3 (`checkpoint_library_unverified`), as it does any
  version other than the verified one.

## Dependency Compatibility (2026-10-17)

- Updated the provider, graph runtime, UI components and development tools together.
  The provider integrations require `langchain-core>=1.6.10`; installing them
  independently with the previous requirements pin could fail dependency resolution.
- Reverified the encrypted checkpoint saver against `langgraph-checkpoint==4.3.0`
  and `langgraph-checkpoint-postgres==3.1.3`. Exact-version refusal, encrypted
  storage, thread binding and existing configuration defaults remain unchanged.
  The upstream schema migration list is unchanged; no new migration is needed.

## Unreleased - Direct-run explanation evidence

- Add opt-in `AGENTICORG_AGENT_EXPLANATION_AUDIT_ENABLED` (default off) so the
  explanation panel can read direct-run audit evidence rather than reporting
  that an agent has never run. Preserve tenant/personal visibility and select
  the newest recorded run. Do not invent missing tool usage or review decisions.
- Preserve this opt-in during Cloud Run releases unless explicitly selected with
  `DEPLOY_AGENT_EXPLANATION_AUDIT=true|false`; only the API uses this read option.
- Remove unconditional free/credential-free Gemini claims and stale routing-price
  labels. Document structured confidence and total spending allowances including
  explanation requests and provider transport retries.

## Unreleased - Knowledge upload recovery and tool-free agents

- Refuse direct runs and chat requests against soft-deleted agents, consistent
  with existing workflow/routing rules; paused-agent policy remains configurable.
- List uploaded documents once rather than also listing their native chunks;
  apply the caller's domain visibility to legacy native document listings.
- Apply the same domain boundary to upload, filename collisions, replacement and
  deletion, before any document write or remote-index deletion.

- Preserve short extracted documents, neighbouring spans and trailing text in
  sentence chunks. Report the actual multilingual embedding model and dimension.
- Refuse malformed/password-protected PDF and malformed Office packages with a
  controlled extraction error rather than an internal server error.
- Retain the previous searchable document until replacement indexing succeeds;
  retire its metadata and native chunks together. Prefer the current indexed
  revision over abandoned failed attempts when selecting a replacement.
- Add opt-in `AGENTICORG_KNOWLEDGE_UPLOAD_RECOVERY_ENABLED` (default off): bounded
  retries of transient native embedding failures on the existing maintenance
  worker. Pending uploads are not reported as indexed; the document list refreshes
  while processing. No raw files, new tables or new cloud services are introduced.
- Allow explicit tool-free agent creation with `use_default_tools=false` and no
  selected tools/connectors. Omitted options preserve suggested-tool defaults.
  The creation wizard accepts zero retries; tenant budgets and action controls remain.

## Unreleased - Tenant-scoped verification plugins

- Allow verification plugins to declare a `for_tenant(UUID)` constructor.
  Governed investigations, screening and authenticated provider-event handling
  pass their server-resolved tenant to it. Missing context refuses construction
  without falling back to shared credentials; existing zero-argument providers
  and injected runtime factories retain their behavior.
- Plugin loading and governed cases remain default-off. No provider credentials,
  production policies or verification capabilities are enabled by this change.

## Unreleased - Coordinated dependency maintenance

- Refresh PDF extraction, validation, vector storage, DNS, Google authentication
  and model integration dependencies together with the UI controls, translations
  and test tooling. Reconcile overlapping UI lockfile updates as one graph.
- Keep API contracts, migrations, provider execution, public discovery, budgets,
  tenant isolation and marketing delivery settings unchanged.
- Keep the multiprocess metrics probe's sample files on native temporary
  storage rather than a Docker Desktop bind mount; retain its aggregation,
  dead-worker cleanup and process-state restoration checks.

## Unreleased - Shared marketing SMTP intake

- Add default-off Grantex website enquiry support over the existing encrypted
  delivery queue and authenticated SMTP mailbox. Keep notification and requester
  recipients separate, and skip AI sales execution for Grantex enquiries.
- Limit Grantex cross-origin access to uncredentialed public enquiry POSTs;
  tenant API CORS, rate limits, model budgets and isolation are unchanged.

## Unreleased - Coordinated dependency and local-runner refresh

- Refresh the backend/UI dependency batch, pinned runtime/tool images and CodeQL
  action. Align LangChain core and OpenTelemetry pins with their required release
  families so both installation manifests can resolve without bypassing audits.
- Match both Docker browser runners to the locked Playwright version and add a
  parity regression. Keep the metrics aggregation probe explicitly on Celery's
  prefork model instead of inheriting Python's process-start default.
- No provider, payment, public-discovery, budget or tenant-isolation default is
  enabled by this dependency release.
- Exclude the optional proprietary-APM dependency bump under the existing
  open-source-only policy; its previous declared baseline is unchanged.
- Bound GPU-pool and provider-billing-zone JSON parsing independently of the
  interpreter recursion limit. Oversized nesting is rejected consistently while
  spend intelligence is enabled; the feature remains off by default.
- Cover local grant refusal and the real signed-grant permission check separately
  in the optional local authorization-service integration suite.
- Exercise the prefork probe in the standard coverage process with an isolated
  metrics registry, while retaining its separate-process CLI regression.

## Unreleased - Verified enquiry delivery and protected spend accounting

- Behind the default-off durable flag, correct demo intake's premature email-sent and agent-triggered claims. Receipt
  responses report pending work and no guaranteed two-minute response; clients
  must use persisted worker outcomes and separate inbox evidence.
- Add encrypted, tenant-isolated demo delivery jobs and identifier-only worker
  dispatch/recovery behind `AGENTICORG_DEMO_DURABLE_DELIVERY_ENABLED` (default off).
  Failed/ambiguous SMTP actions are not automatically replayed.
  Pending publications use an atomic lease and expiring messages. Schema downgrade
  removes an empty outbox but refuses to discard encrypted history.
- Use Gmail LOGIN challenge/response, normalize only recognized Google app-password
  display spacing, and log safe SMTP error classes/codes. Keep validated TLS.
- Replace the platform Gemini spend query's RLS-off attempt with an aggregate-only
  restricted database capability. Keep tenant/platform caps and fail-closed lookups.
- Do not record failed/review-paused sales proposals as contact history or send
  generated mail to a recipient other than the saved lead.
- Add real Postgres isolation/replay regressions, SMTP negative cases and an
  operator runbook. Apply the migration before enabling durable worker delivery.

## Unreleased - Global evaluation journeys and enquiry privacy

- Add bank, fintech and merchant pilot paths to the public landing page with
  direct links to source-backed workflows and a visual rollout checklist.
- Expand the accounts-payable guide with source setup, extraction, review and
  external-confirmation steps, without treating prepared evidence as payment authority.
- Remove company/role fields from public enquiry analytics as a default-on privacy
  correction. Only bounded source/audience values are sent; the intake API retains
  the contact details needed to respond. Add timeout, duplicate-submission and
  accessible recovery behavior without changing API or notification recipients.
- Reserve the actual 1920-by-1080 screenshot dimensions before lazy loading to
  prevent collapsed media boxes and layout movement during the landing journey.

All notable changes to AgenticOrg are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

- Document safe recovery from duplicate Telegram webhook refusals by reusing the
  working connection. Add regression coverage for the existing refusal and
  guidance distinguishing real reasoning, message transport and combined
  channel-to-model acceptance without runtime or deployment-control changes.

### Documentation - existing-user commercialization policy
- Clarify that existing and new covered users require a commercialization
  agreement, with no account-age exemption. Self-use remains free of a software
  license fee; existing hosted customers transition through required contractual
  notice and acceptance. Prior Apache grants, release scope, billing and runtime
  are unchanged.

### Documentation - approved prospective licensing policy
- Add concrete candidate source-available license terms and an explicitly
  publication-approved, empty release-scope manifest for eligible future platform material.
  Preserve legacy grants and separate hosted/reseller/brand agreements.
- Publish the owner-approved self-use/commercial-agreement policy and quiet
  legal/footer links. No software release has adopted it; existing Apache-2.0 grants,
  contribution terms, package metadata, billing and runtime remain unchanged.

### Changed - breaking default for marketing notification recipients
- Website demo/trial notifications now default to `sanjeev@orchestrum.in`, the
  owner-approved marketing inbox, rather than `sanjeev@agenticorg.ai`.
- Self-hosters can opt out by setting `AGENTICORG_DEMO_NOTIFY_TO` to their own
  single valid address, including the previous destination if appropriate.
  An empty, multi-recipient or malformed override refuses notification rather
  than silently forwarding lead details to a fallback inbox.
- Requester confirmations, workspace emails, SMTP sender, credential custody,
  throttling and lead persistence are unchanged.

Entries here have accumulated since 4.0.0. The source version in `pyproject.toml`
is 4.8.0; versions 4.1 to 4.8 have no sections of their own.

### Fixed - durable workflow tenant context
- Durable workflow state and event-wait transactions now bind the validated
  tenant before accessing PostgreSQL. Run, approval-resume and cancellation
  retain server-owned scope. Existing RLS, grants and approval rules are
  unchanged; this repairs persistence under the existing isolation policy,
  rather than introducing an execution permission or a feature-default change.
- Docker regression tests use a non-superuser, non-RLS-bypass role with forced
  RLS and a reused connection, and cover real transform execution, fresh-worker
  reload, cross-tenant denial, event completion, timeout, version and replay.
- Legacy Redis backfill requires an explicit server tenant and matching run
  identity; an unscoped background worker cannot obtain authority from cache.

### Fixed - user documentation matches shipped behaviour
- A new guide, *Documents, conversations, calls and workbenches*, covers the six
  console screens of services that ship off by default, and Start here links
  them with the sidebar's own labels and the Cost Dashboard.
- Guides name the default-off settings behind chat context, the visual workflow
  builder, the Observability Checks tab and step-through debugger, the model
  gateway and its limits, A2A and MCP route scopes, residency enforcement and the
  audit chain. The self-hosting guide lists the feature switches that ship off
  and says strict runtimes make no schema changes at start-up.
- The approvals, troubleshooting, governed-case, knowledge, industry-pack, roles,
  guardrails, evaluation and security guides describe the multi-person approval
  rules, current refusal codes, the provider webhook address, citations and
  excerpts, the Banking Pack, invitation roles, the adversarial routes, the
  prompt-template panels, the Data Region setting and kept call recordings.
- The Connectors guide's diagram is a flow block; the site has no Mermaid
  renderer.
- The README, product status, llms.txt, SDK READMEs and governance overview say
  what AgenticOrg's Grantex call checks; the conversation supervisor is named as
  the internal feed's only writer; llms.txt points A2A discovery at the v1 Agent
  Card; the landing Observatory card describes the screen as it is; Node.js
  22.22 is the stated minimum.

### Fixed - breaking for agent runs whose model answers in content blocks: agent output parsing of provider content blocks
- A deliberate default: a model answer that arrives as a list of provider content blocks (for
  example one `{"type": "text", "text": "```json ...```", "extras":
  {"signature": ...}}` block) is now read for each block's text. The agent
  graph used to join the blocks with `str()`, so the fenced JSON never parsed
  and the run's output became `{"raw_output": "<block repr>"}`; it now parses
  to the model's object, and an answer that is not JSON keeps its readable
  text in `raw_output`. Reasoning, signature and tool-use blocks carry no
  answer text and are skipped, as is a signature-only text block with empty
  text; a refusal block (`{"type": "refusal", "refusal": ...}`) is read for
  its refusal text. The confidence length signal measures the answer text
  instead of the number of blocks.
- The SOP parser, the run explainer and the feedback analyser read block
  content the same way (they failed or fell back to heuristics on it).
  Plain string answers parse exactly as before. The shared helpers are in
  `core/llm/content.py`.
- Opt-out: `AGENTICORG_AGENT_OUTPUT_BLOCK_PARSING=false` (setting
  `agent_output_block_parsing`, default `true`, read by the API and the
  workers) restores the previous parsing in all four parsers: an agent run
  whose model answers in blocks again gets the `str()` of the blocks in
  `raw_output`, with the confidence length signal counting blocks, and the
  SOP parser, explainer and feedback analyser fail or fall back on block
  content as they did. With the opt-out and the guardrail hooks on, the
  reasoning blocks also reach `raw_output` in that `str()` as before, and the
  output rules do not evaluate them: the output stage evaluates the answer's
  text blocks only.
- Guardrails: with `guardrails_hooks_enabled` on, a block answer passes the
  output stage, grounding rules included, on its answer text, as a string
  answer does; it used to skip the stage and travel on unevaluated. Reasoning
  and signature blocks are never evaluated as the answer. A transform
  (redaction) replaces the answer's text blocks (a refusal block keeps its
  type) and keeps every other block (reasoning, signature-only text, tool use)
  as it was; a block rule refuses the run as for a string answer. The input
  stage and the grounding context read block content (a multimodal user turn,
  a tool result in blocks) the same way.
  This is not turned off by `agent_output_block_parsing=false`; it changes
  nothing while the hooks are off, and with them on an answer is changed only
  where a rule transforms or blocks it.

### Added - the bank-operated customer agent, behind `AGENTICORG_CUSTOMER_AGENT_ENABLED` (off by default)
- `POST /api/v1/customer-agent/sessions` exchanges a bank-signed customer
  assertion (RS256 or ES256, verified against the bank's JWKS, issuer and
  audience; single-use by its `jti`) for an opaque session token; the customer never holds a platform
  credential, and the token's hash alone is stored (`customer_sessions`).
  Session, acknowledgement of the AI disclosure, and end-of-session routes
  authenticate that token themselves; the auth middleware exempts the prefix.
- `POST /api/v1/customer-agent/turns` runs the conversational services under
  an autonomy ladder set per task and per customer: level 0 inform, 1
  recommend, 2 prepare and wait for one tap (the default), 3 execute after
  the bank's step-up of the specific action (a signed approval bound to the
  action id), 4 execute within the customer's mandate (caps, counterparties,
  window) with a step-up outside it. `AGENTICORG_CUSTOMER_AGENT_MAX_AUTONOMY`
  (2) bounds every level. Every answer and card carries a "why this"
  explanation: data used, rule applied, alternatives, what the bank earns,
  how to challenge it.
- Prepared actions (`customer_actions`) with `GET /actions`,
  `POST /actions/{id}/confirm` and `POST /actions/{id}/cancel`; a card whose
  conversation moved on is marked superseded and never run. Mandates
  (`customer_mandates`) through `GET /permissions`,
  `PUT /permissions/mandates` and `DELETE /permissions/mandates/{id}`; the
  permissions page also links the authority service's dashboard for outside
  agents when `AGENTICORG_CUSTOMER_AGENT_OUTSIDE_AGENTS` is on.
- `ui/public/customer-agent/widget.js`: a dependency-free embeddable element
  (`<agenticorg-customer-agent>`) for the bank's page, with the disclosure
  gate, one-tap cards, the "why this" panel and a host event for step-ups.
- Migration `v6z84_customer_agent` (three RLS tables). Integration guide:
  `docs/customer-agent/integration.md`.

### Added - Grant revocation for paused and retired agents; an opt-in revocation check before a token is honoured
- Pausing or retiring an agent revokes the run grants the pool minted for it
  at the authority service, evicts them from the shared cache and from every
  node's in-process cache, and reports the real result (`token_revoked`,
  `grants_revoked`, `grants_failed`) instead of a constant.
- The authentication middleware refuses a grant token of a retired agent (or
  a paused one while paused agents are refused). With
  `AGENTICORG_GRANTEX_REVOCATION_CHECK=feed` (off by default, so no existing
  authentication path changes until a deployment opts in) it also refuses a
  token whose grant, or whose parent grant, the authority service has
  revoked, read from the SDK's revocation feed. An unknown revocation state
  then refuses the token in a strict runtime and is allowed with a warning
  elsewhere.
- `POST /agents/{id}/delegate` answers 502 when no delegated grant was issued
  and keeps the grant it receives instead of discarding it; pausing or
  retiring the agent revokes that delegated grant with the rest.

### Fixed - Public status page crawlability
- Render status interpretation, an explicit unconfirmed loading state and
  support links in the initial HTML, even without JavaScript or a live report.
- Keep availability claims sourced from the status endpoint; static content
  does not embed healthy states or historical uptime figures.
- Add server-rendering, pending-report, failure and loaded-report regressions.
- Synchronize the linked BFSI overview and the Start here guide with the
  existing capability inventory; check public and domain summaries against that
  inventory instead of stale counts.

### Changed - configurable reasoning default
- Newly created agents and unset platform model defaults use
  `gemini-3.5-flash-lite`. Existing explicit models are not rewritten. This is a
  deliberate default/cost change: choose another provider/model in AI settings or
  set `AGENTICORG_LLM_PRIMARY` and `AGENTICORG_LLM_FALLBACK` to opt out.
- Added catalog and standard text pricing for the new model. Runner estimates
  account for input/output usage instead of applying the legacy blended rate to
  this model. Speech synthesis still requires a separate provider.

### Added - bring-your-own channel workflow intake
- Owner-scoped encrypted channel bindings and a three-step setup screen for
  WhatsApp, Messenger, Telegram, Viber, Mailgun inbound email and signed gateways.
- Signed, bounded text callbacks, tenant-scoped duplicate protection, one-time
  private sender linking, explicit email/unlinked-message review and durable
  worker dispatch through existing agent/workflow authorization and approvals.
- Forward migration `v6_z83_channel_bindings` adds RLS-protected bindings, sender
  links and receipts. Run migrations before starting the API/worker/beat release.
- Credentials rotate without disclosure; rotation and disable revoke delegation.
  Uncertain executions require review and are never blindly replayed. Optional
  native replies contain status only. Added an account creation/verification guide.
- No native WeChat adapter, channel attachment ingestion or automatic email
  identity delegation is claimed. Real provider delivery requires BYO accounts
  and authorized test contacts; local simulations are labeled separately.

### Security - breaking for audit readers of tenants that keep spend rows: `GET /audit` hides commercial spend audit rows by default
- A deliberate default, on whatever `spend_intelligence_enabled` says:
  `GET /audit` hides the audit rows of rate cards, commitments, invoices,
  reconciliations and spend jobs (`spend.rate_cards.*`, `spend.commitments.*`,
  `spend.invoices.*`, `spend.reconciliations.*`, `spend.job.*`,
  `spend.usage.*`, `spend.fx.*`, `spend.rollups.*`: contract prices,
  committed amounts, invoice figures, job parameters and the amounts jobs
  moved) from every reader who is not a human administrator or auditor, that
  is domain-scoped roles and machine credentials, the readers the spend
  routes refuse. Audit rows are never deleted, so the filter stays when spend
  intelligence is turned off. Only tenants that keep a rate card, commitment,
  invoice, reconciliation or spend job are affected; every other tenant's
  audit query is the one it ran before.
- Opt-out: `AGENTICORG_SPEND_AUDIT_COMMERCIAL_FILTER=false` (setting
  `spend_audit_commercial_filter`, default `true`, read by the API) restores
  the previous `GET /audit` behaviour for every reader, commercial rows
  included, with no extra query.

### Added - AI spend intelligence: invoice reconciliation and the Gate 1 status
- Provider invoices: `POST /spend/invoices/import` takes a provider's invoice or
  billing export for one billing month (CSV or JSON, bounded like every
  import) with line kinds (`usage`, `credit`, `tax`, `fee`, `commitment`),
  card units, quantities and optional daily billing dates. An invoice is
  stored whole or refused whole (422 `invoice_rejected` names every refused
  line); a second current invoice with the same reference needs `replace`,
  which supersedes the earlier one; several references for a month are
  summed, up to 20,000 current lines per provider and month (413
  `too_many_rows`), since a run loads them all, and up to the amounts and
  quantities a run can store of their sums (413 `period_amount_too_large`,
  `period_quantity_too_large`). Invoices are listed and read through
  `GET /spend/invoices`.
- Reconciliation: `POST /spend/reconciliations` compares a provider's
  tenant-billed usage in its own billing month with its current invoices in
  two figures, the amounts as stored and a re-pricing at the cards in force
  on each billing day as known at run time (fallback-priced usage keeps its
  stored price), each with its own month-level tier true-up per contract key
  (one ladder across card versions, graduated or all-units) and commitment
  overage at the commitment's price, worked from the cards and base price
  that figure priced with and converted through INR. A month whose figures
  would not fit a run is refused before anything is written (422
  `figure_out_of_range`). Lines and usage
  match on usage type, the canonical model (aliases applied) and the
  canonical unit, exact lines first; daily lines give day items and monthly
  items list their days. An item is within tolerance only when both figures
  are within 1%; non-usage lines are informational. Runs store the cards
  and FX rows they used, flag data entered after the month or the import,
  supersede earlier runs and carry over unchanged acceptances (never one by
  an importer of the month's invoices). A run keeps digests of the usage,
  cards and model aliases it read and goes stale when any of them would
  read differently, when an FX row it used or the month's invoices change,
  or when a card or usage change is stamped after it, so a change committed
  while the run was reading is not missed. The comparison runs off the
  event loop. Platform-key usage is reported beside the comparison;
  in-house, storage and GPU usage are never compared.
- Acceptance: an item, or a run out of tolerance only at the provider level,
  is accepted with a reason by an active tenant administrator other than the
  invoice importer (maker-checker), audited with both variances.
- `GET /spend/gate` answers the Gate 1 status of a month: at least 98% of
  INR spend attributed to a business unit, department, team or cost centre
  (group nodes do not count) with no unpriced, unconverted or FX-pending
  record, and every invoiced or tenant-billed provider reconciled within 1%
  by a current, fresh run (an accepted difference does not count).
- Invoice, reconciliation and acceptance audit rows are commercial reads
  (`spend.invoices.*`, `spend.reconciliations.*`), hidden from `GET /audit`
  for callers the commercial routes refuse. A card or FX row a current
  reconciliation used counts as in use (a card up to the last billing day
  the run compared, so a run of a month still open does not hold its cards
  to the month's end). Behind `spend_intelligence_enabled` (default off):
  off, the nine new routes are not found. Migration
  `v6z82_spend_reconciliation` adds `spend_invoices`, `spend_invoice_lines`,
  `spend_reconciliations` and `spend_reconciliation_items`, tenant scoped
  under forced row-level security, with composite tenant foreign keys and a
  leading index on each. Freezing closed months and statements are Phase 2.

### Added - AI spend intelligence: embeddings, OCR, speech, priced tools, storage and GPU metering
- Non-token usage becomes usage records with the same pricing, attribution,
  writer, keys and rollups as model calls: embedding tokens (knowledge
  ingestion, re-indexing, search and RPA ingestion; estimated from
  characters and capped at the model's input limit), OCR pages (document
  processing, knowledge uploads, ingestion that extracted for itself; once
  per extraction, even when the upload is refused or nothing is indexed),
  speech minutes (a recording Whisper or Deepgram transcribed, with the
  Deepgram credential's billing account, metered as soon as the engine
  returns, so a redaction, encryption or database failure afterwards still
  leaves the billed minutes recorded once) and connector tool calls for tools
  a rate card prices (an unpriced call is counted as an `unpriced_tool` gap,
  never recorded). Every site is guarded and failure-isolated: a malformed
  object can never fail the ingestion, search, speech save or tool call.
  In-house embeddings, OCR and Whisper price at zero unless a card exists.
- Storage GB-days: a daily job (23:30 IST) samples each tenant's knowledge,
  document, document-processing and recording storage in GiB, one record per
  store and day; a delayed run still records its day, missed days after the
  first sample are filled as estimates (a day sampled empty is marked, so it
  is never filled), and `POST /spend/storage/sample`
  previews the sample without writing (audited). A tenant paused by
  `spend.metering_paused` is not sampled: its day is a `paused` gap and stays
  unmetered (it is never filled later, as paused usage is dropped).
- GPU node hours of in-house serving: standing pools from
  `AGENTICORG_SPEND_GPU_POOLS_JSON` and metered or manual hours from the
  operator command `python -m core.spend.gpu_cli`, held in a platform table;
  an hourly job spreads each closed pool hour over every call of the pool's
  models by tokens and charges the zero-priced calls' tenants (shares by
  largest remainder, each tenant's share priced once and split so hours and
  money are both conserved, records flagged `allocated` with the call's
  attribution). Calls a tenant's own card priced above zero are skipped and
  counted, and their share, the share of tenants deleted since the hour and
  idle hours stay with the platform (`skipped_node_hours`), so no tenant's
  card or alias moves cost onto another; in-house providers take no model
  alias, and an in-house alias kept from before is never applied when a
  call is priced or recorded, so it cannot move a tenant's calls out of
  their pool. A paused tenant's share waits, frozen, and the hour is resumed once
  metering resumes. The operator command records only hours that have ended,
  at most seven days back, with one guarded upsert per hour (an hour being
  allocated is never overwritten), and logs the actor, pool, hours, node
  hours and models, and the values of each hour it overwrote.
  `GET /spend/gpu-allocations` lists a tenant's own shares to an
  administrator or auditor only (403 `tenant_wide_read_refused` for anyone
  else, checked before anything is looked up), because a share sums every
  agent's calls of the hour and carries no agent to filter by.
- Celery tasks `core.tasks.spend_tasks.sample_storage` and
  `allocate_gpu_hours` with their beats. Behind `spend_intelligence_enabled`
  (default off): off, every site is a bool read, the Deepgram credential note
  does nothing, the tasks skip, the operator command refuses and the two new
  routes are not found. Migration `v6z81_spend_gpu` adds
  `spend_gpu_pool_hours` (a platform table without tenant data, so without
  `tenant_id` or a row-level policy) and `spend_gpu_allocations` (tenant
  scoped under forced row-level security), with a leading index on its
  foreign key and a whole-UTC-hour check.

### Added - AI spend intelligence: usage records, attribution and the model-call meter
- Every metered model call becomes usage records (uncached input, cached
  input and output tokens, or one estimated token record when the split is
  unknown), priced at the card in force on the provider's billing date and
  converted to INR on the reporting date, with every attribution dimension:
  agent and version, organisation node and business unit, product line, use
  case, application, region, workflow, run, initiating user id, environment,
  risk tier and billing account (tenant key, platform key or in-house).
  Records hold identifiers, counts and amounts only, and are never deleted.
- Attribution is resolved on the server from the agent's configuration, the
  registry, source mappings and the organisation tree, never from request
  labels; each record stores the rule that matched. A legacy cost-centre or
  department label that names no node leaves the record unattributed as
  `unknown_label`; every unattributed record is kept and counted by reason.
  An agent hint naming a missing or retired agent is ignored on the metering
  path. Entry points (agent runs, chat, A2A, MCP, voice, workflows, content,
  speech, transaction narratives, console tools) bind their application.
- The hook in `record_model_call` is synchronous and failure-isolated: it
  builds events in memory and queues them for one writer thread per process,
  which writes them in batches on its own two-connection engine, retries a
  transient error once, waits out a rollup rebuild without blocking other
  tenants, and spills what it cannot write to the `persist_usage` task.
  Drops, spills and pauses are counted globally and per tenant; a call with
  no tenant is counted as unmetered, never as a write failure; a feature
  flag (`spend.metering_paused`) pauses metering per tenant or for all
  without a restart; the writer drains at shutdown, counting what it cannot
  hand on (late usage, unflushed gaps) as lost. While spend is on, the API
  and each worker process load the metering code at startup. Four direct
  model callers outside the router are metered too.
- A daily rollup kept in the writer's transaction and rebuildable per day;
  coverage (the attributed share of INR spend, by amount and by count, with
  unpriced, unconverted and FX-pending volume, gaps, reasons and attribution
  paths); a read-only comparison with the existing cost ledgers; meter gaps.
  Audited maintenance jobs revise derived fields only: FX settlement (also
  queued by every new or changed rate, and `fx_in_use` for a rate settled
  records use), restatement (queued by corrections and backdated cards),
  re-attribution of unattributed records, and commitment drawdown and
  overage in event order. Each job locks the records it revises and audits
  every transaction that revises any, so concurrent jobs never move a rollup
  twice and a job that stops part-way leaves nothing unaudited. Backfill
  recreates model-call records from `model_gateway_records` with the hook's
  own keys.
- Jobs a reference-data change calls for are never dropped: they are queued
  in the change's own transaction (a rate-card correction, supersede, end
  change or import; an FX rate or import; a commitment create or update), so
  the change and its job commit or roll back together, and sent to a worker
  after the commit (a failed send leaves the job queued for the sweep). They
  are merged into a queued job of the kind that can cover them, or queued to
  run after the running one, never into a job queued again after a
  transient failure. An administrator's job whose send fails answers
  `failed`, unless a change's job was merged into it meanwhile: then it stays
  queued for the sweep. Locks are taken in one order: the resource's lock,
  then the job kind's, then the kind's queued jobs (skipping, never waiting
  for, a row another transaction holds); commitment rows are locked in id
  order on the write path as in the recompute. One job of a kind runs at a
  time; a running job beats a heartbeat, a sweep every 15 minutes requeues a
  lost worker's job and resends queued jobs, and a transient database
  failure is retried twice before the job fails.
- `GET /spend/usage`, `GET /spend/rollups`, `GET /spend/coverage`,
  `GET /spend/coverage/ledgers`, `GET /spend/gaps`, `GET /spend/jobs`,
  `GET /spend/jobs/{job_id}`, and the job routes `POST /spend/rollups/rebuild`,
  `POST /spend/usage/{backfill,restate,reattribute}`,
  `POST /spend/fx-rates/settle` and `POST /spend/commitments/recompute`
  (202 with a job id, one active job of a kind per tenant). One read rule,
  as on `GET /audit`: administrators and auditors read every usage record,
  rollup, coverage figure, gap, job and ledger comparison unfiltered; for
  anyone else usage records, rollups (every grouping, not only the grouping
  by agent) and the ledger comparison apply the agent visibility rule, and
  coverage, gaps and jobs, which sum every agent's usage, are refused (403
  `tenant_wide_read_refused`). User ids, rate cards, unit prices and
  commitments are shown to administrators and auditors only, as are, on
  `GET /audit`, the audit rows of spend jobs (`spend.job.*`, `spend.usage.*`,
  `spend.fx.*`, `spend.rollups.*`: parameters, amounts moved and counts),
  for a tenant that keeps a spend job. Celery tasks
  `core.tasks.spend_tasks.*` with beats for the partition horizon, FX
  settlement, commitment recompute and the job sweep. Alerts
  `AgenticOrgSpendWriteFailures` and `AgenticOrgSpendWriterBacklog`.
- Behind `spend_intelligence_enabled` (default off): off, the hook returns
  at its first statement, no writer starts, scopes and credential notes do
  nothing, the router's responses are unchanged, the tasks skip and the new
  routes are not found. Migration `v6z80_spend_usage` adds
  `spend_usage_records` (range-partitioned by month, July 2026 to December
  2028 plus a default partition), `spend_usage_rollups`, `spend_meter_gaps`
  and `spend_jobs` under forced row-level security on every table and
  partition, with tenant-composite foreign keys and their leading indexes.

### Added - AI spend intelligence: reference data and rupee pricing
- `core/spend/`: the reference data AI spend is measured against. An
  organisation tree (group, business unit, department, team, cost centre)
  keyed by the organisation's own codes, with kind nesting rules, cycle and
  depth checks under a per-tenant lock, deactivation instead of deletion.
  Source mappings tie agents, workflows, applications and legacy cost-centre
  and department labels to nodes, product lines and use cases. Model aliases
  map called model names to the SKUs cards and invoices use.
- Effective-dated rate cards per provider, usage type, model (or provider
  default), unit and source (list or contract), with cached-input prices,
  batch discounts and volume tiers. Overlapping active cards of one key are
  refused on every create, change, correction and import row. A wrong price
  on a started or referenced card is fixed by a correction (retire and
  replace, with a reason), never by an edit. Commitments (quantity or money)
  over billing periods, refused when they overlap. FX rates to INR per
  reporting date.
- A pricing engine: a usage is priced with the card in force on its billing
  date, ranking every pricing path together (model-specific before default,
  cached-capable before "no discount", contract before list), falling back
  to the deployment's list and override prices, zero for in-house models,
  and unpriced (never zero) otherwise; amounts are `Decimal`, rounded
  half-even to ten places, and converted to INR per record on the reporting
  date (`fx_estimated` on an earlier rate, `unconverted` with none). Two
  calendars: `spend_reporting_timezone` (default `Asia/Kolkata`) and each
  provider's billing zone (`spend_provider_billing_timezones_json`).
- `GET /spend/status`, `GET/POST /spend/org-nodes`, `GET/PATCH
  /spend/org-nodes/{node_id}`, `GET/PUT /spend/mappings`, `GET/PUT
  /spend/model-aliases`, `GET/POST /spend/rate-cards`, `PATCH
  /spend/rate-cards/{card_id}`, `POST /spend/rate-cards/{card_id}/correct`,
  `GET/POST /spend/commitments`, `PATCH /spend/commitments/{commitment_id}`,
  `GET/PUT /spend/fx-rates`, `GET /spend/price`, and bounded CSV/JSON imports
  (`POST /spend/{org-nodes,mappings,rate-cards,fx-rates}/import`, 2 MiB and
  5,000 rows, with a dry run). Scope family `spend` (`audit:read`,
  `approvals:write`); every write needs an active human tenant
  administrator and is audited with before and after values (imports as a
  manifest with the file's sha256); rate cards, commitments and prices are
  read by an administrator or auditor only, and `GET /audit` shows the
  `spend.rate_cards.*` and `spend.commitments.*` rows, which carry the same
  values, to those callers only. A request gate refuses oversize import
  bodies before they are read; JSON import numbers are read as decimals.
- Behind `spend_intelligence_enabled` (default off): off, the status route
  answers `enabled: false`, every other spend route is not found before its
  body is read, and nothing else changes, with one exception: the `GET /audit`
  filter above applies whatever the flag says, because the rows outlive it. It
  applies only for a tenant that keeps a rate card or commitment (one
  existence query on those tables, for callers other than a human
  administrator or auditor), so a tenant that never used spend runs the
  audit query it ran before. Migration `v6z79_spend_reference`
  adds `spend_org_nodes`, `spend_source_mappings`, `spend_rate_cards`,
  `spend_model_aliases`, `spend_commitments` and `spend_fx_rates` under
  forced row-level security, with tenant-composite foreign keys and a
  leading index on every foreign key.

### Fixed - Combined security and feature release regressions
- Lineage details ignore superseded node requests, distinguish traversal limits
  from absent sources, and expose native keyboard-accessible node controls.
- Personalisation resolves tenant keys asynchronously and offloads profile
  encryption/decryption from the request event loop. Concurrent creation of a
  duplicate rule returns the documented tenant-scoped `409 rule_exists`.
- The personalisation route-registration regression checks OpenAPI paths,
  including lazily included FastAPI routers.
- A no-op merge revision joins the registry compatibility and feature schema
  branches without renumbering existing migrations. Feature flags remain off
  by default; the merge does not activate optional features.

### Added - Personalisation: a consent-checked service for personalised content
- `core/personalisation/`: content for a subject and purpose is rendered
  only under a valid consent (granted, not withdrawn, not expired; one
  current record per subject and purpose, kept with its evidence when
  withdrawn). Profiles are encrypted for the tenant before any row lock.
  Rules pick a variant for a purpose by priority and conditions (`eq`,
  `ne`, `in`, `gte`, `lte`, `exists`) and declare every attribute they
  read; a placeholder that is not allowed or not in the profile is
  refused, never rendered blank. Every render and refusal is recorded with
  the consent, the rule, the attribute names used (never values) and a
  hash of the content; a preview records nothing.
- `PUT /personalisation/consents`, `POST /personalisation/consents/withdraw`,
  `GET /personalisation/consents`, `PUT/GET /personalisation/profiles`,
  `GET/POST /personalisation/rules`, `PATCH/DELETE /personalisation/rules/{id}`,
  `POST /personalisation/render`, `GET /personalisation/events`; scope
  family `personalisation`. A caller's own template may use only the
  attributes in the business console setting
  `personalisation.template_attributes`. Behind `personalisation_enabled`
  (default off). Migration `v6z77` adds `personalisation_consents`,
  `personalisation_profiles`, `personalisation_rules` and
  `personalisation_events` under forced row-level security.

### Added - Provenance and lineage: the lineage graph in the console
- The Lineage page finds nodes by kind and reference (`GET /lineage/nodes`),
  traces one upstream, downstream or both and draws it from origin to use,
  shows a node's sources, processing history and versions, and lists the
  sync sources with their recent runs; a role that holds approvals:write
  can run a source now. The navigation entry appears only while lineage is
  on (`lineage_enabled`, default off).

### Added - Provenance and lineage: incremental synchronisation
- `core/lineage/sync.py`: a sync source names a feed (public HTTPS,
  egress validated, optional bearer token kept encrypted) polled on an
  interval for what changed since its cursor; a run skips every item
  whose version provenance already keeps, ingests the rest (documents
  through knowledge ingestion, records through the transaction store),
  links each document to the feed it was acquired from, and records what
  it received, processed, skipped and failed; the cursor advances only
  when nothing failed. Sources are claimed under a row lock.
- `GET/POST /lineage/sync/sources`, `PATCH/DELETE /lineage/sync/sources/{id}`,
  `POST /lineage/sync/sources/{id}/run`, `GET /lineage/sync/sources/{id}/runs`;
  the sweep from Celery beat, behind `lineage_sync_sweep_enabled` (default
  off). Migration `v6z76` adds
  `lineage_sync_sources` and `lineage_sync_runs` under forced row-level
  security.

### Added - Provenance and lineage: the provenance model
- `core/lineage/provenance.py`: a node for every kept thing (a source, a
  document, a chunk, an embedding, a transaction record, a transcript, a
  finding, a draft, a model use) under its kind, reference and version,
  with its origin and when it was observed; a step between two nodes
  naming what was done, by which tool, with which parameters. Knowledge
  ingestion notes its chain (source, document, chunks, embeddings) after
  the rows are committed and never fails on it; transaction ingestion
  notes each kept record as acquired from its source.
- `POST /lineage` notes a chain an acquisition produced; `GET
  /lineage/nodes/{kind}/{ref}` describes one thing (versions, sources, the
  processing history back to them); `GET /lineage/trace/{kind}/{ref}` walks
  the graph upstream, downstream or both, bounded. Behind `lineage_enabled`
  (default off). Migration `v6z75` adds `lineage_nodes` and `lineage_steps`
  under forced row-level security. Docs: `docs/lineage/provenance.md`.

### Added - Transaction intelligence: narrative drafting and evidence export
- `POST /txn/findings/{id}/narrative` drafts a suspicious-transaction
  narrative from a finding, its rows and its entity view (through the
  content services' checked model call or from the facts alone): what
  was seen, when, on which accounts, through whom, why, a timeline, the
  parties, the basis, a recommendation and the gaps to check; the draft
  stays on the open finding for a person to review in the investigator
  queue and nothing is filed. `GET /txn/findings/{id}/evidence` exports
  the finding, narrative, rows, entity view and fund flow with a digest
  (`core/txn/narrative.py`). Open findings join the workbench review
  queue as the finding kind (approve confirms, reject dismisses with a
  reason). The Transactions page drafts the narrative and downloads the
  evidence.

### Added - Transaction intelligence: fund-flow graphs across hops
- `GET /txn/graph/{kind}/{ref}` builds the fund-flow graph around an
  account, customer or counterparty: counterparties as nodes expanded hop
  by hop from their own records (up to four hops, two hundred nodes),
  edges with totals, counts, first and last movement and channels, the
  heaviest outward paths, and the findings on every node;
  `GET /txn/graph/{kind}/{ref}/export` carries the graph, the records
  behind every edge and the findings as JSON or CSV for the case file
  (`core/txn/graph.py`). The Transactions page draws it, expands a node on
  click, lists the paths and the findings with their disposition, and
  downloads the export (`ui/src/pages/Transactions.tsx`).

### Added - Transaction intelligence: records, entity aggregation and detectors
- `POST /txn/records` keeps movements on accounts in batches, idempotent
  under each record's reference, and `POST /txn/import/document/{id}`
  books a kept bank statement's line items. `GET /txn/entities` and
  `GET /txn/entities/{kind}/{ref}` give the entity-centric view (totals,
  channels, branches, counterparties, a daily series, the findings).
  `POST /txn/detect` runs the structuring detector (cash deposits under
  the threshold, several within the window, together at or above it,
  graver across branches) and the pass-through detector (an inflow
  mostly gone within the window) over the recent records and keeps
  every new finding once under its fingerprint; a person dispositions a
  finding (dismiss with a reason, confirm, escalate to a case) and the
  detectors file nothing. Thresholds come from the business console.
  (`core/txn/`, `txn_records`, `txn_findings`, `docs/txn/intelligence.md`).
  Off by default (`AGENTICORG_TRANSACTION_INTELLIGENCE_ENABLED`).

### Added - Speech intelligence: spoken sensitive data redacted from the recording and the transcript
- `POST /speech/recordings/{id}/redact` finds spoken card numbers (Luhn
  over runs of spoken digits, number words, double and triple), one-time
  codes, CVVs and PINs after their cues, cuts them from the transcript
  with a marker (a card keeps its last four) and silences them in the
  audio, drops the summary made from the old transcript and records what
  was cut as kinds and times only; `dry_run` reports without changing.
  The business console chooses the kinds and can cut them at
  transcription; a live session masks each turn before keeping it
  (`core/speech/redaction.py`, `speech_recordings.redactions`).

### Added - Speech intelligence: agent assist and disclosure tracking
- A catalogue of disclosure scripts (recorded line, identity verified,
  rate and fees, cooling-off, how to complain, consent, collections
  conduct) with the phrases that count, the call types each applies to
  and deadlines; the business console names the ones a tenant requires.
  `GET /speech/recordings/{id}/disclosures` checks a kept transcript.
  Live agent assist (`POST /speech/live/sessions`, `.../turns`,
  `.../close`): every turn comes back with the checklist, the mood, the
  intent, the next question, the knowledge that answers the customer and
  the flags this turn raised, a disclosure overdue the moment its
  deadline passes unsaid among them; closing gives the compliance report
  (`core/speech/disclosures.py`, `core/speech/assist.py`,
  `speech_live_sessions`). The Calls page drives a live session.

### Added - Speech intelligence: call summaries and analytics
- `POST /speech/recordings/{id}/summary` summarises a transcribed call
  into intent, key points, next actions, outcome and customer mood,
  through the content services' checked model call or from the words
  alone (the banking intent catalogue, the most informative turns, the
  turns that commit to something, the closing turns), and computes the
  call's analytics: customer sentiment by turn and by thirds, the
  agent's empathy markers and a 0 to 100 score for answering negative
  turns with one, talk ratio, pace, interruptions, silences, the longest
  monologue and escalation signals. `GET /speech/analytics` averages
  them over the latest recordings (`core/speech/summary.py`,
  `core/speech/analytics.py`). The summary is kept encrypted like the
  transcript; the figures hold no words.

### Added - Speech intelligence: batch transcription and diarisation
- `POST /speech/recordings` takes a PCM WAV call recording, finds who
  spoke when (by channel for a stereo call with `channel_roles`, by
  sound for a mono one with a deterministic two-speaker clustering over
  energy-detected segments), transcribes it through a local
  faster-whisper model where installed or the tenant's Deepgram
  credential, or takes words transcribed elsewhere
  (`POST /speech/recordings/{id}/transcript`), aligns every word to a
  speaker and groups them into turns, and keeps the recording with the
  transcript encrypted under the tenant's key (`core/speech/`,
  `speech_recordings`, `docs/speech/intelligence.md`). Off by default
  (`AGENTICORG_SPEECH_INTELLIGENCE_ENABLED`): off, the status route
  answers `enabled: false` and the rest is not found.

### Added - Workbenches: search across cases, documents, customers and accounts; accessibility suite
- `GET /workbench/search` searches cases, documents, customers and accounts
  in one query: words and quoted phrases that must all match, a leading
  minus to exclude, facet filters as repeated parameters, and the facet
  values present with counts in the answer; every read tenant scoped and
  bounded, each kind searched only where a held workbench shows it
  (`core/workbench/search.py`, `ui/src/components/WorkbenchSearch.tsx`,
  a Search tab in the review officer's and investigator's workbenches).
  `ui/src/__tests__/workbench_accessibility.test.tsx` runs axe-core over
  every workbench page and panel; a violation fails the suite.

### Added - Workbenches: the business console for rules, thresholds and routing
- `GET /workbench/console` lists the settings a tenant may change without a
  release, with bounds, options, the tenant's value and the default;
  `PUT` and `DELETE /workbench/console/{key}` set a value within the
  bounds or restore the default, keeping the previous value and writing
  an audit row (`core/workbench/console.py`, `business_settings`). The
  values take effect in document review routing (confidence floors,
  types always reviewed), the draft kinds that wait for approval, the
  banking dialogue (answers tried before handing over, negative turns
  before offering a person, amount ceilings by intent) and the review
  queue's priority rules; with the flag off or the store unreadable the
  defaults apply, which are the values the code had. The supervisor's
  workbench shows the console as a tab (`ui/src/components/BusinessConsole.tsx`).

### Added - Workbenches: the unified review queue with edit before approval
- `GET /workbench/queue` lists everything waiting for a person in one
  shape and one order (priority, then age): approvals pending, documents
  in review, content drafts pending approval and governed cases awaiting
  a decision, narrowed to the kinds the caller's workbenches show.
  `GET /workbench/queue/{kind}/{id}` adds the fields a reviewer may edit;
  `POST /workbench/queue/{kind}/{id}/decide` applies the edits and then
  decides through the store that owns the item, so its rules apply
  unchanged (a draft's text with originals kept in `content_drafts.edits`,
  a document's fields as corrections, an approval's amendments recorded
  on the item and in the decision notes; a governed case is decided on
  its own page). The review officer's and supervisor's workbenches show
  the queue as a tab with the four counters summed
  (`core/workbench/queue.py`, `ui/src/components/ReviewQueue.tsx`).

### Added - Workbenches: the shell and who holds which
- `GET /workbench` lists the role-shaped consoles the caller holds (review
  officer, relationship manager, investigator, supervisor) with the tabs
  their role may see; `GET /workbench/{name}/summary` adds the number of
  items waiting behind each tab (approvals pending, documents in review,
  content drafts pending approval, governed cases awaiting a decision,
  conversations active or escalated).
  Administrators read the catalogue and assign workbenches to users
  (`PUT /workbench/assignments/{user_id}`, `workbench_assignments`,
  tenant-scoped under row-level security). The UI shows the shell at
  `/dashboard/workbench`: the held workbenches, a workbench's tabs with
  counts, and the review officer's content drafts decided in place
  (`core/workbench/`, `ui/src/pages/Workbench.tsx`, `docs/workbench/shell.md`).
  Off by default (`AGENTICORG_WORKBENCH_V2_ENABLED`): off, the index
  answers `enabled: false` and the other routes are not found.

### Added - Document processing: statement line items and version comparison
- `GET /idp/documents/{id}/statement` reads the transactions of a kept
  bank statement from its extracted table (date, description, debit,
  credit, balance in whatever order the header gives), joins
  continuation lines, flags salary credits and returned items, checks the
  running balance row by row against the opening balance and every
  debit and credit, and summarises totals, months, average and minimum
  balance and whether the closing balance agrees
  (`core/idp/statements.py`). `GET /idp/documents/{id}/compare/{other}`
  compares one document in two kept files: fields changed, added and
  removed with both values and boxes, page lines added and removed,
  table rows added and removed (`core/idp/compare.py`).
- Statement checks take debit-column amounts as magnitudes, flag returned
  items and salary credits from the full joined description, and report
  `consistent: null` with `rows_checked: 0` when no row could be checked.
  A table present in only one version lists all its rows as added or removed.

### Added - Document processing: reconciliation, stamps and analysis reports
- `GET /idp/documents/{id}/reconcile` compares the fields that should
  agree across the documents of a file (names after normalisation,
  dates, identifiers, amounts within tolerance) and names every value of
  a disagreement with its document, page and box
  (`core/idp/reconcile.py`). `GET /idp/documents/{id}/stamps` finds ink
  regions consistent with a stamp or seal on each rendered page (colour,
  size, density; presence, not authenticity) and says whether the
  document type expected one (`core/idp/stamps.py`). `GET
  /idp/documents/{id}/report` (JSON or Markdown) assembles the analysis
  report: documents and key fields with corrections, reconciliation,
  stamps, review reasons and a narrative built from them, never from a
  model (`core/idp/report.py`).
- Reconciliation requires every pair of values to agree, resolves
  two-digit birth years to the latest century not in the future, and
  keeps the sign of amounts; the report no longer lists corrected fields
  as missing or weak; the stamp check reads the kept file once and
  renders every page from that copy.

### Added - Document processing: review with overlays and corrections
- `POST /idp/analyse?store=true` keeps the file and the result
  (`idp_documents`, migration `v6z64_idp_documents`); a document the
  pipeline routed to review waits in `review`. `/idp/documents` lists
  kept documents by status, serves a document with its fields and
  corrections, renders a page as PNG for the overlay, takes a
  reviewer's value for a field (the extracted value stays beside it) and
  approves or rejects. The Documents page draws every field on the page
  it came from, coloured by confidence, with the reviewer's edits and
  decision (`core/idp/store.py`, `api/v1/idp_review.py`).
- Corrections to extra fields show in the served document, PDF page
  images are rendered with a scale capped at 2400 pixels a side, and the
  Documents page ignores a detail that arrives for a document no longer
  selected.

### Added - Document processing: classification and extraction
- With `AGENTICORG_IDP_ENABLED` on (off by default), `POST /idp/analyse`
  reads a PDF or image into pages of words with bounding boxes (text layer
  or OCR, saying when OCR is unavailable), types each page against a
  synthetic catalogue of banking documents by weighted rules, splits a
  bundle into one document per segment, extracts each type's fields with
  a page, a box and a per-field confidence (plus generic label-value
  lines), extracts tables from aligned columns, and
  routes a document to review when its type is unknown or weak, a
  required field is missing or weak, or a page could not be read
  (`core/idp/`, `GET /idp/document-types`, `POST /idp/classify-text`).
  Uploads over 25 MB are refused while they are read and PDFs over 50
  pages are refused rather than truncated; OCR runs off the event loop;
  a repeated heading alone no longer splits a bundle; negative amounts
  keep their sign; the routes need `approvals:read` / `approvals:write`.

### Added - Content services: document translation across Indian languages
- `POST /content/translate` and `/content/translate/batch`
  (`core/content/translation.py`): a translation into Hindi, Marathi,
  Bengali, Assamese, Gujarati, Punjabi, Odia, Tamil, Telugu, Kannada,
  Malayalam, Urdu or English with a glossary and terms kept verbatim;
  the service checks that every figure is still there, that the glossary
  was applied, that the verbatim terms were kept and that the text is in
  the target script, and marks the translation trusted only when all
  hold; `verify` adds a back-translation with its word overlap for review.
  `GET /content/languages` lists the languages and scripts.
- Figures are compared with their currency and magnitude (lakh, crore,
  thousand, million, in each supported language and in native digits), a
  translation changed by an output guardrail is no longer marked trusted,
  a text is at most 4,000 characters (the completion budget is sized to
  match), and a batch refuses a register or format the single request refuses.

### Added - Content services: narrative to payload, grounded responses, tone adaptation and clause assembly
- Four more content services under `/content`: narrative to payload
  (`structure`: a schema-shaped JSON object from free text, validated
  against an inline, registered or built-in schema, rendered as XML on
  request, with what the text says but the schema cannot hold listed),
  policy-grounded responses (`respond`: an answer from an approved source
  set with a cited quote per claim, or an honest statement that the
  sources do not cover the question), audience-adaptive tone (`adapt`:
  a rewrite for an audience, tone and reading level with every figure
  checked to be still there), and rule-driven clause assembly
  (`assemble`: a document from approved clauses whose conditions hold for
  the facts, placeholders filled and the gaps named, no model involved;
  clause library with versions and second-person approval, table
  `content_clauses`, migration `v6z63_content_clauses`).
- A grounded response is withheld when any of its claims is not covered by a
  verified citation; tone fact checks keep currency, lakh and crore, and
  percent; a payload changed by output guardrails is validated again; a named
  schema resolves to the requested or latest version; clause writes need a
  signed-in tenant administrator, and an update that changes nothing keeps the
  version and approval.
### Security - Reporting queries and conversation parsing
- Attribution and forecast dimensions now select SQLAlchemy columns instead of
  interpolating SQL identifiers. Tenant filters and reporting limits remain bound.
- Amount, repeat-request and rating parsers avoid polynomial regex backtracking;
  subprocess regressions replay long hostile turns, and PostgreSQL tests exercise
  every reporting dimension across two tenants.
- Update the UI source-map dependency, MCP proxy address dependency and MCP SDK
  to patched versions. No security alerts are dismissed or checks disabled.

### Added - Content services: governed drafting, structured summarisation and obligation extraction
- With `AGENTICORG_CONTENT_SERVICES_ENABLED` on (off by default), three
  reusable capability APIs under `/content` (`core/content/`), each with
  an input schema, an output schema, a guardrail profile and an
  evaluation dataset the tenant can install through the evaluation
  framework: drafting (notices, circulars, letters, emails, memos, FAQs
  from points and approved sources, naming the sources used and the
  placeholders for what is not known; notices and circulars wait in the
  drafts queue for a second person, table `content_drafts`, migration
  `v6z62_content_drafts`), summarisation across documents with key points
  that cite their documents, and obligation and deadline extraction where
  every item quotes its source and unsupported items are dropped and
  counted. Inputs pass the input guardrails, the model answers in JSON
  checked against the schema with one retry, and outputs pass the output
  guardrails with the sources as grounding context.
- Content services hardening: the `/content` routes need `audit:read` to
  read and `approvals:write` to act; drafting and draft decisions need an
  active human administrator (never an API key); a masked input is what
  the model sees; knowledge-base sources pass the retrieval guardrails;
  every output field passes the output guardrails; pre-model
  pseudonymisation applies to every content model call; extracted
  obligations need a quote of at least three words that supports them.

### Added - Conversational services: scenario templates, summaries, feedback and sentiment
- Multi-step scenarios chain intents without a model: a raised dispute
  offers to track its reference, a loan enquiry offers to start the
  application (a new `loan_application` intent with tenure) and the
  application offers tracking, a blocked card offers a replacement (a new
  `card_replacement` intent), and an application that needs something
  from the user offers a person (`core/conversation/scenarios.py`). A
  deterministic summary of requests, actions, pending items, hand-off,
  rating and sentiment is available to the caller
  (`GET /conversation/session/summary`), the supervisor and the hand-off
  (`core/conversation/summary.py`). A rating from 1 to 5 is asked once
  after an action or a hand-off or sent from the interface
  (`POST /conversation/feedback`) and kept with the agent's feedback;
  sentiment is read on every user turn and two negative turns in a row
  offer a person (`core/conversation/feedback.py`).
- Feedback for an agent is accepted only when the agent belongs to the
  caller's tenant and company and is visible to the caller, a retried
  rating stores one feedback row, and `stored_with_agent` is true only when
  the row was stored. Ratings and sentiment match whole words, prefer the
  longest phrase and read negation ("not helpful" is a 2), and an amount
  too large to be a number is asked for again.

### Added - Conversational services: escalation hand-off and the supervisor console
- A hand-off leaves a review-queue item (`conversation_escalation`) with
  the summary, the intent tag, the slots and the recent turns, raises a
  ticket through the agent's ticketing tool when one is authorised, marks
  the session escalated and tells the live feed
  (`core/conversation/escalation.py`). A supervisor lists live and
  escalated conversations, reads a transcript, takes a conversation over
  (the assistant stops answering and the user's messages reach the
  supervisor), replies into the user's chat, and
  releases it (`core/conversation/supervisor.py`,
  `/conversation/supervisor/sessions`, the Conversations page; migration
  `v6z61_conversation_supervision`).
- The tenant-wide live feed announces conversation turns and messages
  without their text; the console reads the transcript through its
  tenant-admin route and the user's chat reads supervisor replies from the
  user's own session (`GET /conversation/session` returns `messages`), so
  replies sent while the chat was closed appear when it reopens. A reply
  from a supervisor who does not hold the conversation is refused before
  anything is written, and a hand-off keeps the intent and slots collected
  before the dialogue gave up; accepting the offer of a person after
  repeated fallbacks is recorded as a fallback hand-off with high priority.
- The hand-off is the one path for every escalation: the review item is
  written only for an agent of the tenant and notifies its approvers, and
  when neither the item nor a ticket could be raised the user is told that
  nothing has been handed over instead of being promised a person.

### Added - Conversational services: multi-turn context, clarification and graceful fallbacks
- With `AGENTICORG_CONVERSATION_V2_ENABLED` on, the recent turns of a chat
  reach the agent as the run's context so follow-ups resolve earlier
  references (`core/conversation/context.py`); in the banking dialogue
  "the same amount", "that account" and "again" resolve from the last
  action, a message naming two amounts or two payees is asked about
  instead of acted on, and a failed, empty or low-confidence answer is a
  graceful fallback that says what happened and what is known about the
  outcome (a timeout says the outcome is unknown and asks the user to
  check before retrying), and offers a person after two in a row, counted
  across chat queries from the session history
  (`core/conversation/fallbacks.py`). An amount marked with a currency and
  a bare alternative ("₹500 or 600") are both offered as choices.

### Added - Conversational services: banking intents, slot filling and confirmed execution
- With `AGENTICORG_CONVERSATION_V2_ENABLED` on (off by default), a chat
  message that names a banking intent (balance, statement, card block,
  transfer, bill payment, loan, dispute, application status, talk to a
  person) is a dialogue turn: parameters are extracted and collected turn
  by turn, an ambiguous message is clarified, a transaction is summarised
  and confirmed, and only then the bound tool runs under the grant through
  the agent's governed tools (`core/conversation/`, `/conversation/turns`,
  table `conversation_sessions`, migration `v6z60_conversation_sessions`).
  `POST /chat/query` returns the outcome in `conversation`; other messages
  reach the agent as before.
- A confirmed action is claimed with the session state under a row lock,
  with an idempotency key, before its tool is called, so it runs once; the
  agent's operator halts and throttles apply to it; a request for a person
  raises a hand-off item in the approvals queue, or says honestly that none
  was raised; bindings prefer the exact connector-qualified tool.

### Added - Agent runtime: debugging console with breakpoints and step-through
- With `AGENTICORG_RUNTIME_DEBUG_CONSOLE_ENABLED` on (off by default), a
  tenant administrator reads a run's checkpoints back as steps (the node
  that ran, what it changed, the bounded and redacted state, what is next)
  and inspects one value of one step by its path
  (`core/langgraph/debugger.py`, `/agents/{id}/debug/threads/{thread_id}`).
  An agent's breakpoints pause its runs before a node (`PUT
  /agents/{id}/debug`); a paused run is a debug session (table
  `agent_debug_sessions`, migration `v6z59_agent_debug_sessions`) the
  console steps one node at a time or continues to the next breakpoint.
  The console opens a run's thread from its timeline (`agent.thread_id`).
  A step passes the plan, budget and cost-threshold gates a run passes, adds
  what it spent to the agent's cost ledger, resolves the run's connector
  credentials again, keeps the run's output schema and limits, and an
  approval it reaches opens the normal approval flow.

### Added - Agent runtime: schema-validated tool registration and the execution envelope
- With `AGENTICORG_TOOL_REGISTRY_ENABLED` on (off by default), a tenant
  registers tools with JSON Schemas for their inputs and outputs, a risk
  class and an envelope (`core/tool_gateway/registry.py`, table
  `tool_registrations`, migration `v6z58_tool_registrations`,
  `/tools/registry`); the gateway refuses inputs that fail the schema
  before any call leaves it, audits the refusal, and holds a call to its
  timeout, output cap and output schema, marking the output untrusted.
- The check and the envelope also hold at the shared connector dispatch
  that LangGraph agents, workflow connector steps and remote MCP tools use;
  a registry that cannot be read refuses the call
  (`tool_registry_unavailable`); untrusted output passes the guardrails'
  retrieval stage and is withheld when a rule blocks it; the table's
  row-level policy is forced for the table owner.

### Added - Agent runtime: long-term memory with retention and erasure
- With `AGENTICORG_RUNTIME_MEMORY_ENABLED` on (off by default), a run that
  names a subject recalls what is remembered about it into its prompt and
  stores what it asks to keep (`core/memory/long_term.py`, table
  `agent_memories`, migration `v6z57_agent_memories`); entries expire by
  their kind's retention and are pruned nightly; `/memory` recalls,
  remembers and erases every entry about a subject, with the count.
- Recall needs `audit:read` and writes need `approvals:write`; an `agent_id`
  must name an agent of the tenant the caller can see (404 otherwise).
  Recalled entries are redacted with the task before the model sees them,
  a memory database error never fails a run, and the same content is one
  entry under a unique index written by an atomic upsert.

### Added - Agent runtime: visual workflow builder with branching and fallback
- The console's Build visually tab draws a workflow as a graph of steps,
  dependencies, condition paths and fallbacks, adds and connects agent
  steps, human checkpoints and conditions, and validates the definition
  through `POST /workflows/validate`, which names every problem
  (`core/workflows/graph.py`); `GET /workflows/{id}/graph` draws a stored
  one. The engine gains `on_failure: fallback(step)`. With
  `AGENTICORG_WORKFLOW_BUILDER_V2_ENABLED` on (off by default),
  `POST /workflows` refuses a definition with problems.
- The Build visually tab and the workflow page graph show only while
  `GET /workflows/builder` reports the flag on. A fallback step runs only
  when its source failed, with or without a declared dependency; validation
  refuses entries without a text id and conditions with `rules`, which the
  engine does not branch on yet.

### Added - Agent runtime: execution limits and loop detection
- With `AGENTICORG_RUNTIME_LIMITS_ENABLED` on (off by default), an agent's
  own limits (`PUT /agents/{id}/limits`: model steps, duration, tool calls,
  and the loop rule) are enforced in the graph and the runner, bounded by
  the platform's maxima (`core/langgraph/limits.py`): a run over a limit, or
  repeating a tool call pattern, is stopped with the reason in its error and
  a `limit` block that the run's audit entry and a Prometheus counter carry.
- The step limit is checked before every model call, chat runs carry the
  agent's limits too, `POST /agents/{id}/run` returns the `limit` block, a
  run may make exactly `max_tool_calls` tool calls, and with the switch off
  a run that reaches the platform ceiling fails as it did before.

### Added - FinOps: cost comparison and forecasting
- With `AGENTICORG_FINOPS_FORECAST_ENABLED` on (off by default),
  `GET /finops/forecast` projects tokens and cost per use case for the next
  quarter from the attributed ledger's history and a growth assumption, and
  `GET /finops/comparison` folds the model calls per use case with the
  cheapest catalogue alternatives and a before-and-after around a change
  date (`core/finops/forecast.py`).
  The forecast totals cover every label; the listed rows are the highest
  projected spend first, with `total_rows` and `truncated` when the list is
  cut. Calls without an input and output split are priced at the blended rate
  alongside the split calls in the comparison.

### Added - FinOps: thresholds and actions
- With `AGENTICORG_FINOPS_THRESHOLDS_ENABLED` on (off by default), a
  tenant administrator sets organisation, application, use-case or
  business-unit thresholds per day or month with an action
  (`core/finops/thresholds.py`, table `finops_thresholds`, migration
  `v6z56_finops_thresholds`, `/finops/thresholds`): a breached threshold
  alerts the owner once per period, throttles the run with a short delay,
  or suspends runs until the period resets or an administrator lifts it.
  Thresholds need `AGENTICORG_FINOPS_ATTRIBUTION_ENABLED` (settings refuse
  to load without it), match runs on the agent's own use case and business
  unit rather than the caller's labels, notify once per period under
  concurrent runs, cap a tenant at 200 thresholds and answer a duplicate
  name with 409.

### Added - FinOps: use-case attribution
- With `AGENTICORG_FINOPS_ATTRIBUTION_ENABLED` on (off by default), a run
  binds its use case, application, business unit, department and cost
  centre (`core/finops/attribution.py`); its cost write adds a row per day,
  agent and attribution to `finops_cost_ledger` (migration
  `v6z55_finops_attribution`), each model call record carries the business
  unit and application, and `GET /finops/attribution` folds the ledger by
  any dimension with the unattributed share.
- The ledger's row-level policy is forced, its unique key includes the
  department and cost centre (a mid-day change starts a new row), and the
  legacy-table column additions skip a table that is missing.

### Added - AI governance: policy console
- With `AGENTICORG_GOVERNANCE_POLICY_CONSOLE_ENABLED` on (off by default),
  a tenant administrator sees every policy (model routing, access and
  limits, guardrail rules, approval policies, the action taxonomy) in one
  shape, writes and removes one through its own store's writer, and dry-runs
  a described call, text, tool or workflow across the enforcement points
  (`core/governance/policy_console.py`, `/governance/policies`).
- The console lists disabled policies too, checks approval steps as the
  approval policies API does, and its dry run resolves approvals, model
  access by application and principal, and tool actions as runtime does.

### Added - AI governance: regulatory risk tiers
- With `AGENTICORG_GOVERNANCE_RISK_TIERS_ENABLED` on (off by default), an
  agent's risk tier forces controls whatever the separate switches say
  (`core/governance/risk_tiers.py`): medium needs registry approval; high
  adds a passed evaluation gate, a human oversight condition and 50 scored
  shadow samples; critical adds 200 samples and maker-checker. Promotion and
  resume refuse an unmet requirement; a tier is changed by an administrator
  and lowered by a second person; a regulated agent keeps its oversight and
  its gate. `GET /governance/risk-tiers` shows the policy and compliance.
- Shadow evidence for a tier counts terminal human reviews, not runs the
  model scored itself; a tier change on an active agent is refused while the
  new tier's controls are unmet; a `PUT /agents/{id}` replacement cannot drop
  a regulated agent's oversight condition.

### Added - AI governance: model cards
- With `AGENTICORG_GOVERNANCE_MODEL_CARDS_ENABLED` on (off by default),
  every model a tenant uses has one standard card
  (`core/governance/model_cards.py`): catalogue facts, use and risk tier
  from the inventory, the policies and limits that name it, the residency
  decision, price, health, the newest evaluation run, and the part an
  administrator writes (table `model_cards`, migration `v6z54_model_cards`)
  with approval by a second person; `GET /governance/model-cards` lists the
  cards with what each still lacks.

### Added - AI governance: asset inventory and bill-of-materials export
- With `AGENTICORG_GOVERNANCE_INVENTORY_ENABLED` on (off by default), a
  tenant administrator reads a live inventory of the tenant's agents,
  models, prompts (as hashes), knowledge bases, tools and connectors with
  owner, version, risk tier, status and dependencies
  (`core/governance/inventory.py`, `GET /governance/inventory`), and
  exports it as a bill-of-materials document
  (`GET /governance/inventory/export`); the summary counts unowned assets
  and untiered agents.

### Added - Knowledge retrieval: quality metrics, grounding indicator and re-indexing
- With `AGENTICORG_KNOWLEDGE_METRICS_ENABLED` on (off by default), every
  knowledge search leaves a figures-only sample (`core/rag/metrics.py`,
  table `knowledge_retrieval_metrics`, migration
  `v6z53_knowledge_retrieval_metrics`) and feeds Prometheus series by
  retrieval path; `GET /knowledge/metrics` folds a tenant's window and
  `POST /knowledge/metrics/grounding` returns the share of an answer's
  sentences the given chunks support. With
  `AGENTICORG_KNOWLEDGE_REINDEX_ENABLED` on, `POST /knowledge/reindex`
  re-embeds chunks made by a stale model and records missing entities,
  bounded per call (`core/rag/reindex.py`).

### Added - Knowledge retrieval: graph retrieval over extracted entities
- With `AGENTICORG_KNOWLEDGE_GRAPH_RETRIEVAL_ENABLED` on (off by default),
  ingestion records the names, codes, amounts and dates each chunk mentions
  (`core/rag/entities.py`, table `knowledge_entities`, migration
  `v6z52_knowledge_entities`; identity numbers are never recorded), a search
  fuses in the chunks the graph reaches from the query (matched entities,
  their neighbours, the chunks that mention them) with a `graph` step in the
  trace, and `GET /knowledge/graph?q=` shows the matched entities, their
  neighbours and the links between them.

### Added - Knowledge retrieval: query transformation and retrieval traces
- With `AGENTICORG_KNOWLEDGE_QUERY_TRANSFORM_ENABLED` on (off by default),
  a knowledge search is planned before it runs (`core/rag/query.py`): the
  query is normalised, a compound question decomposed by named rules and a
  keyword form added, with a model proposing further queries when
  `AGENTICORG_KNOWLEDGE_QUERY_REWRITE_MODEL` names one. A weak first pass is
  expanded to the variants and fused by reciprocal rank; `"trace": true` on
  `POST /knowledge/search` returns every step with its counts, and the
  console shows them under the results.

### Added - Knowledge retrieval: document access control
- A knowledge document belongs to a domain or to the tenant as a whole
  (`knowledge_documents.domain`, migration
  `v6z51_knowledge_document_domain`; the upload names it with `?domain=`).
  A caller limited to some domains is shown chunks of documents in those
  domains and of shared documents only, in search results, citations,
  excerpts and the document list (`core/rag/access.py`); an unrestricted
  caller sees the tenant's documents as before, and existing documents stay
  shared.

### Added - Knowledge retrieval: citations and excerpt navigation
- Every knowledge search hit carries a `citation` (chunk id, source,
  chunk number, page, paragraph, heading, sheet, cell range;
  `core/rag/citations.py`) read through a join on the chunk provenance;
  older chunks carry `null` and the original fields are unchanged.
  `GET /knowledge/documents/{id}/excerpt?q=` returns the cited chunk
  whole with the query terms located and the neighbouring chunks, under
  the retrieval guardrails. The Knowledge Base page shows the citation
  beside each hit and opens the excerpt with the terms marked and
  previous/next links.

### Added - Knowledge retrieval: search filters and a re-ranking stage
- `POST /knowledge/search` takes `filters` (`category`, `source`,
  `file_type`, a `created_from`/`created_to` window; `core/rag/filters.py`)
  applied inside the dense and sparse rankings alike, so a narrowed
  search never widens past what was asked. Behind
  `AGENTICORG_KNOWLEDGE_RERANK_ENABLED` (off by default), the fused
  candidates of a hybrid search are re-scored on the query's own terms
  (coverage, phrase, proximity, title, fused score; `core/rag/rerank.py`)
  with no model call; off, the fused order is returned as it was.

### Added - Knowledge retrieval: layout-preserving extraction and chunking strategies
- PDF pages are split into numbered paragraphs with headings recognised
  from line shape, Word documents keep their heading styles and attach
  table rows to their section, and every chunk records its paragraph and
  nearest heading beside its page (`knowledge_chunk_sources.paragraph`,
  `.heading`; migration `v6z50_chunk_layout`). A tenant chooses how spans
  become chunks with `chunk_strategy` in the tenant AI settings
  (`core/rag/chunking.py`): `sentence` (the default, unchanged), `paragraph`
  or `heading`, sized by `chunk_size`. Existing chunks are not re-chunked.

### Added - Agent registry: ratings, reliability and certification
- The card carries reliability metrics over a window (runs by status,
  completion, failure and human-review rates, average and 95th-percentile
  duration, tokens and cost per run, feedback by type, shadow accuracy;
  `GET /agents/{id}/reliability?days=`), the rating summary
  (`POST /agents/{id}/rating`, one score per user and agent, table
  `agent_ratings`, migration `v6z49_agent_ratings`) and a certification
  section: registry approval, the evaluation gate verdict and the model
  provider attestation, with a plain statement that trust-registry
  attestations and passports are not attached.
- The certification shows the provider attestation for the tenant's governed
  data region, as residency enforcement chooses it; a first rating is one
  atomic insert-or-update; a viewer with `agents:read` can rate.

### Added - Agent registry: dependency graph
- `GET /agents/{id}/dependencies` (`core/agent_registry/dependencies.py`)
  returns an agent's models, prompt, tools and connectors, knowledge base,
  governing policies (guardrail rules, review condition, output schema,
  evaluation gate dataset), related agents and teams as nodes and edges,
  with names and references only.
- Governing rules are the ones execution selects (the run use case, no risk
  tier); card-scoped rules are marked as not applied at run time. Connector
  nodes use the runtime tool-reference parser.

### Added - Agent registry: catalogue, templates and banking pack

- Catalogue search now filters visibility and search terms before its result
  limit, ignores superseded responses, clears failed results and supports keyboard
  navigation. Banking workflows use the installer's supported manual format;
  installation does not enable schedules or approve agents.
- `GET /agent-registry` filters by state, risk tier, use case, channel,
  domain and a search term over the card text; the console page Agent
  catalogue shows it. `GET /agent-registry/templates` lists the agent
  templates the industry packs offer in the card's terms. A banking pack
  (`core/agents/packs/banking`) adds five installable templates for retail
  and SME banking operations (loan underwriting analyst, KYC reviewer,
  collections agent, complaint handler, bank reconciliation analyst), each
  with a review condition, a confidence floor of at least 85% and tools the
  platform has.

### Added - Agent registry: approval workflow, environments and traffic split
- With `AGENTICORG_AGENT_REGISTRY_GATES_PROMOTION` on (off by default),
  promotion and resume to active need an approved or published registry
  entry (`core/agent_registry/approval.py`), checked after the shadow
  evidence, the maker-checker check and the evaluation gate; promotion
  publishes an approved entry and retirement retires a published one, each
  a recorded transition. Environments (development, staging, production)
  are read from the state. `PUT /agents/{id}/traffic-split` sends a share
  of an agent's runs through the agents API to another active agent while
  `AGENTICORG_AGENT_TRAFFIC_SPLIT_ENABLED` is on (`core/agent_registry/
  traffic.py`), chosen from the run's thread or correlation id so a retry
  lands on the same agent; the response names the agent that served the
  run, and removing the split is the one-action rollback.

### Added - Agent registry: cards and lifecycle states
- Behind `AGENTICORG_AGENT_REGISTRY_ENABLED` (off by default), each agent
  has a card (`GET /agents/{id}/card`: identity, purpose, risk tier, use
  case and channels, models, tools, permissions, schemas, a prompt summary
  without the text, controls, the evaluation gate verdict and the
  lifecycle state) with the written fields set by `PUT /agents/{id}/card`,
  and a governance lifecycle (draft, review, approved, published,
  deprecated, retired) moved by `POST /agents/{id}/lifecycle` under a
  transition table: the submitter cannot approve and only an active agent
  is published. Every transition is recorded (`agent_registry`,
  `agent_registry_events`, migration `v6z48_agent_registry`);
  `GET /agent-registry` lists entries by state and risk tier. The
  lifecycle does not yet gate promotion. See
  `docs/governance/agent-registry.md`.

### Added - Evaluation framework: promotion gate and model comparison
- An agent may declare an evaluation gate (`PUT /agents/{id}/eval-gate`;
  `core/evals/gates.py`): a dataset, optionally a version, a minimum pass
  rate and a regression allowance. Behind
  `AGENTICORG_EVAL_PROMOTION_GATE_ENABLED` (off by default), promotion and
  resume to active refuse, after the maker-checker check, a prompt whose
  newest model-bound stored run is missing, incomplete, contains answer or
  judge errors, is below the minimum, or regresses against an explicitly
  selected `baseline_run_id`. Runtime fallback models require independent
  passing evidence. `GET /agents/{id}/eval-gate`
  reports the verdict either way. `POST /eval-datasets/{id}/run` takes
  `agent_id` to run an agent's prompt text so the gate can match it.
  `GET /eval-datasets/{id}/compare` ranks the models that ran a version from
  their newest stored runs by pass rate, latency and cost per case, with
  accuracy, answers a minute, tokens per case (migration
  `v6z47_eval_run_tokens`) and the judges' means; the console shows the
  table.

### Added - Evaluation framework: adversarial and scheduled runs
- Two synthetic check kinds (`observability/synthetic.py`, migration
  `v6z46_synthetic_check_kinds`): `adversarial` dry-runs the tenant's
  guardrail rules over the adversarial evaluation set on an interval and
  fails below a minimum recall or above a number of benign controls
  wrongly caught; `eval_dataset` scores a dataset version with a fixed
  prompt and model, keeps the run in the evaluation history and fails
  below a minimum pass rate. Results keep counts and case ids, never a
  text. The Guardrails page lists the adversarial checks with their latest
  result, and the checks panel offers both kinds.

### Added - Evaluation framework: scorers, metrics and stored runs
- A case may carry a `label` (the class the answer should name), a
  `reference` answer and a `context`. A run of a dataset version
  (`core/evals/runs.py`) reports exact-match and classification metrics
  (accuracy, macro precision, recall and F1 over the dataset's labels;
  `core/evals/metrics.py`) beside the pass rate, and any of four
  model-graded judges with a judge model (faithfulness, relevance,
  instruction adherence, context recall; `core/evals/scoring.py`), each one
  more billed call per case and judge, with failures kept apart from scores.
  Runs are stored (`eval_runs`, migration `v6z45_eval_runs`) as what was
  measured and what came out, with the prompt as a hash and an optional
  label; never an answer, an input or a judge's reason. `GET
  /eval-datasets/{id}/runs` and `GET /eval-runs/{run_id}` read them back,
  and the console panel chooses judges and lists earlier runs.

### Added - Evaluation framework: datasets
- Behind `AGENTICORG_EVALS_V2_ENABLED` (off by default), a tenant's
  administrators keep named evaluation datasets as immutable versions
  (`core/evals/datasets.py`, tables `eval_datasets` and
  `eval_dataset_versions` under row-level security, migration
  `v6z44_eval_datasets`). Each version carries the SHA-256 of its cases;
  identical cases are not stored twice and a dataset is archived, not
  deleted. `/eval-datasets` lists, creates, reads, versions and archives;
  `POST /eval-datasets/{id}/run` scores a prompt with one model against a
  version, 25 cases a request, and names the version and hash it measured.
  The Prompt Templates page has a panel for all of it. Runs are not stored.
  See `docs/governance/evaluation-datasets.md`.

### Added - Tenant-owned remote MCP tools
- Dedicated Streamable HTTP bearer connection, protocol discovery, encrypted
  token storage, schema-bound read review, read-only probe and credential rotation.
- Agent create/edit, save validation and runtime share tenant-scoped discovered
  tool identities; remote tool grants use isolated manifests and preserve signature,
  scope and revocation checks. Unlinking, archiving and schema drift refuse calls.
- Remote write tools remain contained. OAuth/stdio/legacy SSE connections and
  live provider speech, messaging or payment actions are not verified by this release.
- Added SDK protocol, Docker API/database and browser regressions, including the
  reported speech-tool names, tenant isolation and refusal paths.

### Fixed - Worker startup and readiness
- Initialize multiprocess metrics before vault imports create DB instruments.
- Bind the worker startup listener only after Celery reports ready, so import,
  vault, metrics and initial broker failures cannot present a healthy worker.
- Add fresh-process regressions for both development and production settings;
  missing vault keys still refuse startup.

### Added - Prompt governance: structured-output enforcement
- Behind `AGENTICORG_OUTPUT_SCHEMA_ENFORCED` (off by default), an agent's
  final answer is validated against the output schema it declares
  (`core/prompts/output_schema.py`): its own JSON Schema, set with
  `PUT /agents/{id}/output-schema` (locked on active agents), or a
  registered schema name. An invalid answer goes back to the model with
  what is wrong, up to two times; one that is still invalid, or a declared
  schema that cannot be used, is escalated to a human reviewer
  (`output_schema_invalid`, `output_schema_unusable`) instead of being
  returned as completed. Agents with no declared schema are not affected.
  Enforced on runs started through the agents API.
  `agenticorg_output_schema_checks_total{result}`.

### Added - Prompt governance: context-window management
- Behind `AGENTICORG_CONTEXT_WINDOW_MANAGED` (off by default), before each
  model call of an agent run the conversation is measured against the
  model's context window, less the room for its answer and a margin
  (`core/prompts/context_window.py`). When it does not fit, older tool
  results are omitted from the copy that is sent, least relevant to the
  latest user message first, each replaced by a marker so every tool call
  stays answered; the largest remaining results are cut if needed. System
  messages, the user's messages, the model's turns and the newest tool
  results are never dropped, the run's history is unchanged, and the
  grounding check still reads everything retrieved. Token counts are
  estimates. `agenticorg_context_window_trims_total{result}`.

### Added - Prompt governance: model comparison and dataset evaluation
- Behind `AGENTICORG_PROMPT_COMPARE_ENABLED` (off by default; tenant
  administrators; six requests a minute per tenant): `POST
  /api/v1/prompt-templates/compare` runs one prompt and one input against up
  to four models and returns each answer with its latency, tokens and cost,
  and `POST /api/v1/prompt-templates/evaluate` scores up to three prompt
  variants against a reference dataset of up to 25 cases with deterministic
  expectations, reporting pass rates and failed checks
  (`core/prompts/compare.py`). Calls go through the model gateway as the
  tenant; one model's failure is its own result; nothing is stored.
- The prompt templates page has a Compare models panel on a selected
  template.

### Added - Prompt governance: maker-checker for agent prompts
- With maker-checker on (`AGENTICORG_PROMPTS_MAKER_CHECKER` or the
  authority flag `prompts.maker_checker`), an agent whose prompt has changed
  since it was last active is promoted, or resumed to active, only by a
  signed-in user other than the one who last changed the prompt
  (`core/prompts/activation.py`); an agent is not created or cloned straight
  into `active`; a change with no recorded author is not activated; an
  unreadable flag refuses the activation. A pause and resume with no prompt
  change is unaffected. Off, activation is as it was.
- Creating or cloning an agent writes its first prompt into the agent's
  prompt history with who set it, so a new agent's prompt has an author.

### Fixed - Public search pages
- Pre-render substantive public pages at build time, preserve private-page
  noindex behavior, and return a real 404 for unknown public paths.
- Repair two public links, connect public pages through crawlable navigation,
  and show the approved founder bio with consistent article author metadata.

### Added - Prompt governance: maker-checker for prompt templates
- With `AGENTICORG_PROMPTS_MAKER_CHECKER` or the authority flag
  `prompts.maker_checker` on (both off by default), creating, changing,
  rolling back or deleting a prompt template is stored as a change request
  and answers 202; a different person approves or rejects it and only an
  approval applies it (`core/prompts/change_requests.py`). The proposer
  cannot decide their own change, a template has one pending change at a
  time, a change proposed against a template that has since changed becomes
  stale and is not applied, and an unreadable flag refuses the change.
- The template history records who proposed a change, who approved it and
  the request it came from. `GET /api/v1/prompt-templates/changes`, `GET
  .../changes/{id}` and `POST .../changes/{id}/approve|reject|withdraw`; the
  prompt templates page shows what is waiting. Migration
  `v6z43_prompt_change_requests`. An agent's own prompt is not covered yet.

### Added - Prompt governance: typed parameters
- A prompt template's variables can be declared as typed parameters
  (`core/prompts/parameters.py`): `string`, `integer`, `number`, `boolean`
  or `enum`, with required, default, range, length, pattern and choices. A
  variable declared by name only is a required string, as before.
- `POST /api/v1/prompt-templates/check` checks a template's text against
  its declarations without storing it, and `POST
  /api/v1/prompt-templates/{id}/render` fills a stored template after
  checking the values; a missing, unknown or mistyped value is refused with
  every problem listed, and no placeholder is left in rendered text.
- Behind `AGENTICORG_PROMPT_TYPED_PARAMETERS_ENABLED` (off by default),
  creating or changing a template checks its parameters and refuses a
  placeholder it does not declare. See `docs/governance/prompt-governance.md`.

### Added - Streaming latency: time to first token and task queue wait
- Behind `AGENTICORG_MODEL_STREAM_TIMING_ENABLED` (off by default) the
  reasoning node reads each model answer as a stream, times its first token
  and reassembles the same message (`observability/streaming.py`); the time
  is observed in `agenticorg_model_first_token_seconds{provider,model}` and
  set on the model call's span as `llm.first_token_ms`. Off, the call is
  made exactly as before.
- Behind `AGENTICORG_TASK_QUEUE_TIMING_ENABLED` (off by default) a
  published background task carries its publish time, and the worker
  observes how long it waited in
  `agenticorg_task_queue_wait_seconds{queue}` and on the task's span
  (`task.queue_wait_ms`); tasks scheduled for later, redeliveries and
  retries are not counted.

### Added - Guardrails: the adversarial evaluation set
- A fixed corpus of 46 synthetic cases
  (`core/governance/guardrails/adversarial.py`): direct and indirect
  injection, sensitive data in answers and tool calls, ungrounded answers
  and output-policy breaks, each with benign controls, and with attacks the
  pattern-based detectors are known to miss. `POST
  /api/v1/guardrails/adversarial/run` dry-runs the tenant's rules or the
  recommended baseline over it and reports per category what was detected,
  missed and wrongly caught, without any case text; the Guardrails console
  has a card for it. The baseline's result (25 of 31 attacks, 1 of 15
  controls wrongly caught) is pinned case by case in a test.
- `evaluate` takes a rule set for a dry run, so a set can be measured
  without storing it.

### Added - Guardrails console
- `/dashboard/settings/guardrails` (administrators): the mode in effect
  (hooks off, flag-only, enforcing), the rules with what each applies to,
  add, change, enable, disable and delete, and a dry run of a stage over a
  text with a retrieved context for a grounding rule. `GET
  /api/v1/guardrails/status` also reports whether the hooks are on and the
  stages, detectors, actions and risk tiers a rule may name.

### Added - Guardrails: the grounding checker
- A `grounding` detector for output-stage rules
  (`core/governance/guardrails/grounding.py`): each claim of an answer is
  held against the context the run retrieved (the conversation's tool
  results and, unless `include_user_input` is false, the user's words). A
  claim whose content words are supported below `min_support` is an
  `unsupported_claim`; a figure that occurs nowhere in the context is an
  `unsupported_number` whatever the claim's support; `require_context`
  reports an answer given with no retrieved context. The rule flags, or
  blocks the answer when `guardrails.enforce` is on; it never rewrites.
  Deterministic and lexical: no model call.
- The reasoning node passes the conversation to the output stage, and
  `POST /api/v1/guardrails/evaluate` takes `context` and `user_input` for a
  dry run. Only a detector that uses the context receives it. See
  `docs/governance/guardrails.md`.

### Added - Synthetic checks
- A tenant administrator defines scheduled probes of the tenant's own paths
  (`observability/synthetic.py`): `model` (a fixed prompt through the direct
  router, optionally expecting a text in the answer), `knowledge` (a fixed
  query expecting a minimum number of results), `guardrail` (a dry run of
  the rules for a stage expecting the text blocked, detected or clean) and
  `audit_chain` (a verification of the newest links). Each takes a latency
  limit. A run ends `ok`, `failed` (with reasons) or `error` (with the
  exception type) and is stored in `synthetic_check_results`; a result keeps
  counts and reasons, never an answer, retrieved text or the input.
- A run claims its check with one conditional update before it probes, so
  overlapping sweeps or a run by hand never probe a check twice; creations
  are serialised per tenant so the limit of 20 holds. Off, adding a check
  and running one are refused and the console shows no Checks tab.
- Behind `AGENTICORG_SYNTHETIC_CHECKS_ENABLED` (off by default) the sweep
  (`core.tasks.synthetic_tasks.run_synthetic_checks`, every five minutes)
  runs each tenant's due checks under the tenant's own row-level security
  context; runs count in `agenticorg_synthetic_checks_total{kind,result}`.
  Results are pruned after `AGENTICORG_SYNTHETIC_CHECKS_RETENTION_DAYS` (30).
- `GET/POST /api/v1/observability/checks`, `PATCH/DELETE /checks/{id}`,
  `POST /checks/{id}/run` and `GET /checks/{id}/results` (administrators
  only), and a Checks tab on the observability console page. Migration
  `v6z42_synthetic_checks`. See `docs/operations/synthetic-checks.md`.

### Added - Tamper-evident audit: the hash chain and the model call digests
- Behind `AGENTICORG_AUDIT_CHAIN_ENABLED` (off by default): the sealing task
  (`core.tasks.audit_chain_tasks.seal_audit_chains`, every five minutes)
  links each tenant's unsealed audit rows in write order with a sequence
  number, the previous link and a SHA-256 link hash over the previous link,
  the row's signed payload and its signature (migration
  `v6z41_tamper_evident_audit`). The chain head is logged after every sealing
  as the anchor. Verification recomputes every link and reports the first
  break with its sequence number and reason (`sequence_gap` for a removed
  row, `previous_link` for an inserted or reordered one, `link_hash` for an
  edited one, `signature` for a forged one). Every sealing also stores the
  head in `audit_chain_anchors` (tenant-scoped under row-level security), and
  verification holds the chain against it and against a head the caller
  supplies from the sealing log (`expected_seq`, `expected_hash`), so a chain
  cut at its end is reported as `truncated` or `anchor_mismatch`. The
  append-only trigger of `audit_log` admits exactly the sealing transition
  (the chain columns of an unsealed row filled, nothing else changed), and a
  tenant's sealers are serialised by an advisory lock. The daily task verifies every
  tenant and counts results in
  `agenticorg_audit_chain_verifications_total{result}`.
- `GET /api/v1/audit/chain` reports the head and the sealing backlog and
  `GET /api/v1/audit/chain/verify?from_seq=&limit=` runs a verification; the
  compliance evidence package's `audit_logs` section carries the head, the
  backlog and a verification of the newest thousand links.
- Routing records carry `prompt_digest`, `request_digest` and
  `response_digest` (SHA-256 over the system prompt, every message the model
  saw and its answer), signed with the record and never the content; records
  written before verify unchanged. See `docs/operations/audit-chain.md`.

### Added - Observability: run timelines, the waterfall and the live workload console
- Behind `AGENTICORG_TRACING_TIMELINE_ENABLED` (off by default; nothing
  while tracing is off): a span processor keeps the finished spans of each
  agent run (the run, its model calls, tool calls and knowledge searches) in
  memory per run, keyed by the run's root span so two runs sharing a trace
  never mix, and the runner stores them in the tenant-scoped `run_spans`
  table when the run ends, on the run's own event loop. Rows hold
  identifiers, timings, outcomes and the governance events, never content;
  a daily task prunes them after `AGENTICORG_TRACING_TIMELINE_RETENTION_DAYS`
  (migration `v6z39_run_spans`).
- `GET /api/v1/observability/runs` lists the newest stored runs (and says
  whether recording is on), `GET /api/v1/observability/runs/{run_id}`
  returns one run's spans with their offsets and durations, and
  `GET /api/v1/observability/workload` reports the tenant's pending reviews
  with the soonest deadline and the overdue count, and the last hour's run,
  model-call and guardrail outcomes, each part reported on its own. Admin-only
  reads of the tenant's own data (the shared task queues are not answered).
  The run response carries `trace_id` when tracing is on.
- Console page `/dashboard/observability` (administrators): the waterfall of
  one run (model, tool and retrieval spans with durations and the gateway and
  guardrail events) and the live workload with a countdown to the soonest
  review deadline.

### Added - Observability: tracing wiring and correlation ids
- Behind `AGENTICORG_TRACING_ENABLED` (off by default): the API's lifespan and
  every worker process install an OpenTelemetry tracer that exports to
  `OTEL_EXPORTER_OTLP_ENDPOINT` over `http/protobuf` or `grpc`
  (`AGENTICORG_TRACING_PROTOCOL`), sampled at `AGENTICORG_TRACING_SAMPLE_RATIO`.
  A strict runtime with tracing on and no endpoint refuses to start; off,
  every helper is a no-op that touches no tracer.
- Spans: `agenticorg.http.request` around every API request (continuing an
  incoming `traceparent`), `agenticorg.task.run` around every Celery task
  (the publisher's trace context travels in the task headers),
  `agenticorg.agent.run` and `agenticorg.agent.resume` around an agent
  graph's execution, `agenticorg.agent.reason` around every model call with
  the provider, model and token counts, `agenticorg.tool.call` around every
  connector dispatch with its outcome, `agenticorg.knowledge.search` around a
  knowledge search. The model gateway's decision and every guardrail outcome
  are events on the span in progress.
- Correlation: while a span records, the log context carries `trace_id`, and
  the signed audit rows the model gateway, operator overrides, residency
  attestations and guardrails write record the trace id (the request id when
  no trace is in progress). See `docs/operations/tracing.md`.
- A span names its tenant by `tenant.ref`, a keyed reference, never by the
  identifier. Residency: with deployment-wide enforcement no exporter is
  installed; with tenant-scoped enforcement a span of a tenant that enforces
  (or was never read) is withheld from export, as is a span naming no tenant
  while some tenant enforces. A worker refuses to start with tracing on and
  misconfigured, as the API does.

### Added - Runtime guardrails: call-site hooks, injection and output-policy detectors
- Behind `AGENTICORG_GUARDRAILS_HOOKS_ENABLED` (off by default; on, every
  stage is evaluated in flag-only mode until `guardrails.enforce` is on for
  the tenant): the reasoning node passes the newest message of each turn
  through the `input` stage and the model's answer through the `output`
  stage; knowledge search results and a governed case's rendered evidence
  pass the `retrieval` stage (a blocked chunk is withheld, a blocked case
  context skips the model call); a tool call's connector, tool and arguments
  pass the `action` stage at the dispatch boundary (flag or block only). A
  block ends an agent run with status `guardrail_blocked` and `E1016`, or
  returns `{"error": "guardrail_blocked"}` from the tool.
- Detectors `injection` (instruction overrides, system-prompt disclosure,
  persona switches, jailbreak markers, fake system blocks, standing orders,
  false authority and invisible characters, each with its own confidence,
  plus an administrator's own patterns) and `output_policy` (`max_length`,
  `require_json`, `required_keys`, `forbidden_phrases`, `no_urls`; output
  stage, flag or block). Transform actions are refused for structural
  detectors and for action-stage rules.
- The compliance evidence package gains a `guardrails` section: hooks and
  enforcement state, rules by stage, blocked and transformed outcomes over
  thirty days.

### Added - Runtime guardrails: engine, rules and the first detectors
- `core/governance/guardrails`: a tenant's rules say which detector runs at
  which stage of a call (`input`, `retrieval`, `output`, `action`) and what
  happens at or above the rule's threshold: `flag`, `mask`, `redact`,
  `tokenise` or `block`, narrowed to an agent, use case or risk tier. Every
  matching rule applies in priority order; a block wins over a transform.
  Detectors: `sensitive_data` (the PII analyser where installed, the regex
  recognisers otherwise, plus a Luhn-checked card-number check), `toxicity`
  (the content-safety classifier with its keyword fallback) and `pattern`
  (an administrator's regular expressions).
- Behind the authority flag `guardrails.enforce` (off by default;
  `AGENTICORG_GUARDRAILS_ENFORCE` for the deployment): off, every rule runs in
  flag-only mode, metered and logged with the text unchanged; on, transforms
  apply, a block refuses the stage with the new `E1016`, and each applied
  action writes a signed audit row. Outcomes are metered in
  `agenticorg_guardrail_outcomes_total{stage,detector,action,mode}` and logged
  with the request's correlation id.
- `GET /api/v1/guardrails/status`, `GET/POST /api/v1/guardrails/rules`,
  `PATCH/DELETE .../rules/{id}`, `POST .../evaluate` (dry run); tenant admin;
  each change writes a signed audit row. Table `guardrail_rules`
  (`v6z38_guardrail_rules`, tenant RLS). Docs: `docs/governance/guardrails.md`.
  The call-site hooks, the prompt-injection and output-policy detectors and the
  grounding checker follow in the package's next parts.
- Each detector runs off the event loop under
  `AGENTICORG_GUARDRAILS_DETECTOR_TIMEOUT_SECONDS` (2); a detector that fails or
  runs out of time fails closed for an enforced transform or block in a strict
  runtime. Pattern rules refuse expressions that nest or repeat unbounded
  quantifiers or use backreferences, take at most 32 patterns of 512
  characters, and scan at most `AGENTICORG_GUARDRAILS_PATTERN_MAX_CHARS`
  (50,000) characters. A rule's options must belong to its detector and
  `entities` must list supported kinds.

### Added - Model gateway: console page
- `/dashboard/settings/model-gateway` (tenant administrators): routing
  policies, access policies and per-model limits with create, enable, disable
  and delete; a dry run of a described request; the routing records with a
  correlation-id filter and the signature check; and the cost comparison.
  Everything reads and writes the `/api/v1/model-gateway` endpoints.

### Added - Model gateway: cost comparison and cost-aware routing
- `core/governance/model_pricing.py`: list prices per million tokens for the
  catalogue models (Gemini from the router's table), nothing per token for
  models inside the deployment, Azure deployments priced as their base model;
  `AGENTICORG_MODEL_PRICE_OVERRIDES_JSON` replaces list prices with negotiated
  ones (a negative or non-finite override is rejected). The routing records
  cost each call at its model's price when one is known; the direct router
  does so behind `AGENTICORG_MODEL_PRICING_FOR_ROUTER_COSTS` (off by default)
  and otherwise keeps its historical flat rates (FINDINGS A-116).
- `GET /api/v1/model-gateway/costs`: every catalogue model and every model seen
  in the records with its list price, blended rate and the observations over
  the window (calls, failures, failure rate, latency, cost), cheapest first.
- Routing policies gain `cost_aware` and `max_failure_rate`
  (migration `v6z37_cost_aware_routing`): with `cost_aware`, each call gets the
  cheapest of the policy's `targets` whose observed failure rate over
  `AGENTICORG_MODEL_GATEWAY_QUALITY_WINDOW_HOURS` (24) stays under the policy's
  threshold or `AGENTICORG_MODEL_GATEWAY_MAX_FAILURE_RATE` (0.05); no
  observations count as healthy, an unpriced target ranks last, unreadable
  records degrade to price alone, and the choice is named in the decision's
  reason. Observations are cached for
  `AGENTICORG_MODEL_GATEWAY_HEALTH_CACHE_SECONDS` (60).

### Added - Model gateway: model-level metrics, correlation ids and routing records
- Every model call on the agent path (the reasoning node) and the direct
  router is metered once it ends: `agenticorg_model_calls_total`,
  `model_call_latency_seconds`, `model_call_tokens_total`,
  `model_call_cost_usd_total`, `model_call_output_tokens_per_second`,
  `model_call_errors_total`, `model_fallbacks_total` and
  `model_admission_wait_seconds`, all by provider and model. The existing
  `agenticorg_llm_tokens_total` and `agenticorg_llm_cost_usd_total` are now fed.
- With `AGENTICORG_MODEL_GATEWAY_RECORDS_ENABLED=true` (off by default), each
  call made while the gateway is on for the tenant writes a signed routing
  record (`model_gateway_records`, migration `v6z36_model_gateway_records`):
  correlation id, use case, agent, routing and access policies evaluated,
  requested and chosen provider and model, fallback, outcome, error type,
  latency, admission wait, tokens and cost. `GET /api/v1/model-gateway/records`
  lists them (filters: correlation id, agent, outcome, before) and reports
  whether each signature still matches. Pruned daily after
  `AGENTICORG_MODEL_GATEWAY_RECORDS_RETENTION_DAYS` (90), deleted tenants
  included. Off, the metrics are still fed.
- The gateway's correlation id is the request id bound for the request (and
  propagated into worker tasks), so one id links the request, its routing
  decisions, its model calls and its audit rows. A per-model concurrency lease
  is now one per admission, not one per correlation id.
- Cost on the agent path uses the provider's list price where it is known
  (Gemini) and the platform's blended estimate otherwise; time to first token
  needs streaming, which the model calls do not use yet. `LLMResponse` now
  carries the provider's input and output token counts, so direct-router
  calls fill the directional token counters and the output-throughput
  histogram is observed only when the output count is known.

### Added - Model gateway: access policies, per-model limits and weighted targets
- Access policies (`model_access_policies`, `GET/POST /api/v1/model-gateway/access-policies`,
  `PATCH/DELETE .../access-policies/{id}`): who may use which provider or model.
  Evaluated after routing, first match in priority order; a policy matches on
  use case, sensitivity, agent, business unit, language, the calling
  `application` and `principal`, and the provider and model chosen; `deny`
  refuses, `allow` lets through fenced to `allowed_providers` and
  `allowed_models`; no match allows. A refusal is `E1014` with `kind: access`.
- The auth middleware binds the caller's identity for every authenticated
  request (`core/governance/caller_identity.py`): `application` is the API
  key's name, `agent:<id>` for an Agent Passport or `console` for a human
  session; `principal` is exactly the audit actor (`user:<id>` for a session
  with a user id, else mode and subject such as `api_key:apikey:<prefix>` or
  `grantex:<subject>`). Work outside a request carries none.
- Per-model limits (`model_limits`, `GET/POST /api/v1/model-gateway/limits`,
  `PATCH/DELETE .../limits/{id}`): `max_concurrency` and `requests_per_minute`
  per provider or per model, enforced in Redis at admission just before each
  model call is sent (every reasoning turn of an agent run, every direct
  completion) and released when the model returns; a slot never released
  expires after `AGENTICORG_MODEL_GATEWAY_LEASE_SECONDS`
  (600). A call above a limit is refused with the new retryable `E1015`
  carrying `retry_after_seconds`; every check is metered in
  `agenticorg_model_gateway_limit_outcomes_total{limit,outcome}`. An
  unreachable limit store admits the call and meters `unavailable`.
- Routing policies may split their matches by weight (`targets`:
  `[{provider, model, weight}]`), stable per correlation id; the dry run
  reports access decisions and takes `application` and `principal`.
- Migration `v6z35_model_access_limits`; each change writes a signed audit row.

### Fixed - A halted workflow stays recoverable when the task queue is unavailable
- When `resume_halted_workflow` cannot be queued (broker down), the background
  executor no longer leaves the run at `running` with nobody retrying it: it
  retries in process every `AGENTICORG_OPERATOR_HALT_RETRY_SECONDS` and offers
  the retry to the queue again on every pass, until the override is released,
  the run is cancelled or the queue takes it. The pending retry is recorded on
  the run, and the `recover_halted_workflows` beat sweep
  (`AGENTICORG_OPERATOR_HALT_RECOVERY_SWEEP_ENABLED`) re-queues a run whose
  record nobody has heartbeated for three retry intervals, so a broker outage
  followed by a restart of the retrying process cannot strand it either.
  Publishing to the broker runs off the event loop.

### Added - Model gateway: routing policies in front of every provider
- `core/governance/model_gateway.py`: tenant routing policies decide the
  provider and model of a model call from its use case, data sensitivity,
  agent, business unit and language; a policy routes (provider, model or cost
  tier), fences (`allowed_providers`, a refusal rather than a substitution) or
  restricts (`in_region_only`). A request tagged `restricted` may only use a
  provider inside the deployment or one attested for the tenant's region,
  whether or not residency enforcement is on; otherwise it is refused with
  `E1014`. Applied at the agent runner (run and resume, with the sensitivity
  recorded on the agent) and `LLMRouter.complete`; every decision is logged
  with a correlation id and counted in
  `agenticorg_model_gateway_decisions_total{outcome}`.
- `GET /api/v1/model-gateway/status`, `GET/POST /api/v1/model-gateway/policies`,
  `PATCH/DELETE .../policies/{id}`, `POST .../evaluate` (dry run); tenant
  admin; each change writes a signed audit row. Table `model_routing_policies`
  (`v6z34_model_routing_policies`, tenant RLS).
- Off by default: the authority flag `model_gateway.enabled` (operator
  managed) or `AGENTICORG_MODEL_GATEWAY_ENABLED` turns it on. Docs:
  `docs/governance/model-gateway.md`.
### Added - Infrastructure attestations in the compliance report
- `GET /compliance/evidence-package` gains `infrastructure_controls`: every hosting
  control of the BFSI deployment reference with the status an operator recorded in
  the JSON file named by `AGENTICORG_INFRASTRUCTURE_ATTESTATIONS_FILE` (`verified`,
  `not_applicable`, or `not_verified` when nothing is recorded), with a summary and
  the ids the file names that the reference does not. An unreadable file reports the
  section `unavailable`.

### Changed - The compliance report no longer claims internal mTLS by default
- `encryption_in_transit.mtls_internal` is false unless `AGENTICORG_MTLS=true` is set
  by a deployment with a mesh (FINDINGS A-114).
### Changed - A paused agent can be refused, not only left out of routing
- `AGENTICORG_PAUSED_AGENTS_REFUSED` (default off): `POST /agents/{id}/run` and a
  chat that names the agent refuse a `paused` agent with 409, and a workflow
  agent step treats it as inactive, so the console's pause stops execution
  (FINDINGS A-110). Routing already skipped paused agents.

### Fixed - Operator override error code
- The operator override refusal is `E1013`. It had been registered as `E1012`,
  the code the pseudonymisation refusal (`pseudonym_restore_failed`) already
  carried, so a client could not tell a halted agent from a refused tool
  argument.
### Added - Residency enforcement and provider attestations
- `core/governance/residency.py`: with enforcement on, a provider is refused
  unless an administrator has attested, for the tenant's data region, that
  processing stays in region and the provider does not train on the data.
  Enforced at the AI credential resolver (every LLM, embedding, retrieval and
  speech credential, with the tenant carried through the explanation, SOP
  parsing and feedback analysis model calls), the managed retrieval service's
  upload, search, list and statistics (deletion stays open), the third-party
  tool hub at the dispatch boundary and tracing export (off deployment-wide;
  under tenant-scoped enforcement an enforcing or unread tenant's payloads are
  withheld); providers inside the deployment need no attestation. Strict runtimes fail closed when the region or the
  attestations cannot be read. Refusals: `agenticorg_residency_refusals_total`.
- `GET /api/v1/residency/status`, `GET/POST /api/v1/residency/attestations`,
  `POST .../{id}/revoke` (tenant admin); each change writes a signed audit row.
  Table `provider_residency_attestations` (`v6z33_provider_attestations`,
  tenant RLS).
- The compliance evidence package gains a `data_residency` section (`RES-1`):
  region, enforcement state, storage-region conformance, tenancy profile
  (`AGENTICORG_TENANCY_PROFILE`), disaster-recovery profile
  (`AGENTICORG_DR_STANDBY_REGION`, `AGENTICORG_DR_LAST_DRILL_AT`) and the active
  attestations.
- Off by default: the authority flag `residency.enforce` (operator managed) or
  `AGENTICORG_RESIDENCY_ENFORCE` turns it on. Docs:
  `docs/governance/data-residency.md`.

### Added - Operator override (halt or throttle a model, agent, workflow or the tool pipeline)
- `core/governance/operator_override.py`: an administrator places an override
  on a provider, a model, one agent, every agent, a workflow definition, a
  connector, one tool or the whole tool pipeline, in `halt` or `throttle`
  (calls per minute) mode, with a reason and an optional expiry. Enforced at
  the model router and the LangGraph reason node (never falls back to another
  model), the agent runner and resume path, `BaseAgent.execute`, the workflow
  engine before every step (the run keeps status `running` and the
  `resume_halted_workflow` worker task retries it every
  `AGENTICORG_OPERATOR_HALT_RETRY_SECONDS`, 30 s by default, until released or
  cancelled), the connector dispatch boundary and `ToolGateway.execute`; the
  agent and workflow run endpoints, and chat before its deterministic route,
  refuse early with 423. Halt beats throttle; a throttle counts
  in Redis across replicas and fails closed in a strict runtime; blocks are
  counted in `agenticorg_operator_override_blocks_total`.
- `POST/GET /api/v1/operator-overrides`, `GET .../status`,
  `POST .../{id}/release` (tenant admin); every change writes a signed audit
  row. Table `operator_overrides` (`v6z32_operator_overrides`, tenant RLS).
- Off by default: the authority flag `operator_override.enabled` (operator
  managed) or `AGENTICORG_OPERATOR_OVERRIDE_ENABLED` turns it on. Docs:
  `docs/governance/operator-override.md`.

### Added - Evidence sink for governed cases, and `make demo-case`
- `AGENTICORG_CASE_EVIDENCE_SERVICE=grantex` (default `off`) records each
  governed-case agent run into the Grantex evidence service
  (`POST /v1/evidence/cases/{case}/records`): the run context (model, prompt
  and policy versions), every provider tool call with the run grant it was
  authorised under and the upstream records it returned, the policy
  evaluation (inputs recorded unsourced: the engine keeps no per-path
  provenance), the recommendation with each memo section's citations
  resolved to the call that retrieved them, and each proposed screening
  disposition with its hit and comparisons. The service exports the signed
  package. Recording is best effort for the case: a failure is logged and
  counted (`agenticorg_case_evidence_records_total`) and never changes the
  case. `AGENTICORG_CASE_EVIDENCE_TIMEOUT_SECONDS` (default 10).
- The grant a tool call was authorised under now travels with it:
  `ToolDecision.grant_id` from the case authorizer, `ToolCallRecord.grant_id`
  in the case record and `GET .../case-record`.
- `make demo-case` (`scripts/demo_case.py`): one governed case under a run
  grant against the mock provider with `grants.enforce_closed=deny` and the
  sink on: root grant, case, every provider call authorised, an out-of-scope
  call denied with its reason, the package exported, its root and anchor read
  from the tenant audit log and verified with the `grantex-evidence` CLI.
  Every step prints `live` or `fixture`. The dev stack's Grantex service is
  started with `EVIDENCE_EXPORT_ENABLED`; the Local Stack workflow runs the
  demo after `make seed-cases`.

### Changed - OpenTelemetry 1.45
- `requirements.txt` and `pyproject.toml` move `opentelemetry-api`,
  `opentelemetry-sdk` and `opentelemetry-exporter-otlp` to 1.45.0 and
  `opentelemetry-instrumentation-fastapi` to 0.66b0 together. The SDK and the
  exporter pin the API and the 0.66b0 semantic conventions exactly, so bumping
  one of them alone left `requirements.txt` uninstallable and `pip-audit`
  unable to audit it.

### Changed - SQLAlchemy 2.1
- `requirements.txt` pins SQLAlchemy 2.1.0 and `pyproject.toml` allows the
  2.1 series (`>=2.1.0,<2.2`), replacing the cap below 2.1 (FINDINGS A-70).
  2.1's result typing no longer infers a type for raw-SQL scalars, so the
  five values mypy reported are annotated; nothing else changes at runtime.

### Fixed - the local stack answers the Grantex SDK's revocation check
- From grantex 0.7 the Python SDK's `enforce()` asks the auth service's
  `GET /v1/revocations/status` about every grant before it allows a tool call
  (`revocation_check="online"`, the new default) and refuses the call when it
  gets no answer. `pyproject.toml` allows `grantex>=0.5.1`, so CI and the
  images picked up 0.7.0 without a change here, and the checks broke:
  - the dev stack's auth service image serves the revocation endpoints only
    with `REVOCATION_FEED_ENABLED=true` and answered 404, so every governed
    tool call was refused (`grant_revoked`, `status_unavailable`), the
    underwriter runs failed and `make e2e-decisions` failed;
  - two unit test modules build a real SDK client against
    `https://grantex.invalid`, so the check failed name resolution, was
    retried with blocking sleeps and ran past the test timeout.
- `docker-compose.dev.yml` sets `REVOCATION_FEED_ENABLED=true` on the
  `grantex` service. The tests answer the status endpoint with a live grant,
  and new tests check that a grant the service reports revoked, suspended or
  unknown, or cannot report on (404, unreachable), is refused on every tool in
  the legacy and deny modes. Revocation checking is unchanged: production
  clients use the SDK default and fail closed.
### Fixed - global feature-flag rows are read by a role subject to row-level security
- `feature_flags` is FORCE ROW LEVEL SECURITY with a policy that compares
  `tenant_id` with the session's tenant, so a global row (`tenant_id` NULL)
  was invisible to a database role that is neither a superuser nor
  `BYPASSRLS`: `core.feature_flags` read no global row in any tenant session
  or under the nil tenant, and every global default - including an
  operator's global rows of the authority flags such as
  `approvals.unevaluable_condition.deny`, `grants.enforce_closed.deny` and
  `pseudonymisation.pre_model` - was ignored. Roles that bypass row-level
  security (and the Postgres tests, which ran as one) were not affected.
- Revision `v6z30_flag_global_read` adds a SELECT-only policy for
  `tenant_id IS NULL` rows. A session now sees its tenant's rows and the
  global rows, and still no other tenant's rows; a tenant row still overrides
  the global row in `is_enabled`, and authority flags still take the stricter
  of the two. A global row holds no tenant data (flag key, enabled, rollout,
  description, timestamps). There is no write policy for global rows: a role
  subject to row-level security cannot insert, update or delete one.
- **Breaking (deployments whose application role is subject to row-level
  security):** global rows that already exist start to apply to every tenant
  that has no row of its own (for authority flags, to every tenant). There is
  no flag: a Postgres policy cannot be switched per request. The opt-out is to
  clear the global rows that should not apply. Migration steps: before
  deploying, list the global rows as a privileged role with
  `SELECT flag_key, enabled, rollout_percentage FROM feature_flags WHERE tenant_id IS NULL;`
  then delete or disable each one that should not apply
  (`scripts/authority_flags.py clear <key> --global` for an authority flag).
  Rollback: the same, or restore the pre-migration backup; migrations are
  forward-only.
- `scripts/authority_flags.py set --global` and `clear --global` need a
  privileged database role (superuser or `BYPASSRLS`). Run as a role subject
  to row-level security they now exit `2` with a message saying so, before
  reading or writing anything, instead of failing on the policy (`set`) or
  reporting the global row as absent or cleared while deleting nothing
  (`clear`). `list --global` and tenant rows work with the application's
  role.
- Deploy with `--with-migrations` so the revision runs before the new code
  serves traffic.
### Security - email webhooks can take the tenant from the URL, not the payload
- The SendGrid, Mailchimp and MoEngage webhooks read the tenant from the
  signed body (a `tenant:<id>` category, `custom_args.tenant_id`, a
  `tenant_id` field), and each provider signs with one key for the whole
  deployment, so a validly signed event could name any tenant and resume that
  tenant's `wait_for_event` steps.
- Each tenant now has its own URL per provider,
  `POST /api/v1/webhooks/email/{provider}/{tenant_id}/{path_token}`, whose
  path token is an HMAC of the tenant and provider under
  `AGENTICORG_SECRET_KEY`. The provider signature is still verified; a wrong
  path answers 404 before the body is read. An event whose payload names a
  different tenant, or a value that is not a tenant id, is refused: it is not
  stored and resumes no wait, and the response counts it in `refused`. An
  active human tenant administrator reads the paths from
  `GET /api/v1/email-webhook-inbox`; API keys and agent tokens are refused.
- New setting `AGENTICORG_WEBHOOKS_TENANT_BOUND_PATHS`, default off. On, the
  shared `/api/v1/webhooks/email/{provider}` URLs answer 409 to any delivery
  with an event that names a tenant and store nothing from it, so the
  provider records the failure and can redeliver; events that name no tenant
  are processed as before. Off, the shared URLs behave exactly as before.
- `agenticorg_email_webhook_tenant_binding_total{provider, outcome}` counts
  events accepted on per-tenant URLs (`bound`), refused there
  (`tenant_mismatch`, `unbound`), and shared-URL events that name a tenant
  (`shared_path_tenant_named` with the setting off, `shared_path_refused` with
  it on), so the setting can be switched on once the first is flat. Moving
  providers: `docs/RUNBOOKS.md#email-webhooks-per-tenant-urls`.
- The API's access log replaces the path token with `[redacted]` in the
  request line for these URLs and for the governed-case provider inbox
  (`/api/v1/webhooks/providers/...`), which carried the same kind of token
  in its path: uvicorn logs every request line, so each delivery used to copy
  a reusable path token into the logs.
### Added - the decision-grant join and the governed-case suites run in CI
- A new `make e2e-decisions` job in `.github/workflows/local-stack.yml` runs
  on every pull request and every push to `main`: it starts the development
  stack with decision grants on and `AGENTICORG_DEV_CASE_DECISION_SERVICE=grantex`,
  then runs `make seed`, `make seed-cases` and `make e2e-decisions`
  (`ui/e2e/decision-grants.spec.ts`) against the real Grantex auth service,
  and uploads the Playwright report when it fails. Its check name,
  `make e2e-decisions`, is the one to require on `main`.
- The `make dev && make test` job now runs `make seed-cases` before `make e2e`,
  so the governed-case suites (`ui/e2e/governed-cases*.spec.ts`) run instead
  of skipping. They stay on that job's stack, which has no decision-grant
  issuer, because `governed-cases-decision.spec.ts` asserts that refusal.
- Both jobs generate the seed password, and the decision job the auth
  service's administrator key, with `openssl rand` for each run and mask them
  in the log. Nothing secret is written into the workflow.
- `make e2e-decisions` now also refuses to start without
  `AGENTICORG_DEV_GRANTEX_ADMIN_KEY` or with a case decision service other than
  `grantex`, before it touches the stack, and the suite's runner uses exactly
  the administrator key the auth service was given: the development
  placeholder it fell back to, which the service never had, is gone.
- In that job a missing seed password or seed file now fails the governed-case
  suites instead of skipping them (`AGENTICORG_E2E_REQUIRE_GOVERNED_CASES=true`),
  so a wiring change cannot leave the job green with nothing checked. Locally
  they still skip.
- The tools image now includes `make`, so the tests that run the
  `make e2e-decisions` guards run in `make test` too instead of skipping.
### Fixed - the dev stack's Postgres is healthy only once it accepts TCP connections
- The `postgres` healthcheck in `docker-compose.dev.yml` ran `pg_isready` over
  the Unix socket, which the image's init-time temporary server already
  answers on a fresh volume, so `grantex-db` or `migrate` could start, get
  `Connection refused` over TCP and fail `make dev`. It now checks
  `127.0.0.1`.
### Fixed - `make seed-cases` no longer leaves every sample case failed
- Every seeded case ended `failed` with `tool_refused:grant_missing`, because
  the development tenant had no agent for either governed-case role and the
  stack no root grant to delegate from. `make seed-cases` now creates one
  active, shared `business_underwriter` and `screening_disposition` agent
  with only its reference agent's read tools, registers them with the stack's
  Grantex service and allows them `aml.cdd.onboarding`, and obtains a root
  grant covering both for the run. It is held in the seed's process only and
  never stored or printed. Re-running reuses the agents and registrations.
- The root grant comes from a second, sandbox developer key the development
  Grantex service now seeds (`SEED_SANDBOX_KEY`), because a live developer's
  authorization waits for the principal's passkey. Only `make seed-cases`
  uses it; the API and worker keep the live key.
- The seed refuses a `GRANTEX_BASE_URL` that is unset or not the stack's own
  Grantex service, stops before registering anything when the tenant already
  has an active shared agent of either role it did not create, and fails
  instead of continuing when a registration cannot be made or read. The
  grant checks are unchanged: a call the delegated grant does not cover is
  still refused.
### Added - governed-case decisions consumed by request id at the issuer (off by default)
- `AGENTICORG_CASE_DECISION_GRANT_RELEASE` (default `false`). On, recording a
  case decision consumes the request at the Grantex auth service by its id
  (`POST /v1/decisions/requests/{id}/consume`) instead of reading
  `decisionGrants` from the request's status and presenting them, so
  AgenticOrg never presents, stores or forwards a decision grant. Until the
  issuer's `DECISION_GRANT_AGENT_BINDING` is on, the status answer still
  carries the grants and they are dropped; once it is on, the issuer never
  sends one here. A case decision is AgenticOrg's own: a person decides it and
  the request names no agent, so it is not fetched with an agent's grant token.
  Readiness comes from the request's state and, when the issuer sends it,
  `decisionGrantsReady`, which can only withhold it.
- This is what keeps decisions recordable once the issuer turns
  `DECISION_GRANT_AGENT_BINDING` on, which stops returning decision grants to
  the developer API key. Order: an issuer release that serves consumption by
  request id, then this change, then this setting on, then the issuer's
  binding on; roll back in reverse. With the setting off and the binding on,
  every decision is refused `decision_not_approved`.
- With the setting on, it fails closed: a request that names an agent is
  refused `wrong_agent` (the issuer's 403 `wrong_agent` is read as that, not as
  an authentication failure), a `decisionGrantsReady` that is not a boolean
  and any consumption answer that is not a confirmed consumption of that
  request naming the grant each approver spent are refused
  (`decision_service_response_invalid`), and nothing is recorded. The receipt
  is also held to the approvals the case recorded for the request: one
  approver where four eyes was needed, more approvers than needed, or the same
  person twice is refused `decision_service_response_invalid`, and a request
  the case has no usable record of is refused `decision_request_not_found`
  before the issuer is asked.
- Off, nothing changes. `ui/e2e/decision-grants.spec.ts` is unchanged and
  holds in both states; running it with the setting on
  (`AGENTICORG_DEV_CASE_DECISION_GRANT_RELEASE=true`) needs the development
  auth service pin moved to a build that serves consumption by request id.
  See `docs/governance/decision-requests.md`.

### Security - route scope checks can refuse unknown authentication modes and cover A2A and MCP
- Route scope checks never looked at how a request was authenticated. The
  auth middleware sets `api_key`, `grantex` or `legacy` once it has verified a
  credential; a request that reached an authenticated route with any other
  mode, or none, was checked against whatever scopes it carried, so
  `agenticorg:admin` passed every route and an unmapped family needed no scope.
  Such a request is now always logged as `route_enforcement_unknown_auth_mode`
  (path and mode only). New setting `AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE`
  (default `false`): off, the request is then checked on its scopes exactly as
  before; set to `true`, it is refused with `403 Unrecognised authentication
  mode; request refused` before any scope is read, also in
  `AGENTICORG_ROUTE_ENFORCEMENT_MODE=log`. No credential the middleware
  accepts is affected either way; public routes are not checked. A later
  release will turn the refusal on by default, with this setting as the
  explicit opt-out, once staging shows no unknown-mode warnings (FINDINGS
  A-95). Rollback: set it back to `false`.
- New setting `AGENTICORG_ROUTE_SCOPE_A2A_MCP` (default `false`, nothing
  changes). Set to `true`, `POST /a2a/tasks` needs `a2a:write`,
  `GET /a2a/tasks/{id}` needs `a2a:read` and `POST /mcp/call` needs
  `mcp:write` (API keys' `mcp:call` is accepted as an alias), or
  `agenticorg:admin`; discovery stays public. No role carries these scopes,
  because the routes run any agent type without a domain check, so only
  administrators' sessions reach them. **Turning it on is breaking** for
  default API keys on `POST /a2a/tasks` (they carry `a2a:read`, not
  `a2a:write`), which is the SDKs' run-by-type path, and for non-admin
  sessions. Issue replacement keys and grant agents the scopes first; the four
  scopes can be granted to agents (`PATCH /agents/{id}` `route_scopes`) while
  the setting is off. Rollback: set it back to `false`. See
  `docs/operations/grant-enforcement.md` (FINDINGS A-68).
- Once on, the refusal leaves valid callers alone only while every
  authenticated route goes through the auth middleware. A regression test now
  pins that no authenticated route accepts OPTIONS or sits under one of the
  middleware's exempt paths or prefixes, and that the known modes are exactly
  the ones the middleware sets. It found one overlap (FINDINGS A-88): an account aggregator
  consent status path whose handle begins with `callback` skips the
  middleware. It is still answered `401` while the refusal is off, and `403`
  with `AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE=true`.
- Docs: the Python and TypeScript SDK READMEs, the MCP product model and the
  API reference's SDK launch contract say which scope `client.mcp.call`
  (`mcp:write`, or the `mcp:call` alias) and a run by agent type
  (`a2a:write`) need with `AGENTICORG_ROUTE_SCOPE_A2A_MCP` on. The API
  reference's API key section now matches the code: the routes are under
  `/api/v1/org/api-keys`, the request field is `expires_days`, the default
  scopes use the canonical `agents:write` and `connectors.read`, and the list
  response is an array that includes revoked keys.
### Changed - sanctions screening runs through the verification provider seam
- The new `sanctions_screening` connector has no endpoint or API key of its
  own: every call goes to the verification provider named by `provider` in the
  connector config - `mock` by default, which runs only in local, development,
  test and CI environments, or a provider from a separately installed package
  registered through the `agenticorg.providers` entry point. Its tools are
  `screen_entity`, `screen_person`, `screen_business`, `screen_transaction` and
  `batch_screen`, and each returns `screening_result` records with every
  candidate the provider found. A name screened without a `type` is screened
  as a person and as a business. An unknown or unavailable provider, one
  without screening, or a screening the provider does not offer fails the call
  instead of returning an empty result. All the screenings of one tool call
  share one deadline, the connector's timeout from when the call starts, so a
  batch of 50 names cannot run for 100 timeouts. Grantex scopes for the new id
  come from `manifests/sanctions_screening.json`.
- `sanctions_api`, which was built on one commercial screening service's API
  and named it in its code, is deprecated and otherwise unchanged: the same
  five tools (`screen_entity`, `screen_transaction`, `get_alert`,
  `batch_screen`, `generate_report`), the same `api_key` authentication, the
  same requests and the same responses. Creating it logs
  `connector_id_deprecated`, and the connector catalog and product counts list
  only `sanctions_screening`. An agent moves to `sanctions_screening` only when
  its connectors are changed to link it. See "The sanctions screening
  connector" in `docs/providers/plugin-packages.md`.
- **Breaking for operators:** the address of the service `sanctions_api` calls
  is no longer in the code. It is the connector config's `base_url` or, when
  that is empty, the new setting `AGENTICORG_SANCTIONS_API_BASE_URL`, which has
  no default. Before deploying, set `AGENTICORG_SANCTIONS_API_BASE_URL` in every
  environment where tenants use `sanctions_api`, to the API base URL those
  tenants have been calling. Without it, and without a `base_url` in the
  tenant's connector config, every `sanctions_api` call fails with
  `SanctionsApiNotConfiguredError` before any request is sent, and the
  connector test reports `not_configured`.
- An agent's tools, bare names such as `screen_entity` included, bind to
  `sanctions_api` unless the agent's connectors name `sanctions_screening` and
  not `sanctions_api`, so a tool can meet a grant issued under the other id.
  Every grant check - LangGraph runs, `BaseAgent` (through
  `execute_agent_tool`) and the tool gateway, in `grants.enforce_closed` off,
  warn and deny alike - now accepts a scope held under either id for the tools
  the two connectors share (`enforce_connector_grant` in
  `auth/grant_enforcement.py`). New grants name `sanctions_screening` for those
  tools and `sanctions_api` for `get_alert` and `generate_report`, which only
  the deprecated connector has. Grants issued before keep covering the tools
  they listed; `screen_person` and `screen_business` are covered once they are
  added to the agent's tools, which recomputes its scopes.
- The `sanctions_screening` connector test reports `configured` and the
  provider's name, never `healthy`, because a provider cannot be probed yet, so
  an agent that links that connector cannot be activated until the provider
  seam gains a probe (tracked in `FINDINGS.md`). `sanctions_api` probes its
  service as before.
- The Risk Sentinel and Vendor Manager prompts name `sanctions_screening` in
  their token-scope lines.
### Added - the vendor denylist audit runs in CI and warns on house terminology
- `python scripts/check_denylist.py audit` passes on the whole tree, and the
  Vendor Denylist workflow now runs it on every pull request, except title
  and description edits, and on pushes to `main`; a denylisted term in any
  tracked file fails the job. `audit <path>...` checks only the tracked files
  under those paths and fails closed when they match none.
- `scan` and `audit` print a warning, with the location and the term to use,
  for kill switch, white-label, anomaly, trust provider, verification partner
  and verification result. The terms are listed in plain text in the script
  (`HOUSE_TERMS`), and a warning never changes the exit code. Directory,
  consumer and name / version have too many ordinary meanings to flag and stay
  a review check. See "Vendor-neutral names" in `CONTRIBUTING.md`.
### Fixed - the feed fan-out cap test no longer depends on runner speed
- `test_fanout_caps_parallel_sends_for_large_tenant` forced a 0.2 s send
  timeout on 100 sockets, so a busy CI runner timed out healthy 10 ms sends
  and the delivery count failed at random. Each mocked send now yields to the
  event loop once instead of sleeping, so the sends of a batch overlap
  deterministically, and the test sets its own 30 s send timeout, so neither
  the runner's speed nor a change to the service's timeout can fail it. It
  still checks that every socket is delivered to and that no more than 32
  sends run at once.
### Added - approval decisions can be refused when a policy condition cannot be evaluated
- A new operator-managed authority flag, `approvals.unevaluable_condition`
  (default `off`; decisions behave as before), decides what happens when a
  step of an item's approval policy has a condition that cannot be evaluated
  for the item. `off` keeps the current rule: the step applies and the vote
  counts. `deny` (an enabled `approvals.unevaluable_condition.deny` row,
  global or for the tenant) refuses every decision on the item that could
  move it forward - an approval, a `defer` or any other value - with `409`
  and `detail.reason_code` `approval_condition_unevaluable`, listing the steps
  in `detail.unevaluable_steps`. A rejection is still taken: it closes the
  item at the step it has reached and approves nothing. Every step of the
  policy is checked before any vote counts, so a later step that cannot be
  evaluated cannot route the item either.
- The refusal is committed before the `409` is returned: the item's
  `context.policy_state` records `last_action: "refused"`, the reason and the
  steps, and the audit log gets a `hitl.decision_refused` event (outcome
  `denied`). The refused vote is not counted, does not make its caller the
  decider and does not bind the item to the policy, so once the policy is
  recreated with a condition that evaluates (or the flag is cleared) the same
  reviewer can decide. The next decision recorded on the item removes the
  refusal keys from `policy_state`, so a decided item never reads as refused;
  the audit event stays.
- The check runs at decision time, the only place a policy is applied to an
  item: items are raised without consulting a policy, and a policy can be
  created or edited while they wait.
- The key is reserved (`403 flag_key_reserved` through the tenant
  feature-flag API); operators set it with `scripts/authority_flags.py`. An
  unreadable flag table resolves to `deny` and logs
  `approval_unevaluable_condition_mode_lookup_failed`; only decisions other
  than a rejection, on items with a condition that cannot be evaluated, are
  refused, and a rejection never reads the flag. A global row applies to every
  tenant, also on a database role subject to row-level security once revision
  `v6z30_flag_global_read` has run (see "global feature-flag rows" above). See
  "When a condition cannot be evaluated" in `docs/approval-policies.md`.
### Added - a runbook for rotating the credential-vault key
- `docs/runbooks/vault-key-rotation.md` rotates the vault key with the tools in
  the repository: add the new key to `AGENTICORG_VAULT_KEYRING` as a
  decrypt-only entry and roll the API, worker and beat; move it to the front and
  roll again; `python -m core.crypto.rewrap --dry-run`, the rewrap and
  `--verify`; `python -m core.crypto.verify_all --check=<old id>`; then remove
  the old key. It covers moving off `AGENTICORG_VAULT_KEY` or
  `AGENTICORG_SECRET_KEY` (and what else that key signs), rollback at each step,
  checks for what the tools cannot see, and a local rehearsal. It lists what
  `scripts/deploy_cloud_run.sh` re-applies on each roll, including the public
  commerce discovery flag taken from the operator's shell; counts a roll as
  complete only once the older revisions have no instances, which is also the
  checkpoint cutoff; stops the rotation on a row rewrap cannot decrypt instead
  of rerunning it; and runs the maintenance job on the services' image. The
  `rewrap` and `verify_all` commands, the SQL checks and the rehearsal were run
  against a local database, and the deploy script against local stand-ins for
  `gcloud`; no `gcloud` step was run against Cloud Run.
- `docs/SECRETS_ROTATION.md` said no secret has a second read path and listed
  the vault only as the `AGENTICORG_SECRET_KEY` fallback. The vault keyring
  decrypts under every key it holds; the page now says so and links the runbook,
  as do `docs/deployment.md`, `docs/RUNBOOKS.md` and the documentation index.
  `docs/architecture.md` no longer says `encryption_key_ref` selects a key
  (nothing reads it) and names both KMS keys a GSTN password can be sealed
  under. `rewrap --help` gave exit code 2 for a missing keyring; it exits 1. It
  also failed to print on a console that cannot encode `→`.
- A regression test starts `uvicorn api.main:app`, the API image's command, with
  `AGENTICORG_ENV=production` and no vault key in its process environment, and
  requires it to exit non-zero with the refusal before it serves or reaches the
  database. The existing lifespan test only read the source.
- Rehearsing the runbook found three gaps, now FINDINGS A-82 to A-84: vault
  ciphertext outside the five registered columns (SSO client secrets, case push
  signing keys, governed-case excerpts, voice SIP settings) is invisible to
  `verify_all`, which calls a key unreferenced while those values still need
  it; rewrap can overwrite a credential written while it runs; and the secrets
  rotation workflow accepts the vault's own secrets. Migrating the scratch
  database also rewrote a committed migration audit record (A-85); the
  rehearsal sets `AGENTICORG_MIGRATION_AUDIT_DIR` to avoid it.

### Fixed - a governed-case agent can no longer be wired without its grant check
- The provider tool gateway refused every call it had no authorizer for, but
  `ProviderToolGateway`, `UnderwriterDependencies` and
  `DispositionDependencies` still defaulted `authorizer` to `None`, so any
  caller could build them without a grant check. `authorizer` is now a
  required field of all three, and outside local and test runtimes building
  any of them with `None` raises `AuthorizerRequiredError`
  (`core/tool_gateway/provider_gateway.py`). Tests that prove the gateway's
  own refusal pass the named `NO_AUTHORIZER_FOR_TESTS`; a regression test fails
  if production code passes it, `authorizer=None` or `authorizer_factory=None`.
- A `CaseRuntime` whose `authorizer_factory` returned `None` still moved the
  case to `in_progress` and started the underwriter, which then failed on its
  first provider call, and one built with `authorizer_factory=None` failed
  with a `TypeError` on its first run instead of a named refusal. Outside local
  and test runtimes a `CaseRuntime` can no longer be built without a factory:
  construction raises `AuthorizerRequiredError` (`core/cases/runtime.py`).
  `CaseRuntime.authorizer_for` refuses a missing factory or a `None` result
  with `authorization_unavailable` (status 503) before the case moves or a
  provider is built, for investigations and screening dispositions alike; a
  workflow `case_agent` step fails with that reason.
- `scripts/seed_governed_cases.py` passes `case_authorizer` to the runtime
  explicitly instead of relying on its default. No feature flag guards this
  change, because every production caller - the API, the workflow step and the
  seed - already supplies a real grant check (`case_authorizer`), so none of
  them behaves differently.
### Fixed - four stale production specs and a smoke test that could not fail
- TC-API-003 (`qa-module-19-health-api.spec.ts`) read the API version from
  `/openapi.json`, which the strict runtime does not serve (`api/main.py`). It
  now reads the version from `/api/v1/health` and, on hosted targets, checks
  that the origin under test does not serve the OpenAPI document (the console
  origin, unless `API_URL` points at the API).
- `tests/e2e/test_smoke.py::test_openapi_docs_accessible` passed only because
  the console answers `/docs` with its own page and a 200. It is replaced by
  `test_api_docs_are_not_published`, which checks the content of
  `/openapi.json`, `/docs` and `/redoc` on the public origin, and on the API's
  own origin when `AGENTICORG_E2E_API_URL` is set.
- D2 Promote/Rollback clicked the first fleet card, but the fleet page lists
  only the selected company's agents and the E2E tenant's agents have none.
  It now opens a shadow agent returned by `GET /api/v1/agents?status=shadow`
  (Promote is disabled on an active one) and requires Promote to be enabled.
- AGENT-CONFIG-003 created an agent with no connector, which has had no tools
  since bug sheet #46 (2026-09-14). It now links Zendesk, checks for a tool
  badge and deletes the agent afterwards instead of leaving one in the
  production tenant on every attempt. The agent is created paused, so a full
  shadow-agent budget cannot fail it.
- `qa-cafirms-may01.spec.ts` fell back to the shared suite token, whose tenant
  cannot see the tester's agent and connector. It now signs in only as the
  tester (`RU_TESTER_EMAIL` / `RU_TESTER_PASSWORD`, passed by the deploy
  workflow) and fails without them, unless the workflow has declared them
  unavailable, in which case it is skipped with a warning on the run.
### Fixed - the production Playwright suite logs in again before its session expires
- The post-deploy suite runs for about 90 minutes on one session token that
  lasts 60, so every test after the first hour failed with 401s that read as
  product failures (28 of the 33 failures on 2026-09-26). Specs now take
  `test` from `ui/e2e/helpers/test.ts`, whose fixtures log in again with
  `E2E_EMAIL` / `E2E_PASSWORD` when less than 15 minutes are left, and read
  `E2E_TOKEN` as a live export of `ui/e2e/helpers/auth.ts` instead of a copy
  taken when the spec loads. The deploy workflow passes the two credentials to
  the Playwright step.
- A token that has expired and cannot be renewed now fails every test with
  the reason, instead of producing assertion failures. A failed login is
  retried at most once every 15 seconds across all workers of the run.
- Each renewed token is masked in the Actions log, and a new workflow step
  revokes every session of the E2E user (`/auth/logout-all`) before the
  Playwright artifacts are uploaded; if it cannot, the upload is skipped. The
  suite's own global teardown ends the demo accounts' sessions the same way
  when `E2E_REVOKE_DEMO_SESSIONS=1`, so the workflow names no demo account.
  Production runs of the suite are serialised, since that revocation would
  break a second run in progress. The default Playwright config now collects
  `*.spec.ts` only.
- `sop-flow.spec.ts` and `video-recordings.spec.ts` passed without a session:
  they accepted a 401 as a validation error, or the login page as a loaded
  page. They now require the expected `400`, and `expectSignedIn` checks for
  the signed-in layout.
### Fixed - DSAR erasure no longer fails on the append-only audit log
- Every `POST /api/v1/dsar/erase` failed with a 500. Erasure rewrote
  `actor_id` on the subject's `audit_log` rows, and the `audit_log_immutable`
  trigger rejects every UPDATE and DELETE on that table. The CI schema is
  built without the trigger, so the Postgres test passed.
- Erasure now leaves audit rows unchanged and reports them as retained under
  GDPR Art. 17(3)(b) (`audit_log_retained`, `audit_log_retention_basis`). The
  user record is still anonymised and feedback pseudonymised. The result no
  longer carries `audit_log_pseudonymised`; no erase request ever completed
  with it.
- A database error while processing a DSAR request is now persisted as
  `failed`. Before, the error aborted the transaction, so the `failed` status
  could not be written and the caller got an unrecorded 500.
### Changed - one engineering guide for every contributor
- `AGENTS.md` is the repository's engineering guide, and the other guide file
  in the root carries the same text, so every contributor and tool works from
  one set of rules. It adds the hard rules (no tool attribution in commits,
  branches or docs; vendor-neutral provider interfaces; house terminology; no
  new public exposure; synthetic data; placeholder secrets) to the existing
  conventions.
- The guide no longer points at the removed `helm/` directory or at version
  strings in `api/main.py` and `api/v1/health.py` (the API reads its version
  from `pyproject.toml`), and its preflight suite list includes
  `tests/contract/` (FINDINGS A-28).

### Fixed - the production Playwright suite no longer runs local-stack specs
- `ui/e2e/regression.config.ts` ran every `*.spec.ts` against production,
  including the specs owned by `dev-stack.config.ts` and
  `decision-grants.config.ts`, which need the dev stack's seeded data and
  dev-only secrets. `decision-grants.spec.ts` throws at load without its
  variables, so the post-deploy run collected no tests and failed. The
  regression config now ignores those specs (722 tests in 78 files remain), and
  a test fails if another config's spec is not ignored.

### Fixed - the post-deploy suite checks the AP Processor's real PineLabs tools
- `tests/e2e/test_cxo_flows.py` still required `check_order_status`, a name no
  connector registers (the PineLabs tool is `get_order_status`) and which was
  removed from the defaults on 2026-09-15, so the manual post-deploy run failed
  before its synthetic and browser steps. It now requires `create_order` from
  the PineLabs connector and that every AP Processor default is registered.

### Added - agents can be granted route scopes
- Route scope checks apply to agent tokens, but registration only ever gave an
  agent tool scopes, so an agent token was refused on every scoped route. A
  human tenant admin can now grant an agent named route scopes with
  `PATCH /agents/{id}` `{"route_scopes": [...]}`; they are added to the agent's
  Grantex registration so a grant issued to it can carry them.
- Only canonical route-family scopes are accepted (never `agenticorg:admin` or
  an alias; `422`); only an active human tenant admin, checked against the user
  row at request time, may set them (`403`); only on a shared agent registered
  on Grantex (`403` for a personal agent, `409` for an unregistered one).
  Grantex is updated before anything is stored, an explicit `route_scopes`
  PATCH always rewrites the registration so drift can be repaired, and every
  change is audited.
- Route scopes are stored apart from tool scopes, so run grants and delegated
  grants never carry them. A tools PATCH and the scope backfill keep them on
  the registration.
- Existing agents have none until an admin grants them; until then an agent
  token keeps getting `403` on scoped routes, as since the route-scope fix.

### Fixed - API keys that were administrators stay administrators
- Revision `v6z29_admin_scope_compat` gives an API key the exact
  `agenticorg:admin` scope - the access it had before admin became an exact
  match - when it holds a colon-delimited admin sub-scope such as
  `agenticorg:admin:full`, was created before the exact-match fix was merged
  (2026-09-25 05:58:53 UTC), and its owner is still an active administrator.
  Keys issued later (even where the fix was deployed afterwards), keys whose
  owner is not an active admin, and look-alikes such as
  `agenticorg:administration:read`, `agenticorg:adminx` or the dot form are not
  changed: those still lose admin as described for the exact-match fix below,
  and its audit query still finds them. Status and other scopes are unchanged;
  a second run changes nothing; the ids of the keys changed are logged.
- The revision also works for a migration role without BYPASSRLS, and refuses
  to run with a tenant context set, which would hide other tenants' keys.
- Creating an API key with a scope that looks like admin but is not exactly
  `agenticorg:admin` (a sub-scope, the dot form, another case or surrounding
  space) is refused with `422`, naming the scopes and the exact one to use.
- Deploy with `--with-migrations` so the revision runs before the new code
  serves traffic.

### Changed - live feed, LLM failover and connector readiness are bounded and truthful
- **Tenant live feed:** subscriptions are single-flight per tenant with bounded
  fanout, a reconnect loop and a subscribe timeout; browsers catch up through
  paginated history. Long-lived connections recheck their credential
  periodically: a revoked, expired, re-scoped or other-tenant API key, or a
  session whose subject or scopes changed, is disconnected (1008), and an auth
  backend error disconnects with 1013 rather than keeping the socket. A socket
  that stalls a delivery is closed (1013) so its client reconnects and catches
  up instead of staying "live" on heartbeats. A subscribe timeout releases the
  Redis connection it opened. No operational writer publishes to this feed yet,
  so the dashboard keeps its audit poll.
- **`LLMRouter.complete`:** bounded by `AGENTICORG_LLM_COMPLETE_TIMEOUT_SECONDS`
  (default 90s), with `AGENTICORG_LLM_PRIMARY_TIMEOUT_FRACTION` (default 0.7) for
  the primary. Only timeouts, connection errors, 429 and 5xx fall back; invalid
  requests, configuration errors and spend caps never do. An explicitly selected
  model, which agent runs normally pass, falls back only to a fallback model from
  the same provider, so an outage never moves a request to another provider.
- **Connectors:** list and detail return tenant- and company-scoped readiness
  evidence (credential presence, health freshness, disabled/missing states)
  without selecting or returning encrypted credentials, and the UI pages past 50
  rows. Replacing a connector's credentials clears its last health check, and a
  token refresh that revives a connector which was not healthy clears it too, so
  the list does not show "Recently checked" for credentials no check has used. A
  health check still does not prove provider scopes, contracts or sync.
- Public status, README, landing and resilience docs describe these limits.

### Fixed - the credential vault no longer falls back to a published key
- Outside a local, dev, development, test or CI runtime, the credential vault
  now refuses its code default (`dev-only-vault-key`), any secret value
  published in this repository (the code defaults, `.env.example`, the compose
  files, the `Makefile`, CI and scripts; compared ignoring case and surrounding
  whitespace), and a blank key. It needs `AGENTICORG_VAULT_KEYRING`, or
  `AGENTICORG_VAULT_KEY`, or as before `AGENTICORG_SECRET_KEY`, in the process
  environment. Before, a production runtime whose secrets were only in `.env`
  (loaded into settings, but not into the environment the vault reads) sealed
  every connector credential and LangGraph checkpoint under the default, which
  anyone with this repository can derive.
- An unset or unrecognised `AGENTICORG_ENV` counts as production here.
- The API refuses to start without a usable key. So does a worker: the check
  runs on Celery's `worker_init` in the main worker process and exits, and the
  Cloud Run entrypoint (`scripts/run_worker.py`) checks before it starts its
  health server. The checkpointer reports `checkpoint_encryption_key_missing`.
- Refusals and keyring parse errors name the setting and the entry's position
  or id, never key material. Before, an entry missing its `id:` prefix (which
  is the raw key) was quoted in the error.
- `Settings` in a strict runtime now refuses every published placeholder as
  `AGENTICORG_SECRET_KEY`, not only `dev-only-secret-key`.
- A keyring that is set but has no entries is refused in every runtime
  instead of falling through to the single-key path. A blank
  `AGENTICORG_VAULT_KEY` now counts as unset (the next fallback applies), and
  a keyring entry with no key material (`v1:`) is refused. Before, both
  derived the key from the empty string, which is public.
- **Breaking for operators:**
  - A strict runtime without a vault key in its process environment no longer
    starts; set `AGENTICORG_VAULT_KEYRING` (see `docs/deployment.md`). CI's
    background Celery worker now sets `AGENTICORG_ENV=ci`, and local
    development must export `AGENTICORG_ENV=development` (see the README).
  - A runtime that ran on the default has credentials sealed under a public
    key. Strict runtimes refuse the default even as a keyring entry, so rewrap
    those rows from a trusted machine with `AGENTICORG_ENV=local` and
    `AGENTICORG_VAULT_KEYRING=v2:<new>,legacy:dev-only-vault-key`, then rotate
    every affected provider credential.
  - Rows sealed under an empty-string key (a blank `AGENTICORG_VAULT_KEY`, or
    a `v1:` entry) can no longer be decrypted in any runtime. Treat those
    provider credentials as exposed and re-enter them.
- The `AGENTICORG_SECRET_KEY` fallback is unchanged; FINDINGS A-71 tracks
  giving every deployment a dedicated vault key.

### Fixed - approval policies cannot be satisfied by one person
- A reviewer may vote once per approval item, across every step of its
  policy. Before, the duplicate-vote check covered only the current step, and
  a step's count resets when the item advances, so one senior user could
  approve each step in turn and decide a multi-person policy alone. A second
  vote now gets `409`.
- A policy step whose condition cannot be evaluated - a field the item does
  not carry, a non-numeric ordering comparison, an unparseable expression -
  now applies instead of being skipped, so the item needs that step's
  approvals. So does a malformed operand (`status ==`, an unterminated
  quote). `NOT` over a missing field no longer counts as a match.
- If an item's policy is deleted mid-approval, another policy now resolves
  for it, or the step it is waiting on is removed, decisions on the item get
  `409` and it stays pending, instead of being decided on the next single
  vote with no policy applied.
- A reviewer is matched on every identifier their session carries (user id,
  subject and email), so an invite-acceptance session and a login session for
  the same person count as one reviewer.
- **Breaking for operators:** items whose policy conditions name fields their
  context lacks now need those steps' approvals. Someone who voted at one step
  cannot approve or reject at a later one, so a policy that needs the same
  person twice cannot complete. Editing (deleting and recreating) a policy, or
  adding one that now resolves instead, leaves items in flight under the old
  one refusing every decision until they expire; there is no override yet
  (FINDINGS A-67). See `docs/approval-policies.md`.

### Fixed - agent tokens need a route's scope, like API keys
- **Breaking:** route scope checks now apply to Grantex agent tokens. Before,
  any credential other than a user session or an API key skipped them, so an
  agent token granted only `tool:mock:read` could read the whole tenant audit
  trail and run any agent. An agent token must now carry the route family's
  scope (`agents:read`, `agents:run`, `audit:read`, ...) or `agenticorg:admin`,
  or it gets `403`. An authenticated request with an unrecognised
  authentication mode is refused the same way.
- A tenant admin grants an agent the route scopes its token needs with
  `PATCH /agents/{id}` `route_scopes` (see "Added - agents can be granted route
  scopes" above). A2A and MCP routes are unaffected.
  See `docs/operations/grant-enforcement.md`.

### Fixed - three external changes that broke CI on every pull request
- `make check`: SQLAlchemy 2.1.0 was released. The tools image and the
  production API image both install `pyproject.toml`'s ranges, so mypy ran
  against 2.1.0 and failed on five unchanged files, and the next API image
  would have shipped 2.1.0 untested. The range is capped below 2.1, which
  keeps both on the 2.0 line `requirements.txt` pins (FINDINGS A-70).
- `make dev`: quay.io removed the MinIO server image the stack pinned, and
  Docker Hub's `minio/minio` now needs credentials. Both compose files use
  `cgr.dev/chainguard/minio`, pinned by digest. It defaults to uid 65532,
  which cannot open a `miniodata` volume the old image wrote as root, so it
  runs as root as that image did and existing volumes keep working. The
  air-gap image list follows.
- UI container scan: CVE-2026-93990 in `libexpat` 2.8.4-r0, which every
  current `nginx:alpine` digest still ships. `Dockerfile.ui` and
  `Dockerfile.ui.cloudrun` - the image Cloud Run serves, which CI did not
  scan - upgrade it to 2.8.5-r0 and fail the build if that version is
  unavailable. The container scan now covers `Dockerfile.ui.cloudrun` too.

### Security - admin is the exact `agenticorg:admin` scope, never a prefix
- Six admin checks accepted any scope *starting with* `agenticorg:admin`.
  Every agent is registered with `agenticorg:{domain}:read` and its domain is
  free text, so a grant token for an agent in a domain such as
  `administration` passed `require_scope` - including the tenant-admin gate
  on API-key creation - and was treated as an administrator for agent,
  connector and approval visibility, report schedules, admin-only RPA
  scripts and merchant commerce configuration. Free-form API-key scopes had
  the same effect. All six now use `core.rbac.has_admin_scope`, an exact
  match, and a test fails if any production module matches the admin scope
  by prefix again.
- **Breaking:** an API key or grant holding a scope such as
  `agenticorg:admin:full` is no longer an administrator. Nothing in this
  repository issues such a scope, but API-key scopes are free-form. To find
  any in use:
  `SELECT tenant_id, id, name FROM api_keys WHERE EXISTS (SELECT 1 FROM unnest(scopes) s WHERE (s LIKE 'agenticorg:admin%' OR s LIKE 'agenticorg.admin%') AND s <> 'agenticorg:admin');`
  Replace such a scope with `agenticorg:admin` if the key should be an
  administrator. Revision `v6z29_admin_scope_compat` does this for keys with a
  colon-delimited sub-scope that predate the fix and belong to an
  administrator; the query still finds the rest.

### Fixed - governed-case provider authorization
- The reference underwriter and screening agent now refuse every provider call
  without an authorizer and a positive delegated-grant check. This applies even
  when general grant enforcement is off or in warn mode. Tenants must register
  one active, shared agent for each role before enabling governed cases; cases
  without that configuration fail closed rather than making unchecked calls.

### Added - the governed-case decision is proven end to end
- `make e2e-decisions` (`ui/e2e/decision-grants.spec.ts`) takes a real
  four-eyes decline across both systems in a browser, with nothing stubbed:
  the console asks for the decision, two different people sign in on the auth
  service's own approval page with a second factor and approve there, the same
  person is refused the second approval, and the console records the decision
  with both approvers and both grant ids. It also proves a single approval with
  step-up, and that a case which changed after the approval is refused with
  `case_changed`. No decision grant reaches the console's browser, and the
  suite fails if one does.

### Fixed
- A consumed decision is recorded with the decision grant each approver spent.
  The issuer lists the approvers and, separately, the grant ids it consumed,
  and those two arrays cannot be paired by position: `jtis` is in the order the
  grants were presented and `approvers` is in approval order, so for a
  four-eyes decision they can disagree and index pairing would put one person's
  approval against the other's credential. The pairing is now taken from the
  issuer's own record of the request, where the grant and the approver are
  stated together, and any approver that does not match exactly one approval -
  or that resolves to a grant the issuer did not say it consumed - is refused
  with `decision_service_response_invalid`. Before this the client read a grant
  id the issuer does not send, recorded an empty one, and the case document
  schema rejected it: the first real four-eyes decision could not be recorded
  at all.

### Added - the development stack serves decision grants
- The pinned Grantex auth-service image moves to a build of Grantex `main`
  (`5b867f68`, `ghcr.io/mishrasanjeev/grantex-auth-service@sha256:b73668a3...`),
  the first one that serves `/v1/decisions/...` and the approval page (PRD
  G-3). `docker-compose.dev.yml` configures it: `DECISION_GRANTS_ENABLED`, a
  vault key, an `ADMIN_API_KEY` and a step-up policy, plus an identity provider
  for approvers only (`oidc-approvers`, two fixture people, separate from the
  console's development SSO). The auth service now listens on the port it
  publishes, because its approval page has to be reached on the same origin it
  checks form posts against, and that origin must be https or loopback.
- **A stack that does not ask for decision grants runs without any of it.**
  `AGENTICORG_DEV_DECISION_GRANTS=true` turns on `DECISION_GRANTS_ENABLED` and
  the relaxed outbound rules the development identity provider needs, and the
  provider itself is in the `decisions` compose profile, so `make dev` does not
  start it. Without the opt-in the decision routes and the approval page answer
  404 and no administrator key is configured.
- **`AGENTICORG_CASE_DECISION_SERVICE` still defaults to off**, in the
  development stack included; a run opts in with
  `AGENTICORG_DEV_CASE_DECISION_SERVICE=grantex`. Nothing changes for a stack
  that does not.
- `scripts/dev_stack_smoke.sh` checks the approval page and the approver
  identity provider's discovery document.

### Fixed
- The development OpenID Connect stub reads a chunked request body. Node's HTTP
  client sends a POST body with `Transfer-Encoding: chunked` when no
  `Content-Length` is set, which the stub read as an empty form and answered
  with an OAuth error about the wrong thing. It also logs the error it returns,
  and its health check honours `OIDC_STUB_PORT`.
- `.gitattributes` keeps shell scripts LF, so the stack's Linux containers can
  run them from a Windows checkout (FINDINGS A-60).

### Changed — breaking for tenants that turn it on
- `grants.enforce_closed` can now be set to `deny` (tenant flag
  `grants.enforce_closed.deny` or `AGENTICORG_GRANTS_ENFORCE_CLOSED=deny`).
  **This will refuse tool calls that have been quietly succeeding:** scope
  enforcement was effectively off on the common path, so an agent without a
  resolvable grant, or with one that does not cover a tool, now fails the
  run instead of calling the tool. Run the tenant in `warn` first and review
  `scripts/grant_enforcement_report.py`. A refused call fails the run with
  `error` `grant_denied: <reason>`; run results gain an optional
  `grant_denial` object, which `POST /agents/{id}/run` also returns and
  writes to its audit row. Default stays `off`; nothing changes unless a
  tenant or the deployment opts in. Rollback: disable the tenant's deny flag
  or reset the deployment default. See the runbook in
  `docs/operations/grant-enforcement.md`. Agents registered before this
  release carry scopes Grantex's check cannot satisfy: run
  `scripts/refresh_grantex_scopes.py --apply` before switching their tenant
  to `deny`.

### Added
- Prometheus metrics are now readable. Every service (API, Celery worker, beat)
  serves the registry at `GET /metrics` on `METRICS_PORT` (default 9090), a
  port Cloud Run does not route, so the endpoint has no external surface and no
  credential to manage; a service refuses to start the listener on the port it
  serves traffic on. The worker exports in multiprocess mode, because Celery's
  prefork pool means counters are incremented in forked children. Until now
  every instrument in the codebase was write-only (FINDINGS A-56).
- The six PRD §10 alerts and one companion, as committed definitions. This
  makes PRD §10 achievable, not met: no sample has yet travelled the whole path
  from a process to a notification. Files:
  `monitoring/prometheus/agenticorg-alerts.yml`, unit-tested with `promtool
  test rules` in CI, with the Cloud Monitoring policies in
  `infra/terraform/monitoring/` built from that same file and a dashboard in
  `monitoring/dashboards/`. `scripts/check_alert_rules.py` refuses a rule that
  reads a raw counter (instances scale to zero, so a counter's value depends on
  which are alive), a rule without a `for`, or a rule reading an instrument not
  declared in `observability/alert_contract.py`.
- `agenticorg_case_decision_dwell_seconds{dwell_source,approval_stage}`: the
  dwell the issuer's approval page measured, recorded when a decision is
  recorded. This is the authoritative figure the rubber-stamping alert reads;
  the console's own render-to-submit metric remains advisory telemetry and is
  never read by an alert.
- `agenticorg_chain_verifications_total` (cited passages and promotion-history
  chains verified against their digests) and
  `agenticorg_budget_cap_events_total` (spend caps warned and exhausted).
- Two more promtool fixture files (fire and no-fire for all seven alerts, and
  the exact `for` boundaries), and two probes:
  `scripts/probe_metrics_multiprocess.py`, which proves a forked child's
  metrics reach the exporter and runs in CI, and `scripts/probe_alert_gate.py`,
  which mutates the alert definitions and checks the gate catches each one.

### Removed
- `scripts/generate_batch2.py`, `generate_batch3.py`, `generate_batch4.py` and
  `generate_batch5.py`. They were the original scaffold for 107 paths, 85 of
  which exist today and have years of hand editing behind them - `api/main.py`,
  `api/deps.py`, `auth/jwt.py`, `auth/grantex.py`, `auth/scopes.py`,
  `observability/metrics.py`, `observability/alerting.py`, `core/agents/*`,
  `audit/*`, `scaling/*`. Each was written with a bare `open(path, "w")` and no
  guard, nothing referenced them, and the metrics they would restore carry
  per-tenant, per-agent labels that `observability/metrics.py` forbids. Git
  holds the day-one version of every generated file, and how it changed since,
  which the scripts cannot.
- The `budget_pct_high` threshold rule and the Grafana "Budget Utilization"
  panel, with the `agenticorg_agent_budget_pct` gauge behind them. The gauge
  has never been given a value by anything, so the rule could not fire and the
  panel could not draw: both were reporting on a metric that does not exist.
  Restoring them means writing the gauge first, with labels that are not
  per-tenant and per-agent.

### Fixed
- `docs/deployment.md` told operators to `curl http://localhost:8000/metrics`,
  which has never served anything.

### Security — breaking for machine callers of the governed-case routes
- Deciding, withdrawing, reviewing a screening disposition and approving an
  information request on a governed case now need a human session. API keys
  and Grantex agent tokens are refused with 403 `human_session_required`.
  Previously any caller with the `approvals:write` scope could take those
  actions, and agent tokens skipped the RBAC scope family check entirely, so
  an agent token issued for tool access could withdraw a case or approve an
  information request — and the action was recorded as `user:<the token's
  subject>`, which read like a person. The actor written onto a case is now
  derived from the session and says what acted: `user:<id>`, `api_key:<prefix>`
  or `agent:<id>`. A disposition review and an information-request approval
  additionally need the case to be `awaiting_decision`. Identities are still
  never read from the request body. Submitting a case and starting an
  investigation stay open to machine callers, recorded under their own labels.
  Rollback: revert this change; there is no flag, because leaving the routes
  open is the defect. See `docs/governance/case-lifecycle.md` and FINDINGS
  A-46 for the separate question of which roles should hold `approvals:write`.

### Security — breaking for anyone already receiving provider webhooks
- Inbound provider webhooks for governed cases no longer act on a delivery
  they cannot verify, and are bound to one tenant. Previously an unsigned or
  forged body posted to the unauthenticated route was used as a trigger: a
  subject reference was read out of it and every matching case awaiting a
  decision was re-investigated, which replaced the memo and cleared the
  analyst's screening-disposition reviews, and could fail the case outright if
  the provider was briefly unreachable. Now an unverifiable delivery is
  recorded and counted only (nothing is read from the body, no case is looked
  up, no investigation runs), and the route has moved to
  `POST /api/v1/webhooks/providers/{tenant_id}/{provider}/{path_token}` with an
  unguessable per-tenant path token, so an event signed with a provider's
  shared secret cannot be replayed at another tenant's inbox. A re-query
  triggered by a verified event records that event on the `in_progress`
  transition, and a case that was awaiting a decision returns to
  `awaiting_decision` with the memo it had (transition reason
  `re_evaluation_failed:<reason>`) instead of failing when the provider cannot
  be reached. Every refusal answers the same 202, and the body is read under
  the 256 KiB cap before any database work. **Action required:** re-point each
  provider at the tenant's new path, read from
  `GET /api/v1/case-push/provider-webhook-inbox?provider={provider}` (tenant
  admin); the old tokenless path is gone and answers 404. Rotating
  `AGENTICORG_SECRET_KEY` changes every tenant's path. New counter outcome
  `agenticorg_provider_webhook_receipts_total{outcome="unbound"}`.

### Added
- Citations that a reviewer can check end to end (PRD A-6, `core/tool_gateway/provider_gateway.py`,
  `core/cases/runtime.py`, `api/v1/governed_cases.py`, `ui/src/components/governed-cases/MemoView.tsx`).
  The tool gateway now captures the record each piece of provider evidence is attached to, exactly
  as it arrived; the underwriting memo attaches every excerpt reference its evidence cites (before,
  `memo.excerpts` was empty on every case, so every citation read "not attached to this memo"), and
  the case keeps the passages in `governed_cases.excerpts`.
  `GET /governed-cases/{case_ref}` gains `tool_calls` (each provider call with the provider and
  the record ids it returned) and `excerpts` (references only), and
  `GET /governed-cases/{case_ref}/excerpts/{excerpt_ref}` serves one passage, decrypted and
  re-hashed: a passage that no longer matches the digest the memo cites is refused
  (`excerpt_integrity_failed`), never shown. `DELETE /governed-cases/{case_ref}/excerpts` forgets
  the passages and keeps the references, and a case keeps at most its 200 most recent passages,
  newest capture per reference winning. The console shows a passage on request, marks a citation
  whose record the run never returned (provider and record id together), and says plainly when a
  case carries no tool calls to check citations against. Passages are stored encrypted with the
  tenant's key in `governed_cases.excerpts_encrypted` (migration `v6z28_case_excerpts`, additive
  and forward-only). Closes FINDINGS A-48.
- Decision requests for governed cases (PRD G-3, `core/cases/decision_requests.py`,
  `POST /api/v1/governed-cases/{case_ref}/decision-requests`,
  `GET .../decision-requests/{request_id}`): AgenticOrg asks the Grantex auth service for a
  decision on one semantic action, records the request on the case and reports the live status of
  the approvals; the person approves only on the auth service's own approval page, which handles
  sign-in, step-up, the four-eyes rule and the authoritative dwell measurement.
  `POST .../decision` accepts `decision_request_id`, fetches the minted grants server-side (a
  decision grant never reaches the browser) and consumes them for the exact action and the case's
  current version through `CaseRuntime.decision_verifier`, so a case that changed after the
  approval is refused with `case_changed`. `client_dwell_ms` on both routes is advisory telemetry
  only (`agenticorg_case_console_dwell_seconds{stage}`), never the authoritative dwell. Off by
  default: without `AGENTICORG_CASE_DECISION_SERVICE=grantex` decision requests answer
  `decision_service_not_configured` and every decision is still refused with `decision_required`.
  New metrics `agenticorg_case_decision_requests_total{outcome,result}` and
  `agenticorg_case_decision_grants_consumed_total{outcome,result}`; new documentation
  `docs/governance/decision-requests.md`. Migration `v6z27_case_decisions` adds
  `governed_cases.decision_requests` (additive, forward-only). The issuer's answers are parsed
  strictly - a field the console states as fact (the action and its hash, the case version, how
  many approvals are required, each approval's subject, authentication, position and dwell
  source) is refused when absent rather than defaulted - the semantic action is bound to the
  tenant as well as the case, `GRANTEX_BASE_URL` must be set explicitly when the service is on,
  and consumption runs with its own shorter deadline while the case row is locked. A case change
  registers the new version with the issuer, so it supersedes an open request and revokes unused
  grants; an issuer answering `404 NOT_FOUND` for a request it no longer holds is reported as
  `decision_request_not_found` rather than as the service being switched off. Registering a case
  version is counted in `agenticorg_case_version_announcements_total{result}`, so an issuer that is
  persistently unreachable is visible rather than only logged.
- Approvals console screens for governed cases (PRD A-9, `ui/src/pages/GovernedCases.tsx`,
  `ui/src/pages/GovernedCaseDetail.tsx`): a queue at `/dashboard/approvals/cases` with the
  state counts, policy tier and proposed recommendation, and a case screen with the cited
  underwriting memo (every evidence entry naming the provider, upstream record, field and
  retrieval time, linked to the cited-records index; an excerpt reference is linked when the
  memo carries the excerpt and labelled as not attached when it does not, which today is every
  case - see FINDINGS A-48), the
  policy score with every fired rule and the evidence values it read, and the case history.
  Sections the provider could not supply are shown as unchecked rather than clear. The
  screens are read-only: no decision, review or state change is made from them. A tenant
  without `governed_cases.enabled` is told so instead of seeing an empty queue.
- Screening disposition review in the approvals console
  (`ui/src/components/governed-cases/ScreeningDispositions.tsx`, PRD §3 US-3): each hit shows
  the list entry it concerns, the proposed outcome, the confidence band as metadata, the
  rationale, the per-identifier comparison table (name, date of birth, nationality, address,
  associated entities) and the cited evidence. An analyst accepts the proposal or overrides it
  with a different outcome and a mandatory written reason; the analyst identity comes from the
  session, a review is written once, and the form appears only while the case is awaiting a
  decision. Nothing closes a screening hit.
- The decision action in the approvals console
  (`ui/src/components/governed-cases/DecisionPanel.tsx`, PRD A-9): request a decision, open the
  decision-grant issuer's own approval page in a new window, watch the approvals arrive and record
  the decision once the grants exist. The console has no approve control at all - step-up, the
  dwell measurement and four eyes happen on the issuer's page - and "Record decision" stays
  disabled until the approvals are complete. The four-eyes state names the first approver and says
  the same person will be refused; a case that changed after the approval, a missing issuer and
  every refusal reason (`decision_required`, `decision_not_approved`, `same_approver`,
  `case_changed`) are surfaced with their code. An outcome that differs from the memo's
  recommendation needs a written reason, which is shown to the approver. The console's own dwell
  (case screen render to submit) is sent as advisory telemetry only; the authoritative dwell is the
  one the approval page measured, and it is shown per approval. A request that can never be
  approved (the case changed under it, or the issuer reports it superseded, cancelled or expired)
  stops the polling, drops the Record control and puts the request form back, so asking for a new
  decision is always possible; the approval page is opened only over https, except on a console
  served over http (a development stack).
- `make seed-cases` (`scripts/seed_governed_cases.py`): development-only sample governed
  cases - turns `governed_cases.enabled` on for the seeded tenant, submits one case per mock
  provider fixture and runs the reference agents against the stack, writing the new case
  references for the browser suite. It refuses any runtime that is not development or test.
- Browser end-to-end coverage for the console screens (`ui/e2e/governed-cases.spec.ts`), with
  an axe accessibility scan (WCAG 2.1 A and AA) and a phone-width pass; the run regenerates
  the screenshots in `docs/console/governed-cases.md`.
- Governance documentation (`docs/governance/README.md`): how grants, policy
  scores and human decisions interact for governed cases - read-only tool sets
  and the grant check at the tool gateway, deterministic policy tiers that
  gate the recommendation while model confidence stays metadata, decisions
  recorded only with verified decision grants, analyst reviews and approved
  information requests, the signed hand-off, and what is recorded for audit.
  Links the case lifecycle, hand-off, agent and security pages.
- Portable case hand-off for governed cases (`core/cases/push.py`, PRD A-8):
  a `case_push` event is written to `case_push_outbox` in the same transaction
  as the case change (`case.completed`, `case.updated`, `case.decided`) and
  delivered to the tenant's configured endpoint with an HMAC-SHA256 signature
  over `<event id>.<timestamp>.<body>` in `AgenticOrg-Signature`
  (`v1=<key_id>:<hex>`, one entry per key so keys rotate without downtime),
  retried with exponential backoff and dead-lettered on rejection or after 10
  attempts; dead letters replay with the same event id. REST retrieval of the
  same document. Inbound provider webhooks at
  `/api/v1/webhooks/providers/{tenant_id}/{provider}/{path_token}` are verified
  with the provider, de-duplicated by event id, and only ever trigger a
  re-investigation from the provider; unverifiable bodies are recorded and
  counted and change nothing (see the security entry above). Signing keys
  are stored encrypted per tenant. Migration `v6z26_case_push` (three tables,
  forced RLS). Celery tasks `dispatch_case_pushes` and `sweep_case_pushes` (the
  sweep is a no-op unless `AGENTICORG_CASE_PUSH_SWEEP_ENABLED=true`). Metrics
  `agenticorg_case_push_{enqueued,deliveries,dead_letters}_total`,
  `agenticorg_case_push_dead_letter_backlog`,
  `agenticorg_case_push_attempt_duration_seconds` and
  `agenticorg_provider_webhook_receipts_total{outcome}`; alert
  `case_push_dead_letters_present`. Requires `governed_cases.enabled` and a
  configured endpoint. See `docs/governance/case-hand-off.md` and the runbook.
- Governed business cases (`core/cases/`, PRD A-8), behind the per-tenant
  flag `governed_cases.enabled` (default off; unreadable counts as off): case
  lifecycle `submitted`/`in_progress`/`awaiting_decision`/`decided`/
  `withdrawn`/`failed` with version-guarded transitions recorded in
  `governed_case_transitions`; a runtime that runs the Business Onboarding
  Underwriter and Screening Disposition agents for a case and stores the memo,
  policy result, screening results, dispositions and agent case records;
  decisions recorded only with verified decision grants (the shipped verifier
  refuses everything with `decision_required` until decision grants land).
  New workflow step type `case_agent` and example workflows
  `workflows/examples/business_onboarding.yaml` and `screening_disposition.yaml`.
  New API under `/api/v1/governed-cases` (submit, list, stats, detail, case
  record, investigate, withdraw, decision, disposition review, information
  requests). Migration `v6z25_governed_cases` adds two tables with row-level
  security. Metric `agenticorg_governed_case_transitions_total{from_state,to_state}`.
  Settings `AGENTICORG_CASE_PROVIDER`, `AGENTICORG_CASE_POLICY_DIR`,
  `AGENTICORG_CASE_LLM_MODEL`. See `docs/governance/case-lifecycle.md`.
- Screening Disposition reference agent (`core/agents/screening_disposition/`,
  PRD A-7 / US-3): for one screening hit it re-screens the subject through
  the provider tool gateway to confirm the hit, compares name, date of birth,
  nationality, address and associated entities, and proposes `true_match`,
  `false_positive` or `insufficient_information` with a confidence band by
  fixed rules, producing a schema-valid, cited `screening_disposition` with
  `review: null`. The model writes the rationale from comparison results only;
  a rationale that states another outcome, talks of closing the hit or repeats
  untrusted text is replaced by a template rationale. No automatic closure in
  any configuration: the tool set is read-only screening and no closing tool
  can be configured. `apply_review` records an analyst's acceptance or
  override (reason required, analyst identity from the authenticated session,
  written once). Metric
  `agenticorg_screening_dispositions_proposed_total{outcome,band}`. Shared
  `core/agents/case_model_call.py` makes the guarded, pseudonymised prose call
  for both reference agents. Nothing in the platform runs the agent yet. See
  `docs/agents/screening-disposition.md`.
- Business Onboarding Underwriter reference agent
  (`core/agents/business_underwriter/`, PRD A-7): resolves an application
  through a verification provider, starts verification and polls while it is
  pending, reconciles ownership against declared owners (`missing_owner`,
  `undeclared_owner` at a configurable threshold, 25% by default), screens
  every party, analyses web presence only through the sandboxed extractor,
  evaluates the deterministic policy and assembles a schema-valid, cited
  `underwriting_memo` with a policy-gated recommendation and a missing-items
  list. The model writes section summaries only, from codes and counts, behind
  the untrusted-content guard and (when `pseudonymisation.pre_model` is on)
  pseudonymisation; each summary is checked against its section's citations.
  Every memo evidence entry is checked against the records returned in the run,
  and the run fails closed otherwise. A capability the provider does not offer
  yields a `not_available` section. Prompts are versioned and pinned by
  SHA-256, recorded with every tool call's request and response hashes in the
  case record. Requests for more information use approved templates released
  only by a human approval bound to the proposal digest. New provider tool
  gateway (`core/tool_gateway/provider_gateway.py`) holds read tools only and
  takes the run's grant check as an authorizer that fails closed. Metrics
  `agenticorg_provider_calls_total{capability,outcome}`,
  `agenticorg_provider_call_duration_seconds{capability}` and
  `agenticorg_case_agent_runs_total{agent,outcome}`. New
  `core.policy.document.policy_result_document`. Nothing in the platform runs
  the agent yet, so existing behaviour is unchanged. See
  `docs/agents/business-underwriter.md`.
- **Coverage gate (pull requests):** 75% of changed lines (diff-cover) and 75%
  of every new Python module (`scripts/check_new_module_coverage.py`; a new
  module no test imports counts as 0%), on top of the existing 55% total and
  per-module floors. Tests, test doubles and fixtures are not counted, renamed
  modules count as new, and a coverage report whose filenames cannot be
  attributed to exactly one module fails the gate. Runs as
  `make coverage-gate` and in the new Local Stack workflow. **Break:** pull requests that add or change Python code below
  these floors now fail.
- Local Stack workflow: `make check`, and `make dev && make test` followed by
  the coverage gate, `make seed` and `make e2e`, on fresh runners for every
  pull request and push to `main`.
- `pip-audit` now runs through `scripts/run_pip_audit.py` in CI, nightly and
  `make check`, with dated, owned exceptions in
  `config/pip-audit-exceptions.toml` (at most 90 days; expired or malformed
  entries fail). A dependency pip-audit could not audit fails too unless a
  `[[skip]]` entry accepts it.
  See "Dependency audit exceptions" in `CONTRIBUTING.md`.
- Nightly Cassette Re-record workflow: re-records every `model_cassette` test
  against a live model with the `MODEL_RECORD_API_KEY` secret and reports
  cassette differences without gating. Its dependencies are hash-pinned
  (`requirements-rerecord.lock`). See `docs/testing/record-replay.md`.
- Local Grantex in the development stack: the Grantex auth service from its
  published image, pinned by digest, with its own `grantex` role and database
  on the stack's Postgres (created once by `grantex-db`) and Redis index 2.
  The API and worker use it through `GRANTEX_BASE_URL` and a seeded
  development `GRANTEX_API_KEY`. The smoke test checks its health and keys and
  that the API container reaches it with the configured key. See "Local
  Grantex" in `docs/quickstart-local.md`.
- OpenAI-compatible model stub in the local stack (`model-stub`,
  `tools/model_stub`): `POST /v1/chat/completions` with tool calls, answered
  from scripted sequences (`scripted/<name>`, ids matching the in-process
  scripted model) or from cassettes keyed and stored by `core/model_replay.py`.
  Options that change the answer (`tool_choice`, `response_format`, `seed`, ...)
  are part of the key and unknown request fields are rejected. Replay misses
  are errors and are never forwarded; record mode forwards to a
  real provider and refuses to start without `MODEL_RECORD_API_KEY`. The API
  and worker send `vllm:` models to it, and the agents `make seed` creates use
  `vllm:scripted/final-only`, so agents run locally without model credentials.
  Refuses to start outside development and test. See "Model stub" in
  `docs/quickstart-local.md`.
- `make seed` (`scripts/seed_dev.py`): an idempotent development tenant with
  Approver A and Approver B (the OIDC stub's identities, matched by email), a
  disabled `dev-oidc` sign-in configuration for the stub, two sample agents in
  shadow mode with no tools, and a two-step sequential approval policy (it does
  not require distinct approvers). Fixed ids make repeated runs a no-op; a
  conflicting existing row fails the run without writes; it refuses
  production-like runtimes and non-local database hosts unless
  `AGENTICORG_SEED_ALLOW_REMOTE_DB=1`. Optional
  `AGENTICORG_SEED_PASSWORD` enables email sign-in. See "Development data" in
  `docs/quickstart-local.md`.
- Development OpenID Connect provider in the local stack (`oidc-stub`,
  `tools/oidc_stub`): discovery, JWKS, authorization code with mandatory PKCE,
  token and userinfo endpoints, and step-up through `acr_values`, `max_age`
  and `prompt=login`, with `acr`, `amr` (`["pwd"]` or `["pwd", "hwk"]`) and
  `auth_time` in its tokens. Seeds Approver A and Approver B from
  `tools/oidc_stub/config.dev.json`. Refuses to start unless `AGENTICORG_ENV`
  is development, local or test. Sessions last eight hours, repeated request
  parameters are rejected, and step-up clients must send `max_age`. `tools/` is
  excluded from the API image. See "Development identity provider" in
  `docs/quickstart-local.md`.
- Vendor-name denylist: `scripts/check_denylist.py` fails a change whose added
  lines, file paths, commit messages, branch name or pull request title and
  description name a denylisted verification, identity-data or screening
  vendor. Terms are matched through salted SHA-256 hashes in
  `config/denylist.sha256` (80 terms; the plain list is not committed, though
  the salted hashes are not secret), independent of case, spacing, punctuation
  and a term glued to the end of a word. Runs in the new Vendor Denylist workflow
  and in `make check`; `audit` checks the whole tree. See "Vendor-neutral
  names" in `CONTRIBUTING.md`.
- `make test`, `make check` and `make e2e`. `make test` runs the unit and
  contract suites with the 55% coverage floor, then the integration and
  regression suites against the local stack's Postgres and Redis in a separate `agenticorg_test`
  database that is recreated each run (the development database is never
  touched). `make check` runs ruff, mypy, bandit, gitleaks, the licence-header
  check, JSON Schema validation of `schemas/` and pip-audit. Both run in a new
  `agenticorg-tools` image (`Dockerfile.tools`, Python 3.12) so only Docker
  and make are needed; `RUNNER=local` uses a local interpreter. `make e2e`
  runs the new `ui/e2e/dev-stack.config.ts` Playwright suite against the
  running stack in the official Playwright image. See "Tests and checks" in
  `docs/quickstart-local.md`.

- Untrusted content extractor (`core/extraction/`): websites, registry
  documents and applicant uploads are parsed in a separate worker process
  with no network access and a wall-clock limit (on Linux a seccomp filter is
  required by default; an audit hook, resource limits and a network namespace
  are added where available) and return typed, length-capped,
  character-class-constrained fields only. Excerpts are stored separately and
  cited by `excerpt_ref`. Timeouts, crashes, oversized or off-schema output
  fail closed with a reason code and no fields. `build_model_context` renders
  evidence for a model with untrusted text replaced by references, and the
  new optional `build_agent_graph(context_guard=...)` stops a run before any
  model call that would carry untrusted text. Metrics
  `agenticorg_extraction_total{kind,outcome}` and
  `agenticorg_extraction_duration_seconds{kind}`. Nothing calls the extractor
  yet and `context_guard` defaults to `None`, so existing behaviour is
  unchanged. PDF and office documents are refused, not parsed. See
  `docs/security/untrusted-content.md`.
- Deterministic case policy engine (`core/policy/`): versioned YAML policies
  evaluated over a case's evidence fields into a tier (`low` < `medium` <
  `high` < `blocked`), a score and ordered reasons naming the rules that fired,
  with the policy version, file hash and inputs recorded in every result. No
  model is involved and model confidence is never an input. Policies load
  strictly and fail closed at load with a reason code; missing evidence moves
  a case towards the stricter tier. A policy can only be marked `production`
  with `reviewed_by`, and loading an example policy logs a warning. Ships
  `business_onboarding_us` and `business_onboarding_uk` **examples, which
  require a compliance owner's review before any real use**. Metrics
  `agenticorg_policy_evaluations_total{tier,policy_status}` and
  `agenticorg_policy_load_total{outcome,reason}`. Nothing calls the engine
  yet, so existing behaviour is unchanged. See `docs/policies/authoring.md`
  and ADR 0011.
- Secret scanning with gitleaks 8.30.1 on every pull request, every push to
  `main` and weekly over the full history, plus a pre-commit hook and a
  `scripts/preflight.sh` step (`SKIP_SECRETS=1` to skip). See "Secret
  scanning" in `CONTRIBUTING.md`.
- New source files must carry `SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0` in their
  first five lines; enforced on pull requests and in `scripts/preflight.sh`.
  Existing files are unaffected. See "Licence headers" in `CONTRIBUTING.md`.
- Container scanning of both the API and console images on every pull
  request, push to `main` and nightly (previously the API image only, nightly
  only), with a pinned Trivy 0.74.0, dated exceptions in `.trivyignore.yaml`
  and a CycloneDX SBOM artifact per image. See "Container scanning" in
  `CONTRIBUTING.md`. The console image's seven base-image `libuuid` findings
  are tracked in `FINDINGS.md` with exceptions expiring 2026-10-14.
- One-command local stack: `make dev` builds and starts Postgres, Redis,
  MinIO, migrations, the API, the worker and the console from
  `docker-compose.dev.yml` (base images pinned by digest, ports bound to
  127.0.0.1, no credentials needed), waits for health and runs a smoke test.
  `make down`, `make clean`, `make logs` and `make ps` manage it. See
  `docs/quickstart-local.md`.
- `ScriptedChatModel` and the `scripted_model` test fixture: fixed tool-call
  sequences for testing agent graph mechanics (tools, interrupts, resume)
  without model text. A script that is overrun, calls an unbound tool or is
  left partly unused fails the test. See `docs/hermetic_test_doubles.md`.
- Record and replay for model calls (`AGENTICORG_MODEL_MODE` =
  `live`/`record`/`replay`) covering LangGraph agents and `LLMRouter`
  completions. Cassettes are keyed by a hash of the rendered request, so a
  prompt, tool or tool-output change misses loudly instead of replaying stale
  text; replay never falls back to a live call and both non-live modes are
  refused outside local and test runtimes. Tests opt in with the
  `model_cassette` fixture and replay by default in CI. Production behaviour
  is unchanged when the variable is unset. See
  `docs/testing/record-replay.md` and ADR 0008.
- Connectors and agents can ship as separate packages through the
  `agenticorg.connectors` and `agenticorg.agents` entry-point groups
  (`agenticorg.workflows` is discovered and rejected until its registry
  exists). Off by default
  (`AGENTICORG_PLUGIN_LOADING`); only distributions in
  `AGENTICORG_PLUGIN_ALLOWLIST` are imported; native implementations keep
  priority; every rejection is logged with a reason and counted in
  `agenticorg_plugin_load_total`. See `docs/providers/plugin-packages.md`.
- Agent runs paused for human approval can be checkpointed in Postgres
  instead of process memory: `AGENTICORG_LANGGRAPH_CHECKPOINTER=postgres`
  (default `memory`, unchanged behaviour). A new migration
  (`v6z22_langgraph_checkpoints`) creates the LangGraph checkpoint tables;
  they are not created at runtime. Checkpoint data is encrypted with the
  credential-vault keyring and bound to its thread, so a blob copied into
  another thread is refused; no channel value is stored in plaintext. With
  the Postgres store selected and unreachable, its schema missing or stale,
  its keyring malformed, or an unverified checkpoint library installed, the
  API refuses to start and agent runs fail (the run endpoint returns
  `503 agent_checkpoint_store_unavailable`); nothing falls back to memory.
  Celery workers open the store on their first agent run, so a store outage
  fails those runs but never stops a worker from starting. Refusals are
  counted in `agenticorg_checkpointer_unavailable_total` by reason. A keyring
  change needs a restart of the API and workers.
  `core.langgraph.checkpointer.delete_tenant_checkpoints` removes a tenant's
  checkpoints for offboarding. Adds `psycopg[binary]` 3.3.5 and `psycopg-pool`
  3.3.1 as direct dependencies and pins `langgraph-checkpoint-postgres` 3.1.2
  and `langgraph-checkpoint` 4.2.0 exactly (previously `>=3.1.2` and
  unpinned).
- Agent runs checkpoint under a server-generated thread id prefixed with the
  run's tenant (`tenant:<tenant id>:run:<random>`). A run paused for approval
  records that thread on its approval row (`hitl_queue.checkpoint_thread_id`,
  migration `v6z23_hitl_checkpoint_thread`, with a check constraint that the
  thread belongs to the row's tenant). The thread id is never returned by the
  API or accepted from a request, and resuming a thread outside the caller's
  tenant is refused (`checkpoint_thread_tenant_mismatch`).
- Approving a paused standalone agent run can resume it from its checkpoint,
  behind the per-tenant feature flag `approvals.resume_agent_runs` (default
  off; decisions behave as before). With the flag on, an `approve` or `reject`
  decision resumes the run in the background under the approval's tenant,
  using the parameters recorded when the run paused. The outcome is recorded
  in the approval's `context.checkpoint_resume`, in an `agent.run.resumed`
  audit event and in `agenticorg_agent_run_resumes_total{outcome}`, and a
  finished run's checkpoints are deleted. Any other decision leaves the run
  paused; an approve or reject left paused because the flag is off or cannot
  be read is logged (`agent_run_resume_skipped`) and counted as
  `outcome="skipped"`. A resume is refused, with a reason code, when the checkpoint is
  missing, not at the approval gate, undecryptable or outside the tenant.
  Approval responses gain `context.checkpoint_resume` for resumed runs; the
  resume parameters stored with a paused run are never returned. See
  "Agent runs paused for approval" in `docs/RUNBOOKS.md` for the flag,
  reason codes and checkpoint retention.
- Governed-case domain schemas (JSON Schema 2020-12, versioned `$id`s):
  `business_case`, `ownership_graph`, `screening_result`,
  `screening_disposition`, `policy_result`, `underwriting_memo` and
  `case_push`, with shared definitions in `common`. Every memo section and
  finding cites `evidence[]` of `{provider, record_id, field, retrieved_at,
  excerpt_ref}`. `core/domain_schemas.py` validates documents and fails closed
  with a reason code. A new `tests/contract/` suite, added to the CI unit job
  and `scripts/preflight.sh`, validates every fixture in `schemas/examples/`,
  fails on a fixture without a schema, and checks that documentation code
  examples match the tests they come from. The schemas are not seeded into
  tenant schema registries. See `docs/schemas/domain-schemas.md`.
- `VerificationProvider` (`connectors/framework/verification_provider.py`):
  one provider-neutral interface for business resolution, verification,
  ownership, person and business screening, web presence and monitoring.
  Providers declare a `Capability` set and callers degrade an undeclared
  capability to `not_available` (`call_capability`); verification is
  start-and-poll with `Pending` as a value; every I/O method takes a
  `Deadline`; errors form a closed taxonomy with reason codes; webhook
  verification returns `None` for anything unverifiable. Typed domain values
  serialise to the published schemas. Providers register in
  `connectors/providers/registry.py`, and plugin packages add them through the
  `agenticorg.providers` entry-point group (still behind
  `AGENTICORG_PLUGIN_LOADING` and the allowlist; natives keep priority). See
  ADR 0009 and `docs/providers/plugin-packages.md`.
- The `mock` verification provider (`connectors/providers/mock`), registered
  natively: twelve synthetic US and UK businesses covering clean cases, a
  missing and an undeclared owner, probable false-positive and true-match
  screening hits, a dissolved company, a thin file with no registry match, and
  adversarial text in website copy, a company name and a screening alias.
  Configurable latency, failure injection and pending polls, deterministic
  under a seed; HMAC-signed webhook events (including company dissolved) with
  recorded genuine and forged deliveries. It runs in-process or as a separate
  HTTP service with a client provider; `make dev` now starts it as
  `mock-provider` (host port `AGENTICORG_DEV_MOCK_PROVIDER_PORT`, default 8081;
  fault-injection and event endpoints only with
  `AGENTICORG_DEV_MOCK_PROVIDER_ADMIN=true`) and points the API and worker at
  it. It runs only when `AGENTICORG_ENV` is explicitly local, development or
  test; elsewhere the registry neither lists nor creates it. See
  `docs/providers/mock-provider.md`.
- Provider conformance suite, published in the full distribution as
  `agenticorg.testing.provider_conformance` (source: `testing/provider_conformance`).
  A provider package subclasses `ProviderConformanceSuite` and supplies a
  `ConformanceTarget`; twelve checks cover identity, capability honesty,
  pending-then-result, expired and overrun deadlines (including polls),
  cancellation, the error taxonomy, webhook verification including forged
  payloads, webhook replay protection (stale deliveries, stable event ids),
  pagination of candidates and monitor alerts, idempotency and schema
  conformance, each failing with a readable reason. `strict=True` turns a
  skipped check into a failure. The mock provider passes strictly in-process
  and over HTTP; deliberately broken providers fail each check. Documentation code examples are extracted from tests. See
  `docs/providers/writing-a-verification-provider.md`.
- Python and TypeScript SDK `0.4.0` resources for knowledge/OCR, voice, RPA,
  local bridges, connector diagnostics, workflow cancellation, and the
  seller/buyer commerce runtime.
- An idempotent migration that repairs the native `knowledge_documents` index
  on both legacy and ORM-bootstrap installations.
- Recognisers for United States SSN, ITIN and EIN, United Kingdom National
  Insurance and Companies House numbers, European VAT numbers and IBANs
  (`core/pii/international_recognizers.py`), alongside the Indian ones. IBANs
  must pass mod 97 and VAT numbers their national check digits where the
  scheme has one (17 country prefixes); shapes that are otherwise ordinary
  numbers are only recognised next to a label such as "SSN" or "company
  number". Used by pre-model pseudonymisation (below).
- Pseudonymisation before the model, per tenant behind the flag
  `pseudonymisation.pre_model` (off by default). Names, dates of birth,
  addresses and identifiers are replaced with placeholders such as
  `[[PERSON_1:3fa9c2]]` before every model call on both model paths
  (LangGraph agents and `LLMRouter`), system prompt included, and restored
  inside the tool boundary so connectors receive the real values. A value
  keeps its placeholder for the run and its resumes; the case is always the
  server-generated run id, never a `case_id` from a request, and only
  placeholders issued into the run's own conversation are restored. The map is
  stored encrypted per tenant in the new `case_pseudonym_maps` table (migration
  `v6z24_case_pseudonym_maps`, additive, row-level security). Fails closed: if
  the flag or the map cannot be read, or the map cannot be written, no model
  call is made; a tool call whose placeholder cannot be restored is refused
  with `E1012 pseudonym_restore_failed` and audited instead of being sent. New
  metrics `agenticorg_pii_pseudonymised_total{entity_type}`,
  `agenticorg_pii_pseudonym_restore_refused_total{reason}` and
  `agenticorg_pii_pseudonymisation_unavailable_total{reason}`. New
  `core.feature_flags.is_enabled_strict`, which raises on a failed lookup
  instead of returning the default. With the flag off behaviour is unchanged.
  See `docs/security/pseudonymisation.md`.
- HITL conditions can be checked when they are saved
  (`AGENTICORG_HITL_CONDITION_VALIDATION` = `off`/`warn`/`reject`, default
  `off`). Agent create, replace, update, generate-and-deploy and SOP deploy
  answer `422 invalid_hitl_condition` with a reason code (`syntax_error`,
  `unsupported_syntax`, `unsupported_operator`, `not_a_comparison`) in
  `reject`; `warn` accepts the condition but logs and counts it. Parse
  failures at save and at run time are counted in
  `agenticorg_hitl_condition_parse_failures_total` (labels `stage`, `reason`,
  `outcome`). **Break when set to `reject`:** a bare label such as
  `needs_review` must be written as a comparison (`needs_review == True`).
  See `docs/hitl-conditions.md`.
- `scripts/check_prompt_tools.py` fails CI (unit-tests job) and
  `scripts/preflight.sh` when a built-in agent prompt calls a tool that no
  connector registers or that is not in that agent's default tools, or when a
  default tool list names an unregistered tool. It runs with no baseline and
  fails closed if the registry cannot be loaded. See "Prompt tool references"
  in `CONTRIBUTING.md`.
- Grant enforcement modes for agent tool calls (`grants.enforce_closed`:
  `off`, `warn`, `deny`). The mode is the strictest of
  `AGENTICORG_GRANTS_ENFORCE_CLOSED` (default `off`, which keeps today's
  behaviour) and the global and tenant rows of the
  `grants.enforce_closed.warn` / `.deny` flags, each read on its own. In
  `warn`, runs from `POST /agents/{id}/run` and other callers of the LangGraph
  runner resolve a grant per run — the caller's token, the agent's configured
  token, or one the token pool now mints by delegating from
  `GRANTEX_ROOT_GRANT_TOKEN` to the agent's registered Grantex agent (cached
  per tenant, agent and scope set, refreshed before it expires, at most one
  mint per key per process) — and every tool call that grant would deny still
  runs (a token supplied by the caller or configured on the agent stays
  enforced as before) but is logged as `grant_enforcement_would_deny` and
  counted in `agenticorg_grant_enforcement_denials_total{mode,reason}` with
  the Grantex SDK's reason (exact messages of the pinned 0.5.x SDK mapped to
  the Appendix B reasons until the Grantex 0.6 SDK with reason codes is
  published), including runs with no grant at all
  (`grant_missing`). If the flag table cannot be read and the process has no
  recent mode for the tenant, the run falls back to the strictest mode.
  See `docs/operations/grant-enforcement.md`.
- The Grantex SDK pin moves from `grantex==0.5.0` to `grantex==0.5.1`
  (amount and malformed-cap checks in `enforce`; no API change).
- **Break:** the authority flags (`grants.enforce_closed.*`,
  `pseudonymisation.pre_model`, `approvals.resume_agent_runs`,
  `decisions.required`, `caps.enforce`) can no longer be created, changed or
  deleted through `/api/v1/feature-flags`; the API answers
  `403 flag_key_reserved`. Platform operators manage them with
  `scripts/authority_flags.py`.
- **Break (Python API):** `core.langgraph.agent_graph.build_agent_graph` and
  every `build_*_graph` builder in `core/langgraph/agents/` take a required
  keyword `run_grant`, so a graph can no longer be built with grant
  enforcement silently left off.
- **Break (Python API):** `core.langgraph.tool_adapter.execute_agent_tool` and
  `core.tool_gateway.gateway.ToolGateway.execute` take a required keyword
  `run_grant` as well. `BaseAgent` passes its run grant in every mode; tests
  that exercise only the legacy checks pass
  `auth.run_grants.NO_RUN_GRANT_FOR_TESTS`, which production code may not use.
- Grant enforcement now covers every agent run entry point: chat, A2A and
  MCP (the run agent's grant; a caller Grantex token issued to another agent
  must also allow every call), voice and
  per-type wrappers through the runner, `resume_agent`, and workflow agent
  steps, collaboration steps, workflow resume and the sales pipeline through
  `BaseAgent` and the tool gateway. Workflow `connector_tool` steps have no
  agent and therefore no grant: recorded in `warn`, refused in `deny`. In
  warn and deny the tool gateway runs the grant check and all of its legacy
  checks. `off` is unchanged.
- A caller Grantex token is bound on every route that starts a run:
  `POST /agents/{id}/run`, `POST /workflows/{id}/run` (every step type that
  calls tools, and sub-workflows) and the sales pipeline routes, as well as
  chat, A2A and MCP. A run started with a caller token and resumed later
  without it refuses tool calls in warn and deny
  (`grant_missing`/`caller_token_unavailable`); only the caller's agent id is
  stored, never the token.

### Fixed
- Storing a governed case's cited passages refuses a passage for which no
  tenant key was resolved (`excerpt_key_unresolved`) instead of falling back to
  the deployment's legacy key. Callers pass `None` for "not resolved"; `""`
  remains the legacy key and encrypts as before.

- Task code called outside a worker no longer runs on the Celery runner loop.
  `core.tasks.async_runner.run_async` kept one event loop per process, which is
  right in a worker — it is the only loop there — but an API process that
  reached a task body from a thread with no running loop (an `asyncio.to_thread`
  call, of which `api/` has two dozen) left shared-pool connections bound to a
  loop no request runs on, and the next request to check one out failed. The
  choice is now made by the role of the process: a worker or beat process
  (marked by the Celery signals, or by `AGENTICORG_WORKER_PROCESS=1`) keeps the
  persistent loop; everywhere else the work runs through
  `core.database.run_db_coroutine_sync` on a private engine.
- The shared database pool now reports when it is used from a second event
  loop: when a session is opened there (which covers a caller that binds
  `async_session_factory` itself and is handed a warm connection), when the
  pool opens a connection there, and when it hands an existing one out.
  It binds to the first loop that uses it and logs `db_cross_loop_use` once per
  foreign loop with the remedy, counting
  `agenticorg_db_cross_loop_checkouts_total{mode}`, instead of leaving the
  `'NoneType' object has no attribute 'send'` that used to surface in an
  unrelated request later. `AGENTICORG_DB_CROSS_LOOP_GUARD=raise` turns it into
  a `CrossLoopConnectionError` at the point of the mistake and `off` silences
  it; warn is the default in production, where the call fails either way and
  raising would turn latent pool problems into new 500s. A test run counts the
  guard's trips and fails when they exceed the committed
  `cross_loop_baseline.txt` (54 trips, measured in CI, roughly 27 distinct
  uses — one use trips the guard once or three times, averaging about two: the
  session check sees every violation and the two pool hooks fire together on
  about half of them; the unit job trips it 0 times), and `scripts/check_cross_loop_baseline.py` refuses a raised baseline.
  Both overrides remain deliberate and silent-free: `AGENTICORG_CROSS_LOOP_BASELINE`
  replaces the number for one run and `AGENTICORG_DB_CROSS_LOOP_GUARD=off`
  stops the counting, and a run with the guard off says so on its summary line, so the existing debt (FINDINGS
  A-58) burns down and a new violation fails immediately.
- `AGENTICORG_WORKER_PROCESS=1` is set on the Celery worker and beat
  entrypoints and in the development stack, so a worker started with
  `--pool=solo`, `threads` or gevent — which never fires
  `worker_process_init` — is still recognised as a worker. See `RUNBOOKS.md`. The live feed, workflow state, workflow event-wait and bridge
  state stores resolve their session factory per call rather than caching the
  shared one, so a synchronous caller's private engine reaches them too.
- Storing a governed case's cited passages no longer opens a database session
  per passage while the case row is locked. The tenant's key is resolved once,
  before the write session (`core.cases.excerpts.tenant_key`), and each passage
  is encrypted with it off the event loop; the retention bound is applied
  first, so a capture larger than the bound does no key work for the passages
  it is about to drop.
- Two migrate jobs started together no longer race on an empty database:
  `migrations/env.py` takes the same transaction-scoped advisory lock
  `init_db()` uses before deciding whether to build the baseline, and rechecks
  the recorded revision after acquiring it. A database holding only views,
  materialised views or sequences counts as occupied (the check reads
  `pg_class`, not just the tables), so a bare `alembic upgrade` refuses it
  with `unmanaged_database_not_empty` instead of creating a baseline beside
  them.
- Encrypted-column migrations write their audit record to
  `AGENTICORG_MIGRATION_AUDIT_DIR` when it is set, so a test run no longer
  rewrites the committed records under `migrations/audit/`.
- A synchronous credential lookup no longer breaks the next request. An
  asyncpg connection belongs to the event loop that opened it, and
  `get_provider_credential_sync` ran the resolver on a throwaway loop while
  using the shared pooled engine: the lookup itself failed with "got Future …
  attached to a different loop" — reported as a credential that "could not be
  decrypted", which also refuses the platform fallback — and the connection it
  left in the pool made an unrelated later request fail, which `pool_pre_ping`
  does not catch (a cross-loop error is not a disconnect). Synchronous entry
  points now run their coroutine through `core.database.run_db_coroutine_sync`,
  on a private `NullPool` engine (built on first use and disposed with the
  loop): the credential resolver, the report generator's KPI bridge
  (`core/reports/generator.py`, reached when the sandbox pilot runs the report
  task body inside its own loop), the weekly-report pilot-proof writer and the
  CDC store's sync helpers. The LangGraph credential prefetch stays as defence
  in depth and one fewer connection per model build.
- The example onboarding policies read only evidence the Business Onboarding
  Underwriter produces, and only registry statuses the provider interface can
  return. `business_onboarding_uk` dropped `filings_overdue`: it read
  `verification.overdue_filings`, which no provider field supplies, so it fired
  as indeterminate on every UK case and added a tier and score on nothing. Both
  examples now match `in_insolvency` for an insolvent registry record instead
  of `liquidation` (UK) and `revoked` (US), neither of which is a
  `RegistryStatus` member, so an insolvent business can reach `blocked`.
  `verification.overdue_filings` is gone from the evidence mapping; a
  filings-overdue signal has to reach the provider interface, the mock provider
  and the conformance suite first (FINDINGS A-51). Dropping a `medium` rule
  can make a case less strict, which `docs/policies/authoring.md` treats as a
  major change: UK example `2.0.0`, US example `1.1.0`.
- The declared-activity vocabulary is published
  (`core.agents.business_underwriter.facts.DECLARED_ACTIVITY_CATEGORIES`, see
  `docs/policies/authoring.md`) and the mock provider's website fixtures are
  HTML documents with a title and a heading, as a real site is. The extractor
  classifies a page's activity from its title, meta description and headings,
  so before this the observed activity was empty for every fixture and
  `web_presence.activity_mismatch` never resolved. It now resolves for seven of
  the twelve fixtures.
- Agents are registered on Grantex (and re-scoped on `PATCH /agents/{id}`)
  with `tool:{connector}:{read|write|delete|admin}:{tool}` scopes from the
  connector's Grantex manifest instead of `...:execute:...`, which Grantex's
  permission check never satisfies, so grants delegated for them allowed
  nothing. `scripts/refresh_grantex_scopes.py` re-scopes agents registered
  before (report only by default; `--apply` updates Grantex, then only
  `config.grantex.grantex_scopes`; deleted agents are skipped and a failure
  for one agent is reported without stopping the run).
- `PATCH /agents/{id}` updates a registered agent's scopes on Grantex before
  storing them, and refuses the change with a `reason_code` when Grantex
  cannot take them (`grantex_update_failed`, `grantex_unconfigured`,
  `scope_computation_failed`, or `scope_limit_exceeded` above 100 scopes).
  Previously it stored scopes the registration did not have.
- The token pool delegates only the stored scopes the agent's Grantex
  registration also carries.
- The token pool refreshes agent tokens by delegating from the root grant
  (`grants.delegate`) instead of an OAuth grant type the Grantex auth service
  does not serve.
- `alembic upgrade head` works on an empty database. It stopped at the first
  revision (`v400_apex`, which alters tables from the pre-Alembic SQL files)
  with `function uuid_generate_v4() does not exist`, so only
  `scripts/alembic_migrate.py` could build a fresh database. `migrations/env.py`
  creates the ORM baseline and stamps `v480_baseline` first, and the wrapper
  now uses that one path instead of its own copy.
- Only `alembic upgrade` bootstraps an empty database. `alembic current` (and
  any other command with no revision argument) raised
  `KeyError: 'destination_rev'`, and `alembic stamp <revision>` on an empty
  database created all 93 ORM tables before writing the version row. The
  environment now recognises the upgrade command itself. An upgrade of a
  database that has tables but no Alembic revision, or of an empty database to
  a revision before the baseline or to a relative target, is refused with a
  reason code instead of failing part-way.
- The integration suite runs `alembic upgrade head` on an empty database, and
  on a database already at head, and fails on any schema difference from the
  ORM models not listed in
  `tests/integration/alembic_schema_drift_allowlist.py`. Migrations stay
  forward-only; `migrations/README.md` documents the bootstrap, the refusals
  and the downgrade limits.
- Four shipped industry-pack agents no longer send every run to human review.
  Their HITL conditions were bare labels (`high_value_or_complex_risk`,
  `high_value_or_fraud_indicator`, `cancellation_or_major_endorsement`,
  `high_value_procurement`) that name no output key, so the fail-closed
  evaluator triggered on each run. They are now expressions over the keys in
  each prompt's output format; see `docs/hitl-conditions.md` for the
  thresholds. Agents already installed keep the old label until the pack is
  re-synced.
- The console images (`Dockerfile.ui`, `Dockerfile.ui.cloudrun`) report
  healthy. Their Docker healthcheck probed `localhost`, which resolves to
  `::1` in the nginx:alpine base while nginx listens on IPv4, so the
  containers showed unhealthy while serving traffic and anything waiting on
  their health never proceeded. The probe now requests
  `http://127.0.0.1/health`.
- Knowledge deletion now retires native vector chunks as well as RAGFlow and
  document records, preventing deleted content from remaining searchable.
- Plural billing callback OpenAPI operations now have unique GET/POST IDs for
  generated SDK clients.
- Connector harness and voice integration fixtures no longer emit avoidable
  async/Pydantic deprecation warnings.
- Agent default tool lists (`_AGENT_TYPE_DEFAULT_TOOLS` and
  `_DOMAIN_DEFAULT_TOOLS` in `api/v1/agents.py`, and the agent generator's
  copy) no longer name 20 tools that no connector registers, such as
  `get_post_analytics`, `schedule_social_post`, `slack_send_message` and
  `search_content_fulltext`. Those names were never bound at run time but
  were shown in the tool picker, MCP/A2A discovery and generated agents.
  Nothing is added in their place. `seo_strategist` is left with no default
  tools, so `POST /mcp/call` for `agenticorg_seo_strategist` now answers 400
  "No tools configured" instead of running an agent with no tools.
- Default tools that several connectors register now name the connector the
  agent's prompt uses, so they no longer resolve to whichever connector was
  imported first: `create_issue` is `jira:create_issue` for `vendor_manager`
  and `facilities_agent` (it resolved to GitHub), `query` and
  `search_contacts` are `salesforce:` for `abm` (it resolved to QuickBooks and
  HubSpot), and `get_analytics`, `create_page`, `send_email`,
  `create_campaign`, `create_incident` and `get_compliance_notice` are
  qualified for the agents that name LinkedIn Ads, Confluence, SendGrid or
  Gmail, Mailchimp, ServiceNow or GSTN. Accounting and HRMS tools shared by
  interchangeable systems (`get_trial_balance`, `get_employee`, ...) stay bare
  and resolve through the connectors linked to the agent.
  `GET /agents/default-tools/{type}` keeps a qualified default only when its
  connector is linked, and Grantex scopes for a qualified tool now name that
  connector (`tool:jira:execute:create_issue`); an unknown connector never
  matches.
- **Behaviour change:** the finance agent prompts (`ap_processor`,
  `ar_collections`, `close_agent`, `fpa_agent`, `recon_agent`,
  `tax_compliance`) no longer tell the model to call tools that do not exist
  (`ocr_extract_invoice`, `gstn_validate`, `erp_get_po`, `erp_get_grn`,
  `erp_queue_payment`, `erp_get_ar_aging`, `erp_get_transactions`,
  `banking_api_get_transactions`, ...). Steps now use the agent's registered
  tools, or read the data from the task input and escalate to human review
  when it is not there: AP needs the extracted invoice, the GSTIN verification
  result and the PO/GRN details in the input; close needs sub-ledger balances;
  FP&A needs the budget; reconciliation needs the GL entries; tax compliance
  needs the period's transactions. AP no longer sends remittance advice,
  reconciliation proposes entries instead of posting them, and AR hands Day
  60+ contact to the collections team.
- **Behaviour change:** the HR agent prompts (`ld_coordinator`,
  `offboarding_agent`, `onboarding_agent`, `payroll_engine`,
  `performance_coach`, `talent_acquisition`) no longer tell the model to call
  tools that do not exist (`get_performance_data`, `okta_deactivate_user`,
  `okta_provision_user`, `jira_create_issue`, `get_okr_progress`) or that the
  agent does not have (`get_leave_balance`, `check_availability`). Steps now
  use the registered equivalents (`deactivate_user`, `provision_user`,
  `assign_group`, `create_page`, `get_employee`) or read performance data,
  OKR progress, leave balances, panel availability and the separation record
  from the task input and escalate to human review when they are missing.
  Actions with no tool (equipment requests, course enrolment, GitHub/Jira/
  Slack removal, data archival) are listed for the responsible team instead
  of being claimed as done.
- **Behaviour change:** the marketing agent prompts (`brand_monitor`,
  `content_factory`, `crm_intelligence`, `seo_strategist`, `social_media`) no
  longer tell the model to call tools that do not exist
  (`get_brand_mentions`, `ahrefs_get_keywords`, `get_contacts`,
  `ahrefs_get_rankings`, `get_post_analytics`). CRM scoring uses
  `list_contacts` / `search_contacts`; social listening uses
  `get_campaign_insights` and `list_channel_videos`; brand mentions, keyword
  research, rankings and post analytics are read from the task input, with
  escalation to human review when they are missing. The SEO strategist
  writes ticket-ready recommendations instead of claiming to create Jira
  tickets.
- **Behaviour change:** the `vendor_manager` and `support_triage` prompts no
  longer tell the model to call `sanctions_screen`, `gstn_validate` and
  `mca_get_company_data` (not registered) or `get_ticket` (not in the
  agent's tools). Vendor onboarding now requires the sanctions screening,
  GSTIN verification and company registry results in the task input and
  stops for human review when any is missing; SLA monitoring uses
  `search_issues` and `get_project_metrics`. Support triage reads the ticket
  from the task input and uses `apply_macro` / `update_ticket`.

## [4.0.0] — 2026-04-05

### Added — Project Apex (22 Features)
- **1000+ Integrations**: Composio SDK (MIT) connector expansion layer alongside 54 native connectors
- **Smart LLM Routing**: RouteLLM multi-model routing across 3 tiers (85% cost savings)
- **Pre-LLM PII Redaction**: Microsoft Presidio anonymizes Aadhaar, PAN, GSTIN, UPI before data reaches LLM
- **NL Workflow Builder**: Describe business processes in English, auto-generates workflow
- **Persona Builder**: Describe the employee you need, auto-generates full agent config
- **Explainable AI**: Plain-English decision explanations with Flesch-Kincaid readability scoring
- **Self-Improving Agents**: Feedback loop with thumbs up/down, automatic prompt refinement
- **Dynamic Re-planning**: Workflows adapt when steps fail — LLM generates alternative plan
- **Voice Agents**: LiveKit + Pipecat foundation with Whisper local STT, SIP telephony
- **Browser RPA**: Playwright-based automation for legacy web portals (EPFO, MCA, Income Tax)
- **Multi-Language**: Hindi (HI) + English (EN) with language picker in header
- **Content Safety**: PII leakage + toxicity + duplicate detection on generated content
- **Air-Gapped Deployment**: Ollama (CPU) + vLLM (GPU) local LLM, zero internet required
- **Hosted Tier**: Stripe (global) + PineLabs Plural (India) billing with Free/Pro/Enterprise plans
- **Microsoft 365**: Teams bot + Outlook/SharePoint/OneDrive via Composio
- **Multi-Agent Collaboration**: Parallel agent execution with merge/vote/first_complete aggregation
- **Support Deflection Agent**: 60%+ auto-resolution with RAG knowledge base + FAQ matching
- **Industry Packs**: Healthcare, Legal, Insurance, Manufacturing — one-click install
- **SOC2/ISO 27001**: 10-point compliance control framework with evidence package API
- **Enterprise Onboarding**: 4-week guided deployment playbook with milestone tracking
- **Real-Time CDC**: Webhook + polling change data capture with workflow triggers
- **Billing Portal**: Usage meters, plan management, invoice history, India INR pricing toggle

### Changed
- Agents: 35 → 50+ (industry packs + support deflection + voice)
- Tools: 54 → 1000+ (Composio MIT integration)
- Workflows: 15 → 20+ (NL-generated + adaptive replanning)
- Dependencies: composio-core, routellm, presidio-analyzer, presidio-anonymizer (all optional [v4] group)
- UI: react-i18next for multi-language, Billing page, enhanced Onboarding
- Backend tests: 1,662 → 1,931+ (269 new tests across 22 features)
- Playwright E2E: 342 → 345+
- Version: 3.3.0 → 4.0.0

## [3.3.0] — 2026-04-04

### Added — Scope Enforcement Fix (Grantex SDK v0.3.3)
- **Manifest-based scope enforcement**: Replaced keyword-based permission guessing (`check_scope()`) with Grantex SDK `grantex.enforce()` — offline JWT verification + manifest permission lookup in <1ms per tool call
- **`validate_scopes` graph node**: New LangGraph node between `should_use_tools` and `execute_tools` that enforces Grantex scopes on every tool call. Graph flow: `reason → validate_scopes → execute_tools` (was: `reason → execute_tools`)
- **53 pre-built Grantex manifests**: All connector tool permissions loaded at startup from `grantex.manifests.*` — no manual permission mapping needed
- **Custom manifest support**: Load additional manifests from `GRANTEX_MANIFESTS_DIR` directory (JSON/YAML)
- **JWKS cache warm-up**: Dummy `enforce()` call at FastAPI startup pre-warms the JWKS cache (~300ms) so first real tool call is <1ms
- **Scope Dashboard** (`/dashboard/scopes`): New page showing all agents' scope coverage, permission levels, denial rates, and aggregate stats
- **Enforce Audit Log** (`/dashboard/enforce-audit`): Real-time feed of all `enforce()` decisions with filters (denied only, by agent, by connector), CSV export, pagination
- **Permission badges in AgentCreate**: Tool selector shows READ/WRITE/DELETE/ADMIN permission badges; yellow warning banner for destructive (DELETE/ADMIN) tools
- **Scopes tab in AgentDetail**: New tab showing resolved Grantex scopes, permission levels, grant token status (active/expiring/expired), and enforcement log
- **Org chart scope narrowing**: Visual indicators showing scope reduction in delegation chains (e.g., "write → read")
- **ToolGateway Grantex integration**: `execute()` now accepts `grant_token` parameter and uses `grantex.enforce()` as primary enforcement; legacy `check_scope()` retained as fallback for HS256 tokens

### Changed
- **Grantex SDK**: 0.2.5 → **0.3.3** (adds `enforce()`, `load_manifests()`, `load_manifests_from_dir()`, 53 pre-built manifests)
- **`check_scope()` deprecated**: `auth/scopes.py` now emits `DeprecationWarning` — use `grantex.enforce()` instead
- **Permission hierarchy**: Enforcement now uses Grantex's manifest-defined hierarchy (`admin > delete > write > read`) instead of keyword-guessing (`process_refund` was misclassified as "read")
- **Security**: LangGraph agents can no longer bypass scope restrictions — `grant_token` is verified at graph level before any tool executes
- Backend tests: 1,633 → **1,662** (29 new scope enforcement tests: 18 unit + 4 integration + 3 E2E + 4 UI)
- Version: 3.2.0 → **3.3.0**

### Fixed
- **Critical security fix**: LangGraph tool execution path (`ToolNode → _execute_connector_tool()`) now enforces Grantex scopes — previously, `grant_token` in `AgentState` was never read during tool execution
- **`process_refund` misclassification**: Keyword-based `check_scope()` classified `process_refund` as "read" (no write keyword match); manifest-based enforcement correctly identifies it as WRITE
- **Revoked token bypass**: Revoked grant tokens now fail JWT verification at `validate_scopes` node — previously, tools were built from a static list and ignored token revocation

## [3.2.0] — 2026-04-02

### Added — Tier 1: Marketing Automation
- **Web Push Notifications**: One-tap approve/reject HITL decisions from browser push notifications (ServiceWorker + VAPID). Notification bell dropdown in dashboard header. Push permission toggle per user
- **Email Drip Engine**: Behavior-triggered email sequences — trigger on open, click, or time delay. Re-engage non-openers. Rescore leads after drip completion. New `email_drip_sequence` workflow template
- **A/B Testing**: Create campaign variants, auto-select winners by open rate or CTR, CMO override before sending to remaining audience. New `ab_test_campaign` workflow template
- **Email Webhooks**: SendGrid, Mailchimp, and MoEngage open/click tracking via inbound webhooks (`POST /webhooks/email/{provider}`). Events stored and linked to drip sequences
- **Intent Data Aggregation**: Bombora + G2 + TrustRadius connectors with weighted scoring (40/30/30) for account-level buying signals
- **ABM Dashboard** (`/dashboard/abm`): Target account management, intent heatmap, CSV upload, tier filtering, and one-click campaign launch. Endpoints: `GET/POST /abm/accounts`, `POST /abm/accounts/upload`, `GET /abm/accounts/{id}/intent`, `POST /abm/accounts/{id}/campaign`, `GET /abm/dashboard`
- **Wait Step**: Real time delays in workflows (was stub) — supports minutes, hours, and day-based delays
- **Wait-for-Event Step**: Pause workflow until email opened, link clicked, or form submitted. Used in `lead_nurture` template
- **3 new connectors**: Bombora (intent data API), G2 (buyer intent signals), TrustRadius (review + intent data)
- **4 new workflow templates**: `email_drip_sequence`, `ab_test_campaign`, `abm_campaign`, plus `lead_nurture` now has `wait_for_event` steps
- **Push notification endpoints**: `POST /push/subscribe`, `POST /push/unsubscribe`, `GET /push/vapid-key`, `POST /push/test`

### Changed
- Connector count: 51 → **54** (3 new intent data connectors)
- Tool count: 320+ → **340+** (12 new tools across Bombora, G2, TrustRadius)
- Workflow templates: 11 → **15** (4 new marketing automation templates)
- Marketing connector group: 16 → **19** (added Bombora, G2, TrustRadius)
- Backend tests: 1,196+ → **1,633**
- Frontend vitest: **93** tests
- Playwright E2E: 14 → **17** spec files
- CI E2E now runs against production on every merge to main
- Version: 3.1.0 → **3.2.0**

## [3.1.0] — 2026-04-02

### Added
- **7 new connectors**: GA4, MoEngage, NetSuite, WordPress, Twitter/X, YouTube, Mailchimp — all with real API endpoints from official documentation
- **8 new agents**: Treasury (cash management, sweep, forecast), Expense Manager (receipt OCR, policy enforcement, reimbursement), Rev Rec ASC 606 (performance obligation identification, revenue allocation, journal entries), Fixed Assets (depreciation schedules, impairment testing, disposal), Email Marketing (campaign creation, list segmentation, A/B testing), Social Media (scheduling, engagement monitoring, analytics), ABM (account targeting, intent signals, personalized outreach), Competitive Intel (competitor monitoring, pricing analysis, feature comparison)
- **CFO Dashboard** (`/dashboard/cfo`): Cash Runway, Burn Rate, DSO, DPO, AR/AP Aging (30/60/90/120+), P&L Summary, Bank Balances (via AA), Tax Calendar with filing deadlines
- **CMO Dashboard** (`/dashboard/cmo`): CAC by channel, MQLs/SQLs pipeline, Pipeline Value by stage, ROAS by Channel (Google/Meta/LinkedIn), Email Performance (open/CTR/unsub), Brand Sentiment trend, Content Performance
- **NL Query interface**: Cmd+K global search bar + slide-out chat panel with full conversational UI, agent attribution on every answer, and persistent chat history
- **Multi-company support**: company switcher in top nav for CA firms managing multiple client entities, isolated data per company, cross-company consolidated reporting, RBAC per entity
- **Scheduled Report Engine**: Celery beat scheduler with cron expressions, PDF/Excel output with branded templates, delivery to email/Slack/WhatsApp, Report Scheduler UI for create/manage/toggle/run-now
- **8 new workflow templates**: `month_end_close` (trial balance through close), `daily_treasury` (cash position, sweep, forecast, report), `tax_calendar` (deadline tracking, filing prep, DSC signing), `invoice_to_pay_v3` (OCR through payment execution), `campaign_launch` (brief through monitoring), `content_pipeline` (ideation through publish), `lead_nurture` (scoring through sales handoff), `weekly_marketing_report` (collect metrics, build report, deliver)
- **Report Scheduler UI**: create, manage, toggle on/off, and run-now scheduled reports from the dashboard
- **3 new blog posts**: month-end close optimization, honest ROI measurement framework, CFO story (200-person IT company)

### Fixed
- All 38 stub connectors rewritten with real API endpoints from official documentation — zero stubs remain
- **Tally**: fake REST replaced with proper XML/TDL protocol + bridge agent for remote on-premise instances (WebSocket tunnel, auto-reconnect, heartbeats)
- **Banking AA**: removed illegal payment tools, implemented full RBI-compliant consent flow (create consent, redirect, callback, FI session, fetch data). Connector is now read-only
- **GSTN**: fixed base URL + implemented real Adaequare 2-step authentication (POST /authenticate for session token) + DSC signing (PKCS#1 v1.5 RSA-SHA256 via cryptography library)
- **AP Processor**: wired to PineLabs Plural for actual payment execution (not simulated)
- ROI claims in marketing copy replaced with honest "measured during pilot" language throughout
- mypy errors resolved: grantex module typing, ChatAnthropic imports, LangGraph overload signatures
- bandit security scan clean: defusedxml for all XML parsing, nosec annotations for GAQL/SOQL query strings

### Changed
- Agent count: 27 → **35** (8 new specialist agents across Finance and Marketing)
- Connector count: 43 → **51** (7 new connectors, all with real endpoints)
- Tool count: 273 → **320+** (new tools across all 8 new connectors)
- Workflow templates: 3 → **11** (8 new production-ready templates)
- Landing page updated with correct agent/connector/tool counts
- Documentation: added CFO Guide, CMO Guide, updated API Reference with 12 new endpoints
- Version: 2.3.0 → **3.1.0**

## [2.3.0] - 2026-03-31

### Added — Security, Error Handling, SDKs & New Features
- **Password Reset Flow**: Full forgot-password + reset-password with JWT tokens, rate-limited, email enumeration safe
- **Connector Detail Page**: View/edit individual connector auth config, secret references, health checks
- **Connector Registry Endpoint**: `GET /connectors/registry` returns all registered connectors with tool counts
- **Connector Create Page**: `/connector-create` UI for adding new connector configurations
- **Email Workflow Triggers**: `email_received` trigger type matches on subject keywords for inbox-driven workflows
- **API Event Triggers**: `api_event` trigger type for event-driven workflow automation
- **Agent Tool Auto-Population**: 25 agent types + 5 domain fallbacks auto-assign relevant tools on creation
- **Slack Full Configuration**: Bot token auth, connector detail edit, Slack tools in support/ops agent defaults
- **API Key Management**: `ao_sk_` prefixed keys, admin-only endpoints (`POST/GET/DELETE /org/api-keys`), bcrypt-hashed at rest
- **Shadow Limit Enforcement**: Agents must pass shadow quality gates before promotion to active status
- **HITL via GraphInterrupt**: LangGraph-based HITL with `GraphInterrupt` for pause/resume at approval nodes
- **Tool Validation**: Scope enforcement ensures agents cannot call tools outside their authorized set
- **Secret Manager Integration**: GCP Secret Manager via `secret_ref` field in connector config
- **Auth Failure Clearing**: IP-based failure tracking with auto-block + success clears failure count
- **Python SDK** (`pip install agenticorg`): client.agents.run(), client.sop.parse_text(), client.a2a.agent_card()
- **TypeScript SDK** (`npm i agenticorg-sdk`): full agent/SOP/A2A/MCP client
- **MCP Server** (`npx agenticorg-mcp-server`): exposes 340+ tools to Claude Desktop, Cursor, ChatGPT
- **CLI**: `agenticorg agents list`, `agenticorg agents run`, `agenticorg sop parse`, `agenticorg mcp tools`
- **Integration Workflow Page**: `/integration-workflow` with visual protocol guide + SDK quickstart
- **Developer Section**: Landing page developer section with SDK/CLI/MCP quickstart
- **Comms Domain**: 3 comms agent types (Ops Commander, DevOps Scout, Slack Notifier) — 6 domains total
- **Negative Test Suite**: 22 unit tests + 19 E2E tests covering error paths (401, 400, 404, 409, 410, 429)
- **Regression Tests**: 55 regression tests (40 March 2026 + 15 April 2026 PR fixes)

### Fixed — QA Bug List (7 bugs)
- **AUTH-RESET-001**: Password reset email flow (was just an alert() stub)
- **ORG-INV-002**: Invite accept "Invalid issuer" — dynamic issuer matching for production
- **AGENT-CONFIG-003**: Tools auto-populated based on agent_type/domain
- **HITL-COUNT-004**: Decided tab shows decision badge instead of action buttons
- **HITL-EXP-005**: Expired items filtered from Pending queue (backend + frontend)
- **WF-CONN-006**: Email trigger + api_event added to workflow UI (was missing)
- **CONN-SLACK-007**: Slack connector end-to-end config from UI

### Security — All CodeQL + Dependabot Resolved
- Fixed 17 CodeQL alerts: stack trace exposure, clear-text logging, XSS, socket binding, workflow permissions
- Fixed 2 Dependabot alerts: picomatch 2.3.1→2.3.2, 4.0.3→4.0.4
- Auth middleware: generic error messages (no internal details leaked)
- Sales API: whitelisted response fields (no agent internals exposed)
- API key endpoints admin-only (`agenticorg:admin` scope required)
- Secret key hardening via GCP Secret Manager (`_get_secret()` in BaseConnector)
- Auth failure clearing — successful auth clears IP-based failure count

### Changed
- Workflow UI: 5 trigger types (manual, schedule, webhook, api_event, email_received)
- Approval card: readonly mode for decided items with decision + timestamp
- ConnectorCard: clickable, navigates to detail page
- All form pages: extract and display API error details instead of generic messages
- Settings/Workflows: user-facing error messages instead of console.error
- Integrations page: replaced curl examples with SDK/CLI quickstart
- Agent domains: 5 → **6** (added Comms domain)
- Agent skills: 25 pre-built + 3 comms = **28 total skills**
- Automated tests: 1,031 → **1,196+** (821 unit + 86 security + 174 connector harness + 55 regression + 62 integration + 370+ Playwright E2E + 148 production E2E)
- Version: 2.2.0 → **2.3.0**

## [2.2.0] - 2026-03-29

### Added — Agent-to-Connector Bridge (Agents That Act)
- **Tool Calling Pipeline**: Agents now parse LLM output for `tool_calls`, execute them via Tool Gateway against real external APIs, and synthesize results in a second LLM pass
- **GitHub Connector**: 9 real API v3 tools (list_repos, get_repo, issues, PRs, releases, search_code, actions)
- **Jira Connector**: 11 real Atlassian REST API tools (projects, issues, JQL search, transitions, comments, sprints, metrics)
- **HubSpot Connector**: 13 real CRM API v3 tools (contacts, deals, companies, pipelines, analytics) with OAuth auto-refresh
- **3 New Agents**: Ops Commander (Jira triage), CRM Intelligence (HubSpot analysis), DevOps Scout (GitHub + Jira health)
- **3 Pre-built Workflows**: Incident Response Pipeline, Lead-to-Revenue Pipeline, Weekly DevOps Health Report
- **Production Connector Test Suite**: 17 tests hitting real Jira/HubSpot/GitHub APIs

### Fixed — Critical Production Bugs
- **Workflow Engine**: `run_workflow` now actually executes the WorkflowEngine in background — creates StepExecution DB records, updates progress, creates HITLQueue entries for approval steps
- **Token Blacklist**: Changed Redis key from `token[:32]` (shared by ALL HS256 JWTs) to SHA-256 hash — one logout was blocking every user
- **Playground 401**: Token validation now guards empty JWKS URL; frontend handles demo login failure properly
- **Agent Promote**: `shadow_min_samples=0` now bypasses shadow validation (was blocking all promotions)
- **Base Connector Auth**: `_authenticate()` now runs before HTTP client creation so auth headers are included
- **Jira Search API**: Migrated from deprecated `/rest/api/3/search` (410 Gone) to `/rest/api/3/search/jql`
- **HubSpot OAuth**: Auto-refresh on token expiry + 401 retry with re-authentication

### Changed
- **Workflow `_execute_agent`**: Replaced hardcoded stub with real agent instantiation and LLM execution
- **ToolGateway**: Optional dependencies (works without rate limiter/audit), dynamic connector resolution from registry + DB
- **Playground UI**: Displays tool call results with connector name, status, and latency
- **WorkflowRun UI**: Auto-polls every 3s while workflow is running
- **Version**: 2.1.0 → 2.2.0

### Metrics
- Automated tests: 353 → **1,031** (pytest) + 125 production E2E
- Production E2E: **125/125 (100%)** — all 21 sections, all demo users, full lifecycle
- Connector tools: 54 connectors × **273 total tools**
- Real API verified: GitHub (9), Jira (11), HubSpot (13) — 14 Jira tickets created on production

## [2.1.0] - 2026-03-21

### Added — Full PRD v4 Compliance
- **Workflow Engine**: Dependency graph resolution via topological sort, timeout enforcement, retry integration with exponential backoff, HITL pause/resume, sub-workflow execution
- **Schedule Trigger**: Full cron expression matching (5-field) for time-based workflow triggers
- **Token Bucket Rate Limiter**: Redis Lua-based atomic token bucket replacing simple counter
- **JWT Issuer Validation**: `iss` claim validation against Grantex token server
- **Token Pool Refresh**: Background refresh via `delegate_agent_token()` at 50% TTL
- **API Endpoints**: GET `/workflows/runs/{id}`, POST `/dsar/export`, POST `/schemas`, PUT `/agents/{id}`
- **WebSocket Feed**: Registered `/ws/feed/{tenant_id}` in main router
- **Agent Prompts**: Token scope declarations and `<processing_sequence>` steps for all 24 agents
- **OpenTelemetry**: All 7 spans with full PRD attributes and proper SpanKind
- **LangSmith Integration**: Full httpx-based trace logging (log_trace, log_batch, update_run)
- **Alert Manager**: 11 PRD-defined threshold checks with Slack/email notification
- **Shadow Comparator**: 6 quality gates (accuracy, confidence calibration, HITL rate, hallucination, tool errors, latency)
- **HPA Integration**: Queue depth + CPU + schedule-based scaling signals
- **Cost Ledger**: Redis+DB dual persistence with daily/monthly budget enforcement
- **RLS on Tenants**: Row-level security now covers all 18 tables
- **CI/CD Approval Gate**: Manual approval stage before production deployment (9/9 stages)
- **Test Suite**: 161 test functions across 13 files (Finance 15, HR 12, Ops/Mkt 13, Performance 9, Reliability 7, Security Auth+LLM 22, Security Data+Infra 25, Agent Scaling 31)
- **UI Pages**: All 10 pages fully implemented (Agents, Workflows, Approvals, Connectors, Schemas, Audit, Settings, AgentDetail, WorkflowRun, Dashboard)
- **BaseConnector._get_secret()**: Proper credential retrieval via config/env/secret_ref

## [2.0.0] - 2026-03-21

### Added — Initial Platform
- 24 specialist agents + NEXUS orchestrator
- 43 typed connectors (PineLabs Plural for payments, Gmail)
- Workflow engine with 9 step types
- Full PostgreSQL DDL with pgvector, RLS, and time-range partitioning (6 migrations)
- 18 JSON Schema data templates
- OAuth2/Grantex auth with JWT, token pool, scope enforcement
- Tool Gateway with rate limiting, idempotency, PII masking, audit logging
- React 18 + TypeScript + Shadcn/ui frontend (10 pages, 8 components)
- OpenTelemetry tracing + Prometheus metrics
- Agent Factory with shadow mode, lifecycle FSM, cost ledger
- 9-stage CI/CD pipeline with Docker + Helm charts
- SOC2/GDPR/DPDP compliance tools built-in
- Apache 2.0 license
