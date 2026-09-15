# Findings

Defects found while doing other work and deliberately left out of that change.
Each entry says where it was found, what is wrong and what fixing it involves.
Remove an entry in the pull request that fixes it.

## A-1 — Console image base has fixable HIGH advisories in libuuid

- **Found:** container scan of `Dockerfile.ui` (2026-09-14).
- **What:** the pinned `nginx:alpine@sha256:72ba65eb…` base ships util-linux
  `libuuid` 2.42.1-r0, affected by CVE-2026-53612, CVE-2026-53613,
  CVE-2026-53614, CVE-2026-76642, CVE-2026-78408, CVE-2026-78409 and
  CVE-2026-78410 (fixed in 2.42.3-r0 / 2.42.3-r1). The nightly scan only
  covered the API image, so this was not reported before.
- **Fix:** move the digest in `Dockerfile.ui` to an `nginx:alpine` build with
  libuuid 2.42.3-r1 or later (`scripts/refresh_image_digests.sh`), rebuild,
  rescan and drop the seven entries from `.trivyignore.yaml`.

## A-2 — CONTRIBUTING.md overstates the coverage gate

- **Found:** editing `CONTRIBUTING.md` (2026-09-14).
- **What:** "Tests" says a minimum 80% coverage is enforced in CI; CI and
  `scripts/preflight.sh` enforce `--cov-fail-under=55` plus per-module floors
  (`scripts/check_module_coverage.py`).
- **Fix:** state the real gate. The governed-actions work raises it to 75% on
  changed code, so update the text in that change.

## A-3 — Preflight mypy and CI lint type-check different environments

- **Found:** running `scripts/preflight.sh` on `main` at 784cbd03 (2026-09-14).
- **What:** the CI `lint` job installs only `ruff mypy pydantic` before
  `mypy --ignore-missing-imports .`, so third-party packages such as structlog
  are untyped (`Any`) there. Preflight runs the same command inside a full
  `.[dev]` environment, where structlog's types apply, and fails on
  `core/logging_config.py:92` (`list-item`) while CI passes. The script says
  it mirrors CI exactly; it does not.
- **Fix:** type-check against the installed dependencies in both places
  (install `.[dev]` in the lint job) and fix the processor list's annotation,
  or make preflight mirror the lint job's minimal environment. The first
  catches real type errors; the second only restores agreement.

## A-7 — Six built-in agent prompts are never loaded

- **Found:** aligning prompts with default tools (PRD F-3, 2026-09-15).
- **What:** the run, A2A and MCP paths load a built-in prompt with
  `importlib.import_module(f"core.langgraph.agents.{agent_type}")`
  (`api/v1/agents.py` run path, `api/v1/a2a.py`, `api/v1/mcp.py:_load_agent_prompt`).
  The modules for agent types `abm`, `treasury`, `rev_rec` and `fixed_assets`
  are `abm_agent.py`, `treasury_agent.py`, `rev_rec_agent.py` and
  `fixed_assets_agent.py`, so the import fails and those agents run on the
  one-line generic placeholder instead of `abm_agent.prompt.txt`,
  `treasury_agent.prompt.txt`, `rev_rec_agent.prompt.txt` and
  `fixed_assets_agent.prompt.txt`. `sales_agent.prompt.txt` and
  `nexus_orchestrator.prompt.txt` have no loader at all (only the claims
  linter reads the first).
- **Fix:** resolve the module by an explicit agent-type → module map (or the
  `_agent` suffix) in one shared helper used by all three paths, fail loudly
  when a built-in type has no prompt, and wire or delete the two orphan
  prompts. `scripts/check_prompt_tools.py` already maps `<type>_agent` stems
  to `<type>`.

## A-8 — The three default-tool sources disagree and still name dead tools

- **Found:** removing unregistered tools from `_AGENT_TYPE_DEFAULT_TOOLS`
  (PRD F-3, 2026-09-15).
- **What:** default tools live in `api/v1/agents.py`
  (`_AGENT_TYPE_DEFAULT_TOOLS`), `core/agent_generator.py` (its own copy) and
  each `core/langgraph/agents/*.py` module (`DEFAULT_TOOLS`,
  `AP_PROCESSOR_TOOLS`, ...), which `build_*_graph` binds when no tools are
  passed. For 27 of 37 agent types the three lists differ. The F-3 change
  removed unregistered names from the first two (and from `fpa_agent.py`,
  which a test pins equal), but the modules still name tools no connector
  registers, for example `check_order_status` (ap_processor,
  expense_manager), `get_post_analytics` (social_media, brand_monitor,
  content_factory, seo_strategist), `read_email`/`draft_email` (email_agent),
  `send_notification` (notification_agent) and `read_messages` (chat_agent).
