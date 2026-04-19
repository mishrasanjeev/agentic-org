# agenticorg-mcp-server changelog

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
