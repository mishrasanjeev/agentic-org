# agenticorg-mcp-server changelog

## 4.0.3 — 2026-04-19

Republish to pick up the runtime fixes that landed on main after 4.0.2
was pushed to npm + MCP registry.

### Fixed
- `server.version` advertised by the MCP server was hardcoded `"0.1.0"`
  in the 4.0.2 npm bundle while the published package was 4.0.2.
  `src/index.ts` now reads `version` from `package.json` at runtime,
  so the advertised version can never drift from the package identifier.
- Server `description` in the 4.0.2 npm bundle still carried stale marketing
  counts ("50+ agents, 1000+ integrations, 54 native connectors,
  340+ tools"). Replaced with a neutral description that points callers
  at `GET /api/v1/product-facts` for live counts.
- Header comment no longer claims callers can "Call any of the 340+
  connector tools directly" — connector tools flow through agents per
  `docs/mcp-product-model.md`.

### Notes
- No wire-protocol or tool-surface changes. Pure republish of the
  server identity string + header comments that landed in PR #238.
- `package.json`, `package-lock.json`, and `server.json` (top-level
  `version` + `packages[0].version`) bumped together per the lockstep
  policy documented in 4.0.2.

## 4.0.2 — 2026-04-19

Version alignment + truth-pass.

### Changed
- `package.json` 4.0.0 → 4.0.2 to match the MCP registry manifest
  `server.json`. Pre-G3, the two were out of sync — the MCP registry
  advertised 4.0.1 while npm served 4.0.0.
- `server.json.packages[0].version` pinned to the same 4.0.2 — the
  registry must reference the exact npm version it's shipping.

### Fixed
- Stale public claims scrubbed from tool descriptions (50+ AI agents,
  54 native connectors, 340+ connector tools). Shipped via PR-G1
  (#218) into main; this version bump reflects the user-visible
  change.

### Version policy

- `package.json.version` and `server.json.version` always move in
  lockstep. Bumping one without the other silently creates npm/MCP
  drift.
- `server.json.packages[0].version` must equal `package.json.version` —
  the registry tells the MCP client which npm release to pull.
- MCP server versions are independent of the main AgenticOrg app —
  this 4.x line tracks wire-protocol compatibility with the server,
  not the app's own `pyproject.toml` version.

## 4.0.1 — prior

MCP registry manifest update (server.json only; package.json unchanged
at 4.0.0). Left the repo in a mismatched state closed by 4.0.2.

## 4.0.0 — initial release

First npm + MCP registry publish.
