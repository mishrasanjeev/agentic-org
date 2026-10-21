# Serving the UCP Profile From Your Apex Domain

Agents often look for a merchant's UCP profile at the merchant's main domain:
`https://merchant.example/.well-known/ucp`. A CNAME record cannot sit at a
zone apex, and the apex usually serves the merchant's website. So AgenticOrg
connects a subdomain ([merchant custom domains](merchant-custom-domains.md)),
and the apex passes the agent paths through to it.

**Before you start:** connect a subdomain such as `agent.merchant.example`
until it shows **Live** on the Custom Domains page. Everything below forwards
to that host.

## The rules

1. **Pass through, never redirect.** UCP 2026-04-08 says profile endpoints
   must not answer with a redirect (3xx), and verifiers must not follow one
   ([UCP overview](https://ucp.dev/2026-04-08/specification/overview/)). A
   `301`, `302`, `307` or `308` from `merchant.example/.well-known/ucp` to the
   agent host makes the profile unusable. Use a reverse proxy that returns the
   upstream response itself. Do not use page rules that forward, redirect
   rules, bulk redirects or `return 301` blocks.
2. **Forward these paths unchanged**, with their query strings:

   | Path | Match | Why |
   | --- | --- | --- |
   | `/.well-known/ucp` | exact | The business profile. |
   | `/.well-known/ucp/` | prefix | Leaf profiles for older UCP releases (`/.well-known/ucp/2026-04-08`). |
   | `/ucp/` | prefix | The UCP REST service (catalog, locations, checkout, cart, order). |

3. **Set `Host` to the agent host** (`agent.merchant.example`) and use it for
   TLS SNI and certificate verification. AgenticOrg chooses the merchant from
   the `Host` header alone. Forwarding `Host: merchant.example` reaches no
   merchant, because Cloud Run routes by host and the apex is not mapped.
4. **Keep the method, body and request headers**, including `Content-Type`,
   `Authorization`, `UCP-Agent`, `Idempotency-Key`, `Signature`,
   `Signature-Input` and `Signature-Agent`. Keep the response status and
   headers, including `Cache-Control` and `ETag`. Do not cache `POST`
   responses. Caching the profile `GET` is fine if you honour its
   `Cache-Control` (`public, max-age=300`).
5. **Serve the apex over HTTPS** with a valid certificate for `merchant.example`.

### What the profile advertises

The profile names its REST service at the attested serving host:
`https://agent.merchant.example/ucp`. Agents that follow the profile call the
agent host directly. The `/ucp/` pass-through is for agents that assume the
discovery origin.

**Signed agent requests.** AgenticOrg checks the RFC 9421 `@authority` of an
agent request against the `Host` it receives. A request signed for
`merchant.example` and forwarded with `Host: agent.merchant.example` does not
match. With `AGENTICORG_HTTPSIG_VERIFY_MODE=enforce` it is refused, and in the
default `log` mode it is recorded as invalid. Signed agents should therefore
call the endpoint the profile advertises. The pass-through is reliable for the
profile and for unsigned reads.

## Cloudflare

Origin Rules can override the `Host` header only on the Enterprise plan. On
every plan, use a Worker: it fetches the agent host and returns its response,
with no redirect. The Workers free plan allows 100,000 requests a day; choose
fail-open or fail-closed for the route when the limit is reached.

```js
// Worker bound to the routes merchant.example/.well-known/ucp* and merchant.example/ucp/*
const AGENT_HOST = "agent.merchant.example";

export default {
  async fetch(request) {
    const url = new URL(request.url);
    url.hostname = AGENT_HOST; // fetch() sends Host and SNI for this name
    // A new Request keeps the method, headers and body; never follow or issue a redirect.
    return fetch(new Request(url, request), { redirect: "manual" });
  },
};
```

- Add both routes in **Workers Routes**: `merchant.example/.well-known/ucp*` and
  `merchant.example/ucp/*`. The first pattern also matches names such as
  `/.well-known/ucpx`, which simply reach AgenticOrg and answer 404.
- The `agent.merchant.example` CNAME to `ghs.googlehosted.com` stays **DNS
  only** (grey cloud). Turn off **Always Use HTTPS** for it, as Google advises,
  so certificate issuance and renewal are not intercepted.
- No Page Rule or Redirect Rule may match these paths.

## nginx

```nginx
# Inside the server block for merchant.example (listen 443 ssl).
resolver 1.1.1.1 8.8.8.8 valid=300s;   # re-resolve the agent host; Google's addresses can change
set $agent_host agent.merchant.example;

location = /.well-known/ucp { include snippets/ucp-pass.conf; }
location ^~ /.well-known/ucp/ { include snippets/ucp-pass.conf; }
location ^~ /ucp/ { include snippets/ucp-pass.conf; }
```

```nginx
# snippets/ucp-pass.conf
proxy_pass https://$agent_host;          # a variable and no URI: the request URI is passed unchanged
proxy_http_version 1.1;
proxy_set_header Host $agent_host;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto https;
proxy_ssl_server_name on;
proxy_ssl_name $agent_host;
proxy_ssl_verify on;
proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
proxy_redirect off;                       # never rewrite or add a Location header
proxy_intercept_errors off;               # pass AgenticOrg's status and body through
```

Remove any `return 301`, `rewrite ... permanent` or `try_files` rule that
matches these paths before them.

## Check it

```bash
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://merchant.example/.well-known/ucp
```

The result must be `200` with no redirect URL. The body must equal
`https://agent.merchant.example/.well-known/ucp`. A `404` from both means the
profile is not published yet (see the [UCP operator flow](ucp-profile.md#operator-flow)),
not a proxy fault.

## The other option: the whole apex on AgenticOrg

A Cloud Run domain mapping can also take the apex itself, using the A and AAAA
records the mapping lists. The apex would then serve only AgenticOrg, and the
merchant's website would have to move elsewhere. AgenticOrg does not offer this
in the self-service flow. Use the pass-through above.
