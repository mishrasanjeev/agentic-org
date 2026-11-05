# Network registrations runbook: ChatGPT, Google, Microsoft, Perplexity

This runbook registers the AgenticOrg shopping network with four outside programs:

| Program | What is registered | Key setting value |
| --- | --- | --- |
| ChatGPT app directory | The hosted MCP server `https://mcp.agenticorg.ai/mcp`, as one app for the whole network | `chatgpt` |
| Google Merchant Center | One multi-client (advanced) account, one sub-account per shop, each with the shop's own feed | `google` |
| Microsoft Merchant Center (Copilot shopping) | One store per shop, each with the shop's own feed | `microsoft` |
| Perplexity merchant program | The network, with every shop's feed and pages | `perplexity` |

AgenticOrg builds and serves everything on its side. The owner creates the accounts, fills in the
forms and accepts each program's terms. Nothing in this runbook is done by a script on the owner's
behalf, apart from the Firebase Hosting release in step 0.3.

## Sources read

Read on 2026-11-04. Where a page could not be read, this runbook says so and builds to the formats
both merchant centers document.

| Source | Result |
| --- | --- |
| OpenAI, Plugin guidelines, `developers.openai.com/apps-sdk/app-submission-guidelines` | Read. The ChatGPT directory now takes "plugins": an MCP server plus listing metadata. |
| OpenAI, Upload and submit your plugin, `developers.openai.com/apps-sdk/deploy/submission` | Read. Package layout, listing limits, review test cases, domain challenge. |
| OpenAI, Apps SDK reference and authentication, `developers.openai.com/apps-sdk/reference`, `.../build/auth` | Read. Tool annotations required; per-tool `securitySchemes`; anonymous read-only servers allowed. |
| OpenAI commerce index, `developers.openai.com/commerce` | Read. An index of specs only; no merchant application form. |
| `chatgpt.com/merchants` | Not readable (HTTP 403). |
| Google, Product data specification, `support.google.com/merchants/answer/7052112` | Read (the first part verbatim; condition, brand and mpn from a summary). |
| Google, RSS 2.0 specification, `support.google.com/merchants/answer/160589` | Read. `xmlns:g="http://base.google.com/ns/1.0"`; the feed is a full snapshot. |
| Google, gtin, identifier_exists and shipping, `answer/6324461`, `answer/6324478`, `answer/6324484` | Read. |
| Google, Request an advanced account setup, `support.google.com/merchants/answer/188487` | Read. Sub-accounts, website claim inheritance. |
| Google, `support.google.com/merchants/answer/12475797` | Not found (HTTP 404). |
| Microsoft, Can I use the Google feed file?, `help.ads.microsoft.com/apex/index/3/en/51110` | Found by search: Microsoft Merchant Center supports Google data feeds. |
| Microsoft, Products resource, `learn.microsoft.com/en-us/advertising/shopping-content/products-resource` | Read (updated 2026-10-29). The link domain must match the store's domain and must not redirect. |
| Microsoft, feed attribute help, `help.ads.microsoft.com/#apex/ads/en/51084/1` | Not readable (an empty app shell). |
| Perplexity, Shop like a pro, `perplexity.ai/hub/blog/shop-like-a-pro` | Read. Dated 18 November 2024; a signup form; no feed format is given. |
| Perplexity, `perplexity.ai/merchants` | Read, but it describes another product, not the merchant program. |
| MCP registry, generic server.json and official registry requirements, `github.com/modelcontextprotocol/registry` | Read. |
| MCP registry schema `static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json` | Read; both server.json files validate against it. |
| MCP specification 2025-11-25, lifecycle and schema | Read. `serverInfo` title, websiteUrl, icons; `instructions`. |

## What AgenticOrg serves

On every listed network shop's own web address (its `<slug>.agenticorg.ai` address or its own domain),
built from the newest catalog snapshot of every source (Shopify or upload), only while Grantex verifies
the catalog, price and stock (code: `core/commerce/shop_site.py`, `core/commerce/shop_feed.py`,
`api/v1/shop_site.py`):

| URL | What it is |
| --- | --- |
| `https://<host>/` | The shop's home page: name, products, shipping and returns, how to buy |
| `https://<host>/products/<handle>` | One product: price, stock, images, description, details, shipping and returns, schema.org `Product` JSON-LD with `gtin` and `shippingDetails`. The feed links here. |
| `https://<host>/feeds/google.xml` | The product feed, RSS 2.0 with the `g:` namespace. Microsoft Merchant Center takes the same file. |
| `https://<host>/sitemap.xml`, `/robots.txt`, `/llms.txt` | For crawlers and AI agents |