- **Fix:** make `api/v1/agents.py` the only source (the generator and the
  modules import it), then delete the copies; update
  `tests/unit/test_new_agents.py` and `tests/unit/test_langgraph_runtime.py`,
  which pin the module lists.

## A-9 — Prompt token-scope lines name connectors and permissions agents lack

- **Found:** PRD F-3 cites `risk_sentinel.prompt.txt:4` (2026-09-15).
- **What:** the `Token scope:` line of most built-in prompts lists
  connector/permission pairs that are neither registered tools nor in the
  agent's defaults, e.g. `ocr(r:extract)` and `banking_api(w:queue_payment)`
  in `ap_processor`, `jira(w:create_issue)` and `sanctions_api(r:batch_screen)`
  in `risk_sentinel`, `outlook(...)` in `email_agent`. The model reads them as
  capabilities. `scripts/check_prompt_tools.py` checks tool calls only and
  skips these lines because they are not tool names.
- **Fix:** rewrite each token-scope line from the agent's default tools
  (`connector(tool, ...)`) and extend the check to parse and verify it.

## A-10 — Default-tool derivation widens to every tool of a linked connector

- **Found:** removing unregistered defaults (PRD F-3, 2026-09-15).
- **What:** `_derive_default_tools` (`api/v1/agents.py`) returns every tool of
  the linked connectors when none of the agent type's defaults intersect
  them. `seo_strategist` now has no defaults, so linking any connector grants
  all of its tools, including writes; any agent linked to a connector outside
  its defaults gets the same.
- **Fix:** return an empty list (or only read tools) when nothing intersects
  and let the user choose tools explicitly; update
  `tests/unit/test_agent_default_tools_endpoint.py` accordingly.

## A-11 — Console permission badge misreads connector-qualified tools

- **Found:** qualifying ambiguous default tools (PRD F-3, 2026-09-15).
- **What:** `getToolPermission` in `ui/src/pages/AgentCreate.tsx` and
  `ui/src/pages/AgentDetail.tsx` matches the name prefix (`create_`,
  `delete_`, ...), so `jira:create_issue`, `zoho_books:create_bill` or
  `grantex_commerce:cart_create` show as READ and the "minimal scope set"
  preview understates write access. The tool picker can also offer the bare
  `create_issue` beside an already-selected `jira:create_issue`.
- **Fix:** strip the connector prefix before classifying (or use the backend's
  `classify_action`), and treat `connector:tool` and `tool` as the same entry
  in the picker when the connector is linked.

## A-12 — Industry pack prompts call tools nothing registers

- **Found:** running the prompt tool parser over `core/agents/packs` (2026-09-15).
- **What:** the insurance, legal and manufacturing pack prompts call
  `knowledge_base_search()`, which no connector registers, and the CA
  `tds_compliance` prompt calls bare `calculate_tds()` (registered by both
  `income_tax_india` and `zoho_books`) and `get_vendor_details()`.
  `scripts/check_prompt_tools.py` covers `core/agents/prompts` only, because
  pack agents also use `composio:` tools that are discovered over the network.
- **Fix:** register a knowledge-search tool or rewrite those steps, qualify
  the CA names, and extend the check to pack prompts against each pack's
  `tools:` list, treating `composio:` names as declared.

## A-13 — Importing the provider interface loads every connector

- **Found:** building the provider conformance suite (2026-09-15).
- **What:** `connectors/__init__.py` imports all connector modules at package
  import. `connectors.framework.verification_provider`, which every provider
  package and `agenticorg.testing.provider_conformance` import, therefore
  pulls in every connector and its third-party dependencies, so a provider
  package's tests need the whole platform's dependency set installed.
- **Fix:** register native connectors from an explicit function called at
  application and worker startup (before plugin loading) instead of at
  package import, or move `connectors/framework` into a package with no
  import-time side effects. Keep the native-before-plugin ordering test.

## A-14 — mypy skips every module under connectors/

- **Found:** type-checking the provider seam (2026-09-15).
- **What:** `pyproject.toml` sets `ignore_errors = true` for `connectors.*`,
  so CI's `mypy .` reports nothing for the new provider interface, registry
  and mock provider, or for any connector. The new modules were checked
  separately with a stricter configuration and are clean.
- **Fix:** replace the blanket override with a per-module list of the legacy
  connectors that still fail, so new code under `connectors/` is checked.

## A-15 — CLAUDE.md lists the preflight test suites without tests/contract

- **Found:** adding `tests/contract/` to the CI unit job and
  `scripts/preflight.sh` (2026-09-15).
- **What:** "Required Before Every Push" in `CLAUDE.md` still lists
  `pytest tests/regression/ tests/unit/ tests/security/ tests/connector_harness/`.
- **Fix:** add `tests/contract/` to that line.
