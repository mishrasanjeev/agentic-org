# Configurable channel workflow validation

## Scope and evidence

This change provides customer-owned channel credentials, signed inbound text
callbacks, private-account delegation or single-message review, durable dispatch
to existing agents/workflows, encrypted receipt/results, and a setup UI. New
agent defaults use `gemini-3.5-flash-lite`; explicitly stored model choices and
separate routing-tier overrides are preserved.

Local validation uses a dedicated Docker project with the real API,
PostgreSQL, Redis, worker, beat and UI. Callback bodies and secrets are synthetic.
The agent model's final response is scripted locally; it is not a Google API
call. The tests do not contact native channel providers or make telephone calls.

| Check | Observed result | Boundary proved |
|---|---|---|
| HTTP/worker channel E2E | Passed, 53 seconds | Disabled/bad-signature refusal; one-message review; private linking; duplicate/content-mismatch refusal; rotation/revocation; native callback parsing; actual workflow and LangGraph dispatch; paused target refusal |
| Chromium desktop/mobile | Passed, 13 seconds | Real signup, workflow selection, protected credential form, save/enable/replace, no secret returned, responsive layout and public workflow guide |
| Isolated PostgreSQL RLS | Passed | Full migration chain; non-superuser/non-bypass role; cross-tenant reads/writes refused on all three new tables; another owner cannot read a binding |
| Default/provider/auth regression replay | 164 passed | Explicit legacy models preserved, configured default respected, catalog/credential access separation, no new authentication exemption overlap, vault startup tests and isolated metrics environment |
| Native provider and setup unit tests | 50 passed | Signatures, receiver scope, bounded payloads, private Telegram identity, replay windows, malformed replies, access-log token redaction, confirmed Telegram registration, foreign-callback refusal, owner-scoped activation and local/production callback separation |
| UI production build and SEO verification | Passed | Public guide is prerendered; sitemap/canonical/CSP/generated documentation remain synchronized |

Full preflight and exact PR/main CI results are release gates, not inferred from
these focused results. Record their final results on the PR before merge. Do
not describe this report as production verification.

## Reproduced problems and corrections

- A paused channel target initially ran because the legacy direct-run pause
  policy defaults off. New channel dispatch independently rechecks ownership
  and active/shadow lifecycle state. The same HTTP/worker test now observes
  refusal after pausing the target.
- The first full suite exposed stale Gemini default expectations and the new
  non-secret registry defaults field. Assertions now reflect the deliberate
  default change while preserving explicit legacy-provider tests and access
  denials.
- Management route parameters initially shared the public webhook prefix.
  Management mutations now use `/channels/bindings/{binding_id}`; the existing
  authentication overlap test passes without a new baseline exception.
- An existing metrics test leaked a temporary multiprocess directory into
  later subprocess tests. Its fixture now restores the environment and value
  class, so import/vault tests replay independently and in the suite ordering.
- Meta challenge tokens arrive in URL queries. Application access logs redact
  plain and percent-encoded token keys. Infrastructure/provider logs can still
  retain URLs; never reuse an API key or app secret as the verification token.
- A local binding could initially display the production callback origin.
  Local and staging defaults now stay local; the Docker overlay supplies its
  actual UI origin. Real Telegram registration refuses non-public HTTPS
  callbacks, rather than registering a local binding on production.

## Design review

The repository's optional external design command pack was unavailable. A
manual audit/critique/polish review and Chromium screenshots were used instead:
labelled protected fields, keyboard-operable controls, compact three-step setup,
unframed sections, clear disabled/review/error states, credential clearing after
save, bounded result display, and no desktop/mobile page overflow. Native
outbound messages intentionally contain status only, not private tool outputs.
Telegram callback registration is a protected server-side action; users do not
need a token-bearing browser URL or terminal command. Provider setup contract
tests use an in-process HTTP double, not a real Telegram account. A bot attached
to another application's callback is refused rather than overwritten.

## Not verified or not implemented by this release

- Provider account approval, real Google inference/quota, native inbound
  delivery, native outbound delivery, speech accuracy and telephone delivery
  require customer-owned accounts and authorized test recipients.
- Messaging callbacks ingest text only. Images, attachments and audio notes
  are not automatically ingested; existing knowledge/voice flows are separate.
- WeChat uses an approved signed gateway, not a native WeChat adapter.
- Email and generic gateways do not send native outbound replies.
- Changing or disabling a binding cannot undo an already-running external
  action. Ambiguous executions/delivery acknowledgements require review and
  are not blindly replayed.

See the [account guide](../user-guide/channel-workflows.md) and
[migration-first release runbook](channel-workflows-release-runbook.md).
