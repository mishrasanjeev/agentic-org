```summary
Buyer agents can find your shop on a subdomain you own, such as agent.your-store.example, instead of an AgenticOrg address such as your-shop.agenticorg.ai.
You add two DNS records. AgenticOrg checks them, tells you exactly what to fix, and connects the domain itself.
Google issues the certificate, usually within the hour. You do not wait for anyone or share any account.
Only the agent addresses are served on your domain; nothing else of AgenticOrg is reachable there.
Your main domain (your-store.example) can answer too, through a pass-through rule. A redirect is not allowed there.
```

## What you get

Your UCP profile is the file buyer agents read first. It says where your shop is and what it offers. With a custom domain it is served at `https://agent.your-store.example/.well-known/ucp`, together with your catalog and store locations under `/ucp`.

```figure
oacp-custom-domain | Your own domain, step by step | You add a subdomain and publish two DNS records. AgenticOrg checks them, connects the domain and follows the certificate until the domain is live.
```

**Do you need one?** Not to get started. When you turn on Sell everywhere and your shop has no address yet, AgenticOrg gives it `<your-shop>.agenticorg.ai` and sets it up for you, certificate included, with no DNS records on your side. Only 20 of these addresses can be given out in all. Connect your own subdomain when you want your own name, or when they are taken; the network then uses your domain. A shop holds one domain at a time, so a shop already on a platform address removes it here before adding its own. See [Sell everywhere](/docs/oacp-sell-everywhere).

You will use the **Custom Domains** page at `/dashboard/custom-domains`. You need the administrator role or the merchant role. Enter your merchant ID, and your seller agent ID if your shop has more than one, then press **Load**.

## Before you start

