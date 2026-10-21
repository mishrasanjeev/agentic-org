```summary
Every OACP surface that could expose your shop or move money starts switched off.
The safety checks start switched on in production: signature checks, verified buyer authority and the Grantex countersignature.
Request signatures start in log mode: checked and logged, never refused, until someone chooses enforce.
Switches are settings on the AgenticOrg service. They are read at start-up, so a change needs a restart.
Turn them on one at a time, in the order below, and write down how to turn each one back off first.
```

## Three kinds of switch

```figure
oacp-switch-board | Safe by default | Safety checks start on in production. Every surface that publishes or moves money starts off. Request signatures start in log mode and move to enforce only by choice.
```

"Strict runtimes" are production, staging, preview and any unknown environment name. Local, dev, development, test and ci are not strict.

## Safety checks (on in production)

| Switch | Default | What it does |
| --- | --- | --- |
| `AGENTICORG_OACP_VERIFY_STRICT` | On in strict runtimes; log-only elsewhere | Refuses to keep any fact whose signature, key, scope, expiry or revocation check fails. |
| `AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY` | On in strict runtimes | Refuses buyer authority that AgenticOrg did not verify itself (rule 4). |
| `AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT` | On in strict runtimes | An allow decision can be used only with a verified Grantex countersignature. |
| `AGENTICORG_ARBITER_GRANTEX_POST_ENABLED` | Follows the switch above | Sends each decision to Grantex for its countersignature. |
| `AGENTICORG_ROUTE_ENFORCEMENT_MODE` | `enforce` | A commerce write that skips its ledger row is answered as an error. `log` only logs it. |

Turning a safety check off weakens the shop's protection. Do it only as a recorded decision, for a short window.

## Ledger extras (off by default)

| Switch | Default | What it does |
| --- | --- | --- |
| `AGENTICORG_AUDIT_CHAIN_DEFAULT_ON` | Off | Links the ledger rows of every commerce tenant into a hash chain (the global `AGENTICORG_AUDIT_CHAIN_ENABLED` does it for all tenants). |
| `AGENTICORG_AUDIT_WITNESS_ENABLED` | Off | Sends each sealed chain head to Grantex, which countersigns it as an outside witness. |
| `AGENTICORG_OACP_RETENTION_APPLY_ENABLED` | Off | Lets the daily task delete review manifests whose stored retention decision has come due. |

## Selling surfaces (off by default)

| Switch | Default | What it does |
| --- | --- | --- |
| `AGENTICORG_UCP_PROFILE_ENABLED` | Off | Serves your UCP profile, catalog and store locations. Off, they answer 404. |
| `AGENTICORG_UCP_VERSIONS` | `2026-04-08` | Which UCP releases are served. Add `2026-08-25` when platforms read it. |
| `AGENTICORG_UCP_KEY_ROTATION_GRACE_DAYS` | `7` (minimum 7) | How long a rotated-out signing key stays published. |
| `AGENTICORG_CUSTOM_DOMAINS_ENABLED` | Off | Lets you connect your own subdomain (for example `agent.your-store.example`) for your UCP profile under Commerce > Custom Domains. Off, those pages and routes answer 404. See [Your own domain for buyer agents](/docs/oacp-custom-domains). |
| `AGENTICORG_CUSTOM_DOMAIN_MAPPING_ACCOUNT` | Empty | The Google account that connects domains. Merchants add it as an owner in Search Console. |
| `AGENTICORG_CUSTOM_DOMAIN_OPERATOR_TENANT_ID` | Empty | Your own organization's id. Only its administrators see the operator queue. Empty, nobody does. |
| `AGENTICORG_CUSTOM_DOMAIN_OPERATOR_EMAIL` | Empty | Where each new operator queue item is emailed, with its command. |
| `AGENTICORG_CUSTOM_DOMAIN_CLOUD_RUN_PROJECT` | Empty | The project the queue's commands name. Region (`asia-southeast1`), service (`agenticorg-api`) and CNAME target (`ghs.googlehosted.com`) have their own settings with these defaults. |
| `AGENTICORG_CUSTOM_DOMAIN_CLOUD_RUN_AUTOMAP_ENABLED` | Off | Lets AgenticOrg connect and disconnect domains itself. Keep it off: Google names a very broad role for it. |
| `AGENTICORG_UCP_CHECKOUT_ENABLED` | Off | Serves UCP checkout, cart and order routes. Off, they answer 404. |
| `AGENTICORG_ACP_FEED_ENABLED` | Off | Lets you export the ACP product feed. |
| `AGENTICORG_ACP_CHECKOUT_ENABLED` | Off | Serves ACP checkout sessions (your shop must also opt in). |
| `AGENTICORG_ACP_SIGNATURE_MODE` | `log` | Keep `log`. ACP publishes no signing method yet, so `enforce` would refuse every request. |
| `OACP_PUBLIC_CATALOG_PLATFORM_DISABLED` | Unset | An operator override that withdraws the public catalog pages for every merchant. |

