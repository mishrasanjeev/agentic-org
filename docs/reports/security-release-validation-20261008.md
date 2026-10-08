# Security and Combined Release Validation

Date: 2026-10-08. Status: local validation in progress; not production sign-off.

## Scope

The release candidate integrates the 42 open pull-request heads captured for
this release, plus the security remediation. Validation applies to their
combined result, not merely to each isolated branch. Unrelated workspaces and
Docker projects are outside scope.

## Security Remediation

| Finding | Change | Regression evidence |
| --- | --- | --- |
| Code scanning 125-126: reporting query construction | SQLAlchemy column expressions selected from a fixed dimension allow-list; values remain bound | Actual PostgreSQL queries across all six dimensions and two tenants; hostile dimensions refused |
| Code scanning 127-137: conversation parser complexity | Bounded token matching and possessive quantifiers; adjacent reference and malformed decimal paths covered | Hostile 100,000-character inputs run in timeout-limited subprocesses; valid conversations retained |
| Dependabot 119: source-map-js | UI lockfile resolves 1.2.2 | UI npm audit: zero vulnerabilities |
| Dependabot 118: proxy-addr | MCP lockfile resolves 2.0.8 | MCP npm audit: zero vulnerabilities |
| Additional MCP SDK advisory | SDK resolves 1.32.1 | MCP build and five-call smoke passed; npm audit clean |

GitHub alert closure requires a scan of the final main commit. Alerts must not
be dismissed as a substitute for that scan.

## Combined-Branch Corrections

- Registry/catalogue enforcement fixes from the earlier release are preserved
  while incorporating the newer registry and risk-tier follow-ups.
- A single no-op Alembic merge revision joins the registry compatibility head
  and the latest feature migration. Existing migration identifiers stay intact.
- The personalisation registration test uses OpenAPI rather than assuming
  every lazily included router exposes a `path` attribute.
- Profile encryption and decryption run off the request event loop. A
  thread-identity regression failed before the fix and passes afterward across
  profile write/read and content rendering.
- Duplicate-rule insertion is atomic. The precheck-race regression failed
  before the fix; actual concurrent PostgreSQL requests now produce one
  success and one `409 rule_exists`. Another tenant can use the same name.
- Lineage ignores late responses for superseded selections, labels truncated
  source traversal accurately, and provides native keyboard node controls.
  The three new UI regressions failed before the fixes and pass afterward.

## Measured Validation

| Check | Observed result |
| --- | --- |
| First security preflight | Passed: 12,183 backend tests; 18 skipped; 5 expected failures; 81.78% coverage |
| First security UI run | Passed: 443 tests in 69 files; lint, types and build passed |
| Final security parser expansion | Focused conversation suites: 120 passed; full preflight pending |
| Combined static checks | `make check RUNNER=local` passed inside the Docker test container |
| Focused personalisation suite | 32 passed |
| PostgreSQL reporting and duplicate-rule regressions | 2 passed |
| Focused lineage UI suite | 8 passed |
| Delayed dashboard-response sign-in browser regression | Passed against the existing local stack; not a final-image release result |
| Final rebuilt Docker runtime, complete suites and browser replay | Pending |
| Remote final candidate CI and main CI | Pending |
| Production rollout and verification | Not performed |

## UI Review

Audit: graph-only mouse actions and missing traversal-state distinctions were
confirmed. Critique: provenance must not present an incomplete traversal as
proof of no source. Polish: native focusable controls, explicit accessible
names, visible focus outlines, 44-pixel targets, bounded scrolling and wrapping
references preserve the existing console hierarchy. The optional design-command
pack is not installed; this is a manual review, not a claim that those commands
ran. Final browser screenshots and overflow checks remain pending.

## Release Limits

Optional features remain default off. No real payment, paid phone call or
external email is part of local verification. Real-provider acceptance is not
inferred from synthetic fixtures. Production rollout requires the combined
local Docker tests, green candidate CI, and green final-main checks first.
