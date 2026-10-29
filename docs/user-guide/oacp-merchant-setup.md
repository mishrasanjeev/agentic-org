```summary
Setup has five phases: connect your shop, connect to Grantex, publish your UCP profile, open sales channels, and payments.
Most steps are done once. Each step has an owner and a clear "if blocked" answer.
Your UCP profile goes live only after you prove you own the web address with a DNS TXT record.
Checkout, the ACP feed and MCP tools each have their own switch, so you can open one channel at a time.
Live payment stays off until a payment-provider sandbox run has passed. Nothing charges a buyer before then.
```

## The five phases

Want to see the result first? [Nilgiri Tea House](/docs/oacp-example-nilgiri-tea) is a live example shop that went through every phase except payments.

```figure
oacp-setup-path | Five phases from shop to sales | Connect your shop, connect to Grantex, publish your UCP profile, open sales channels, and finally turn on payments. The last phase stays off until a payment-provider sandbox run has passed.
```

Phases 1 to 3 are one page for a merchant: **Commerce > Sell Everywhere** (`/dashboard/sell-everywhere`) takes you from a new shop to a published profile, with no step for your platform operator; see [Sell everywhere](/docs/oacp-sell-everywhere). The **Commerce Runtime** page at `/dashboard/commerce-runtime` is the operator console for the same runtime. You need the administrator role or the merchant role. A few steps are done by your platform operator, the person who runs your AgenticOrg deployment. Those steps say so.

## Phase 1. Connect your shop

```flow start=1
Create your shop | On Sell Everywhere, type a shop name and select Create shop. AgenticOrg creates the merchant and its id. | Owner: Merchant | If blocked: Check that you have the merchant or administrator role.
Connect your catalog | Connect Shopify with the Admin API token of a custom app in your own store (read_products, read_inventory), or upload a CSV or JSON catalog. AgenticOrg encrypts the token and never shows it again. | Owner: Merchant | If blocked: Every sync uses your own stored token; without one it stops with blocked_missing_shopify_credentials.
Check the first read | Compare a few products, prices and stock levels with your store or file. | Owner: Merchant | If blocked: A wrong store address or a bad file row stops the read and says which.
Set your order limits | Set the largest single order, the most per day, the most items per order and your currency. Saving other settings later keeps them. | Owner: Merchant | If blocked: Without limits, Sell everywhere stays off and the arbiter refuses every purchase with merchant_limits_missing.
```

## Phase 2. Connect to Grantex

```flow start=5
Enrol your tenant at Grantex | Turning on Sell everywhere enrols your tenant, merchant and catalog source with Grantex using the service token your platform operator configured once. | Owner: AgenticOrg | If blocked: The page shows Waiting while your Grantex has no enrolment route; an older Grantex still needs the operator's allowlist.
Request authority | From Commerce Runtime, send the Grantex authority request. Grantex signs your synced facts. | Owner: Merchant | If blocked: A 202 answer means connector evidence is missing; sync again first.
Verify and keep the facts | Cache the returned artifacts. AgenticOrg checks every signature, key, expiry and revocation slot before it keeps anything. | Owner: AgenticOrg | If blocked: A 422 artifact_verification_refused lists each refusal code; nothing from that batch is kept.
Ask a test question | Ask your seller agent "Which variants are in stock?" and check that the answer shows its source and freshness. | Owner: Merchant | If blocked: A missing answer means catalog, price or stock facts are missing; refresh them.
```

## Phase 3. Publish your UCP profile

Your UCP profile tells buyer agents where your shop is and what it offers. It is served at `https://<your shop host>/.well-known/ucp`.

```figure
oacp-dns-txt | The DNS TXT step | AgenticOrg gives you a TXT record name and value. You add it at your DNS provider. AgenticOrg looks it up and, on a match, records that you control the host.
```