## Mandates and payments (off by default)

| Switch | Default | What it does |
| --- | --- | --- |
| `AGENTICORG_GRANTEX_MANDATES_ENABLED` | Off | A checkout Complete gets Intent, Cart and Payment mandates from Grantex and verifies them. |
| `AGENTICORG_GRANTEX_MANDATE_CAPABILITY_EVIDENCE_ENABLED` | Off | Sends your payment-capability check result to Grantex with the authority request. |
| `PLURAL_PINE_LIVE_EXECUTION_ENABLED` | Off | Registers the Plural executor, so an allowed checkout or refund can actually charge or pay back. |
| `AGENTICORG_RBI_EMANDATE_AFA_LIMIT_MINOR_UNITS` | `1500000` (₹15,000) | Later UPI AutoPay debits above this need RBI AFA. |
| `AGENTICORG_RBI_EMANDATE_AFA_HIGHER_LIMIT_MINOR_UNITS` | `10000000` (₹1,00,000) | The higher limit for insurance, mutual fund and credit-card bill debits. |

## Agent request signatures

| Switch | Default | What it does |
| --- | --- | --- |
| `AGENTICORG_HTTPSIG_VERIFY_MODE` | `log` | `log` checks and logs signatures and refuses nothing. `enforce` answers 401 to unsigned calls on the commerce API. |
| `AGENTICORG_HTTPSIG_AUTHORITIES` | Empty | The public host names a signature must cover. Set before `enforce`. |
| `AGENTICORG_HTTPSIG_TAP_KEY_STORE_URLS` | Empty | Where Visa TAP keys are looked up. Empty means no TAP key is found. |
| `AGENTICORG_HTTPSIG_SIGNATURE_AGENT_ORIGINS` | Empty | Which Web Bot Auth signature agents are trusted. |
| `AGENTICORG_HTTPSIG_REGISTRY_LOOKUP_ENABLED` | Off | Records a verified agent's DID and scheme ids from the Grantex registry. |
| `AGENTICORG_HTTPSIG_SIGN_OUTBOUND` | Off | Makes AgenticOrg's own buyer agent sign its requests. |
| `AGENTICORG_A2A_SCHEME_AGENT_IDS` | Empty | AgenticOrg's own scheme agent ids, shown in its A2A agent card. |

## MCP server and merchant settings

| Setting | Default | What it does |
| --- | --- | --- |
| MCP server `AGENTICORG_UCP_PROFILE_URL` and `AGENTICORG_UCP_AGENT_PROFILE` | Unset | Point an MCP install at your UCP profile. Unset, no checkout tools appear. |
| Merchant `provider_policy.ucp.executor` | None | Names the payment executor for your shop. None means no charge. |
| Merchant `provider_policy.ucp.grantex_mandates` | None | Names your Grantex commerce tenant and merchant for mandates. |
| Merchant `provider_policy.acp.checkout_enabled` | False | Your opt-in to ACP checkout. |
| Merchant `provider_policy.arbiter.escalate_to_human` | False | Copies refusals into your review queue. It never changes or delays a decision. |

## The order to turn things on

First run the release in staging with route enforcement and signatures in `log`, and UCP checkout and payments off. Then change one switch at a time, each followed by its smoke test.

```flow
Staging run | Route enforcement log, signatures log, UCP checkout off, payments off. Check the ledger has no missing rows. | Owner: Platform operator | If blocked: Fix every missing ledger row before production.
Route enforcement | Keep enforce in production, the default. | Owner: Platform operator | If blocked: Use log only for a short, recorded window.
Request signatures | Start in log. Move to enforce only when the expected callers show as verified and the owner records the decision. | Owner: Platform operator | If blocked: Set log again; requests are answered again.
UCP profile and checkout | Turn on for a published merchant and run the UCP smoke test. | Owner: Platform operator | If blocked: Set false; routes answer 404 and stored sessions are kept.
Grantex mandates | Turn on before payments, merchant by merchant. | Owner: Platform operator | If blocked: Set false; production refuses checkout decisions again.
Payments | Only after the sandbox run passes, one merchant at a time. | Owner: Platform operator | If blocked: Unset and restart; checkout answers executor_unavailable.
```

The ACP feed and checkout, mandate-capability evidence, witnessing, retention and audit-chain sealing follow the same pattern, one at a time.

## Turning something back off

Set the switch back and restart the service. For worker tasks (witness, retention, sealing), change the worker and scheduler too.

- Data already written is kept: decisions, ledger rows, sessions, orders and payment records.
- To stop one merchant only, use its merchant setting instead, for example remove its executor.

The full table, with the exact turn-on condition and rollback for each switch, is in the [launch and rollback runbook](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/explainers/launch-rollback-runbook.md).

## Related guides

- [Merchant setup checklist](/docs/oacp-merchant-setup)
- [Safety and trust](/docs/oacp-safety-and-trust)
