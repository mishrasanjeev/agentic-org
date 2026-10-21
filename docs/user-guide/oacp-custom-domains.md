```summary
Buyer agents can find your shop on a subdomain you own, such as agent.your-store.example, instead of an AgenticOrg address.
You add two DNS records and one Search Console owner. AgenticOrg checks them and tells you exactly what to fix.
Your platform operator connects the domain with one command. Google then issues its certificate, usually within 15 minutes.
It costs nothing extra: no load balancer, no paid certificate, no new server.
Your main domain (your-store.example) can answer too, through a pass-through rule. A redirect is not allowed there.
```

## What you get

Your UCP profile is the file buyer agents read first. It says where your shop is and what it offers. With a custom domain it is served at `https://agent.your-store.example/.well-known/ucp`, together with your catalog and store locations under `/ucp`.

```figure
oacp-custom-domain | Your own domain, step by step | You add a subdomain, publish two DNS records and add one Search Console owner. AgenticOrg checks them and queues the domain. Your platform operator connects it, Google issues the certificate, and the domain is live.
```

You will use the **Custom Domains** page at `/dashboard/custom-domains`. You need the administrator role or the merchant role. Enter your merchant ID, and your seller agent ID if your shop has more than one, then press **Load**.

## Before you start

- **A subdomain you control.** Use a name such as `agent.your-store.example` or `shop-agent.your-store.example`. It can be at most 64 characters long. Your main domain cannot be used directly, because a CNAME record is not allowed at the top of a domain. The [apex section](#your-main-domain) below shows how to make your main domain answer as well.
- **Access to your DNS provider**, to add two records.
- **Google Search Console** for your domain. If you already use Google Merchant Center, you probably have it.

## Connect your domain

```flow start=1
Add the subdomain | On Custom Domains, type the subdomain and press Add domain. AgenticOrg shows the two records to publish, each with a copy button. | Owner: Merchant | If blocked: "This name is the apex of its DNS zone" means you entered your main domain; use a subdomain.
Publish the CNAME record | At your DNS provider, point the subdomain at ghs.googlehosted.com. Keep it DNS only: switch off any proxy, CDN or CNAME flattening for this record. | Owner: Merchant | If blocked: Some providers add your domain to the end of the name for you; enter only the first part if so.
Publish the TXT record | Add the TXT record named _agenticorg-ucp.<your subdomain> with the value shown. It proves you control the subdomain. It has its own name, so it never clashes with the CNAME. | Owner: Merchant | If blocked: Copy the value exactly; DNS changes can take up to an hour to be seen.
Add the mapping account in Search Console | Google connects a domain only for an account that owns it in Search Console. Add the account shown on the page under Settings, Users and permissions, Verified owners, Add an owner. Keep it while the domain is connected. | Owner: Merchant | If blocked: Verify your domain property in Search Console first if it is not verified yet.
Verify records | Tick "I added ... as an owner", then press Verify records. AgenticOrg checks both records and the confirmation. Every problem is listed with its fix. | Owner: Merchant | If blocked: See "If a check fails" below, fix the record and press Verify records again.
Connect the domain | Your domain joins the operator queue and your platform operator is emailed. The operator runs one command to connect it. | Owner: Platform operator | If blocked: The step shows "Needs attention" with the operator's reason and a Retry button.
Certificate, then live | Google issues the certificate, usually within 15 minutes and at most 24 hours. AgenticOrg checks every few minutes and marks the domain Live. | Owner: Google and AgenticOrg | If blocked: After 48 hours without a certificate the domain shows a failure; check CAA records and that the CNAME is DNS only.
```

The progress bar on the page shows the same five stages: publish DNS records, verify ownership, connect domain, issue certificate, live. Each stage is marked done, current or failed.

Your profile is served on the domain once it is published. That means your signing key exists, you pressed publish, and Grantex allows public discovery for the host. See [Merchant setup checklist](/docs/oacp-merchant-setup), phase 3.

## The records

| Type | Name | Value | Why |
| --- | --- | --- | --- |
| `CNAME` | `agent.your-store.example` | `ghs.googlehosted.com.` | Sends traffic for the subdomain to AgenticOrg. |
| `TXT` | `_agenticorg-ucp.agent.your-store.example` | `agenticorg-ucp-verify=<code shown>` | Proves you control the subdomain. Keep it while the domain is connected. |

Two more rules keep the certificate working:

- **Keep the CNAME DNS only.** On Cloudflare that means the grey cloud. Also turn off **Always Use HTTPS** for it, so Google's certificate checks get through.
- **Check your CAA records.** If your domain has CAA records, they must allow `pki.goog` and `letsencrypt.org`.

## If a check fails

| Message | What it means | What to do |
| --- | --- | --- |
| The TXT ownership record was not found | The TXT record is missing or holds another value | Publish the exact value shown and check again in a few minutes |
| No CNAME record was found | The subdomain has no CNAME, or a proxy hides it | Add the CNAME and set it to DNS only |
| The CNAME record points somewhere else | The found target is shown | Point it at the target on the page |
| DNS did not answer in time | A temporary lookup problem | Check again in a few minutes |
| Confirm that you added the mapping account | The records are right but the Search Console box is not ticked | Add the owner, tick the box, check again |
| Already connected to another organization | Another AgenticOrg customer proved control of this host first | Remove it there first |
| Already connected to another merchant | Another merchant in your organization holds the host | Remove it there first |
| AgenticOrg could not connect the domain | The operator or Google refused the connection; the reason is shown | Usually the Search Console owner is missing. Fix it and press Retry |
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

On **Cloudflare**, use a Worker. It works on every plan; the free plan allows 100,000 requests a day. Bind it to the routes `your-store.example/.well-known/ucp*` and `your-store.example/ucp/*`:

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

### Turn it on

Custom domains need the `v6z91_merchant_custom_domains` database migration, which the normal deploy runs with `--with-migrations`. Then set these on the API and worker services. The beat service only schedules the checks, so it needs none of them. The deploy script adds to the existing settings rather than replacing them, so they survive later deploys. A settings change creates a new revision. If your services pin traffic to named revisions, as the deploy script does, move traffic to the new revision afterwards. The exact commands are in the repository at `docs/oacp/merchant-custom-domains.md`, under "Production turn-on".

| Setting | Value |
| --- | --- |
| `AGENTICORG_CUSTOM_DOMAINS_ENABLED` | `true`. Off, the Custom Domains routes answer 404 and the status checks stop. |
| `AGENTICORG_CUSTOM_DOMAIN_MAPPING_ACCOUNT` | The Google account you run `gcloud` with. Merchants add it in Search Console. |
| `AGENTICORG_CUSTOM_DOMAIN_OPERATOR_TENANT_ID` | Your own AgenticOrg organization's id. Only its administrators see the operator queue. Your browser shows it at `/api/v1/auth/me` while you are signed in. |
| `AGENTICORG_CUSTOM_DOMAIN_OPERATOR_EMAIL` | The inbox that gets a message for each new queue item, with its command. |
| `AGENTICORG_CUSTOM_DOMAIN_CLOUD_RUN_PROJECT` | The Google Cloud project of the API service. The region (`asia-southeast1`), the service (`agenticorg-api`) and the CNAME target (`ghs.googlehosted.com`) have matching settings with these defaults. |

Leave `AGENTICORG_CUSTOM_DOMAIN_CLOUD_RUN_AUTOMAP_ENABLED` off. It would let AgenticOrg create mappings itself, but Google names the Project Editor role for that. That role is far broader than a one-minute command is worth.

### Work the queue

The **Operator queue** appears on the Custom Domains page for administrators of your organization. It is also emailed to you.

```flow start=1
Connect a new domain | Copy the command from the queue item or the email and run it signed in as the mapping account. It looks like gcloud beta run domain-mappings create --service=agenticorg-api --domain=<host> --project=<project> --region=asia-southeast1. | Owner: Platform operator | If blocked: "Domain not verified" means the merchant has not added your account in Search Console; press Mark failed with that reason.
Mark it mapped | Press Mark mapped. AgenticOrg then watches for the certificate and marks the domain live by itself. | Owner: Platform operator | If blocked: If you forget, AgenticOrg still finds the live certificate on its next check.
Disconnect a removed domain | When a merchant removes a connected domain, a "Delete mapping" item appears. Run its gcloud beta run domain-mappings delete command, then press Mark deleted. | Owner: Platform operator | If blocked: If the item says another domain now uses the host, keep the mapping and press Mark done.
```

Every operator action is written to the merchant's commerce ledger with you as the actor.

### Limits and when to change approach

Custom domains use Cloud Run domain mappings, which Google still marks as a preview. They work, with some limits:

- **Latency.** Google warns of extra latency and does not recommend mappings for production.
- **No wildcards and no path routing.** A connected host reaches the whole API service.
- **Certificates.** About 15 minutes to issue, at most 24 hours. Google allows 50 per top domain per week.
- **Names.** At most 64 characters.

A load balancer with managed certificates would replace mappings, when a paying merchant needs a service level or traffic must be filtered per host. It is not needed today. At list prices checked on 2026-10-21, it costs about $19 a month for 10 to 100 merchants and about $199 a month for 1,000.

### Turn it off

Set `AGENTICORG_CUSTOM_DOMAINS_ENABLED=false`. The Custom Domains pages, routes and checks stop at once. Domains already verified keep serving under the UCP profile rules until each merchant unpublishes, or until `AGENTICORG_UCP_PROFILE_ENABLED` is off. Mappings stay in Cloud Run until you delete them.
