## Agent Security Deployment And Evidence Guide

Use [CERT-In CIAD-2026-0049](https://www.cert-in.org.in/s2cMainServlet?pageid=PUBVLNOTES02&VLCODE=CIAD-2026-0049)
and [CIAD-2026-0046](https://www.cert-in.org.in/s2cMainServlet?pageid=PUBVLNOTES02&VLCODE=CIAD-2026-0046)
as primary references. If a detail page is unavailable, CERT-In's
[official 2026 advisory index](https://www.cert-in.org.in/s2cMainServlet?pageid=PUBADVLIST02&year=2026)
lists the titles and issue dates. Detail-page availability was intermittent when
checked on 17 October 2026. This guide does not confer certification, legal compliance,
bank approval or permission to conduct a live financial transaction.

## Responsibility Boundaries

Tenants configure their own approved SSO/OIDC connections and group mappings;
they do not receive platform-operator privileges by controlling an IdP. Require
MFA in the tenant IdP's application policy, test non-admin and missing-factor
refusals, and record the exact issuer, audience, redirect URI and group mapping.
An endpoint-discovery test alone proves none of these. Keep machine credentials
and recovery access separately protected. This release does not switch any
tenant's MFA policy or platform IAM/IAP configuration on automatically.

AgenticOrg coordinates work, applies configured action/tenant policies and
records evidence. Grantex supplies delegated authorization primitives. The
operator owns deployment isolation, IAM, network policy, identity-provider MFA,
security-log storage and incident response. External systems still enforce
their own permissions and business rules. None of these boundaries is replaced
by a prompt or a model's confidence score.

Existing inventory records do not automatically discover every deployed agent,
API or third-party tool. Reconcile the runtime catalog with cloud assets, service
accounts, installed packages, scheduled jobs and external credentials. Record an
owner and decommission decision for dormant endpoints and identities.

## Separate Run Authorization

The source adds an opt-in control; the compatibility default remains off:

```bash
AGENTICORG_GRANTS_ISOLATE_RUN_TOKENS=true
AGENTICORG_GRANTS_ENFORCE_CLOSED=deny
AGENTICORG_GRANTS_RUN_TOKEN_TTL_SECONDS=900
```

When isolation is enabled, the run-grant resolver forces deny mode even if the
deployment supplied off or warn. It obtains a delegated grant for a server-created
run authorization context rather than sharing the same cache entry across
separate contexts. Refresh preserves the isolation key. Governed-case calls in
one authorizer share that context; another authorizer receives another key.

A legacy static token in agent configuration no longer substitutes for the run's
delegated grant. A supplied external token remains an additional restriction;
it does not become a shortcut around fresh run authority. Ambiguous supplied
and caller tokens are refused. Missing registration, scopes, root authority,
issuer availability or valid delegation prevents authorized tool dispatch.

The root credential must come from a secret manager and the configured registered
agent/scopes must be reviewed. Tokens expire; this setting does not automatically
revoke a grant the moment a run completes. It does not create a separate OS user,
container or cryptographic agent key per run. It also does not bind a JWT to one
case ID or enforce case-purpose/cap semantics that the SDK call does not receive.
Do not describe this as complete per-case authorization or complete advisory
coverage. See the [governed-case boundary](../user-guide/governed-cases.md).

## Runtime And Network Isolation

Run evaluations with synthetic targets and credentials in disposable infrastructure.
Use a network-enforced default-deny policy and allow only reviewed destinations,
ports and protocols. Apply it to API workers, model/tool runtimes, browser or code
execution and remote-tool transports, not just the public API container.

The public-URL validator and DNS-pinned transport reject unsafe destinations on
the paths that use them. They are not a global firewall and do not prevent an
unrelated SDK, shell or connector from opening another socket. Cloud Run VPC
routing alone is not an allowlist. Use a reviewed firewall or authenticated
egress proxy, and test direct-connect bypass. Keep policy changes, cloud role
administration and workload restarts outside the agent's permissions.

Do not hand provider keys to the model or persist them in plaintext workflow
state. Use tenant-aware encrypted connector configuration or secret references.
If an agent encounters an unexpected credential, stop the run, quarantine that
credential through the operator's shared suppression/rotation process and
investigate. Do not try it against a real endpoint to establish validity. Masking
a secret in logs is not revocation or cross-run quarantine.

## Human Authority And Containment

Sensitive actions need an approved policy and authenticated human authority at
the backend execution boundary. Separate preparation from approval and execution.
For financial work, the maker cannot approve their own action; a payment executor
must independently verify the approved record, beneficiary, amount, expiry,
replay state and cumulative ceilings. A case recommendation is not activation.

Configure independent operator override and upstream revocation. Verify their
effect on active workers, resumed work and cached grants. Do not let the same
agent change its scopes, remove its own audit trail or restore its root grant.
Human decisions, API-key access and service identities have different purposes;
an API key is not MFA. Protect privileged administration through private ingress
and an approved MFA-enforcing identity provider. Test the backend origin as well
as the public domain.

## Monitoring, Retention And Evidence

Centralize security-relevant request and action logs. Retain the applicable
security logs for at least 180 days, or longer where another requirement applies.
Verify the actual bucket and sink, exclusions, access restrictions and legal
holds. A seven-year audit setting does not prove a separate cloud request-log
bucket has equivalent retention. Extending a bucket cannot restore expired logs.

Capture actor/run/grant references, tool, destination, port, protocol, timestamp
and result at the appropriate boundary; exclude tokens, raw credentials and
sensitive payloads. Configure per-second bursts, enumeration, bulk-read and
automated-registration alerts, and verify alert receipt. Process-local limits
are not a deployment-wide ceiling. Hash-linked evidence still needs protected
storage and independent verification; it is not proof that a privileged operator
cannot delete a database or bucket.

## Operational Control Register

| Area | Required evidence before an enabled production rollout |
|---|---|
| Inventory | Reconciled agents, tools, APIs, workloads, accounts and owners |
| Secrets | Commit/history/build/config/cloud scan, issuer rotation records, credential suppression across future runs |
| IAM | Least-privilege roles, tenant isolation and anonymous read/write denial tests |
| Privileged access | Private ingress, MFA policy, authorized operator mapping and direct-origin bypass test |
| Runtime authority | Separate run grants, scope attenuation, online revocation and denial under issuer/cache failures |
| Network | Enforcing firewall/CNI/proxy, exact allowlist and denied socket test for every execution runtime |
| Monitoring | Live destination/action telemetry, burst/bulk-read alerts and on-call receipt |
| Retention | Bucket read-back, sink routing, exclusions, evidence integrity and custody |
| Patching | Deployed image digest, middleware/dependency scan and retest |
| Sensitive decisions | Human session, step-up and distinct approver tests; exact action binding |
| Financial execution | Approved references, beneficiaries, ceilings, sequence binding, velocity and reconciliation at the payment API |
| Response | Independent stop, evidence preservation, rotation, root-cause remediation, external retest and reporting assessment |

The operator must record verified configuration and test artifacts, not mark rows
complete because a feature exists in a repository. Unknown and disabled controls
remain open. Never call a sandbox merchant pilot a compliance-approved live release.

## Incident And Recovery Procedure

Stop and isolate the affected run without destroying evidence. Preserve original
timestamps, relevant logs, image digests, authority identifiers and configuration;
restrict access and record custody. Revoke affected grants, rotate credentials
at every actual issuer and investigate adjacent accounts and persistence. Patch
root causes and replay the original failure plus sibling paths in isolation.
Retest externally through an authorized independent party before restoring access.
The incident commander and legal/security owners assess applicable reporting
obligations using the official CERT-In channels. Do not wait for a framework or
identity-verification provider to make that decision for the organization.

## Safe Adoption Sequence

Start one synthetic workflow locally, set deny enforcement, register the smallest
tool set and test cross-tenant, revoked-grant, missing-approval and denied-egress
paths. Add run-token isolation after issuer registration/root permissions are
validated. Configure private MFA administration, network enforcement, retention
and alerts. Capture evidence and approve a staged rollout with rollback and
independent containment. Do not enable a sensitive or live onboarding flow while
any of its required gates are unresolved.