While the verified catalog is unavailable, these pages and the feed answer 503 with `Retry-After`,
so a merchant center keeps the items it has instead of dropping them.

The feed has one item per sellable variant: `g:id` (SKU, or a 24-character digest), `title`,
`description`, `link`, `g:image_link`, up to ten `g:additional_image_link`, `g:availability`
(`in_stock` or `out_of_stock`), `g:price` (`449.00 INR`), `g:sale_price` when the catalog has a
higher compare-at price, `g:brand` when the catalog names one, `g:gtin` (valid check digit) or else
`g:identifier_exists` `no`, `g:condition`, `g:product_type`, `g:item_group_id` for products with
options, and `g:shipping` per country for the shop's own flat rate. A variant with no price, a zero
price, no stock figure or no image is left out.

For the whole network:

| URL | What it is |
| --- | --- |
| `https://app.agenticorg.ai/api/v1/network/feeds` | Every listed shop: name, website, feed URL, sitemap, eligible item count. Use it to set up sub-accounts. Built at most once every five minutes, so a change shows within about five minutes. |
| `https://mcp.agenticorg.ai/mcp` | The hosted MCP server (Streamable HTTP, no sign-in) |
| `https://mcp.agenticorg.ai/.well-known/openai-apps-challenge` | The ChatGPT domain challenge token, once `MCP_OPENAI_APPS_CHALLENGE` is set |
| `https://agenticorg.ai/privacy` | Privacy notice, with the section "Shopping network buyers" (`#shopping-network-buyers`) |
| `https://agenticorg.ai/network-terms` | Shopping network terms, for buyers and shops |
| `https://agenticorg.ai/support` | Support contact |
| `https://agenticorg.ai/favicon-512x512.png` | Logo, 512 x 512 PNG |

The privacy notice and the network terms are labelled draft and need legal review before submission.

## 0. Before any program

0.1. Deploy this release to `agenticorg-api` and `agenticorg-ui`, and the MCP image to the hosted MCP
service, by the usual manual Cloud Run steps.

0.2. Check the flags on `agenticorg-api`: `AGENTICORG_NETWORK_ENABLED=true`, and for platform
subdomains and custom domains `AGENTICORG_CUSTOM_DOMAIN_FIREBASE_ENABLED=true` with the site set.

0.3. Release the Firebase Hosting site again, so that the shop-site paths reach the API on every
merchant domain and platform subdomain already connected. Hosting applies one release to every
domain on the site; no per-domain step is needed. Then check a few hosts:

```bash
python scripts/release_merchant_domain_site.py --project PROJECT_ID --site agenticorg-merchant-domains --dry-run
python scripts/release_merchant_domain_site.py --project PROJECT_ID --site agenticorg-merchant-domains --check-host SHOP1.agenticorg.ai --check-host SHOP2.example
```

Each host should print `shop site live (robots.txt 200)`. Other answers:

| Printed | Meaning |
| --- | --- |
| `reaches AgenticOrg, not a listed network shop (404)` | The host is a merchant domain, but its shop is not listed, not live, or the network is off. |
| `reaches AgenticOrg, shop lookup failed; check again in a minute (503)` | The API could not look the host up (for example a database problem). Crawlers are told to retry. |
| `reaches an AgenticOrg API without the shop site; deploy this release first (401)` | The Hosting release is out, but the API serving the host is an older release. |
| `not reaching AgenticOrg yet` | The release has not reached that host; wait a minute and check again. |

0.4. Open `https://app.agenticorg.ai/api/v1/network/feeds`. Every shop you plan to register needs
`catalog_verified: true` and `eligible_items` above 0. Open one shop's home page, one product page
and its feed in a browser. Check the product page price and stock against the feed item.

0.5. Have legal review `https://agenticorg.ai/privacy` and `https://agenticorg.ai/network-terms`.

## 1. ChatGPT app directory

The package is in `mcp-server/shopping/openai-plugin/`: `plugin.json` (listing, review cases,
commerce declaration), `mcp.json` (the one MCP server, no headers) and `assets/logo.png`.

