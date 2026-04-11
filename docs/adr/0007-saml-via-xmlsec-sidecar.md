# ADR 0007: SAML 2.0 via an xmlsec sidecar (deferred from v4.7.0)

- **Status**: Proposed (target: v4.8.0)
- **Date**: 2026-04-11
- **Deciders**: Sanjeev (CTO), Auth team

## Context

In v4.7.0 we shipped OIDC-based SSO but explicitly deferred SAML
because `python3-saml` depends on the `xmlsec1` C library + Python
bindings, which:

- Won't `pip install` cleanly on Windows dev boxes (the user's
  primary development environment).
- Requires platform-specific wheels or apt-get builds.
- Has a long history of CVEs that we'd inherit.

But several Indian enterprise customers (notably the CA-firm pack
target accounts) still run on-premise IdPs that only speak SAML, not
OIDC. We can't ignore SAML.

## Options considered

### Option A — `python3-saml` directly in the API container

- **Cost:** Build a custom Docker image with `libxmlsec1-dev` +
  `xmlsec` Python bindings. Bumps the image by ~25 MB and adds a CVE
  surface.
- **Dev experience:** Broken on Windows. Devs would need WSL2 or
  remote dev.
- **Verdict:** Rejected. We already have devs on Windows and the
  user has been clear about not making the dev workflow harder.

### Option B — A SAML sidecar microservice

- A small Go or Rust service (or a Python image with xmlsec) that
  handles only SAML AuthnRequest creation and AuthnResponse
  validation. The main API talks to it over a unix socket inside
  the pod.
- **Cost:** One extra container per pod, ~50 MB. The sidecar is
  isolated, so a CVE in xmlsec doesn't compromise the API process
  memory.
- **Dev experience:** Devs run the sidecar via `docker-compose up`
  alongside the API. Windows works because Docker Desktop is fine.
- **Verdict:** ✅ **This is the plan.**

### Option C — A managed IdP broker (Auth0, WorkOS)

- Third-party SaaS that translates SAML to OIDC for us.
- **Cost:** $$. Adds a third-party data flow that customers may
  reject for compliance reasons.
- **Verdict:** Rejected — violates the "open-source only" instruction
  and adds vendor lock-in.

## Decision

Adopt **Option B** — a SAML sidecar.

### Sidecar specification

- **Image:** `agenticorg/saml-sidecar:latest`
- **Base:** `python:3.12-slim` + `libxmlsec1-dev` + `python3-saml`
- **Interface:** Unix socket at `/var/run/agenticorg/saml.sock`,
  HTTP/JSON shape so we don't take a Python pickle dependency.
- **Endpoints:**
  - `POST /authn-request` — given an SP entity ID + IdP metadata URL,
    return a base64 SAML AuthnRequest + `RelayState`.
  - `POST /authn-response` — given an IdP-posted base64 response,
    validate the signature against the IdP cert and return the
    extracted attributes.
- **Lifecycle:** Sidecar runs as a separate container in the same pod,
  shares an `emptyDir` volume for the unix socket. Helm chart adds it
  conditionally based on `sso.saml.enabled`.

## Implementation plan (v4.8.0)

1. Create `infra/saml-sidecar/` with the Dockerfile + a thin Flask
   wrapper around `python3-saml`.
2. Publish the image to `ghcr.io/mishrasanjeev/agenticorg-saml-sidecar`.
3. Add `auth/sso/saml.py` in the main repo — talks to the sidecar
   over the unix socket via `httpx[http2]` with `transport=UnixAsyncTransport`.
4. Extend `core/models/sso_config.py::SSOConfig.provider_type` to
   accept `"saml"` (already does — the validation is at the API
   layer).
5. Wire `api/v1/sso.py::sso_login` and `sso_callback` to dispatch to
   the SAML branch when `provider_type == "saml"`. Currently it
   raises `HTTPException(400, "Only OIDC is supported")`.
6. Add an integration test using a `saml-test-idp` Docker image
   (Shibboleth provides one).
7. Update the Helm chart to add the sidecar container behind a
   feature flag.

## Risks

- **Sidecar restart cascade:** if xmlsec crashes inside the sidecar,
  Kubernetes will restart only that container — the API stays up but
  SAML logins fail. We'll add a `/healthz` on the sidecar and a
  liveness probe that monitors it independently from the main app.
- **CVE management:** the sidecar gets its own dependabot alert
  stream. The Auth team is the on-call owner for sidecar CVEs.
- **Latency:** unix socket adds ~1 ms vs in-process. Acceptable for
  a once-per-login operation.

## Out of scope

- **SAML SLO** (Single Logout) — most customers don't use it. Add
  later if anyone asks.
- **SAML metadata signing** — we'll publish unsigned metadata in v1
  and add signing once we have a customer that needs it.

## References

- python3-saml: https://github.com/SAML-Toolkits/python3-saml (MIT)
- xmlsec1: https://www.aleksey.com/xmlsec/ (MIT-style)
- Shibboleth test IdP image:
  https://github.com/UniconLabs/simple-saml-php-idp
