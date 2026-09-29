## Scenario and ownership

This is a reference example for an acquiring bank's merchant-enablement pilot,
not an already integrated banking product or a claim of live payment execution.

An acquiring bank wants to help an approved Shopify merchant become usable by agentic buyer interfaces. The bank can offer an institution-managed or reviewed hosted AgenticOrg workspace, but merchant/provider/platform contracts still determine actual availability.

AgenticOrg owns seller/buyer runtime. Grantex owns trust/policy/canonical artifacts. Shopify owns merchant catalog/inventory/order truth. The bank/fintech/provider owns payment/mandate execution and customer authorization. OACP is not a reason to make Grantex the engine for every buyer message.

## Build one real merchant path

1. Assign a merchant owner, bank integration owner, provider contact and channel owner.
2. Configure merchant-scoped Seller Commerce Agent onboarding and source/channel settings.
3. Obtain read-only Shopify access through the merchant's approved process.
4. Sync products/variants/images/price/inventory and compare samples with Shopify.
5. Send the bounded Grantex authority request and verify/cache issued artifacts.
6. Ask real product questions through the configured web or agent bridge.
7. Inspect source, freshness, supported facts and refusal behavior.
8. Prepare a provider/POS handoff and confirm that no order/payment state is fabricated.

```flow
Bank and merchant setup | Agree ownership, identities, read scopes and publishing state.
Shopify evidence | Real read-only sync produces source-linked commercial facts.
Trust artifacts | Grantex authority and AgenticOrg cache preserve scope and freshness.
Buyer channel | A configured bridge answers from supported artifacts.
Provider capability | Plural/Pine evidence checks configured capability, not successful payment.
Authoritative handoff | The provider, POS or merchant confirms its own execution outcome.
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

Next: [Commerce guide](/docs/commerce), [API and agent clients](/docs/api-sdk-mcp), [Team adoption](/docs/adoption-checklist).
