```summary
Sell everywhere puts your shop on every AI surface the AgenticOrg network reaches, with one switch.
Buyers then find you in ChatGPT, Claude and other AI apps, in the AgenticOrg shopping bot on Telegram, and on the open web.
You do no setup on each surface: AgenticOrg connects to them once, for every shop on the network.
If you have no web address for buyer agents yet, AgenticOrg gives you one, such as your-shop.agenticorg.ai.
You stay the seller. Buyers always finish and pay with you, and you can pause it at any time.
```

## One switch, every surface

```figure
sell-everywhere-network | One switch, every surface | You turn it on once. AgenticOrg lists your shop and connects it to each surface. Buyers finish and pay with you.
```

| Surface | How buyers reach you | What you do |
| --- | --- | --- |
| ChatGPT, Claude and other AI apps | They add AgenticOrg's server once and can then shop every network shop | Nothing |
| Telegram | They tap your link, `t.me/<bot>?start=<your-shop>`, or search across shops in the AgenticOrg shopping bot | Share your link if you like |
| The open web | AI search engines and agents read your shop's profile at your web address | Nothing |
| Google Gemini and AI Mode, Microsoft Copilot, Perplexity | Coming: AgenticOrg is registering with each program for the whole network | Nothing yet |
| Payment in the chat | Not in this release: buyers open a checkout in the chat and finish with you | Nothing |

## Turn it on

You need a merchant in Commerce Runtime with your catalog in it.

```flow
Your catalog | Connect Shopify read-only or add your products in Commerce Runtime. This is what buyers will search.
Your policies | Check returns, shipping and store pickup in Commerce Runtime. Buyer agents read them before they buy.
Sell everywhere | Open Commerce > Sell Everywhere, enter your merchant ID and load it. Choose your shop name, accept the network terms and turn it on.
```

Your shop name is the short name buyers see in AI apps and in your Telegram link, such as `nilgiritea`. Use 3 to 40 lowercase letters, digits or hyphens. It cannot change later, so choose it with care.

The network terms say that AgenticOrg lists your shop and opens checkouts for buyers who find it, and that you remain the seller and take payment yourself.

## Your web address

Buyer agents read your shop's profile at `https://<your address>/.well-known/ucp`.

- **You already have an address.** If you connected your own domain in Custom Domains, or your profile is already published, the network uses it.
- **You have no address yet.** AgenticOrg gives you `<your-shop>.agenticorg.ai` and sets it up for you, certificate included. It usually goes live within an hour.

AgenticOrg can give out 20 of these addresses in all. If they are taken, you can still turn on Sell everywhere and connect your own domain instead; see [Your own domain for buyer agents](/docs/oacp-custom-domains).

Your shop appears on the network only once its profile is published at that address. Until then, the page shows **Waiting for publication**.

## Where buyers can find you

The page lists each surface and where it stands:

| Status | Meaning |
| --- | --- |
| Live | Buyers can reach you there now. |
| Setting up, Issuing certificate | Your web address is being connected. Check back in a few minutes. |
| Waiting for publication | You are on the network, but your profile is not published yet. |
| Coming: registration in progress | AgenticOrg is still registering with that program. |
| Off | Sell everywhere is paused or not turned on. |

To see what buyers see in ChatGPT, follow [the ChatGPT run in the Nilgiri Tea House example](/docs/oacp-example-nilgiri-tea).

## Pause or resume

Choose **Pause** on the page. Your shop leaves the directory at once: AI apps and the Telegram bot stop offering it, and new network checkouts are refused. Your web address and your shop name stay yours. Choose **Resume Sell everywhere** to come back.

## What stays with you

- **Payment.** No surface takes payment in this release. A buyer opens a checkout in the chat and finishes with you.
- **Your rules.** Every network checkout goes through the same checks as any other: your signed facts, your limits and the arbiter's fixed rules, recorded in your ledger.
- **Your data.** The directory shows your shop name, display name, categories and profile address, nothing else.

## Read next

- [Merchant setup checklist](/docs/oacp-merchant-setup)
- [Your own domain for buyer agents](/docs/oacp-custom-domains)
- [Example: Nilgiri Tea House, end to end](/docs/oacp-example-nilgiri-tea)
- [Feature switches](/docs/oacp-feature-switches)
