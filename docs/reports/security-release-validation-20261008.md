# Security and Combined Release Validation

Date: 2026-10-08. Local validation record, not production sign-off.
Current candidate, CI and rollout status is tracked in
[the combined release PR](https://github.com/mishrasanjeev/agentic-org/pull/1548).

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
- The isolated registry PostgreSQL fixture includes the rating and execution
  history tables read by the current card response. Concurrency and tenant
  isolation assertions remain intact, with empty-history response assertions.
- Migration round-trip verification names the retained parent at a merge head
  instead of using an ambiguous relative downgrade. Existing migration
  identifiers and production rollout policy are unchanged.
- The reviewed personalisation configuration-rule deletion raises the bounded
  route guard by one. A PostgreSQL regression proves existing render evidence
  survives with its content hash and a cleared rule reference.
- The bounded amount parser preserves conventional rupee `/-` suffixes across
  single amounts, multiple choices and entity extraction. Seven focused
  assertions reproduced the suffix and currency-marked malformed-decimal bugs
  before correction; all focused conversation/security suites pass afterward.
  The hostile-input replay includes a long malformed suffix.

## Measured Validation

| Check | Observed result |
| --- | --- |
| First security preflight | Passed: 12,183 backend tests; 18 skipped; 5 expected failures; 81.78% coverage |
| First security UI run | Passed: 443 tests in 69 files; lint, types and build passed |
| Final security parser expansion | Focused conversation/security suites after suffix correction: 132 passed. Before that correction, refreshed security preflight passed: 12,215 backend tests, 18 skipped, 5 expected failures, 81.85% coverage |
| Combined static checks | `make check RUNNER=local` passed inside the Docker test container |
| Focused personalisation suite | 32 passed |
| PostgreSQL reporting and duplicate-rule regressions | 2 passed |
| Focused lineage UI suite | 8 passed |
| Registry concurrency and merge-head round trip | 7 passed on a dedicated local PostgreSQL database |
| Final combined unit, security, connector and contract run | 10,138 passed; 7 skipped |
| Final combined integration and regression replay | 2,681 passed; 13 skipped; 5 expected failures |
| Changed-line and new-module coverage | 96% over 7,622 changed lines; every new module meets the 75% floor |
| Full combined preflight | Earlier combined run passed: 12,570 backend tests, 18 skipped, 5 expected failures; 502 UI tests in 80 files. The final-head refreshed gate is mandatory; its result is recorded on the release PR |
| Final rebuilt Docker runtime browser suite | 17 passed against the final API/UI images |
| Final signed-decision browser suite | 3 passed, including delayed dashboard-response hydration and changed-case refusal; grant-leak watcher unchanged and enabled |
| Final catalogue opt-in browser replay | Passed: banking-pack installation, search, keyboard navigation and mobile overflow checks |
| Final registry HTTP checks | 12 passed, including maker/checker refusal, lifecycle history, paused-agent refusal and invalid traffic splits |
| Final lineage and personalisation runtime checks | 14 passed, including concurrent duplicate 201/409, retained audit evidence on rule deletion, consent withdrawal refusal, keyboard controls and mobile layout |
| Final worker entrypoint | Health check and isolated-queue task/result round trip passed; canary stopped afterward |
| Local migration-first rollout | Passed to the single merge head, with PostgreSQL concurrency and merge-head round-trip tests passing |
| Remote final candidate CI and main CI | See the release PR for exact-head check results; no main merge or deployment permitted before green required checks |
| Production rollout and verification | See the release PR for rollout state; local evidence alone is not deployed evidence |

## UI Review

Audit: graph-only mouse actions and missing traversal-state distinctions were
confirmed. Critique: provenance must not present an incomplete traversal as
proof of no source. Polish: native focusable controls, explicit accessible
names, visible focus outlines, 44-pixel targets, bounded scrolling and wrapping
references preserve the existing console hierarchy. The optional design-command
pack is not installed; this is a manual review, not a claim that those commands
ran. Final desktop/mobile screenshots were inspected; keyboard controls,
wrapping and document-overflow checks passed.

## Timing Replay

One refreshed preflight, while concurrent Docker builds, suites and a worker
canary were running, measured 500 mocked workflow starts at 2.075 seconds
against the existing 2-second limit. The unchanged performance suite passed in
isolation, and the exact test passed three consecutive coverage-enabled
replays after the competing jobs completed. No timeout, assertion, security
gate or production code was relaxed. The complete refreshed preflight must
still pass before the candidate is pushed.

## Release Limits

Optional features remain default off. No real payment, paid phone call or
external email is part of local verification. Real-provider acceptance is not
inferred from synthetic fixtures. Production rollout requires the combined
local Docker tests, green candidate CI, and green final-main checks first.

The MCP server is a separately installed npm package. Repository lockfile and
build fixes do not update already installed clients. No npm publication has
been performed by this release; existing clients need a separately verified
package release before claiming that they received the dependency fixes.
