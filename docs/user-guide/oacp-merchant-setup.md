```summary
Setup has five phases: connect your shop, connect to Grantex, publish your UCP profile, open sales channels, and agent-executed payment.
For a merchant, phases 1 to 3 are one page, Sell Everywhere, with no step for your platform operator.
Your UCP profile goes live on an address you control: a platform address AgenticOrg gives you, or your own subdomain proved with a DNS TXT record.
Buyers pay on your own Shopify checkout or Plural page, which you choose on the Sell Everywhere page. AgenticOrg never holds the money.
Agent-executed payment with buyer mandates stays off until a payment-provider sandbox run has passed.
```

## The five phases

Want to see the result first? [Nilgiri Tea House](/docs/oacp-example-nilgiri-tea) is a live example shop that went through phases 1 to 4 without a payment method.

```figure
oacp-setup-path | Five phases from shop to sales | Connect your shop, connect to Grantex, publish your UCP profile and open sales channels. The fifth phase, agent-executed payment, stays off until a payment-provider sandbox run has passed. Buyers already pay on your own page once checkout is open.
```

Phases 1 to 3 are one page for a merchant: **Sell Everywhere** (`/dashboard/sell-everywhere`) takes you from a new shop to a published profile, with no step for your platform operator; see [Sell everywhere](/docs/oacp-sell-everywhere). The **Commerce Runtime** page at `/dashboard/commerce-runtime` is the operator console for the same runtime. You need the administrator role or the merchant role. A few steps are done by your platform operator, the person who runs your AgenticOrg deployment. Those steps say so.

## Phase 1. Connect your shop

```flow start=1
Create your shop | On Sell Everywhere, choose your shop, or type a shop name and select Create shop. AgenticOrg creates the merchant and its id; you never type one. | Owner: Merchant | If blocked: Check that you have the merchant or administrator role.
Connect your catalog | Connect Shopify with the Admin API token of a custom app in your own store (read_products, read_inventory), or upload a CSV or JSON catalog. AgenticOrg encrypts the token and never shows it again. Then compare a few products, prices and stock levels with your store or file. | Owner: Merchant | If blocked: Every sync uses your own stored token; without one it stops with blocked_missing_shopify_credentials. A wrong store address or a bad file row stops the read and says which.
Write your policies | Write your returns and shipping terms, and store pickup if you offer it. Without Shopify, also set one shipping fee per order, its currency and the countries you ship to; 0 ships free. | Owner: Merchant | If blocked: A shop without Shopify and without a shipping fee can offer buyers no shipping, so Sell everywhere stays off until it is set.
Set your order limits | Set the largest single order, the most per day, the most items per order and your currency. Nothing is set for you, and the daily maximum must be at least the per-order maximum. | Owner: Merchant | If blocked: Without limits, Sell everywhere stays off.
```

Where buyers pay is the next step on the same page; the checklist covers it in phase 4, where checkouts end.

## Phase 2. Connect to Grantex

```flow start=5
Turn on Sell everywhere | Choose your shop name, accept the network terms and turn it on. AgenticOrg enrols your tenant, merchant and catalog source with Grantex itself; nobody edits a list for your shop. | Owner: Merchant, then AgenticOrg | If blocked: The switch stays off until your catalog, policies, shipping fee and limits are done (setup_incomplete names what is missing). Waiting means your deployment's Grantex cannot enrol tenants yet.
Request authority | Going live sends the Grantex authority request once by itself, and Grantex signs your catalog, prices, stock and policies. Your operator can also send it from Commerce Runtime. | Owner: AgenticOrg | If blocked: A 202 answer means connector evidence is missing; read your catalog again first.
Verify and keep the facts | AgenticOrg checks every signature, key, expiry and revocation slot before it keeps anything. The Verified catalog row then shows Done. | Owner: AgenticOrg | If blocked: Verified catalog shows To do; select Publish to try again. A 422 artifact_verification_refused lists each refusal code, and nothing from that batch is kept.
Ask a test question | Ask your seller agent "Which variants are in stock?" and check that the answer shows its source and freshness. | Owner: Merchant | If blocked: A missing answer means catalog, price or stock facts are missing; refresh them.
```

## Phase 3. Publish your UCP profile

Your UCP profile tells buyer agents where your shop is and what it offers. It is served at `https://<your shop host>/.well-known/ucp`.

With Sell everywhere this phase runs by itself:

- A shop with no address gets `<your-shop>.agenticorg.ai`. It needs no TXT record, because AgenticOrg owns that domain. At most 20 shops get one.
- A missing signing key is created, and the profile is published as soon as nothing stands in the way.
- The **Shop profile** row shows the exact reason when it waits. Select **Publish** to run the steps again.

For a host of your own, ownership is proved with a DNS TXT record. The **Custom Domains** page runs these steps for you:

```figure
oacp-dns-txt | The DNS TXT step | AgenticOrg gives you a TXT record name and value. You add it at your DNS provider. AgenticOrg looks it up and, on a match, records that you control the host.
```

