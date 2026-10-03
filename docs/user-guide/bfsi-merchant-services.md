## Scenario and ownership

This is a reference example for an acquiring bank's merchant-enablement pilot,
not an already integrated banking product or a claim of live payment execution.

In this synthetic example, an acquiring bank wants to help a fictional Shopify
merchant become usable by agentic buyer interfaces. The bank can offer an
institution-managed or reviewed hosted AgenticOrg workspace, but
merchant/provider/platform contracts still determine actual availability.

AgenticOrg owns seller/buyer runtime. Grantex owns trust/policy/canonical artifacts. Shopify owns merchant catalog/inventory/order truth. The bank/fintech/provider owns payment/mandate execution and customer authorization. OACP is not a reason to make Grantex the engine for every buyer message.

## Build one synthetic merchant path

1. Assign a merchant owner, bank integration owner, provider contact and channel owner.
2. Configure merchant-scoped Seller Commerce Agent onboarding and source/channel settings.
3. Obtain read-only access to a sandbox Shopify store with a fictional catalog through the merchant's approved process.
4. Sync synthetic products/variants/images/price/inventory and compare samples with the sandbox store.
5. Send the bounded Grantex authority request and verify/cache issued artifacts.
6. Ask product questions about the fictional catalog through the configured web or agent bridge.
7. Inspect source, freshness, supported facts and refusal behavior.
8. Prepare a provider/POS handoff and confirm that no order/payment state is fabricated.

## Process map

This is a bank-led reference pilot with configured merchant, channel and
provider paths. A prepared handoff is never a completed transaction.

```flow
Bank and merchant setup | Agree identities, read scopes, provider contract and publishing state. | Owner: Bank integration owner and merchant owner | Human decision: Owners approve the pilot scope and any channel publication under their own processes. | If blocked: Unapproved access or publishing state keeps the channel off.
Merchant-source evidence | Read-only sync supplies product, price and inventory snapshots for sample checks. | Owner: Merchant source owner and configured AgenticOrg sync | If blocked: Missing scopes or stale source facts stop unsupported buyer promises.
Trust artifacts | The bounded authority request and cache retain scope, source and freshness. | Owner: Grantex authority and AgenticOrg runtime | If blocked: Invalid or stale artifacts cannot support commitment-bound requests.
Buyer channel | A configured bridge answers from supported merchant artifacts. | Owner: Channel owner and AgenticOrg buyer runtime | If blocked: Unsupported channels or facts are refused until the integration is approved.
Provider capability | A configured provider path checks non-sensitive capability evidence. | Owner: Bank/provider integration team | Human decision: The customer and provider complete any required authorization outside this map. | If blocked: Capability evidence alone does not prove a mandate or payment.
Authoritative handoff | A prepared packet awaits provider, POS or merchant confirmation. | Owner: Provider, POS or merchant system | If blocked: Absent or failed confirmation stays pending or failed; no paid or placed state is fabricated.
```

## Plural/Pine and mandates

Arrange the provider onboarding contact, merchant account, sandbox/live access, allowed capability contract and human authorization journey. AgenticOrg can verify provider-owned capability evidence directly through its configured path. Store non-sensitive references, not raw mandate/payment credentials.

A credential/token capability check is not proof of a completed mandate or an allowed payment in every geography. Live execution needs the provider's real contract, authorization, settlement/error/refund handling and institution approval. The current OACP runtime remains prepared/non-executing for those actions.

## Channels and protocol payloads

UCP/ACP/schema.org/AP2/A2A/MCP-style payloads and bridges are not automatic marketplace placement. Agree each target's supported contract, authentication, publication/approval requirements and actual customer journey. WhatsApp/Telegram need configured credentials and channel rollout; ChatGPT/Claude/Gemini/Perplexity need appropriate client/platform setup.

## Physical-store POS

An offline POS bridge prepares a store-linked handoff and reconciles authoritative provider/POS confirmation. It must preserve merchant/store context, expiry, source, reference and duplicate handling. Do not call a paid receipt successful because an agent displayed a prepared packet.

## Pilot success criteria

Verify accurate catalog facts, stale-source refusal, cross-merchant isolation, invalid webhook rejection, channel authentication and non-fabricated transaction state. Keep public discovery off until the publishing path is approved and validated. Measure the complete buyer journey rather than counting adapter names.

Next: [Capability status and gaps](/docs/bfsi-capability-status), [Commerce guide](/docs/commerce), [API and agent clients](/docs/api-sdk-mcp), [Team adoption](/docs/adoption-checklist).
