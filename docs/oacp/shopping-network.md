# The AgenticOrg shopping network ("Sell everywhere")

A merchant turns the network on once. AgenticOrg then:

- lists the shop in a public directory that the hosted MCP server (ChatGPT, Claude, any MCP app)
  and the public Telegram bot read;
- opens checkouts there for the AgenticOrg network platform through the network checkout gateway;
- gives a shop with no host of its own a platform address `<slug>.<apex>` (for example
  `tea-corner.agenticorg.ai`), served through the Firebase Hosting front.

A listing never makes a shop public by itself. The directory and the gateway only serve a shop whose
UCP profile is published on an attested host, so the Grantex authority and publication gates stay in
force. Payments are not part of the network: the buyer pays with the shop.

## Routes

| Route | Who | What |
| --- | --- | --- |
| `GET /api/v1/network/merchants` | Anyone (rate-limited, cached 60 s) | Listed shops that are servable now: slug, name, categories, profile URL, Telegram link. |
| `POST /api/v1/network/shops/{slug}/checkout-sessions` | Network service credential | Open a checkout at the shop. |
| `GET`, `PUT /api/v1/network/shops/{slug}/checkout-sessions/{id}` | Network service credential | Read or replace a network checkout. |
| `POST /api/v1/network/shops/{slug}/checkout-sessions/{id}/complete` | Network service credential | The payment hand-off only: the link to the shop's own payment page ([payment-handoff.md](payment-handoff.md)). |
| `GET`, `PUT /api/v1/commerce/runtime/merchant-configs/{id}/sell-everywhere` | Merchant (config write) | Where the shop stands on each surface; turn the network on (accepting the terms) or pause it. |

The gateway runs the same UCP handlers as `https://<host>/ucp` (`api.v1.commerce_ucp.run_ucp_operation`)
in the shop's tenant on its published host, so idempotency, the arbiter and the ledger behave the same.
Network checkouts answer to the network platform only (`AGENTICORG_NETWORK_AGENT_PROFILE`). Complete is
the payment hand-off only: a body with a payment instrument, decision or mandate is refused (422
`payment_execution_not_allowed`), and a shop without a hand-off configured answers
`payment_handoff_unavailable` instead of running an executor. There is no cancel or payment route.

## Data

`commerce_network_listings` (migration `v6z95_network_listings`): one row per merchant scope with the
network slug (unique across tenants, fixed once chosen), status `listed` or `paused`, the terms version
accepted, by whom and when, and the platform subdomain if one was claimed. Tenant row-level security plus
a SELECT-only platform-context policy, as for `merchant_custom_domains`.

## Platform subdomains

When a listed shop has no published host, AgenticOrg claims `<slug>.<apex>`:

1. The host is attested as the shop's serving host without a DNS TXT record, because AgenticOrg owns the
   apex (`api.v1.commerce_ucp_publishing.attest_platform_subdomain`).
2. A `merchant_custom_domains` row is created with provider `firebase_hosting` straight in
   `pending_mapping`; the custom-domain status task creates the Firebase Hosting domain and follows the
   certificate to live.
3. The shop then publishes its profile on that host as usual.

Prerequisites: custom domains and the Firebase front on, and one wildcard record
`*.<apex> CNAME <site>.web.app.` in the apex's DNS zone. Firebase allows 20 custom domains per apex,
so at most `AGENTICORG_PLATFORM_SUBDOMAIN_CAP` (20) are claimed; past the cap the listing still stands
and the merchant is asked to connect its own domain.

## The public Telegram bot

One AgenticOrg bot serves every network shop to anyone on Telegram, separate from tenant channels
(which answer only a linked owner). `POST /api/v1/network/telegram/webhook` checks Telegram's secret
header and queues the update (`core.tasks.network_tasks`); the worker answers it once
(`core.commerce.network_telegram`).

- `t.me/<bot>?start=<slug>` picks a shop; `/shops` lists the network; `/stop` forgets the chat.
- Anything else goes to a tool-calling model with seven tools: list shops, search (one shop or the
  network), open, read and update a checkout, choose pickup, and get the payment link. The checkout
  tools use the same network path as the gateway. Once a checkout is ready the model asks for the
  payment link (the hand-off) and gives the buyer `summary.continue_url`, the shop's own payment page.
  There is no cancel or payment tool, and the model is told never to ask for payment details.
- Chat state (the chosen shop and the last six turns, never tool output) is kept in Redis under a
  SHA-256 of the chat id; turns expire after 30 minutes of quiet, the state after seven days.
- Limits: 20 messages per chat per hour and 60 per minute for the bot, then a short "try later".
  The platform Gemini spend cap applies. Replies pass the channel answer filter (no secrets, no echo
  of the bot's own credentials, capped length).

Set it up once: create the bot with BotFather, store its token and a webhook secret in Secret
Manager, give them to the API service, then run
`python -m scripts.connect_network_telegram_bot --base-url https://app.agenticorg.ai` and set
`AGENTICORG_NETWORK_TELEGRAM_BOT_USERNAME` to the username it prints.

## Switches

| Switch | Default | What it does |
| --- | --- | --- |
| `AGENTICORG_NETWORK_ENABLED` | Off | Serves the network routes. Off, they answer 404. |
| `AGENTICORG_NETWORK_SERVICE_TOKEN` | Empty | The credential the hosted MCP server presents to the gateway (Secret Manager, at least 32 characters). Empty, the gateway refuses every call. |
| `AGENTICORG_NETWORK_AGENT_PROFILE` | `https://agenticorg.ai/ucp/platforms/agenticorg-network/profile.json` | The UCP platform profile network checkouts are opened for. |
| `AGENTICORG_NETWORK_TERMS_VERSION` | `2026-10-25` | The network terms version a merchant accepts. |
| `AGENTICORG_NETWORK_TELEGRAM_BOT_USERNAME` | Empty | The public bot's username, for `t.me/<bot>?start=<slug>` links. |
| `AGENTICORG_NETWORK_TELEGRAM_ENABLED` | Off | Serves the public bot's webhook. Off, it answers 404. |
| `AGENTICORG_NETWORK_TELEGRAM_BOT_TOKEN` / `_WEBHOOK_SECRET` | Empty | The bot's token and webhook secret (Secret Manager). |
| `AGENTICORG_NETWORK_TELEGRAM_CHAT_LIMIT_PER_HOUR` / `_GLOBAL_LIMIT_PER_MINUTE` | `20` / `60` | Message limits per chat and for the bot. |
| `AGENTICORG_PLATFORM_SUBDOMAIN_APEX` | `agenticorg.ai` | The apex for platform subdomains. |
| `AGENTICORG_PLATFORM_SUBDOMAIN_CAP` | `20` | How many platform subdomains may be claimed. |

The gateway also needs `AGENTICORG_UCP_CHECKOUT_ENABLED`.