```flow start=9
Choose your shop host | Ask AgenticOrg for a challenge for your host, for example shop.merchant.example. It returns a TXT record name and value. | Owner: Merchant | If blocked: An IP address or a single-word host is not accepted.
Add the DNS TXT record | At your DNS provider, add the TXT record _agenticorg-ucp.shop.merchant.example with the value AgenticOrg gave you. | Owner: Merchant | If blocked: DNS changes can take time to spread; wait and try the check again.
Verify the host | Ask AgenticOrg to check the record. On a match it stores the proof and links the host to your merchant. | Owner: AgenticOrg | If blocked: A host already linked to another tenant is refused.
Create your signing key | AgenticOrg creates one signing key for your shop. Your profile publishes its public half. Rotated keys stay published for at least 7 days. | Owner: Merchant | If blocked: Publishing is refused with signing_key_missing until a key exists; Publish on Sell Everywhere creates it.
Publish | Record your opt-in to public discovery. The next Grantex authority request asks Grantex to allow discovery for this host. | Owner: Merchant | If blocked: Refusals name the reason, such as serving_host_not_attested.
Profile goes live | When the UCP profile switch is on and Grantex has issued an allowing discovery artifact, the profile is served. | Owner: Platform operator | If blocked: The profile answers 404 until every condition holds; the UCP status view shows which one is missing.
```

Your host must also reach the AgenticOrg service with the original host name intact. The easiest way is the **Custom Domains** page. It runs the TXT step above for you, adds the CNAME record that routes the host, and follows the domain until its certificate is live. See [Your own domain for buyer agents](/docs/oacp-custom-domains).

## Phase 4. Open your sales channels

Each channel has its own switch. Turn on one, test it, then the next.

```flow start=15
UCP checkout | The operator turns on UCP checkout and you issue each agent platform its own API key. Agents can then open checkouts at your /ucp address. | Owner: Platform operator and merchant | If blocked: With the switch off, every checkout route answers 404, the network included.
ACP product feed | The operator turns on the ACP feed. You export it as JSON, JSON Lines or CSV and upload the files where the agent platform asks. | Owner: Platform operator and merchant | If blocked: The feed is refused until the catalog's verified facts are fresh.
ACP checkout | The operator turns on ACP checkout, you opt your shop in, and you issue the platform a key with ACP write access. | Owner: Platform operator and merchant | If blocked: Without your opt-in your ACP routes answer 404.
MCP checkout tools | Sell everywhere shops are served by the AgenticOrg server at https://mcp.agenticorg.ai/mcp, with nothing to set. For an MCP server of your own, set the address of your UCP profile on each install; its checkout tools appear only while your profile lists checkout. | Owner: AgenticOrg, or the platform operator for your own install | If blocked: No profile address, or checkout off, means no tools are registered.
Choose where buyers pay | In the Payments step of Sell Everywhere, choose your own Shopify checkout, or your own Plural account (client ID, client secret and webhook secret, Sandbox or Production, then Test). On every channel above, a ready checkout then answers requires_escalation with continue_url, the link to that page. | Owner: Merchant | If blocked: Shopify checkout needs a connected store; Plural needs saved credentials with a webhook secret. Without a method, buyers get no payment link.
```

See [Getting paid](/docs/oacp-getting-paid) for what the buyer sees, the Orders page and the email for each order.

## Phase 5. Agent-executed payment: off until a sandbox run passes

> Agent-executed payment, where AgenticOrg charges through a payment provider against verified buyer mandates, stays off. It is turned on only after a full run against the payment provider's sandbox has passed in staging, and then one merchant at a time. A shop that chose where buyers pay always hands the buyer a link instead. A shop with neither gives no payment link, its checkouts stop before payment, and no buyer is charged.

```flow start=20
Grantex mandates | The operator turns on mandates and you name your Grantex commerce tenant and merchant in your settings. | Owner: Platform operator and merchant | If blocked: Without mandates, production refuses agent-executed decisions as buyer_authority_verified.
Sandbox credentials | The operator stores the provider's sandbox credentials in the secret manager. | Owner: Platform operator | If blocked: Without credentials the sandbox tests are skipped, not passed.
Staging run | In staging, turn on payment execution for one test merchant and run the sandbox tests and the checkout smoke test. | Owner: Platform operator | If blocked: Any failure keeps payments off.
Check the ledger | Confirm each payment call comes after a consumed allow decision. | Owner: Platform operator | If blocked: A payment row without a consumed decision is an irregularity; stop and investigate.
Production, one merchant at a time | After owner approval, name the executor for one merchant whose payment method is off, and watch its first orders. A shop with a payment method keeps getting links. | Owner: Platform operator | If blocked: Remove the merchant's executor setting to stop it at once.
```

Mandates matter only for agent-executed payment. A payment link needs none: the buyer authenticates and pays on your own page.

## Quick test before you go live

- Ask a buyer question and check the source and freshness label.
- Open a test checkout and confirm it reaches ready_for_complete.
- Complete it and confirm the answer is `requires_escalation` with a `continue_url` to your payment page. With Plural in Sandbox, pay the link and check the order on the Orders page and the email.
- Export the ledger and run `scripts/verify_ledger.py` on it.

## Related guides

- [Sell everywhere](/docs/oacp-sell-everywhere)
- [Getting paid](/docs/oacp-getting-paid)
- [Feature switches reference](/docs/oacp-feature-switches)
- [Commerce runtime and Shopify](/docs/commerce)
- Technical references: [UCP profile](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/ucp-profile.md), [ACP feed and sessions](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/acp-feed-and-sessions.md), [payment hand-off](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/payment-handoff.md) and the [launch and rollback runbook](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/explainers/launch-rollback-runbook.md)
