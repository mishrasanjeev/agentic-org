# Merchant onboarding desk: agent identity and the agent-ready offer

This page covers two things. **Agent identity** checks, before each action a desk or case agent takes, that the agent's identity credential is still active and that its Grantex grant is bound to that identity. The **agent-ready offer** lets an approved merchant enrol its verified business with the identity provider so AI agents can find it.

Both are off by default. With agent identity off, no identity provider is called, the evidence pack and report have the same sections and figures as before, and every agent acts exactly as it did.

## What agent identity does

Each agent the desk runs has an **agent identity credential** held by an identity provider:

| Field | Meaning |
|---|---|
| Identifier | The agent's identifier at the identity provider |
| Attestation | A statement about the agent from an accredited issuer, as a compact JWS or an opaque string. The desk records its SHA-256, never the attestation itself |
| Trust tier | The tier the attestation gives the agent |
| Allowed scopes | What the attestation lets the agent do |
| Status | `active`, `revoked`, `suspended`, `expired` or `unknown` |
| Expiry | When the credential stops being valid |
| Key thumbprint | The RFC 7638 thumbprint of the agent's public key |

The agents are:

| Role | Action | Scope it needs |
|---|---|---|
| `onboarding_outreach` | Sends first contact and reminders | `agenticorg:onboarding_desk:send:outreach` |
| `onboarding_conversation` | Replies to text messages | `agenticorg:onboarding_desk:reply:sms` |
| `business_underwriter` | Runs the case checks | `tool:<provider>:read:<tool>` for each provider call |
| `screening_disposition` | Proposes screening dispositions | `tool:<provider>:read:<tool>` for each provider call |

Before each action the desk:

1. Asks the identity provider for the agent's credential and its current status.
2. Checks the agent's Grantex grant: it must be active and carry `cnf.jkt` equal to the credential's key thumbprint.
3. Acts only when the action's scope is in both the attestation's allowed scopes and the grant's scopes. This is the narrower of the two sets.

The result is cached for a short time (the status cache setting), and never past the credential's own expiry. Failures are never cached.

## Settings

| Setting | Default | Effect |
|---|---|---|
| `AGENTICORG_ONBOARDING_DESK_AGENT_IDENTITY_ENABLED` | `false` | Turns agent identity on for the deployment |
| `AGENTICORG_ONBOARDING_DESK_AGENT_IDENTITY_PROVIDER` | empty | The registered identity provider. Empty while identity is on: every agent action is held |
| `AGENTICORG_ONBOARDING_DESK_AGENT_IDENTITY_STATUS_CACHE_SECONDS` | `60` | How long a status and grant check is reused, from 0 to 3600 |

The Grantex calls use `GRANTEX_BASE_URL` and `GRANTEX_API_KEY`. The issuer must be named explicitly; without it, grant checks hold every action with `grantex_not_configured`.

Each desk role needs one active, tenant-shared agent of that type (`onboarding_outreach`, `onboarding_conversation`) with `config.grantex.grantex_agent_id`, as the case agents already do.

## Binding the agent's key to its grant

`POST /api/v1/onboarding-desk/agent-identity/{role}/bind` (tenant administrator) registers the agent's public key on its Grantex agent and proves possession of it:

1. `GET /v1/agents/{id}/keys` at Grantex. If the key is not there, `POST /v1/agents/{id}/keys` registers it as `pending`.
2. `POST /v1/agents/{id}/keys/{thumbprint}/challenge` returns a nonce.
3. The identity provider signs the proof (`typ` `agent-key-proof+jwt`, claims `aud`, `sub`, `nonce`, `iat`) with the agent's private key, which never leaves the provider.
4. `POST /v1/agents/{id}/keys/{thumbprint}/prove` makes the key `active`.

From then on, grants Grantex issues or delegates to the agent carry the key's thumbprint. Grantex has no route that binds a key to an existing grant, so the desk checks the binding on each grant token instead (`POST /v1/grants/verify`).

`GET /api/v1/onboarding-desk/agent-identity` shows each agent's check now: `allowed`, `held` or `stopped`, the reason, the identity fields, the grant and the narrowed scopes.

## Failure behaviour

| Answer | Outreach | Text reply | Case checks |
|---|---|---|---|
| Active, bound grant, scope allowed | Sends; the attempt records the identity | Replies; the transcript records the identity | Runs; each provider call records the identity |
| `revoked`, `suspended` or `expired`, a grant that is inactive or not bound to the key, or a scope outside the narrower set | Stopped: not sent. A new lead is checked again in 24 hours; other leads are parked | No reply; the message is kept in the transcript | Refused with `authorization_unavailable` before the case moves; a call refused mid-run fails that call |
| `unknown`, not found, the provider or Grantex unreachable or not configured | Held: rescheduled in 15 minutes, like any held contact | No reply; the message is kept | Refused with `authorization_unavailable` before the case moves |

Every desk action, allowed or not, is recorded with its outcome and reason.

## Presented identity

A verification provider can declare `accepts_presented_identity`. For such a provider, the case investigator's `verify_business` call carries `VerifyOptions.presented_identity`: the identifier, attestation, attestation hash and trust tier. A provider that does not declare it never receives one. The call record says whether the identity was presented. The mock verification provider accepts it and echoes the identifier in the verification handle's `accepted_identity`.

## How a provider adapter plugs in

