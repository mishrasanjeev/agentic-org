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

## MCP server (npm + MCP registry)

**Two separate publishes.** An npm push does NOT propagate to the
MCP registry. They are independent stores, one for the artifact, one
for the discovery metadata. Both steps are required.

### Step 1 — publish the npm tarball

```bash
cd mcp-server
npm run build
npm publish --access public
```

### Step 2 — publish to registry.modelcontextprotocol.io

Install `mcp-publisher` CLI once per machine. PowerShell:

```powershell
$arch = if ([System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture -eq "Arm64") { "arm64" } else { "amd64" }
Invoke-WebRequest -Uri "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_windows_$arch.tar.gz" -OutFile mcp-publisher.tar.gz
tar xf mcp-publisher.tar.gz mcp-publisher.exe
```

Then from `mcp-server/`:

```bash
mcp-publisher validate       # catch schema failures (desc <=100 chars, etc.)
mcp-publisher login github   # device-flow OAuth, needs the GitHub account
                             # that owns the `io.github.<user>/*` namespace
mcp-publisher publish        # pushes server.json to the registry
```

The registry validates `description` length (<=100 chars), `version`,
`packages[].version`, and the `$schema` URL. `scripts/consistency_sweep.py`
catches the description-length constraint locally so a preflight fails
instead of a `mcp-publisher validate` later.

### Lockstep rule

`mcp-server/package.json.version`, `server.json.version`, and
`server.json.packages[0].version` must always move together. The
sweep fails if any drifts.

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
