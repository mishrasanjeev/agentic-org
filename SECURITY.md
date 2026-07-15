# Security Policy

## Report a vulnerability

Do not open a public issue for a suspected vulnerability. Email
**sanjeev@agenticorg.ai** with a description, reproducible steps, affected
version or commit, and any impact you observed. Avoid including live secrets or
unnecessary personal data.

We coordinate acknowledgement, triage, remediation, and disclosure directly
with the reporter. This repository does not publish a fixed response or repair
service level; contractual support terms, if any, belong in the applicable
signed order form or support agreement.

## Supported versions

Support status is determined by the release policy and the customer's signed
agreement at the time of a report. A branch, tag, package version, or file in
this repository is not by itself evidence that a release is currently
supported or deployed.

## What the repository can demonstrate

The repository contains implementation candidates for authentication and
authorization middleware, tenant and company scoping, action-policy checks,
audit-event handling, secret configuration, dependency scanning, and security
tests. Those controls must be reviewed in the code and verified in the target
deployment before they are relied on.

Code presence does not establish that a control is enabled, correctly
configured, effective in production, or covered by an external audit. Runtime
claims require environment-specific evidence such as deployed configuration,
provider records, executed test artifacts, logs, and an identified reviewer.

In particular:

- token validation depends on configured issuers, keys, scopes, and deployment
  settings;
- tenant and company isolation depends on authenticated context and database
  policy configuration;
- action governance depends on the selected policy mode, grants, connectors,
  and durable audit configuration;
- retention, backup, restore behavior, data location, and incident response
  depend on the deployed environment and applicable agreement; and
- third-party connectors and model providers retain their own security and
  availability responsibilities.

This repository does not claim SOC 2 or ISO 27001 certification, regulatory
compliance, a penetration-test result, or a production security guarantee.

## Maintainer verification

Relevant local checks include the focused security and authorization tests,
static analysis, dependency review, and the public-claim scan. Maintainers
should also validate the deployed identity provider, database policies,
secrets, network boundaries, logging, backups, and incident contacts for the
specific environment. Passing repository checks does not replace deployment
verification or independent review.

## Disclosure

Please allow coordinated investigation before publishing technical details.
We will coordinate with the reporter on a disclosure plan appropriate to the impact
and the affected deployment.