- **A subdomain you control.** Use a name such as `agent.your-store.example` or `shop-agent.your-store.example`. It can be at most 64 characters long. Your main domain cannot be used directly, because a CNAME record is not allowed at the top of a domain. The [apex section](#your-main-domain) below shows how to make your main domain answer as well.
- **Access to your DNS provider**, to add two records.

## Connect your domain

```flow start=1
Add the subdomain | On Custom Domains, type the subdomain and press Add domain. AgenticOrg shows the two records to publish, each with a copy button. | Owner: Merchant | If blocked: "This name is the apex of its DNS zone" means you entered your main domain; use a subdomain.
Publish the CNAME record | At your DNS provider, point the subdomain at the address shown, which ends in .web.app. Keep it DNS only: switch off any proxy, CDN or CNAME flattening for this record. | Owner: Merchant | If blocked: Some providers add your domain to the end of the name for you; enter only the first part if so.
Publish the TXT record | Add the TXT record named _agenticorg-ucp.<your subdomain> with the value shown. It ties the subdomain to your AgenticOrg account, so nobody else can claim it. It has its own name, so it never clashes with the CNAME. | Owner: Merchant | If blocked: Copy the value exactly; DNS changes can take up to an hour to be seen.
Verify records | Press Verify records. AgenticOrg checks both records and lists every problem with its fix. | Owner: Merchant | If blocked: See "If a check fails" below, fix the record and press Verify records again.
Connect the domain | AgenticOrg connects the domain by itself within a few minutes. Nobody needs to act. | Owner: AgenticOrg | If blocked: A connection problem shows "Needs attention" with the reason and a Retry button.
Certificate, then live | Google issues the certificate, usually within the hour and at most 24 hours. AgenticOrg checks every few minutes and marks the domain Live. | Owner: Google and AgenticOrg | If blocked: After 48 hours without a certificate the domain shows a failure; check that the CNAME is DNS only.
```

The progress bar on the page shows the same five stages: publish DNS records, verify ownership, connect domain, issue certificate, live. Each stage is marked done, current or failed.

Your profile is served on the domain once it is published. That means your signing key exists, you pressed publish, and Grantex allows public discovery for the host. If Sell everywhere is on, select **Publish** on the Sell Everywhere page once the domain is live: it creates a missing signing key, publishes your profile on the domain and shows the exact reason if anything still waits. See [Merchant setup checklist](/docs/oacp-merchant-setup), phase 3.

**If the page also asks for a Search Console owner,** your deployment still connects domains through Cloud Run domain mappings. In that case:
- The CNAME points at `ghs.googlehosted.com`.
- Google connects the domain only for an account that owns it in Search Console, so add the account shown on the page under Settings, Users and permissions, Verified owners, Add an owner, then tick the box on the page.
- Your platform operator then connects the domain with one command.

## The records

| Type | Name | Value | Why |
| --- | --- | --- | --- |
| `CNAME` | `agent.your-store.example` | the address shown, ending in `.web.app.` | Sends traffic for the subdomain to AgenticOrg, and lets Google issue its certificate. |
| `TXT` | `_agenticorg-ucp.agent.your-store.example` | `agenticorg-ucp-verify=<code shown>` | Ties the subdomain to your AgenticOrg account. Keep it while the domain is connected. |

Two more rules keep the certificate working:

- **Keep the CNAME DNS only.** On Cloudflare that means the grey cloud. Also turn off **Always Use HTTPS** for it, so the certificate checks get through.
- **Check your CAA records.** If your domain has CAA records, they must allow the certificate authorities Google uses: `pki.goog` and `letsencrypt.org`.

## If a check fails

| Message | What it means | What to do |
| --- | --- | --- |
| The TXT ownership record was not found | The TXT record is missing or holds another value | Publish the exact value shown and check again in a few minutes |
| No CNAME record was found | The subdomain has no CNAME, or a proxy hides it | Add the CNAME and set it to DNS only |
| The CNAME record points somewhere else | The found target is shown | Point it at the target on the page |
| DNS did not answer in time | A temporary lookup problem | Check again in a few minutes |
| Confirm that you added the mapping account | Only on deployments using domain mappings: the records are right but the Search Console box is not ticked | Add the owner, tick the box, check again |
| Already connected to another organization | Another AgenticOrg customer proved control of this host first | Remove it there first |
| Already connected to another merchant | Another merchant in your organization holds the host | Remove it there first |
| AgenticOrg could not connect the domain | The connection was refused; the reason is shown, for example that the domain is connected to another Firebase project | Fix the cause and press Retry |
| Google is issuing the certificate | Nothing is wrong; it takes up to 24 hours | Wait |

**Retry and check status.** The button on the page always does the right thing for the domain's current state. It verifies records, checks the certificate, or retries a failed domain. A failed domain keeps its subdomain reserved for you until you remove it.

**Change or remove.** To use a different subdomain, remove the current one first with the bin button. Removing it stops your profile being served there at once. AgenticOrg asks your platform operator to disconnect it, and that request stays in the operator queue until it is done.

## Your main domain

Many agents look for `https://your-store.example/.well-known/ucp`. Keep your website where it is, and let your web server pass three paths through to your connected subdomain:

| Path | Match |
| --- | --- |
| `/.well-known/ucp` | exact |
| `/.well-known/ucp/` | everything under it (older UCP releases) |
| `/ucp/` | everything under it (catalog, locations, checkout) |

The rules for these paths:

- **Never redirect.** UCP does not allow a redirect for the profile, and agents will not follow one.
- **Send the request to the subdomain with `Host` set to the subdomain.** AgenticOrg recognises your shop by that name.
- **Keep the method, body and headers.**
- **Use HTTPS on your main domain.**

Agents that sign their requests should call the address your profile names, which is your subdomain. A signed request forwarded from your main domain no longer matches the name it was signed for.

On **Cloudflare**, use a Worker. Workers are available on every Cloudflare account; check Cloudflare's current request limits for yours. Bind it to the routes `your-store.example/.well-known/ucp*` and `your-store.example/ucp/*`:

```js
const AGENT_HOST = "agent.your-store.example";
export default {
  async fetch(request) {
    const url = new URL(request.url);
    url.hostname = AGENT_HOST;
    return fetch(new Request(url, request), { redirect: "manual" });
  },
};
```

On **nginx**, add three locations to the server block for your main domain:

```nginx
resolver 1.1.1.1 8.8.8.8 valid=300s;
set $agent_host agent.your-store.example;
location = /.well-known/ucp { include snippets/ucp-pass.conf; }
location ^~ /.well-known/ucp/ { include snippets/ucp-pass.conf; }
location ^~ /ucp/ { include snippets/ucp-pass.conf; }
```

And the shared `snippets/ucp-pass.conf`:

```nginx
proxy_pass https://$agent_host;
proxy_set_header Host $agent_host;
proxy_ssl_server_name on;
proxy_ssl_name $agent_host;
proxy_ssl_verify on;
proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
proxy_redirect off;
```

Check it with `curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://your-store.example/.well-known/ucp`. It must print `200` and no redirect address. The full guide is in the repository at `docs/oacp/merchant-apex-domain.md`.

## For platform operators

### How domains reach AgenticOrg

The recommended setup puts a Firebase Hosting site in front of the API service:
- Each merchant subdomain is a custom domain on that site, which AgenticOrg creates and removes through the Firebase Hosting API.
- The site sends only the agent paths to the API: `/.well-known/ucp`, `/ucp/`, `/acp/` and the host check. Every other path on a merchant domain is a plain 404.
- Firebase passes the merchant's domain to the API in `X-Forwarded-Host`. AgenticOrg accepts it only for domains it connected itself, and only on those paths.

The older setup maps each domain to the API service with a Cloud Run domain mapping. Google then requires a Search Console owner and an operator command for every merchant.

### Turn it on

Firebase mode needs the `v6z91` and `v6z93` database migrations, which the normal deploy runs with `--with-migrations`. Then, once:

```flow start=1
Add Firebase to the project | Enable the Firebase and Firebase Hosting APIs and add Firebase to the Google Cloud project of the API service. | Owner: Platform operator | If blocked: Adding Firebase needs the project owner's account.
Create the merchant domains site | Create one Firebase Hosting site, for example agenticorg-merchant-domains. Its address, <site>.web.app, is the CNAME target merchants publish. | Owner: Platform operator | If blocked: Site ids are global; pick another if the name is taken.
Release the agent-only configuration | Run python scripts/release_merchant_domain_site.py --project <project> --site <site>. It sends the agent paths to the API and nothing else. | Owner: Platform operator | If blocked: Run it with your own Google account signed in to gcloud.
Allow AgenticOrg to manage the site | Grant the API's service account the Firebase Hosting Admin role (roles/firebasehosting.admin) on the project. | Owner: Platform operator | If blocked: Without it, every domain fails with a permission message.
Set the settings | Set the settings below on the API and worker services, then move traffic to the new revisions. | Owner: Platform operator | If blocked: The exact commands are in the repository at docs/oacp/merchant-custom-domains.md.
```

| Setting | Value |
| --- | --- |
| `AGENTICORG_CUSTOM_DOMAINS_ENABLED` | `true`. Off, the Custom Domains routes answer 404 and the status checks stop. |
| `AGENTICORG_CUSTOM_DOMAIN_FIREBASE_ENABLED` | `true`, to connect new domains through Firebase Hosting. |
| `AGENTICORG_CUSTOM_DOMAIN_FIREBASE_SITE` | The site id, for example `agenticorg-merchant-domains`. |
| `AGENTICORG_CUSTOM_DOMAIN_CLOUD_RUN_PROJECT` | The Google Cloud project, also the Firebase project. |
| `AGENTICORG_CUSTOM_DOMAIN_OPERATOR_TENANT_ID` | Your own AgenticOrg organization's id, for the operator queue. Your browser shows it at `/api/v1/auth/me` while you are signed in. |
| `AGENTICORG_CUSTOM_DOMAIN_OPERATOR_EMAIL` | The inbox for domains that need attention under the older setup. |

The deploy script adds to the existing settings rather than replacing them, so they survive later deploys.

### Work the queue

With Firebase Hosting, AgenticOrg connects and disconnects domains by itself. A Firebase domain appears in the **Operator queue** only when it fails, for example because the domain is connected to another Firebase project. It shows the reason and needs no command.

Domains on the older domain-mapping setup still need you:

```flow start=1
Connect a new domain | Copy the command from the queue item or the email and run it signed in as the mapping account. It looks like gcloud beta run domain-mappings create --service=agenticorg-api --domain=<host> --project=<project> --region=asia-southeast1. | Owner: Platform operator | If blocked: "Domain not verified" means the merchant has not added your account in Search Console; press Mark failed with that reason.
Mark it mapped | Press Mark mapped. AgenticOrg then watches for the certificate and marks the domain live by itself. | Owner: Platform operator | If blocked: If you forget, AgenticOrg still finds the live certificate on its next check.
Disconnect a removed domain | When a merchant removes a connected domain, a "Delete mapping" item appears. Run its gcloud beta run domain-mappings delete command, then press Mark deleted. | Owner: Platform operator | If blocked: If the item says another domain now uses the host, keep the mapping and press Mark done.
```

Every operator action is written to the merchant's commerce ledger with you as the actor.

### Platform addresses for Sell everywhere

Shops that turn on Sell everywhere without an address of their own get `<shop>.<apex>`, for example `tea-corner.agenticorg.ai`. AgenticOrg owns the apex, so the address is attested without a TXT record; it is created as a Firebase Hosting custom domain and followed to a live certificate like any other.

- It needs the Firebase setup above and one wildcard record in the apex's DNS zone: `*.<apex> CNAME <site>.web.app.`
- `AGENTICORG_PLATFORM_SUBDOMAIN_APEX` names the apex (`agenticorg.ai` by default).
- `AGENTICORG_PLATFORM_SUBDOMAIN_CAP` (20 by default) caps how many are given out, because Firebase connects at most 20 custom domains per domain. Past the cap, a shop can still turn on Sell everywhere and is asked to connect its own domain.

### Limits and when to change approach

**Firebase Hosting:**
- Each merchant domain can have at most 20 subdomains connected. The platform addresses all sit under one apex, so they share that limit.
- A request can take at most 60 seconds.
- Responses that do not set their own caching are marked not to be cached, so Firebase never serves a stale answer.
- Hosting meters storage and data transfer beyond its monthly allowance. The site stores no files, and agent responses are small.

**Cloud Run domain mappings** (older setup) are still a Google preview:
- Google warns of extra latency.
- A connected host reaches the whole API service.
- Google allows 50 certificates for each top domain in a week.

A load balancer with managed certificates would replace either setup when a paying merchant needs a service level or traffic must be filtered per host. It is not needed today. At list prices checked on 2026-10-21, it costs about $19 a month for 10 to 100 merchants and about $199 a month for 1,000.

### Turn it off

Set `AGENTICORG_CUSTOM_DOMAINS_ENABLED=false`. The Custom Domains pages, routes and checks stop at once. Turning only `AGENTICORG_CUSTOM_DOMAIN_FIREBASE_ENABLED` off sends new domains to the older setup, and makes domains already connected through Firebase answer 404 until it is on again. Remove those domains first if you are leaving Firebase for good.
