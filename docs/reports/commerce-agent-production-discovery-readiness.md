# Commerce Agent Production Discovery Readiness

- Assessment date: 2026-05-17T14:58:09+05:30
- Scope: read-only unauthenticated production discovery posture
- AgenticOrg main commit: `468f3c8b4388880d2e1c6c7a12df40e1222bc257`
- Required C3 evidence merge included: `9904489aebb342a6b34553254a24d51391c37444`
- Production changes made: none
- Production Commerce V1 enabled by this task: false
- Live payments enabled by this task: false
- Live Plural enabled by this task: false
- Authenticated production tokens used: false
- Raw payloads recorded: false
- Secret values recorded: false
- Mock mode default confirmed: `demos/commerce_sales_agent_demo.py` keeps `--mode` defaulting to `mock`

## Endpoint Results

| Host | Endpoint | HTTP | Latency ms | Status | Redacted body hash | Discovery assertion |
| --- | --- | ---: | ---: | --- | --- | --- |
| `app.agenticorg.ai` | `/api/v1/health/liveness` | 200 | 457 | public | `bc24b0b5a305` | Liveness endpoint is publicly reachable. |
| `app.agenticorg.ai` | `/api/v1/health` | 200 | 235 | public | `d7dcd79dee4e` | Health endpoint is publicly reachable. |
| `app.agenticorg.ai` | `/api/v1/mcp/tools` | 200 | 280 | public discovery | `c04936f87e4b` | MCP tools include `agenticorg_commerce_sales_agent`. |
| `app.agenticorg.ai` | `/api/v1/a2a/.well-known/agent.json` | 200 | 263 | public discovery | `6c93efef0fa5` | A2A agent card contains commerce metadata and Grantex issuer/JWKS references. |
| `app.agenticorg.ai` | `/api/v1/a2a/agents` | 200 | 229 | public discovery | `a3a7a69c021d` | A2A agents include `commerce_sales_agent` with `grantex_commerce:*` tools. |

## Readiness Assessment

- Current AgenticOrg production discovery status: publicly available.
- Production MCP discovery currently exposes the commerce sales agent.
- Production A2A discovery currently exposes commerce agent metadata and Grantex commerce tool references.
- Grantex production Commerce V1 discovery is not currently enabled, so AgenticOrg commerce metadata should not be treated as production commerce readiness.
- Grantex issuer/JWKS references are production-safe public verification metadata, not secret material.
- No direct Stripe, Plural, Pine, provider credential, bearer token, passport/JWT, idempotency key, webhook secret, DB/Redis URL, private key, or raw payload material was observed or recorded in this evidence.
- No live payment or live Plural behavior was exercised or enabled.
- No production runtime defaults were changed by this task.

## Recommendation

Keep AgenticOrg commerce-specific production discovery gated or disabled for production-readiness purposes until Grantex production Commerce V1 read-only discovery is explicitly approved. AgenticOrg can be eligible for a later read-only production discovery proposal only after:

- Grantex production discovery payload is approved and enabled read-only;
- AgenticOrg commerce metadata is reviewed against the approved Grantex issuer/JWKS and tool contract;
- direct provider credential paths remain absent from commerce discovery;
- live payment and live Plural language remains absent or explicitly blocked;
- legal/product signoff confirms no overclaim of certification, production readiness, external pilot readiness, AP2, UCP, ACP, or equivalent compliance;
- rollback plan for hiding/gating commerce discovery is documented.
