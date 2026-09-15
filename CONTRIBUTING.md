# Contributing to AgenticOrg

We welcome contributions from the community. This guide covers everything you need to get started.

## Development Setup

### Prerequisites

- Python 3.12+
- Node.js 20+ (for UI)
- Docker & Docker Compose
- Git

### Getting Started

```bash
# Clone the repo
git clone https://github.com/your-org/agenticorg.git
cd agenticorg

# Set up Python environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"

# Start infrastructure
docker compose up -d postgres redis minio

# Copy and configure environment
cp .env.example .env
# Edit .env with your keys

# Run tests to verify setup
pytest tests/unit/
```

### Secret scanning

Every pull request, every push to `main` and a weekly full-history run are
scanned with [gitleaks](https://github.com/gitleaks/gitleaks) 8.30.1
(`.github/workflows/secret-scan.yml`). `scripts/preflight.sh` runs the same
scan over your branch. Install gitleaks 8.30.1 locally, then install the hooks
once per clone:

```bash
pip install pre-commit
pre-commit install
```

Run the checks by hand:

```bash
bash scripts/scan-secrets.sh range "$(git merge-base origin/main HEAD)" HEAD
bash scripts/scan-secrets.sh history
bash scripts/test-scan-secrets.sh     # scanner self-test
```

The scanner fails closed: a missing or different gitleaks version, an unknown
mode or an unresolvable commit is an error, not a pass.

If a scan reports a finding:

1. **A live credential** — revoke or rotate it first, then remove it from the
   branch. Rewriting history does not un-leak a pushed secret; rotation does.
   Report it privately as described in [SECURITY.md](SECURITY.md).
2. **A placeholder** (a test fixture or documentation example) — prefer
   changing it so it no longer looks like a credential. If it must stay,
   append `gitleaks:allow` as a comment on that line, or add its fingerprint
   (printed with the finding) to `.gitleaksignore` and say why in the pull
   request.

### UI Development

```bash
cd ui
npm install
npm run dev    # http://localhost:5173
```

## Contribution Workflow

1. **Fork** the repository
2. **Create a feature branch**: `git checkout -b feature/my-feature`
3. **Make changes** following the code standards below
4. **Write tests** for all new functionality
5. **Run the full test suite**: `pytest tests/`
6. **Submit a Pull Request** against `main`

## Code Standards

### Licence headers

AgenticOrg is Apache-2.0. Every **new** source file (`.py`, `.ts`, `.tsx`,
`.js`, `.jsx`, `.mjs`, `.cjs`, `.sh`) must carry an SPDX identifier within its
first five lines — after a shebang or encoding line if there is one:

```python
# SPDX-License-Identifier: Apache-2.0
```

```ts
// SPDX-License-Identifier: Apache-2.0
```

Existing files are not required to gain a header when edited. Type declaration
files (`.d.ts`), minified bundles, empty files and recorded test cassettes are
exempt. Pull requests enforce this in CI, and `scripts/preflight.sh` runs the
same check over your branch:

```bash
python scripts/check_license_headers.py --base origin/main
```

### Vendor-neutral names

Code, tests, fixtures, documentation, commit messages, branch names and pull
requests never name a commercial verification, KYB/KYC, identity-data or
sanctions-screening vendor. Interfaces are built from the domain; the mock
provider is `mock` and documentation and examples use `acme_kyb`.

`scripts/check_denylist.py` enforces this. It splits the added lines of a
change, its file paths, commit messages and branch name (and, in CI, the pull
request title and description) into words, and compares salted SHA-256 hashes
of every run of up to a few consecutive words against `config/denylist.sha256`.
Spacing, punctuation, case and accents do not matter: `Acme Verify`,
`acme_verify` and `AcmeVerify` are the same term. Only the salt and the hashes
are committed and the plain list is kept outside the repository by the
maintainers, which keeps the names out of the tree and its diffs. The hashes
are not a secret: with the committed salt anyone can test a guessed name. Failures give the location, not the matched words. The check fails
closed when git or the hash file misbehaves.

```bash
python scripts/check_denylist.py scan --base origin/main   # this branch (also part of make check)
python scripts/check_denylist.py audit                     # every tracked file
```

The **Vendor Denylist** workflow runs `scan` on every pull request (including
title and description edits) and on pushes to `main`. If it flags a word that
is not a vendor name, tell a maintainer rather than working around it. To change
the list, a maintainer edits the private terms file and regenerates the hashes
(`python scripts/check_denylist.py build --keep-salt --terms-file <path outside
the repository>`, which refuses a terms file inside the working tree and
prints only a count), then commits `config/denylist.sha256`.

### Python (Backend)

- **Linter**: `ruff check .` (zero violations required)
- **Type checking**: `mypy --ignore-missing-imports .`
- **Formatting**: ruff format (line length 100)
- **Async**: All I/O operations must be async
- **Error codes**: Use the E-series taxonomy from `core/schemas/errors.py` — no ad-hoc error strings

### TypeScript (Frontend)

- **Linter**: `eslint .`
- **Type checking**: `tsc --noEmit`
- **Components**: Functional components with TypeScript interfaces
- **Styling**: Tailwind CSS + Shadcn/ui components

### Tests

- Coverage gates in CI: the unit and contract suites must keep total coverage at
  or above **55%**, with per-module floors from
  `scripts/check_module_coverage.py`; on pull requests, **75% of the changed
  lines** (diff-cover) and **75% of every new Python module**
  (`scripts/check_new_module_coverage.py`, which counts a new module no test
  imports as 0%) must be covered. Tests, `conftest.py` files and Alembic
  revisions are not gated as modules. Run the same gate locally after
  `make test` with `make coverage-gate`.
- All PRD test IDs (FT-FIN-xxx, SEC-AUTH-xxx, etc.) must pass
- Use `pytest-asyncio` for async tests
- Mock external services, not internal modules

### Container scanning

`.github/workflows/container-scan.yml` builds the API (`Dockerfile`) and console
(`Dockerfile.ui`) images on every pull request, push to `main` and nightly,
scans each with [Trivy](https://trivy.dev/) 0.74.0 and uploads a CycloneDX SBOM
per image (`agenticorg-api-sbom`, `agenticorg-ui-sbom`). The scan fails on HIGH
or CRITICAL vulnerabilities that have a fixed version. Run it locally:

```bash
docker build -t agenticorg-api:scan .
bash scripts/scan-container.sh image agenticorg-api:scan agenticorg-api.cdx.json
bash scripts/test-scan-container.sh   # scanner self-test
```

**Exceptions.** Fix a finding when you can: refresh the pinned base image
digest (`scripts/refresh_image_digests.sh`), update the dependency, or keep the
package out of the runtime image. When a fix has to wait, add an entry to
`.trivyignore.yaml` with the vulnerability `id`, the exact package `purls`, a
`statement` naming the `FINDINGS.md` entry that tracks the fix, and an
`expired_at` date no more than 30 days out. An expired entry stops applying and
the scan fails again.

### Dependency audit exceptions

`scripts/run_pip_audit.py` runs `pip-audit` over the project metadata,
`requirements.txt` and `requirements-v4.txt` on every pull request
(`security-scan` job), nightly and in `make check`. It fails on any known
vulnerability, and on anything that stops the audit from completing.

Fix a finding by upgrading the dependency (or its parent) whenever a fixed
version exists. When a fix has to wait:

1. Assess the advisory: is the vulnerable code reachable here, and what
   mitigates it?
2. Add an entry to `config/pip-audit-exceptions.toml` with the advisory `id`
   (or any alias, such as the CVE), the `package`, the `reason` from your
   assessment, an `owner`, and an `expires` date no more than 90 days out.
3. Get the entry reviewed in the pull request like any other security change.

The audit reports every accepted finding with its owner and expiry, warns about
exceptions that match nothing (remove them), and fails once an entry has
expired until the dependency is fixed or the review is renewed.

## Agent Development

### Creating a New Agent

1. Create the prompt file: `core/agents/prompts/{agent_type}.prompt.txt`
   - Must include: Token scope, `<processing_sequence>`, `<escalation_rules>`, `<anti_hallucination>`, `<output_format>`
2. Create the agent class: `core/agents/{domain}/{agent_type}.py`
   - Set `agent_type`, `domain`, `confidence_floor`, `prompt_file`
3. Register in `core/agents/registry.py`
4. Add authorized tool scopes in agent config
5. Write tests covering all processing steps

### Prompt tool references

Every tool a built-in prompt tells the model to call (`call x(...)`, `x()`, or a
snake_case `x(...)` outside the `Token scope:` block) must be registered by a
connector and be in that agent type's list in `_AGENT_TYPE_DEFAULT_TOOLS`
(`api/v1/agents.py`); every name in the default lists must be registered, and a
`connector:tool` name must resolve to that connector. A prompt maps to the agent
type named by its file stem, or by the stem without `_agent`.

```bash
python scripts/check_prompt_tools.py
```

It runs in CI, in `scripts/preflight.sh` and as
`tests/unit/test_prompt_tool_references.py`, with no baseline. When it fails,
rewrite the prompt step to use a tool the agent has, or to work from the task
input and escalate to human review when the data is missing. Do not add tools
to a default list to make a prompt pass.

### Agent Lifecycle Rules

- All new agents **must start in shadow mode** — no exceptions for production
- Shadow mode requires minimum 100 samples and 95% accuracy before promotion
- Clone agents inherit parent scopes — cannot elevate permissions
- Kill switch must work in <30 seconds

### Org Chart Tree Structure

Agents are organized in a parent-child hierarchy per department. Key patterns:

- Each agent has an `org_level` field (e.g., `"Head"`, `"Manager"`, `"Analyst"`) and an optional `parent_agent_id` foreign key
- The `/agents/org-tree` endpoint returns the full tree, built recursively from the `parent_agent_id` references
- **Smart escalation**: When an agent's confidence falls below its threshold, the task auto-escalates to its parent in the org tree. Implement escalation-aware logic in `core/orchestrator/`
- **CSV bulk import** (`/agents/import-csv`): Parses a CSV with columns like `name, designation, domain, org_level, parent_name` and creates agents in dependency order (parents before children). Validation rejects orphan rows and duplicate names within a department

## Connector Development

### Adding a New Connector

1. Create `connectors/{category}/{connector_name}.py`
2. Extend `BaseConnector`
3. Implement `_register_tools()` and `_authenticate()`
4. Use `self._get_secret(key)` for credentials — **never hardcode tokens**
5. Set `rate_limit_rpm` appropriate to the API's limits
6. Register in `connectors/registry.py`

## Pull Request Guidelines

- Keep PRs focused — one feature or fix per PR
- Include the relevant PRD test IDs in the PR description
- Update CHANGELOG.md for user-facing changes
- Security-sensitive changes require review from a security team member
- All CI checks must pass before merge

## Reporting Issues

- Use GitHub Issues for bugs and feature requests
- For security vulnerabilities, see [SECURITY.md](SECURITY.md)

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
