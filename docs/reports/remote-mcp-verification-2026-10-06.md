# Remote MCP verification and issue disposition

## Scope and verdict

This change implements incoming, tenant-owned MCP tool connections on the
AgenticOrg codebase. It is not a blanket resolution of all provider connection
failures. A protocol test is not a speech, messaging or payment-provider test.

| Item | Verdict | Evidence and residual boundary |
| --- | --- | --- |
| AgenticOrg incoming MCP discovery/save/runtime gap | Implemented and verified in local Docker; deployment must be confirmed separately | Official MCP SDK over real HTTP, authenticated API, PostgreSQL/Redis, browser replay and signed-grant checks |
| Issue #1504 | Reported deployment remains unverified | The reporter used a separate hosted deployment. AgenticOrg.ai is the only authorized release target. Local tests replay the reported speech-tool names and rejection class, not that tenant or real speech provider |
| Issue #1507 | Partially addressed; live provider authorization remains unverified | Native registration identity and Gmail profile-health fix are in the same PR. Incoming MCP has a dedicated path. WhatsApp, Twilio, Gmail and Plural still require customer-owned credentials and approved provider-side scopes |

Do not automatically close either issue or mark the whole connector area fixed.
Record the deployed commit and target, then ask for a sanitized customer replay
on that target. Do not request secrets in an issue. Customers enter their own
credentials through the protected connection form.

## User-visible path

1. Open Connectors > Remote MCP.
2. Register an approved public HTTPS Streamable HTTP endpoint and bearer token.
3. Inspect the discovered tools and input schemas. Explicitly review read-only
   tools; a server annotation alone is not authority.
4. Run a bounded read-only probe with synthetic, non-sensitive arguments.
5. Select the connection and exact tools in Agent > Config > Edit, save and reload.
6. Run the company-scoped agent with a valid grant. Runtime reloads persisted
   tenant/ownership/link authority and compares the live tool schema before calling.

The canonical setup and troubleshooting guide is [Connectors](../user-guide/connectors.md).

## Verification method

- `tests/unit/test_remote_mcp.py`: official SDK client and FastMCP server using
  real TCP/HTTP and bearer authentication. Covers initialization, paginated
  discovery, schema validation, one-attempt calls, bad authentication, missing
  tools, unselected connectors, private endpoints, redirects, oversized responses,
  repeated pagination, unsafe schema programs and credential-echo withholding.
- Signed-grant regressions use generated local RSA keys with real SDK signature
  verification. Only JWKS retrieval and revocation-service responses are local
  fixtures. They check expiry, wrong signatures, audience, wrong tool/connector,
  insufficient permission, revoked grants and mixed scopes. No key is committed.
- `tests/integration/test_remote_mcp.py`: real API/database plus TCP MCP. Checks
  encrypted credential persistence, cross-tenant refusal, POST/PATCH/PUT agent
  validation, graph and Tool Gateway dispatch, signed-grant refusal before dispatch,
  write containment, unlink and archive behavior.
- `ui/e2e/remote-mcp.spec.ts`: Chromium against local Docker Nginx/API and a real
  SDK server. Creates a synthetic local tenant, rejects a bad bearer, registers
  the server, reviews `gnani_transcribe`, refuses read classification of
  `gnani_voice_reply`, probes, selects tools, saves, reloads and verifies persisted
  configuration. Desktop/mobile screenshots and overflow checks are included.
- The local browser fixture is `tests/fixtures/remote_mcp_browser.py`. It refuses
  to start outside the local development Docker database. Only its outbound MCP
  transport is redirected to the loopback SDK server; no production egress bypass
  is present in the application. Synthetic tool results are explicitly not STT/TTS.
- UI unit regression preserves native tool selections while unlinking a remote
  connector. No cross-connector combinations are offered by the new picker.

Run the browser test only against that isolated fixture:

```powershell
$env:BASE_URL = 'http://127.0.0.1:3003'
$env:MCP_LOCAL_FIXTURE = '1'
npx playwright test e2e/remote-mcp.spec.ts --project=chromium --retries=0 --workers=1
```

