# Publishing the SDKs

Version is bumped in-repo by the PR that ships the API change; the
actual PyPI / npm publish is a separate manual step (needs
credentials, should not live in CI without a release-gating flow).

## Python SDK (PyPI)

**Prereqs:** `pip install build twine`. PyPI token in `~/.pypirc`.

```bash
cd sdk
rm -rf dist
python -m build
twine check dist/*
twine upload dist/*
```

**Verify:**

```bash
pip install --upgrade agenticorg
python -c "import agenticorg; print(agenticorg.__version__)"
# expect the version from sdk/agenticorg/__init__.py
```

The `__version__` in `sdk/agenticorg/__init__.py` must match
`version` in `sdk/pyproject.toml`. CHANGELOG.md under `sdk/` is the
release-notes source; keep the top entry dated.

## TypeScript SDK (npm)

**Prereqs:** `npm login` (or set `NODE_AUTH_TOKEN`).

```bash
cd sdk-ts
npm run build
npm publish --access public
```

**Verify:**

```bash
npm view agenticorg-sdk version
# expect the version from sdk-ts/package.json
```

## MCP server (npm)

```bash
cd mcp-server
npm run build
npm publish --access public
```

MCP registry (`mcp-server/server.json`) points at the npm package —
its `packages[].version` field must stay in lockstep with
`package.json` `version`. Bump both in the same PR.

## Version policy

- Major bump when the wire contract breaks (e.g. field removed).
- Minor bump when a new field is added (backward-compatible) — this
  is what PR-A shipped (canonical `AgentRunResult`).
- Patch bump for bugfix-only changes.

SDK versions track the server's wire contract, not the app version.
A main app at `4.8.0` is fine with SDKs at `0.2.0` — what matters is
that the SDK can parse what the server emits.

## Drift guard

`scripts/consistency_sweep.py` does NOT currently cross-check SDK
versions with published registry versions. Add it to the sweep's
future-checks list only when there's tooling that can query PyPI /
npm cheaply enough not to slow the preflight gate.