```flow start=9
Choose your shop host | Ask AgenticOrg for a challenge for your host, for example shop.merchant.example. It returns a TXT record name and value. | Owner: Merchant | If blocked: An IP address or a single-word host is not accepted.
Add the DNS TXT record | At your DNS provider, add the TXT record _agenticorg-ucp.shop.merchant.example with the value AgenticOrg gave you. | Owner: Merchant | If blocked: DNS changes can take time to spread; wait and try the check again.
Verify the host | Ask AgenticOrg to check the record. On a match it stores the proof and links the host to your merchant. | Owner: AgenticOrg | If blocked: A host already linked to another tenant is refused.
Create your signing key | AgenticOrg creates one signing key for your shop. Your profile publishes its public half. Rotated keys stay published for at least 7 days. | Owner: Merchant | If blocked: Publishing is refused with signing_key_missing until a key exists.
Publish | Record your opt-in to public discovery. The next Grantex authority request asks Grantex to allow discovery for this host. | Owner: Merchant | If blocked: Refusals name the reason, such as serving_host_not_attested.
Profile goes live | When the UCP profile switch is on and Grantex has issued an allowing discovery artifact, the profile is served. | Owner: Platform operator | If blocked: The profile answers 404 until every condition holds; the UCP status view shows which one is missing.
```

Your operator must also route your shop host to the AgenticOrg service with the original host name intact. The easiest way is the **Custom Domains** page. It runs the TXT step above for you, adds the CNAME record that routes the host, and follows the domain until its certificate is live. See [Your own domain for buyer agents](/docs/oacp-custom-domains).

## Phase 4. Open your sales channels

Each channel has its own switch. Turn on one, test it, then the next.

```flow start=15
UCP checkout | The operator turns on UCP checkout and you issue each agent platform its own API key. Agents can then open checkouts at your /ucp address. | Owner: Platform operator and merchant | If blocked: With the switch off, every checkout route answers 404.
ACP product feed | The operator turns on the ACP feed. You export it as JSON, JSON Lines or CSV and upload the files where the agent platform asks. | Owner: Platform operator and merchant | If blocked: The feed is refused until the catalog's verified facts are fresh.
ACP checkout | The operator turns on ACP checkout, you opt your shop in, and you issue the platform a key with ACP write access. | Owner: Platform operator and merchant | If blocked: Without your opt-in your ACP routes answer 404.
MCP checkout tools | On each MCP server install, set the address of your UCP profile. The checkout tools appear only while your profile lists checkout. | Owner: Platform operator | If blocked: No profile address, or checkout off, means no tools are registered.
Grantex mandates | The operator turns on mandates and you name your Grantex commerce tenant and merchant in your settings. | Owner: Platform operator and merchant | If blocked: Without mandates, production refuses checkout decisions as buyer_authority_verified.
```

Turn mandates on before payments. In production, a checkout without verified mandates is refused.

## Phase 5. Payments: off until a sandbox run passes

> Live payment execution stays off. It is turned on only after a full run against the payment provider's sandbox has passed in staging, and then one merchant at a time. Until then, a checkout stops at executor_unavailable and no buyer is charged.

```flow start=20
Sandbox credentials | The operator stores the provider's sandbox credentials in the secret manager. | Owner: Platform operator | If blocked: Without credentials the sandbox tests are skipped, not passed.
Staging run | In staging, turn on payment execution for one test merchant and run the sandbox tests and the checkout smoke test. | Owner: Platform operator | If blocked: Any failure keeps payments off.
Check the ledger | Confirm each payment call comes after a consumed allow decision. | Owner: Platform operator | If blocked: A payment row without a consumed decision is an irregularity; stop and investigate.
Production, one merchant at a time | After owner approval, name the executor for one merchant and watch its first orders. | Owner: Platform operator | If blocked: Remove the merchant's executor setting to stop it at once.
```

## Quick test before you go live

- Ask a buyer question and check the source and freshness label.
- Open a test checkout and confirm it reaches ready_for_complete.
- With payments still off, confirm Complete answers executor_unavailable and nothing is charged.
- Export the ledger and run `scripts/verify_ledger.py` on it.

## Related guides

- [Feature switches reference](/docs/oacp-feature-switches)
- [Commerce runtime and Shopify](/docs/commerce)
- Technical references: [UCP profile](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/ucp-profile.md), [ACP feed and sessions](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/acp-feed-and-sessions.md) and the [launch and rollback runbook](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/explainers/launch-rollback-runbook.md)
