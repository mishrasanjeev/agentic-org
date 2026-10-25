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
| `GET`, `PUT /api/v1/commerce/runtime/merchant-configs/{id}/sell-everywhere` | Merchant (config write) | Where the shop stands on each surface; turn the network on (accepting the terms) or pause it. |

The gateway runs the same UCP handlers as `https://<host>/ucp` (`api.v1.commerce_ucp.run_ucp_operation`)
in the shop's tenant on its published host, so idempotency, the arbiter and the ledger behave the same.
Network checkouts answer to the network platform only (`AGENTICORG_NETWORK_AGENT_PROFILE`). There is no
complete, cancel or payment route.

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

## Switches

| Switch | Default | What it does |
| --- | --- | --- |
| `AGENTICORG_NETWORK_ENABLED` | Off | Serves the network routes. Off, they answer 404. |
| `AGENTICORG_NETWORK_SERVICE_TOKEN` | Empty | The credential the hosted MCP server presents to the gateway (Secret Manager, at least 32 characters). Empty, the gateway refuses every call. |
| `AGENTICORG_NETWORK_AGENT_PROFILE` | `https://agenticorg.ai/ucp/platforms/agenticorg-network/profile.json` | The UCP platform profile network checkouts are opened for. |
| `AGENTICORG_NETWORK_TERMS_VERSION` | `2026-10-25` | The network terms version a merchant accepts. |
| `AGENTICORG_NETWORK_TELEGRAM_BOT_USERNAME` | Empty | The public bot's username, for `t.me/<bot>?start=<slug>` links. |
| `AGENTICORG_PLATFORM_SUBDOMAIN_APEX` | `agenticorg.ai` | The apex for platform subdomains. |
| `AGENTICORG_PLATFORM_SUBDOMAIN_CAP` | `20` | How many platform subdomains may be claimed. |

The gateway also needs `AGENTICORG_UCP_CHECKOUT_ENABLED`.