An identity provider implements `connectors.framework.agent_identity_provider.AgentIdentityProvider`:

| Method | Does |
|---|---|
| `resolve(agent, deadline=)` | Returns the agent's credential, or raises `AgentIdentityNotFound` |
| `check_status(identifier, deadline=)` | Returns the current status; raises `AgentIdentityUnavailable` when it cannot answer |
| `present(credential, audience=, deadline=)` | Returns what the agent shows a verification provider |
| `prove_possession(credential, claims, typ=, deadline=)` | Signs a key-possession proof with the agent's key |
| `enrol_business(request, deadline=)` | Takes a verified business for agent enrolment and returns the provider's reference and status |

A private package registers its class in the `agenticorg.agent_identity_providers` entry-point group:

```toml
[project.entry-points."agenticorg.agent_identity_providers"]
example_identity = "example_identity_agenticorg.provider:ExampleIdentityProvider"
```

Plugins load only with `AGENTICORG_PLUGIN_LOADING=true` and the distribution named in `AGENTICORG_PLUGIN_ALLOWLIST`, and never replace a native provider. The in-repository `mock` provider runs only where `AGENTICORG_ENV` is local, development or test. Its fixtures are `active` (the default), `revoked`, `suspended`, `expired`, `unknown` and `unreachable`, set with `AGENTICORG_MOCK_AGENT_IDENTITY_FIXTURE` or per role in tests.

## The agent-ready offer

A partner turns the offer on per tenant:

```bash
curl -s -X PUT https://app.example.test/api/v1/onboarding-desk/agent-ready-settings \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"enabled": true, "offer": {"en": "", "fr": ""}, "max_verification_age_days": 90}'
```

| Setting | Default | Effect |
|---|---|---|
| `enabled` | `false` | Shows the offer after approval |
| `offer.en`, `offer.fr` | Built-in wording | The offer text, at most 320 characters. Empty uses the built-in wording |
| `max_verification_age_days` | `90` | The oldest business verification the desk will share, from 1 to 365 days |

The offer also needs agent identity on for the deployment, because the enrolment goes through the identity provider.

### When the offer appears

Only after the hand-off: the application is submitted and a person approved its governed case. The offer appears on the applicant page and as the reply to the applicant's text messages. It never changes the application, the case or its decision.

### Accepting

1. The applicant gives their name, role and contact and ticks the box (`POST /apply/agent-ready/accept`).
2. The desk records a consent with purpose `agent_enrolment` on the `web` channel. The wording names the person and their role, and the person's details are stored encrypted.
3. The desk asks Grantex for a consent grant for that person (`POST /v1/authorize`, scope `agenticorg:onboarding_desk:enrol:agent`, purpose `x-agenticorg.agent_enrolment`). The person approves it on the Grantex consent page. Without a configured Grantex issuer the offer cannot be accepted (`consent_service_not_configured`).
4. `POST /apply/agent-ready/continue` finds the active grant and hands the business to the identity provider.

### What is shared

Only the business verification reference (the case, the verification provider and its reference, and when the business was verified) and fields the desk verified against the registry:

- the jurisdiction and registry identifiers from the registry's own record;
- the registry status, when the registry reported the business active;
- the legal name, only when the registry matched the business, reported it active and raised no name finding.

Owners, addresses, activity, contact details and anything else the applicant only declared are never shared. The provider's reference and status are recorded as received.

### Old verifications

A business verification older than `max_verification_age_days` is never shared. The applicant is asked to verify their business again, on the page and by text message.

### Withdrawing

`POST /apply/agent-ready/withdraw` withdraws the consent and revokes the Grantex grant (`DELETE /v1/grants/{id}`). Before any sharing the desk checks that the consent is still granted and the Grantex grant still active, so a withdrawal, or a grant the person revoked at Grantex, blocks any further sharing. What was already shared stays with the identity provider; the desk has no route to recall it.

## Evidence and reporting

With agent identity on, the evidence pack gains two sections, and the report gains `funnel.agent_ready`. See [cases, evidence and reporting](cases-evidence-reporting.md).

| Section | Holds |
|---|---|
| `agent_identity` | `actions` (each desk action, its outcome and reason), `contact_attempts`, `replies` and `case_tool_calls`, each with `agent_identity_id`, `attestation_sha256` and `trust_tier` |
| `enrolment` | Each enrolment: status, consent and consent grant, shared fields, verification reference, provider reference and status, and its times |

## Records

Migration `v6z121_desk_agent_identity` adds:

| Change | Holds |
|---|---|
| `onboarding_desk_contact_attempts` columns | `agent_identity_id`, `agent_attestation_sha256`, `agent_trust_tier` |
| `onboarding_desk_consents` | The `web` channel and `subject_ciphertext`, the encrypted person behind an agent-enrolment consent |
| `onboarding_desk_agent_actions` | Each desk agent action, its outcome, reason, identity fields and grant |
| `onboarding_desk_enrolments` | Each enrolment: consent, consent request and grant, status, provider reference and status, shared fields, verification reference and times |

Both new tables are tenant-scoped under forced row-level security.

## Who may do what

| Action | Needs |
|---|---|
| Read the agent-ready settings and the agent identity checks | `audit:read` |
| Change the agent-ready settings, bind an agent's key | `approvals:write` and a tenant administrator |
| Accept, continue or withdraw the offer | The applicant's session |