## Compatibility lesson and regression protection

Testing only discovery or only the agent save API missed the grant boundary.
The requirements-pinned Grantex 0.7.1 SDK also needed explicit narrowing of its
verified scopes to the requested tool. Remote enforcement now uses an isolated
manifest map, keeps signature/revocation/cap settings, supplies the configured
audience, and checks the exact tool and permission in verified scopes. Native
connector enforcement is unchanged. Tests run against both that pinned version
and the newer dependency selected by the local development image. The production
Dockerfile installs the pyproject dependency range, so its resolved package
versions must also be recorded rather than assumed from requirements.txt.

Releases must test discovery -> picker -> save -> reload -> grant -> dispatch
together, and distinguish the public product from separately hosted deployments.

Native connector UUID resolution also has regression coverage: resolving an
existing native binding must not add an unrelated remote catalog database lookup.
The existing UUID-fallback regression was replayed after correcting that path.

## Local results

| Gate | Observed result |
| --- | --- |
| Focused MCP unit/integration | 24 passed in local Docker with real HTTP, API, PostgreSQL and Redis |
| Production-image dependency environment | 24 passed using Python 3.14, MCP 1.30.0, Grantex 0.7.2, FastAPI 0.142.2 and HTTPX 0.28.1; candidate source and local fixture mounted read-only; only test-runner packages added to a temporary environment |
| Requirements-pinned grant compatibility | 23 focused tests passed on Grantex 0.7.1 before the additional compression regression; signed-grant enforcement is unchanged by that transport-only patch |
| Chromium Docker replay | Passed with both reported tool names selected, saved and reloaded; reviewed read probe passed; write dispatch remains blocked |
| UI unit suite | 399 passed |
| TypeScript, ESLint, UI build and SEO build checks | Passed; existing lint/build warnings remain |
| Production Dockerfile build | Passed dependency installation and package consistency checks; no production deployment performed |
| Full backend preflight | Passed in a frozen Linux Docker snapshot: 11,382 passed, 18 skipped, 5 xfailed; 80.43% coverage; lint, types, security and enterprise gates passed |

Backend preflight and UI gates were run separately because the Linux backend
snapshot does not include Node. The UI suite, typecheck, lint, build, generated
documentation freshness and browser replay were validated separately on the
workstation and local Docker services. This is not a claim that the backend
preflight script ran the UI suite.

The initial broad run had 11,375 passes and six failures. One exposed the native
UUID compatibility issue above. The other five were invalidated by overlapping
test runs deleting a shared temporary directory and source changes while
inspection tests were running. All six passed on isolated replay. This is not
counted as a green full run: the subsequent frozen-snapshot suite passed with the
results above. Never mutate source or share pytest basetemp between
concurrent validation runs.

The first remote CI attempt rejected the generated guide's prose tool-limit
sentence as an unregistered inventory claim. The guide now uses an explicit
input-limit table, without relaxing the claims gate. Regeneration, documentation
tests and claims lint passed locally. Run claims lint after public artifacts are
regenerated, not only against the earlier backend snapshot.

Transport checks reject compressed responses that ignore `Accept-Encoding:
identity`, as well as oversized identity responses, before exposing the client to
unbounded decompression. This limitation is documented for connector operators.

## Explicitly not verified or enabled

- Real Gnani STT/TTS, Telegram delivery, WhatsApp, Twilio, Gmail sending or Plural
  actions: no approved provider credentials or destination were available.
- Arbitrary remote writes: selectable but contained by existing action policy.
- OAuth authorization, local stdio and legacy HTTP+SSE endpoints: unsupported by
  this incoming connector path; the separate AgenticOrg MCP server is unchanged.
- Universal server compatibility, unlimited schemas or sustained provider-load
  performance: not claimed. Supported limits and refusal reasons are documented.
- Production readiness or successful deployment: local tests alone do not prove
  either. CI, deployment identity and safe post-deploy checks are separate evidence.