**Decision before you submit.** OpenAI's plugin guidelines (read 2026-11-04) say plugins may conduct
commerce only for physical goods, should send buyers to complete purchases on the seller's own
domain, and "may not: link directly to a checkout or other transactional page". The network's
`complete_checkout` tool returns the shop's payment page (its Shopify checkout or Plural page). Ask
OpenAI developer support whether that hand-off is allowed for a multi-shop app before submitting, or
expect a rejection on that point. The review package declares the commerce behaviour plainly
(`review.commerce: true` and `commerce_description`).

1.1. Verify the developer: at `https://platform.openai.com/settings/organization/general` complete
business verification for Orchestrum Technologies LLP (or individual verification). Add support
contact details.

1.2. Record a short demo (search, one shop, checkout to the payment link, the buyer paying on the
shop's page) and upload it where reviewers can open it. Put its URL in
`extensions.com.openai.review.demo_recording_url` in `plugin.json`.

1.3. Build the ZIP from the package folder, with `plugin.json` at the root:

```bash
cd mcp-server/shopping/openai-plugin && zip -r ../agenticorg-shops-plugin.zip plugin.json mcp.json assets
```

1.4. In the OpenAI platform dashboard, open the plugin submission page, choose the organization and
project, choose "With MCP" and upload the ZIP. Pick the category in the dashboard (the package says
`Shopping`; change it there if the dashboard has no such category).

1.5. Domain verification. The dashboard shows a challenge token. Set it on the hosted MCP service
and redeploy that service:

```bash
gcloud run services update MCP_SERVICE --region asia-southeast1 --update-env-vars MCP_OPENAI_APPS_CHALLENGE=TOKEN
curl -s https://mcp.agenticorg.ai/.well-known/openai-apps-challenge
```

The curl prints only the token. Then confirm the challenge in the dashboard.

1.6. Authentication: choose no authentication. Every tool declares `securitySchemes:
[{"type": "noauth"}]` and explicit `readOnlyHint`, `destructiveHint` and `openWorldHint`.

1.7. Check the URLs the form shows: website `https://agenticorg.ai`, support
`https://agenticorg.ai/support`, privacy `https://agenticorg.ai/privacy`, terms
`https://agenticorg.ai/network-terms`, MCP URL `https://mcp.agenticorg.ai/mcp`, logo
`assets/logo.png`. Fix any automated findings, complete the review form, accept the policy
attestations and submit.

1.8. Set `chatgpt` to `submitted` (section 5). After approval, publish in the dashboard and set
`chatgpt` to `live`.

Also list the server in the MCP registry (optional, no review): with the `mcp-publisher` tool,

```bash
cd mcp-server/shopping
mcp-publisher login github
mcp-publisher publish
```

`mcp-server/shopping/server.json` is `io.github.mishrasanjeev/agenticorg-shopping` with one
`streamable-http` remote, `https://mcp.agenticorg.ai/mcp`. It is separate from
`mcp-server/server.json`, which stays the npm stdio package `io.github.mishrasanjeev/agenticorg`.
Keep its `version` equal to `mcp-server/package.json`.

## 2. Google Merchant Center (multi-client account)

2.1. Create the parent account. Sign in to Merchant Center with the AgenticOrg Google account. Create
a Merchant Center account for AgenticOrg with website `https://agenticorg.ai`, verify and claim
`agenticorg.ai`, then request the advanced (multi-client) account setup from Google (Google's page
"Request an advanced account setup"). Google requires the website to be verified and claimed first.
The existing account becomes a sub-account under the new parent.

2.2. One sub-account per shop. For each shop in `/api/v1/network/feeds`:

- Create a sub-account named after the shop, with website `website_url` from the index and the
  shop's country.
- Website claim. A platform address `<slug>.agenticorg.ai` sits under the parent's claimed
  `agenticorg.ai`, so the sub-account can inherit the claim. A shop on its own domain needs one of
  Google's three options: you verify the domain in the sub-account's settings, the shop verifies it
  in its own Search Console and shares it, or the shop owner is added as a user and claims it.
- Add a product data source: "Add products from a file", file URL = `feed_url` from the index
  (`https://<host>/feeds/google.xml`), scheduled fetch daily. Set the language and the country the
  shop sells to. The file name ends in `.xml`, as Google requires.
- Shipping. A shop with a flat shipping fee (no Shopify) has `g:shipping` in its feed for the
  countries it named. A Shopify shop's rates are read from Shopify at checkout, so its feed has no
  `g:shipping`: set the shipping service in the sub-account to match its Shopify rates. A shop that
  ships to "any country" without naming countries also needs the shipping service set there.
- Returns. Set the return policy in the sub-account from the shop's returns text (shown on its pages).
- Business information: the shop's name, and its support contact.

2.3. Review the diagnostics after the first fetch. Items without a GTIN are sent with
`identifier_exists` `no`; if Google asks for identifiers, the shop adds barcodes to its catalog.
Product landing pages have no basket of their own: they link to the shop's own store page when the
catalog has one, to the Telegram shopping bot and to the MCP server. If Google flags the landing
pages because buyers cannot buy on them, record the finding and contact AgenticOrg engineering.

2.4. Set `google` to `submitted`, and to `live` once the first sub-accounts show approved items. A
shop whose sub-account is not approved yet gets its own value under `shops` (section 5).

## 3. Microsoft Merchant Center (Copilot shopping)

Microsoft Merchant Center supports Google data feed files, so each shop uses the same
`https://<host>/feeds/google.xml`.

3.1. Sign in to Microsoft Advertising with the AgenticOrg account and open Merchant Center.

3.2. For each shop, create a store: store name = the shop name, store URL = `website_url` from the
index. Microsoft requires each item's link to be on the store's domain and not to redirect; the feed
links are the shop's own product pages on that host, served without redirects. Verify the store's
domain the way the Merchant Center page asks.

3.3. Add a catalog to the store and a scheduled feed: the feed URL from the index, daily. As an
alternative, Merchant Center can import from the Google Merchant Center sub-account.

3.4. Join the Copilot shopping or merchant program from Merchant Center if the account is offered it,
and accept its terms.

3.5. Set `microsoft` to `submitted`, then `live` once stores show approved offers.

## 4. Perplexity merchant program

The only public description found is Perplexity's 18 November 2024 post, which offers a signup form
and says the program aims at large retailers. It gives no feed format.

4.1. Fill in Perplexity's merchant signup form (linked from that post) for AgenticOrg as a network of
shops. Give: the network feed index `https://app.agenticorg.ai/api/v1/network/feeds`, a sample shop
site and its feed, the sitemap and `llms.txt` of each shop, and the support address.

4.2. If Perplexity asks for one feed per shop, send each `feed_url`; the format is Google RSS 2.0.

4.3. Set `perplexity` to `submitted` when the form is sent, and `live` once Perplexity confirms.

## 5. Recording each program's status

`AGENTICORG_NETWORK_REGISTRATIONS` on `agenticorg-api` holds a JSON object. Each program is
`not_started`, `submitted` or `live`, or an object with a `status` and per-shop values that win over
it. Anything missing or unreadable counts as `not_started`.

```json
{"chatgpt": "submitted", "google": {"status": "live", "shops": {"spice-hub": "submitted"}}, "microsoft": "submitted", "perplexity": "not_started"}
```

Set it with an alternate delimiter, because the JSON contains commas:

```bash
gcloud run services update agenticorg-api --region asia-southeast1 --update-env-vars "^|^AGENTICORG_NETWORK_REGISTRATIONS={\"chatgpt\":\"submitted\",\"google\":\"submitted\"}"
```

What each shop then sees on its Sell everywhere page:

| Shop qualifies? | Program value | Shown |
| --- | --- | --- |
| Yes | `not_started` | Coming: AgenticOrg has not registered yet |
| Yes | `submitted` | Submitted, waiting for approval |
| Yes | `live` | Live |
| No | any | Not eligible yet, with the reason: Sell everywhere is off, the web address is not live, the catalog is not verified, or no product has a price, a stock figure and an image |

Google, Microsoft and Perplexity rows also show the shop's feed address while it qualifies.

## Rollback

- Shop pages and feeds: release the previous Hosting config (check out the previous release and run
  step 0.3) to take the shop-site paths off merchant hosts. The API serves them only on listed shop
  hosts in any case; pausing a shop takes its pages and feed off within about two minutes.
- Registration labels: remove `AGENTICORG_NETWORK_REGISTRATIONS`; every program shows as not started.
- ChatGPT domain challenge: remove `MCP_OPENAI_APPS_CHALLENGE`; the path answers 404.
